import type { StatCardProps } from '../../typefiles';

export function StatCard({ title, value, subtitle, icon, iconBg, subtitleColor = 'text-gray-500' }: StatCardProps) {
  return (
    <div className="bg-white rounded-lg shadow p-4 hover:shadow-md transition-all border border-gray-200">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-gray-500 text-[11px] font-semibold uppercase tracking-widest font-mono">{title}</p>
          <p className="text-[1.7rem] leading-tight font-semibold text-gray-800 mt-1 tabular-nums">{value}</p>
        </div>
        <div className={`w-9 h-9 ${iconBg} rounded-md border border-gray-200 flex items-center justify-center`}>
          {icon}
        </div>
      </div>
      {subtitle && (
        <p className={`text-[11px] mt-2 ${subtitleColor}`}>{subtitle}</p>
      )}
    </div>
  );
}
