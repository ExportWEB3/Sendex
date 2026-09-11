/**
 * Skeleton loading components shown while page data is being fetched.
 * Prevents flash of empty states ("No campaigns yet") before data loads.
 */

const shimmer = 'animate-pulse bg-gray-200 rounded';

function Bar({ className = '' }: { className?: string }) {
  return <div className={`${shimmer} ${className}`} />;
}

/* ─── Stat Card Skeleton ─── */
function StatCardSkeleton() {
  return (
    <div className="bg-white rounded-xl shadow p-4 sm:p-5">
      <div className="flex items-center justify-between mb-3">
        <Bar className="h-3 w-24" />
        <div className={`${shimmer} h-9 w-9 rounded-xl`} />
      </div>
      <Bar className="h-7 w-16 mb-1.5" />
      <Bar className="h-3 w-20" />
    </div>
  );
}

/* ─── Card Skeleton (generic white card with placeholder lines) ─── */
function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="bg-white rounded-xl shadow p-5 space-y-3">
      <div className="flex justify-between items-center">
        <Bar className="h-4 w-32" />
        <Bar className="h-5 w-16 rounded-full" />
      </div>
      {Array.from({ length: lines }).map((_, i) => (
        <Bar key={i} className={`h-3 ${i === 0 ? 'w-3/4' : i === 1 ? 'w-1/2' : 'w-2/3'}`} />
      ))}
    </div>
  );
}

/* ─── Row Skeleton (for table-like list rows) ─── */
function RowSkeleton() {
  return (
    <div className="bg-white rounded-xl shadow p-3 sm:p-4 flex items-center gap-3">
      <div className={`${shimmer} h-8 w-8 rounded-lg flex-shrink-0`} />
      <div className="flex-1 space-y-2">
        <Bar className="h-4 w-40" />
        <Bar className="h-3 w-56" />
      </div>
      <Bar className="h-6 w-16 rounded-full" />
    </div>
  );
}

/* ─── Table Row Skeleton ─── */
function TableRowSkeleton({ cols = 5 }: { cols?: number }) {
  return (
    <tr className="border-b border-gray-50">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-3 py-3">
          <Bar className={`h-3.5 ${i === 0 ? 'w-28' : i === 1 ? 'w-36' : 'w-16'}`} />
        </td>
      ))}
    </tr>
  );
}

/* ═══════════════════════════════════════════════════════
   Page-specific skeletons
   ═══════════════════════════════════════════════════════ */

export function DashboardSkeleton() {
  return (
    <div className="p-4 sm:p-6 space-y-8">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-6">
        {Array.from({ length: 4 }).map((_, i) => <StatCardSkeleton key={i} />)}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl shadow p-6 space-y-3">
          <Bar className="h-5 w-40 mb-2" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
              <div className="space-y-2 flex-1">
                <Bar className="h-4 w-32" />
                <Bar className="h-3 w-20" />
              </div>
              <Bar className="h-5 w-16 rounded-full" />
            </div>
          ))}
        </div>
        <div className="bg-white rounded-xl shadow p-6 space-y-3">
          <Bar className="h-5 w-36 mb-2" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
              <div className="space-y-2 flex-1">
                <Bar className="h-4 w-40" />
                <Bar className="h-3 w-24" />
              </div>
              <Bar className="h-5 w-14 rounded-full" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function CampaignsSkeleton() {
  return (
    <div className="p-3 sm:p-4 md:p-6 space-y-4">
      <div className="flex justify-between items-center">
        <Bar className="h-4 w-52" />
        <Bar className="h-8 w-32 rounded-lg" />
      </div>
      <div className="space-y-3 sm:space-y-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-white rounded-xl shadow p-3 sm:p-4 md:p-5 space-y-3">
            <div className="flex items-center gap-2 mb-1">
              <Bar className="h-4 w-44" />
              <Bar className="h-5 w-16 rounded-full" />
            </div>
            <Bar className="h-3 w-64" />
            <div className="flex gap-2">
              <Bar className="h-5 w-36 rounded" />
              <Bar className="h-5 w-28 rounded" />
            </div>
            <div className="flex gap-2 items-center">
              <Bar className="h-2 flex-1 rounded-full" />
              <Bar className="h-3 w-16" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function InboxesSkeleton() {
  return (
    <div className="p-3 sm:p-4 md:p-6 space-y-4">
      <div className="flex justify-between items-center">
        <Bar className="h-4 w-44" />
        <Bar className="h-8 w-28 rounded-lg" />
      </div>
      {/* Mobile cards */}
      <div className="md:hidden space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <CardSkeleton key={i} lines={4} />
        ))}
      </div>
      {/* Desktop table */}
      <div className="hidden md:block bg-white rounded-xl shadow overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              {['Email', 'SMTP', 'State', 'Warmup', 'Reply-To', 'Actions'].map(h => (
                <th key={h} className="px-3 py-2.5 text-left text-xs font-medium text-gray-500">
                  <Bar className="h-3 w-16" />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: 4 }).map((_, i) => <TableRowSkeleton key={i} cols={6} />)}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function ListsSkeleton() {
  return (
    <div className="p-4 sm:p-6 space-y-6">
      <div className="flex justify-between items-center">
        <Bar className="h-4 w-52" />
        <Bar className="h-8 w-24 rounded-lg" />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="bg-white rounded-xl shadow p-6 space-y-4">
            <div className="flex justify-between">
              <div className="space-y-2">
                <Bar className="h-4 w-28" />
                <Bar className="h-3 w-40" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <Bar className="h-3 w-10" />
                <Bar className="h-6 w-12" />
              </div>
              <div className="space-y-1">
                <Bar className="h-3 w-10" />
                <Bar className="h-6 w-12" />
              </div>
            </div>
            <div className="flex gap-2">
              <Bar className="h-7 flex-1 rounded-lg" />
              <Bar className="h-7 w-7 rounded-lg" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function SmtpAccountsSkeleton() {
  return (
    <div className="p-4 sm:p-6 space-y-6">
      <div className="flex justify-between items-center">
        <Bar className="h-4 w-60" />
        <Bar className="h-8 w-36 rounded-lg" />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="bg-white rounded-xl shadow p-5 space-y-3">
            <div className="flex justify-between">
              <div className="space-y-2">
                <Bar className="h-4 w-32" />
                <Bar className="h-3 w-40" />
              </div>
              <Bar className="h-5 w-14 rounded-full" />
            </div>
            <div className="space-y-2">
              <Bar className="h-3 w-24" />
              <Bar className="h-3 w-32" />
            </div>
            <div className="flex gap-2 pt-2 border-t border-gray-100">
              <Bar className="h-7 w-16 rounded-lg" />
              <Bar className="h-7 w-16 rounded-lg" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function QueueSkeleton() {
  return (
    <div className="p-4 sm:p-6 space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-6">
        {Array.from({ length: 4 }).map((_, i) => <StatCardSkeleton key={i} />)}
      </div>
      <div className="flex flex-wrap gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Bar key={i} className="h-8 w-28 rounded-lg" />
        ))}
      </div>
      <div className="bg-white rounded-xl shadow p-5 space-y-3">
        <Bar className="h-5 w-32 mb-2" />
        {Array.from({ length: 5 }).map((_, i) => <RowSkeleton key={i} />)}
      </div>
    </div>
  );
}

export function RepliesSkeleton() {
  return (
    <div className="p-3 sm:p-4 md:p-6 space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 sm:gap-3">
        {Array.from({ length: 4 }).map((_, i) => <StatCardSkeleton key={i} />)}
      </div>
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="bg-white rounded-xl shadow p-4 space-y-3">
            <div className="flex justify-between items-center">
              <div className="space-y-2">
                <Bar className="h-4 w-40" />
                <Bar className="h-3 w-28" />
              </div>
              <div className="flex gap-2">
                <Bar className="h-6 w-14 rounded-full" />
                <Bar className="h-6 w-14 rounded-full" />
              </div>
            </div>
            <Bar className="h-2 w-full rounded-full" />
            <div className="flex gap-2">
              <Bar className="h-7 w-24 rounded-lg" />
              <Bar className="h-7 w-28 rounded-lg" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function SettingsSkeleton() {
  return (
    <div className="p-4 sm:p-6 space-y-6">
      {/* Activation Codes section */}
      <div className="bg-white/60 backdrop-blur rounded-2xl border border-gray-200/60 shadow-sm p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className={`${shimmer} h-9 w-9 rounded-xl`} />
          <div className="space-y-2">
            <Bar className="h-5 w-36" />
            <Bar className="h-3 w-56" />
          </div>
        </div>
        <div className="p-4 bg-white/50 rounded-xl border border-gray-200/60 space-y-3">
          <Bar className="h-4 w-32" />
          <div className="flex gap-3">
            <Bar className="h-9 w-24 rounded-xl" />
            <Bar className="h-9 flex-1 rounded-xl" />
            <Bar className="h-9 w-28 rounded-xl" />
          </div>
        </div>
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="p-3 bg-white/50 rounded-xl border border-gray-200/60 flex justify-between">
            <div className="space-y-2">
              <Bar className="h-4 w-44" />
              <Bar className="h-3 w-32" />
            </div>
            <Bar className="h-5 w-14 rounded-full" />
          </div>
        ))}
      </div>
      {/* User Management section */}
      <div className="bg-white/60 backdrop-blur rounded-2xl border border-gray-200/60 shadow-sm p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className={`${shimmer} h-9 w-9 rounded-xl`} />
          <div className="space-y-2">
            <Bar className="h-5 w-40" />
            <Bar className="h-3 w-48" />
          </div>
        </div>
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="p-3 bg-white/50 rounded-xl border border-gray-200/60 flex justify-between items-center">
            <div className="space-y-2">
              <Bar className="h-4 w-36" />
              <Bar className="h-3 w-24" />
            </div>
            <Bar className="h-7 w-16 rounded-lg" />
          </div>
        ))}
      </div>
    </div>
  );
}
