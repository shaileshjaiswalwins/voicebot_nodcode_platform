import React, { useState } from 'react';
import { Plus, Trash2, X } from 'lucide-react';
import type { CustomFunction } from '../types';

export type DynamicVariables = Record<string, string>;
export type FunctionMocks = Record<string, string>;

/** Test Inputs modal for the Test LLM sandbox (Manual Chat / AI Simulated Chat).
 *
 * Deliberately generic — no hardcoded fields (no lead_id/city/buyer_name assumptions).
 * "Dynamic Variables" is a free-form name/value list the tester defines themselves, and
 * "Custom Function Mocks" is generated from whatever during_call functions this bot actually
 * has configured, so the panel scales to any bot's function set instead of one demo's shape. */
export function TestInputsModal({
  open,
  onClose,
  functions,
  dynamicVariables,
  onChangeDynamicVariables,
  functionMocks,
  onChangeFunctionMocks,
}: {
  open: boolean;
  onClose: () => void;
  functions: CustomFunction[];
  dynamicVariables: DynamicVariables;
  onChangeDynamicVariables: (v: DynamicVariables) => void;
  functionMocks: FunctionMocks;
  onChangeFunctionMocks: (v: FunctionMocks) => void;
}) {
  const [tab, setTab] = useState<'variables' | 'mocks'>('variables');
  const [rows, setRows] = useState<Array<{ name: string; value: string }>>(
    () => Object.entries(dynamicVariables).map(([name, value]) => ({ name, value })),
  );
  const [mocks, setMocks] = useState<FunctionMocks>(() => ({ ...functionMocks }));

  if (!open) return null;

  const duringCallFunctions = functions.filter((fn) => fn.trigger === 'during_call' && fn.enabled);

  function addRow() {
    setRows((r) => [...r, { name: '', value: '' }]);
  }
  function updateRow(idx: number, patch: Partial<{ name: string; value: string }>) {
    setRows((r) => r.map((row, i) => (i === idx ? { ...row, ...patch } : row)));
  }
  function removeRow(idx: number) {
    setRows((r) => r.filter((_, i) => i !== idx));
  }

  function handleSave() {
    const next: DynamicVariables = {};
    rows.forEach(({ name, value }) => {
      if (name.trim()) next[name.trim()] = value;
    });
    onChangeDynamicVariables(next);
    onChangeFunctionMocks(mocks);
    onClose();
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <h2>Test Inputs</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>

        <div className="mode-toggle" role="tablist">
          <button role="tab" aria-selected={tab === 'variables'} className={tab === 'variables' ? 'mode-btn active' : 'mode-btn'} onClick={() => setTab('variables')}>
            Dynamic Variables
          </button>
          <button role="tab" aria-selected={tab === 'mocks'} className={tab === 'mocks' ? 'mode-btn active' : 'mode-btn'} onClick={() => setTab('mocks')}>
            Custom Function Mocks
          </button>
        </div>

        {tab === 'variables' && (
          <div style={{ marginTop: '0.75rem' }}>
            <p className="muted" style={{ fontSize: '0.82rem' }}>
              Seed values substituted for <code>{'{{variable}}'}</code> tokens in the system prompt during Manual Chat and AI Simulated Chat.
            </p>
            {rows.map((row, idx) => (
              <div key={idx} style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.4rem' }}>
                <input
                  placeholder="Enter the variable name"
                  value={row.name}
                  onChange={(e) => updateRow(idx, { name: e.target.value })}
                  style={{ flex: 1 }}
                />
                <input
                  placeholder="Enter the value"
                  value={row.value}
                  onChange={(e) => updateRow(idx, { value: e.target.value })}
                  style={{ flex: 1 }}
                />
                <button className="modal-close" onClick={() => removeRow(idx)} aria-label="Remove variable"><Trash2 size={14} /></button>
              </div>
            ))}
            <button onClick={addRow}><Plus size={13} /> Add</button>
          </div>
        )}

        {tab === 'mocks' && (
          <div style={{ marginTop: '0.75rem' }}>
            <p className="muted" style={{ fontSize: '0.82rem' }}>
              Set a mocked JSON response for each of this bot's during_call functions — used during Manual Chat and AI Simulated Chat instead of the real URL.
            </p>
            {duringCallFunctions.length === 0 && (
              <p className="muted" style={{ fontSize: '0.82rem' }}>This bot has no enabled during_call functions to mock yet.</p>
            )}
            {duringCallFunctions.map((fn) => (
              <label key={fn.id || fn.name} className="full" style={{ marginBottom: '0.6rem' }}>
                {fn.name}
                <textarea
                  rows={2}
                  placeholder='{"result": "..."}'
                  value={mocks[fn.name] || ''}
                  onChange={(e) => setMocks((m) => ({ ...m, [fn.name]: e.target.value }))}
                />
              </label>
            ))}
          </div>
        )}

        <div className="modal-footer" style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1rem' }}>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={handleSave}>Save</button>
        </div>
      </div>
    </div>
  );
}
