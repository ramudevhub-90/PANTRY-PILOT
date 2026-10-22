// Cognito custom auth, step 3: check the code the user typed.
// The judges' demo accounts also accept a fixed code (no inbox needed) until DEMO_UNTIL.
// The code comes from the DEMO_CODE env var (GitHub secret DEMO_CODE), never from the repo;
// the emailed code keeps working, and define-challenge's 3-try limit still applies.
import { timingSafeEqual } from 'node:crypto';

const DEMO_EMAILS = new Set(['ramu.devhub@gmail.com', 'ramu@mobil80.com']);
const DEMO_CODE = process.env.DEMO_CODE ?? '';
const DEMO_UNTIL = Date.parse('2026-12-31T23:59:59Z');

const same = (a, b) => {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === 6 && x.length === y.length && timingSafeEqual(x, y);
};

export const handler = async (event) => {
  const expected = String(event.request.privateChallengeParameters?.answer ?? '');
  const given = String(event.request.challengeAnswer ?? '').trim();
  const demo =
    DEMO_CODE.length === 6 &&
    Date.now() < DEMO_UNTIL && DEMO_EMAILS.has(String(event.request.userAttributes?.email ?? '').toLowerCase());
  event.response.answerCorrect = same(expected, given) || (demo && same(DEMO_CODE, given));
  return event;
};
