import React, { useState, useEffect } from 'react';
import { AlertTriangle, ChevronRight, PhoneCall, Play, Sparkles, Square } from 'lucide-react';
import type { Bot as BotType, BotVersion, RuntimeSettings } from '../api';
import { api } from '../api';
import type { CustomFunction, TestCallStatus, TestForm } from '../types';
import { deriveAgentState } from '../utils/config';
import { titleCase } from '../utils/formatting';
import { LiveKitTestSession } from '../components/LiveKitTestSession';

function Step({ title, text }: { title: string; text: string }) {
  return <div className="step"><strong>{title}</strong><p>{text}</p></div>;
}

export function titleFor(view: import('../types').View) {
  return {
    dashboard: 'Dashboard',
    bots: 'Agents',
    builder: 'Agents',
    flow: 'Flow Builder',
    campaigns: 'Campaigns',
    phone_numbers: 'Phone Numbers',
    number_mapping: 'Number Mapping',
    test: 'WebRTC Test Call',
    transcripts: 'Transcripts',
    analytics: 'Analytics',
    library: 'Phrase Library',
    settings: 'Settings',
    audit_log: 'Audit Log',
    admin: 'Admin'
  }[view];
}

export function subtitleFor(view: import('../types').View) {
  return {
    dashboard: 'A live snapshot of every agent — volume, minutes, and performance leaders.',
    bots: 'Manage, edit, publish, and safely delete voice agents.',
    builder: 'Manage, edit, publish, and safely delete voice agents.',
    flow: 'Design the conversation as a visual graph — named nodes, conditionals, and transfers.',
    campaigns: 'Connect one bot to one campaign and its lead/callback APIs.',
    phone_numbers: 'Map real phone numbers to bots and environments for inbound routing.',
    number_mapping: 'Assign one agent to each inbound number.',
    test: 'Start a controlled browser call with helpful connection diagnostics.',
    transcripts: 'Inspect raw call transcripts, outcomes, and config snapshots.',
    analytics: 'Outcome aggregation, quality alerts, and call performance trends.',
    library: 'Edit voicemail, hold-music, and DNC trigger phrases without a code deploy.',
    settings: 'Control LiveKit routing and dashboard runtime options.',
    audit_log: 'Every admin mutation, recorded with who did it and when.',
    admin: 'Configure LLM/STT/TTS model pricing (₹/min), reflected across the platform.'
  }[view];
}

/** One field per query_params key, grouped under the function's own name — the dynamic
 * counterpart to the fixed Call setup fields below, for whatever pre_call functions this
 * particular bot has configured beyond the platform's built-in lead fetch. */
function PreCallFunctionFields({
  functions,
  values,
  onChange,
}: {
  functions: CustomFunction[];
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  const preCallFunctions = functions.filter((fn) => fn.trigger === 'pre_call' && fn.enabled);
  const paramKeys = new Set<string>();
  preCallFunctions.forEach((fn) => Object.keys(fn.query_params || {}).forEach((k) => paramKeys.add(k)));
  // lead_id/mobile/call_id are already covered by the fixed Call setup fields above (and
  // always merged in by the pipeline) — no need to duplicate them here.
  ['lead_id', 'mobile', 'call_id'].forEach((k) => paramKeys.delete(k));

  if (!preCallFunctions.length) return null;

  return (
    <>
      {preCallFunctions.map((fn) => {
        const keys = Object.keys(fn.query_params || {}).filter((k) => paramKeys.has(k));
        if (!keys.length) return null;
        return (
          <div key={fn.id || fn.name}>
            <div className="test-section-label">{fn.name}</div>
            <div className="test-rail-fields">
              {keys.map((key) => (
                <label key={key}>
                  {key}
                  <input
                    value={values[key] ?? ''}
                    placeholder={fn.query_params?.[key] || `Enter ${key}…`}
                    onChange={(e) => onChange(key, e.target.value)}
                  />
                </label>
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}

/** One field per bot-declared dynamic variable (BotConfig.dynamic_variables), so the tester
 * can supply {{var_name}} values for this test call the same way pre_call query params
 * already work above — falls back to the variable's own default_value (set in the prompt
 * editor) when left blank here. */
function DynamicVariableFields({
  variables,
  values,
  onChange,
}: {
  variables: { name: string; default_value?: string }[];
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  if (!variables.length) return null;
  return (
    <div>
      <div className="test-section-label">Dynamic variables</div>
      <div className="test-rail-fields">
        {variables.map((v) => (
          <label key={v.name}>
            {v.name}
            <input
              value={values[v.name] ?? ''}
              placeholder={v.default_value || `Enter ${v.name}…`}
              onChange={(e) => onChange(v.name, e.target.value)}
            />
          </label>
        ))}
      </div>
    </div>
  );
}

/** "What went wrong?" trigger for a just-ended test call — one click feeds the transcript +
 * close note/error to the backend and shows back a plain-English diagnosis + suggested fix,
 * so the user doesn't have to reverse-engineer the transcript by hand. */
function TriageButton({
  roomName,
  status,
  error,
  closeNote,
  onTriage,
}: {
  roomName: string;
  status: TestCallStatus;
  error: string;
  closeNote: string;
  onTriage: (roomName: string, status: TestCallStatus, error: string, closeNote: string) => Promise<{ diagnosis: string; transcript_found: boolean }>;
}) {
  const [state, setState] = useState<'idle' | 'running' | 'failed'>('idle');
  const [result, setResult] = useState<{ diagnosis: string; transcript_found: boolean } | null>(null);
  const [errMsg, setErrMsg] = useState('');

  async function run() {
    setState('running');
    setErrMsg('');
    setResult(null);
    try {
      const res = await onTriage(roomName, status, error, closeNote);
      setResult(res);
      setState('idle');
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : 'Triage failed.');
      setState('failed');
    }
  }

  return (
    <div style={{ marginTop: '0.5rem' }}>
      <button type="button" onClick={run} disabled={state === 'running'} style={{ fontSize: 'var(--font-size-sm)' }}>
        <Sparkles size={13} /> {state === 'running' ? 'Analyzing…' : 'What went wrong?'}
      </button>
      {state === 'failed' && <div className="notice error" role="alert" style={{ marginTop: '0.4rem', fontSize: 'var(--font-size-sm)' }}>{errMsg}</div>}
      {result && (
        <div style={{ marginTop: '0.5rem', border: '1px solid var(--border)', borderRadius: '6px', padding: '0.6rem 0.75rem', fontSize: 'var(--font-size-md)', background: 'var(--surface-2)', whiteSpace: 'pre-wrap' }}>
          {!result.transcript_found && (
            <div style={{ color: 'var(--muted)', fontStyle: 'italic', marginBottom: '0.4rem' }}>
              No transcript found for this call yet — diagnosis is based only on the session status/error.
            </div>
          )}
          {result.diagnosis}
        </div>
      )}
    </div>
  );
}

export function TestCallPanel({
  bots,
  selectedBot,
  selectedBotId,
  onSelectBot,
  runtimeSettings,
  functions,
  dynamicVariables,
  form,
  setForm,
  status,
  error,
  closeNote,
  micEnabled,
  roomName,
  remoteAudioReady,
  livekitUrl,
  livekitToken,
  onStart,
  onStop,
  onLiveKitConnected,
  onLiveKitDisconnected,
  onLiveKitError,
  onMicChange,
  onAudioReady,
  onTriage,
  preferredVersionId,
  variant = 'page'
}: {
  bots: BotType[];
  selectedBot?: BotType;
  selectedBotId: string;
  onSelectBot: (botId: string) => void;
  runtimeSettings: RuntimeSettings | null;
  functions?: CustomFunction[];
  dynamicVariables?: { name: string; default_value?: string }[];
  form: TestForm;
  setForm: React.Dispatch<React.SetStateAction<TestForm>>;
  status: TestCallStatus;
  error: string;
  closeNote: string;
  micEnabled: boolean;
  roomName: string;
  remoteAudioReady: boolean;
  livekitUrl: string;
  livekitToken: string;
  onStart: () => void;
  onStop: () => void;
  onLiveKitConnected: () => void;
  onLiveKitDisconnected: () => void;
  onLiveKitError: (err: Error) => void;
  onMicChange: (enabled: boolean) => void;
  onAudioReady: (ready: boolean) => void;
  /** "What went wrong?" button: sends the just-ended call's room/status/error/close-note to
   * the backend, which pairs it with the transcript and current bot instructions for a
   * plain-English diagnosis. Optional — omitted where there's no bot_id yet to scope it to. */
  onTriage?: (roomName: string, status: TestCallStatus, error: string, closeNote: string) => Promise<{ diagnosis: string; transcript_found: boolean }>;
  /** The version currently open in the Builder ('rail' variant only) — when set and still
   * present after `loadVersions` refetches, it wins over the active/draft/first heuristic.
   * Without this, "Test Agent" silently tested the published version even while editing an
   * unpublished draft, so a field/prompt change just made in the editor appeared to have no
   * effect on the test call — it was actually testing a different, unrelated version. */
  preferredVersionId?: string;
  /**
   * 'page'  — the standalone two-column /test screen (legacy).
   * 'rail'  — a single-column panel docked beside the agent builder. The agent is the one
   *           being edited, so the agent <select> is dropped and the layout is condensed
   *           to fit a ~340px column without the user leaving the prompt they're editing.
   */
  variant?: 'page' | 'rail';
}) {
  const [botVersions, setBotVersions] = useState<BotVersion[]>([]);
  const [versionsError, setVersionsError] = useState('');

  function loadVersions() {
    if (!selectedBot) { setBotVersions([]); return; }
    setVersionsError('');
    api.bot(selectedBot._id)
      .then((bundle) => {
        const sorted = bundle.versions; // already sorted desc by version
        setBotVersions(sorted);
        // Prefer whatever version the Builder is currently editing (e.g. an unpublished
        // draft the PM just added a field to); otherwise auto-select the active published
        // version, falling back to the latest draft.
        const preferred = preferredVersionId && sorted.find((v) => v._id === preferredVersionId);
        const active = sorted.find((v) => v._id === bundle.bot.active_version_id);
        const defaultV = preferred || active || sorted.find((v) => v.state === 'draft') || sorted[0];
        if (defaultV) {
          setForm((prev) => ({ ...prev, test_bot_version_id: defaultV._id }));
        }
      })
      .catch((err) => {
        setBotVersions([]);
        setVersionsError(err instanceof Error ? err.message : 'Failed to load bot versions.');
      });
  }

  useEffect(() => {
    loadVersions();
    // preferredVersionId is intentionally included: switching versions inside an
    // already-open Builder session (e.g. clicking a different row in Version history)
    // must re-sync which version Test Agent defaults to. Without this, the panel kept
    // testing whatever version was selected when it first mounted for this bot —
    // switching to a draft afterward silently kept testing the old (often published)
    // version, with no visible indication the panel hadn't followed along.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBot?._id, preferredVersionId]);

  function updateField(key: keyof TestForm, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updatePreCallParam(key: string, value: string) {
    setForm((current) => ({ ...current, pre_call_params: { ...current.pre_call_params, [key]: value } }));
  }

  function updateDynamicVariable(key: string, value: string) {
    setForm((current) => ({ ...current, dynamic_variables: { ...current.dynamic_variables, [key]: value } }));
  }

  // The name the backend actually dispatches to (testcall.py's TESTCALL_AGENT_NAME). This
  // used to display runtimeSettings.livekit_agent_name, which the test-call endpoint never
  // reads — so the panel could confidently show one worker while the dispatch went to
  // another, and a call that reached nobody looked correctly configured on screen.
  const defaultWorker = 'voice-bot-acmecorp-dashboard-test';
  const effectiveWorker = form.test_worker_agent_name || defaultWorker;
  // Once LiveKit reports its own real AgentState (liveAgentState), prefer it over the
  // heuristic below — deriveAgentState only approximates from the status string and
  // remoteAudioReady, and the two can disagree (caption said "Speaking" while the
  // orb correctly showed "thinking", since the orb already used the real state).
  const [liveAgentState, setLiveAgentState] = useState<string | null>(null);
  const agentState = liveAgentState ?? deriveAgentState(status, remoteAudioReady, Boolean(roomName));
  const connected = Boolean(roomName) && status !== 'Idle' && status !== 'Failed';
  useEffect(() => { if (!connected) setLiveAgentState(null); }, [connected]);

  const selectedVersion = botVersions.find((v) => v._id === form.test_bot_version_id);
  const starting = status === 'Creating room…' || status === 'Connecting to LiveKit…';

  const errorBlock = error ? (
    <div className="notice error test-error">
      <AlertTriangle size={16} />
      <div>
        <strong>{error}</strong>
        <span>Fallback: keep the entered metadata, retry room setup, or end this test and continue editing the bot.</span>
        <div className="button-row">
          <button className="fallback-button" onClick={onStart}>Retry room setup</button>
          <button onClick={onStop}>Skip test for now</button>
        </div>
      </div>
    </div>
  ) : null;

  // ── Rail variant: docked beside the builder, agent implied by the workspace ──
  if (variant === 'rail') {
    return (
      <aside className="panel test-rail" aria-label="Test call">
        <div className="test-rail-header">
          <div>
            <h2>Talk to it</h2>
            <p>{selectedBot ? selectedBot.name : 'No agent selected'}</p>
          </div>
          <span className={`session-dot ${agentState}`} />
        </div>

        <label className="test-rail-version">
          Version to test
          <select
            value={form.test_bot_version_id}
            onChange={(e) => updateField('test_bot_version_id', e.target.value)}
            disabled={botVersions.length === 0 || starting || connected}
          >
            {botVersions.length === 0 && (
              <option value="">{versionsError ? 'Failed to load' : !selectedBot ? 'Select an agent first' : 'Loading…'}</option>
            )}
            {botVersions.map((v) => (
              <option key={v._id} value={v._id}>
                v{v.version} — {v.state === 'published' ? '✓ Published' : '✏ Draft'}
              </option>
            ))}
          </select>
          <small>
            {starting || connected
              ? 'Locked during the call — end it to switch versions.'
              : versionsError
                ? versionsError
                : selectedVersion
                  ? selectedVersion.state === 'published' ? 'Active published version' : 'Draft — not yet live in production'
                  : 'No versions found'}
          </small>
          {versionsError && !(starting || connected) && <button className="fallback-button" style={{ marginTop: '0.35rem' }} onClick={loadVersions}>Retry</button>}
        </label>

        <PreCallFunctionFields functions={functions || []} values={form.pre_call_params} onChange={updatePreCallParam} />
        <DynamicVariableFields variables={dynamicVariables || []} values={form.dynamic_variables} onChange={updateDynamicVariable} />

        <div className="button-row test-rail-actions">
          {error ? (
            <button className="fallback-button" onClick={onStart} disabled={starting}>
              <Play size={16} /> Retry
            </button>
          ) : starting || connected ? (
            <button className="danger-button" onClick={onStop}>
              <Square size={16} /> End call
            </button>
          ) : (
            <button className="primary" onClick={onStart} disabled={!selectedBot}>
              <Play size={16} /> Talk to it
            </button>
          )}
        </div>

        {(connected || roomName || closeNote) && (
          <div className="live-session-card test-rail-session">
            <div className="live-session-header">
              <span className={`session-dot ${agentState}`} />
              <div>
                <h2>Live session</h2>
                <p>{titleCase(agentState)} · {status}</p>
              </div>
            </div>
            <LiveKitTestSession
              serverUrl={livekitUrl}
              token={livekitToken}
              connected={connected}
              status={status}
              onConnected={onLiveKitConnected}
              onDisconnected={onLiveKitDisconnected}
              onError={onLiveKitError}
              onDisconnectRequested={onStop}
              onMicChange={onMicChange}
              onAudioReady={onAudioReady}
              onAgentStateChange={setLiveAgentState}
              botName={selectedBot?.name}
              roomName={roomName}
            />
            {closeNote && <p className="session-close-note">{closeNote}</p>}
            {onTriage && roomName && (status === 'Failed' || Boolean(closeNote) || Boolean(error)) && (
              <TriageButton roomName={roomName} status={status} error={error} closeNote={closeNote} onTriage={onTriage} />
            )}
          </div>
        )}

        {errorBlock}
      </aside>
    );
  }

  return (
    <section className="test-grid">
      <div className="panel">
        <div className="panel-header">
          <div>
            <h2>{selectedBot ? `Test ${selectedBot.name}` : 'Select an agent to test'}</h2>
            <p>Generate the room, connect browser microphone, and dispatch the agent automatically.</p>
          </div>
          <PhoneCall size={20} />
        </div>
        <div className="test-context">
          <div>
            <span>Agent</span>
            <select value={selectedBotId} onChange={(event) => onSelectBot(event.target.value)} disabled={starting || connected}>
              {bots.map((bot) => <option key={bot._id} value={bot._id}>{bot.name}</option>)}
            </select>
            <small>{starting || connected ? 'Locked during the call — end it to switch agents.' : selectedBot?.assistant_id || 'Select an agent to test'}</small>
          </div>
          <ChevronRight size={18} />
          <div>
            <span>Version to test</span>
            <select
              value={form.test_bot_version_id}
              onChange={(e) => updateField('test_bot_version_id', e.target.value)}
              disabled={botVersions.length === 0 || starting || connected}
            >
              {botVersions.length === 0 && (
                <option value="">{versionsError ? 'Failed to load' : !selectedBot ? 'Select an agent first' : 'Loading…'}</option>
              )}
              {botVersions.map((v) => (
                <option key={v._id} value={v._id}>
                  v{v.version} — {v.state === 'published' ? '✓ Published' : '✏ Draft'}
                </option>
              ))}
            </select>
            <small>
              {starting || connected
                ? 'Locked during the call — end it to switch versions.'
                : versionsError
                  ? versionsError
                  : selectedVersion
                    ? selectedVersion.state === 'published'
                      ? `Active published version`
                      : `Draft — not yet live in production`
                    : 'No versions found'}
            </small>
            {versionsError && !(starting || connected) && <button className="fallback-button" style={{ marginTop: '0.35rem' }} onClick={loadVersions}>Retry</button>}
          </div>
          <ChevronRight size={18} />
          <div>
            <span>LiveKit worker</span>
            <strong>{effectiveWorker}</strong>
          </div>
        </div>
        <PreCallFunctionFields functions={functions || []} values={form.pre_call_params} onChange={updatePreCallParam} />
        <DynamicVariableFields variables={dynamicVariables || []} values={form.dynamic_variables} onChange={updateDynamicVariable} />

        <div className="button-row">
          {error ? (
            <button className="fallback-button" onClick={onStart} disabled={starting}>
              <Play size={16} /> Fallback: retry setup
            </button>
          ) : starting || connected ? (
            <button className="danger-button" onClick={onStop}>
              <Square size={16} /> End call
            </button>
          ) : (
            <button className="primary" onClick={onStart} disabled={!selectedBot}>
              <Play size={16} /> Start WebRTC test
            </button>
          )}
        </div>
      </div>
      <div className="panel status-panel">
        <div className="live-session-card">
          <div className="live-session-header">
            <span className={`session-dot ${agentState}`} />
            <div>
              <h2>Live session</h2>
              <p>{titleCase(agentState)} · {status}</p>
            </div>
          </div>
          <LiveKitTestSession
            serverUrl={livekitUrl}
            token={livekitToken}
            connected={connected}
            status={status}
            onConnected={onLiveKitConnected}
            onDisconnected={onLiveKitDisconnected}
            onError={onLiveKitError}
            onDisconnectRequested={onStop}
            onMicChange={onMicChange}
            onAudioReady={onAudioReady}
            onAgentStateChange={setLiveAgentState}
            botName={selectedBot?.name}
            roomName={roomName}
          />
          {closeNote && <p className="session-close-note">{closeNote}</p>}
          {onTriage && roomName && (status === 'Failed' || Boolean(closeNote) || Boolean(error)) && (
            <TriageButton roomName={roomName} status={status} error={error} closeNote={closeNote} onTriage={onTriage} />
          )}
        </div>
        {errorBlock}
      </div>
    </section>
  );
}
