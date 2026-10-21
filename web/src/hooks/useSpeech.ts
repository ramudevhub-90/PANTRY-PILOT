import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { HAS_SERVER, api } from '../agent/remote';
import { POLLY_CODES, replyLanguage } from '../lib/language';
import { speechLanguage } from './useHandsFree';

// Minimal typings for the Web Speech API, which TypeScript's DOM lib doesn't fully cover.
interface RecognitionResultLike {
  isFinal: boolean;
  0: { transcript: string };
}
interface RecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<RecognitionResultLike>;
}
interface RecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: RecognitionEventLike) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognitionCtor = new () => RecognitionLike;

function getRecognitionCtor(): RecognitionCtor | undefined {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}

export function useSpeechRecognition(onFinal: (text: string) => void) {
  const Ctor = useMemo(getRecognitionCtor, []);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState('');
  const [error, setError] = useState<string | null>(null);
  const recRef = useRef<RecognitionLike | null>(null);
  const onFinalRef = useRef(onFinal);

  useEffect(() => {
    onFinalRef.current = onFinal;
  }, [onFinal]);

  const start = useCallback(() => {
    if (!Ctor || recRef.current) return;
    const rec = new Ctor();
    rec.lang = speechLanguage();
    rec.interimResults = true;
    rec.continuous = false;
    let finalText = '';
    rec.onresult = (e) => {
      let interimText = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interimText += r[0].transcript;
      }
      setInterim(finalText + interimText);
    };
    rec.onerror = (e) => setError(e.error === 'not-allowed' ? 'Microphone access was blocked.' : `Voice input error: ${e.error}`);
    rec.onend = () => {
      recRef.current = null;
      setListening(false);
      setInterim('');
      if (finalText.trim()) onFinalRef.current(finalText.trim());
    };
    recRef.current = rec;
    setError(null);
    setListening(true);
    rec.start();
  }, [Ctor]);

  const stop = useCallback(() => recRef.current?.stop(), []);

  useEffect(() => () => recRef.current?.abort(), []);

  return { supported: Boolean(Ctor), listening, interim, error, start, stop };
}

export const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window;

// Replies are spoken by Amazon Polly (an MP3 from the server) when there is one, because iPhone
// Safari's built-in speech often stays silent. The browser's speech is the fallback.
const player: HTMLAudioElement | null = HAS_SERVER && typeof Audio !== 'undefined' ? new Audio() : null;
/** The reply playing now. `stop(false)` silences it without reporting it finished (a new reply replaces it). */
let current: { stop: (report?: boolean) => void } | null = null;

/** 0.1 s of silence as a WAV file, to play during the first tap. */
function silentWav(): string {
  const samples = 800;
  const view = new DataView(new ArrayBuffer(44 + samples * 2));
  const text = (offset: number, value: string) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, 'RIFF');
  view.setUint32(4, 36 + samples * 2, true);
  text(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 8000, true);
  view.setUint32(28, 16000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, 'data');
  view.setUint32(40, samples * 2, true);
  return URL.createObjectURL(new Blob([view.buffer], { type: 'audio/wav' }));
}

// Phone browsers only let a page make sound after the user has tapped it, and replies to
// hands-free requests arrive with no tap at all. Playing silence on the first tap unlocks the
// audio player (and the browser's speech) for the rest of the visit.
if (typeof window !== 'undefined') {
  const unlock = () => {
    if (player) {
      player.src = silentWav();
      void player.play().catch(() => undefined);
    }
    if (canSpeak) {
      const silent = new SpeechSynthesisUtterance(' ');
      silent.volume = 0;
      window.speechSynthesis.speak(silent);
    }
  };
  window.addEventListener('pointerdown', unlock, { once: true, capture: true });
  window.addEventListener('keydown', unlock, { once: true, capture: true });
}

function pickVoice(): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices();
  const preferred = ['Samantha', 'Google US English', 'Microsoft Aria Online (Natural) - English (United States)'];
  return voices.find((v) => preferred.includes(v.name)) ?? voices.find((v) => v.lang === 'en-US');
}

/** A voice for this language (e.g. bn-IN), or any voice for the base language (bn). */
function pickVoiceFor(lang: string): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices();
  const base = lang.split('-')[0];
  return voices.find((v) => v.lang.replace('_', '-') === lang) ?? voices.find((v) => v.lang.startsWith(base));
}

/** While the microphone is open (hands-free), iPhones play page audio through the quiet earpiece.
 * A "playback" audio session sends it to the loudspeaker; returns a function that restores it. */
function loudspeaker(): () => void {
  const session = (navigator as Navigator & { audioSession?: { type: string } }).audioSession;
  if (!session) return () => undefined;
  const previous = session.type;
  session.type = 'playback';
  return () => {
    session.type = previous;
  };
}

export function speak(text: string, onEnd: () => void) {
  current?.stop(false);
  const restore = loudspeaker();
  let done = false;
  const finish = (report = true) => {
    if (done) return;
    done = true;
    current = null;
    restore();
    if (report) onEnd();
  };
  const lang = replyLanguage();
  // Polly's Kajal speaks English and Hindi; for Bengali, Tamil and the rest the phone's own voice
  // reads the text when it has one for that language.
  if (player && POLLY_CODES.has(lang.code)) speakWithPolly(text, finish, lang.code === 'hi-IN' ? 'hi-IN' : speechLanguage());
  else speakWithBrowser(text, finish, lang.code === 'en-IN' ? undefined : lang.code);
}

function speakWithPolly(text: string, finish: (report?: boolean) => void, lang: string) {
  const audio = player!;
  let cancelled = false;
  let url: string | null = null;
  const cleanup = () => {
    audio.onended = audio.onerror = null;
    if (url) URL.revokeObjectURL(url);
  };
  current = {
    stop: (report = true) => {
      cancelled = true;
      audio.pause();
      cleanup();
      finish(report);
    },
  };
  const fallback = () => {
    cleanup();
    if (!cancelled) speakWithBrowser(text, finish, lang === 'hi-IN' ? lang : undefined);
  };
  api
    .speech(text, lang)
    .then((blob) => {
      if (cancelled) return;
      url = URL.createObjectURL(blob);
      audio.onended = () => {
        cleanup();
        finish();
      };
      audio.onerror = fallback;
      audio.src = url;
      return audio.play();
    })
    .catch(fallback);
}

function speakWithBrowser(text: string, finish: (report?: boolean) => void, lang?: string) {
  if (!canSpeak) {
    finish();
    return;
  }
  const synth = window.speechSynthesis;
  // Safari silently drops an utterance queued right after cancel(), so only cancel when something
  // is actually playing, and queue the reply on the next tick.
  const busy = synth.speaking || synth.pending;
  if (busy) synth.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = lang ? pickVoiceFor(lang) : pickVoice();
  if (voice) utterance.voice = voice;
  if (lang) utterance.lang = lang;
  utterance.rate = 1.05;

  // Some browsers (and embedded webviews) never fire `end`, so cap it by an estimated duration.
  const fallback = setTimeout(finish, 2000 + text.split(/\s+/).length * 450);
  const end = (report = true) => {
    clearTimeout(fallback);
    finish(report);
  };
  current = {
    stop: (report = true) => {
      utterance.onend = utterance.onerror = null;
      synth.cancel();
      end(report);
    },
  };
  utterance.onend = () => end();
  utterance.onerror = () => end();
  if (busy) setTimeout(() => synth.speak(utterance), 50);
  else synth.speak(utterance);
}

export function stopSpeaking() {
  current?.stop();
}
