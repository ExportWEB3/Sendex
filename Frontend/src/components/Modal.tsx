import { X, AlertTriangle, Trash2, Info } from 'lucide-react';
import { useEffect, useState, useCallback, useRef } from 'react';
import type { ConfirmOptions, ConfirmProviderProps, ConfirmVariant, ModalProps } from '../../typefiles';
import { ConfirmContext } from './useConfirm';

export function Modal({ isOpen, onClose, title, children, zIndex, size = 'default' }: ModalProps) {
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      document.addEventListener('keydown', handleEsc);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEsc);
      document.body.style.overflow = 'unset';
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 flex items-center justify-center p-4" style={{ zIndex: zIndex || 50 }}>
      <div className="fixed inset-0 bg-black/50" onClick={onClose} />
      <div
        className="relative bg-white rounded-xl shadow-xl w-full max-h-[90vh] overflow-y-auto will-change-[max-width]"
        style={{
          maxWidth: size === 'xl' ? '80rem' : size === 'wide' ? '64rem' : '32rem',
          transition: 'max-width 0.35s cubic-bezier(0.22, 1, 0.36, 1)',
        }}
      >
        <div className="flex items-center justify-between p-4 border-b">
          <h3 className="text-lg font-semibold">{title}</h3>
          <button
            onClick={onClose}
            className="p-1 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

// ============================================================================
// Confirm Modal - Reusable confirmation dialog
// ============================================================================

export function ConfirmProvider({ children }: ConfirmProviderProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const resolveRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback((opts: ConfirmOptions): Promise<boolean> => {
    setOptions(opts);
    setIsOpen(true);
    
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
    });
  }, []);

  const handleClose = () => {
    setIsOpen(false);
    resolveRef.current?.(false);
    resolveRef.current = null;
  };

  const handleConfirm = () => {
    setIsOpen(false);
    resolveRef.current?.(true);
    resolveRef.current = null;
  };

  const getVariantStyles = (variant: ConfirmVariant = 'danger') => {
    switch (variant) {
      case 'danger':
        return {
          icon: <Trash2 className="w-6 h-6 text-red-600" />,
          iconBg: 'bg-red-100',
          buttonBg: 'bg-red-600 hover:bg-red-700',
        };
      case 'warning':
        return {
          icon: <AlertTriangle className="w-6 h-6 text-yellow-600" />,
          iconBg: 'bg-yellow-100',
          buttonBg: 'bg-yellow-600 hover:bg-yellow-700',
        };
      case 'info':
        return {
          icon: <Info className="w-6 h-6 text-blue-600" />,
          iconBg: 'bg-blue-100',
          buttonBg: 'bg-blue-600 hover:bg-blue-700',
        };
    }
  };

  const styles = options ? getVariantStyles(options.variant) : getVariantStyles('danger');

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      
      {isOpen && options && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <div className="fixed inset-0 bg-black/50" onClick={handleClose} />
          <div className="relative bg-white rounded-xl shadow-xl max-w-md w-full">
            <div className="p-6">
              <div className="flex items-start gap-4">
                <div className={`p-3 rounded-full ${styles.iconBg}`}>
                  {styles.icon}
                </div>
                <div className="flex-1">
                  <h3 className="text-lg font-semibold text-gray-900">{options.title}</h3>
                  <p className="mt-2 text-sm text-gray-600 whitespace-pre-wrap">{options.message}</p>
                </div>
              </div>
              
              <div className="mt-6 flex justify-end gap-3">
                <button
                  onClick={handleClose}
                  className="px-3 py-1.5 text-sm text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
                >
                  {options.cancelText || 'Cancel'}
                </button>
                <button
                  onClick={handleConfirm}
                  className={`px-3 py-1.5 text-sm text-white rounded-lg transition-colors ${styles.buttonBg}`}
                >
                  {options.confirmText || 'Confirm'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function ConfirmModal() {
  // This is a placeholder - the actual modal is rendered by ConfirmProvider
  return null;
}
