import React, { useState } from 'react';
import { CheckCircle2, FlaskConical, Plus, Trash2, XCircle } from 'lucide-react';
import type { EvalRun, EvalScenario } from '../api';

// Pre-publish evals/simulations panel (Phase 2a, Vapi/Bland benchmark) — runs an LLM-vs-LLM
// simulated call against the draft's system_prompt and checks it against pass/fail criteria,
// with no live telephony needed. See backend/evals.py.
function blankScenario(): EvalScenario {
  return { name: '', caller_persona: '', max_turns: 4, must_contain: [], must_not_contain: [] };
}

function ScenarioEditor({ scenarios, onChange }: { scenarios: EvalScenario[]; onChange: (s: EvalScenario[]) => void }) {
  function update(idx: number, patch: Partial<EvalScenario>) {
    onChange(scenarios.map((s, i) => (i === idx ? { ...s, ...patch } : s)));
  }
  function remove(idx: number) {
    onChange(scenarios.filter((_, i) => i !== idx));
  }
  return (
    <div style={{ marginBottom: '0.75rem' }}>
      {scenarios.map((scenario, idx) => (
        <div key={idx} className="form-grid" style={{ border: '1px solid var(--border)', borderRadius: 6, padding: '0.5rem', marginBottom: '0.5rem' }}>
          <label>
            Scenario name
            <input value={scenario.name} onChange={(e) => update(idx, { name: e.target.value })} placeholder="e.g. Angry repeat caller" />
          </label>
          <label className="full">
            Caller persona (instructions for the simulated caller)
            <textarea
              rows={2}
              value={scenario.caller_persona}
              onChange={(e) => update(idx, { caller_persona: e.target.value })}
              placeholder="You are a caller who..."
            />
          </label>
          <label>
            Max turns
            <input
              type="number"
              min={1}
              max={20}
              value={scenario.max_turns}
              onChange={(e) => update(idx, { max_turns: Number(e.target.value) || 1 })}
            />
          </label>
          <label>
            Must contain (comma-separated, optional)
            <input
              value={(scenario.must_contain || []).join(', ')}
              onChange={(e) => update(idx, { must_contain: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })}
            />
          </label>
          <label>
            Must NOT contain (comma-separated, optional)
            <input
              value={(scenario.must_not_contain || []).join(', ')}
              onChange={(e) => update(idx, { must_not_contain: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })}
            />
          </label>
          <button className="danger-button full" onClick={() => remove(idx)}><Trash2 size={13} /> Remove scenario</button>
        </div>
      ))}
      <button onClick={() => onChange([...scenarios, blankScenario()])}><Plus size={13} /> Add custom scenario</button>
    </div>
  );
}

export function EvalsPanel({
  runs,
  onRun,
  running,
  disabled,
}: {
  runs: EvalRun[];
  onRun: (scenarios?: EvalScenario[]) => void;
  running: boolean;
  disabled?: boolean;
}) {
  const latest = runs[0];
  const [customScenarios, setCustomScenarios] = useState<EvalScenario[]>([]);
  const [showEditor, setShowEditor] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string>('');
  const selectedRun = runs.find((r) => r._id === selectedRunId) || latest;

  const validCustom = customScenarios.filter((s) => s.name.trim() && s.caller_persona.trim());

  return (
    <div className="panel" style={{ marginTop: '0.75rem' }}>
      <div className="panel-header">
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <FlaskConical size={16} /> Pre-publish evals
          </h2>
          <p>Simulate calls against this draft's prompt before publishing — no live call required.</p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button onClick={() => setShowEditor((v) => !v)}>{showEditor ? 'Hide scenarios' : 'Customize scenarios'}</button>
          <button
            className="primary"
            onClick={() => onRun(validCustom.length ? validCustom : undefined)}
            disabled={disabled || running}
          >
            {running ? 'Running simulation…' : validCustom.length ? `Run ${validCustom.length} custom scenario(s)` : 'Run default evals'}
          </button>
        </div>
      </div>

      {showEditor && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p style={{ fontSize: 'var(--font-size-md)', color: 'var(--muted)' }}>
            Leave empty to run the two built-in scenarios (interested buyer / not-interested caller). Add scenarios below to run your own instead.
          </p>
          <ScenarioEditor scenarios={customScenarios} onChange={setCustomScenarios} />
        </div>
      )}

      {!latest && !running && <p style={{ fontSize: 'var(--font-size-md)', color: 'var(--muted)' }}>No eval runs yet for this draft.</p>}

      {runs.length > 1 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', marginBottom: '0.75rem' }}>
          <div style={{ fontSize: 'var(--font-size-xs)', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            Run history ({runs.length})
          </div>
          {runs.map((run) => (
            <button
              key={run._id}
              onClick={() => setSelectedRunId(run._id)}
              style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                textAlign: 'left', width: '100%',
                background: run._id === (selectedRun?._id) ? 'var(--primary-bg)' : undefined,
                borderColor: run._id === (selectedRun?._id) ? 'var(--primary)' : undefined,
              }}
            >
              <span>{run.passed}/{run.total} scenarios passed</span>
              <small style={{ color: 'var(--muted)' }}>{new Date(run.created_at).toLocaleString()}</small>
            </button>
          ))}
        </div>
      )}

      {selectedRun && (
        <div>
          <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '0.6rem', fontSize: 'var(--font-size-lg)' }}>
            <span>{selectedRun.passed}/{selectedRun.total} scenarios passed</span>
            <span style={{ color: 'var(--muted)' }}>Run at {new Date(selectedRun.created_at).toLocaleString()}</span>
          </div>
          {selectedRun.results.map((result) => (
            <details key={result.scenario} style={{ marginBottom: '0.5rem', border: '1px solid var(--border)', borderRadius: 6, padding: '0.5rem' }}>
              <summary style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}>
                {result.passed ? <CheckCircle2 size={14} color="var(--success)" /> : <XCircle size={14} color="var(--danger)" />}
                {result.scenario}
              </summary>
              <div style={{ marginTop: '0.5rem', fontSize: 'var(--font-size-md)' }}>
                {result.transcript.map((turn, idx) => (
                  <p key={idx} style={{ margin: '0.2rem 0' }}>
                    <strong>{turn.role === 'caller' ? 'Caller' : 'Bot'}:</strong> {turn.text}
                  </p>
                ))}
                {result.missing_required_phrases.length > 0 && (
                  <p style={{ color: 'var(--warning)' }}>Missing required phrases: {result.missing_required_phrases.join(', ')}</p>
                )}
                {result.forbidden_phrases_found.length > 0 && (
                  <p style={{ color: 'var(--danger)' }}>Forbidden phrases found: {result.forbidden_phrases_found.join(', ')}</p>
                )}
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}
