export type Category = 'produce' | 'dairy' | 'protein' | 'grains' | 'pantry' | 'condiments';
export type ItemSource = 'seed' | 'scan' | 'voice' | 'manual' | 'order';

export interface PantryItem {
  id: string;
  name: string;
  emoji: string;
  quantity: number;
  unit: string;
  category: Category;
  /** Local calendar date, YYYY-MM-DD. */
  expiresOn: string;
  source: ItemSource;
}

export interface Meal {
  id: string;
  name: string;
  emoji: string;
  minutes: number;
  servings: number;
  /** Pantry items this meal uses. */
  uses: string[];
  /** Subset of `uses` that expire within 3 days. */
  rescues: string[];
  missing: string[];
  steps: string[];
  timer?: { label: string; minutes: number };
  reason: string;
  /** breakfast, lunch, dinner, evening snacks… (server/pantry_pilot/mealtime.py). */
  occasion?: string | null;
}

export interface GroceryItem {
  id: string;
  name: string;
  note?: string;
  checked: boolean;
  source: 'agent' | 'voice' | 'manual';
}

export interface KitchenTimer {
  id: string;
  label: string;
  totalSeconds: number;
  endsAt: number;
}

/** An item the vision model found in a photo, waiting for the user to confirm it. */
export interface DetectedItem {
  id: string;
  name: string;
  emoji: string;
  quantity: number;
  unit: string;
  category: Category;
  expiresInDays: number;
  /** 0–1. Below CONFIDENT the review screen asks the user to check it; 1 means user-confirmed. */
  confidence: number;
}

export interface ScanReview {
  images: string[];
  status: 'analyzing' | 'ready';
  items: DetectedItem[];
}

export type ToolName =
  | 'scan_pantry'
  | 'update_inventory'
  | 'get_inventory'
  | 'plan_meals'
  | 'build_grocery_list'
  | 'get_grocery_list'
  | 'set_timer';

export interface ToolCall {
  id: string;
  name: ToolName;
  args: Record<string, unknown>;
  status: 'running' | 'done' | 'error';
  summary?: string;
  durationMs?: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  images?: string[];
  toolCalls: ToolCall[];
  pending?: boolean;
}

export type AssistantMode = 'idle' | 'listening' | 'thinking' | 'speaking';
