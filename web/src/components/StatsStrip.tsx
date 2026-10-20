import { daysUntil } from '../lib/format';
import { usePantry } from '../state/store';

export function StatsStrip() {
  const { state } = usePantry();
  const expiring = state.inventory.filter((i) => daysUntil(i.expiresOn) <= 2).length;
  const toBuy = state.grocery.filter((g) => !g.checked).length;

  const stats = [
    { label: 'Items in pantry', value: state.inventory.length },
    { label: 'Expiring in 2 days', value: expiring, tone: expiring ? 'urgent' : undefined },
    { label: 'On grocery list', value: toBuy },
    { label: 'Timers running', value: state.timers.length },
  ];

  return (
    <div className="stats">
      {stats.map((s) => (
        <div key={s.label} className={`stat${s.tone ? ` stat--${s.tone}` : ''}`}>
          <span className="stat__value">{s.value}</span>
          <span className="stat__label">{s.label}</span>
        </div>
      ))}
    </div>
  );
}
