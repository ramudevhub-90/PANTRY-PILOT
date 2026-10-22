import { useEffect, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import { api } from '../agent/remote';
import { currentEmail, currentRefreshToken } from './cognito';

/** "Claude wants to use your Pantry Pilot": shown after sign-in when an MCP client is connecting. */
export function ConsentScreen({ requestId }: { requestId: string }) {
  const [client, setClient] = useState<{ client: string; verified: boolean; redirectHost: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.oauthRequest(requestId).then(setClient).catch((err: Error) => setError(err.message));
  }, [requestId]);

  async function answer(allow: boolean) {
    setBusy(true);
    setError(null);
    try {
      window.location.assign(await api.answerOAuth(requestId, allow, currentRefreshToken()));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.');
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card consent">
        <div className="consent__icon" aria-hidden="true">
          <ShieldCheck size={26} />
        </div>
        {client ? (
          <>
            <h1>Connect {client.client} to Pantry Pilot?</h1>
            <p className="muted">
              Signed in as <strong>{currentEmail()}</strong>. {client.client} will be able to:
            </p>
            <ul className="consent__list">
              <li>see and update your pantry and grocery list</li>
              <li>plan meals and set kitchen timers</li>
              <li>compare grocery prices; orders still need you to say “place the order” in Pantry Pilot</li>
            </ul>
            {!client.verified && (
              <p className="consent__warn small-print">
                “{client.client}” named itself when it registered. Only allow it if you just started connecting it from{' '}
                <strong>{client.redirectHost}</strong>.
              </p>
            )}
            <p className="muted small-print">You’ll go back to {client.redirectHost}. You can disconnect it there any time.</p>
            <div className="consent__actions">
              <button type="button" className="btn" disabled={busy} onClick={() => void answer(true)}>
                {busy ? 'Connecting…' : `Allow ${client.client}`}
              </button>
              <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => void answer(false)}>
                Cancel
              </button>
            </div>
          </>
        ) : (
          !error && <p className="muted">Loading…</p>
        )}
        {error && <p className="auth-error">{error}</p>}
      </div>
    </div>
  );
}
