import React, { useEffect, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';

type Row = { k: string; v: string };

function toRows(value: Record<string, string>): Row[] {
  return Object.entries(value).map(([k, v]) => ({ k, v }));
}
function toRecord(rows: Row[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const { k, v } of rows) if (k.trim()) out[k] = v;
  return out;
}

/** Editable list of key/value string pairs (headers, query params, etc.), rendered as a
 * titled section: a top-right "Add" button, and a bordered box below it that shows either
 * the rows or a dashed-border empty state ("No headers configured. Click 'Add Header' to add
 * custom headers.") — mirrors the inspiration platform's Headers/Query Parameters layout.
 * Keeps local row state so blank/half-typed rows survive editing; the parent only ever
 * receives a Record with non-empty keys. */
export function KeyValueEditor({
  label,
  hint,
  value,
  onChange,
  keyPlaceholder = 'key',
  valuePlaceholder = 'value',
  addLabel = 'Add',
  emptyLabel = 'No entries configured',
}: {
  label?: string;
  hint?: string;
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
  addLabel?: string;
  emptyLabel?: string;
}) {
  const [rows, setRows] = useState<Row[]>(() => toRows(value));

  // Re-sync from props only when the external record differs from what our rows represent
  // (e.g. switching to a different function). Avoids clobbering in-progress blank rows.
  useEffect(() => {
    const current = JSON.stringify(toRecord(rows));
    if (JSON.stringify(value) !== current) setRows(toRows(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  function apply(next: Row[]) {
    setRows(next);
    onChange(toRecord(next));
  }

  return (
    <div className="kv-editor" style={{ marginBottom: '0.9rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.4rem' }}>
        <div>
          {label && <div style={{ fontSize: '0.85rem', fontWeight: 600 }}>{label}</div>}
          {hint && <div style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>{hint}</div>}
        </div>
        <button type="button" onClick={() => apply([...rows, { k: '', v: '' }])} style={{ fontSize: '0.78rem', flexShrink: 0 }}>
          <Plus size={13} /> {addLabel}
        </button>
      </div>
      {rows.length === 0 ? (
        <div style={{
          border: '1px dashed var(--border)', borderRadius: '6px', padding: '1.1rem', textAlign: 'center',
        }}>
          <div style={{ fontSize: '0.82rem', color: 'var(--muted)' }}>{emptyLabel}</div>
          <div style={{ fontSize: '0.75rem', color: 'var(--muted)', opacity: 0.8 }}>Click "{addLabel}" above to add one.</div>
        </div>
      ) : (
        <div style={{ border: '1px solid var(--border)', borderRadius: '6px', padding: '0.6rem' }}>
          {rows.map((row, i) => (
            <div key={i} style={{ display: 'flex', gap: '0.4rem', marginBottom: i === rows.length - 1 ? 0 : '0.4rem' }}>
              <input
                aria-label={`${label || 'pair'} key ${i + 1}`}
                style={{ flex: 1, minWidth: '6rem' }}
                placeholder={keyPlaceholder}
                value={row.k}
                onChange={(e) => apply(rows.map((r, idx) => (idx === i ? { ...r, k: e.target.value } : r)))}
              />
              <input
                aria-label={`${label || 'pair'} value ${i + 1}`}
                style={{ flex: 1, minWidth: '6rem' }}
                placeholder={valuePlaceholder}
                value={row.v}
                onChange={(e) => apply(rows.map((r, idx) => (idx === i ? { ...r, v: e.target.value } : r)))}
              />
              <button type="button" title="Remove" onClick={() => apply(rows.filter((_, idx) => idx !== i))} style={{ padding: '0 0.5rem', flexShrink: 0 }}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
