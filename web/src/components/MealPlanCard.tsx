import { useEffect, useRef, useState } from 'react';
import { Clock, Leaf, RefreshCw, ShoppingBasket, Timer, Users, UtensilsCrossed } from 'lucide-react';
import { HAS_SERVER, api } from '../agent/remote';
import { useAssistant } from '../agent/useAssistant';
import { FoodPreferences } from './FoodPreferences';
import { joinNames, lower } from '../lib/format';
import { currentOccasion, planTitle } from '../lib/mealtime';
import { usePantry } from '../state/store';

export function MealPlanCard() {
  const { state, dispatch } = usePantry();
  const { ask } = useAssistant();
  const [planning, setPlanning] = useState(false);
  const busy = state.mode === 'thinking' || planning;
  const [top, ...alternatives] = state.meals;
  const occasion = top?.occasion ?? currentOccasion();

  async function replan() {
    if (!HAS_SERVER) {
      void ask(`Suggest different ${currentOccasion()} ideas with what I have`);
      return;
    }
    setPlanning(true);
    try {
      dispatch({ type: 'sync_state', state: await api.plan(currentOccasion()) });
    } catch (err) {
      console.error('Could not plan meals', err);
    } finally {
      setPlanning(false);
    }
  }

  // A saved plan from earlier (or for another meal, like lunch ideas at 6 pm) is replaced with a
  // fresh one for the meal due now, once the pantry has loaded.
  const refreshedFor = useRef<string | null>(null);
  const loaded = state.inventory.length > 0;
  useEffect(() => {
    const due = currentOccasion();
    if (!HAS_SERVER || !loaded || refreshedFor.current === due) return;
    if (state.meals[0]?.occasion === due) return;
    refreshedFor.current = due;
    void replan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, state.meals]);

  return (
    <section className="card card--meal" aria-labelledby="meal-title">
      <header className="card__head">
        <h2 id="meal-title">
          <UtensilsCrossed size={17} />
          {planTitle(occasion)}
        </h2>
        {top && (
          <button type="button" className="link-btn" onClick={() => void replan()} disabled={busy}>
            <RefreshCw size={14} className={planning ? 'spin' : undefined} />
            {planning ? 'Planning…' : 'Re-plan'}
          </button>
        )}
      </header>
      <FoodPreferences />

      {!top ? (
        <div className="empty">
          <p>Pantry Pilot suggests dishes that use what’s about to expire, so food gets eaten instead of thrown away.</p>
          <button type="button" className="btn" onClick={() => void ask(`What can I make for ${occasion}?`)} disabled={busy}>
            What can I make for {occasion}?
          </button>
        </div>
      ) : (
        <div className="meal">
          <div className="meal__hero">
            <span className="meal__emoji" aria-hidden="true">
              {top.emoji}
            </span>
            <div>
              <h3>{top.name}</h3>
              <p className="meal__meta">
                <span>
                  <Clock size={14} />
                  {top.minutes} min
                </span>
                <span>
                  <Users size={14} />
                  Serves {top.servings}
                </span>
                <span>Uses {top.uses.length} of your items</span>
              </p>
              <p className="meal__reason">
                <Leaf size={14} />
                {top.reason}
              </p>
            </div>
          </div>

          <div className="meal__body">
            <ol className="steps">
              {top.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            <div className="meal__side">
              {top.missing.length > 0 && (
                <div>
                  <p className="eyebrow">Missing</p>
                  <div className="chips">
                    {top.missing.map((m) => (
                      <span key={m} className="chip">
                        {m}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <div className="meal__actions">
                {top.timer && (
                  <button
                    type="button"
                    className="btn"
                    onClick={() => void ask(`Set a ${top.timer!.minutes} minute timer for the ${top.timer!.label}`)}
                    disabled={busy}
                  >
                    <Timer size={16} />
                    Start {top.timer.minutes}-min {top.timer.label} timer
                  </button>
                )}
                {top.missing.length > 0 && (
                  <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={() => void ask(`Add ${joinNames(top.missing.map(lower))} to my grocery list`)}
                    disabled={busy}
                  >
                    <ShoppingBasket size={16} />
                    Add missing to list
                  </button>
                )}
              </div>
            </div>
          </div>

          {alternatives.length > 0 && (
            <div className="alts">
              <p className="eyebrow">Also good</p>
              <ul>
                {alternatives.map((m) => (
                  <li key={m.id} className="alt">
                    <span aria-hidden="true">{m.emoji}</span>
                    <div>
                      <strong>{m.name}</strong>
                      <span>
                        {m.minutes} min · {m.missing.length ? `missing ${joinNames(m.missing.map(lower))}` : 'have everything'}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
