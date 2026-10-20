// Client for the Pantry Pilot server (pantry-pilot/server). Enabled when VITE_API_URL is set:
// a full URL in development, or "/" when the server shares the app's domain (CloudFront).
// Unset, the app runs on the in-browser mock agent.
import { accessToken, signOut, type AuthSettings } from '../auth/cognito';
import type { DetectedItem, GroceryItem, KitchenTimer, Meal, PantryItem, ToolCall } from '../types';

const configured: string | undefined = import.meta.env.VITE_API_URL;
export const HAS_SERVER = Boolean(configured);
/** Base URL for API calls; "" means same origin. */
export const API_URL = (configured ?? '').replace(/\/$/, '');

export interface ServerState {
  inventory: PantryItem[];
  meals: Meal[];
  grocery: GroceryItem[];
  timers: KitchenTimer[];
}

/** Detections arrive in the server's snake_case shape. */
interface WireDetection {
  id: string;
  name: string;
  emoji: string;
  quantity: number;
  unit: string;
  category: DetectedItem['category'];
  expires_in_days: number;
  confidence: number;
}

export type ServerEvent =
  | { type: 'tool_start'; id: string; name: ToolCall['name']; args: Record<string, unknown> }
  | { type: 'tool_end'; id: string; name: ToolCall['name']; ok: boolean; summary: string; durationMs: number }
  | { type: 'detections'; items: DetectedItem[] }
  | { type: 'reply'; text: string }
  | { type: 'state'; state: ServerState };

export interface TurnBody {
  text?: string;
  images?: string[];
  confirmItems?: DetectedItem[];
}

function toDetected(w: WireDetection): DetectedItem {
  return { ...w, expiresInDays: w.expires_in_days };
}

/** fetch() against the server with the signed-in user's token; a 401 signs them out. */
async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await accessToken();
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (response.status === 401 && token) signOut();
  return response;
}

/** POST one turn and yield the server-sent events as they stream in. */
export async function* streamTurn(body: TurnBody): AsyncGenerator<ServerEvent> {
  const response = await apiFetch('/api/turn', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) throw new Error(`Server error ${response.status}`);

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary: number;
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = chunk
        .split('\n')
        .filter((line) => line.startsWith('data: '))
        .map((line) => line.slice(6))
        .join('\n');
      if (!data) continue;
      const event = JSON.parse(data);
      yield event.type === 'detections' ? { type: 'detections', items: (event.items as WireDetection[]).map(toDetected) } : event;
    }
  }
}

async function post(path: string, body: unknown): Promise<ServerState> {
  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`Server error ${response.status}`);
  return response.json();
}

export interface NewPantryItem {
  name: string;
  quantity: number;
  unit: string;
  category: DetectedItem['category'];
  emoji: string;
  expiresInDays: number;
}

export interface Connector {
  id: 'zepto' | 'swiggy';
  name: string;
  connected: boolean;
  /** Sign-in returns straight to the app (/api/connectors/{id}/callback); no pasting. */
  directReturn: boolean;
  /** Sign-in started, waiting for the pasted http://localhost/callback address. */
  pending: boolean;
  /** Epoch seconds. */
  lastSync: number | null;
  importedOrders: number;
}

interface ConnectorResponse {
  connectors: Connector[];
  authUrl?: string;
  sync?: { summary: string; orders: string[]; items: string[] };
  state?: ServerState;
}

async function connectorCall(path: string, body: unknown = {}): Promise<ConnectorResponse> {
  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error ?? `Server error ${response.status}`);
  return data;
}

export interface KrogerStatus {
  enabled: boolean;
  connected: boolean;
}

/** A Kroger product, shown exactly as Kroger returns it (their API terms). */
export interface KrogerProduct {
  upc: string;
  description: string;
  brand: string | null;
  size: string | null;
  image: string | null;
}

export interface KrogerMatch {
  item: string;
  products: KrogerProduct[];
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function krogerCall(path: string, body: unknown = {}): Promise<any> {
  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error ?? `Server error ${response.status}`);
  return data;
}

export interface ServerHealth {
  nova: 'bedrock' | 'mock';
  model: string | null;
  tools: number;
}

export const api = {
  /** Public: whether sign-in is on, and the Cognito settings to sign in with. */
  config: async (): Promise<{ auth: AuthSettings | null }> => {
    const response = await fetch(`${API_URL}/api/config`);
    if (!response.ok) throw new Error(`Server error ${response.status}`);
    return response.json();
  },
  /** Public: creates the account on a first email-code sign-in. */
  ensureAccount: async (email: string): Promise<void> => {
    const response = await fetch(`${API_URL}/api/auth/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error ?? 'Could not start sign-in.');
  },
  /** A signed Amazon Transcribe streaming URL for hands-free voice. */
  voiceStream: async (lang: string): Promise<{ url: string }> => {
    const response = await apiFetch(`/api/voice/stream?lang=${encodeURIComponent(lang)}`);
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error ?? `Server error ${response.status}`);
    return response.json();
  },
  health: async (): Promise<ServerHealth> => {
    const response = await fetch(`${API_URL}/health`);
    if (!response.ok) throw new Error(`Server error ${response.status}`);
    return response.json();
  },
  state: async (): Promise<ServerState> => {
    const response = await apiFetch('/api/state');
    if (!response.ok) throw new Error(`Server error ${response.status}`);
    return response.json();
  },
  connectors: async (): Promise<Connector[]> => {
    const response = await apiFetch('/api/connectors');
    if (!response.ok) throw new Error(`Server error ${response.status}`);
    return (await response.json()).connectors;
  },
  startConnector: (id: Connector['id']) => connectorCall(`/api/connectors/${id}/start`),
  finishConnector: (id: Connector['id'], redirectUrl: string) => connectorCall(`/api/connectors/${id}/finish`, { redirectUrl }),
  syncConnector: (id: Connector['id']) => connectorCall(`/api/connectors/${id}/sync`),
  disconnectConnector: (id: Connector['id']) => connectorCall(`/api/connectors/${id}/disconnect`),
  /** Whether the server has an Instacart key (US shopping-list pages). */
  instacartEnabled: async (): Promise<boolean> => {
    const response = await apiFetch('/api/instacart');
    return response.ok && (await response.json()).enabled === true;
  },
  /** An Instacart page with the open grocery items; the user checks out on Instacart. */
  shopOnInstacart: async (): Promise<{ url: string; items: string[]; summary: string }> => {
    const response = await apiFetch('/api/instacart', { method: 'POST' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error ?? `Server error ${response.status}`);
    return data;
  },
  kroger: async (): Promise<KrogerStatus> => {
    const response = await apiFetch('/api/kroger');
    return response.ok ? response.json() : { enabled: false, connected: false };
  },
  startKroger: async (): Promise<string> => (await krogerCall('/api/kroger/start')).authUrl,
  matchKroger: async (): Promise<KrogerMatch[]> => (await krogerCall('/api/kroger/match')).matches,
  addToKrogerCart: (items: { upc: string; quantity: number }[], modality: 'PICKUP' | 'DELIVERY') =>
    krogerCall('/api/kroger/cart', { items, modality }) as Promise<{ added: number; cartUrl: string }>,
  disconnectKroger: () => krogerCall('/api/kroger/disconnect') as Promise<KrogerStatus>,
  addToPantry: (items: NewPantryItem[]) => post('/api/pantry', { items }),
  addGrocery: (name: string) => post('/api/grocery', { add: name }),
  toggleGrocery: (id: string) => post('/api/grocery', { toggle: id }),
  clearChecked: () => post('/api/grocery', { clearChecked: true }),
  cancelTimer: (id: string) => post('/api/timers/cancel', { id }),
};
