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
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = pickVoice();
  if (voice) utterance.voice = voice;
  utterance.rate = 1.05;

  // Some browsers (and embedded webviews) never fire `end`, so cap it by an estimated duration.
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    clearTimeout(fallback);
    onEnd();
  };
  const fallback = setTimeout(finish, 2000 + text.split(/\s+/).length * 450);
  utterance.onend = finish;
  utterance.onerror = finish;
  window.speechSynthesis.speak(utterance);
}

export function stopSpeaking() {
  if (canSpeak) window.speechSynthesis.cancel();
}
