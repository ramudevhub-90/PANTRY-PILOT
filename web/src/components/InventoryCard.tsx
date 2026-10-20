import { useState } from 'react';
import { Camera, Plus, Refrigerator } from 'lucide-react';
import { AddItemForm } from './AddItemForm';
import { ScanButton } from './ScanButton';
import { daysUntil, expiryLabel, expiryLevel } from '../lib/format';
import { usePantry } from '../state/store';
import type { Category, PantryItem } from '../types';

type Filter = 'all' | 'soon' | Category;

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'soon', label: 'Use soon' },
  { id: 'produce', label: 'Produce' },
  { id: 'dairy', label: 'Dairy' },
  { id: 'protein', label: 'Protein' },
  { id: 'grains', label: 'Grains' },
  { id: 'pantry', label: 'Pantry' },
];

function matches(item: PantryItem, filter: Filter): boolean {
  if (filter === 'all') return true;
  if (filter === 'soon') return daysUntil(item.expiresOn) <= 3;
  if (filter === 'pantry') return item.category === 'pantry' || item.category === 'condiments';
  return item.category === filter;
}

export function InventoryCard() {
  const { state } = usePantry();
  const [filter, setFilter] = useState<Filter>('all');
  const [adding, setAdding] = useState(false);
  const items = state.inventory
    .filter((i) => matches(i, filter))
    .sort((a, b) => daysUntil(a.expiresOn) - daysUntil(b.expiresOn));

  return (
    <section className="card card--inventory" aria-labelledby="inventory-title">
      <header className="card__head">
        <h2 id="inventory-title">
          <Refrigerator size={17} />
          Pantry <span className="count">{state.inventory.length}</span>
        </h2>
        <div className="card__actions">
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => setAdding(true)} disabled={adding}>
            <Plus size={15} />
            Add item
          </button>
          <ScanButton disabled={state.mode === 'thinking'} />
        </div>
      </header>

      {adding && <AddItemForm onDone={() => setAdding(false)} />}

      <div className="filters" role="group" aria-label="Filter pantry">
        {FILTERS.map((f) => (
          <button key={f.id} type="button" className="filter" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
            {f.label}
          </button>
        ))}
      </div>

      {items.length === 0 ? (
        <p className="muted">Nothing here.</p>
      ) : (
        <ul className="inv">
          {items.map((item) => {
            const days = daysUntil(item.expiresOn);
            return (
              <li key={item.id} className={`inv__row${state.highlight.includes(item.id) ? ' is-new' : ''}`}>
                <span className="inv__emoji" aria-hidden="true">
                  {item.emoji}
                </span>
                <div className="inv__main">
                  <span className="inv__name">
                    {item.name}
                    {item.source === 'scan' && (
                      <span className="tag">
                        <Camera size={11} />
                        scanned
                      </span>
                    )}
                  </span>
                  <span className="inv__qty">
                    {item.quantity} {item.unit}
                  </span>
                </div>
                <span className={`badge badge--${expiryLevel(days)}`}>{expiryLabel(days)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
