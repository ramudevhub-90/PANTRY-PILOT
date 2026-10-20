// Order emails (summary, payment link, cart ready) from the Pantry Pilot server.
// Same route as sign-in codes: SES in the account set by SES_ROLE_ARN, through a send-only role,
// because this account's SES is still in the sandbox. The server invokes this function directly.
import { SESv2Client, SendEmailCommand } from '@aws-sdk/client-sesv2';
import { AssumeRoleCommand, STSClient } from '@aws-sdk/client-sts';
import { layout } from './templates.mjs';

const { SES_ROLE_ARN, SES_REGION = 'us-east-1', FROM_ADDRESS } = process.env;
const sts = new STSClient({});
let ses = null;
let sesExpiresAt = 0;

async function sesClient() {
  if (ses && Date.now() < sesExpiresAt) return ses;
  if (!SES_ROLE_ARN) return (ses = new SESv2Client({ region: SES_REGION }));
  const { Credentials: c } = await sts.send(
    new AssumeRoleCommand({ RoleArn: SES_ROLE_ARN, RoleSessionName: 'pantry-pilot-notify', DurationSeconds: 900 }),
  );
  ses = new SESv2Client({
    region: SES_REGION,
    credentials: { accessKeyId: c.AccessKeyId, secretAccessKey: c.SecretAccessKey, sessionToken: c.SessionToken },
  });
  sesExpiresAt = c.Expiration.getTime() - 60_000;
  return ses;
}

const escape = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

/** Item rows, an optional total and an optional big button, as email-safe tables. */
function body({ items = [], total, button }) {
  const rows = items
    .map(
      (i) => `<tr>
  <td style="padding:8px 0;border-bottom:1px solid #efe9df;font-size:14px;">${escape(i.name)}${i.quantity ? ` <span style="color:#9a9285;">× ${escape(i.quantity)}</span>` : ''}</td>
  <td style="padding:8px 0;border-bottom:1px solid #efe9df;font-size:14px;text-align:right;white-space:nowrap;">${escape(i.price ?? '')}</td>
</tr>`,
    )
    .join('');
  const totalRow = total
    ? `<tr><td style="padding:12px 0 0;font-size:15px;font-weight:700;">Total</td><td style="padding:12px 0 0;font-size:15px;font-weight:700;text-align:right;">${escape(total)}</td></tr>`
    : '';
  const table = rows || totalRow ? `<table role="presentation" width="100%" cellpadding="0" cellspacing="0">${rows}${totalRow}</table>` : '';
  const cta =
    button?.url && /^https:\/\//.test(button.url)
      ? `<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:20px;"><tr><td style="background:#2f7a4f;border-radius:10px;">
  <a href="${escape(button.url)}" style="display:inline-block;padding:12px 22px;color:#ffffff;font-size:15px;font-weight:700;text-decoration:none;">${escape(button.label || 'Open')}</a>
</td></tr></table>`
      : '';
  return table + cta;
}

export const handler = async (event) => {
  const { to, subject, heading, intro, footer = '', items, total, button, text } = event;
  if (!to || !subject) throw new Error('to and subject are required');
  const html = layout({
    heading: escape(heading || subject),
    intro: escape(intro || ''),
    codeBlock: body({ items, total, button }),
    footer: escape(footer),
  });
  const plain = [heading || subject, intro, ...(items ?? []).map((i) => `- ${i.name}${i.price ? `: ${i.price}` : ''}`), total ? `Total: ${total}` : '', button?.url ? `${button.label || 'Open'}: ${button.url}` : '', text || '', footer]
    .filter(Boolean)
    .join('\n');
  await (await sesClient()).send(
    new SendEmailCommand({
      FromEmailAddress: `Pantry Pilot <${FROM_ADDRESS}>`,
      Destination: { ToAddresses: [to] },
      Content: { Simple: { Subject: { Data: subject }, Body: { Html: { Data: html }, Text: { Data: plain } } } },
    }),
  );
  return { sent: true };
};
