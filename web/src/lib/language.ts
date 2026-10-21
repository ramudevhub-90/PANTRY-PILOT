// The household's reply language (a food preference on the server) and how to say it out loud.

export const REPLY_LANGUAGES: { name: string; code: string; label?: string }[] = [
  { name: 'Auto', code: 'auto', label: 'Same as I speak' },
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
  return REPLY_LANGUAGES.find((l) => l.name.toLowerCase() === current.toLowerCase()) ?? REPLY_LANGUAGES[1];
}

/** The language a reply is written in, from its script (Bengali, Devanagari...); Latin is English. */
export function scriptLanguage(text: string): string {
  const scripts: [RegExp, string][] = [
    [/[\u0980-\u09FF]/, 'bn-IN'],
    [/[\u0900-\u097F]/, 'hi-IN'],
    [/[\u0B80-\u0BFF]/, 'ta-IN'],
    [/[\u0C00-\u0C7F]/, 'te-IN'],
    [/[\u0C80-\u0CFF]/, 'kn-IN'],
    [/[\u0D00-\u0D7F]/, 'ml-IN'],
    [/[\u0A80-\u0AFF]/, 'gu-IN'],
    [/[\u0A00-\u0A7F]/, 'pa-IN'],
    [/[\u0B00-\u0B7F]/, 'or-IN'],
  ];
  return scripts.find(([re]) => re.test(text))?.[1] ?? 'en-IN';
}
