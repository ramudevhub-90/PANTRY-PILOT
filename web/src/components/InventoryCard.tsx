import { useState } from 'react';
import { Camera, Check, Flame, Mic, PackageCheck, Plus, Refrigerator, Search, ShoppingBasket } from 'lucide-react';
import { useAssistant } from '../agent/useAssistant';
import { useKitchenActions } from '../agent/useKitchenActions';
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

const SOURCE_ICON: Partial<Record<PantryItem['source'], typeof Camera>> = {
  scan: Camera,
  order: PackageCheck,
  voice: Mic,
};

const SOURCE_LABEL: Partial<Record<PantryItem['source'], string>> = {
  scan: 'scanned',
  order: 'from an order',
  voice: 'by voice',
};

/** How much shelf life is left, as a bar: two weeks or more is full. */
function freshness(days: number): number {
  return Math.max(4, Math.min(100, (days / 14) * 100));
}

function matches(item: PantryItem, filter: Filter): boolean {
  if (filter === 'all') return true;
  if (filter === 'soon') return daysUntil(item.expiresOn) <= 3;
  if (filter === 'pantry') return item.category === 'pantry' || item.category === 'condiments';
  return item.category === filter;
}

export function InventoryCard() {
  const { state } = usePantry();
  const { ask } = useAssistant();
  const { addGrocery } = useKitchenActions();
  const [filter, setFilter] = useState<Filter>('all');
  const [adding, setAdding] = useState(false);
  // WhatsApp's "Open the camera" button links to /?scan=1. Browsers only open the camera on a tap,
  // so we show a big "Take a photo" button instead of opening it automatically.
  const [fromWhatsApp, setFromWhatsApp] = useState(() => new URLSearchParams(window.location.search).get('scan') === '1');
  const closeScanPrompt = () => {
    setFromWhatsApp(false);
    const url = new URL(window.location.href);
    url.searchParams.delete('scan');
    window.history.replaceState(null, '', url);
  };
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const items = state.inventory
    .filter((i) => matches(i, filter) && (!q || i.name.toLowerCase().includes(q)))
    .sort((a, b) => daysUntil(a.expiresOn) - daysUntil(b.expiresOn));
  const useSoon = state.inventory.filter((i) => daysUntil(i.expiresOn) <= 3);
  const onList = new Set(state.grocery.filter((g) => !g.checked).map((g) => g.name.toLowerCase()));

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

      {fromWhatsApp && (
        <div className="scan-prompt" ref={(el) => {
            el?.scrollIntoView({ block: 'center' });
          }}>
          <Camera size={22} aria-hidden="true" />
          <div>
            <strong>Scan your groceries</strong>
            <span>Take a photo of what you bought, your fridge or the bill. Nova adds the items to your pantry.</span>
          </div>
          <ScanButton variant="hero" camera disabled={state.mode === 'thinking'} onPicked={closeScanPrompt} />
        </div>
      )}

      {adding && <AddItemForm onDone={() => setAdding(false)} />}

      {useSoon.length > 0 && (
        <div className="pantry-alert">
          <Flame size={18} aria-hidden="true" />
          <div>
            <strong>
              {useSoon.length} {useSoon.length === 1 ? 'item' : 'items'} to use in the next 3 days
            </strong>
            <span>{useSoon.slice(0, 4).map((i) => i.name).join(', ')}{useSoon.length > 4 ? '…' : ''}</span>
          </div>
          <button
            type="button"
            className="btn btn--sm"
            onClick={() => void ask('What can I make with the food that expires soon?')}
            disabled={state.mode === 'thinking'}
          >
            Cook with these
          </button>
        </div>
      )}

      <div className="pantry-tools">
        {state.inventory.length > 8 && (
          <label className="pantry-search">
            <Search size={15} aria-hidden="true" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search your pantry" aria-label="Search your pantry" />
          </label>
        )}
        <div className="filters" role="group" aria-label="Filter pantry">
          {FILTERS.map((f) => {
            const n = state.inventory.filter((i) => matches(i, f.id)).length;
            if (f.id !== 'all' && n === 0) return null;
            return (
              <button key={f.id} type="button" className="filter" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
                {f.label}
                <span className="filter__n">{n}</span>
              </button>
            );
          })}
        </div>
      </div>

      {items.length === 0 ? (
        <p className="muted">{q ? `Nothing matches “${query.trim()}”.` : 'Nothing here.'}</p>
      ) : (
        <ul className="plist">
          {items.map((item) => {
            const days = daysUntil(item.expiresOn);
            const level = expiryLevel(days);
            const listed = onList.has(item.name.toLowerCase());
            const SourceIcon = SOURCE_ICON[item.source];
            return (
              <li key={item.id} className={`pitem pitem--${level}${state.highlight.includes(item.id) ? ' is-new' : ''}`}>
                <span className="pitem__emoji" aria-hidden="true">
                  {item.emoji}
                </span>
                <div className="pitem__main">
                  <span className="pitem__name">{item.name}</span>
                  <span className="pitem__meta">
                    {item.quantity} {item.unit}
                    {SourceIcon && (
                      <span className="pitem__source">
                        <SourceIcon size={11} aria-hidden="true" />
                        {SOURCE_LABEL[item.source]}
                      </span>
                    )}
                  </span>
                  <span className="pitem__fresh" aria-hidden="true">
                    <span style={{ width: `${freshness(days)}%` }} />
                  </span>
                </div>
                <span className={`badge badge--${level}`}>{expiryLabel(days)}</span>
                <button
                  type="button"
                  className={`pitem__add${listed ? ' is-listed' : ''}`}
                  onClick={() => !listed && addGrocery(item.name)}
                  disabled={listed}
                  aria-label={listed ? `${item.name} is on the grocery list` : `Add ${item.name} to the grocery list`}
                  title={listed ? 'On the grocery list' : 'Add to grocery list'}
                >
                  {listed ? <Check size={15} /> : <ShoppingBasket size={15} />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
