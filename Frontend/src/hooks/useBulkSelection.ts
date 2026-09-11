import { useCallback, useMemo, useReducer } from 'react';
import type {
  BulkSelectionAction,
  BulkSelectionController,
  BulkSelectionState,
} from '../../typefiles';

const INITIAL_SELECTION_STATE: BulkSelectionState = {
  enabled: false,
  selectedIds: new Set<number>(),
  isDeleting: false,
};

function bulkSelectionReducer(
  state: BulkSelectionState,
  action: BulkSelectionAction,
): BulkSelectionState {
  switch (action.type) {
    case 'toggle-mode':
      return {
        ...state,
        enabled: !state.enabled,
        selectedIds: new Set<number>(),
      };
    case 'toggle-one': {
      const selectedIds = new Set(state.selectedIds);
      if (selectedIds.has(action.id)) selectedIds.delete(action.id);
      else selectedIds.add(action.id);
      return { ...state, selectedIds };
    }
    case 'toggle-all':
      return {
        ...state,
        selectedIds: state.selectedIds.size === action.ids.length
          ? new Set<number>()
          : new Set(action.ids),
      };
    case 'delete-started':
      return { ...state, isDeleting: true };
    case 'delete-finished':
      return action.reset
        ? { ...INITIAL_SELECTION_STATE, selectedIds: new Set<number>() }
        : { ...state, isDeleting: false };
    case 'reset':
      return { ...INITIAL_SELECTION_STATE, selectedIds: new Set<number>() };
    default:
      return state;
  }
}

export function useBulkSelection(itemIds: number[]): BulkSelectionController {
  const [state, dispatch] = useReducer(bulkSelectionReducer, INITIAL_SELECTION_STATE);

  const toggleMode = useCallback(() => dispatch({ type: 'toggle-mode' }), []);
  const toggleOne = useCallback((id: number) => dispatch({ type: 'toggle-one', id }), []);
  const toggleAll = useCallback(
    () => dispatch({ type: 'toggle-all', ids: itemIds }),
    [itemIds],
  );
  const beginDelete = useCallback(() => dispatch({ type: 'delete-started' }), []);
  const finishDelete = useCallback(
    (reset = false) => dispatch({ type: 'delete-finished', reset }),
    [],
  );
  const reset = useCallback(() => dispatch({ type: 'reset' }), []);

  return useMemo(() => ({
    ...state,
    selectedCount: state.selectedIds.size,
    allSelected: itemIds.length > 0 && state.selectedIds.size === itemIds.length,
    toggleMode,
    toggleOne,
    toggleAll,
    beginDelete,
    finishDelete,
    reset,
  }), [
    beginDelete,
    finishDelete,
    itemIds.length,
    reset,
    state,
    toggleAll,
    toggleMode,
    toggleOne,
  ]);
}
