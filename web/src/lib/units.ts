import type { Category } from '../types';

export const UNITS = ['pcs', 'g', 'kg', 'ml', 'L', 'bunch', 'bag', 'pack', 'tub', 'bottle', 'can', 'bulb'];

export const CATEGORIES: { id: Category; label: string; emoji: string; shelfLifeDays: number }[] = [
  { id: 'produce', label: 'Produce', emoji: '🥬', shelfLifeDays: 5 },
  { id: 'dairy', label: 'Dairy', emoji: '🥛', shelfLifeDays: 7 },
  { id: 'protein', label: 'Protein', emoji: '🍗', shelfLifeDays: 3 },
  { id: 'grains', label: 'Grains', emoji: '🌾', shelfLifeDays: 180 },
  { id: 'pantry', label: 'Pantry', emoji: '🥫', shelfLifeDays: 180 },
  { id: 'condiments', label: 'Condiments', emoji: '🧂', shelfLifeDays: 365 },
];
