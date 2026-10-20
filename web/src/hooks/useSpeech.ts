import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

// Phone browsers only let a page talk after the user has tapped it, and replies to hands-free
// requests arrive with no tap at all. A silent utterance on the first tap unlocks speech for the
// rest of the visit.
if (canSpeak) {
  const unlock = () => {
    const silent = new SpeechSynthesisUtterance(' ');
    silent.volume = 0;
    window.speechSynthesis.speak(silent);
  };
  window.addEventListener('pointerdown', unlock, { once: true, capture: true });
  window.addEventListener('keydown', unlock, { once: true, capture: true });
}

function pickVoice(): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices();
  const preferred = ['Samantha', 'Google US English', 'Microsoft Aria Online (Natural) - English (United States)'];
  return voices.find((v) => preferred.includes(v.name)) ?? voices.find((v) => v.lang === 'en-US');
}

export function speak(text: string, onEnd: () => void) {
  if (!canSpeak) {
    onEnd();
    return;
  }
  const synth = window.speechSynthesis;
  // Safari silently drops an utterance queued right after cancel(), so only cancel when something
  // is actually playing, and queue the reply on the next tick.
  const busy = synth.speaking || synth.pending;
  if (busy) synth.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = pickVoice();
  if (voice) utterance.voice = voice;
  utterance.rate = 1.05;

  // While the microphone is open (hands-free), iPhones play page audio through the quiet earpiece.
  // A "playback" audio session sends it to the loudspeaker; hands-free sends silence meanwhile anyway.
  const session = (navigator as Navigator & { audioSession?: { type: string } }).audioSession;
  const previousType = session?.type;
  if (session) session.type = 'playback';

  // Some browsers (and embedded webviews) never fire `end`, so cap it by an estimated duration.
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    clearTimeout(fallback);
    if (session && previousType !== undefined) session.type = previousType;
    onEnd();
  };
  const fallback = setTimeout(finish, 2000 + text.split(/\s+/).length * 450);
  utterance.onend = finish;
  utterance.onerror = finish;
  if (busy) setTimeout(() => synth.speak(utterance), 50);
  else synth.speak(utterance);
}

export function stopSpeaking() {
  if (canSpeak) window.speechSynthesis.cancel();
}
