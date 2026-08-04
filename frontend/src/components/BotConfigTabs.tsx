import React, { useEffect, useRef, useState } from 'react';
import { Activity, AlertTriangle, Database, Sparkles } from 'lucide-react';
import type { RuntimeConfig, CustomFunction, FunctionTestResult } from '../types';
import type { BotMetrics, LanguageOption, PricingConfig, PricingModelEntry } from '../api';
import { api } from '../api';
import { EmptyState } from './EmptyState';
import { MiniBarChart, MiniLineChart } from './MiniCharts';
import { SARVAM_TTS_VOICES, SARVAM_TTS_LANGUAGES, TTS_PROVIDER_MODEL_KEY } from '../constants/ui';
import { CustomFunctionsEditor } from './CustomFunctionsEditor';
import { CloseMarkersEditor } from './CloseMarkersEditor';
import { ChipListEditor } from './ChipListEditor';
import { ProviderOptionsEditor } from './ProviderOptionsEditor';
import { CostEstimateStrip } from './CostEstimateStrip';
import { WorkflowBuilderView } from '../views/WorkflowBuilderView';
import { SARVAM_STT_FIELDS, SARVAM_TTS_FIELDS, GEMINI_LLM_FIELDS } from '../constants/providerParams';
import type { ParamField } from '../constants/providerParams';
import type { AgentCostEstimate } from '../utils/agentCost';

export type BuilderTab = 'metrics' | 'agent' | 'speed' | 'stt' | 'tts' | 'llm' | 'functions' | 'workflow' | 'advanced';

const TABS: { id: BuilderTab; label: string }[] = [
  { id: 'metrics', label: 'Metrics' },
  { id: 'agent', label: 'Agent' },
  { id: 'speed', label: 'Speed' },
  { id: 'stt', label: 'STT' },
  { id: 'tts', label: 'TTS' },
  { id: 'llm', label: 'LLM' },
  { id: 'functions', label: 'Functions' },
  { id: 'workflow', label: 'Workflow' },
  { id: 'advanced', label: 'Advanced' },
];

/** Metrics tab content — per-bot call performance over a selectable window. Needs a saved
 * bot_id (transcripts are keyed by it), so it shows a "save this agent first" notice for a
 * not-yet-created bot, matching how other botId-dependent features in this file degrade
 * (see the Functions tab's `botId` guard below). */
function MetricsTab({ botId }: { botId?: string }) {
  const [days, setDays] = useState<7 | 30>(7);
  const [metrics, setMetrics] = useState<BotMetrics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!botId) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    api.botMetrics(botId, days)
      .then((m) => { if (!cancelled) setMetrics(m); })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load metrics.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [botId, days]);

  if (!botId) {
    return (
      <div role="tabpanel">
        <div className="notice" style={{ display: 'flex', alignItems: 'flex-start', gap: '0.5rem' }}>
          <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: '0.1rem' }} />
          <span>Save this agent first — metrics need a saved agent ID to look up call history.</span>
        </div>
      </div>
    );
  }

  const fmtTrend = (v: number | null) => v === null ? '—' : `${v >= 0 ? '+' : ''}${v}% vs previous ${days}d`;

  return (
    <div role="tabpanel">
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '0.75rem' }}>
        <select value={days} onChange={(e) => setDays(Number(e.target.value) as 7 | 30)}>
          <option value={7}>Last 7 Days</option>
          <option value={30}>Last 30 Days</option>
        </select>
      </div>

      {error && <div className="notice error" role="alert">{error}</div>}
      {loading && !metrics && <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Loading…</p>}

      {metrics && metrics.total_calls === 0 ? (
        <EmptyState
          icon={<Activity size={32} />}
          heading="No calls yet"
          description="This agent hasn't been called yet — metrics will appear here once it has."
        />
      ) : metrics && (
        <>
          <div className="metric-board" style={{ marginBottom: '12px' }}>
            <div className="metric">
              <span>Total calls</span>
              <strong>{metrics.total_calls}</strong>
              <small style={{ fontWeight: 400, fontSize: '0.75rem' }}>{fmtTrend(metrics.trend_vs_previous_pct.total_calls)}</small>
            </div>
            <div className="metric">
              <span>Success rate</span>
              <strong>{metrics.success_rate_pct}%</strong>
              <small style={{ fontWeight: 400, fontSize: '0.75rem' }}>{fmtTrend(metrics.trend_vs_previous_pct.success_rate)}</small>
            </div>
            <div className="metric">
              <span>Avg duration</span>
              <strong>{metrics.avg_duration_sec}s</strong>
            </div>
          </div>
          <div className="metric-board" style={{ marginBottom: '16px' }}>
            <div className="metric"><span>Calls today</span><strong>{metrics.calls_today}</strong></div>
            <div className="metric"><span>Calls this week</span><strong>{metrics.calls_this_week}</strong></div>
            <div className="metric"><span>Calls this month</span><strong>{metrics.calls_this_month}</strong></div>
          </div>

          <div className="content-grid two-col" style={{ marginBottom: '16px' }}>
            <div className="panel">
              <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>Call Volume Trends</h3>
              <MiniBarChart data={metrics.daily_volume.map(d => ({ label: d.date.slice(5), value: d.count }))} />
            </div>
            <div className="panel">
              <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>Call Duration Trends</h3>
              <MiniLineChart data={metrics.daily_avg_duration.map(d => ({ label: d.date.slice(5), value: d.avg_duration_sec }))} unit="s" />
            </div>
          </div>

          {metrics.outcome_breakdown.length > 0 && (
            <>
              <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>Call Success Analysis</h3>
              <div className="detail-list">
                {metrics.outcome_breakdown.map(({ outcome, count }) => {
                  const total = metrics.outcome_breakdown.reduce((s, o) => s + o.count, 0) || 1;
                  return (
                    <div key={outcome} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{ minWidth: '140px', fontSize: '0.82rem' }}>{outcome}</span>
                      <div style={{ flex: 1, background: 'var(--bg-tertiary)', borderRadius: '3px', height: '8px', overflow: 'hidden' }}>
                        <div style={{ width: `${(count / total) * 100}%`, background: 'var(--success)', height: '100%', transition: 'width 0.3s' }} />
                      </div>
                      <span style={{ fontSize: '0.82rem', minWidth: '40px', textAlign: 'right' }}>{count}</span>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

type PromptAssistTarget = 'system_prompt' | 'closing_line' | 'analysis_prompt' | 'global_prompt';

/** The Sparkles trigger + popover for "Generate/Refine with AI" — originally built just for
 * System prompt, generalized so Closing line and Analysis prompt override can reuse the exact
 * same interaction (click Sparkles, describe the change, apply) against their own backend
 * framing (see backend/prompt_assist.py's per-target instructions). */
function PromptAssistButton({
  target,
  currentText,
  onGenerate,
  onApply,
  generateLabel,
  refineLabel,
  generatePlaceholder,
  refinePlaceholder,
}: {
  target: PromptAssistTarget;
  currentText: string;
  onGenerate: (mode: 'generate' | 'refine', instruction: string, currentText: string, target: PromptAssistTarget) => Promise<{ text: string }>;
  onApply: (text: string) => void;
  generateLabel: string;
  refineLabel: string;
  generatePlaceholder: string;
  refinePlaceholder: string;
}) {
  const [instruction, setInstruction] = useState('');
  const [state, setState] = useState<'idle' | 'running' | 'failed'>('idle');
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  // analysis_prompt always refines (even from blank — see backend docstring); the other two
  // generate from scratch until there's existing text, then switch to refine.
  const hasExisting = target === 'analysis_prompt' || Boolean(currentText.trim());

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

  const handleGenerate = async () => {
    if (!instruction.trim()) return;
    setState('running');
    setError('');
    try {
      const mode = hasExisting ? 'refine' : 'generate';
      const result = await onGenerate(mode, instruction.trim(), currentText, target);
      onApply(result.text);
      setInstruction('');
      setState('idle');
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed.');
      setState('failed');
    }
  };

  return (
    <div className="prompt-assist" ref={ref}>
      <button
        type="button"
        className="prompt-assist-trigger"
        title={hasExisting ? refineLabel : generateLabel}
        aria-label={hasExisting ? refineLabel : generateLabel}
        onClick={() => setOpen((v) => !v)}
      >
        <Sparkles size={14} />
      </button>
      {open && (
        <div className="prompt-assist-popover">
          <textarea
            rows={2}
            autoFocus
            placeholder={hasExisting ? refinePlaceholder : generatePlaceholder}
            value={instruction}
            disabled={state === 'running'}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleGenerate(); } }}
          />
          <button type="button" className="primary" disabled={state === 'running' || !instruction.trim()} onClick={handleGenerate}>
            <Sparkles size={13} /> {state === 'running' ? 'Working…' : hasExisting ? refineLabel : generateLabel}
          </button>
          {state === 'failed' && <div className="notice error" role="alert">{error}</div>}
        </div>
      )}
    </div>
  );
}

/** Tabbed bot settings (retell.ai-style): Metrics · Agent · Speed · STT · TTS · LLM ·
 * Functions · Workflow · Advanced. Every field is per-bot and versioned via the parent's
 * onUpdateConfig, except Metrics which is read-only reporting. */
export function BotConfigTabs({
  value,
  onUpdateConfig,
  onUpdateLanguage,
  languages,
  configText,
  onConfigTextChange,
  configOk,
  configError,
  botId,
  onTestFunction,
  onGeneratePrompt,
  costEstimate,
  pricing,
  onTabChange,
}: {
  value: RuntimeConfig;
  onUpdateConfig: (key: keyof RuntimeConfig, value: unknown) => void;
  onUpdateLanguage: (value: string) => void;
  languages: LanguageOption[];
  configText: string;
  onConfigTextChange: (value: string) => void;
  configOk: boolean;
  configError?: string;
  botId?: string;
  onTestFunction?: (fn: CustomFunction, args: Record<string, unknown>) => Promise<FunctionTestResult>;
  onGeneratePrompt?: (
    mode: 'generate' | 'refine',
    instruction: string,
    currentPrompt: string,
    target: 'system_prompt' | 'closing_line' | 'analysis_prompt' | 'global_prompt',
  ) => Promise<{ text: string }>;
  costEstimate?: AgentCostEstimate | null;
  pricing?: PricingConfig | null;
  /** Lets the workspace shell know when the Workflow tab is active, so it can drop the
   * two-column layout (test rail + other builder chrome) and give the graph canvas the
   * full viewport — it's unusable squeezed into a 340px-narrower shared column. */
  onTabChange?: (tab: BuilderTab) => void;
}) {
  const [tab, setTabState] = useState<BuilderTab>('agent');
  function setTab(next: BuilderTab) {
    setTabState(next);
    onTabChange?.(next);
  }
  // Scoped JSON editor for just the `workflow` graph field (rather than the whole bot's
  // Developer JSON below) — local text state so an operator can type transiently-invalid
  // JSON without it being force-parsed on every keystroke, matching the Developer JSON
  // editor's own pattern. No visual drag-and-drop editor for this schema exists yet
  // (see plans/07-nocode-platform-demo-readiness.md) — this is the interim authoring path.
  const [workflowText, setWorkflowText] = useState(() => JSON.stringify(value.workflow || { nodes: [], edges: [] }, null, 2));
  const [workflowJsonError, setWorkflowJsonError] = useState<string | undefined>(undefined);
  const [workflowJsonOpen, setWorkflowJsonOpen] = useState(false);
  // Resync the local draft when switching bots (parent identifies this via botId) — matches
  // BuilderView.tsx's own configText/editingVersionId resync pattern, so this scoped editor
  // doesn't carry stale text from a previously-viewed bot across a switch.
  useEffect(() => {
    setWorkflowText(JSON.stringify(value.workflow || { nodes: [], edges: [] }, null, 2));
    setWorkflowJsonError(undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId]);
  // The canvas above edits `value.workflow` directly (not through this textarea), so resync
  // the JSON draft whenever the panel opens — otherwise it'd show whatever was there the last
  // time it was opened rather than the canvas's current graph.
  useEffect(() => {
    if (!workflowJsonOpen) return;
    setWorkflowText(JSON.stringify(value.workflow || { nodes: [], edges: [] }, null, 2));
    setWorkflowJsonError(undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowJsonOpen]);
  const handleWorkflowTextChange = (text: string) => {
    setWorkflowText(text);
    try {
      const parsed = JSON.parse(text);
      setWorkflowJsonError(undefined);
      onUpdateConfig('workflow', parsed);
    } catch (e) {
      setWorkflowJsonError(e instanceof Error ? e.message : String(e));
    }
  };
  // Only companies whose provider is actually wired into pipeline_providers.py may surface
  // as selectable models here — the pricing catalog also carries catalog-only companies
  // (Google TTS/STT, Cartesia, Anthropic, ...) that would silently no-op on a real call.
  const byCompany = (entries: PricingModelEntry[] | undefined, company: string): PricingModelEntry[] =>
    (entries || []).filter((e) => e.company === company);
  const openAiModels = byCompany(pricing?.llm, 'OpenAI');
  const geminiModels = byCompany(pricing?.llm, 'Google').filter((e) => e.key.startsWith('gemini'));
  const deepgramSttModels = byCompany(pricing?.stt, 'Deepgram');
  const geminiLlmFields: ParamField[] = GEMINI_LLM_FIELDS.map((f) =>
    f.key === 'model' && geminiModels.length
      ? { ...f, options: geminiModels.map((e) => e.label) }
      : f
  );
  const recording = (typeof value.recording === 'object' && value.recording ? value.recording : {}) as RuntimeConfig['recording'];
  const apiUrls = (typeof value.api_urls === 'object' && value.api_urls ? value.api_urls : {}) as Record<string, string>;
  const optsFor = (key: 'stt_options' | 'tts_options' | 'llm_options'): Record<string, unknown> =>
    (typeof value[key] === 'object' && value[key] ? value[key] : {}) as Record<string, unknown>;

  // Soft numeric range check: returns a warning string when `n` violates min/max, else null.
  // We warn rather than block so an operator can still push an edge value if they mean to.
  const numWarn = (n: number, opts: { min?: number; max?: number; integer?: boolean } = {}): string | null => {
    if (!Number.isFinite(n)) return 'Enter a number.';
    if (opts.integer && !Number.isInteger(n)) return 'Must be a whole number.';
    if (opts.min !== undefined && n < opts.min) return `Must be at least ${opts.min}.`;
    if (opts.max !== undefined && n > opts.max) return `Must be at most ${opts.max}.`;
    return null;
  };
  const Warn = ({ msg }: { msg: string | null }) =>
    msg ? <small role="alert" style={{ color: 'var(--warning, #b45309)' }}>{msg}</small> : null;

  // Workflow bots run entirely through workflow_engine.py's run_workflow_call, which reads
  // only a handful of BotConfig fields (workflow, global_prompt, temperature,
  // max_call_duration, post_speech_hold_ms, tts_provider/tts_voice, interruption_sensitivity).
  // Every other field a PM could edit elsewhere in this component is silently ignored for
  // this bot type — see run_workflow_call's own bot_config.get(...) calls. INERT_TABS marks
  // tabs where *nothing* on the tab applies; the fields below flag individual controls on
  // tabs that are a mix of applicable and inert.
  const isWorkflow = value.bot_type === 'workflow';
  const INERT_TABS: BuilderTab[] = ['stt', 'functions'];

  const InertNotice = ({ children }: { children: React.ReactNode }) => (
    <div className="notice" style={{ marginBottom: '0.75rem', display: 'flex', alignItems: 'flex-start', gap: '0.5rem' }}>
      <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: '0.1rem' }} />
      <span>{children}</span>
    </div>
  );
  const inertFieldStyle: React.CSSProperties = { opacity: 0.55 };

  return (
    <div className="bot-config-tabs">
      <div className="cf-tabbar" role="tablist" style={{ display: 'flex', gap: '0.25rem', borderBottom: '1px solid var(--border)', marginBottom: '1rem', flexWrap: 'wrap' }}>
        {TABS.map((t) => {
          const inert = isWorkflow && INERT_TABS.includes(t.id);
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              className={tab === t.id ? 'cf-tab active' : 'cf-tab'}
              onClick={() => setTab(t.id)}
              title={inert ? `${t.label} settings are ignored for workflow bots` : undefined}
              style={{
                padding: '0.45rem 0.85rem', border: 'none', background: 'none', cursor: 'pointer',
                fontWeight: tab === t.id ? 700 : 500,
                borderBottom: tab === t.id ? '2px solid var(--primary, #2563eb)' : '2px solid transparent',
                color: tab === t.id ? 'var(--primary, #2563eb)' : 'var(--muted)',
                opacity: inert ? 0.55 : 1,
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === 'metrics' && <MetricsTab botId={botId} />}

      {tab === 'agent' && (
        <div role="tabpanel">
          <div className="form-grid">
            <label title="'Workflow' bots are a real state-machine graph (see the Workflow tab) driven entirely by workflow_engine.py, bypassing System prompt/Opening line/Closing line below. 'Standard' is the existing fixed-assistant pipeline.">
              Bot type
              <select
                value={String(value.bot_type || 'standard')}
                onChange={(e) => onUpdateConfig('bot_type', e.target.value)}
              >
                <option value="standard">Standard (system prompt)</option>
                <option value="workflow">Workflow (visual graph)</option>
              </select>
              <small>Workflow bots use the Workflow tab's graph instead of the fields below.</small>
            </label>
            <label title="The name the bot introduces itself with. Used in the opening line and system prompt." style={isWorkflow ? inertFieldStyle : undefined}>
              Persona name
              <input value={String(value.agent_name || '')} placeholder="e.g. Tarun, Priya, Aman" onChange={(e) => onUpdateConfig('agent_name', e.target.value)} />
              <small>The name the bot introduces itself with — appears in the opening line and system prompt.</small>
            </label>
          </div>
          <div title="Free-form labels for organizing/filtering agents on the Agents page — has no effect on call behavior.">
            <ChipListEditor
              label="Tags"
              helpText="Used to filter/group the Agents page — no effect on the bot itself. Press Enter to add a tag."
              placeholder="e.g. Hindi, Justdial, Qualification"
              emptyText="No tags yet"
              items={Array.isArray(value.tags) ? (value.tags as string[]) : []}
              onChange={(v) => onUpdateConfig('tags', v)}
            />
          </div>
          {isWorkflow && (
            <InertNotice>
              <strong>System prompt, Opening/Closing line, Inactivity phrase, Language, and Persona name</strong> below
              are ignored for workflow bots — set the equivalent shared context on the <strong>Workflow</strong> tab's
              Global prompt, and the greeting on the graph's Start node. Only <strong>Max call duration</strong> here
              still applies.
            </InertNotice>
          )}
          <label className="full" style={isWorkflow ? inertFieldStyle : undefined}>
            System prompt
            <div className="prompt-editor-wrap">
              <textarea className="prompt-editor prompt-editor-main" value={String(value.system_prompt || '')} onChange={(e) => onUpdateConfig('system_prompt', e.target.value)} />
              {onGeneratePrompt && (
                <PromptAssistButton
                  target="system_prompt"
                  currentText={String(value.system_prompt || '')}
                  onGenerate={onGeneratePrompt}
                  onApply={(text) => onUpdateConfig('system_prompt', text)}
                  generateLabel="Generate prompt"
                  refineLabel="Refine prompt"
                  generatePlaceholder='Describe the bot you want, e.g. "a friendly agent that qualifies real estate leads"'
                  refinePlaceholder='Describe the change to make, e.g. "make the tone more casual"'
                />
              )}
            </div>
          </label>
          <label className="full">
            Analysis prompt override (optional)
            <div className="prompt-editor-wrap">
              <textarea
                className="prompt-editor"
                placeholder="Leave blank to use the global post-call analysis prompt (Settings → Library)."
                value={String(value.analysis_prompt || '')}
                onChange={(e) => onUpdateConfig('analysis_prompt', e.target.value)}
              />
              {onGeneratePrompt && (
                <PromptAssistButton
                  target="analysis_prompt"
                  currentText={String(value.analysis_prompt || '')}
                  onGenerate={onGeneratePrompt}
                  onApply={(text) => onUpdateConfig('analysis_prompt', text)}
                  generateLabel="Refine analysis prompt"
                  refineLabel="Refine analysis prompt"
                  generatePlaceholder='Describe the rule to add or change, e.g. "add a disposition for callback requests"'
                  refinePlaceholder='Describe the rule to add or change, e.g. "add a disposition for callback requests"'
                />
              )}
            </div>
            <small>
              Runs once after each call ends, to classify the outcome from the saved transcript — unrelated to
              the system prompt above, which only drives the live conversation. Applies to both standard and
              workflow bots. Leave blank to use the shared default (AI-assist edits a copy of that default when
              this field is empty, so the required placeholders/JSON schema are always preserved — enforced on
              save either way).
            </small>
          </label>
          <div className="form-grid">
            <label title="The first thing the bot says when the call connects." style={isWorkflow ? inertFieldStyle : undefined}>
              Opening line
              <input value={String(value.initial_message || '')} placeholder="e.g. Hello, this is Priya from JustDial…" onChange={(e) => onUpdateConfig('initial_message', e.target.value)} />
              <small>The first sentence spoken when the call connects.</small>
            </label>
            <label title="The bot says this right before hanging up normally." style={isWorkflow ? inertFieldStyle : undefined}>
              Closing line
              <div className="prompt-editor-wrap prompt-editor-wrap-inline">
                <input value={String(value.call_end_text || '')} placeholder="e.g. Thank you, have a great day!" onChange={(e) => onUpdateConfig('call_end_text', e.target.value)} />
                {onGeneratePrompt && (
                  <PromptAssistButton
                    target="closing_line"
                    currentText={String(value.call_end_text || '')}
                    onGenerate={onGeneratePrompt}
                    onApply={(text) => onUpdateConfig('call_end_text', text)}
                    generateLabel="Generate closing line"
                    refineLabel="Refine closing line"
                    generatePlaceholder='Describe the tone, e.g. "warm and brief, in Hinglish"'
                    refinePlaceholder="Describe the change, e.g. &quot;mention we'll call back within 24 hours&quot;"
                  />
                )}
              </div>
              <small>Spoken just before the bot ends the call normally.</small>
            </label>
            <label style={isWorkflow ? inertFieldStyle : undefined}>
              Inactivity end phrase
              <input value={String(value.inactivity_end_text || '')} placeholder="Platform default used if blank" onChange={(e) => onUpdateConfig('inactivity_end_text', e.target.value)} />
            </label>
            <label style={isWorkflow ? inertFieldStyle : undefined}>
              Language
              <select value={String(value.language || '')} onChange={(e) => onUpdateLanguage(e.target.value)}>
                {languages.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
              </select>
              <small>Not yet wired into the runtime — bot_pipeline.py's Sarvam STT/TTS always runs in Hindi (hi-IN) regardless of this setting.</small>
            </label>
            <label>
              Max call duration: {Number(value.max_call_duration || 300)}s ({Math.round(Number(value.max_call_duration || 300) / 60)} min)
              <input type="range" min={60} max={600} step={30} value={Number(value.max_call_duration || 300)} onChange={(e) => onUpdateConfig('max_call_duration', Number(e.target.value))} />
            </label>
          </div>
        </div>
      )}

      {tab === 'speed' && (
        <div role="tabpanel">
          {isWorkflow && (
            <InertNotice>
              Only <strong>Post-speech hold</strong> below applies to workflow bots. Voice-activity threshold,
              inactivity timers, backchanneling, and noise filter are ignored — workflow_engine.py's session
              doesn't read them.
            </InertNotice>
          )}
          <div className="form-grid">
            <label title="Silence (ms) the bot waits after the caller stops before replying. Lower feels snappier but risks cutting the caller off.">
              Post-speech hold (ms)
              <input type="number" min={0} max={5000} step={50} value={Number(value.post_speech_hold_ms ?? 400)} onChange={(e) => onUpdateConfig('post_speech_hold_ms', Number(e.target.value))} />
              <small>Pause after the caller stops speaking before the bot responds. Typical 200–800 ms.</small>
              <Warn msg={numWarn(Number(value.post_speech_hold_ms ?? 400), { min: 0, max: 5000, integer: true })} />
            </label>
            <label title="Speech-detection sensitivity (0–1). Higher = stricter, ignores more background noise but may miss soft speech." style={isWorkflow ? inertFieldStyle : undefined}>
              Voice-activity threshold
              <input type="number" min="0" max="1" step="0.05" value={Number(value.silero_threshold ?? 0.6)} onChange={(e) => onUpdateConfig('silero_threshold', Number(e.target.value))} />
              <small>Sensitivity for real speech vs. background noise (0–1). Default 0.6.</small>
              <Warn msg={numWarn(Number(value.silero_threshold ?? 0.6), { min: 0, max: 1 })} />
            </label>
            <label title="Minimum length (ms) of sound before it counts as speech. Filters out coughs and clicks." style={isWorkflow ? inertFieldStyle : undefined}>
              Min speech duration (ms)
              <input type="number" min={0} max={10000} step={50} value={Number(value.silero_min_speech_ms ?? 1000)} onChange={(e) => onUpdateConfig('silero_min_speech_ms', Number(e.target.value))} />
              <small>Shortest utterance treated as real speech. Raise to ignore brief noises.</small>
              <Warn msg={numWarn(Number(value.silero_min_speech_ms ?? 1000), { min: 0, max: 10000, integer: true })} />
            </label>
            <label title="Seconds of silence at the start of a turn before the bot gently re-engages the caller." style={isWorkflow ? inertFieldStyle : undefined}>
              First rescue (s)
              <input type="number" min={0.5} max={60} step="0.5" value={Number(value.inactivity_first_rescue_secs ?? 4)} onChange={(e) => onUpdateConfig('inactivity_first_rescue_secs', Number(e.target.value))} />
              <small>Silence before the first re-engagement prompt.</small>
              <Warn msg={numWarn(Number(value.inactivity_first_rescue_secs ?? 4), { min: 0.5, max: 60 })} />
            </label>
            <label title="Seconds to wait after the first rescue before starting the repeating nudge cycle." style={isWorkflow ? inertFieldStyle : undefined}>
              First nudge gap (s)
              <input type="number" min={0.5} max={60} step="0.5" value={Number(value.inactivity_first_nudge_gap_secs ?? 4)} onChange={(e) => onUpdateConfig('inactivity_first_nudge_gap_secs', Number(e.target.value))} />
              <small>Delay between the first rescue and the recurring nudges.</small>
              <Warn msg={numWarn(Number(value.inactivity_first_nudge_gap_secs ?? 4), { min: 0.5, max: 60 })} />
            </label>
            <label title="Seconds between each repeating nudge while the caller stays silent." style={isWorkflow ? inertFieldStyle : undefined}>
              Nudge interval (s)
              <input type="number" min={0.5} max={120} step="0.5" value={Number(value.inactivity_nudge_secs ?? 10)} onChange={(e) => onUpdateConfig('inactivity_nudge_secs', Number(e.target.value))} />
              <small>Gap between repeated “are you still there?” prompts.</small>
              <Warn msg={numWarn(Number(value.inactivity_nudge_secs ?? 10), { min: 0.5, max: 120 })} />
            </label>
            <label title="Seconds of continued silence after the nudges before the bot ends the call." style={isWorkflow ? inertFieldStyle : undefined}>
              Auto-close (s)
              <input type="number" min={0.5} max={120} step="0.5" value={Number(value.inactivity_close_secs ?? 5)} onChange={(e) => onUpdateConfig('inactivity_close_secs', Number(e.target.value))} />
              <small>Final silence window before the call is hung up.</small>
              <Warn msg={numWarn(Number(value.inactivity_close_secs ?? 5), { min: 0.5, max: 120 })} />
            </label>
            <label style={isWorkflow ? inertFieldStyle : undefined}>
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <input type="checkbox" checked={Boolean(value.backchanneling_enabled)} onChange={(e) => onUpdateConfig('backchanneling_enabled', e.target.checked)} />
                Backchanneling
              </span>
              <small>Plays a short hold/acknowledgment sound during tool calls.</small>
            </label>
            <label style={isWorkflow ? inertFieldStyle : undefined}>
              Noise filter sensitivity
              <select value={String(value.noise_filter_sensitivity || 'medium')} onChange={(e) => onUpdateConfig('noise_filter_sensitivity', e.target.value)}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </label>
          </div>
        </div>
      )}

      {(tab === 'stt' || tab === 'tts' || tab === 'llm') && costEstimate && (
        <CostEstimateStrip estimate={costEstimate} />
      )}

      {tab === 'stt' && (
        <div role="tabpanel">
          {isWorkflow && (
            <InertNotice>
              STT is ignored for workflow bots — workflow_engine.py hardcodes Sarvam <code>saaras:v3</code>,
              Hindi (hi-IN), regardless of anything set below.
            </InertNotice>
          )}
          <div className="form-grid" style={isWorkflow ? inertFieldStyle : undefined}>
            <label>
              Speech-to-text (STT)
              <select value={String(value.stt_provider || '')} onChange={(e) => onUpdateConfig('stt_provider', e.target.value)}>
                <option value="">Sarvam (default)</option>
                <option value="sarvam">Sarvam</option>
                <option value="deepgram">Deepgram</option>
              </select>
            </label>
            {value.stt_provider === 'deepgram' && (
              <>
                <label>
                  STT model
                  <select value={String(value.stt_model || '')} onChange={(e) => onUpdateConfig('stt_model', e.target.value)}>
                    <option value="">nova-3 (default)</option>
                    {deepgramSttModels.map((e) => <option key={e.key} value={e.label}>{e.label}</option>)}
                  </select>
                </label>
                <label>
                  STT language
                  <input value={String(value.stt_language || '')} placeholder="en-US (default)" onChange={(e) => onUpdateConfig('stt_language', e.target.value)} />
                </label>
              </>
            )}
            {(value.stt_provider === 'sarvam' || !value.stt_provider) && (
              <div style={{ gridColumn: '1 / -1' }}>
                <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.75rem 0 0.25rem' }}>
                  Sarvam STT parameters
                </div>
                <ProviderOptionsEditor fields={SARVAM_STT_FIELDS} value={optsFor('stt_options')} onChange={(next) => onUpdateConfig('stt_options', next)} />
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'tts' && (
        <div role="tabpanel" className="form-grid">
          <label>
            Text-to-speech (TTS)
            <select
              value={String(value.tts_provider || '')}
              onChange={(e) => {
                const provider = e.target.value;
                onUpdateConfig('tts_provider', provider);
                // Keep tts_model in sync with the provider so the cost estimate (agentCost.ts,
                // keyed off tts_model against the Admin pricing catalog) matches the TTS
                // actually running the call instead of silently falling back to Sarvam's rate.
                onUpdateConfig('tts_model', TTS_PROVIDER_MODEL_KEY[provider] ?? '');
              }}
            >
              <option value="">Sarvam (default)</option>
              <option value="sarvam">Sarvam</option>
              <option value="elevenlabs">ElevenLabs</option>
              <option value="justdial">Justdial (in-house IndicF5)</option>
            </select>
          </label>
          {(value.tts_provider === 'sarvam' || !value.tts_provider) && (
            <>
              <label>
                Sarvam voice
                <select value={String(value.tts_voice || '')} onChange={(e) => onUpdateConfig('tts_voice', e.target.value)}>
                  <option value="">simran (default)</option>
                  {SARVAM_TTS_VOICES.map((voice) => <option key={voice} value={voice}>{voice}</option>)}
                </select>
              </label>
              <label>
                Sarvam language
                <select value={String(value.tts_language || '')} onChange={(e) => onUpdateConfig('tts_language', e.target.value)}>
                  <option value="">Hindi (default)</option>
                  {SARVAM_TTS_LANGUAGES.map((lang) => <option key={lang.id} value={lang.id}>{lang.label}</option>)}
                </select>
              </label>
            </>
          )}
          {value.tts_provider === 'elevenlabs' && (
            <label>
              ElevenLabs voice ID
              <input value={String(value.tts_voice || '')} placeholder="Paste a voice ID from your ElevenLabs dashboard" onChange={(e) => onUpdateConfig('tts_voice', e.target.value)} />
              <small>Find voice IDs at elevenlabs.io under Voices.</small>
            </label>
          )}
          {value.tts_provider === 'justdial' && (
            <label>
              IndicF5 speaker
              <select value={String(value.tts_voice || 'simran')} onChange={(e) => onUpdateConfig('tts_voice', e.target.value)}>
                <option value="simran">Simran (default)</option>
                <option value="anushka">Anushka</option>
                <option value="niharika">Niharika</option>
              </select>
              <small>Our own fine-tuned Hindi TTS — hits an internal WebSocket server (INDIC_TTS_WS_URL), not a third-party API. These are the only 3 speakers our IndicF5 model is fine-tuned on.</small>
            </label>
          )}
          {(value.tts_provider === 'sarvam' || !value.tts_provider) && (
            <div style={{ gridColumn: '1 / -1' }}>
              <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.75rem 0 0.25rem' }}>
                Sarvam TTS parameters
              </div>
              <ProviderOptionsEditor fields={SARVAM_TTS_FIELDS} value={optsFor('tts_options')} onChange={(next) => onUpdateConfig('tts_options', next)} />
            </div>
          )}
        </div>
      )}

      {tab === 'llm' && (
        <div role="tabpanel">
          {isWorkflow && (
            <InertNotice>
              Only <strong>Temperature</strong> below applies to workflow bots. LLM provider/model selection and
              the Gemini parameters are ignored — workflow_engine.py hardcodes <code>gemini-3.1-flash-lite</code>.
            </InertNotice>
          )}
          <div className="form-grid">
            <label style={isWorkflow ? inertFieldStyle : undefined}>
              LLM
              <select value={String(value.llm_provider || '')} onChange={(e) => onUpdateConfig('llm_provider', e.target.value)}>
                <option value="">Gemini (default)</option>
                <option value="gemini">Gemini</option>
                <option value="openai">OpenAI</option>
              </select>
            </label>
            {value.llm_provider === 'openai' && (
              <label title="Which OpenAI model handles the conversation. Affects both response quality/speed and cost per minute." style={isWorkflow ? inertFieldStyle : undefined}>
                OpenAI model
                <select value={String(value.llm_model || '')} onChange={(e) => onUpdateConfig('llm_model', e.target.value)}>
                  <option value="">gpt-4.1 (default)</option>
                  {openAiModels.map((e) => <option key={e.key} value={e.label}>{e.label}</option>)}
                </select>
              </label>
            )}
            <label title="LLM randomness (0–2). Lower = more consistent and on-script; higher = more varied and creative.">
              Temperature
              <input type="number" min="0" max="2" step="0.1" value={Number(value.temperature ?? 0.4)} onChange={(e) => onUpdateConfig('temperature', Number(e.target.value))} />
              <small>Sampling randomness (0–2). Default 0.4 — keep low for predictable scripted calls.</small>
              <Warn msg={numWarn(Number(value.temperature ?? 0.4), { min: 0, max: 2 })} />
            </label>
            {(value.llm_provider === 'gemini' || !value.llm_provider) && (
              <div style={{ gridColumn: '1 / -1', ...(isWorkflow ? inertFieldStyle : {}) }}>
                <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.75rem 0 0.25rem' }}>
                  Gemini parameters
                </div>
                <ProviderOptionsEditor fields={geminiLlmFields} value={optsFor('llm_options')} onChange={(next) => onUpdateConfig('llm_options', next)} />
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'functions' && (
        <div role="tabpanel">
          {isWorkflow && (
            <InertNotice>
              Custom functions here aren't wired into workflow bots — they're never called on a workflow call.
              Use a <strong>Function</strong>-kind node in the <strong>Workflow</strong> tab's graph instead to hit
              an external API mid-call.
            </InertNotice>
          )}
          <div style={isWorkflow ? inertFieldStyle : undefined}>
            <CustomFunctionsEditor
              functions={Array.isArray(value.functions) ? (value.functions as CustomFunction[]) : []}
              onChange={(next) => {
                onUpdateConfig('functions', next);
                onUpdateConfig('function_calling', next.some((fn) => fn.trigger === 'during_call'));
              }}
              onTest={botId ? onTestFunction : undefined}
            />
          </div>
        </div>
      )}

      {tab === 'workflow' && (
        <div role="tabpanel">
          {value.bot_type !== 'workflow' && (
            <div className="notice" style={{ marginBottom: '0.75rem' }}>
              This bot's type is "Standard" (Agent tab) — the graph below is saved but
              ignored at call time until you switch Bot type to "Workflow".
            </div>
          )}
          <label className="full">
            Global prompt
            <div className="prompt-editor-wrap">
              <textarea
                className="prompt-editor"
                value={String(value.global_prompt || '')}
                onChange={(e) => onUpdateConfig('global_prompt', e.target.value)}
                placeholder="Shared persona/context prepended to every conversation node's instructions."
              />
              {onGeneratePrompt && (
                <PromptAssistButton
                  target="global_prompt"
                  currentText={String(value.global_prompt || '')}
                  onGenerate={onGeneratePrompt}
                  onApply={(text) => onUpdateConfig('global_prompt', text)}
                  generateLabel="Generate global prompt"
                  refineLabel="Refine global prompt"
                  generatePlaceholder='Describe the shared persona/context, e.g. "a friendly scheduling assistant for a dental clinic"'
                  refinePlaceholder='Describe the change, e.g. "mention we are open on weekends too"'
                />
              )}
            </div>
            <small>Prepended to every node's compiled instructions — each node is otherwise its own independent agent.</small>
          </label>
          <div style={{ marginTop: '1rem' }}>
            <WorkflowBuilderView
              workflow={value.workflow || { nodes: [], edges: [] }}
              onChange={(wf) => onUpdateConfig('workflow', wf)}
            />
          </div>

          <div style={{ marginTop: '1rem' }}>
            <button
              type="button"
              onClick={() => setWorkflowJsonOpen((v) => !v)}
              style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8rem' }}
            >
              <Database size={14} /> {workflowJsonOpen ? 'Hide' : 'Show'} raw graph JSON (advanced)
            </button>
            {workflowJsonOpen && (
              <div style={{ marginTop: '0.6rem' }}>
                <p style={{ fontSize: '0.78rem', color: 'var(--muted)', margin: '0 0 0.4rem' }}>
                  Same graph as the canvas above, as raw nodes/edges JSON consumed by
                  workflow_engine.py — see backend/models.py's WorkflowGraphDef for the exact
                  shape. Editing here updates the canvas immediately; the two stay in sync.
                </p>
                <textarea
                  className="json-editor"
                  value={workflowText}
                  onChange={(e) => handleWorkflowTextChange(e.target.value)}
                  spellCheck={false}
                  aria-invalid={!!workflowJsonError}
                />
                {workflowJsonError && (
                  <div className="notice error" role="alert" style={{ marginTop: '0.5rem' }}>
                    <AlertTriangle size={16} /> Invalid JSON — fix before saving: {workflowJsonError}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'advanced' && (
        <div role="tabpanel">
          {isWorkflow && (
            <InertNotice>
              Dialer service ID/city, MIS API base URL, and Close markers below are read by the standard pipeline
              only — workflow bots don't use them (a workflow bot's own <strong>Function</strong> nodes call
              whatever endpoints they're configured with directly). <strong>Developer JSON</strong> still works.
            </InertNotice>
          )}
          <div className="form-grid" style={isWorkflow ? inertFieldStyle : undefined}>
            <label title="Numeric dialer/recording service ID this bot's calls are attributed to. Change only if telephony ops tells you to.">
              Dialer service ID
              <input type="number" min={0} step={1} value={Number(recording?.service_id || 293)} onChange={(e) => onUpdateConfig('recording', { ...recording, service_id: Number(e.target.value) })} />
              <small>Recording/telephony service this bot dials through. Default 293.</small>
              <Warn msg={numWarn(Number(recording?.service_id || 293), { min: 0, integer: true })} />
            </label>
            <label title="City the dialer routes this bot's outbound calls from.">
              Dialer city
              <input value={String(recording?.dialer_city || 'bangalore')} placeholder="e.g. bangalore" onChange={(e) => onUpdateConfig('recording', { ...recording, dialer_city: e.target.value })} />
              <small>City the dialer places calls from.</small>
            </label>
            <label>
              MIS API base URL
              <input value={String(apiUrls?.mis_api_base || '')} placeholder="Leave blank to use platform default" onChange={(e) => onUpdateConfig('api_urls', { ...apiUrls, mis_api_base: e.target.value })} />
              <small>Per-bot override for MIS lead fetch endpoint.</small>
            </label>
          </div>
          <div style={isWorkflow ? inertFieldStyle : undefined}>
            <CloseMarkersEditor
              markers={Array.isArray(value.close_markers) ? (value.close_markers as string[]) : []}
              onChange={(v) => onUpdateConfig('close_markers', v)}
            />
          </div>
          <div style={{ marginTop: '1rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' }}>
              <Database size={16} />
              <strong style={{ fontSize: '0.9rem' }}>Developer JSON</strong>
            </div>
            <p style={{ fontSize: '0.78rem', color: 'var(--muted)', margin: '0 0 0.4rem' }}>Full runtime config. Edits here override the fields above.</p>
            <textarea className="json-editor" value={configText} onChange={(e) => onConfigTextChange(e.target.value)} spellCheck={false} aria-invalid={!configOk} />
            {!configOk && (
              <div className="notice error" role="alert" style={{ marginTop: '0.5rem' }}>
                <AlertTriangle size={16} /> Invalid JSON — fix before saving: {configError}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
