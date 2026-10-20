import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { AuthScreen } from './AuthScreen';
import { configure, currentEmail, subscribe } from './cognito';
import { HAS_SERVER, api } from '../agent/remote';

type Gate = 'loading' | 'open' | 'auth' | 'offline';

/** Shows the sign-in screen when the server requires it; otherwise renders the app.
 *  Signing out unmounts the app, so the next person starts with a clean slate. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [gate, setGate] = useState<Gate>(HAS_SERVER ? 'loading' : 'open');
  const email = useSyncExternalStore(subscribe, currentEmail);

  useEffect(() => {
    if (!HAS_SERVER) return;
    api
      .config()
      .then(({ auth }) => {
        if (auth) configure(auth);
        setGate(auth ? 'auth' : 'open');
      })
      .catch(() => setGate('offline'));
  }, []);

  if (gate === 'loading') return <div className="auth-page" aria-busy="true" />;
  if (gate === 'offline') {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <h1>Pantry Pilot</h1>
          <p className="muted">Can’t reach the Pantry Pilot server. Check your connection and reload.</p>
        </div>
      </div>
    );
  }
  if (gate === 'auth' && !email) return <AuthScreen />;
  return <>{children}</>;
}
