// The meal due now on this device; the server uses the same table (server/pantry_pilot/mealtime.py).

const SLOTS: [number, string][] = [
  [5 * 60, 'breakfast'],
  [10 * 60 + 30, 'brunch'],
  [12 * 60, 'lunch'],
  [15 * 60 + 30, 'evening snacks'],
  [18 * 60 + 30, 'dinner'],
  [22 * 60 + 30, 'late-night snack'],
];

export function currentOccasion(now = new Date()): string {
  const minutes = now.getHours() * 60 + now.getMinutes();
  let current = 'late-night snack';
  for (const [start, occasion] of SLOTS) if (minutes >= start) current = occasion;
  return current;
}

export const deviceTimeZone = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch {
    return '';
  }
})();

const TITLES: Record<string, string> = {
  breakfast: 'Breakfast plan',
  brunch: 'Brunch plan',
  lunch: 'Lunch plan',
  'evening snacks': 'Evening snacks',
  dinner: 'Tonight’s plan',
  'late-night snack': 'Late-night snack',
};

export function planTitle(occasion: string): string {
  return TITLES[occasion] ?? 'Meal plan';
}

// Same places the server tells Nova (server/pantry_pilot/meal_ideas.py place_for).
const PLACES: Record<string, string> = {
  'Asia/Kolkata': 'India',
  'Asia/Calcutta': 'India',
  'Asia/Dubai': 'UAE',
  'Asia/Singapore': 'Singapore',
  'Europe/London': 'UK',
};
const US_ZONES = ['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Phoenix'];

/** Where meal ideas are aimed: a country name, or the time zone itself when we don't map it. */
export function placeForZone(zone: string): string {
  if (PLACES[zone]) return PLACES[zone];
  if (US_ZONES.some((z) => zone.startsWith(z))) return 'United States';
  return zone || 'India';
}
