import { Eye, UserPlus, Trash2 } from 'lucide-react';
import type { ListCardProps } from '../../typefiles';

export function ListCard({ list, onViewEmails, onAddRecipients, onDelete, selectMode, selected, onToggleSelect }: ListCardProps) {
  return (
    <div className="flex h-full min-w-0 max-w-full flex-col overflow-hidden bg-white p-6 shadow">
      <div className="mb-4 min-w-0">
        <div className="flex min-w-0 items-start gap-2">
          {selectMode && (
            <input
              type="checkbox"
              checked={!!selected}
              onChange={() => onToggleSelect?.(list.id)}
              className="mt-1 h-4 w-4 shrink-0 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
          )}
          <div className="min-w-0 flex-1">
            <h3
              className="line-clamp-2 min-h-10 wrap-anywhere font-semibold leading-5 text-gray-900"
              title={list.name}
            >
              {list.name}
            </h3>
            {list.description && (
              <p className="mt-1 line-clamp-2 wrap-anywhere text-sm text-gray-500" title={list.description}>
                {list.description}
              </p>
            )}
          </div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4 text-sm mb-4">
        <div>
          <p className="text-gray-500">Total</p>
          <p className="text-xl font-bold text-gray-900">{list.recipient_count}</p>
        </div>
        <div>
          <p className="text-gray-500">Active</p>
          <p className="text-xl font-bold text-green-600">{list.active_count}</p>
        </div>
      </div>
      <div className="text-xs text-gray-400 mb-4">
        Created {new Date(list.created_at).toLocaleDateString()}
      </div>
      <div className="mt-auto flex min-w-0 gap-2">
        <button
          onClick={() => onViewEmails(list.id, list.name)}
          className="flex min-w-0 flex-1 items-center justify-center gap-1.5 bg-gray-50 py-1.5 text-sm text-gray-700 transition-colors hover:bg-gray-100"
        >
          <Eye size={14} className="shrink-0" /> <span className="truncate">View Emails</span>
        </button>
        <button
          onClick={() => onAddRecipients(list.id)}
          className="flex min-w-0 flex-1 items-center justify-center gap-1.5 bg-indigo-50 py-1.5 text-sm text-indigo-600 transition-colors hover:bg-indigo-100"
        >
          <UserPlus size={14} className="shrink-0" /> <span className="truncate">Add</span>
        </button>
        <button
          onClick={() => onDelete(list.id)}
          className="shrink-0 bg-red-50 px-3 py-1.5 text-red-600 transition-colors hover:bg-red-100"
          aria-label={`Delete ${list.name}`}
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}
