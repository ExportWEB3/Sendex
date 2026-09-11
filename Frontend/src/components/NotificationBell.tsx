import { useState, useEffect, useRef } from 'react';
import { Bell, ChevronDown, Clock, Info, AlertTriangle } from 'lucide-react';
import { useKillSwitchStatus } from '../hooks/useKillSwitchStatus';
import type { BellNotification } from '../../typefiles';

function timeAgo(date: Date): string {
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function NotificationBell() {
  const {
    enabled: killSwitchEnabled,
    message: killSwitchMessage,
  } = useKillSwitchStatus();
  const [notifications, setNotifications] = useState<BellNotification[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [seenCount, setSeenCount] = useState(0);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [, setTick] = useState(0);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  const addNotification = (title: string, description: string) => {
    setNotifications(prev => [{
      id: nextId.current++,
      timestamp: new Date(),
      title,
      description,
      type: 'info' as const,
    }, ...prev].slice(0, 50));
  };

  // Expose addNotification globally
  useEffect(() => {
    window.__addNotification = addNotification;
    return () => { delete window.__addNotification; };
  }, []);

  // Tick every 10s to update relative timestamps
  useEffect(() => {
    const timer = setInterval(() => setTick(t => t + 1), 10000);
    return () => clearInterval(timer);
  }, []);

  // Close on outside click
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const toggleOpen = () => {
    const nextOpen = !isOpen;
    if (nextOpen) setSeenCount(notifications.length);
    setIsOpen(nextOpen);
  };

  const unseenCount = notifications.length - seenCount + (killSwitchEnabled ? 1 : 0);

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Bell Button */}
      <button
        onClick={toggleOpen}
        className="relative p-1.5 text-[#8ea5b9] hover:text-[#ebf6ff] hover:bg-[#101923] border border-transparent hover:border-[#2f4558] rounded-md transition-colors"
      >
        <Bell size={18} className={unseenCount > 0 || killSwitchEnabled ? 'text-red-500' : ''} />
        {(unseenCount > 0 || killSwitchEnabled) && (
          <span className="absolute -top-0.5 -right-0.5 min-w-4.5 h-4.5 flex items-center justify-center px-1 text-[10px] font-bold text-white bg-red-500 rounded-md shadow-sm">
            {unseenCount > 99 ? '99+' : unseenCount}
          </span>
        )}
      </button>

      {/* Dropdown */}
      {isOpen && (
        <div className="absolute right-0 top-full mt-2 w-95 max-h-120 bg-[#0a1016] rounded-lg border border-[#2a3f52] shadow-xl shadow-black/60 z-50 overflow-hidden flex flex-col">
          {/* Header */}
          <div className="px-3 py-2.5 border-b border-[#233646] bg-[#0d151e] flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Bell size={16} className="text-[#76c7e5]" />
              <span className="text-xs font-semibold uppercase tracking-[0.08em] text-[#e6f3ff]">Notifications</span>
            </div>
            <span className="text-xs text-[#7e97ac]">
              {notifications.length + (killSwitchEnabled ? 1 : 0)} total
            </span>
          </div>

          {/* Notifications List */}
          <div className="flex-1 overflow-y-auto divide-y divide-[#1f2f3d]">
            {/* Kill switch persistent alert — always on top, never clears */}
            {killSwitchEnabled && (
              <div className="px-4 py-3 bg-[#241318] border-b border-[#5b2a35]">
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 p-1.5 rounded-lg bg-red-500/15 border border-red-500/25">
                    <AlertTriangle size={14} className="text-red-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-[10px] font-semibold uppercase tracking-wider text-red-300 mb-0.5">System Alert</p>
                    <p className="text-sm font-medium text-red-100 leading-snug">{killSwitchMessage}</p>
                    <p className="text-[11px] text-red-300/80 mt-1">All sending is disabled until this is resolved</p>
                  </div>
                </div>
              </div>
            )}

            {notifications.length === 0 && !killSwitchEnabled ? (
              <div className="p-6 text-center text-sm text-[#7d93a7]">No notifications yet</div>
            ) : (
              notifications.map(n => {
                const isExpanded = expandedId === n.id;
                return (
                  <div
                    key={n.id}
                    className={`px-3 py-3 cursor-pointer transition-colors hover:bg-[#111a23] ${isExpanded ? 'bg-[#111a23]' : ''}`}
                    onClick={() => setExpandedId(isExpanded ? null : n.id)}
                  >
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 p-1.5 rounded-lg bg-[#214661]/45 border border-[#3d6886]">
                        <Info size={14} className="text-[#86cdea]" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className="text-[10px] text-[#7d93a7] flex items-center gap-0.5">
                            <Clock size={9} /> {timeAgo(n.timestamp)}
                          </span>
                        </div>
                        <p className="text-sm font-medium text-[#e7f3ff] leading-snug">{n.title}</p>
                        
                        {/* Expandable details */}
                        <div className={`overflow-hidden transition-all duration-300 ${isExpanded ? 'max-h-60 mt-2' : 'max-h-0'}`}>
                          <p className="text-xs text-[#a9bfd3] leading-relaxed">{n.description}</p>
                        </div>
                      </div>
                      <ChevronDown
                        size={14}
                        className={`text-[#5f768a] mt-1 transition-transform duration-200 shrink-0 ${isExpanded ? 'rotate-180' : ''}`}
                      />
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
