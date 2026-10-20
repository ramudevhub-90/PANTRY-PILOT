import { useEffect, useState } from 'react';
import { Check, ExternalLink, Mic, Plus, ShoppingBasket, Sparkles } from 'lucide-react';
import { api, HAS_SERVER } from '../agent/remote';
import { useKitchenActions } from '../agent/useKitchenActions';
import { daysUntil } from '../lib/format';
import { foodEmoji } from '../lib/foodEmoji';
import { availableHere } from '../lib/region';
import { usePantry } from '../state/store';
import type { GroceryItem } from '../types';
import { KrogerCart } from './KrogerCart';

export function GroceryCard() {
  const { state } = usePantry();
  const { addGrocery, toggleGrocery, clearChecked } = useKitchenActions();
  const [draft, setDraft] = useState('');
  const [instacart, setInstacart] = useState(false);
  const [shopping, setShopping] = useState(false);
  const [shopError, setShopError] = useState<string | null>(null);
  const open = state.grocery.filter((g) => !g.checked);
  const done = state.grocery.filter((g) => g.checked);
  const total = state.grocery.length;
  const onList = new Set(state.grocery.map((g) => g.name.toLowerCase()));
  // Pantry food about to run out (expiring in two days) that isn't on the list yet: one tap to add.
  const runningOut = state.inventory
    .filter((i) => daysUntil(i.expiresOn) <= 2 && !onList.has(i.name.toLowerCase()))
    .slice(0, 4);

  useEffect(() => {
    if (HAS_SERVER && availableHere('instacart')) api.instacartEnabled().then(setInstacart).catch(() => setInstacart(false));
  }, []);

  function add(e: React.FormEvent) {
    e.preventDefault();
    const name = draft.trim();
    if (!name) return;
    addGrocery(name);
    setDraft('');
  }

  async function shop() {
    setShopping(true);
    setShopError(null);
    // Open the tab during the click so the browser allows it, then point it at the Instacart page.
    const tab = window.open('', '_blank');
    try {
      const { url } = await api.shopOnInstacart();
      if (tab) tab.location.href = url;
      else window.location.assign(url);
    } catch (err) {
      tab?.close();
      setShopError(err instanceof Error ? err.message : 'Instacart is unavailable right now.');
    } finally {
      setShopping(false);
    }
  }

  return (
    <section className="card card--grocery" aria-labelledby="grocery-title">
      <header className="card__head">
        <h2 id="grocery-title">
          <ShoppingBasket size={17} />
          Grocery list <span className="count">{open.length}</span>
        </h2>
        {total > 0 && (
          <span className="glist__progress-label">
            {done.length} of {total} in the basket
          </span>
        )}
      </header>
      {total > 0 && (
        <div className="glist__progress" aria-hidden="true">
          <span style={{ width: `${(done.length / total) * 100}%` }} />
        </div>
      )}

      {total === 0 ? (
        <div className="glist__empty">
          <span aria-hidden="true">🧺</span>
          <p>Nothing to buy yet.</p>
          <p className="muted small-print">Say “We’re out of milk”, or add the missing items from a meal plan.</p>
        </div>
      ) : (
        <ul className="glist">
          {open.map((g) => (
            <GroceryRow key={g.id} item={g} emoji={foodEmoji(g.name, state.inventory)} onToggle={() => toggleGrocery(g.id)} />
          ))}
        </ul>
      )}

      {done.length > 0 && (
        <details className="glist__done">
          <summary>
            <span>In the basket · {done.length}</span>
            <button
              type="button"
              className="link-btn"
              onClick={(e) => {
                e.preventDefault();
                clearChecked();
              }}
            >
              Clear
            </button>
          </summary>
          <ul className="glist">
            {done.map((g) => (
              <GroceryRow key={g.id} item={g} emoji={foodEmoji(g.name, state.inventory)} onToggle={() => toggleGrocery(g.id)} />
            ))}
          </ul>
        </details>
      )}

      {runningOut.length > 0 && (
        <div className="glist__suggest">
          <span className="muted small-print">Running out soon</span>
          <div className="glist__chips">
            {runningOut.map((i) => (
              <button key={i.id} type="button" className="gchip" onClick={() => addGrocery(i.name)}>
                <span aria-hidden="true">{i.emoji}</span>
                {i.name}
                <Plus size={13} aria-hidden="true" />
              </button>
            ))}
          </div>
        </div>
      )}

      <form className="add-row" onSubmit={add}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add an item…" aria-label="Add grocery item" />
        <button type="submit" className="icon-btn add-row__go" disabled={!draft.trim()} aria-label="Add to list">
          <Plus size={18} />
        </button>
      </form>

      {instacart && (
        <div className="instacart">
          <button type="button" className="btn" onClick={shop} disabled={shopping || open.length === 0}>
            <ExternalLink size={15} />
            {shopping ? 'Making your list…' : 'Shop on Instacart'}
          </button>
          <p className="muted small-print">US · You pick the store and check out on Instacart.</p>
          {shopError && <p className="auth-error">{shopError}</p>}
        </div>
      )}

      {availableHere('kroger') && <KrogerCart openItems={open.length} />}

    </section>
  );
}

/** Items added by voice or by the assistant get a small badge; ones you typed don't need one. */
const SOURCE = {
  voice: { icon: Mic, label: 'Added by voice' },
  agent: { icon: Sparkles, label: 'Added by Pantry Pilot' },
} as const;

function GroceryRow({ item, emoji, onToggle }: { item: GroceryItem; emoji: string; onToggle: () => void }) {
  const source = item.source === 'manual' ? null : SOURCE[item.source];
  const Icon = source?.icon;
  return (
    <li className={`gitem${item.checked ? ' is-checked' : ''}`}>
      <button
        type="button"
        role="checkbox"
        aria-checked={item.checked}
        aria-label={item.note ? `${item.name}, ${item.note}` : item.name}
        className="gitem__tap"
        onClick={onToggle}
      >
        <span className="gitem__check" aria-hidden="true">
          {item.checked && <Check size={14} strokeWidth={3} />}
        </span>
        <span className="gitem__emoji" aria-hidden="true">
          {emoji}
        </span>
        <span className="gitem__body">
          <span className="gitem__name">{item.name}</span>
          {item.note && <span className="gitem__note">{item.note}</span>}
        </span>
        {source && Icon && (
          <span className="gitem__source" title={source.label}>
            <Icon size={13} aria-hidden="true" />
          </span>
        )}
      </button>
    </li>
  );
}
