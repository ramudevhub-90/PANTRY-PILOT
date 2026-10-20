// Cognito custom auth, step 2: email a 6-digit sign-in code.
// Sends through Amazon SES in the account set by SES_ROLE_ARN (assumed with a send-only role),
// so codes reach any inbox even while this account's SES is still in the sandbox.
// A retry after a wrong answer re-uses the same code instead of sending a new email.
import { randomInt } from 'node:crypto';
import { SESv2Client, SendEmailCommand } from '@aws-sdk/client-sesv2';
import { AssumeRoleCommand, STSClient } from '@aws-sdk/client-sts';
import { codeBox, layout } from './templates.mjs';

const { SES_ROLE_ARN, SES_REGION = 'us-east-1', FROM_ADDRESS } = process.env;
const sts = new STSClient({});
let ses = null;
let sesExpiresAt = 0;

async function sesClient() {
  if (ses && Date.now() < sesExpiresAt) return ses;
  if (!SES_ROLE_ARN) return (ses = new SESv2Client({ region: SES_REGION }));
  const { Credentials: c } = await sts.send(
    new AssumeRoleCommand({ RoleArn: SES_ROLE_ARN, RoleSessionName: 'pantry-pilot-sign-in', DurationSeconds: 900 }),
  );
  ses = new SESv2Client({
    region: SES_REGION,
    credentials: { accessKeyId: c.AccessKeyId, secretAccessKey: c.SecretAccessKey, sessionToken: c.SessionToken },
  });
  sesExpiresAt = c.Expiration.getTime() - 60_000;
  return ses;
}

async function sendCode(to, code) {
  const html = layout({
    heading: 'Your sign-in code',
    intro: 'Enter this code to sign in to Pantry Pilot:',
    codeBlock: codeBox(code),
    footer: 'The code works for 3 minutes. If you didn’t try to sign in, you can ignore this email.',
  });
  await (await sesClient()).send(
    new SendEmailCommand({
      FromEmailAddress: `Pantry Pilot <${FROM_ADDRESS}>`,
      Destination: { ToAddresses: [to] },
      Content: {
        Simple: {
          Subject: { Data: `${code} is your Pantry Pilot sign-in code` },
          Body: {
            Html: { Data: html },
            Text: { Data: `Your Pantry Pilot sign-in code is ${code}. It works for 3 minutes.` },
          },
        },
      },
    }),
  );
}

export const handler = async (event) => {
  const previous = (event.request.session ?? []).findLast((s) => s.challengeName === 'CUSTOM_CHALLENGE');
  let code = previous?.challengeMetadata?.startsWith('CODE-') ? previous.challengeMetadata.slice(5) : null;

  if (!code) {
    code = String(randomInt(0, 1_000_000)).padStart(6, '0');
    await sendCode(event.request.userAttributes.email, code);
  }

  const email = event.request.userAttributes.email;
  event.response.publicChallengeParameters = { email: email.replace(/^(.)[^@]*(@.*)$/, '$1•••$2') };
  event.response.privateChallengeParameters = { answer: code };
  event.response.challengeMetadata = `CODE-${code}`;
  return event;
};
