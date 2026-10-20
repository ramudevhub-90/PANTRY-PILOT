// A scripted stand-in for the Nova-powered agent. It routes requests by keyword, calls the
// same tools the real agent will, and keeps one pending follow-up so "yes" works.
import { TOOLS, type ToolContext, type ToolResult } from './tools';
import { capitalize, daysUntil, durationWords, joinNames, lower, uid, whenPhrase } from '../lib/format';
import type { DetectedItem, ToolCall, ToolName } from '../types';

export interface AgentInput {
  text: string;
  /** Grocery photos to scan. */
  images?: string[];
  /** Reviewed scan results to add to the pantry. */
  confirmItems?: DetectedItem[];
}

export type AgentEvent = { type: 'tool'; call: ToolCall } | { type: 'reply'; text: string };

type FollowUp =
  | { kind: 'timer'; label: string; minutes: number }
  | { kind: 'grocery'; mealId: string }
  | { kind: 'plan' };

interface ItemSummary {
  name: string;
  days_left: number;
}

let followUp: FollowUp | null = null;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const reply = (text: string): AgentEvent => ({ type: 'reply', text });

const AFFIRMATIVE = /^(yes|yeah|yep|yup|sure|ok|okay|please|do it|go ahead|sounds good)\b/;
const NEGATIVE = /^(no|nope|nah|not now)\b/;

const NUMBER_WORDS: Record<string, number> = {
  a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8,
  nine: 9, ten: 10, fifteen: 15, twenty: 20, thirty: 30, forty: 40,
};
const AMOUNT = '(\\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty)';
const UNIT = '(hours?|hrs?|minutes?|mins?|seconds?|secs?)';

async function* callTool(name: ToolName, args: Record<string, unknown>, ctx: ToolContext): AsyncGenerator<AgentEvent, ToolResult> {
  const call: ToolCall = { id: uid(), name, args, status: 'running' };
  yield { type: 'tool', call };
  const started = performance.now();
  await sleep(300 + Math.random() * 500);
  const result = TOOLS[name](args, ctx);
  yield { type: 'tool', call: { ...call, status: 'done', summary: result.summary, durationMs: Math.round(performance.now() - started) } };
  return result;
}

function parseTimer(t: string): { seconds?: number; label: string } | null {
  if (!/\btimer\b|\bremind me in\b/.test(t)) return null;
  const m = t.match(new RegExp(`${AMOUNT}[\\s-]*${UNIT}\\b`));
  const amount = m ? Number(m[1]) || NUMBER_WORDS[m[1]] : undefined;
  const unit = m?.[2] ?? '';
  const seconds = amount ? amount * (unit.startsWith('h') ? 3600 : unit.startsWith('s') ? 1 : 60) : undefined;
  const labelMatch = t.match(/\bfor (?:the |my )?([a-z][a-z -]*?)(?: for\b| in\b|[.!?]|$)/);
  const isDuration = labelMatch && new RegExp(`^${AMOUNT}\\b|^${UNIT}`).test(labelMatch[1]);
  return { seconds, label: labelMatch && !isDuration ? labelMatch[1].trim() : 'kitchen' };
}

/** "we're out of milk" / "add eggs and butter to my list" → item names. */
function parseGroceryAdd(t: string): string[] | null {
  const m = t.match(/\b(?:out of|add|buy|pick up)\s+(?:some\s+|more\s+)?(.+?)(?:\s+(?:to|on)\s+(?:my|the)\s+(?:grocery\s+|shopping\s+)?list)?[.!?]*$/);
  if (!m) return null;
  const items = m[1]
    .split(/\s*(?:,|\band\b)\s*/)
    .map((s) => s.replace(/^(?:the|some|a|an)\s+/, '').trim())
    .filter(Boolean);
  if (!items.length || items.some((i) => /\b(list|missing|ingredients|everything|it|them)\b/.test(i))) return null;
  return items;
}

async function* timerFlow(seconds: number, label: string, ctx: ToolContext): AsyncGenerator<AgentEvent> {
  yield* callTool('set_timer', { label, seconds }, ctx);
  yield reply(label === 'kitchen' ? `Okay, ${durationWords(seconds)}, starting now.` : `Okay, ${durationWords(seconds)} on the ${label} timer.`);
}

async function* dinnerFlow(ctx: ToolContext): AsyncGenerator<AgentEvent> {
  yield* callTool('get_inventory', { filter: 'expiring' }, ctx);
  yield* callTool('plan_meals', { prefer: 'use_expiring', limit: 3 }, ctx);
  const { meals, inventory } = ctx.getState();
  const top = meals[0];

  let text = `I'd make ${top.name}. `;
  if (top.rescues.length) {
    const days = top.rescues.map((n) => {
      const item = inventory.find((i) => i.name === n);
      return item ? daysUntil(item.expiresOn) : 3;
    });
    const when = days.every((d) => d === days[0]) ? whenPhrase(days[0]) : `within ${Math.max(...days)} days`;
    const verb = top.rescues.length > 1 ? 'expire' : 'expires';
    text = `Your ${joinNames(top.rescues.map(lower))} ${verb} ${when}, so I'd make ${top.name}. `;
  }

  if (top.missing.length) {
    const pronoun = top.missing.length > 1 ? 'them' : 'it';
    text += `It takes ${top.minutes} minutes and you're only missing ${joinNames(top.missing.map(lower))}. Want me to add ${pronoun} to your grocery list?`;
    followUp = { kind: 'grocery', mealId: top.id };
  } else if (top.timer) {
    text += `It takes ${top.minutes} minutes and you have everything. Want me to start a ${top.timer.minutes}-minute timer for the ${top.timer.label}?`;
    followUp = { kind: 'timer', ...top.timer };
  } else {
    text += `It takes ${top.minutes} minutes and you have everything.`;
  }
  yield reply(text);
}

async function* groceryFlow(ctx: ToolContext, mealId?: string): AsyncGenerator<AgentEvent> {
  if (!ctx.getState().meals.length) yield* callTool('plan_meals', { prefer: 'use_expiring', limit: 3 }, ctx);
  const { meals } = ctx.getState();
  const meal = meals.find((m) => m.id === mealId) ?? meals[0];
  const result = yield* callTool('build_grocery_list', { meal_id: meal.id }, ctx);
  const added = result.data.added as string[];
  yield reply(
    added.length
      ? `Added ${joinNames(added.map(lower))} to your grocery list for the ${lower(meal.name)}.`
      : `You already have everything for ${meal.name}, so your list hasn't changed.`,
  );
}

async function* expiringFlow(ctx: ToolContext): AsyncGenerator<AgentEvent> {
  const result = yield* callTool('get_inventory', { filter: 'expiring' }, ctx);
  const items = result.data.items as ItemSummary[];
  if (!items.length) {
    yield reply('Nothing is about to expire. Nice work!');
    return;
  }
  const byDay = new Map<number, string[]>();
  for (const i of items) byDay.set(i.days_left, [...(byDay.get(i.days_left) ?? []), lower(i.name)]);
  const parts = [...byDay].map(([days, names]) => `${joinNames(names)} ${whenPhrase(days)}`);
  followUp = { kind: 'plan' };
  yield reply(`Use these soon: ${parts.join('; ')}. Want a dinner idea that uses them?`);
}

async function* inventoryFlow(ctx: ToolContext): AsyncGenerator<AgentEvent> {
  const result = yield* callTool('get_inventory', {}, ctx);
  const items = result.data.items as ItemSummary[];
  const expiring = items.filter((i) => i.days_left <= 2).map((i) => lower(i.name));
  const tail = expiring.length
    ? `${capitalize(joinNames(expiring))} ${expiring.length > 1 ? 'need' : 'needs'} using in the next two days.`
    : 'Nothing is close to expiring.';
  yield reply(`You have ${items.length} items in your pantry. ${tail}`);
}

async function* scanFlow(images: string[], ctx: ToolContext): AsyncGenerator<AgentEvent> {
  ctx.dispatch({ type: 'open_review', images });
  const result = yield* callTool('scan_pantry', { photos: images.length, model: 'amazon.nova-2-lite-v1:0' }, ctx);
  const count = (result.data.items as unknown[]).length;
  const unsure = result.data.unsure as number;
  const check = unsure ? `, and ${unsure} ${unsure > 1 ? 'need' : 'needs'} a quick check` : '';
  yield reply(`I found ${count} items${check}. Fix any quantities, then tap Add to pantry.`);
}

async function* stockFlow(items: DetectedItem[], ctx: ToolContext): AsyncGenerator<AgentEvent> {
  yield* callTool(
    'update_inventory',
    {
      action: 'add',
      items: items.map((i) => ({
        name: i.name,
        quantity: i.quantity,
        unit: i.unit,
        expires_in_days: i.expiresInDays,
        emoji: i.emoji,
        category: i.category,
      })),
    },
    ctx,
  );
  followUp = { kind: 'plan' };
  yield reply(`Done, I added ${items.length} items to your pantry. Want a dinner idea that uses them?`);
}

export async function* runMockAgent(input: AgentInput, ctx: ToolContext): AsyncGenerator<AgentEvent> {
  const text = input.text.trim().toLowerCase();
  await sleep(250);

  if (input.images?.length) {
    followUp = null;
    yield* scanFlow(input.images, ctx);
    return;
  }
  if (input.confirmItems) {
    followUp = null;
    yield* stockFlow(input.confirmItems, ctx);
    return;
  }

  const pending = followUp;
  followUp = null;
  if (pending && AFFIRMATIVE.test(text)) {
    if (pending.kind === 'timer') yield* timerFlow(pending.minutes * 60, pending.label, ctx);
    else if (pending.kind === 'grocery') yield* groceryFlow(ctx, pending.mealId);
    else yield* dinnerFlow(ctx);
    return;
  }
  if (pending && NEGATIVE.test(text)) {
    yield reply('No problem.');
    return;
  }

  const timer = parseTimer(text);
  if (timer) {
    if (timer.seconds) yield* timerFlow(timer.seconds, timer.label, ctx);
    else yield reply('How long should I set the timer for?');
    return;
  }

  const adds = parseGroceryAdd(text);
  if (adds) {
    const result = yield* callTool('build_grocery_list', { items: adds }, ctx);
    const added = result.data.added as string[];
    yield reply(
      added.length
        ? `Got it, I added ${joinNames(added.map(lower))} to your grocery list.`
        : `${capitalize(joinNames(adds))} ${adds.length > 1 ? 'are' : 'is'} already on your list.`,
    );
    return;
  }

  if (/\b(grocery|groceries|shopping|buy|missing)\b|what do i need/.test(text)) return yield* groceryFlow(ctx);
  if (/\b(dinner|lunch|breakfast|cook|make|eat|recipe|meal|hungry|tonight|plan)\b/.test(text)) return yield* dinnerFlow(ctx);
  if (/expir|going bad|go bad|use up|using up|spoil|about to/.test(text)) return yield* expiringFlow(ctx);
  if (/what do i have|inventory|in my pantry|in stock|what's in|whats in/.test(text)) return yield* inventoryFlow(ctx);
  if (/scan|photo|picture|fridge|camera/.test(text)) {
    yield reply("Tap the camera button and take a photo of your fridge or shelf. I'll add what I see to your pantry.");
    return;
  }

  const greeting = /^(hi|hello|hey)\b/.test(text) ? 'Hi! ' : '';
  yield reply(
    `${greeting}I can plan dinner from what you have, tell you what's about to expire, keep your grocery list and run kitchen timers. Try asking, "What can I make for dinner?"`,
  );
}
