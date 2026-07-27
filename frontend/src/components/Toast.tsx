import React from 'react';
import { CheckCircle2, Info, X, XCircle } from 'lucide-react';
import type { Toast, ToastTone } from '../hooks/useToasts';

const TONE_ICON: Record<ToastTone, React.ReactNode> = {
  success: <CheckCircle2 size={16} />,
  error: <XCircle size={16} />,
  info: <Info size={16} />,
};

export function ToastContainer({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: string) => void }) {
  if (!toasts.length) return null;
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.tone}`}>
          {TONE_ICON[toast.tone]}
          <span>{toast.message}</span>
          <button className="toast-dismiss" aria-label="Dismiss notification" onClick={() => onDismiss(toast.id)}>
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
