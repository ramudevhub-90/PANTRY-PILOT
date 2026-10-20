import type { Category, DetectedItem, ItemSource, PantryItem } from '../types';
import { isoInDays, uid } from '../lib/format';

type ItemRow = [name: string, emoji: string, quantity: number, unit: string, category: Category, expiresInDays: number];

const SEED_ROWS: ItemRow[] = [
  ['Baby spinach', '🥬', 1, 'bag', 'produce', 1],
  ['Cilantro', '🌿', 1, 'bunch', 'produce', 1],
  ['Chicken thighs', '🍗', 500, 'g', 'protein', 2],
  ['Bell peppers', '🫑', 3, 'pcs', 'produce', 2],
  ['Greek yogurt', '🥣', 1, 'tub', 'dairy', 3],
  ['Milk', '🥛', 1, 'L', 'dairy', 4],
  ['Corn tortillas', '🫓', 8, 'pcs', 'grains', 5],
  ['Limes', '🍋', 2, 'pcs', 'produce', 6],
  ['Eggs', '🥚', 6, 'pcs', 'protein', 10],
  ['Cheddar', '🧀', 200, 'g', 'dairy', 14],
  ['Garlic', '🧄', 1, 'bulb', 'produce', 20],
  ['Onions', '🧅', 3, 'pcs', 'produce', 25],
  ['Basmati rice', '🍚', 1, 'kg', 'grains', 180],
  ['Penne pasta', '🍝', 500, 'g', 'grains', 240],
  ['Crushed tomatoes', '🥫', 2, 'cans', 'pantry', 300],
  ['Olive oil', '🫒', 1, 'bottle', 'condiments', 365],
];

/** What the mock vision model "sees" in uploaded grocery photos. */
const DETECTED_ROWS: [...ItemRow, confidence: number][] = [
  ['Tomatoes', '🍅', 1, 'kg', 'produce', 6, 0.62],
  ['Onions', '🧅', 2, 'kg', 'produce', 25, 0.91],
  ['Eggs', '🥚', 12, 'pcs', 'protein', 14, 0.96],
  ['Coriander', '🌿', 1, 'bunch', 'produce', 4, 0.84],
  ['Paneer', '🧀', 200, 'g', 'dairy', 5, 0.97],
  ['Bananas', '🍌', 6, 'pcs', 'produce', 5, 0.88],
  ['Baby spinach', '🥬', 1, 'bag', 'produce', 5, 0.79],
];

export function mockDetections(): DetectedItem[] {
  return DETECTED_ROWS.map(([name, emoji, quantity, unit, category, expiresInDays, confidence]) => ({
    id: uid(),
    name,
    emoji,
    quantity,
    unit,
    category,
    expiresInDays,
    confidence,
  }));
}

export function toPantryItems(rows: ItemRow[], source: ItemSource): PantryItem[] {
  return rows.map(([name, emoji, quantity, unit, category, days], i) => ({
    // Seed ids are stable so StrictMode's double-initialised reducer stays in sync.
    id: source === 'seed' ? `seed-${i}` : uid(),
    name,
    emoji,
    quantity,
    unit,
    category,
    expiresOn: isoInDays(days),
    source,
  }));
}

export const seedInventory = () => toPantryItems(SEED_ROWS, 'seed');

export interface Recipe {
  id: string;
  name: string;
  emoji: string;
  minutes: number;
  servings: number;
  /** `key` is matched as a substring of pantry item names. */
  ingredients: { key: string; label: string }[];
  steps: string[];
  timer?: { label: string; minutes: number };
}

export const RECIPES: Recipe[] = [
  {
    id: 'rice-bowl',
    name: 'Chicken & Spinach Rice Bowl',
    emoji: '🍛',
    minutes: 30,
    servings: 2,
    ingredients: [
      { key: 'chicken', label: 'Chicken thighs' },
      { key: 'spinach', label: 'Spinach' },
      { key: 'rice', label: 'Rice' },
      { key: 'garlic', label: 'Garlic' },
      { key: 'lime', label: 'Lime' },
      { key: 'cilantro', label: 'Cilantro' },
    ],
    steps: [
      'Rinse the rice and simmer it, covered, for 15 minutes.',
      'Season the chicken and sear with garlic, 6–7 minutes a side.',
      'Wilt the spinach in the same pan for 1 minute.',
      'Slice the chicken and serve over rice with lime and cilantro.',
    ],
    timer: { label: 'rice', minutes: 15 },
  },
  {
    id: 'fajita-tacos',
    name: 'Chicken Fajita Tacos',
    emoji: '🌮',
    minutes: 25,
    servings: 3,
    ingredients: [
      { key: 'chicken', label: 'Chicken thighs' },
      { key: 'pepper', label: 'Bell peppers' },
      { key: 'onion', label: 'Onion' },
      { key: 'tortilla', label: 'Tortillas' },
      { key: 'lime', label: 'Lime' },
      { key: 'cilantro', label: 'Cilantro' },
      { key: 'yogurt', label: 'Greek yogurt' },
      { key: 'cumin', label: 'Ground cumin' },
    ],
    steps: [
      'Slice chicken, peppers and onion into strips.',
      'Toss the chicken with cumin, lime juice and salt.',
      'Cook everything in a hot pan for 12 minutes.',
      'Serve in warm tortillas with yogurt and cilantro.',
    ],
    timer: { label: 'fajitas', minutes: 12 },
  },
  {
    id: 'frittata',
    name: 'Spinach & Pepper Frittata',
    emoji: '🍳',
    minutes: 20,
    servings: 2,
    ingredients: [
      { key: 'egg', label: 'Eggs' },
      { key: 'spinach', label: 'Spinach' },
      { key: 'pepper', label: 'Bell peppers' },
      { key: 'cheddar', label: 'Cheddar' },
      { key: 'onion', label: 'Onion' },
    ],
    steps: [
      'Soften the onion and peppers in an oven-safe pan.',
      'Add spinach, then pour over the beaten eggs.',
      'Top with cheddar and bake at 200°C for 12 minutes.',
    ],
    timer: { label: 'frittata', minutes: 12 },
  },
  {
    id: 'tomato-penne',
    name: 'Garlicky Tomato Penne',
    emoji: '🍝',
    minutes: 20,
    servings: 2,
    ingredients: [
      { key: 'penne', label: 'Penne' },
      { key: 'tomato', label: 'Crushed tomatoes' },
      { key: 'garlic', label: 'Garlic' },
      { key: 'onion', label: 'Onion' },
      { key: 'spinach', label: 'Spinach' },
      { key: 'olive oil', label: 'Olive oil' },
      { key: 'parmesan', label: 'Parmesan' },
    ],
    steps: [
      'Boil the penne for 10 minutes.',
      'Fry garlic and onion in olive oil, add tomatoes and simmer.',
      'Stir in spinach and the drained pasta.',
      'Finish with grated parmesan.',
    ],
    timer: { label: 'pasta', minutes: 10 },
  },
  {
    id: 'fried-rice',
    name: 'Veggie Fried Rice',
    emoji: '🥡',
    minutes: 20,
    servings: 2,
    ingredients: [
      { key: 'rice', label: 'Rice' },
      { key: 'egg', label: 'Eggs' },
      { key: 'pepper', label: 'Bell peppers' },
      { key: 'onion', label: 'Onion' },
      { key: 'garlic', label: 'Garlic' },
      { key: 'soy sauce', label: 'Soy sauce' },
      { key: 'peas', label: 'Frozen peas' },
    ],
    steps: [
      'Scramble the eggs and set aside.',
      'Stir-fry onion, garlic, peppers and peas for 4 minutes.',
      'Add cold rice and soy sauce, then fold the eggs back in.',
    ],
    timer: { label: 'stir-fry', minutes: 8 },
  },
];
