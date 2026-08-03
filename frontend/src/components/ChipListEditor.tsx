import React, { useState } from 'react';
import { Plus } from 'lucide-react';

/** Generic chip-list editor: type a value, press Enter (or click Add) to commit it as a
 * chip, click × to remove one. Extracted from CloseMarkersEditor so any comma-list-shaped
 * field (tags, close markers, etc.) gets the same free-typing input instead of a
 * live split(',')/join(', ') on every keystroke — that pattern silently eats a trailing
 * comma/space as soon as it's typed (the controlled input's value is recomputed from the
 * already-filtered array before the user can type the next chip's text), which reads as a
 * completely broken input field. */
export function ChipListEditor({
  items,
  onChange,
  label,
  placeholder,
  helpText,
  emptyText,
}: {
  items: string[];
  onChange: (value: string[]) => void;
  label?: string;
  placeholder?: string;
  helpText?: string;
  emptyText?: string;
}) {
  const [input, setInput] = useState('');
  function add() {
    const trimmed = input.trim();
    if (trimmed && !items.includes(trimmed)) onChange([...items, trimmed]);
    setInput('');
  }
  return (
    <label className="full">
      {label}
      {helpText && <small>{helpText}</small>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.4rem', marginBottom: '0.4rem', minHeight: '2rem' }}>
        {items.map((item) => (
          <span key={item} style={{ background: 'var(--surface-2)', borderRadius: '4px', padding: '2px 8px', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
            {item}
            <button style={{ padding: 0, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)', lineHeight: 1 }} onClick={() => onChange(items.filter((x) => x !== item))}>×</button>
          </span>
        ))}
        {items.length === 0 && emptyText && <span style={{ fontSize: '0.78rem', color: 'var(--muted)', fontStyle: 'italic' }}>{emptyText}</span>}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={placeholder}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          style={{ flex: 1 }}
        />
        <button onClick={add} style={{ whiteSpace: 'nowrap' }}><Plus size={14} /> Add</button>
      </div>
    </label>
  );
}
