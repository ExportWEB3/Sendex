import { Eye, UserPlus, Trash2 } from 'lucide-react';
import type { ListCardProps } from '../../typefiles';

export function ListCard({ list, onViewEmails, onAddRecipients, onDelete, selectMode, selected, onToggleSelect }: ListCardProps) {
  return (
    <div className="bg-white rounded-xl shadow p-6">
      <div className="flex justify-between items-start mb-4">
        <div className="flex items-start gap-2">
          {selectMode && (
            <input
              type="checkbox"
              checked={!!selected}
              onChange={() => onToggleSelect?.(list.id)}
              className="mt-1 w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
          )}
          <div>
            <h3 className="font-semibold text-gray-900">{list.name}</h3>
            {list.description && (
              <p className="text-sm text-gray-500 mt-1">{list.description}</p>
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
      <div className="flex gap-2">
        <button
          onClick={() => onViewEmails(list.id, list.name)}
          className="flex-1 py-1.5 text-sm bg-gray-50 text-gray-700 rounded-lg flex items-center justify-center gap-1.5 hover:bg-gray-100 transition-colors"
        >
          <Eye size={14} /> View Emails
        </button>
        <button
          onClick={() => onAddRecipients(list.id)}
          className="flex-1 py-1.5 text-sm bg-indigo-50 text-indigo-600 rounded-lg flex items-center justify-center gap-1.5 hover:bg-indigo-100 transition-colors"
        >
          <UserPlus size={14} /> Add
        </button>
        <button
          onClick={() => onDelete(list.id)}
          className="px-3 py-1.5 bg-red-50 text-red-600 rounded-lg hover:bg-red-100 transition-colors"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}
