import { CheckCircle, XCircle, Info, X } from 'lucide-react';
import { useEffect, useState, useCallback } from 'react';
import type { Toast } from '../../typefiles';
import { subscribeToToasts } from './toast-store';

export type { ToastType } from '../../typefiles';

const config = {
  success: {
    icon: CheckCircle,
    accent: '#2f6f75',
    bg: 'rgba(47, 111, 117, 0.1)',
    border: 'rgba(47, 111, 117, 0.28)',
    label: 'Success',
  },
  error: {
    icon: XCircle,
    accent: '#745067',
    bg: 'rgba(116, 80, 103, 0.1)',
    border: 'rgba(116, 80, 103, 0.28)',
    label: 'Error',
  },
  info: {
    icon: Info,
    accent: '#2f6f9f',
    bg: 'rgba(47, 111, 159, 0.1)',
    border: 'rgba(47, 111, 159, 0.28)',
    label: 'Info',
  },
};

function ToastItem({ toast, onRemove }: { toast: Toast; onRemove: (id: number) => void }) {
  const { icon: Icon, accent, border } = config[toast.type];
  const panelBg = `linear-gradient(145deg, rgba(8, 12, 17, 0.985), rgba(6, 10, 14, 0.985))`;
  const panelShadow = `0 14px 30px rgba(0,0,0,0.56), 0 0 0 1px rgba(83,128,165,0.22) inset`;
  const messageClass = 'text-[12px] leading-snug text-[#e7f2ff] font-medium';
  const closeClass = 'shrink-0 mt-0.5 p-1 rounded-md text-[#88a2b8] hover:text-[#eef7ff] hover:bg-white/10 transition-all duration-150';

  return (
    <div
      className={toast.exiting ? 'animate-slide-out' : 'animate-slide-in'}
      style={{ marginBottom: toast.exiting ? 0 : undefined }}
    >
      <div
        className="relative overflow-hidden rounded-md"
        style={{
          background: panelBg,
          border: `1px solid ${border}`,
          boxShadow: panelShadow,
          minWidth: 300,
          maxWidth: 420,
        }}
      >
        <div className="flex items-start gap-2.5 px-3 py-2.5">
          {/* Icon */}
          <div
            className="toast-icon-pop shrink-0 mt-0.5 rounded-md p-1.5"
            style={{ background: `${accent}26`, border: `1px solid ${border}` }}
          >
            <Icon
              className="w-4 h-4"
              style={{ color: accent }}
              strokeWidth={2.5}
            />
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0 pr-1">
            <p
              className="text-[10px] font-semibold uppercase tracking-[0.09em] mb-0.5 font-mono"
              style={{ color: accent }}
            >
              {config[toast.type].label}
            </p>
            <p className={messageClass}>
              {toast.message}
            </p>
          </div>

          {/* Close */}
          <button
            onClick={() => onRemove(toast.id)}
            className={closeClass}
          >
            <X size={14} strokeWidth={2.5} />
          </button>
        </div>

        {/* Progress bar */}
        <div className="h-0.5 w-full" style={{ background: `${accent}30` }}>
          <div
            className="h-full toast-progress rounded-full"
            style={{
              background: `linear-gradient(90deg, ${accent}, ${accent}80)`,
            }}
          />
        </div>
      </div>
    </div>
  );
}

export function ToastContainer() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: number) => {
    setToasts(prev => prev.map(t => t.id === id ? { ...t, exiting: true } : t));
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 300);
  }, []);

  useEffect(() => {
    const listener = (toast: Toast) => {
      setToasts(prev => [...prev, toast]);
      setTimeout(() => {
        removeToast(toast.id);
      }, 4000);
    };
    return subscribeToToasts(listener);
  }, [removeToast]);

  return (
    <div className="fixed top-4 right-4 z-9999 flex flex-col gap-2">
      {toasts.map(toast => (
        <ToastItem key={toast.id} toast={toast} onRemove={removeToast} />
      ))}
    </div>
  );
}
