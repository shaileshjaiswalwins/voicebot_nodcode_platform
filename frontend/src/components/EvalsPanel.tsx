import React, { useEffect, useRef, useState } from 'react';
import { Bot, CheckCircle2, ChevronDown, FlaskConical, Plus, Sparkles, Square, Trash2, User, XCircle } from 'lucide-react';
import type { EvalRun, EvalScenario, EvalScenarioResult } from '../api';

// Pre-publish evals/simulations panel (Phase 2a, Vapi/Bland benchmark) — runs an LLM-vs-LLM
// simulated call against the draft's system_prompt and checks it against pass/fail criteria,
// with no live telephony needed. See backend/evals.py.
function blankScenario(): EvalScenario {
  return { name: '', caller_persona: '', max_turns: 4, must_contain: [], must_not_contain: [] };
}

// Mirrors backend/evals.py's DEFAULT_SCENARIOS turn counts — used only to estimate how long
// a "Run default evals" click will take, since the backend runs synchronously and reports
// no progress of its own.
const DEFAULT_SCENARIO_TURNS = [4, 3];
// Rough wall-clock cost of one Gemini flash-lite generate_content call observed in practice.
// Each simulated turn makes two of these (caller reply, then bot reply) — this is a ballpark
// for the countdown, not a measured guarantee.
const SECONDS_PER_LLM_CALL = 2.5;

function estimateSeconds(scenarios: EvalScenario[]): number {
  const turnCounts = scenarios.length ? scenarios.map((s) => s.max_turns || 1) : DEFAULT_SCENARIO_TURNS;
  const totalCalls = turnCounts.reduce((sum, turns) => sum + turns * 2, 0);
  return Math.round(totalCalls * SECONDS_PER_LLM_CALL);
}

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return 'any moment now';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `~${m}m ${s}s` : `~${s}s`;
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
        <div key={idx} className="form-grid eval-fade-up" style={{ border: '1px solid var(--border)', borderRadius: 6, padding: '0.5rem', marginBottom: '0.5rem' }}>
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

/** One scenario's result, in a smoothly-animated collapsible card (grid-template-rows 0fr↔1fr
 * trick, since animating height:auto isn't possible with plain CSS transitions — this avoids
 * native <details>'s instant snap-open/closed). Transcript reuses the chat-turn/chat-bubble
 * classes TestLLMPanel already established, with each turn fading/sliding in on a stagger so
 * a freshly-run scenario reads as a conversation unfolding rather than a text dump. */
function ScenarioResultCard({ result, defaultOpen }: { result: EvalScenarioResult; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(!!defaultOpen);
  // Transcript mounts lazily on first open, rather than always being present at zero height —
  // that's what makes the per-turn stagger below replay on every open, instead of finishing
  // silently the moment the card first renders (while still collapsed and invisible).
  const [everOpened, setEverOpened] = useState(!!defaultOpen);
  function toggle() {
    setOpen((v) => !v);
    if (!everOpened) setEverOpened(true);
  }
  return (
    <div className={open ? 'eval-scenario-card open' : 'eval-scenario-card'}>
      <button className="eval-scenario-summary" onClick={toggle} aria-expanded={open}>
        {result.passed ? <CheckCircle2 size={14} color="var(--success)" className="eval-status-icon" /> : <XCircle size={14} color="var(--danger)" className="eval-status-icon" />}
        <span>{result.scenario}</span>
        <ChevronDown size={14} className="eval-chevron" />
      </button>
      <div className="eval-scenario-collapse">
        <div className="eval-scenario-collapse-inner">
          {everOpened && (
            <div className="chat-transcript eval-transcript">
              {result.transcript.map((turn, idx) => (
                <div
                  key={idx}
                  className={turn.role === 'caller' ? 'chat-turn user eval-turn-in' : 'chat-turn eval-turn-in'}
                  style={{ animationDelay: `${Math.min(idx, 10) * 45}ms` }}
                >
                  <div className="chat-avatar">{turn.role === 'caller' ? <User size={14} /> : <Bot size={14} />}</div>
                  <div className="chat-bubble"><p>{turn.text}</p></div>
                </div>
              ))}
            </div>
          )}
          {result.missing_required_phrases.length > 0 && (
            <p style={{ color: 'var(--warning)', fontSize: 'var(--font-size-md)' }}>Missing required phrases: {result.missing_required_phrases.join(', ')}</p>
          )}
          {result.forbidden_phrases_found.length > 0 && (
            <p style={{ color: 'var(--danger)', fontSize: 'var(--font-size-md)' }}>Forbidden phrases found: {result.forbidden_phrases_found.join(', ')}</p>
          )}
        </div>
      </div>
    </div>
  );
}

export function EvalsPanel({
  runs,
  onRun,
  onStop,
  onGenerateScenarios,
  running,
  disabled,
}: {
  runs: EvalRun[];
  onRun: (scenarios?: EvalScenario[]) => void;
  /** Stops the frontend from waiting on the current run. The backend has no cancellation
   * hook (see backend/evals.py) — the simulation keeps running server-side to completion
   * regardless, so this only ends the UI's wait, not the underlying work. */
  onStop?: () => void;
  /** "Generate with AI" — derives scenarios from the bot's current system_prompt (draft, as
   * currently edited, not necessarily saved yet). Undefined when there's no prompt to work
   * from (e.g. a workflow bot with no system_prompt field), in which case the button hides. */
  onGenerateScenarios?: (count: number) => Promise<EvalScenario[]>;
  running: boolean;
  disabled?: boolean;
}) {
  const latest = runs[0];
  const [customScenarios, setCustomScenarios] = useState<EvalScenario[]>([]);
  const [showEditor, setShowEditor] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string>('');
  const selectedRun = runs.find((r) => r._id === selectedRunId) || latest;
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  // Tracks (by object reference) which entries in customScenarios came from the last AI
  // generation and haven't been hand-edited since. ScenarioEditor's update() always replaces
  // an edited entry with a new object, so once the user touches a generated scenario it
  // naturally falls out of this set — "Regenerate" then only swaps the untouched ones,
  // leaving anything the user has since edited alone.
  const [aiGenerated, setAiGenerated] = useState<EvalScenario[]>([]);

  async function handleGenerate() {
    if (!onGenerateScenarios) return;
    setGenerating(true);
    setGenerateError('');
    try {
      const generated = await onGenerateScenarios(3);
      setCustomScenarios((prev) => [...prev.filter((s) => !aiGenerated.includes(s)), ...generated]);
      setAiGenerated(generated);
      setShowEditor(true);
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : 'Scenario generation failed.');
    } finally {
      setGenerating(false);
    }
  }

  const validCustom = customScenarios.filter((s) => s.name.trim() && s.caller_persona.trim());

  // Countdown estimate — ticks down from a rough total while a run is in flight. Resets
  // whenever `running` flips true so re-runs (and different scenario counts) start fresh.
  const [secondsLeft, setSecondsLeft] = useState(0);
  const estimateRef = useRef(0);
  useEffect(() => {
    if (!running) return;
    estimateRef.current = estimateSeconds(validCustom);
    setSecondsLeft(estimateRef.current);
    const interval = setInterval(() => {
      setSecondsLeft((s) => Math.max(0, s - 1));
    }, 1000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  return (
    <div className="panel" style={{ marginTop: '0.75rem' }}>
      <div className="panel-header">
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <FlaskConical size={16} /> Pre-publish evals
          </h2>
          <p>Simulate calls against this draft's prompt before publishing — no live call required.</p>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.35rem' }}>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button onClick={() => setShowEditor((v) => !v)} disabled={running}>{showEditor ? 'Hide scenarios' : 'Customize scenarios'}</button>
            {running ? (
              <button className="danger-button" onClick={onStop} disabled={!onStop} title="Stops the UI from waiting — the simulation keeps running on the server until it finishes.">
                <Square size={13} /> Stop
              </button>
            ) : (
              <button
                className="primary"
                onClick={() => onRun(validCustom.length ? validCustom : undefined)}
                disabled={disabled}
              >
                {validCustom.length ? `Run ${validCustom.length} custom scenario(s)` : 'Run default evals'}
              </button>
            )}
          </div>
          {running && (
            <small className="eval-simulating">
              <span className="eval-pulse-dot" />
              Simulating… est. {formatCountdown(secondsLeft)} remaining
            </small>
          )}
        </div>
      </div>

      {showEditor && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p style={{ fontSize: 'var(--font-size-md)', color: 'var(--muted)' }}>
            Leave empty to run the two built-in scenarios (interested buyer / not-interested caller). Add scenarios below to run your own instead.
          </p>
          {onGenerateScenarios && (
            <div style={{ marginBottom: '0.6rem' }}>
              <button onClick={handleGenerate} disabled={generating || running} className={generating ? 'eval-generating' : undefined}>
                <Sparkles size={13} className={generating ? 'eval-sparkle-spin' : undefined} />
                {generating ? 'Generating scenarios…' : aiGenerated.length ? 'Regenerate with AI' : 'Generate with AI'}
              </button>
              <small style={{ display: 'block', marginTop: '0.3rem', color: 'var(--muted)' }}>
                Reads this draft's current system prompt and drafts scenarios tailored to what this bot actually does — review and edit before running.
              </small>
              {generateError && <p style={{ color: 'var(--danger)', fontSize: 'var(--font-size-sm)', marginTop: '0.3rem' }}>{generateError}</p>}
            </div>
          )}
          <ScenarioEditor scenarios={customScenarios} onChange={setCustomScenarios} />
        </div>
      )}

      {!latest && !running && <p style={{ fontSize: 'var(--font-size-md)', color: 'var(--muted)' }}>No eval runs yet for this draft.</p>}

      {runs.length > 1 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', marginBottom: '0.75rem' }}>
          <div style={{ fontSize: 'var(--font-size-xs)', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            Run history ({runs.length})
          </div>
          {runs.map((run) => {
            const isSelected = run._id === selectedRun?._id;
            return (
              <button
                key={run._id}
                onClick={() => setSelectedRunId(run._id)}
                className={isSelected ? 'eval-history-row selected' : 'eval-history-row'}
              >
                <span className="eval-history-badge">
                  {run.failed === 0 ? <CheckCircle2 size={13} color="var(--success)" /> : <XCircle size={13} color="var(--danger)" />}
                  {run.passed}/{run.total} scenarios passed
                </span>
                <small style={{ color: 'var(--muted)' }}>{new Date(run.created_at).toLocaleString()}</small>
              </button>
            );
          })}
        </div>
      )}

      {selectedRun && (
        <div key={selectedRun._id} className="eval-results-in">
          <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '0.6rem', fontSize: 'var(--font-size-lg)' }}>
            <span>{selectedRun.passed}/{selectedRun.total} scenarios passed</span>
            <span style={{ color: 'var(--muted)' }}>Run at {new Date(selectedRun.created_at).toLocaleString()}</span>
          </div>
          {selectedRun.results.map((result) => (
            <ScenarioResultCard key={result.scenario} result={result} />
          ))}
        </div>
      )}
    </div>
  );
}
