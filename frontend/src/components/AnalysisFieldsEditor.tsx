import React, { useMemo, useState } from 'react';
import { Plus, Trash2, ChevronDown, ChevronRight, AlertCircle } from 'lucide-react';
import type { AnalysisFieldDef, AnalysisFieldType } from '../types';
import { ChipListEditor } from './ChipListEditor';
import { IconButton } from './ui/icon-button';

/** Soft client-side guidance only — the backend is the source of truth for the real cap
 * (see backend/routers/bots.py's field-count validation). Kept generous so this doesn't
 * block a PM before the server has a chance to weigh in with its actual number. */
const FIELD_COUNT_SOFT_CAP = 20;

const TYPE_LABELS: Record<AnalysisFieldType, string> = {
  boolean: 'Boolean',
  text: 'Text',
  number: 'Number',
  enum: 'Enum',
};

function newFieldKey(existing: AnalysisFieldDef[]): string {
  let i = existing.length + 1;
  let key = `field_${i}`;
  while (existing.some((f) => f.key === key)) {
    i += 1;
    key = `field_${i}`;
  }
  return key;
}

function slugifyKey(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '') || 'field';
}

/** Per-field validation, mirrored client-side from the backend rules (unique keys within
 * one bot version, enum_options required + non-empty when type === 'enum'). The backend
 * remains the source of truth (backend/routers/bots.py) — this is inline UX only, shown as
 * the PM types rather than only surfaced after a failed save. */
function fieldErrors(field: AnalysisFieldDef, allFields: AnalysisFieldDef[]): string[] {
  const errors: string[] = [];
  const key = field.key.trim();
  if (!key) errors.push('Field key is required.');
  else if (allFields.filter((f) => f.key.trim() === key).length > 1) errors.push('Field key must be unique.');
  if (!field.label.trim()) errors.push('Display name is required.');
  if (field.type === 'enum' && (!field.enum_options || field.enum_options.filter((o) => o.trim()).length === 0)) {
    errors.push('Enum fields need at least one allowed option.');
  }
  return errors;
}

/** The "Post-Call Analysis" tab's field editor (Part 2 of the Post-Call Analysis Revamp) —
 * lets a PM declare typed fields (boolean/text/number/enum) the new generic extractor
 * (backend/post_call_analysis.py) should pull out of each call's transcript, closer to
 * Retell's per-agent analysis config screen than anything that existed here before. Follows
 * this file's own CustomFunctionsEditor card-list pattern (expand/collapse, Trash2 remove)
 * rather than introducing a new list-editing shape. */
export function AnalysisFieldsEditor({
  fields,
  onChange,
}: {
  fields: AnalysisFieldDef[];
  onChange: (next: AnalysisFieldDef[]) => void;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const safeFields = fields;

  const allErrorsByKey = useMemo(() => {
    const map: Record<string, string[]> = {};
    safeFields.forEach((f, i) => { map[`${f.key}__${i}`] = fieldErrors(f, safeFields); });
    return map;
  }, [safeFields]);

  function update(index: number, patch: Partial<AnalysisFieldDef>) {
    onChange(safeFields.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  }
  function remove(index: number) {
    onChange(safeFields.filter((_, i) => i !== index));
  }
  function move(index: number, dir: -1 | 1) {
    const target = index + dir;
    if (target < 0 || target >= safeFields.length) return;
    const next = [...safeFields];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }
  function add() {
    const key = newFieldKey(safeFields);
    const field: AnalysisFieldDef = { key, label: '', type: 'boolean', description: '' };
    onChange([...safeFields, field]);
    setOpenKey(`${key}__${safeFields.length}`);
  }

  const atCap = safeFields.length >= FIELD_COUNT_SOFT_CAP;

  return (
    <div className="analysis-fields-editor">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontWeight: 600 }}>Post-Call Analysis Fields</div>
          <div style={{ fontSize: 'var(--font-size-md)', color: 'var(--muted)' }}>
            Define structured data to extract from every call's transcript for this bot — each field's
            description is fed to the extractor prompt, so be specific about what it means.
          </div>
        </div>
        <button type="button" className="primary" onClick={add} disabled={atCap} title={atCap ? `Soft limit of ${FIELD_COUNT_SOFT_CAP} fields reached` : undefined}>
          <Plus size={14} /> Add field
        </button>
      </div>

      {atCap && (
        <div className="notice" style={{ marginTop: '0.6rem' }}>
          <AlertCircle size={14} /> You're at the recommended limit of {FIELD_COUNT_SOFT_CAP} fields — the
          server enforces the actual cap on save.
        </div>
      )}

      {safeFields.length === 0 && (
        <div style={{ fontSize: 'var(--font-size-lg)', color: 'var(--muted)', fontStyle: 'italic', margin: '1rem 0' }}>
          No analysis fields yet — this bot falls back to the legacy lead-qualification classifier
          for its post-call analysis until you add at least one field here.
        </div>
      )}

      <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {safeFields.map((field, index) => {
          const errKey = `${field.key}__${index}`;
          const isOpen = openKey === errKey;
          const errors = allErrorsByKey[errKey] || [];
          return (
            <div key={errKey} className="cf-card" style={{ border: '1px solid var(--border)', borderRadius: '8px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.6rem 0.75rem' }}>
                <button
                  type="button"
                  aria-label={isOpen ? 'Collapse' : 'Expand'}
                  onClick={() => setOpenKey(isOpen ? null : errKey)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                >
                  {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </button>
                <span style={{ fontWeight: 600, flex: 1 }}>
                  {field.label || <em style={{ color: 'var(--muted)' }}>Untitled field</em>}
                  <span style={{ fontWeight: 400, color: 'var(--muted)', marginLeft: '0.4rem', fontSize: 'var(--font-size-sm)' }}>
                    ({field.key || 'no key'})
                  </span>
                </span>
                {errors.length > 0 && (
                  <span title={errors.join(' ')} style={{ display: 'inline-flex', color: '#dc2626' }}>
                    <AlertCircle size={15} />
                  </span>
                )}
                <span className="cf-trigger-badge" style={{ fontSize: 'var(--font-size-xs)', background: 'var(--surface-2)', borderRadius: '4px', padding: '2px 8px' }}>
                  {TYPE_LABELS[field.type]}
                </span>
                <IconButton type="button" title="Move up" aria-label="Move field up" onClick={() => move(index, -1)} disabled={index === 0} style={{ padding: '0 0.3rem' }}>
                  ↑
                </IconButton>
                <IconButton type="button" title="Move down" aria-label="Move field down" onClick={() => move(index, 1)} disabled={index === safeFields.length - 1} style={{ padding: '0 0.3rem' }}>
                  ↓
                </IconButton>
                <IconButton type="button" title="Remove field" aria-label="Remove field" onClick={() => remove(index)} style={{ padding: '0 0.4rem' }}>
                  <Trash2 size={14} />
                </IconButton>
              </div>
              {isOpen && (
                <div style={{ padding: '0 0.75rem 0.9rem', borderTop: '1px solid var(--border)' }}>
                  <div className="form-grid" style={{ marginTop: '0.6rem' }}>
                    <label>
                      Display name
                      <input
                        value={field.label}
                        placeholder="e.g. Appointment booked"
                        onChange={(e) => {
                          const label = e.target.value;
                          // Auto-derive the key from the label only while it still matches the
                          // machine-readable slug of the previous label — once a PM hand-edits
                          // the key directly it stops tracking the label, same "derived until
                          // touched" pattern used elsewhere in this codebase's id generation.
                          update(index, { label });
                        }}
                      />
                    </label>
                    <label>
                      Field key
                      <input
                        value={field.key}
                        placeholder="e.g. appointment_booked"
                        onChange={(e) => update(index, { key: slugifyKey(e.target.value) })}
                      />
                      <small>Machine-readable key written into the analysis JSON. Must be unique within this bot.</small>
                    </label>
                    <label>
                      Type
                      <select
                        value={field.type}
                        onChange={(e) => {
                          const type = e.target.value as AnalysisFieldType;
                          update(index, {
                            type,
                            enum_options: type === 'enum' ? (field.enum_options && field.enum_options.length ? field.enum_options : ['']) : undefined,
                          });
                        }}
                      >
                        <option value="boolean">Boolean</option>
                        <option value="text">Text</option>
                        <option value="number">Number</option>
                        <option value="enum">Enum</option>
                      </select>
                    </label>
                  </div>
                  <label className="full" style={{ marginTop: '0.6rem' }}>
                    Description
                    <textarea
                      rows={2}
                      value={field.description}
                      placeholder="Explain what this field means so the extractor prompt can pull it out correctly, e.g. &quot;True if the caller agreed to a scheduled appointment time.&quot;"
                      onChange={(e) => update(index, { description: e.target.value })}
                    />
                  </label>
                  {field.type === 'enum' && (
                    <div style={{ marginTop: '0.6rem' }}>
                      <ChipListEditor
                        label="Allowed options"
                        helpText="At least one option is required for an enum field. Press Enter to add."
                        placeholder="e.g. Interested"
                        emptyText="No options yet — required for enum fields"
                        items={(field.enum_options || []).filter((o) => o.trim())}
                        onChange={(v) => update(index, { enum_options: v })}
                      />
                    </div>
                  )}
                  {errors.length > 0 && (
                    <div className="notice error" role="alert" style={{ marginTop: '0.5rem' }}>
                      <AlertCircle size={14} /> {errors.join(' ')}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
