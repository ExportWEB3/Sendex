import { CheckSquare, Square, Trash2, X } from 'lucide-react';
import type { SelectionBarProps } from '../../typefiles';

export function SelectionBar({ count, total, onSelectAll, onDelete, onCancel, deleting, itemLabel }: SelectionBarProps) {
  const allSelected = total > 0 && count === total;
  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-3 bg-indigo-50 border border-indigo-200 rounded-lg px-4 py-2.5 mb-4">
      <button
        onClick={onSelectAll}
        disabled={total === 0}
        className="flex items-center gap-2 text-sm font-medium text-indigo-700 hover:text-indigo-900 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {allSelected ? <CheckSquare size={16} /> : <Square size={16} />}
        {allSelected ? 'Deselect All' : 'Select All'}
      </button>
      <span className="text-sm text-indigo-700">{count} of {total} selected</span>
      <div className="flex-1" />
      <div className="flex items-center gap-2">
        <button
          onClick={onDelete}
          disabled={count === 0 || deleting}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Trash2 size={14} /> {deleting ? 'Deleting...' : `Delete ${count} ${itemLabel}${count === 1 ? '' : 's'}`}
        </button>
        <button
          onClick={onCancel}
          disabled={deleting}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 disabled:opacity-50"
        >
          <X size={14} /> Cancel
        </button>
      </div>
    </div>
  );
}
