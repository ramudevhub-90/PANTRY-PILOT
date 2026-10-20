import { useCallback } from 'react';
import { HAS_SERVER, api, type NewPantryItem, type ServerState } from './remote';
import { capitalize, isoInDays, uid } from '../lib/format';
import { usePantry } from '../state/store';

/** Direct edits from the dashboard: saved on the server when there is one, otherwise locally. */
export function useKitchenActions() {
  const { dispatch } = usePantry();

  const sync = useCallback(
    (request: Promise<ServerState>) =>
      request.then((state) => dispatch({ type: 'sync_state', state })).catch((err) => console.error(err)),
    [dispatch],
  );

  const addToPantry = useCallback(
    (item: NewPantryItem) => {
      if (HAS_SERVER) return sync(api.addToPantry([item]));
      dispatch({
        type: 'add_stock',
        items: [
          {
            id: uid(),
            name: capitalize(item.name.trim()),
            emoji: item.emoji,
            quantity: item.quantity,
            unit: item.unit,
            category: item.category,
            expiresOn: isoInDays(item.expiresInDays),
            source: 'manual',
          },
        ],
      });
      return Promise.resolve();
    },
    [dispatch, sync],
  );

  const addGrocery = useCallback(
    (name: string) => {
      if (HAS_SERVER) return void sync(api.addGrocery(name));
      dispatch({ type: 'add_grocery', items: [{ id: uid(), name: capitalize(name), checked: false, source: 'manual' }] });
    },
    [dispatch, sync],
  );

  const toggleGrocery = useCallback(
    (id: string) => {
      dispatch({ type: 'toggle_grocery', id }); // optimistic
      if (HAS_SERVER) void sync(api.toggleGrocery(id));
    },
    [dispatch, sync],
  );

  const clearChecked = useCallback(() => {
    dispatch({ type: 'clear_checked' });
    if (HAS_SERVER) void sync(api.clearChecked());
  }, [dispatch, sync]);

  const removeTimer = useCallback(
    (id: string) => {
      dispatch({ type: 'remove_timer', id });
      if (HAS_SERVER) void sync(api.cancelTimer(id));
    },
    [dispatch, sync],
  );

  return { addToPantry, addGrocery, toggleGrocery, clearChecked, removeTimer };
}
