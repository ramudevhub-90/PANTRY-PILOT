import { Clock, Leaf, RefreshCw, ShoppingBasket, Timer, Users, UtensilsCrossed } from 'lucide-react';
import { useAssistant } from '../agent/useAssistant';
import { FoodPreferences } from './FoodPreferences';
import { joinNames, lower } from '../lib/format';
import { currentOccasion, planTitle } from '../lib/mealtime';
import { usePantry } from '../state/store';

export function MealPlanCard() {
  const { state } = usePantry();
  const { ask } = useAssistant();
  const busy = state.mode === 'thinking';
  const [top, ...alternatives] = state.meals;
  const occasion = top?.occasion ?? currentOccasion();

  return (
    <section className="card card--meal" aria-labelledby="meal-title">
      <header className="card__head">
        <h2 id="meal-title">
          <UtensilsCrossed size={17} />
          {planTitle(occasion)}
        </h2>
        {top && (
          <button type="button" className="link-btn" onClick={() => void ask(`Suggest different ${occasion} ideas with what I have`)} disabled={busy}>
            <RefreshCw size={14} />
            Re-plan
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
