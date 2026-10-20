// Cognito custom auth, step 1: decide what happens next in an email-code sign-in.
// One CUSTOM_CHALLENGE (the emailed code); tokens once it's answered correctly; give up after 3 wrong tries.

const MAX_ATTEMPTS = 3;

export const handler = async (event) => {
  const session = event.request.session ?? [];
  const attempts = session.filter((s) => s.challengeName === 'CUSTOM_CHALLENGE');
  const last = session.at(-1);

  if (event.request.userNotFound) {
    event.response.issueTokens = false;
    event.response.failAuthentication = true;
  } else if (last?.challengeName === 'CUSTOM_CHALLENGE' && last.challengeResult === true) {
    event.response.issueTokens = true;
    event.response.failAuthentication = false;
  } else if (attempts.length >= MAX_ATTEMPTS) {
    event.response.issueTokens = false;
    event.response.failAuthentication = true;
  } else {
    event.response.issueTokens = false;
    event.response.failAuthentication = false;
    event.response.challengeName = 'CUSTOM_CHALLENGE';
  }
  return event;
};
