import React, { useState } from 'react';
import { Plus, Trash2, Play, ChevronDown, ChevronRight } from 'lucide-react';
import type { CustomFunction, FunctionParam, FunctionTestResult, HttpMethod } from '../types';
import {
  newCustomFunction,
  newParam,
  newStoreVariable,
  validateFunction,
  sampleArgsFromParams,
} from '../utils/customFunctions';
import { KeyValueEditor } from './KeyValueEditor';

const METHODS: HttpMethod[] = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
const TRIGGERS: { id: CustomFunction['trigger']; label: string; hint: string }[] = [
  { id: 'pre_call', label: 'Before call', hint: 'Fetch lead/caller details before the conversation starts.' },
  { id: 'during_call', label: 'During call', hint: 'Exposed to the LLM as a tool it can call mid-conversation.' },
  { id: 'post_call', label: 'After call', hint: 'Runs after the call ends — analytics, persistence, product change.' },
];

const sectionLabel: React.CSSProperties = {
  fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)',
  textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.9rem 0 0.5rem',
};

/** The full Custom Function editor surface (the "Functions" tab). Owns the list of
 * per-bot custom functions and lets the PM add/edit/remove/test them. */
export function CustomFunctionsEditor({
  functions,
  onChange,
  onTest,
}: {
  functions: CustomFunction[];
  onChange: (next: CustomFunction[]) => void;
  onTest?: (fn: CustomFunction, args: Record<string, unknown>) => Promise<FunctionTestResult>;
}) {
  const [openId, setOpenId] = useState<string | null>(null);

  function update(id: string, patch: Partial<CustomFunction>) {
    onChange(functions.map((f) => (f.id === id ? { ...f, ...patch } : f)));
  }
  function add() {
    const fn = newCustomFunction();
    onChange([...functions, fn]);
    setOpenId(fn.id);
  }
  function remove(id: string) {
    onChange(functions.filter((f) => f.id !== id));
  }

  return (
    <div className="custom-functions-editor">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontWeight: 600 }}>Custom Functions</div>
          <div style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>
            Call any API before, during, or after the conversation.
          </div>
        </div>
        <button type="button" className="primary" onClick={add}><Plus size={14} /> Add function</button>
      </div>

      {functions.length === 0 && (
        <div style={{ fontSize: '0.85rem', color: 'var(--muted)', fontStyle: 'italic', margin: '1rem 0' }}>
          No custom functions yet.
        </div>
      )}

      <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {functions.map((fn) => {
          const isOpen = openId === fn.id;
          const otherNames = functions.filter((f) => f.id !== fn.id).map((f) => (f.name || '').trim());
          return (
            <div key={fn.id} className="cf-card" style={{ border: '1px solid var(--border)', borderRadius: '8px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.6rem 0.75rem' }}>
                <button
                  type="button"
                  aria-label={isOpen ? 'Collapse' : 'Expand'}
                  onClick={() => setOpenId(isOpen ? null : fn.id)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                >
                  {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </button>
                <span style={{ fontWeight: 600, flex: 1 }}>{fn.name || <em style={{ color: 'var(--muted)' }}>Unnamed function</em>}</span>
                <span className="cf-trigger-badge" style={{ fontSize: '0.72rem', background: 'var(--surface-2)', borderRadius: '4px', padding: '2px 8px' }}>
                  {TRIGGERS.find((t) => t.id === fn.trigger)?.label}
                </span>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.78rem', margin: 0 }}>
                  <input type="checkbox" checked={fn.enabled} onChange={(e) => update(fn.id, { enabled: e.target.checked })} style={{ width: 'auto' }} />
                  Enabled
                </label>
                <button type="button" title="Remove function" onClick={() => remove(fn.id)} style={{ padding: '0 0.4rem' }}>
                  <Trash2 size={14} />
                </button>
              </div>
              {isOpen && (
                <div style={{ padding: '0 0.75rem 0.9rem', borderTop: '1px solid var(--border)' }}>
                  <FunctionForm fn={fn} otherNames={otherNames} onUpdate={(patch) => update(fn.id, patch)} onTest={onTest} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FunctionForm({
  fn,
  otherNames,
  onUpdate,
  onTest,
}: {
  fn: CustomFunction;
  otherNames: string[];
  onUpdate: (patch: Partial<CustomFunction>) => void;
  onTest?: (fn: CustomFunction, args: Record<string, unknown>) => Promise<FunctionTestResult>;
}) {
  const [testState, setTestState] = useState<'idle' | 'running'>('idle');
  const [testResult, setTestResult] = useState<FunctionTestResult | null>(null);
  const errors = validateFunction(fn, otherNames);

  function setParam(i: number, patch: Partial<FunctionParam>) {
    onUpdate({ parameters: fn.parameters.map((p, idx) => (idx === i ? { ...p, ...patch } : p)) });
  }

  async function runTest() {
    if (!onTest) return;
    setTestState('running');
    setTestResult(null);
    try {
      const result = await onTest(fn, sampleArgsFromParams(fn.parameters));
      setTestResult(result);
    } catch (e) {
      setTestResult({
        ok: false, status_code: null, latency_ms: 0, response: null,
        extracted_vars: {}, error: e instanceof Error ? e.message : String(e),
        request: { method: fn.method, url: fn.url || '', params: null, json: null, data: null },
      });
    } finally {
      setTestState('idle');
    }
  }

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <div className="form-grid">
        <label>
          Name
          <input value={fn.name} placeholder="e.g. get_lead_details" onChange={(e) => onUpdate({ name: e.target.value })} />
        </label>
        <label>
          When to run
          <select value={fn.trigger} onChange={(e) => onUpdate({ trigger: e.target.value as CustomFunction['trigger'] })}>
            {TRIGGERS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
          <small>{TRIGGERS.find((t) => t.id === fn.trigger)?.hint}</small>
        </label>
      </div>
      <label className="full">
        Description
        <input value={fn.description || ''} placeholder="What this function does (shown to the LLM for during-call tools)" onChange={(e) => onUpdate({ description: e.target.value })} />
      </label>

      <div style={sectionLabel}>API Endpoint</div>
      <div style={{ display: 'flex', gap: '0.4rem' }}>
        <select aria-label="HTTP method" value={fn.method} onChange={(e) => onUpdate({ method: e.target.value as HttpMethod })} style={{ maxWidth: '7rem' }}>
          {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <input aria-label="URL" style={{ flex: 1 }} value={fn.url || ''} placeholder="https://api.example.com/endpoint" onChange={(e) => onUpdate({ url: e.target.value })} />
      </div>
      <label style={{ marginTop: '0.5rem', display: 'block', maxWidth: '14rem' }}>
        Timeout (ms)
        <input type="number" min={1} value={fn.timeout_ms} onChange={(e) => onUpdate({ timeout_ms: Number(e.target.value) })} />
      </label>

      <div style={sectionLabel}>Headers</div>
      <KeyValueEditor label="" hint="HTTP headers sent with the request." value={fn.headers} onChange={(headers) => onUpdate({ headers })} keyPlaceholder="Header-Name" valuePlaceholder="value" />

      <div style={sectionLabel}>Query Parameters</div>
      <KeyValueEditor label="" hint="Appended to the URL as ?key=value." value={fn.query_params} onChange={(query_params) => onUpdate({ query_params })} />

      <div style={sectionLabel}>Request Body — Parameters</div>
      <div style={{ fontSize: '0.75rem', color: 'var(--muted)', marginBottom: '0.4rem' }}>
        For during-call tools, these define what the LLM returns. Sent as {fn.body_mode === 'form' ? 'form data' : 'JSON'}.
      </div>
      <div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.5rem' }}>
        {(['form', 'json'] as const).map((mode) => (
          <button key={mode} type="button" className={fn.body_mode === mode ? 'active' : ''} onClick={() => onUpdate({ body_mode: mode })} style={{ fontSize: '0.8rem', fontWeight: fn.body_mode === mode ? 700 : 400 }}>
            {mode === 'form' ? 'Form' : 'JSON'}
          </button>
        ))}
      </div>
      {fn.parameters.map((p, i) => (
        <div key={i} style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.35rem', alignItems: 'center' }}>
          <input aria-label={`Parameter name ${i + 1}`} style={{ flex: 1 }} placeholder="Name" value={p.name} onChange={(e) => setParam(i, { name: e.target.value })} />
          <input aria-label={`Parameter detail ${i + 1}`} style={{ flex: 1.5 }} placeholder="Detail / description" value={p.description || ''} onChange={(e) => setParam(i, { description: e.target.value })} />
          <select aria-label={`Parameter type ${i + 1}`} value={p.type} onChange={(e) => setParam(i, { type: e.target.value as FunctionParam['type'] })}>
            {(['string', 'number', 'boolean', 'object', 'array'] as const).map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.75rem', margin: 0 }}>
            <input type="checkbox" checked={p.required} onChange={(e) => setParam(i, { required: e.target.checked })} style={{ width: 'auto' }} /> Req
          </label>
          <button type="button" title="Remove parameter" onClick={() => onUpdate({ parameters: fn.parameters.filter((_, idx) => idx !== i) })} style={{ padding: '0 0.4rem' }}>
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button type="button" onClick={() => onUpdate({ parameters: [...fn.parameters, newParam()] })} style={{ fontSize: '0.8rem' }}>
        <Plus size={14} /> Add parameter
      </button>

      <div style={sectionLabel}>Store Fields as Variables</div>
      <div style={{ fontSize: '0.75rem', color: 'var(--muted)', marginBottom: '0.4rem' }}>
        Extract values from the response and store as dynamic variables (usable as {'{{name}}'} in the prompt).
      </div>
      {fn.store_variables.map((sv, i) => (
        <div key={i} style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.35rem' }}>
          <input aria-label={`Variable name ${i + 1}`} style={{ flex: 1 }} placeholder="variable" value={sv.variable} onChange={(e) => onUpdate({ store_variables: fn.store_variables.map((s, idx) => (idx === i ? { ...s, variable: e.target.value } : s)) })} />
          <input aria-label={`Variable path ${i + 1}`} style={{ flex: 1 }} placeholder="response path e.g. data.name" value={sv.json_path} onChange={(e) => onUpdate({ store_variables: fn.store_variables.map((s, idx) => (idx === i ? { ...s, json_path: e.target.value } : s)) })} />
          <button type="button" title="Remove variable" onClick={() => onUpdate({ store_variables: fn.store_variables.filter((_, idx) => idx !== i) })} style={{ padding: '0 0.4rem' }}>
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button type="button" onClick={() => onUpdate({ store_variables: [...fn.store_variables, newStoreVariable()] })} style={{ fontSize: '0.8rem' }}>
        <Plus size={14} /> New key value pair
      </button>

      {errors.length > 0 && (
        <div className="notice error" role="alert" style={{ marginTop: '0.75rem' }}>
          {errors.map((e) => <div key={e} style={{ fontSize: '0.78rem' }}>• {e}</div>)}
        </div>
      )}

      {onTest && (
        <div style={{ marginTop: '0.9rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button type="button" onClick={runTest} disabled={testState === 'running' || errors.length > 0}>
            <Play size={14} /> {testState === 'running' ? 'Testing…' : 'Test'}
          </button>
          {errors.length > 0 && <span style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>Fix the errors above to test.</span>}
        </div>
      )}

      {testResult && (
        <div style={{ marginTop: '0.6rem', border: '1px solid var(--border)', borderRadius: '6px', padding: '0.6rem 0.75rem', fontSize: '0.8rem' }}>
          <div style={{ fontWeight: 600, color: testResult.ok ? 'var(--success, green)' : 'var(--warning, orange)' }}>
            {testResult.error
              ? `Request failed: ${testResult.error}`
              : `HTTP ${testResult.status_code} · ${testResult.latency_ms} ms · ${testResult.ok ? 'OK' : 'non-2xx'}`}
          </div>
          {Object.keys(testResult.extracted_vars || {}).length > 0 && (
            <div style={{ marginTop: '0.4rem' }}>
              Extracted variables: <code>{JSON.stringify(testResult.extracted_vars)}</code>
            </div>
          )}
          {testResult.response != null && (
            <pre style={{ marginTop: '0.4rem', maxHeight: '10rem', overflow: 'auto', background: 'var(--surface-2)', padding: '0.5rem', borderRadius: '4px' }}>
              {typeof testResult.response === 'string' ? testResult.response : JSON.stringify(testResult.response, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
