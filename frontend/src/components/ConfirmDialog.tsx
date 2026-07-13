import React from 'react';
import { AlertTriangle, HelpCircle } from 'lucide-react';
import { Dialog } from './Dialog';

export function ConfirmDialog({
  title,
  description,
  confirmLabel,
  busyLabel,
  busy,
  tone = 'default',
  onCancel,
  onConfirm,
}: {
  title: string;
  description: React.ReactNode;
  confirmLabel: string;
  busyLabel?: string;
  busy?: boolean;
  tone?: 'default' | 'danger';
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      title={title}
      icon={tone === 'danger' ? <AlertTriangle size={17} /> : <HelpCircle size={17} />}
      tone={tone}
      maxWidth={440}
      onClose={onCancel}
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
      footer={
        <>
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className={tone === 'danger' ? 'danger-button' : 'primary'} onClick={onConfirm} disabled={busy}>
            {busy ? (busyLabel || `${confirmLabel}…`) : confirmLabel}
          </button>
        </>
      }
    >
      <p>{description}</p>
    </Dialog>
  );
}
