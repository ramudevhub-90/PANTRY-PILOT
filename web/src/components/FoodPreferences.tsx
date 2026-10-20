import { useEffect, useRef, useState } from 'react';
import { LocateFixed, MapPin, SlidersHorizontal } from 'lucide-react';
import { HAS_SERVER, api } from '../agent/remote';
import { deviceCity } from '../lib/geo';
import { currentOccasion, deviceTimeZone, placeForZone } from '../lib/mealtime';
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

const ASKED_KEY = 'pantry-pilot.locationAsked';

function askedBefore(): boolean {
  try {
    return localStorage.getItem(ASKED_KEY) === '1';
  } catch {
    return false;
  }
}

function rememberAsked() {
  try {
    localStorage.setItem(ASKED_KEY, '1');
  } catch {
    // Storage blocked: we may ask again next visit.
  }
}

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
  const [location, setLocation] = useState('');
  const [saving, setSaving] = useState(false);
  const [locating, setLocating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** Save preferences, then plan again so the card follows them right away. */
  async function apply(change: Parameters<typeof api.setPreferences>[0]) {
    dispatch({ type: 'sync_state', state: await api.setPreferences(change) });
    dispatch({ type: 'sync_state', state: await api.plan(currentOccasion()) });
  }

  async function locate(): Promise<boolean> {
    setLocating(true);
    setError(null);
    try {
      const city = await deviceCity();
      if (!city) {
        setError('Couldn’t get your city. Type it under Change instead.');
        return false;
      }
      await apply({ location: city });
      return true;
    } catch {
      setError('Couldn’t save your location. Try again.');
      return false;
    } finally {
      setLocating(false);
    }
  }

  // After sign-in, ask the device for its location once (the browser shows its own prompt), so
  // meal ideas fit the city: Bengaluru and Lucknow cook very differently, though both are India.
  const loaded = state.inventory.length > 0;
  const tried = useRef(false);
  useEffect(() => {
    if (!HAS_SERVER || !loaded || tried.current || prefs.location || askedBefore()) return;
    tried.current = true;
    void locate().then((ok) => {
      if (!ok) rememberAsked();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, prefs.location]);

  if (!HAS_SERVER) return null;

  const place = prefs.location ? prefs.location.replace(/, India$/, '') : placeForZone(deviceTimeZone);
  const placeNote = prefs.location ? '' : deviceTimeZone && place !== deviceTimeZone ? ` (${deviceTimeZone})` : '';

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
    setLocation(prefs.location);
    setError(null);
    setEditing(true);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await apply({ diet, cuisines: list(cuisines), avoid: list(avoid), location: location.trim() });
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
        <span
          className="prefs__place"
          title={prefs.location ? `Dishes cooked at home in ${place}` : `Dishes common in ${place}, from your device's time zone`}
        >
          <MapPin size={13} aria-hidden="true" />
          {place}
          {placeNote}
        </span>
        {!prefs.location && (
          <button type="button" className="link-btn" onClick={() => void locate()} disabled={locating}>
            <LocateFixed size={13} aria-hidden="true" />
            {locating ? 'Locating…' : 'Use my city'}
          </button>
        )}
        <SlidersHorizontal size={13} aria-hidden="true" />
        <span>{summary}</span>
        <button type="button" className="link-btn" onClick={open}>
          Change
        </button>
        {error && <span className="auth-error">{error}</span>}
      </p>
    );
  }

  return (
    <form className="prefs-form" onSubmit={save}>
      <label>
        City
        <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Bengaluru, Karnataka" />
      </label>
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
