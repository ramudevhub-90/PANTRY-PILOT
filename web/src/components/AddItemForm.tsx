import { useEffect, useRef, useState } from 'react';
import { useKitchenActions } from '../agent/useKitchenActions';
import { CATEGORIES, UNITS } from '../lib/units';
import type { Category } from '../types';

/** Manual pantry entry: name, quantity, unit, category and days until it expires. */
export function AddItemForm({ onDone }: { onDone: () => void }) {
  const { addToPantry } = useKitchenActions();
  const nameRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [unit, setUnit] = useState('pcs');
  const [category, setCategory] = useState<Category>('produce');
  const [days, setDays] = useState(String(CATEGORIES[0].shelfLifeDays));
  const [daysEdited, setDaysEdited] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  function pickCategory(next: Category) {
    setCategory(next);
    // Suggest a typical shelf life until the user sets the expiry themselves.
    if (!daysEdited) setDays(String(CATEGORIES.find((c) => c.id === next)!.shelfLifeDays));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const qty = Number(quantity);
    if (!name.trim()) return setError('Enter an item name.');
    if (!(qty > 0)) return setError('Enter a quantity above 0.');
    setSaving(true);
    await addToPantry({
      name: name.trim(),
      quantity: qty,
      unit,
      category,
      emoji: CATEGORIES.find((c) => c.id === category)!.emoji,
      expiresInDays: Math.max(0, Math.round(Number(days) || 0)),
    });
    setSaving(false);
    onDone();
  }

  return (
    <form className="add-item" onSubmit={submit} onKeyDown={(e) => e.key === 'Escape' && onDone()}>
      <label className="add-item__name">
        <span>Item</span>
        <input
          ref={nameRef}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setError('');
          }}
          placeholder="e.g. Paneer"
        />
      </label>
      <label>
        <span>Quantity</span>
        <div className="add-item__qty">
          <input
            type="number"
            inputMode="decimal"
            min={0}
            step="any"
            value={quantity}
            onChange={(e) => {
              setQuantity(e.target.value);
              setError('');
            }}
          />
          <select value={unit} onChange={(e) => setUnit(e.target.value)} aria-label="Unit">
            {UNITS.map((u) => (
              <option key={u}>{u}</option>
            ))}
          </select>
        </div>
      </label>
      <label>
        <span>Category</span>
        <select value={category} onChange={(e) => pickCategory(e.target.value as Category)}>
          {CATEGORIES.map((c) => (
            <option key={c.id} value={c.id}>
              {c.emoji} {c.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Expires in (days)</span>
        <input
          type="number"
          min={0}
          value={days}
          onChange={(e) => {
            setDays(e.target.value);
            setDaysEdited(true);
          }}
        />
      </label>
      <div className="add-item__actions">
        {error && <p className="add-item__error">{error}</p>}
        <button type="button" className="btn btn--secondary btn--sm" onClick={onDone}>
          Cancel
        </button>
        <button type="submit" className="btn btn--sm" disabled={saving}>
          {saving ? 'Adding…' : 'Add to pantry'}
        </button>
      </div>
    </form>
  );
}
