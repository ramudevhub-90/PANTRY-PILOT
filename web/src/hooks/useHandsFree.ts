import { useCallback, useEffect, useRef, useState } from 'react';
import { HAS_SERVER, api } from '../agent/remote';
import { audioEvent, decodeMessage } from '../lib/eventstream';
import { replyLanguage } from '../lib/language';

// Hands-free voice in any browser: the microphone streams to Amazon Transcribe and we watch the
// transcript for "Alexa". "Alexa, what's for dinner?" runs at once; a bare "Alexa" waits for the
// request. Once mode ("tap the mic") takes the next sentence and then stops.

export type HandsFreePhase = 'off' | 'starting' | 'waiting' | 'command';
type StartMode = 'wake' | 'once';

const WAKE = /\b(?:(?:hey|hi|ok|okay)[ ,]+)?(?:alexa|alexia)\b[\s,.!?]*/i;
const COMMAND_WAIT_MS = 8000;
/** Streaming costs money, so hands-free switches itself off after this long without "Alexa". */
const IDLE_OFF_MS = 5 * 60 * 1000;
const SAMPLE_RATE = 16000;

// Runs on the audio thread: downsamples to 16 kHz mono and posts 100 ms chunks of 16-bit PCM.
// Each output sample averages the input samples it replaces. Picking every third sample instead
// folds hiss above 8 kHz back into the speech band, and Transcribe mishears noticeably more.
const WORKLET = `
class PcmChunker extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.ratio = sampleRate / ${SAMPLE_RATE};
    this.t = 0;
    this.sum = 0;
    this.n = 0;
    this.chunk = new Int16Array(${SAMPLE_RATE / 10});
    this.fill = 0;
  }
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.sum += input[i];
      this.n++;
      this.t += 1;
      if (this.t < this.ratio) continue;
      this.t -= this.ratio;
      const s = Math.max(-1, Math.min(1, this.sum / this.n));
      this.sum = 0;
      this.n = 0;
      this.chunk[this.fill++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.fill === this.chunk.length) {
        this.port.postMessage(this.chunk.buffer.slice(0));
        this.fill = 0;
      }
    }
    return true;
  }
}
registerProcessor('pcm-chunker', PcmChunker);
`;

interface TranscriptResult {
  IsPartial: boolean;
  Alternatives?: { Transcript: string; Items?: { Type: string; Confidence?: number }[] }[];
}

/** Transcribe's average word confidence, 0–1 (1 when it doesn't say). Noise scores low. */
function confidence(result: TranscriptResult): number {
  const scores = (result.Alternatives?.[0]?.Items ?? [])
    .filter((i) => i.Type === 'pronunciation' && typeof i.Confidence === 'number')
    .map((i) => i.Confidence as number);
  return scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 1;
}

/** How sure Transcribe must be before acting. Short answers to Alexa's question ("ok", "yes")
 * start timers and the like, and background noise often comes out as exactly those words. */
function trusted(text: string, score: number, answering: boolean): boolean {
  const words = text.match(/[\p{L}\p{N}']+/gu)?.length ?? 0;
  if (answering) return score >= (words <= 2 ? 0.9 : 0.75);
  return score >= (words <= 1 ? 0.8 : 0.5);
}

/** The speech model to use. Browsers in India often report en-US or en-GB, so the time zone also
 * counts: the Indian English model understands Indian accents far better than the US one. */
export function speechLanguage(): string {
  // Hindi speakers are heard in Hindi; for other languages people mostly talk to Alexa in English.
  if (replyLanguage().code === 'hi-IN') return 'hi-IN';
  const lang = navigator.language || 'en-US';
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (lang === 'en-IN' || lang === 'hi-IN' || zone === 'Asia/Kolkata' || zone === 'Asia/Calcutta') return 'en-IN';
  return lang.startsWith('en-') ? lang : 'en-US';
}

/** Sounds that aren't a request ("Mhm", "uh", a lone letter), so they never reach the assistant. */
const FILLER = /^(?:m+h*m+|h+m+|u+h+m*|u+m+|a+h+|o+h+|e+r+m*|[a-z])[\s.,!?]*$/i;

export const handsFreeSupported =
  HAS_SERVER &&
  typeof window !== 'undefined' &&
  typeof WebSocket !== 'undefined' &&
  typeof AudioWorkletNode !== 'undefined' &&
  Boolean(navigator.mediaDevices?.getUserMedia);

/** `paused`: the assistant is thinking or speaking, so its own voice must not trigger it. */
export function useHandsFree(onCommand: (text: string) => void, paused: boolean) {
  const [phase, setPhaseState] = useState<HandsFreePhase>('off');
  const [interim, setInterim] = useState('');
  const [error, setError] = useState<string | null>(null);

  const phaseRef = useRef<HandsFreePhase>('off');
  const modeRef = useRef<StartMode>('wake');
  const pausedRef = useRef(paused);
  const onCommandRef = useRef(onCommand);
  const wsRef = useRef<WebSocket | null>(null);
  const audioRef = useRef<{ ctx: AudioContext; stream: MediaStream } | null>(null);
  const commandTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const idleTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const activeRef = useRef(false);
  /** In the few seconds after Alexa asked a question (stricter about noise). */
  const answeringRef = useRef(false);

  useEffect(() => {
    onCommandRef.current = onCommand;
  }, [onCommand]);
  useEffect(() => {
    pausedRef.current = paused;
    if (paused) setInterim('');
  }, [paused]);

  const setPhase = useCallback((p: HandsFreePhase) => {
    phaseRef.current = p;
    if (p !== 'command') answeringRef.current = false;
    setPhaseState(p);
  }, []);

  const stop = useCallback(() => {
    activeRef.current = false;
    clearTimeout(commandTimer.current);
    clearTimeout(idleTimer.current);
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(audioEvent(new Uint8Array(0)));
    ws?.close();
    const audio = audioRef.current;
    audioRef.current = null;
    audio?.stream.getTracks().forEach((t) => t.stop());
    void audio?.ctx.close();
    setInterim('');
    setPhase('off');
  }, [setPhase]);

  /** Restarted whenever the user talks to the assistant. */
  const armIdleTimer = useCallback(() => {
    clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => {
      stop();
      setError('Hands-free turned off after 5 quiet minutes.');
    }, IDLE_OFF_MS);
  }, [stop]);

  /** Gives up on a request that never comes: back to waiting for "Alexa", or off in once mode. */
  const armCommandTimer = useCallback(() => {
    clearTimeout(commandTimer.current);
    commandTimer.current = setTimeout(() => {
      if (phaseRef.current !== 'command') return;
      if (modeRef.current === 'once') stop();
      else setPhase('waiting');
    }, COMMAND_WAIT_MS);
  }, [setPhase, stop]);

  const runCommand = useCallback(
    (text: string) => {
      clearTimeout(commandTimer.current);
      armIdleTimer();
      setInterim('');
      onCommandRef.current(text);
      if (modeRef.current === 'once') stop();
      else setPhase('waiting');
    },
    [armIdleTimer, setPhase, stop],
  );

  const handleFinal = useCallback(
    (text: string, score: number) => {
      if (pausedRef.current || !text.trim() || FILLER.test(text.trim())) return;
      if (phaseRef.current === 'command') {
        if (!trusted(text, score, answeringRef.current)) return; // probably noise: keep listening
        answeringRef.current = false;
        runCommand(text.trim());
        return;
      }
      const wake = WAKE.exec(text);
      if (!wake) return;
      const rest = text.slice(wake.index + wake[0].length).trim();
      if (/\w{2,}/.test(rest)) {
        if (trusted(rest, score, false)) runCommand(rest);
      } else {
        setPhase('command');
        armCommandTimer();
      }
    },
    [armCommandTimer, runCommand, setPhase],
  );

  const connect = useCallback(async () => {
    const { url } = await api.voiceStream(speechLanguage());
    if (!activeRef.current) return;
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;
    ws.onmessage = (event) => {
      const message = decodeMessage(event.data as ArrayBuffer);
      const body = JSON.parse(new TextDecoder().decode(message.payload));
      if (message.headers[':message-type'] === 'exception') {
        setError(body.Message ?? 'Voice service error.');
        return;
      }
      for (const result of (body.Transcript?.Results ?? []) as TranscriptResult[]) {
        const text = result.Alternatives?.[0]?.Transcript ?? '';
        if (result.IsPartial) {
          if (pausedRef.current) continue;
          if (phaseRef.current === 'command') {
            setInterim(text);
            armCommandTimer(); // still talking
          } else if (WAKE.test(text)) {
            setInterim(text);
          }
        } else {
          handleFinal(text, confidence(result));
        }
      }
    };
    ws.onclose = () => {
      // Streams end after long silences or network blips; keep listening while switched on.
      if (wsRef.current === ws && activeRef.current) {
        wsRef.current = null;
        setTimeout(() => activeRef.current && void connect().catch(() => stop()), 1000);
      }
    };
  }, [armCommandTimer, handleFinal, stop]);

  const start = useCallback(
    async (mode: StartMode = 'wake') => {
      modeRef.current = mode;
      if (activeRef.current) {
        if (mode === 'once') {
          setPhase('command');
          armCommandTimer();
        }
        return;
      }
      activeRef.current = true;
      setError(null);
      setPhase('starting');
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
        });
        const ctx = new AudioContext();
        audioRef.current = { ctx, stream };
        // Started on page load (hands-free is on by default), the browser keeps audio paused until
        // the first tap or key press anywhere on the page.
        if (ctx.state === 'suspended') {
          const resume = () => void ctx.resume();
          window.addEventListener('pointerdown', resume, { once: true });
          window.addEventListener('keydown', resume, { once: true });
        }
        const module = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
        await ctx.audioWorklet.addModule(module);
        URL.revokeObjectURL(module);
        const node = new AudioWorkletNode(ctx, 'pcm-chunker');
        node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
          const ws = wsRef.current;
          if (!ws || ws.readyState !== WebSocket.OPEN) return;
          // While the assistant talks, send silence so the stream stays open but can't hear itself.
          const pcm = pausedRef.current ? new Uint8Array(event.data.byteLength) : new Uint8Array(event.data);
          ws.send(audioEvent(pcm));
        };
        ctx.createMediaStreamSource(stream).connect(node);
        await connect();
        if (!activeRef.current) return;
        setPhase(mode === 'once' ? 'command' : 'waiting');
        armIdleTimer();
        if (mode === 'once') armCommandTimer();
      } catch (err) {
        stop();
        setError(
          err instanceof DOMException && err.name === 'NotAllowedError'
            ? 'Microphone access was blocked.'
            : 'Couldn’t start hands-free voice.',
        );
      }
    },
    [armCommandTimer, armIdleTimer, connect, setPhase, stop],
  );

  /** Alexa just asked a question: take the next sentence as the answer, no "Alexa" needed. */
  const expectAnswer = useCallback(() => {
    if (!activeRef.current || phaseRef.current !== 'waiting') return;
    answeringRef.current = true;
    setPhase('command');
    armCommandTimer();
  }, [armCommandTimer, setPhase]);

  // iPhones may end the microphone while a reply plays through the loudspeaker; reopen it afterwards.
  useEffect(() => {
    if (paused || !activeRef.current) return;
    const audio = audioRef.current;
    if (audio && audio.stream.getAudioTracks().some((t) => t.readyState === 'ended')) {
      const mode = modeRef.current;
      stop();
      void start(mode);
    }
  }, [paused, start, stop]);

  useEffect(() => () => stop(), [stop]);

  /** Forget an old error (e.g. "Microphone access was blocked") once the user carries on by typing. */
  const dismissError = useCallback(() => setError(null), []);

  return { supported: handsFreeSupported, phase, interim, error, start, stop, expectAnswer, dismissError };
}
