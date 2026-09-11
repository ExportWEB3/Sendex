export function StatusBadge({ status, text }: { status: string; text?: string }) {
  const colors: Record<string, string> = {
    // Campaign statuses
    draft: 'bg-gray-100 text-gray-700',
    scheduled: 'bg-indigo-50 text-indigo-700',
    running: 'bg-emerald-50 text-emerald-700',
    cooldown: 'bg-amber-50 text-amber-700',
    paused: 'bg-amber-50 text-amber-700',
    completed: 'bg-indigo-100 text-indigo-700',
    cancelled: 'bg-red-50 text-red-700',
    // Inbox states
    not_started: 'bg-gray-100 text-gray-700',
    warming_up: 'bg-amber-50 text-amber-700',
    warmed_up: 'bg-emerald-50 text-emerald-700',
    disabled: 'bg-red-50 text-red-700',
    // Generic
    active: 'bg-emerald-50 text-emerald-700',
    inactive: 'bg-gray-100 text-gray-700',
    pending: 'bg-amber-50 text-amber-700',
    error: 'bg-red-50 text-red-700',
  };

  return (
    <span className={`px-2 py-1 text-xs font-medium rounded-full ${colors[status] || 'bg-gray-100 text-gray-800'}`}>
      {text || status.replace(/_/g, ' ')}
    </span>
  );
}
