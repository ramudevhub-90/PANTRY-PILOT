// Browser-side stand-ins for the Pantry Pilot MCP tools. Names and arguments match the
// server tools so the UI can switch to the real MCP server without changing.
import { RECIPES, mockDetections } from '../data/seed';
import { capitalize, daysUntil, formatClock, isoInDays, joinNames, lower, uid } from '../lib/format';
import type { Action, PantryState } from '../state/store';
import type { Category, GroceryItem, Meal, PantryItem, ToolName } from '../types';

export interface ToolContext {
  getState: () => PantryState;
  dispatch: (action: Action) => void;
}

export interface ToolResult {
  summary: string;
  data: Record<string, unknown>;
}

type Tool = (args: Record<string, unknown>, ctx: ToolContext) => ToolResult;

export function planMeals(inventory: PantryItem[]): Meal[] {
  return RECIPES.map((recipe) => {
    const uses: string[] = [];
    const rescues: string[] = [];
    const missing: string[] = [];
    let rescueScore = 0;
    for (const ing of recipe.ingredients) {
      const item = inventory.find((i) => lower(i.name).includes(ing.key) && daysUntil(i.expiresOn) >= 0);
      if (!item) {
        missing.push(ing.label);
        continue;
      }
      uses.push(item.name);
      const days = daysUntil(item.expiresOn);
      if (days <= 3) {
        rescues.push(item.name);
        rescueScore += days <= 1 ? 3 : 2;
      }
    }
    // Favour recipes that use up expiring food, then ones we can cook without shopping.
    const score = rescueScore * 2 + uses.length - missing.length * 3;
    return { recipe, uses, rescues, missing, score };
  })
    .sort((a, b) => b.score - a.score)
    .slice(0, 3)
    .map(({ recipe, uses, rescues, missing }) => ({
      id: recipe.id,
      name: recipe.name,
      emoji: recipe.emoji,
      minutes: recipe.minutes,
      servings: recipe.servings,
      uses,
      rescues,
      missing,
      steps: recipe.steps,
      timer: recipe.timer,
      reason: rescues.length
        ? `Uses ${joinNames(rescues.map(lower))} before ${rescues.length > 1 ? 'they expire' : 'it expires'}`
        : missing.length
          ? 'Mostly from what you already have'
          : 'You have everything for this',
    }));
}

const getInventory: Tool = (args, { getState }) => {
  const items = getState()
    .inventory.map((i) => ({ name: i.name, quantity: `${i.quantity} ${i.unit}`, days_left: daysUntil(i.expiresOn) }))
    .sort((a, b) => a.days_left - b.days_left);
  const expiring = items.filter((i) => i.days_left <= 2);
  if (args.filter === 'expiring') {
    return { summary: `${expiring.length} items expire within 2 days`, data: { items: items.filter((i) => i.days_left <= 3) } };
  }
  return { summary: `${items.length} items · ${expiring.length} expiring soon`, data: { items } };
};

const planMealsTool: Tool = (_args, { getState, dispatch }) => {
  const meals = planMeals(getState().inventory);
  dispatch({ type: 'set_meals', meals });
  return { summary: `Ranked ${RECIPES.length} recipes · top pick: ${meals[0].name}`, data: { meals: meals.map((m) => m.name) } };
};

const buildGroceryList: Tool = (args, { getState, dispatch }) => {
  const state = getState();
  let wanted: Pick<GroceryItem, 'name' | 'note' | 'source'>[];
  if (Array.isArray(args.items)) {
    wanted = (args.items as string[]).map((name) => ({ name: capitalize(name), note: 'You asked', source: 'voice' }));
  } else {
    const meal = state.meals.find((m) => m.id === args.meal_id) ?? state.meals[0];
    wanted = [
      ...(meal ? meal.missing.map((name) => ({ name, note: `For ${meal.name}`, source: 'agent' as const })) : []),
      ...state.inventory
        .filter((i) => daysUntil(i.expiresOn) < 0)
        .map((i) => ({ name: i.name, note: 'Expired', source: 'agent' as const })),
    ];
  }
  const added = wanted.filter((w) => !state.grocery.some((g) => !g.checked && lower(g.name) === lower(w.name)));
  dispatch({ type: 'add_grocery', items: added.map((w) => ({ ...w, id: uid(), checked: false })) });
  return {
    summary: added.length ? `Added ${joinNames(added.map((a) => a.name))}` : 'Nothing new to add',
    data: { added: added.map((a) => a.name) },
  };
};

const getGroceryList: Tool = (_args, { getState }) => {
  const items = getState().grocery.filter((g) => !g.checked).map((g) => g.name);
  return { summary: `${items.length} items to buy`, data: { items } };
};

const setTimer: Tool = (args, { dispatch }) => {
  const seconds = Number(args.seconds);
  const label = String(args.label ?? 'kitchen');
  dispatch({ type: 'add_timer', timer: { id: uid(), label, totalSeconds: seconds, endsAt: Date.now() + seconds * 1000 } });
  return { summary: `${label} · ${formatClock(seconds)}`, data: { label, seconds } };
};

/** Detections at or above this confidence don't need the user to double-check them. */
export const CONFIDENT = 0.85;

const scanPantry: Tool = (_args, { dispatch }) => {
  // Fixed result for the mock. The server version sends the photos to Amazon Nova 2 Lite.
  // Detections go to the review screen; nothing is saved until update_inventory runs.
  const items = mockDetections();
  dispatch({ type: 'set_review_items', items });
  const unsure = items.filter((i) => i.confidence < CONFIDENT).length;
  return {
    summary: `Detected ${items.length} items${unsure ? ` · ${unsure} to check` : ''}`,
    data: {
      items: items.map((i) => ({ name: i.name, quantity: i.quantity, unit: i.unit, confidence: i.confidence })),
      unsure,
    },
  };
};

interface StockArg {
  name: string;
  quantity: number;
  unit: string;
  expires_in_days: number;
  emoji?: string;
  category?: Category;
}

const updateInventory: Tool = (args, { dispatch }) => {
  const items: PantryItem[] = (args.items as StockArg[]).map((s) => ({
    id: uid(),
    name: capitalize(s.name.trim()),
    emoji: s.emoji ?? '🛒',
    quantity: s.quantity,
    unit: s.unit,
    category: s.category ?? 'pantry',
    expiresOn: isoInDays(s.expires_in_days),
    source: 'scan',
  }));
  dispatch({ type: 'add_stock', items });
  return { summary: `Added ${items.length} items to stock`, data: { added: items.map((i) => i.name) } };
};

export const TOOLS: Record<ToolName, Tool> = {
  scan_pantry: scanPantry,
  update_inventory: updateInventory,
  get_inventory: getInventory,
  plan_meals: planMealsTool,
  build_grocery_list: buildGroceryList,
  get_grocery_list: getGroceryList,
  set_timer: setTimer,
};
