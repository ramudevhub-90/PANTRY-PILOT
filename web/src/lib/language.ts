// The household's reply language (a food preference on the server) and how to say it out loud.

export const REPLY_LANGUAGES: { name: string; code: string }[] = [
  { name: 'English', code: 'en-IN' },
  { name: 'Hindi', code: 'hi-IN' },
  { name: 'Bengali', code: 'bn-IN' },
  { name: 'Tamil', code: 'ta-IN' },
  { name: 'Telugu', code: 'te-IN' },
  { name: 'Kannada', code: 'kn-IN' },
  { name: 'Malayalam', code: 'ml-IN' },
  { name: 'Marathi', code: 'mr-IN' },
  { name: 'Gujarati', code: 'gu-IN' },
  { name: 'Punjabi', code: 'pa-IN' },
  { name: 'Odia', code: 'or-IN' },
];

/** Polly (Kajal) speaks only these; other languages use the phone's own voice when it has one. */
export const POLLY_CODES = new Set(['en-IN', 'hi-IN']);

let current = 'English';

export function setReplyLanguage(name: string) {
  current = name || 'English';
}

export function replyLanguage(): { name: string; code: string } {
  return REPLY_LANGUAGES.find((l) => l.name.toLowerCase() === current.toLowerCase()) ?? REPLY_LANGUAGES[0];
}
