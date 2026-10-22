// Sign-in against the Amazon Cognito user pool, using Cognito's public JSON API (no SDK needed).
// The session lives in localStorage so people stay signed in; tokens refresh before they expire.

export interface AuthSettings {
  region: string;
  userPoolId: string;
  clientId: string;
  /** Passwordless sign-in with an emailed code is available. */
  emailOtp?: boolean;
}

/** Where an email-code sign-in stands between "send code" and "enter code". */
export interface EmailCodeFlow {
  email: string;
  /** Cognito's custom-auth session; changes after each wrong answer. */
  session: string;
}

interface Session {
  email: string;
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

export class AuthError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

const STORAGE_KEY = 'pantry-pilot.session';

let settings: AuthSettings | null = null;
let session: Session | null = readStoredSession();
const listeners = new Set<() => void>();

function readStoredSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

function setSession(next: Session | null) {
  session = next;
  try {
    if (next) localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Private windows can refuse storage; the session still works until the tab closes.
  }
  listeners.forEach((fn) => fn());
}

export function configure(next: AuthSettings) {
  settings = next;
}

export function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export const currentEmail = () => session?.email ?? null;
export const emailOtpEnabled = () => Boolean(settings?.emailOtp);

const FRIENDLY: Record<string, string> = {
  NotAuthorizedException: 'Incorrect email or password.',
  UserNotFoundException: 'Incorrect email or password.',
  UsernameExistsException: 'An account with this email already exists. Sign in instead.',
  CodeMismatchException: "That code isn't right. Check the email and try again.",
  ExpiredCodeException: 'That code has expired. Send a new one.',
  LimitExceededException: 'Too many attempts. Wait a few minutes and try again.',
  TooManyRequestsException: 'Too many attempts. Wait a few minutes and try again.',
};

async function call<T>(action: string, body: Record<string, unknown>): Promise<T> {
  if (!settings) throw new AuthError('NotConfigured', 'Sign-in is not available right now.');
  const response = await fetch(`https://cognito-idp.${settings.region}.amazonaws.com/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-amz-json-1.1',
      'X-Amz-Target': `AWSCognitoIdentityProviderService.${action}`,
    },
    body: JSON.stringify({ ClientId: settings.clientId, ...body }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const code = String(data.__type ?? 'Error').split('#').pop()!;
    throw new AuthError(code, FRIENDLY[code] ?? data.message ?? 'Something went wrong. Please try again.');
  }
  return data as T;
}

interface AuthResult {
  AuthenticationResult: { AccessToken: string; RefreshToken?: string; ExpiresIn: number };
}

function startSession(email: string, { AuthenticationResult: r }: AuthResult) {
  setSession({ email, accessToken: r.AccessToken, refreshToken: r.RefreshToken!, expiresAt: Date.now() + r.ExpiresIn * 1000 });
}

export async function signIn(email: string, password: string) {
  startSession(
    email,
    await call<AuthResult>('InitiateAuth', {
      AuthFlow: 'USER_PASSWORD_AUTH',
      AuthParameters: { USERNAME: email, PASSWORD: password },
    }),
  );
}

/** "Continue with email": our server makes sure the account exists (first-timers get one), then
 *  Cognito's custom auth flow has our Lambda email a 6-digit code. */
export async function sendEmailCode(email: string, ensureAccount: (email: string) => Promise<void>): Promise<EmailCodeFlow> {
  await ensureAccount(email);
  const { Session } = await call<{ Session: string }>('InitiateAuth', {
    AuthFlow: 'CUSTOM_AUTH',
    AuthParameters: { USERNAME: email },
  });
  return { email, session: Session };
}

/** Checks the code. Signs in on success; on a wrong code returns the flow to retry with (Cognito
 *  issues a new session after each attempt and allows three). */
export async function verifyEmailCode(
  flow: EmailCodeFlow,
  code: string,
): Promise<{ signedIn: true } | { signedIn: false; retry: EmailCodeFlow }> {
  const result = await call<Partial<AuthResult> & { Session?: string }>('RespondToAuthChallenge', {
    ChallengeName: 'CUSTOM_CHALLENGE',
    Session: flow.session,
    ChallengeResponses: { USERNAME: flow.email, ANSWER: code },
  }).catch((err: unknown) => {
    if (err instanceof AuthError && err.code === 'NotAuthorizedException') {
      throw new AuthError(err.code, 'That code has expired or was entered wrong too many times. Send a new code.');
    }
    throw err;
  });
  if (result.AuthenticationResult) {
    startSession(flow.email, result as AuthResult);
    return { signedIn: true };
  }
  return { signedIn: false, retry: { ...flow, session: result.Session ?? flow.session } };
}

export const signUp = (email: string, password: string) =>
  call('SignUp', { Username: email, Password: password, UserAttributes: [{ Name: 'email', Value: email }] });

export const confirmSignUp = (email: string, code: string) =>
  call('ConfirmSignUp', { Username: email, ConfirmationCode: code });

export const resendCode = (email: string) => call('ResendConfirmationCode', { Username: email });

export const forgotPassword = (email: string) => call('ForgotPassword', { Username: email });

export const resetPassword = (email: string, code: string, password: string) =>
  call('ConfirmForgotPassword', { Username: email, ConfirmationCode: code, Password: password });

/** The signed-in session's refresh token, to hand an app the user just allowed (Claude). */
export function currentRefreshToken(): string | null {
  return session?.refreshToken ?? null;
}

export function signOut() {
  setSession(null);
}

/** A valid access token, refreshed if it expires within a minute; null when signed out. */
export async function accessToken(): Promise<string | null> {
  if (!session) return null;
  if (session.expiresAt - Date.now() > 60_000) return session.accessToken;
  try {
    const { AuthenticationResult: r } = await call<AuthResult>('InitiateAuth', {
      AuthFlow: 'REFRESH_TOKEN_AUTH',
      AuthParameters: { REFRESH_TOKEN: session.refreshToken },
    });
    setSession({ ...session, accessToken: r.AccessToken, expiresAt: Date.now() + r.ExpiresIn * 1000 });
    return r.AccessToken;
  } catch {
    setSession(null);
    return null;
  }
}
