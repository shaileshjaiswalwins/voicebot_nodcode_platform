import { useCallback, useRef, useState } from 'react';

export type ToastTone = 'success' | 'error' | 'info';

export type Toast = {
  id: string;
  message: string;
  tone: ToastTone;
};

const DEFAULT_DURATION_MS = 4000;

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counterRef = useRef(0);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback((message: string, tone: ToastTone = 'success', durationMs = DEFAULT_DURATION_MS) => {
    counterRef.current += 1;
    const id = `toast-${counterRef.current}`;
    setToasts((prev) => [...prev, { id, message, tone }]);
    if (durationMs > 0) {
      setTimeout(() => dismissToast(id), durationMs);
    }
    return id;
  }, [dismissToast]);

  return { toasts, showToast, dismissToast };
}
