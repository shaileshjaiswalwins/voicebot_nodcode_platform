import React, { useEffect, useId } from 'react';
import { X } from 'lucide-react';

export function Dialog({
  title,
  icon,
  children,
  footer,
  tone = 'default',
  maxWidth = 480,
  closeOnBackdrop = true,
  closeOnEscape = true,
  onClose,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  tone?: 'default' | 'danger';
  maxWidth?: number;
  closeOnBackdrop?: boolean;
  closeOnEscape?: boolean;
  onClose: () => void;
}) {
  const titleId = useId();

  useEffect(() => {
    if (!closeOnEscape) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [closeOnEscape, onClose]);

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={closeOnBackdrop ? onClose : undefined}
    >
      <section
        className={`modal-panel${tone === 'danger' ? ' critical' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={{ maxWidth }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          {icon && <div className="modal-icon">{icon}</div>}
          <h2 id={titleId}>{title}</h2>
          {/* Explicit close control — matters most when closeOnBackdrop is false (see
              CreateAgentPicker), where an accidental outside click no longer dismisses the
              dialog and this becomes the only click-based way out besides Escape. */}
          <button type="button" className="modal-close" aria-label="Close dialog" onClick={onClose}>
            <X size={16} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </section>
    </div>
  );
}
