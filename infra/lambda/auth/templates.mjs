// Branded HTML email shared by the Cognito custom-message trigger and the sign-in code sender.

export const APP_NAME = 'Pantry Pilot';

export function layout({ heading, intro, codeBlock, footer }) {
  // Email clients need tables and inline styles; no external CSS or images.
  return `<!doctype html>
<html><body style="margin:0;padding:0;background:#f5f1ea;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1f1c17;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f1ea;padding:32px 12px;">
<tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border:1px solid #e7e0d4;border-radius:16px;">
    <tr><td style="padding:28px 32px 8px;">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td style="width:40px;height:40px;background:#2f7a4f;border-radius:10px;text-align:center;font-size:22px;line-height:40px;">🥕</td>
        <td style="padding-left:12px;font-size:17px;font-weight:700;">${APP_NAME}<br><span style="font-size:12.5px;font-weight:400;color:#6e675c;">Kitchen agent for Alexa+</span></td>
      </tr></table>
    </td></tr>
    <tr><td style="padding:20px 32px 0;">
      <h1 style="margin:0 0 8px;font-size:22px;letter-spacing:-0.01em;">${heading}</h1>
      <p style="margin:0;font-size:15px;line-height:1.5;color:#3d3830;">${intro}</p>
    </td></tr>
    <tr><td style="padding:20px 32px;">${codeBlock}</td></tr>
    <tr><td style="padding:0 32px 28px;font-size:13px;line-height:1.5;color:#6e675c;">${footer}</td></tr>
  </table>
  <p style="margin:16px 0 0;font-size:12px;color:#9a9285;">${APP_NAME} · plans meals around what’s about to expire</p>
</td></tr>
</table>
</body></html>`;
}

export const codeBox = (code) =>
  `<div style="background:#e3f0e7;border-radius:12px;padding:18px;text-align:center;font-size:32px;font-weight:700;letter-spacing:8px;color:#1f5c3a;font-family:SFMono-Regular,Menlo,Consolas,monospace;">${code}</div>`;
