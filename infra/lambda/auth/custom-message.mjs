// Cognito "custom message" trigger: branded HTML emails for sign-up codes, new codes,
// password resets and admin-created accounts. Cognito swaps {####} for the code and
// {username} for the username, so both placeholders must appear where Cognito expects them.
import { codeBox, layout } from './templates.mjs';

const APP_URL = process.env.APP_URL ?? '';

const COPY = {
  CustomMessage_Authentication: {
    subject: 'Your Pantry Pilot sign-in code',
    heading: 'Your sign-in code',
    intro: 'Enter this code to sign in to Pantry Pilot:',
  },
  CustomMessage_SignUp: {
    subject: 'Your Pantry Pilot verification code',
    heading: 'Welcome to Pantry Pilot',
    intro: 'Enter this code to finish creating your account:',
  },
  CustomMessage_ResendCode: {
    subject: 'Your new Pantry Pilot code',
    heading: 'Here’s your new code',
    intro: 'Enter this code to verify your email:',
  },
  CustomMessage_ForgotPassword: {
    subject: 'Reset your Pantry Pilot password',
    heading: 'Reset your password',
    intro: 'Enter this code to choose a new password:',
  },
  CustomMessage_VerifyUserAttribute: {
    subject: 'Confirm your email for Pantry Pilot',
    heading: 'Confirm your email',
    intro: 'Enter this code to confirm your email address:',
  },
  CustomMessage_UpdateUserAttribute: {
    subject: 'Confirm your new email for Pantry Pilot',
    heading: 'Confirm your new email',
    intro: 'Enter this code to confirm your new email address:',
  },
};

export const handler = async (event) => {
  const { codeParameter, usernameParameter } = event.request;
  const copy = COPY[event.triggerSource];

  if (copy) {
    event.response.emailSubject = copy.subject;
    event.response.emailMessage = layout({
      heading: copy.heading,
      intro: copy.intro,
      codeBlock: codeBox(codeParameter),
      footer: 'The code expires soon. If you didn’t ask for this, you can ignore this email.',
    });
  } else if (event.triggerSource === 'CustomMessage_AdminCreateUser') {
    event.response.emailSubject = 'Your Pantry Pilot account is ready';
    event.response.emailMessage = layout({
      heading: 'Your account is ready',
      intro: `Sign in${APP_URL ? ` at <a href="${APP_URL}" style="color:#2f7a4f;">${APP_URL.replace(/^https?:\/\//, '')}</a>` : ''} with:`,
      codeBlock: `<p style="margin:0 0 6px;font-size:14px;">Email: <strong>${usernameParameter}</strong></p>
        <p style="margin:0;font-size:14px;">Temporary password: <strong style="font-family:Menlo,Consolas,monospace;">${codeParameter}</strong></p>`,
      footer: 'You’ll be asked to choose your own password the first time you sign in.',
    });
  }
  return event;
};
