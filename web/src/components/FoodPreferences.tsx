import { useEffect, useRef, useState } from 'react';
import { House, Languages, LocateFixed, MapPin, SlidersHorizontal, X } from 'lucide-react';
import { HAS_SERVER, api } from '../agent/remote';
import { deviceCity } from '../lib/geo';
import { POLLY_CODES, REPLY_LANGUAGES } from '../lib/language';
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
const HOME_ASKED_KEY = 'pantry-pilot.homeFoodAsked';

/** Regional home cooking people often carry with them when they move for work. */
const HOME_FOODS = [
  'Bengali', 'Punjabi', 'Tamil', 'Kerala', 'Andhra', 'Telangana', 'Kannada', 'Marathi',
  'Gujarati', 'Rajasthani', 'Bihari', 'Odia', 'North-East', 'Kashmiri', 'Goan', 'Konkani',
];

function homeAskedBefore(): boolean {
  try {
    return localStorage.getItem(HOME_ASKED_KEY) === '1';
  } catch {
    return false;
  }
}

function rememberHomeAsked() {
  try {
    localStorage.setItem(HOME_ASKED_KEY, '1');
  } catch {
    // Storage blocked: the question may show again next visit.
  }
}

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
  const [homeCuisine, setHomeCuisine] = useState('');
  const [language, setLanguage] = useState('English');
  const [homeDismissed, setHomeDismissed] = useState(homeAskedBefore);
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
  const home = prefs.homeCuisine && prefs.homeCuisine.toLowerCase() !== 'local' ? prefs.homeCuisine : '';
  const askHome = loaded && !prefs.homeCuisine && prefs.cuisines.length === 0 && !homeDismissed;

  async function chooseHome(value: string) {
    rememberHomeAsked();
    setHomeDismissed(true);
    try {
      await apply({ homeCuisine: value });
    } catch {
      setError('Couldn’t save that. Try again under Change.');
    }
  }
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
    setHomeCuisine(prefs.homeCuisine === 'local' ? '' : prefs.homeCuisine);
    setLanguage(prefs.language || 'English');
    setError(null);
    setEditing(true);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await apply({
        diet,
        cuisines: list(cuisines),
        avoid: list(avoid),
        location: location.trim(),
        homeCuisine: homeCuisine.trim() || (prefs.homeCuisine ? 'local' : ''),
        language,
      });
      setEditing(false);
    } catch {
      setError('Couldn’t save. Try again.');
    } finally {
      setSaving(false);
    }
  }

  const homeAsk = askHome ? (
    <div className="home-ask">
      <div className="home-ask__head">
        <strong>What food did you grow up with?</strong>
        <button
          type="button"
          className="icon-btn"
          aria-label="Not now"
          onClick={() => {
            rememberHomeAsked();
            setHomeDismissed(true);
          }}
        >
          <X size={15} />
        </button>
      </div>
      <p className="muted small-print">Moved for work? Pantry Pilot can cook what you miss from home, with what {place} shops sell.</p>
      <div className="home-ask__chips">
        <button type="button" className="gchip" onClick={() => void chooseHome('local')}>
          Same as {place.split(',')[0]}
        </button>
        {HOME_FOODS.map((f) => (
          <button key={f} type="button" className="gchip" onClick={() => void chooseHome(f)}>
            {f}
          </button>
        ))}
      </div>
    </div>
  ) : null;

  if (!editing) {
    return (
      <>
      {homeAsk}
      <p className="prefs muted small-print">
        <span
          className="prefs__place"
          title={prefs.location ? `Dishes cooked at home in ${place}` : `Dishes common in ${place}, from your device's time zone`}
        >
          <MapPin size={13} aria-hidden="true" />
          {place}
          {placeNote}
        </span>
        {prefs.language && prefs.language !== 'English' && (
          <span className="prefs__place prefs__home" title={`Replies in ${prefs.language}`}>
            <Languages size={13} aria-hidden="true" />
            {prefs.language}
          </span>
        )}
        {home && (
          <span className="prefs__place prefs__home" title={`Mostly ${home} home cooking`}>
            <House size={13} aria-hidden="true" />
            {home} food
          </span>
        )}
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
      </>
    );
  }

  return (
    <form className="prefs-form" onSubmit={save}>
      <label>
        City
        <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Bengaluru, Karnataka" />
      </label>
      <label>
        Food you grew up with
        <input value={homeCuisine} onChange={(e) => setHomeCuisine(e.target.value)} placeholder="Bengali (leave empty for local food)" />
      </label>
      <label>
        Reply language
        <select value={language} onChange={(e) => setLanguage(e.target.value)}>
          {REPLY_LANGUAGES.map((l) => (
            <option key={l.name} value={l.name}>
              {l.name}
              {POLLY_CODES.has(l.code) ? '' : ' (text; voice if your phone has it)'}
            </option>
          ))}
        </select>
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
