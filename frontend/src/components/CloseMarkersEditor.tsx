import React, { useState } from 'react';
import { Plus } from 'lucide-react';

/** Chip-style editor for the call-end close phrases. Extracted from BuilderView so it can
 * be shared by the tabbed settings (BotConfigTabs) without a circular import. */
export function CloseMarkersEditor({ markers, onChange }: { markers: string[]; onChange: (value: string[]) => void }) {
  const [input, setInput] = useState('');
  function add() {
    const trimmed = input.trim();
    if (trimmed && !markers.includes(trimmed)) onChange([...markers, trimmed]);
    setInput('');
  }
  return (
    <label className="full">
      Call-end close phrases
      <small>Bot ends the call when it detects any of these phrases. Override the hardcoded Hindi defaults for non-Hindi bots.</small>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.4rem', marginBottom: '0.4rem', minHeight: '2rem' }}>
        {markers.map(m => (
          <span key={m} style={{ background: 'var(--surface-2)', borderRadius: '4px', padding: '2px 8px', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
            {m}
            <button style={{ padding: 0, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)', lineHeight: 1 }} onClick={() => onChange(markers.filter(x => x !== m))}>×</button>
          </span>
        ))}
        {markers.length === 0 && <span style={{ fontSize: '0.78rem', color: 'var(--muted)', fontStyle: 'italic' }}>Using hardcoded defaults (Hindi)</span>}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="e.g. thank you, goodbye, dhanyavaad"
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          style={{ flex: 1 }}
        />
        <button onClick={add} style={{ whiteSpace: 'nowrap' }}><Plus size={14} /> Add</button>
      </div>
    </label>
  );
}
