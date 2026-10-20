const DAY_MS = 86_400_000;

function startOfToday(): Date {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

/** Local date `days` from today as YYYY-MM-DD (avoids the UTC shift of toISOString). */
export function isoInDays(days: number): string {
  const d = startOfToday();
  d.setDate(d.getDate() + days);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}

export function daysUntil(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number);
  return Math.round((new Date(y, m - 1, d).getTime() - startOfToday().getTime()) / DAY_MS);
}

export type ExpiryLevel = 'expired' | 'urgent' | 'soon' | 'ok';

export function expiryLevel(days: number): ExpiryLevel {
  if (days < 0) return 'expired';
  if (days <= 1) return 'urgent';
  if (days <= 3) return 'soon';
  return 'ok';
}

export function expiryLabel(days: number): string {
  if (days < 0) return 'Expired';
  if (days === 0) return 'Today';
  if (days === 1) return 'Tomorrow';
  if (days < 14) return `${days} days`;
  if (days < 60) return `${Math.round(days / 7)} weeks`;
  return `${Math.round(days / 30)} months`;
}

/** "today", "tomorrow", "in 3 days" — for spoken replies. */
export function whenPhrase(days: number): string {
  if (days <= 0) return 'today';
  if (days === 1) return 'tomorrow';
  return `in ${days} days`;
}

export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.ceil(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

export function durationWords(seconds: number): string {
  if (seconds < 60) return `${seconds} seconds`;
  if (seconds % 3600 === 0) return seconds === 3600 ? '1 hour' : `${seconds / 3600} hours`;
  const minutes = Math.round(seconds / 60);
  return minutes === 1 ? '1 minute' : `${minutes} minutes`;
}

export function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

export const lower = (s: string) => s.toLowerCase();
export const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
export const uid = () => crypto.randomUUID();
