import { RefreshCw } from 'lucide-react';
import { NotificationBell } from './NotificationBell';
import type { HeaderProps } from '../../typefiles';

export function Header({ title, onRefresh, lastUpdated }: HeaderProps) {
  return (
    <header className="bg-white/90 backdrop-blur-sm shadow-sm border-b border-gray-200 px-3 sm:px-4 py-2.5 flex justify-between items-center sticky top-0 z-30 md:z-30">
      <h2 className="text-lg font-semibold text-gray-800 ml-12 lg:ml-0 tracking-[0.04em]">{title}</h2>
      <div className="flex items-center gap-3">
        {lastUpdated && (
          <span className="text-xs text-gray-500 hidden sm:inline font-mono tracking-wide">
            Updated: {lastUpdated.toLocaleTimeString()}
          </span>
        )}
        {onRefresh && (
          <button
            onClick={onRefresh}
            className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 border border-transparent hover:border-gray-300 rounded-md transition-colors"
          >
            <RefreshCw size={16} />
          </button>
        )}
        <NotificationBell />
      </div>
    </header>
  );
}
