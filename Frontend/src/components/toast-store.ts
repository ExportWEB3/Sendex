import type { Toast, ToastType } from '../../typefiles';

let toastId = 0;
const listeners = new Set<(toast: Toast) => void>();

export function showToast(message: string, type: ToastType = 'info') {
  const toast = { id: ++toastId, message, type };
  listeners.forEach((listener) => listener(toast));
}

export function subscribeToToasts(listener: (toast: Toast) => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}