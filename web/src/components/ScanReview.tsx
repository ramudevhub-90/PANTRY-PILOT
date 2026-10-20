import { useEffect } from 'react';
import { AlertTriangle, Check, Minus, Plus, Trash2, X } from 'lucide-react';
import { CONFIDENT } from '../agent/tools';
import { useAssistant } from '../agent/useAssistant';
import { usePantry } from '../state/store';
import { UNITS } from '../lib/units';
import type { DetectedItem } from '../types';

const stepFor = (unit: string) => (unit === 'g' || unit === 'ml' ? 50 : unit === 'kg' || unit === 'L' ? 0.25 : 1);
const round = (n: number) => Math.round(n * 100) / 100;

/** Lets the user check what the vision model found before anything is saved. */
export function ScanReview() {
  const { state, dispatch } = usePantry();
  const { ask } = useAssistant();
  const review = state.review;

  useEffect(() => {
    if (!review) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && dispatch({ type: 'close_review' });
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [review, dispatch]);

  if (!review) return null;

  const analyzing = review.status === 'analyzing';
  const valid = review.items.filter((i) => i.name.trim() && i.quantity > 0);
  const unsure = review.items.filter((i) => i.confidence < CONFIDENT).length;
  const close = () => dispatch({ type: 'close_review' });

  function confirm() {
    dispatch({ type: 'close_review' });
    void ask(`Add these ${valid.length} items to my pantry`, { confirmItems: valid });
  }

  return (
    <div className="sheet-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="review-title">
        <header className="sheet__head">
          <div>
            <h2 id="review-title">Review scanned groceries</h2>
            <p className="muted" aria-live="polite">
              {analyzing
                ? 'Identifying items with Amazon Nova…'
                : `${review.items.length} items found${unsure ? ` · ${unsure} to check` : ''}. Fix anything that’s off, then add them.`}
            </p>
          </div>
          <button type="button" className="icon-btn" onClick={close} aria-label="Close review">
            <X size={18} />
          </button>
        </header>

        <div className="sheet__photos">
          {review.images.map((src, i) => (
            <img key={i} src={src} alt={`Grocery photo ${i + 1}`} />
          ))}
        </div>

        {analyzing ? (
          <ul className="review" aria-hidden="true">
            {[0, 1, 2, 3, 4].map((i) => (
              <li key={i} className="skeleton" />
            ))}
          </ul>
        ) : (
          <>
            <ul className="review">
              <li className="review__labels" aria-hidden="true">
                <span />
                <span>Item</span>
                <span>Quantity</span>
                <span>Use within</span>
                <span />
              </li>
              {review.items.map((item) => (
                <ReviewRow key={item.id} item={item} />
              ))}
            </ul>
            <button type="button" className="link-btn" onClick={() => dispatch({ type: 'add_review_item' })}>
              <Plus size={15} />
              Add an item it missed
            </button>
          </>
        )}

        <footer className="sheet__foot">
          <button type="button" className="btn btn--secondary" onClick={close}>
            Cancel
          </button>
          <button type="button" className="btn" onClick={confirm} disabled={analyzing || !valid.length}>
            Add {valid.length} {valid.length === 1 ? 'item' : 'items'} to pantry
          </button>
        </footer>
      </div>
    </div>
  );
}

function ReviewRow({ item }: { item: DetectedItem }) {
  const { dispatch } = usePantry();
  // Any edit counts as the user confirming the row.
  const set = (patch: Partial<DetectedItem>) => dispatch({ type: 'update_review_item', id: item.id, patch: { ...patch, confidence: 1 } });
  const step = stepFor(item.unit);
  const label = item.name || 'item';

  return (
    <li className={`review__row${item.confidence < CONFIDENT ? ' is-unsure' : ''}`}>
      <span className="review__emoji" aria-hidden="true">
        {item.emoji}
      </span>

      <div className="review__name">
        <input value={item.name} onChange={(e) => set({ name: e.target.value })} placeholder="Item name" aria-label="Item name" />
        {item.confidence >= 1 ? (
          <span className="conf conf--ok">
            <Check size={12} />
            Confirmed
          </span>
        ) : item.confidence < CONFIDENT ? (
          <span className="conf conf--low">
            <AlertTriangle size={12} />
            Check quantity
          </span>
        ) : (
          <span className="conf">{Math.round(item.confidence * 100)}% sure</span>
        )}
      </div>

      <div className="qty">
        <button type="button" className="qty__btn" onClick={() => set({ quantity: Math.max(0, round(item.quantity - step)) })} aria-label={`Less ${label}`}>
          <Minus size={14} />
        </button>
        <input
          type="number"
          inputMode="decimal"
          min={0}
          step={step}
          value={item.quantity}
          onChange={(e) => set({ quantity: Number(e.target.value) })}
          aria-label={`${label} quantity`}
        />
        <button type="button" className="qty__btn" onClick={() => set({ quantity: round(item.quantity + step) })} aria-label={`More ${label}`}>
          <Plus size={14} />
        </button>
        <select value={item.unit} onChange={(e) => set({ unit: e.target.value })} aria-label={`${label} unit`}>
          {UNITS.map((u) => (
            <option key={u}>{u}</option>
          ))}
        </select>
      </div>

      <label className="review__expiry">
        <input
          type="number"
          min={0}
          value={item.expiresInDays}
          onChange={(e) => set({ expiresInDays: Math.max(0, Math.round(Number(e.target.value))) })}
          aria-label={`Days until ${label} expires`}
        />
        days
      </label>

      <button type="button" className="icon-btn" onClick={() => dispatch({ type: 'remove_review_item', id: item.id })} aria-label={`Remove ${label}`}>
        <Trash2 size={16} />
      </button>
    </li>
  );
}
