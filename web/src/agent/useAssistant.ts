import { useCallback } from 'react';
import { runMockAgent, type AgentInput } from './mockAgent';
import { HAS_SERVER, streamTurn, type TurnBody } from './remote';
import { speak, stopSpeaking } from '../hooks/useSpeech';
import { uid } from '../lib/format';
import { usePantry, type Action, type PantryState } from '../state/store';
import type { ToolCall } from '../types';

type Ctx = { dispatch: (action: Action) => void; getState: () => PantryState };

/** Runs a turn on the Pantry Pilot server, mirroring its events into the store. */
async function runRemoteTurn(input: AgentInput, replyId: string, { dispatch, getState }: Ctx): Promise<string> {
  const body: TurnBody = input.images
    ? { images: input.images }
    : input.confirmItems
      ? { confirmItems: input.confirmItems }
      : { text: input.text };
  if (input.images) dispatch({ type: 'open_review', images: input.images });

  const calls = new Map<string, ToolCall>();
  let reply = '';
  try {
    for await (const event of streamTurn(body)) {
      switch (event.type) {
        case 'tool_start': {
          const call: ToolCall = { id: event.id, name: event.name, args: event.args, status: 'running' };
          calls.set(event.id, call);
          dispatch({ type: 'upsert_tool_call', messageId: replyId, call });
          break;
        }
        case 'tool_end': {
          const start = calls.get(event.id);
          if (!start) break;
          const call: ToolCall = { ...start, status: event.ok ? 'done' : 'error', summary: event.summary, durationMs: event.durationMs };
          dispatch({ type: 'upsert_tool_call', messageId: replyId, call });
          break;
        }
        case 'detections':
          dispatch({ type: 'set_review_items', items: event.items });
          break;
        case 'reply':
          reply = event.text;
          break;
        case 'state':
          dispatch({ type: 'sync_state', state: event.state });
          break;
      }
    }
  } finally {
    // Don't leave the review screen spinning if the scan failed.
    if (input.images && getState().review?.status === 'analyzing') dispatch({ type: 'close_review' });
  }
  return reply;
}

async function runMockTurn(input: AgentInput, replyId: string, ctx: Ctx): Promise<string> {
  let reply = '';
  for await (const event of runMockAgent(input, ctx)) {
    if (event.type === 'reply') reply = event.text;
    else ctx.dispatch({ type: 'upsert_tool_call', messageId: replyId, call: event.call });
  }
  return reply;
}

export function useAssistant() {
  const { dispatch, getState } = usePantry();

  const say = useCallback(
    (text: string) => {
      if (!getState().voiceReplies) {
        dispatch({ type: 'set_mode', mode: 'idle' });
        return;
      }
      dispatch({ type: 'set_mode', mode: 'speaking' });
      speak(text, () => {
        if (getState().mode === 'speaking') dispatch({ type: 'set_mode', mode: 'idle' });
      });
    },
    [dispatch, getState],
  );

  const ask = useCallback(
    async (text: string, extra: Omit<AgentInput, 'text'> = {}) => {
      if (getState().mode === 'thinking') return;
      stopSpeaking();
      dispatch({ type: 'add_message', message: { id: uid(), role: 'user', text, images: extra.images, toolCalls: [] } });
      const replyId = uid();
      dispatch({ type: 'add_message', message: { id: replyId, role: 'assistant', text: '', toolCalls: [], pending: true } });
      dispatch({ type: 'set_mode', mode: 'thinking' });

      let reply = '';
      try {
        const run = HAS_SERVER ? runRemoteTurn : runMockTurn;
        reply = await run({ text, ...extra }, replyId, { dispatch, getState });
      } catch (err) {
        console.error(err);
        reply = HAS_SERVER
          ? "Sorry, I couldn't reach the Pantry Pilot server. Is it running?"
          : 'Sorry, something went wrong on my side. Could you try that again?';
      }
      dispatch({ type: 'patch_message', id: replyId, patch: { text: reply || 'Done.', pending: false } });
      say(reply || 'Done.');
    },
    [dispatch, getState, say],
  );

  return { ask, say };
}
