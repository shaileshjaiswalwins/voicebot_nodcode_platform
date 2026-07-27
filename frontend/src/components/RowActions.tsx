import React, { useState } from 'react';
import { Pencil, Trash2 } from 'lucide-react';
import { ConfirmDialog } from './ConfirmDialog';

export function RowActions<T>({
  item,
  onEdit,
  editTitle = 'Edit',
  onDelete,
  deleteTitle,
  deleteDescription,
  deleteConfirmLabel = 'Delete',
}: {
  item: T;
  onEdit?: (item: T) => void;
  editTitle?: string;
  onDelete?: (item: T) => Promise<void> | void;
  deleteTitle: string;
  deleteDescription: (item: T) => React.ReactNode;
  deleteConfirmLabel?: string;
}) {
  const [pendingDelete, setPendingDelete] = useState<T | null>(null);
  const [busy, setBusy] = useState(false);

  async function confirmDelete() {
    if (!pendingDelete || !onDelete) return;
    setBusy(true);
    try {
      await onDelete(pendingDelete);
      setPendingDelete(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="button-row" style={{ display: 'flex', gap: '6px' }}>
      {onEdit && (
        <button title={editTitle} onClick={() => onEdit(item)}>
          <Pencil size={13} />
        </button>
      )}
      {onDelete && (
        <button className="danger-button" title={deleteTitle} onClick={() => setPendingDelete(item)}>
          <Trash2 size={13} />
        </button>
      )}
      {pendingDelete && (
        <ConfirmDialog
          title={deleteTitle}
          description={deleteDescription(pendingDelete)}
          confirmLabel={deleteConfirmLabel}
          busyLabel={`${deleteConfirmLabel}…`}
          busy={busy}
          tone="danger"
          onCancel={() => setPendingDelete(null)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  );
}
