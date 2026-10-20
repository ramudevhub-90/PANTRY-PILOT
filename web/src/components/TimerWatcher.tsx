import { useEffect, useRef } from 'react';
import { useAssistant } from '../agent/useAssistant';
import { useKitchenActions } from '../agent/useKitchenActions';
import { uid } from '../lib/format';
import { chime } from '../lib/media';
import { usePantry } from '../state/store';

/** Announces finished timers with a chime, a transcript message and a spoken reply. */
export function TimerWatcher() {
  const { state, dispatch } = usePantry();
  const { say } = useAssistant();
  const { removeTimer } = useKitchenActions();
  const fired = useRef(new Set<string>());

  useEffect(() => {
    if (!state.timers.length) return;
    const id = setInterval(() => {
      for (const t of state.timers) {
        if (t.endsAt > Date.now() || fired.current.has(t.id)) continue;
        fired.current.add(t.id);
        const text = t.label === 'kitchen' ? 'Your timer is done.' : `Your ${t.label} timer is done.`;
        removeTimer(t.id);
        dispatch({ type: 'add_message', message: { id: uid(), role: 'assistant', text: `⏰ ${text}`, toolCalls: [] } });
        chime();
        say(text);
      }
    }, 250);
    return () => clearInterval(id);
  }, [state.timers, dispatch, say, removeTimer]);

  return null;
}
