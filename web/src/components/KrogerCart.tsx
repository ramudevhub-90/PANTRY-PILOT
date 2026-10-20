import { useEffect, useState } from 'react';
import { ExternalLink, Link2, ShoppingCart, Unlink } from 'lucide-react';
import { api, HAS_SERVER, type KrogerMatch, type KrogerStatus } from '../agent/remote';

interface Choice {
  upc: string;
  quantity: number;
  include: boolean;
}

/**
 * Kroger (US): match the open grocery items in Kroger's catalog, let the user pick, then add only
 * those to their Kroger cart. Product text is shown exactly as Kroger returns it, with no prices
 * compared, per Kroger's API terms. The user checks out on kroger.com.
 */
export function KrogerCart({ openItems }: { openItems: number }) {
  const [status, setStatus] = useState<KrogerStatus>({ enabled: false, connected: false });
  const [matches, setMatches] = useState<KrogerMatch[] | null>(null);
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [modality, setModality] = useState<'PICKUP' | 'DELIVERY'>('PICKUP');
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'bad'; text: string; cartUrl?: string } | null>(null);

  useEffect(() => {
    if (HAS_SERVER) api.kroger().then(setStatus).catch(() => undefined);
  }, []);

  if (!status.enabled) return null;

  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setNotice({ tone: 'bad', text: err instanceof Error ? err.message : 'Kroger is unavailable right now.' });
    } finally {
      setBusy(null);
    }
  }

  const connect = () =>
    run('connect', async () => {
      window.location.assign(await api.startKroger());
    });

  const review = () =>
    run('match', async () => {
      const found = await api.matchKroger();
      setMatches(found);
      setChoices(
        Object.fromEntries(
          found
            .filter((m) => m.products.length > 0)
            .map((m) => [m.item, { upc: m.products[0].upc, quantity: 1, include: true }]),
        ),
      );
    });

  const chosen = Object.values(choices).filter((c) => c.include);

  const addToCart = () =>
    run('cart', async () => {
      const { added, cartUrl } = await api.addToKrogerCart(
        chosen.map(({ upc, quantity }) => ({ upc, quantity })),
        modality,
      );
      setMatches(null);
      setChoices({});
      setNotice({ tone: 'ok', text: `Added ${added} ${added === 1 ? 'item' : 'items'} to your Kroger cart.`, cartUrl });
    });

  const disconnect = () =>
    run('disconnect', async () => {
      setStatus(await api.disconnectKroger());
      setMatches(null);
    });

  const update = (item: string, change: Partial<Choice>) =>
    setChoices((prev) => ({ ...prev, [item]: { ...prev[item], ...change } }));

  return (
    <div className="kroger">
      {!status.connected ? (
        <>
          <button type="button" className="btn btn--secondary" onClick={connect} disabled={busy !== null}>
            <Link2 size={15} />
            Connect Kroger
          </button>
          <p className="muted small-print">US · Fill your Kroger cart from this list. You check out on Kroger.</p>
        </>
      ) : matches === null ? (
        <>
          <button type="button" className="btn" onClick={review} disabled={busy !== null || openItems === 0}>
            <ShoppingCart size={15} />
            {busy === 'match' ? 'Finding products…' : 'Add to Kroger cart'}
          </button>
          <p className="muted small-print">
            US · You review every product first, then check out on Kroger.{' '}
            <button type="button" className="link-btn" onClick={disconnect} disabled={busy !== null}>
              <Unlink size={11} /> Disconnect
            </button>
          </p>
        </>
      ) : (
        <div className="kroger-review" role="group" aria-label="Choose Kroger products">
          <p className="kroger-review__title">Choose what goes in your Kroger cart</p>
          <ul>
            {matches.map((m) => {
              const choice = choices[m.item];
              if (!choice) {
                return (
                  <li key={m.item} className="kroger-row is-missing">
                    <span className="kroger-row__item">{m.item}</span>
                    <span className="muted">Not found at Kroger</span>
                  </li>
                );
              }
              const product = m.products.find((p) => p.upc === choice.upc) ?? m.products[0];
              return (
                <li key={m.item} className="kroger-row">
                  <label className="kroger-row__pick">
                    <input
                      type="checkbox"
                      checked={choice.include}
                      onChange={(e) => update(m.item, { include: e.target.checked })}
                      aria-label={`Add ${m.item}`}
                    />
                    {product.image ? <img src={product.image} alt="" width={40} height={40} /> : <span className="kroger-row__noimg" />}
                  </label>
                  <div className="kroger-row__main">
                    <span className="kroger-row__item">{m.item}</span>
                    {m.products.length > 1 ? (
                      <select
                        value={choice.upc}
                        onChange={(e) => update(m.item, { upc: e.target.value })}
                        aria-label={`Kroger product for ${m.item}`}
                      >
                        {m.products.map((p) => (
                          <option key={p.upc} value={p.upc}>
                            {p.description}
                            {p.size ? ` · ${p.size}` : ''}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="kroger-row__product">
                        {product.description}
                        {product.size ? ` · ${product.size}` : ''}
                      </span>
                    )}
                  </div>
                  <input
                    type="number"
                    min={1}
                    max={24}
                    value={choice.quantity}
                    onChange={(e) => update(m.item, { quantity: Math.max(1, Math.min(24, Number(e.target.value) || 1)) })}
                    aria-label={`Quantity of ${m.item}`}
                    className="kroger-row__qty"
                  />
                </li>
              );
            })}
          </ul>
          <div className="kroger-review__foot">
            <div className="segmented" role="radiogroup" aria-label="Pickup or delivery">
              {(['PICKUP', 'DELIVERY'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={modality === m}
                  className={modality === m ? 'is-on' : undefined}
                  onClick={() => setModality(m)}
                >
                  {m === 'PICKUP' ? 'Pickup' : 'Delivery'}
                </button>
              ))}
            </div>
            <button type="button" className="link-btn" onClick={() => setMatches(null)} disabled={busy !== null}>
              Cancel
            </button>
            <button type="button" className="btn btn--sm" onClick={addToCart} disabled={busy !== null || chosen.length === 0}>
              {busy === 'cart' ? 'Adding…' : `Add ${chosen.length} to cart`}
            </button>
          </div>
        </div>
      )}

      {notice && (
        <p className={notice.tone === 'ok' ? 'auth-notice' : 'auth-error'}>
          {notice.text}{' '}
          {notice.cartUrl && (
            <a href={notice.cartUrl} target="_blank" rel="noreferrer">
              Check out on Kroger <ExternalLink size={11} aria-hidden="true" />
            </a>
          )}
        </p>
      )}
    </div>
  );
}
