import React, { useState } from 'react';
import { Pencil, Trash2 } from 'lucide-react';
import { ConfirmDialog } from './ConfirmDialog';
import { RowActionsMenu } from './RowActionsMenu';

/** Generic per-row Edit/Delete for a table, used across Campaigns/Library/PhoneNumbers.
 * Renders through the same "⋮" RowActionsMenu as the Agents table (rather than always-visible
 * button pairs) — callers keep the same props (onEdit/onDelete/delete confirmation), only the
 * presentation changed. Delete still goes through ConfirmDialog before onDelete fires. */
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

  const actions = [
    ...(onEdit ? [{ label: editTitle, icon: <Pencil size={13} />, onClick: () => onEdit(item) }] : []),
    ...(onDelete ? [{ label: deleteTitle.replace(/\?$/, ''), icon: <Trash2 size={13} />, onClick: () => setPendingDelete(item), danger: true }] : []),
  ];

  return (
    <>
      <RowActionsMenu actions={actions} />
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
    </>
  );
}
