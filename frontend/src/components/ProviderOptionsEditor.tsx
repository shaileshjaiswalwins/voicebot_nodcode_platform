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

  // A number is "out of range" when it violates the spec's min/max (browsers don't hard-block
  // typed input against min/max, so we surface a soft warning instead of rejecting the value).
  function rangeWarning(f: ParamField): string | null {
    if (f.kind !== 'number') return null;
    const v = value[f.key];
    if (typeof v !== 'number' || Number.isNaN(v)) return null;
    if (f.min !== undefined && v < f.min) return `Below recommended minimum (${f.min}).`;
    if (f.max !== undefined && v > f.max) return `Above recommended maximum (${f.max}).`;
    return null;
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
            {g.items.map((f) => {
              const warn = rangeWarning(f);
              return (
              <label key={f.key} title={f.hint}>
                {f.label}
                {f.kind === 'bool' ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginTop: '0.3rem' }}>
                    <input
                      type="checkbox"
                      aria-label={f.label}
                      title={f.hint}
                      checked={Boolean(value[f.key])}
                      onChange={(e) => setKey(f.key, e.target.checked ? true : '')}
                      style={{ width: 'auto' }}
                    />
                    <span style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>{f.hint}</span>
                  </div>
                ) : f.kind === 'select' ? (
                  <select aria-label={f.label} title={f.hint} value={String(value[f.key] ?? '')} onChange={(e) => setKey(f.key, e.target.value)}>
                    <option value="">{f.placeholder ? `${f.placeholder} (default)` : 'Default'}</option>
                    {(f.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : f.kind === 'number' ? (
                  <input
                    type="number"
                    aria-label={f.label}
                    title={f.hint}
                    aria-invalid={warn ? true : undefined}
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
                    title={f.hint}
                    value={String(value[f.key] ?? '')}
                    placeholder={f.placeholder}
                    onChange={(e) => setKey(f.key, e.target.value)}
                  />
                )}
                {warn && <small role="alert" style={{ color: 'var(--warning, #b45309)' }}>{warn}</small>}
                {f.kind !== 'bool' && f.hint && !warn && <small>{f.hint}</small>}
              </label>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
