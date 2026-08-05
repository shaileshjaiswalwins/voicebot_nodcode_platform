import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Trash2, Play, ChevronDown, ChevronRight, AlertCircle, Sparkles, Terminal } from 'lucide-react';
import type { CustomFunction, FunctionParam, FunctionTestResult, HttpMethod } from '../types';
import {
  newCustomFunction,
  newParam,
  validateFunction,
  sampleArgsFromParams,
  ensureFunctionIds,
  parseCurlCommand,
} from '../utils/customFunctions';
import { functionNeedsAttention } from '../utils/workflowPlaceholders';
import { KeyValueEditor } from './KeyValueEditor';

const METHODS: HttpMethod[] = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
const TRIGGERS: { id: CustomFunction['trigger']; label: string; hint: string }[] = [
  { id: 'pre_call', label: 'Before call', hint: 'Fetch lead/caller details before the conversation starts.' },
  { id: 'during_call', label: 'During call', hint: 'Exposed to the LLM as a tool it can call mid-conversation.' },
  { id: 'post_call', label: 'After call', hint: 'Runs after the call ends — analytics, persistence, product change.' },
];

/** The full Custom Function editor surface (the "Functions" tab). Owns the list of
 * per-bot custom functions and lets the PM add/edit/remove/test them. */
export function CustomFunctionsEditor({
  functions,
  onChange,
  onTest,
  onGenerateFunction,
}: {
  functions: CustomFunction[];
  onChange: (next: CustomFunction[]) => void;
  onTest?: (fn: CustomFunction, args: Record<string, unknown>) => Promise<FunctionTestResult>;
  onGenerateFunction?: (description: string) => Promise<Partial<CustomFunction>>;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  // Memoized on the `functions` prop reference: ensureFunctionIds generates fresh random ids
  // for blank/duplicate ones, and recomputing on every render (e.g. an unrelated openId toggle)
  // would reassign ids out from under `openId`, collapsing whatever card was open.
  const safeFunctions = useMemo(() => ensureFunctionIds(functions), [functions]);

  function update(id: string, patch: Partial<CustomFunction>) {
    onChange(safeFunctions.map((f) => (f.id === id ? { ...f, ...patch } : f)));
  }
  function add() {
    const fn = newCustomFunction();
    onChange([...safeFunctions, fn]);
    setOpenId(fn.id);
  }
  function remove(id: string) {
    onChange(safeFunctions.filter((f) => f.id !== id));
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
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <CurlImportButton
            onImported={(fn) => {
              onChange([...safeFunctions, fn]);
              setOpenId(fn.id);
            }}
          />
          {onGenerateFunction && (
            <FunctionAssistButton
              onGenerate={onGenerateFunction}
              onCreated={(fn) => {
                onChange([...safeFunctions, fn]);
                setOpenId(fn.id);
              }}
            />
          )}
          <button type="button" className="primary" onClick={add}><Plus size={14} /> Add function</button>
        </div>
      </div>

      {safeFunctions.length === 0 && (
        <div style={{ fontSize: '0.85rem', color: 'var(--muted)', fontStyle: 'italic', margin: '1rem 0' }}>
          No custom functions yet.
        </div>
      )}

      <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {safeFunctions.map((fn) => {
          const isOpen = openId === fn.id;
          const otherNames = safeFunctions.filter((f) => f.id !== fn.id).map((f) => (f.name || '').trim());
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
                {functionNeedsAttention(fn) && (
                  <span title="Placeholder endpoint — needs a real URL" style={{ display: 'inline-flex', color: '#dc2626' }}>
                    <AlertCircle size={15} />
                  </span>
                )}
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

/** Sparkles trigger + popover for the Functions tab: paste an API description / curl example /
 * docs snippet and get back a whole new draft function (method/url/headers/params/store
 * variables filled in) to review before saving — same click-describe-apply interaction as the
 * prompt fields' "Generate with AI" button (BotConfigTabs.tsx's PromptAssistButton), but this
 * one creates a brand-new function rather than editing existing text. */
function FunctionAssistButton({
  onGenerate,
  onCreated,
}: {
  onGenerate: (description: string) => Promise<Partial<CustomFunction>>;
  onCreated: (fn: CustomFunction) => void;
}) {
  const [description, setDescription] = useState('');
  const [state, setState] = useState<'idle' | 'running' | 'failed'>('idle');
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  async function handleGenerate() {
    if (!description.trim()) return;
    setState('running');
    setError('');
    try {
      const result = await onGenerate(description.trim());
      onCreated({ ...newCustomFunction(), ...result });
      setDescription('');
      setState('idle');
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed.');
      setState('failed');
    }
  }

  return (
    <div className="prompt-assist-toolbar" ref={ref}>
      <button
        type="button"
        className="prompt-assist-trigger"
        title="Generate a function with AI"
        aria-label="Generate a function with AI"
        onClick={() => setOpen((v) => !v)}
      >
        <Sparkles size={14} />
      </button>
      {open && (
        <div className="prompt-assist-popover">
          <textarea
            rows={4}
            autoFocus
            placeholder={'Paste a curl example, API docs snippet, or describe the call — e.g.\n"GET https://api.example.com/orders/{order_id} with header Authorization: Bearer <token>, returns { status, eta }"'}
            value={description}
            disabled={state === 'running'}
            onChange={(e) => setDescription(e.target.value)}
          />
          <button type="button" className="primary" disabled={state === 'running' || !description.trim()} onClick={handleGenerate}>
            <Sparkles size={13} /> {state === 'running' ? 'Working…' : 'Generate function'}
          </button>
          {state === 'failed' && <div className="notice error" role="alert">{error}</div>}
        </div>
      )}
    </div>
  );
}

/** "Import from cURL" trigger + popover: paste a raw curl command (e.g. "Copy as cURL" from
 * DevTools or API docs) and get back a whole new draft function — parsed deterministically
 * client-side (parseCurlCommand), not via the LLM, so it's instant and works offline. Sibling
 * to FunctionAssistButton (the AI-assist sparkle), same click-paste-apply interaction. */
function CurlImportButton({ onImported }: { onImported: (fn: CustomFunction) => void }) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  function handleImport() {
    setError('');
    try {
      const parsed = parseCurlCommand(text);
      onImported({ ...newCustomFunction(), ...parsed });
      setText('');
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not parse that curl command.');
    }
  }

  return (
    <div className="prompt-assist-toolbar" ref={ref}>
      <button
        type="button"
        title="Import a function from a curl command"
        aria-label="Import a function from a curl command"
        onClick={() => setOpen((v) => !v)}
      >
        <Terminal size={14} /> Import from cURL
      </button>
      {open && (
        <div className="prompt-assist-popover">
          <textarea
            rows={5}
            autoFocus
            placeholder={'Paste a curl command, e.g.\ncurl -X POST https://api.example.com/orders \\\n  -H "Authorization: Bearer <token>" \\\n  -d \'{"order_id": "123"}\''}
            value={text}
            onChange={(e) => setText(e.target.value)}
            style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}
          />
          <button type="button" className="primary" disabled={!text.trim()} onClick={handleImport}>
            <Terminal size={13} /> Import
          </button>
          {error && <div className="notice error" role="alert">{error}</div>}
        </div>
      )}
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

  const storeVarsRecord: Record<string, string> = {};
  for (const sv of fn.store_variables) if (sv.variable.trim()) storeVarsRecord[sv.variable] = sv.json_path;

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <div className="form-grid">
        <label>
          Function Name
          <input value={fn.name} placeholder="e.g. get_lead_details" onChange={(e) => onUpdate({ name: e.target.value })} />
        </label>
        <label>
          Function Description
          <input value={fn.description || ''} placeholder="e.g. Fetch the caller's lead details" onChange={(e) => onUpdate({ description: e.target.value })} />
          <small>Shown to the LLM for during-call tools.</small>
        </label>
      </div>

      <label className="full" style={{ marginTop: '0.6rem' }}>
        Function URL
        <input
          aria-label="URL"
          style={functionNeedsAttention(fn) ? { borderColor: '#dc2626', boxShadow: '0 0 0 1px #dc2626' } : undefined}
          value={functionNeedsAttention(fn) ? '' : fn.url || ''}
          placeholder="https://api.example.com/function"
          onChange={(e) => onUpdate({ url: e.target.value })}
        />
        <small>The endpoint URL where the function will be called.</small>
      </label>
      {functionNeedsAttention(fn) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: '#dc2626', fontSize: '0.78rem', marginTop: '0.3rem' }}>
          <AlertCircle size={13} /> AI-generated placeholder — set the real endpoint before this function can run.
        </div>
      )}

      <div className="form-grid" style={{ marginTop: '0.6rem' }}>
        <label>
          HTTP Method
          <select aria-label="HTTP method" value={fn.method} onChange={(e) => onUpdate({ method: e.target.value as HttpMethod })}>
            {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label>
          Body Format
          <div style={{ display: 'flex', gap: '0.4rem' }}>
            {(['form', 'json'] as const).map((mode) => (
              <button key={mode} type="button" className={fn.body_mode === mode ? 'active' : ''} onClick={() => onUpdate({ body_mode: mode })} style={{ fontSize: '0.85rem', fontWeight: fn.body_mode === mode ? 700 : 400 }}>
                {mode === 'form' ? 'Form' : 'JSON'}
              </button>
            ))}
          </div>
          <small>For during-call tools, this is how parameters below are sent.</small>
        </label>
      </div>

      <label style={{ marginTop: '0.6rem', display: 'block', maxWidth: '14rem' }}>
        Timeout (ms)
        <input
          type="text"
          inputMode="numeric"
          value={fn.timeout_ms}
          onChange={(e) => {
            const digits = e.target.value.replace(/\D/g, '');
            onUpdate({ timeout_ms: digits === '' ? 0 : Number(digits) });
          }}
        />
      </label>

      <label className="full" style={{ marginTop: '0.6rem' }}>
        When to run
        <select value={fn.trigger} onChange={(e) => onUpdate({ trigger: e.target.value as CustomFunction['trigger'] })}>
          {TRIGGERS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
        <small>{TRIGGERS.find((t) => t.id === fn.trigger)?.hint}</small>
      </label>

      <div style={{ marginTop: '1rem' }}>
        <KeyValueEditor
          key={`${fn.id}-headers`}
          label="Headers"
          hint="HTTP headers sent with the request."
          value={fn.headers}
          onChange={(headers) => onUpdate({ headers })}
          keyPlaceholder="Header-Name"
          valuePlaceholder="value"
          addLabel="Add Header"
          emptyLabel="No headers configured"
        />

        <KeyValueEditor
          key={`${fn.id}-query`}
          label="Query Parameters"
          hint="Appended to the URL as ?key=value."
          value={fn.query_params}
          onChange={(query_params) => onUpdate({ query_params })}
          addLabel="Add Parameter"
          emptyLabel="No query parameters configured"
        />

        <div style={{ marginBottom: '0.9rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.4rem' }}>
            <div>
              <div style={{ fontSize: '0.85rem', fontWeight: 600 }}>Request Body — Parameters</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>
                For during-call tools, these define what the LLM returns. Sent as {fn.body_mode === 'form' ? 'form data' : 'JSON'}.
              </div>
            </div>
            <button type="button" onClick={() => onUpdate({ parameters: [...fn.parameters, newParam()] })} style={{ fontSize: '0.78rem', flexShrink: 0 }}>
              <Plus size={13} /> Add Parameter
            </button>
          </div>
          {fn.parameters.length === 0 ? (
            <div style={{ border: '1px dashed var(--border)', borderRadius: '6px', padding: '1.1rem', textAlign: 'center' }}>
              <div style={{ fontSize: '0.82rem', color: 'var(--muted)' }}>No parameters configured</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--muted)', opacity: 0.8 }}>Click "Add Parameter" above to add one.</div>
            </div>
          ) : (
            <div style={{ border: '1px solid var(--border)', borderRadius: '6px', padding: '0.6rem' }}>
              {fn.parameters.map((p, i) => (
                <div key={i} style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', marginBottom: i === fn.parameters.length - 1 ? 0 : '0.4rem' }}>
                  <input aria-label={`Parameter name ${i + 1}`} style={{ flex: 1, minWidth: '6rem' }} placeholder="Name" value={p.name} onChange={(e) => setParam(i, { name: e.target.value })} />
                  <input aria-label={`Parameter detail ${i + 1}`} style={{ flex: 1.5, minWidth: '9rem' }} placeholder="Detail / description" value={p.description || ''} onChange={(e) => setParam(i, { description: e.target.value })} />
                  <select aria-label={`Parameter type ${i + 1}`} value={p.type} onChange={(e) => setParam(i, { type: e.target.value as FunctionParam['type'] })}>
                    {(['string', 'number', 'boolean', 'object', 'array'] as const).map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.75rem', margin: 0 }}>
                    <input type="checkbox" checked={p.required} onChange={(e) => setParam(i, { required: e.target.checked })} style={{ width: 'auto' }} /> Req
                  </label>
                  <button type="button" title="Remove parameter" onClick={() => onUpdate({ parameters: fn.parameters.filter((_, idx) => idx !== i) })} style={{ padding: '0 0.4rem', flexShrink: 0 }}>
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <KeyValueEditor
          key={`${fn.id}-storevars`}
          label="Store Fields as Variables"
          hint={`Extract values from the response and store as dynamic variables (usable as {{name}} in the prompt).`}
          value={storeVarsRecord}
          onChange={(next) => onUpdate({ store_variables: Object.entries(next).map(([variable, json_path]) => ({ variable, json_path })) })}
          keyPlaceholder="variable"
          valuePlaceholder="response path e.g. data.name"
          addLabel="Add Variable"
          emptyLabel="No variables configured"
        />
      </div>

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
