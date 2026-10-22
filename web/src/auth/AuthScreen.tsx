import { useState } from 'react';
import { ChefHat, Eye, EyeOff } from 'lucide-react';
import {
  AuthError,
  confirmSignUp,
  emailOtpEnabled,
  forgotPassword,
  resendCode,
  resetPassword,
  sendEmailCode,
  signIn,
  signUp,
  verifyEmailCode,
  type EmailCodeFlow,
} from './cognito';
import { api } from '../agent/remote';

// email/code: passwordless sign-in with an emailed code (also creates new accounts).
// signin/signup/confirm/forgot/reset: the password flow, kept for the judges' demo account.
type Step = 'email' | 'code' | 'signin' | 'signup' | 'confirm' | 'forgot' | 'reset';

const TITLES: Record<Step, string> = {
  email: 'Sign in or create an account',
  code: 'Check your email',
  signin: 'Sign in with password',
  signup: 'Create your account',
  confirm: 'Check your email',
  forgot: 'Reset your password',
  reset: 'Choose a new password',
};

const SUBMIT: Record<Step, string> = {
  email: 'Continue with email',
  code: 'Sign in',
  signin: 'Sign in',
  signup: 'Create account',
  confirm: 'Verify and sign in',
  forgot: 'Send reset code',
  reset: 'Save password and sign in',
};

const PASSWORD_RULES = 'At least 8 characters, with upper and lower case letters, a number and a symbol.';

export function AuthScreen() {
  const otp = emailOtpEnabled();
  const [step, setStep] = useState<Step>(otp ? 'email' : 'signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [flow, setFlow] = useState<EmailCodeFlow | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const address = email.trim().toLowerCase();

  function go(next: Step) {
    setStep(next);
    setError('');
    setNotice('');
    setCode('');
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      if (err instanceof AuthError && err.code === 'UserNotConfirmedException') {
        await resendCode(address).catch(() => undefined);
        go('confirm');
        setNotice('Your email isn’t verified yet. We sent you a new code.');
      } else {
        setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.');
      }
    } finally {
      setBusy(false);
    }
  }

  async function emailCode() {
    setFlow(await sendEmailCode(address, api.ensureAccount));
    go('code');
    setNotice(`We sent a 6-digit code to ${address}. It works for 3 minutes.`);
  }

  async function checkCode() {
    const result = await verifyEmailCode(flow!, code.trim());
    if (!result.signedIn) {
      setFlow(result.retry);
      setCode('');
      setError("That code isn't right. Check the email and try again.");
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!address.includes('@')) return setError('Enter your email address.');
    const needsCodeNow = step === 'code' || step === 'confirm' || step === 'reset';
    if (needsCodeNow && !code.trim()) return setError('Enter the code from the email.');
    if ((step === 'signup' || step === 'reset') && password.length < 8) return setError(PASSWORD_RULES);
    if (step === 'signin' && !password) return setError('Enter your password.');

    switch (step) {
      case 'email':
        return void run(emailCode);
      case 'code':
        return void run(checkCode);
      case 'signin':
        return void run(() => signIn(address, password));
      case 'signup':
        return void run(async () => {
          await signUp(address, password);
          go('confirm');
          setNotice(`We sent a 6-digit code to ${address}.`);
        });
      case 'confirm':
        return void run(async () => {
          await confirmSignUp(address, code.trim());
          await signIn(address, password);
        });
      case 'forgot':
        return void run(async () => {
          await forgotPassword(address);
          go('reset');
          setNotice(`If an account exists for ${address}, we sent it a reset code.`);
        });
      case 'reset':
        return void run(async () => {
          await resetPassword(address, code.trim(), password);
          await signIn(address, password);
        });
    }
  }

  const needsPassword = step === 'signin' || step === 'signup' || step === 'reset';
  const needsCode = step === 'code' || step === 'confirm' || step === 'reset';
  const emailLocked = step === 'code' || step === 'confirm' || step === 'reset';

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit} noValidate>
        <div className="auth-brand">
          <span className="brand__mark">
            <ChefHat size={20} />
          </span>
          <div>
            <strong>Pantry Pilot</strong>
            <span>Kitchen agent for Alexa+</span>
          </div>
        </div>

        <h1>{TITLES[step]}</h1>
        {step === 'email' && <p className="muted">We’ll email you a 6-digit code. No password needed.</p>}
        {notice && <p className="auth-notice">{notice}</p>}

        <label className="auth-field">
          <span>Email</span>
          <input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={emailLocked}
            autoFocus={!emailLocked}
          />
        </label>

        {needsCode && (
          <label className="auth-field">
            <span>Code from the email</span>
            <input
              className="auth-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={8}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              autoFocus
            />
          </label>
        )}

        {needsPassword && (
          <label className="auth-field">
            <span>{step === 'reset' ? 'New password' : 'Password'}</span>
            <div className="auth-password">
              <input
                type={showPassword ? 'text' : 'password'}
                autoComplete={step === 'signin' ? 'current-password' : 'new-password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="icon-btn"
                onClick={() => setShowPassword((s) => !s)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            {step !== 'signin' && <small>{PASSWORD_RULES}</small>}
          </label>
        )}

        {error && (
          <p className="auth-error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" className="btn auth-submit" disabled={busy}>
          {busy ? 'Please wait…' : SUBMIT[step]}
        </button>

        <div className="auth-links">
          {step === 'email' && (
            <button type="button" className="link-btn" onClick={() => go('signin')}>
              Use a password instead
            </button>
          )}
          {step === 'code' && (
            <>
              <button type="button" className="link-btn" disabled={busy} onClick={() => run(emailCode)}>
                Send a new code
              </button>
              <button type="button" className="link-btn" onClick={() => go('email')}>
                Use a different email
              </button>
            </>
          )}
          {step === 'signin' && (
            <>
              <button type="button" className="link-btn" onClick={() => go('forgot')}>
                Forgot password?
              </button>
              {otp ? (
                <button type="button" className="link-btn" onClick={() => go('email')}>
                  Email me a code instead
                </button>
              ) : (
                <span>
                  New here?{' '}
                  <button type="button" className="link-btn" onClick={() => go('signup')}>
                    Create an account
                  </button>
                </span>
              )}
            </>
          )}
          {step === 'confirm' && (
            <button
              type="button"
              className="link-btn"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await resendCode(address);
                  setNotice('We sent you a new code.');
                })
              }
            >
              Send a new code
            </button>
          )}
          {(step === 'signup' || step === 'confirm' || step === 'forgot' || step === 'reset') && (
            <button type="button" className="link-btn" onClick={() => go(otp ? 'email' : 'signin')}>
              Back to sign in
            </button>
          )}
        </div>
      </form>
      <footer className="site-footer">
        <strong>Pantry Pilot</strong> · a kitchen agent for Alexa+, the web and WhatsApp ·{' '}
        <a href="/privacy.html">Privacy policy</a> · <a href="mailto:ramu.devhub@gmail.com">ramu.devhub@gmail.com</a>
      </footer>
    </div>
  );
}
