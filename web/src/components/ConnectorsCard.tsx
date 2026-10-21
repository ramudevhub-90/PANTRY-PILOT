import { useEffect, useState } from 'react';
import { ClipboardPaste, ExternalLink, Link2, Mail, MessageCircle, RefreshCw, ShoppingBag, Unlink } from 'lucide-react';
import { api, type Connector, type KrogerStatus } from '../agent/remote';
import { availableHere } from '../lib/region';
import { usePantry } from '../state/store';

const touchScreen = typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches;

const NOT_A_CALLBACK = touchScreen
  ? 'That isn’t the sign-in address yet. In the sign-in tab, tap the address bar, tap and hold the address, choose Copy, then come back.'
  : 'That isn’t the sign-in address yet. In the app’s sign-in tab, click the address bar (⌘L or Ctrl+L), copy it (⌘C or Ctrl+C), and try again.';

// iPhone Safari copies the address bar without "http://", so the scheme is optional.
const CALLBACK = /^(https?:\/\/)?(localhost|127\.0\.0\.1)(:\d+)?\/callback\?.*code=/i;
const looksLikeCallback = (text: string) => CALLBACK.test(text.trim());
/** The pasted address with a scheme, as the server expects. */
const callbackUrl = (text: string) => (/^https?:\/\//i.test(text.trim()) ? text.trim() : `http://${text.trim()}`);

function connectedNote(name: string, hasOrders: boolean): string {
  return hasOrders
    ? `${name} connected. Tap Sync orders to import your recent orders.`
    : `${name} connected. Pantry Pilot can now compare its prices and fill your cart.`;
}

function ago(epochSeconds: number | null): string {
  if (!epochSeconds) return 'never synced';
  const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60);
  if (minutes < 1) return 'synced just now';
  if (minutes < 60) return `synced ${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `synced ${hours} h ago` : `synced ${Math.round(hours / 24)} d ago`;
}

/** Swiggy Instamart and Zepto: connect once, then import delivered orders into the pantry. */
export function ConnectorsCard() {
  const { state, dispatch } = usePantry();
  const [whatsapp, setWhatsapp] = useState('');
  const [editingWhatsapp, setEditingWhatsapp] = useState(false);
  const [connectors, setConnectors] = useState<Connector[] | null>(null);
  const [connecting, setConnecting] = useState<Connector['id'] | null>(null);
  const [pasted, setPasted] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);

  const [kroger, setKroger] = useState<KrogerStatus>({ enabled: false, connected: false });

  useEffect(() => {
    api.connectors().then((list) => setConnectors(list.filter((c) => availableHere(c.id)))).catch(() => setConnectors([]));
    if (availableHere('kroger')) api.kroger().then(setKroger).catch(() => undefined);
  }, []);

  const connectKroger = () =>
    act('start-kroger', async () => {
      window.location.assign(await api.startKroger());
    });

  const disconnectKroger = () =>
    act('disconnect-kroger', async () => {
      setKroger(await api.disconnectKroger());
    });

  // Back from a shopping app's sign-in (/api/connectors/{id}/callback sends ?connected= or ?connectError=).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connectedId = params.get('connected');
    const error = params.get('connectError');
    if (!connectedId && !error) return;
    if (error) setMessage({ tone: 'bad', text: error });
    else if (connectedId === 'kroger') {
      setMessage({ tone: 'ok', text: 'Kroger connected. Use “Add to Kroger cart” on your grocery list.' });
    } else {
      const name =
        connectedId === 'swiggy' ? 'Swiggy Instamart' : connectedId === 'zepto' ? 'Zepto' : connectedId === 'bigbasket' ? 'BigBasket' : 'The app';
      setMessage({ tone: 'ok', text: connectedNote(name, connectedId !== 'bigbasket') });
    }
    window.history.replaceState(null, '', window.location.pathname);
  }, []);

  async function act(key: string, action: () => Promise<void>) {
    setBusy(key);
    setMessage(null);
    try {
      await action();
    } catch (err) {
      setMessage({ tone: 'bad', text: err instanceof Error ? err.message : 'Something went wrong.' });
    } finally {
      setBusy(null);
    }
  }

  const start = (c: Connector) =>
    act(`start-${c.id}`, async () => {
      if (c.directReturn) {
        // The app's sign-in sends this tab straight back to Pantry Pilot, connected.
        const { authUrl } = await api.startConnector(c.id);
        if (authUrl) window.location.assign(authUrl);
        return;
      }
      // Open the tab first so the browser treats it as a click, then send it to the sign-in page.
      const tab = window.open('', '_blank');
      const { authUrl } = await api.startConnector(c.id);
      if (tab && authUrl) tab.location.href = authUrl;
      else if (authUrl) window.location.assign(authUrl);
      setConnecting(c.id);
      setPasted('');
    });

  async function complete(c: Connector, address: string) {
    if (!looksLikeCallback(address)) throw new Error(NOT_A_CALLBACK);
    const res = await api.finishConnector(c.id, callbackUrl(address));
    setConnectors(res.connectors.filter((c) => availableHere(c.id)));
    setConnecting(null);
    setPasted('');
    setMessage({ tone: 'ok', text: connectedNote(c.name, c.hasOrders) });
  }

  const finish = (c: Connector, address: string) => act(`finish-${c.id}`, () => complete(c, address));

  const pasteAndFinish = (c: Connector) =>
    act(`finish-${c.id}`, async () => {
      const text = await navigator.clipboard.readText().catch(() => {
        throw new Error('Your browser blocked reading the clipboard. Paste the address into the box instead.');
      });
      setPasted(text.trim());
      await complete(c, text);
    });

  const sync = (c: Connector) =>
    act(`sync-${c.id}`, async () => {
      const res = await api.syncConnector(c.id);
      setConnectors(res.connectors.filter((c) => availableHere(c.id)));
      if (res.state) dispatch({ type: 'sync_state', state: res.state });
      if (res.sync) setMessage({ tone: 'ok', text: res.sync.summary });
    });

  const disconnect = (c: Connector) =>
    act(`disconnect-${c.id}`, async () => {
      setConnectors((await api.disconnectConnector(c.id)).connectors.filter((x) => availableHere(x.id)));
    });

  return (
    <section className="card card--apps" aria-labelledby="apps-title">
      <header className="card__head">
        <h2 id="apps-title">
          <ShoppingBag size={17} />
          Shopping apps
        </h2>
      </header>
      <p className="muted small-print">
        Import delivered orders into your pantry and compare prices. Orders are placed only after you say “place the order”.
      </p>

      {connectors === null ? (
        <p className="muted">Loading…</p>
      ) : (
        <ul className="apps">
          {connectors.map((c) => (
            <li key={c.id} className="app-row">
              <div className="app-row__main">
                <strong>{c.name}</strong>
                <span className="muted">
                  {!c.connected
                    ? c.hasOrders
                      ? 'Not connected'
                      : 'Not connected · prices and cart'
                    : c.hasOrders
                      ? `Connected · ${ago(c.lastSync)}${c.importedOrders ? ` · ${c.importedOrders} orders imported` : ''}`
                      : 'Connected · prices and cart (you check out on BigBasket)'}
                </span>
              </div>
              {c.connected ? (
                <div className="app-row__actions">
                  {c.hasOrders && (
                    <button type="button" className="btn btn--sm" onClick={() => sync(c)} disabled={busy !== null}>
                      <RefreshCw size={14} className={busy === `sync-${c.id}` ? 'spin' : undefined} />
                      Sync orders
                    </button>
                  )}
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => disconnect(c)}
                    disabled={busy !== null}
                    aria-label={`Disconnect ${c.name}`}
                    title="Disconnect"
                  >
                    <Unlink size={15} />
                  </button>
                </div>
              ) : (
                <button type="button" className="btn btn--secondary btn--sm" onClick={() => start(c)} disabled={busy !== null}>
                  <Link2 size={14} />
                  Connect
                </button>
              )}

              {(connecting === c.id || c.pending) && !c.connected && !c.directReturn && (
                <form
                  className="app-connect"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void finish(c, pasted);
                  }}
                >
                  <ol>
                    <li>
                      Sign in to {c.name} in the tab that opened <ExternalLink size={12} aria-hidden="true" /> (mobile number and OTP).
                    </li>
                    <li>
                      {c.name} then shows <strong>“This site can’t be reached”</strong> at <code>localhost/callback</code>. That’s
                      expected: the sign-in code is in its address.
                    </li>
                    <li>
                      {touchScreen ? (
                        <>In that tab, tap the address bar, then tap and hold the address and choose <strong>Copy</strong>.</>
                      ) : (
                        <>
                          In that tab, copy the address: <kbd>⌘L</kbd> then <kbd>⌘C</kbd> (Windows: <kbd>Ctrl+L</kbd>, <kbd>Ctrl+C</kbd>).
                        </>
                      )}
                    </li>
                    <li>Come back here and finish:</li>
                  </ol>
                  <button type="button" className="btn btn--sm" onClick={() => pasteAndFinish(c)} disabled={busy !== null}>
                    <ClipboardPaste size={14} />
                    Paste &amp; finish
                  </button>
                  <div className="app-connect__row">
                    <input
                      value={pasted}
                      onChange={(e) => setPasted(e.target.value)}
                      onPaste={(e) => {
                        const text = e.clipboardData.getData('text');
                        if (looksLikeCallback(text)) {
                          e.preventDefault();
                          setPasted(text);
                          void finish(c, text);
                        }
                      }}
                      placeholder="…or paste it here: http://localhost/callback?code=…"
                      aria-label={`${c.name} sign-in address`}
                    />
                    <button type="submit" className="btn btn--secondary btn--sm" disabled={!pasted.trim() || busy !== null}>
                      Finish
                    </button>
                  </div>
                  <div className="app-connect__links">
                    <button type="button" className="link-btn" onClick={() => start(c)} disabled={busy !== null}>
                      Start again
                    </button>
                    <button type="button" className="link-btn" onClick={() => setConnecting(null)}>
                      Cancel
                    </button>
                  </div>
                </form>
              )}
            </li>
          ))}
          {kroger.enabled && (
            <li className="app-row">
              <div className="app-row__main">
                <strong>Kroger</strong>
                <span className="muted">
                  {kroger.connected ? 'Connected · fill your cart from the grocery list' : 'US · fill your Kroger cart from the grocery list'}
                </span>
              </div>
              {kroger.connected ? (
                <div className="app-row__actions">
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={disconnectKroger}
                    disabled={busy !== null}
                    aria-label="Disconnect Kroger"
                    title="Disconnect"
                  >
                    <Unlink size={15} />
                  </button>
                </div>
              ) : (
                <button type="button" className="btn btn--secondary btn--sm" onClick={connectKroger} disabled={busy !== null}>
                  <Link2 size={14} />
                  Connect
                </button>
              )}
            </li>
          )}
        </ul>
      )}

      {message && <p className={message.tone === 'ok' ? 'auth-notice' : 'auth-error'}>{message.text}</p>}

      <div className="apps__whatsapp">
        <MessageCircle size={15} aria-hidden="true" />
        {state.notifications.whatsapp && !editingWhatsapp ? (
          <>
            <span>
              WhatsApp updates to <strong>{state.notifications.whatsapp}</strong>
            </span>
            <button type="button" className="link-btn" onClick={() => setEditingWhatsapp(true)}>
              Change
            </button>
            <button
              type="button"
              className="link-btn"
              disabled={busy !== null}
              onClick={() => act('whatsapp-off', async () => dispatch({ type: 'sync_state', state: await api.setWhatsapp('') }))}
            >
              Turn off
            </button>
          </>
        ) : (
          <form
            className="apps__whatsapp-form"
            onSubmit={(e) => {
              e.preventDefault();
              void act('whatsapp', async () => {
                dispatch({ type: 'sync_state', state: await api.setWhatsapp(whatsapp) });
                setEditingWhatsapp(false);
                setMessage({ tone: 'ok', text: 'WhatsApp updates are on. Check WhatsApp for a confirmation message.' });
              });
            }}
          >
            <input
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              value={whatsapp}
              onChange={(e) => setWhatsapp(e.target.value)}
              placeholder="WhatsApp number, e.g. +91 98765 43210"
              aria-label="WhatsApp number for order updates"
            />
            <button type="submit" className="btn btn--sm" disabled={!whatsapp.trim() || busy !== null}>
              {busy === 'whatsapp' ? 'Saving…' : 'Get updates'}
            </button>
          </form>
        )}
      </div>

      <p className="apps__notify muted small-print">
        <Mail size={13} aria-hidden="true" />
        Order summaries and payment links also come by email.
        <button
          type="button"
          className="link-btn"
          disabled={busy !== null}
          onClick={() =>
            act('test-email', async () => {
              const to = await api.testEmail();
              setMessage({ tone: 'ok', text: `Sent a test email to ${to}.` });
            })
          }
        >
          {busy === 'test-email' ? 'Sending…' : 'Send a test'}
        </button>
      </p>
    </section>
  );
}
