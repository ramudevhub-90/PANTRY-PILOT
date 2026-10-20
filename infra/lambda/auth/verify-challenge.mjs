// Cognito custom auth, step 3: check the code the user typed.
import { timingSafeEqual } from 'node:crypto';

export const handler = async (event) => {
  const expected = Buffer.from(String(event.request.privateChallengeParameters?.answer ?? ''));
  const given = Buffer.from(String(event.request.challengeAnswer ?? '').trim());
  event.response.answerCorrect = expected.length === 6 && given.length === expected.length && timingSafeEqual(expected, given);
  return event;
};
