import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode } from 'react';
import { HAS_SERVER, api, type ServerState } from '../agent/remote';
import { seedInventory } from '../data/seed';
import { daysUntil, uid } from '../lib/format';
import type {
  AssistantMode,
  ChatMessage,
  DetectedItem,
  FoodPreferences,
  GroceryItem,
  KitchenTimer,
  Meal,
  PantryItem,
  ScanReview,
  ToolCall,
} from '../types';

export interface PantryState {
  inventory: PantryItem[];
  meals: Meal[];
  grocery: GroceryItem[];
  timers: KitchenTimer[];
  preferences: FoodPreferences;
  messages: ChatMessage[];
  mode: AssistantMode;
  voiceReplies: boolean;
  /** Inventory ids to flash after they change. */
  highlight: string[];
  /** Scanned items awaiting confirmation; null when the review screen is closed. */
  review: ScanReview | null;
}

export type Action =
  | { type: 'sync_state'; state: ServerState }
  | { type: 'add_stock'; items: PantryItem[] }
  | { type: 'open_review'; images: string[] }
  | { type: 'set_review_items'; items: DetectedItem[] }
  | { type: 'update_review_item'; id: string; patch: Partial<DetectedItem> }
  | { type: 'remove_review_item'; id: string }
  | { type: 'add_review_item' }
  | { type: 'close_review' }
  | { type: 'set_meals'; meals: Meal[] }
  | { type: 'add_grocery'; items: GroceryItem[] }
  | { type: 'toggle_grocery'; id: string }
  | { type: 'clear_checked' }
  | { type: 'add_timer'; timer: KitchenTimer }
  | { type: 'remove_timer'; id: string }
  | { type: 'add_message'; message: ChatMessage }
  | { type: 'patch_message'; id: string; patch: Partial<ChatMessage> }
  | { type: 'upsert_tool_call'; messageId: string; call: ToolCall }
  | { type: 'set_mode'; mode: AssistantMode }
  | { type: 'set_voice'; on: boolean }
  | { type: 'set_highlight'; ids: string[] };

function initialState(): PantryState {
  return {
    // With a server, the pantry loads from it; the seed is for the offline mock.
    inventory: HAS_SERVER ? [] : seedInventory(),
    meals: [],
    grocery: [],
    timers: [],
    preferences: { diet: 'any', cuisines: [], avoid: [] },
    messages: [],
    mode: 'idle',
    voiceReplies: true,
    highlight: [],
    review: null,
  };
}

function patchReview(state: PantryState, items: (items: DetectedItem[]) => DetectedItem[]): PantryState {
  return state.review ? { ...state, review: { ...state.review, items: items(state.review.items) } } : state;
}

const sameName = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

function reducer(state: PantryState, action: Action): PantryState {
  switch (action.type) {
    case 'sync_state': {
      // Flash items the server added or changed since we last saw them.
      const before = new Map(state.inventory.map((i) => [i.id, i]));
      const changed = action.state.inventory
        .filter((i) => {
          const prev = before.get(i.id);
          return !prev || prev.quantity !== i.quantity || prev.expiresOn !== i.expiresOn;
        })
        .map((i) => i.id);
      return {
        ...state,
        ...action.state,
        preferences: action.state.preferences ?? state.preferences,
        highlight: state.inventory.length ? changed : [],
      };
    }
    case 'add_stock': {
      const inventory = [...state.inventory];
      const touched: string[] = [];
      for (const item of action.items) {
        const i = inventory.findIndex((x) => sameName(x.name, item.name));
        if (i < 0) {
          inventory.push(item);
          touched.push(item.id);
          continue;
        }
        const prev = inventory[i];
        if (prev.unit === item.unit) {
          // Keep the older stock's date while it's still good, so it gets used up first.
          const keepOldDate = daysUntil(prev.expiresOn) >= 0 && prev.expiresOn < item.expiresOn;
          inventory[i] = {
            ...prev,
            quantity: Math.round((prev.quantity + item.quantity) * 100) / 100,
            expiresOn: keepOldDate ? prev.expiresOn : item.expiresOn,
          };
        } else {
          inventory[i] = { ...item, id: prev.id };
        }
        touched.push(prev.id);
      }
      return { ...state, inventory, highlight: touched };
    }
    case 'open_review':
      return { ...state, review: { images: action.images, status: 'analyzing', items: [] } };
    case 'set_review_items':
      return state.review ? { ...state, review: { ...state.review, status: 'ready', items: action.items } } : state;
    case 'update_review_item':
      return patchReview(state, (items) => items.map((i) => (i.id === action.id ? { ...i, ...action.patch } : i)));
    case 'remove_review_item':
      return patchReview(state, (items) => items.filter((i) => i.id !== action.id));
    case 'add_review_item':
      return patchReview(state, (items) => [
        ...items,
        { id: uid(), name: '', emoji: '🛒', quantity: 1, unit: 'pcs', category: 'pantry', expiresInDays: 7, confidence: 1 },
      ]);
    case 'close_review':
      return { ...state, review: null };
    case 'set_meals':
      return { ...state, meals: action.meals };
    case 'add_grocery': {
      const fresh = action.items.filter((item) => !state.grocery.some((g) => !g.checked && sameName(g.name, item.name)));
      return { ...state, grocery: [...state.grocery, ...fresh] };
    }
    case 'toggle_grocery':
      return { ...state, grocery: state.grocery.map((g) => (g.id === action.id ? { ...g, checked: !g.checked } : g)) };
    case 'clear_checked':
      return { ...state, grocery: state.grocery.filter((g) => !g.checked) };
    case 'add_timer':
      return { ...state, timers: [...state.timers, action.timer] };
    case 'remove_timer':
      return { ...state, timers: state.timers.filter((t) => t.id !== action.id) };
    case 'add_message':
      return { ...state, messages: [...state.messages, action.message] };
    case 'patch_message':
      return { ...state, messages: state.messages.map((m) => (m.id === action.id ? { ...m, ...action.patch } : m)) };
    case 'upsert_tool_call':
      return {
        ...state,
        messages: state.messages.map((m) => {
          if (m.id !== action.messageId) return m;
          const exists = m.toolCalls.some((c) => c.id === action.call.id);
          const toolCalls = exists
            ? m.toolCalls.map((c) => (c.id === action.call.id ? action.call : c))
            : [...m.toolCalls, action.call];
          return { ...m, toolCalls };
        }),
      };
    case 'set_mode':
      return state.mode === action.mode ? state : { ...state, mode: action.mode };
    case 'set_voice':
      return { ...state, voiceReplies: action.on };
    case 'set_highlight':
      return { ...state, highlight: action.ids };
  }
}

interface PantryContextValue {
  state: PantryState;
  dispatch: (action: Action) => void;
  /** Latest state, including writes not yet rendered. */
  getState: () => PantryState;
}

const PantryContext = createContext<PantryContextValue | null>(null);

export function PantryProvider({ children }: { children: ReactNode }) {
  const [state, reactDispatch] = useReducer(reducer, undefined, initialState);
  const stateRef = useRef(state);

  // Mirror every action into a ref so a chain of agent tool calls sees its own writes
  // before React re-renders (e.g. plan_meals followed by build_grocery_list).
  const dispatch = useCallback((action: Action) => {
    stateRef.current = reducer(stateRef.current, action);
    reactDispatch(action);
  }, []);
  const getState = useCallback(() => stateRef.current, []);

  useEffect(() => {
    if (!HAS_SERVER) return;
    api
      .state()
      .then((s) => dispatch({ type: 'sync_state', state: s }))
      .catch((err) => console.error('Could not load pantry from server', err));
  }, [dispatch]);

  useEffect(() => {
    if (!state.highlight.length) return;
    const t = setTimeout(() => dispatch({ type: 'set_highlight', ids: [] }), 2400);
    return () => clearTimeout(t);
  }, [state.highlight, dispatch]);

  const value = useMemo(() => ({ state, dispatch, getState }), [state, dispatch, getState]);
  return <PantryContext.Provider value={value}>{children}</PantryContext.Provider>;
}

export function usePantry(): PantryContextValue {
  const ctx = useContext(PantryContext);
  if (!ctx) throw new Error('usePantry must be used inside <PantryProvider>');
  return ctx;
}
