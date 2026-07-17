import React from 'react';
import type { ParamField } from '../constants/providerParams';

/** Renders a spec-driven grid of optional provider parameters (Sarvam STT/TTS, Gemini).
 * Only parameters the user actually sets are kept in the options object; clearing a field
 * removes the key so the pipeline falls back to its default. */
export function ProviderOptionsEditor({
  fields,
  value,
  onChange,
}: {
  fields: ParamField[];
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  function setKey(key: string, raw: unknown) {
    const next = { ...value };
    // Empty / unchecked-to-default removes the key so it falls back to the pipeline default.
    if (raw === '' || raw === undefined || raw === null) {
      delete next[key];
    } else {
      next[key] = raw;
    }
    onChange(next);
  }

  // Preserve declared order but group by the optional `group` label.
  const groups: { group: string | undefined; items: ParamField[] }[] = [];
  for (const f of fields) {
    const last = groups[groups.length - 1];
    if (last && last.group === f.group) last.items.push(f);
    else groups.push({ group: f.group, items: [f] });
  }

  return (
    <div className="provider-options">
      {groups.map((g, gi) => (
        <div key={gi} style={{ marginTop: g.group ? '0.75rem' : 0 }}>
          {g.group && (
            <div style={{ fontSize: '0.72rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.5rem 0' }}>
              {g.group}
            </div>
          )}
          <div className="form-grid">
            {g.items.map((f) => (
              <label key={f.key}>
                {f.label}
                {f.kind === 'bool' ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginTop: '0.3rem' }}>
                    <input
                      type="checkbox"
                      aria-label={f.label}
                      checked={Boolean(value[f.key])}
                      onChange={(e) => setKey(f.key, e.target.checked ? true : '')}
                      style={{ width: 'auto' }}
                    />
                    <span style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>{f.hint}</span>
                  </div>
                ) : f.kind === 'select' ? (
                  <select aria-label={f.label} value={String(value[f.key] ?? '')} onChange={(e) => setKey(f.key, e.target.value)}>
                    <option value="">{f.placeholder ? `${f.placeholder} (default)` : 'Default'}</option>
                    {(f.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : f.kind === 'number' ? (
                  <input
                    type="number"
                    aria-label={f.label}
                    value={value[f.key] === undefined ? '' : String(value[f.key])}
                    placeholder={f.placeholder}
                    min={f.min}
                    max={f.max}
                    step={f.step}
                    onChange={(e) => setKey(f.key, e.target.value === '' ? '' : Number(e.target.value))}
                  />
                ) : (
                  <input
                    type="text"
                    aria-label={f.label}
                    value={String(value[f.key] ?? '')}
                    placeholder={f.placeholder}
                    onChange={(e) => setKey(f.key, e.target.value)}
                  />
                )}
                {f.kind !== 'bool' && f.hint && <small>{f.hint}</small>}
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
