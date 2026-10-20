import { Timer, X } from 'lucide-react';
import { useAssistant } from '../agent/useAssistant';
import { useKitchenActions } from '../agent/useKitchenActions';
import { useNow } from '../hooks/useNow';
import { formatClock } from '../lib/format';
import { usePantry } from '../state/store';

const R = 18;
const CIRCUMFERENCE = 2 * Math.PI * R;

export function TimersCard() {
  const { state } = usePantry();
  const { ask } = useAssistant();
  const { removeTimer } = useKitchenActions();
  const now = useNow(state.timers.length > 0);

  return (
    <section className="card card--timers" aria-labelledby="timers-title">
      <header className="card__head">
        <h2 id="timers-title">
          <Timer size={17} />
          Timers
        </h2>
      </header>

      {state.timers.length === 0 ? (
        <div className="empty empty--row">
          <p className="muted">No timers running.</p>
          <button
            type="button"
            className="btn btn--secondary btn--sm"
            onClick={() => void ask('Set a 10 minute timer for the pasta')}
            disabled={state.mode === 'thinking'}
          >
            10-min pasta timer
          </button>
        </div>
      ) : (
        <ul className="timers">
          {state.timers.map((t) => {
            const remaining = Math.max(0, (t.endsAt - now) / 1000);
            const progress = 1 - remaining / t.totalSeconds;
            return (
              <li key={t.id} className={`timer${remaining <= 10 ? ' timer--ending' : ''}`}>
                <svg className="timer__ring" width="44" height="44" viewBox="0 0 44 44" aria-hidden="true">
                  <circle cx="22" cy="22" r={R} className="timer__track" />
                  <circle
                    cx="22"
                    cy="22"
                    r={R}
                    className="timer__progress"
                    strokeDasharray={CIRCUMFERENCE}
                    strokeDashoffset={CIRCUMFERENCE * (1 - progress)}
                  />
                </svg>
                <div className="timer__info">
                  <span className="timer__time">{formatClock(remaining)}</span>
                  <span className="timer__label">{t.label}</span>
                </div>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => removeTimer(t.id)}
                  aria-label={`Cancel ${t.label} timer`}
                >
                  <X size={16} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
