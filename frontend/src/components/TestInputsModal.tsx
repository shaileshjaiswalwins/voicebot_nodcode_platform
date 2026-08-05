import React, { useEffect, useRef, useState } from 'react';
import { Plus, Trash2, X } from 'lucide-react';
import type { CustomFunction } from '../types';

export type DynamicVariables = Record<string, string>;
export type FunctionMocks = Record<string, string>;
export type PreCallParams = Record<string, string>;

type Row = { name: string; value: string };
const toRows = (v: Record<string, string>): Row[] => Object.entries(v).map(([name, value]) => ({ name, value }));
const toRecord = (rows: Row[]): Record<string, string> => {
  const out: Record<string, string> = {};
  rows.forEach(({ name, value }) => { if (name.trim()) out[name.trim()] = value; });
  return out;
};

/** Test Inputs modal — shared between the Test LLM sandbox (Manual Chat / AI Simulated
 * Chat) and a real Test Call (Audio).
 *
 * Deliberately generic — no hardcoded fields (no lead_id/city/buyer_name assumptions).
 * "Dynamic Variables" and "Pre-call Parameters" are free-form name/value lists the tester
 * defines themselves; "Custom Function Mocks" is generated from whatever during_call
 * functions this bot actually has configured. Dynamic Variables/Mocks only affect the text
 * sandbox (it never makes real HTTP calls); Pre-call Parameters flow into a real Test Call —
 * they're merged into every pre_call function's query params (bot.py's `_pre_call_params`
 * merge), so e.g. a mock vendor-lookup endpoint that accepts overrides can be steered from
 * here without touching code. */
export function TestInputsModal({
  open,
  onClose,
  functions,
  dynamicVariables,
  onChangeDynamicVariables,
  functionMocks,
  onChangeFunctionMocks,
  preCallParams,
  onChangePreCallParams,
}: {
  open: boolean;
  onClose: () => void;
  functions: CustomFunction[];
  dynamicVariables: DynamicVariables;
  onChangeDynamicVariables: (v: DynamicVariables) => void;
  functionMocks: FunctionMocks;
  onChangeFunctionMocks: (v: FunctionMocks) => void;
  preCallParams: PreCallParams;
  onChangePreCallParams: (v: PreCallParams) => void;
}) {
  const [tab, setTab] = useState<'variables' | 'mocks' | 'precall'>('variables');
  const [rows, setRows] = useState<Row[]>(() => toRows(dynamicVariables));
  const [mocks, setMocks] = useState<FunctionMocks>(() => ({ ...functionMocks }));
  const [precallRows, setPrecallRows] = useState<Row[]>(() => toRows(preCallParams));
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Re-seed local state from props whenever the modal is (re)opened, so a save made
  // elsewhere (or a bot switch) isn't clobbered by stale local state from a prior open.
  useEffect(() => {
    if (!open) return;
    setRows(toRows(dynamicVariables));
    setMocks({ ...functionMocks });
    setPrecallRows(toRows(preCallParams));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function commit(nextRows: Row[], nextMocks: FunctionMocks, nextPrecallRows: Row[]) {
    onChangeDynamicVariables(toRecord(nextRows));
    onChangeFunctionMocks(nextMocks);
    onChangePreCallParams(toRecord(nextPrecallRows));
  }

  function scheduleAutoSave(nextRows: Row[], nextMocks: FunctionMocks, nextPrecallRows: Row[]) {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => commit(nextRows, nextMocks, nextPrecallRows), 300);
  }

  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  if (!open) return null;

  const duringCallFunctions = functions.filter((fn) => fn.trigger === 'during_call' && fn.enabled);
  const preCallFunctions = functions.filter((fn) => fn.trigger === 'pre_call' && fn.enabled);

  function addRow() {
    setRows((r) => {
      const next = [...r, { name: '', value: '' }];
      scheduleAutoSave(next, mocks, precallRows);
      return next;
    });
  }
  function updateRow(idx: number, patch: Partial<Row>) {
    setRows((r) => {
      const next = r.map((row, i) => (i === idx ? { ...row, ...patch } : row));
      scheduleAutoSave(next, mocks, precallRows);
      return next;
    });
  }
  function removeRow(idx: number) {
    setRows((r) => {
      const next = r.filter((_, i) => i !== idx);
      scheduleAutoSave(next, mocks, precallRows);
      return next;
    });
  }
  function updateMock(name: string, value: string) {
    setMocks((m) => {
      const next = { ...m, [name]: value };
      scheduleAutoSave(rows, next, precallRows);
      return next;
    });
  }
  function addPrecallRow() {
    setPrecallRows((r) => {
      const next = [...r, { name: '', value: '' }];
      scheduleAutoSave(rows, mocks, next);
      return next;
    });
  }
  function updatePrecallRow(idx: number, patch: Partial<Row>) {
    setPrecallRows((r) => {
      const next = r.map((row, i) => (i === idx ? { ...row, ...patch } : row));
      scheduleAutoSave(rows, mocks, next);
      return next;
    });
  }
  function removePrecallRow(idx: number) {
    setPrecallRows((r) => {
      const next = r.filter((_, i) => i !== idx);
      scheduleAutoSave(rows, mocks, next);
      return next;
    });
  }

  function handleClose() {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    commit(rows, mocks, precallRows);
    onClose();
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" onClick={handleClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <h2>Test Inputs</h2>
          <button className="modal-close" onClick={handleClose} aria-label="Close"><X size={16} /></button>
        </div>

        <div className="mode-toggle" role="tablist">
          <button role="tab" aria-selected={tab === 'variables'} className={tab === 'variables' ? 'mode-btn active' : 'mode-btn'} onClick={() => setTab('variables')}>
            Dynamic Variables
          </button>
          <button role="tab" aria-selected={tab === 'mocks'} className={tab === 'mocks' ? 'mode-btn active' : 'mode-btn'} onClick={() => setTab('mocks')}>
            Custom Function Mocks
          </button>
          <button role="tab" aria-selected={tab === 'precall'} className={tab === 'precall' ? 'mode-btn active' : 'mode-btn'} onClick={() => setTab('precall')}>
            Pre-call Parameters
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
                  onChange={(e) => updateMock(fn.name, e.target.value)}
                />
              </label>
            ))}
          </div>
        )}

        {tab === 'precall' && (
          <div style={{ marginTop: '0.75rem' }}>
            <p className="muted" style={{ fontSize: '0.82rem' }}>
              Extra query params sent to every pre_call function on a real Test Call (Audio) —
              merged on top of that function's own fixed query_params. Use this to override what
              a pre_call lookup returns (e.g. a mock endpoint that reads owner_name/business_name
              overrides) without touching the bot config. Has no effect on Manual Chat / AI
              Simulated Chat, which never make real HTTP calls.
            </p>
            {preCallFunctions.length === 0 && (
              <p className="muted" style={{ fontSize: '0.82rem' }}>This bot has no enabled pre_call functions.</p>
            )}
            {precallRows.map((row, idx) => (
              <div key={idx} style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.4rem' }}>
                <input
                  placeholder="Enter the param name"
                  value={row.name}
                  onChange={(e) => updatePrecallRow(idx, { name: e.target.value })}
                  style={{ flex: 1 }}
                />
                <input
                  placeholder="Enter the value"
                  value={row.value}
                  onChange={(e) => updatePrecallRow(idx, { value: e.target.value })}
                  style={{ flex: 1 }}
                />
                <button className="modal-close" onClick={() => removePrecallRow(idx)} aria-label="Remove pre-call parameter"><Trash2 size={14} /></button>
              </div>
            ))}
            <button onClick={addPrecallRow}><Plus size={13} /> Add</button>
          </div>
        )}

        <div className="modal-footer" style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1rem' }}>
          <button className="primary" onClick={handleClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
