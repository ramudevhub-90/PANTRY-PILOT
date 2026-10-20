import { useState } from 'react';
import { MapPin, SlidersHorizontal } from 'lucide-react';
import { HAS_SERVER, api } from '../agent/remote';
import { deviceTimeZone, placeForZone } from '../lib/mealtime';
import { usePantry } from '../state/store';
import type { Diet } from '../types';

const DIETS: { value: Diet; label: string }[] = [
  { value: 'any', label: 'Any diet' },
  { value: 'vegetarian', label: 'Vegetarian' },
  { value: 'eggetarian', label: 'Eggetarian' },
  { value: 'vegan', label: 'Vegan' },
  { value: 'jain', label: 'Jain' },
  { value: 'non-vegetarian', label: 'Non-vegetarian' },
];

const list = (text: string) =>
  text
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

/** Diet, favourite cuisines and foods to avoid; meal ideas follow them. Also settable by voice. */
export function FoodPreferences() {
  const { state, dispatch } = usePantry();
  const prefs = state.preferences;
  const [editing, setEditing] = useState(false);
  const [diet, setDiet] = useState<Diet>(prefs.diet);
  const [cuisines, setCuisines] = useState('');
  const [avoid, setAvoid] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!HAS_SERVER) return null;

  const place = placeForZone(deviceTimeZone);

  const label = DIETS.find((d) => d.value === prefs.diet)?.label ?? 'Any diet';
  const summary = [
    label,
    prefs.cuisines.length ? prefs.cuisines.join(', ') : 'local favourites',
    ...(prefs.avoid.length ? [`no ${prefs.avoid.join(', ')}`] : []),
  ].join(' · ');

  function open() {
    setDiet(prefs.diet);
    setCuisines(prefs.cuisines.join(', '));
    setAvoid(prefs.avoid.join(', '));
    setError(null);
    setEditing(true);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const next = await api.setPreferences({ diet, cuisines: list(cuisines), avoid: list(avoid) });
      dispatch({ type: 'sync_state', state: next });
      setEditing(false);
    } catch {
      setError('Couldn’t save. Try again.');
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <p className="prefs muted small-print">
        <span className="prefs__place" title={`Dishes common in ${place}, from your device's time zone`}>
          <MapPin size={13} aria-hidden="true" />
          {place}
          {deviceTimeZone && place !== deviceTimeZone ? ` (${deviceTimeZone})` : ''}
        </span>
        <SlidersHorizontal size={13} aria-hidden="true" />
        <span>{summary}</span>
        <button type="button" className="link-btn" onClick={open}>
          Change
        </button>
      </p>
    );
  }

  return (
    <form className="prefs-form" onSubmit={save}>
      <label>
        Diet
        <select value={diet} onChange={(e) => setDiet(e.target.value as Diet)}>
          {DIETS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Favourite cuisines
        <input value={cuisines} onChange={(e) => setCuisines(e.target.value)} placeholder="South Indian, Punjabi, Italian" />
      </label>
      <label>
        Never use
        <input value={avoid} onChange={(e) => setAvoid(e.target.value)} placeholder="mushroom, peanuts" />
      </label>
      {error && <p className="auth-error">{error}</p>}
      <div className="prefs-form__actions">
        <button type="submit" className="btn btn--sm" disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="link-btn" onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}
