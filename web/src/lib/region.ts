// Shopping apps only work in their own country, so each one is shown only where it works.
// The browser's time zone says where the user is without asking for location permission.

export type Region = 'IN' | 'US';

const US_ZONE =
  /^(?:America\/(?:New_York|Chicago|Denver|Phoenix|Los_Angeles|Anchorage|Juneau|Sitka|Nome|Yakutat|Metlakatla|Adak|Boise|Detroit|Menominee|Indiana\/.+|Kentucky\/.+|North_Dakota\/.+)|Pacific\/Honolulu|US\/.+)$/;

function detect(): Region | null {
  if (typeof Intl === 'undefined') return null;
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (zone === 'Asia/Kolkata' || zone === 'Asia/Calcutta') return 'IN';
  if (US_ZONE.test(zone)) return 'US';
  return null;
}

export const userRegion: Region | null = detect();

/** Where each shopping app works. */
export const APP_REGION: Record<string, Region> = {
  swiggy: 'IN',
  zepto: 'IN',
  bigbasket: 'IN',
  kroger: 'US',
  instacart: 'US',
};

export function availableHere(appId: string): boolean {
  const region = APP_REGION[appId];
  return !region || region === userRegion;
}
