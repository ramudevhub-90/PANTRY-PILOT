import { useEffect, useState } from 'react';
import { ExternalLink, Plus, ShoppingBasket } from 'lucide-react';
import { api, HAS_SERVER } from '../agent/remote';
import { useKitchenActions } from '../agent/useKitchenActions';
import { availableHere } from '../lib/region';
import { usePantry } from '../state/store';
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
        {done.length > 0 && (
          <button type="button" className="link-btn" onClick={clearChecked}>
            Clear {done.length} done
          </button>
        )}
      </header>

      {state.grocery.length === 0 ? (
        <p className="muted">Empty for now. Say “We’re out of milk” or ask for a meal plan.</p>
      ) : (
        <ul className="grocery">
          {[...open, ...done].map((g) => (
            <li key={g.id} className={g.checked ? 'is-checked' : undefined}>
              <label>
                <input type="checkbox" checked={g.checked} onChange={() => toggleGrocery(g.id)} />
                <span className="grocery__name">{g.name}</span>
                {g.note && <span className="grocery__note">{g.note}</span>}
              </label>
            </li>
          ))}
        </ul>
      )}

      <form className="add-row" onSubmit={add}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add an item…" aria-label="Add grocery item" />
        <button type="submit" className="icon-btn" disabled={!draft.trim()} aria-label="Add to list">
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
