import React, { useState, useEffect, useRef } from 'react';
import {
  AlertTriangle, ChevronRight, Database, GitBranch, Pencil, Plus,
  Rocket, Save, ShieldCheck, SlidersHorizontal, Wand2
} from 'lucide-react';
import type { Bot as BotType, BotVersion, LanguageOption } from '../api';
import type { RuntimeConfig, BuilderMode } from '../types';
import { defaultConfig, SARVAM_TTS_VOICES, SARVAM_TTS_LANGUAGES } from '../constants/ui';
import { StatusPill } from '../components/StatusPill';
import { TimeAgo } from '../components/TimeAgo';
import { Detail } from '../components/Detail';

export function CloseMarkersEditor({ markers, onChange }: { markers: string[]; onChange: (value: string[]) => void }) {
  const [input, setInput] = useState('');
  function add() {
    const trimmed = input.trim();
    if (trimmed && !markers.includes(trimmed)) onChange([...markers, trimmed]);
    setInput('');
  }
  return (
    <label className="full">
      Call-end close phrases
      <small>Bot ends the call when it detects any of these phrases. Override the hardcoded Hindi defaults for non-Hindi bots.</small>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.4rem', marginBottom: '0.4rem', minHeight: '2rem' }}>
        {markers.map(m => (
          <span key={m} style={{ background: 'var(--surface-2)', borderRadius: '4px', padding: '2px 8px', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
            {m}
            <button style={{ padding: 0, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)', lineHeight: 1 }} onClick={() => onChange(markers.filter(x => x !== m))}>×</button>
          </span>
        ))}
        {markers.length === 0 && <span style={{ fontSize: '0.78rem', color: 'var(--muted)', fontStyle: 'italic' }}>Using hardcoded defaults (Hindi)</span>}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="e.g. thank you, goodbye, dhanyavaad"
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          style={{ flex: 1 }}
        />
        <button onClick={add} style={{ whiteSpace: 'nowrap' }}><Plus size={14} /> Add</button>
      </div>
    </label>
  );
}

export function PromptPreview({ version, srchterm }: { version: BotVersion; srchterm: string }) {
  const cfg = version.config as Record<string, string | undefined>;
  const rawOpening = cfg.initial_message || '(no opening line set)';
  const opening = rawOpening
    .replace('{product}', srchterm || '<product>')
    .replace('{agent_name}', cfg.agent_name || '<agent_name>')
    .replace('{organization_name}', cfg.organization_name || '<org_name>');
  const systemPrompt = cfg.system_prompt || '(no system_prompt set in this version)';
  const agentName = cfg.agent_name;
  const orgName = cfg.organization_name;
  const aiPartner = cfg.ai_partner;

  const warnings: string[] = [];
  if (!agentName) warnings.push('agent_name not set — bot may use hardcoded persona');
  if (!orgName) warnings.push('organization_name not set');
  if (!cfg.initial_message) warnings.push('initial_message not set — bot will use fallback');

  const configSource = version.state === 'published'
    ? `embedded_test_config:${version._id.slice(-6)}`
    : `draft:v${version.version}`;

  return (
    <details className="prompt-preview" style={{ marginTop: '1rem' }}>
      <summary style={{ cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem', padding: '0.5rem 0', userSelect: 'none' }}>
        Prompt preview — v{version.version} ({version.state})
      </summary>
      <div style={{ marginTop: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <code style={{ fontSize: '0.72rem', background: 'var(--surface-2)', padding: '2px 8px', borderRadius: '4px', color: 'var(--muted)' }}>
            config: {configSource}
          </code>
          {aiPartner && <code style={{ fontSize: '0.72rem', background: 'var(--surface-2)', padding: '2px 8px', borderRadius: '4px', color: 'var(--muted)' }}>ai_partner: {aiPartner}</code>}
        </div>
        {warnings.length > 0 && (
          <div style={{ background: 'var(--warning-bg)', border: '1px solid var(--warning-border)', borderRadius: '6px', padding: '0.5rem 0.75rem' }}>
            {warnings.map((w) => <div key={w} style={{ fontSize: '0.78rem', color: 'var(--warning)' }}>⚠ {w}</div>)}
          </div>
        )}
        <div>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.25rem' }}>Persona</div>
          <code style={{ fontSize: '0.8rem' }}>{agentName || '(not set)'} · {orgName || '(org not set)'}</code>
        </div>
        <div>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.25rem' }}>Pipeline</div>
          <code style={{ fontSize: '0.8rem' }}>Sarvam STT (saaras:v3) → gemini-3.1-flash-lite → Sarvam TTS (bulbul:v3, simran)</code>
        </div>
        <div>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.25rem' }}>Opening line</div>
          <div style={{ background: 'var(--surface-2)', borderRadius: '6px', padding: '0.5rem 0.75rem', fontSize: '0.85rem', fontStyle: 'italic' }}>
            "{opening}"
          </div>
        </div>
        <div>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.25rem' }}>System prompt</div>
          <pre style={{ background: 'var(--surface-2)', borderRadius: '6px', padding: '0.75rem', fontSize: '0.78rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '300px', overflowY: 'auto', margin: 0 }}>{systemPrompt}</pre>
        </div>
      </div>
    </details>
  );
}

export function BuilderView({
  selectedBot,
  versions,
  activeVersion,
  latestDraft,
  publishedCount,
  config,
  configText,
  languages,
  onConfigTextChange,
  onUpdateConfig,
  onUpdateLanguage,
  onSaveDraft,
  onPublish,
  onUnpublish,
  onRename,
  onBack,
  saveState,
  publishState,
  unpublishState,
  renameState,
  editingVersionId,
  onSelectVersion,
  onUpdateVersion,
  updateVersionState,
  onRollback,
  rollbackState,
  onShowDiff,
  liveCallsByVersion,
  builderMode,
  onToggleMode
}: {
  selectedBot?: BotType;
  versions: BotVersion[];
  activeVersion?: BotVersion;
  latestDraft?: BotVersion;
  publishedCount: number;
  config: { ok: true; value: RuntimeConfig } | { ok: false; error: string };
  configText: string;
  languages: LanguageOption[];
  onConfigTextChange: (value: string) => void;
  onUpdateConfig: (key: keyof RuntimeConfig, value: unknown) => void;
  onUpdateLanguage: (value: string) => void;
  onSaveDraft: () => void;
  onPublish: () => void;
  onUnpublish?: () => void;
  onRename?: (name: string, description: string) => void;
  onBack?: () => void;
  saveState?: 'idle' | 'running' | 'failed';
  publishState?: 'idle' | 'running' | 'failed';
  unpublishState?: 'idle' | 'running' | 'failed';
  renameState?: 'idle' | 'running' | 'failed';
  editingVersionId?: string;
  onSelectVersion?: (version: BotVersion) => void;
  onUpdateVersion?: (versionId: string) => void;
  updateVersionState?: 'idle' | 'running' | 'failed';
  onRollback?: (versionId: string) => void;
  rollbackState?: Record<string, 'idle' | 'running' | 'failed'>;
  onShowDiff?: (versionIdA: string, versionIdB: string) => void;
  liveCallsByVersion?: Record<string, number>;
  builderMode?: BuilderMode;
  onToggleMode?: (mode: BuilderMode) => void;
}) {
  const value = config.ok ? config.value : defaultConfig;
  const isAdvanced = builderMode === 'advanced';

  const [draftName, setDraftName] = useState(selectedBot?.name || '');
  const [draftDescription, setDraftDescription] = useState(selectedBot?.description || '');

  useEffect(() => {
    setDraftName(selectedBot?.name || '');
    setDraftDescription(selectedBot?.description || '');
  }, [selectedBot?._id]);

  const nameChanged = draftName.trim() !== (selectedBot?.name || '').trim()
    || draftDescription.trim() !== (selectedBot?.description || '').trim();

  // ── Dirty tracking: detect unsaved config changes ──────────────────
  const [baseConfigText, setBaseConfigText] = useState(configText);
  const prevSaveState = useRef(saveState);
  const prevUpdateState = useRef(updateVersionState);
  // Capture config at the moment a save starts so we can reset base to
  // exactly what was saved, not what the user may have typed since.
  const configAtSaveStartRef = useRef(configText);

  // Reset base when a new version is selected (editingVersionId changes)
  useEffect(() => { setBaseConfigText(configText); }, [editingVersionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Track config at save start, reset base on successful completion
  useEffect(() => {
    if (saveState === 'running' && prevSaveState.current !== 'running') {
      configAtSaveStartRef.current = configText;
    }
    if (prevSaveState.current === 'running' && saveState === 'idle') {
      setBaseConfigText(configAtSaveStartRef.current);
    }
    prevSaveState.current = saveState;
  }, [saveState]); // intentionally omit configText — we capture it via ref at save-start

  useEffect(() => {
    if (updateVersionState === 'running' && prevUpdateState.current !== 'running') {
      configAtSaveStartRef.current = configText;
    }
    if (prevUpdateState.current === 'running' && updateVersionState === 'idle') {
      setBaseConfigText(configAtSaveStartRef.current);
    }
    prevUpdateState.current = updateVersionState;
  }, [updateVersionState]); // intentionally omit configText — captured via ref

  const isDirtyConfig = configText !== baseConfigText;
  const isDirty = isDirtyConfig || nameChanged;

  // ── localStorage draft recovery ────────────────────────────────────
  const lsKey = selectedBot?._id ? `draft-autosave-${selectedBot._id}` : null;

  // Detect a saved draft when the bot or version changes
  const [recoveryDraft, setRecoveryDraft] = useState<string | null>(null);
  useEffect(() => {
    if (!lsKey) return;
    const saved = localStorage.getItem(lsKey);
    // Only offer recovery if the saved draft differs from what's already loaded
    if (saved && saved !== configText) {
      setRecoveryDraft(saved);
    } else {
      setRecoveryDraft(null);
    }
  }, [lsKey, editingVersionId]); // eslint-disable-line react-hooks/exhaustive-deps

  function applyRecoveryDraft() {
    if (!recoveryDraft) return;
    onConfigTextChange(recoveryDraft);
    setRecoveryDraft(null);
  }

  function discardRecoveryDraft() {
    if (lsKey) localStorage.removeItem(lsKey);
    setRecoveryDraft(null);
  }

  // Auto-save to localStorage (debounced 2s)
  useEffect(() => {
    if (!lsKey || !isDirtyConfig) return;
    const id = window.setTimeout(() => {
      localStorage.setItem(lsKey, configText);
    }, 2000);
    return () => clearTimeout(id);
  }, [configText, lsKey, isDirtyConfig]);

  // Clear localStorage after a successful save
  useEffect(() => {
    if (prevSaveState.current === 'running' && saveState === 'idle' && lsKey) {
      localStorage.removeItem(lsKey);
    }
  }, [saveState, lsKey]);

  // Warn before tab close when there are unsaved changes
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isDirty]);

  // Cmd/Ctrl+S to save draft
  useEffect(() => {
    function handleSave(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        if (config.ok && saveState !== 'running') onSaveDraft();
      }
    }
    window.addEventListener('keydown', handleSave);
    return () => window.removeEventListener('keydown', handleSave);
  }, [config.ok, saveState, onSaveDraft]);

  const editingVer = versions.find(v => v._id === editingVersionId);
  const isPublishedVer = editingVer?.state === 'published';

  return (
    <section className="builder-layout">
      {/* ── Draft recovery banner ─────────────────────────────── */}
      {recoveryDraft && (
        <div className="draft-recovery-banner">
          <Save size={14} />
          <span>You have an unsaved draft from a previous session.</span>
          <button className="primary" onClick={applyRecoveryDraft}>Restore draft</button>
          <button onClick={discardRecoveryDraft}>Discard</button>
        </div>
      )}
      {/* ── Back nav + action bar ─────────────────────────────── */}
      <div className="builder-action-bar">
        {onBack && (
          <button className="back-link" onClick={onBack}>
            <ChevronRight className="rotate-180" size={15} /> Back to agents
          </button>
        )}
        {onToggleMode && (
          <div className="mode-toggle" role="group" aria-label="Builder mode">
            <button
              className={!isAdvanced ? 'mode-btn active' : 'mode-btn'}
              onClick={() => onToggleMode('pm')}
              title="PM view — core fields only"
            >
              <Pencil size={13} /> PM
            </button>
            <button
              className={isAdvanced ? 'mode-btn active' : 'mode-btn'}
              onClick={() => onToggleMode('advanced')}
              title="Advanced — all config fields including admin settings"
            >
              <SlidersHorizontal size={13} /> Advanced
            </button>
          </div>
        )}
        <div className="builder-action-bar-right">
          {isDirty && (
            <span className="unsaved-indicator" title="You have unsaved changes. Press ⌘S to save a new draft.">
              <span className="unsaved-dot" />
              Unsaved changes
            </span>
          )}
          {!isPublishedVer && editingVersionId && onUpdateVersion && (
            <button
              disabled={!config.ok || updateVersionState === 'running'}
              onClick={() => onUpdateVersion(editingVersionId)}
            >
              <Save size={15} />
              {updateVersionState === 'running' ? 'Saving…' : updateVersionState === 'failed' ? 'Retry' : `Update v${editingVer?.version ?? ''}`}
            </button>
          )}
          {activeVersion && !latestDraft && onUnpublish && (
            <button
              className="fallback-button"
              disabled={unpublishState === 'running'}
              onClick={onUnpublish}
              title="Move the published version back to draft so you can edit it"
            >
              <Pencil size={15} /> {unpublishState === 'running' ? 'Unpublishing…' : 'Edit published'}
            </button>
          )}
          <button disabled={!config.ok || saveState === 'running'} onClick={onSaveDraft}>
            <Plus size={15} /> {saveState === 'failed' ? 'Retry' : saveState === 'running' ? 'Saving…' : 'New version'}
          </button>
          <button
            className={publishState === 'failed' ? 'fallback-button' : 'primary'}
            disabled={publishState === 'running' || (!latestDraft && versions.length > 0)}
            onClick={onPublish}
          >
            <Rocket size={15} /> {publishState === 'failed' ? 'Retry' : publishState === 'running' ? 'Publishing…' : 'Publish'}
          </button>
        </div>
      </div>

      <div className="builder-main">
        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>{draftName || selectedBot?.name || 'Bot Builder'}</h2>
              <p>PMs edit the spoken behavior here. Developers can use the JSON panel for advanced runtime settings.</p>
            </div>
          </div>
          {!config.ok && <div className="notice error"><AlertTriangle size={16} /> JSON is invalid: {config.error}</div>}
          {isPublishedVer && (
            <div className="notice" style={{ background: 'var(--warning-bg)', borderColor: 'var(--warning-border)', color: 'var(--warning)' }}>
              <ShieldCheck size={15} />
              Viewing published v{editingVer?.version} — changes here create a new draft. The live version is unaffected.
            </div>
          )}
          <div className="form-section">
            <div className="form-grid agent-identity">
              <label>
                Agent name
                <input
                  value={draftName}
                  placeholder={selectedBot?.name || 'Agent name'}
                  onChange={(e) => setDraftName(e.target.value)}
                />
              </label>
              <label>
                Description
                <input
                  value={draftDescription}
                  placeholder="Short description (optional)"
                  onChange={(e) => setDraftDescription(e.target.value)}
                />
              </label>
              {onRename && (
                <div className="rename-action">
                  <button
                    className={renameState === 'failed' ? 'fallback-button' : nameChanged ? 'primary' : ''}
                    disabled={!nameChanged || renameState === 'running'}
                    onClick={() => onRename(draftName, draftDescription)}
                  >
                    <Pencil size={14} />
                    {renameState === 'running' ? 'Saving…' : renameState === 'failed' ? 'Retry rename' : 'Save name'}
                  </button>
                </div>
              )}
            </div>

            {/* ── Persona & Identity — always visible ── */}
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem', marginBottom: '0.25rem' }}>
              <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Persona &amp; Identity</div>
              <div className="form-grid">
                <label>
                  Persona name
                  <input value={String(value.agent_name || '')} placeholder="e.g. Tarun, Priya, Aman" onChange={(e) => onUpdateConfig('agent_name', e.target.value)} />
                  <small>Used in opening line and system prompt.</small>
                </label>
                <label>
                  Organization name
                  <input value={String(value.organization_name || '')} placeholder="e.g. JustDial" onChange={(e) => onUpdateConfig('organization_name', e.target.value)} />
                </label>
                {isAdvanced && (
                  <>
                    <label>
                      AI partner key
                      <input value={String(value.ai_partner || '')} placeholder="e.g. inh-suny-bot" onChange={(e) => onUpdateConfig('ai_partner', e.target.value)} />
                      <small>Dialer lead fetch tag. Leave blank to use platform default.</small>
                    </label>
                    <label>
                      Inactivity end phrase
                      <input value={String(value.inactivity_end_text || '')} placeholder="Platform default used if blank" onChange={(e) => onUpdateConfig('inactivity_end_text', e.target.value)} />
                      <small>Spoken after extended silence. Language-specific.</small>
                    </label>
                  </>
                )}
              </div>
            </div>

            {/* ── Core content — always visible ── */}
            <label className="full">
              System prompt
              <textarea className="prompt-editor" value={String(value.system_prompt || '')} onChange={(event) => onUpdateConfig('system_prompt', event.target.value)} />
            </label>
            <div className="form-grid">
              <label>
                Opening line
                <input value={String(value.initial_message || '')} onChange={(event) => onUpdateConfig('initial_message', event.target.value)} />
              </label>
              <label>
                Closing line
                <input value={String(value.call_end_text || '')} onChange={(event) => onUpdateConfig('call_end_text', event.target.value)} />
              </label>
              <label>
                Language
                <select value={String(value.language || '')} onChange={(event) => onUpdateLanguage(event.target.value)}>
                  {languages.map((language) => <option key={language.id} value={language.id}>{language.label}</option>)}
                </select>
                <small>Not yet wired into the runtime — bot_pipeline.py's Sarvam STT/TTS currently always runs in Hindi (hi-IN) regardless of this setting.</small>
              </label>
              <label>
                Max call duration: {Number(value.max_call_duration || 300)}s ({Math.round(Number(value.max_call_duration || 300) / 60)} min)
                <input type="range" min={60} max={600} step={30} value={Number(value.max_call_duration || 300)} onChange={(event) => onUpdateConfig('max_call_duration', Number(event.target.value))} />
                <small>Also update the "X minutes" mention in your system prompt.</small>
              </label>
            </div>

            {/* ── Advanced-only fields ── */}
            {isAdvanced && (
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem', marginTop: '0.5rem' }}>
                <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <SlidersHorizontal size={12} /> Pipeline
                </div>
                <div className="form-grid">
                  <label>
                    Speech-to-text (STT)
                    <select
                      value={String(value.stt_provider || '')}
                      onChange={(event) => onUpdateConfig('stt_provider', event.target.value)}
                    >
                      <option value="">Sarvam (default)</option>
                      <option value="sarvam">Sarvam</option>
                      <option value="deepgram">Deepgram</option>
                    </select>
                  </label>
                  {value.stt_provider === 'deepgram' && (
                    <>
                      <label>
                        STT model
                        <input
                          value={String(value.stt_model || '')}
                          placeholder="nova-3 (default)"
                          onChange={(event) => onUpdateConfig('stt_model', event.target.value)}
                        />
                      </label>
                      <label>
                        STT language
                        <input
                          value={String(value.stt_language || '')}
                          placeholder="en-US (default)"
                          onChange={(event) => onUpdateConfig('stt_language', event.target.value)}
                        />
                      </label>
                    </>
                  )}
                  <label>
                    Text-to-speech (TTS)
                    <select
                      value={String(value.tts_provider || '')}
                      onChange={(event) => onUpdateConfig('tts_provider', event.target.value)}
                    >
                      <option value="">Sarvam (default)</option>
                      <option value="sarvam">Sarvam</option>
                      <option value="elevenlabs">ElevenLabs</option>
                    </select>
                  </label>
                  {(value.tts_provider === 'sarvam' || !value.tts_provider) && (
                    <>
                      <label>
                        Sarvam voice
                        <select
                          value={String(value.tts_voice || '')}
                          onChange={(event) => onUpdateConfig('tts_voice', event.target.value)}
                        >
                          <option value="">simran (default)</option>
                          {SARVAM_TTS_VOICES.map((voice) => <option key={voice} value={voice}>{voice}</option>)}
                        </select>
                      </label>
                      <label>
                        Sarvam language
                        <select
                          value={String(value.tts_language || '')}
                          onChange={(event) => onUpdateConfig('tts_language', event.target.value)}
                        >
                          <option value="">Hindi (default)</option>
                          {SARVAM_TTS_LANGUAGES.map((lang) => <option key={lang.id} value={lang.id}>{lang.label}</option>)}
                        </select>
                      </label>
                    </>
                  )}
                  {value.tts_provider === 'elevenlabs' && (
                    <label>
                      ElevenLabs voice ID
                      <input
                        value={String(value.tts_voice || '')}
                        placeholder="Paste a voice ID from your ElevenLabs dashboard"
                        onChange={(event) => onUpdateConfig('tts_voice', event.target.value)}
                      />
                      <small>Find voice IDs at elevenlabs.io under Voices — click a voice and copy its ID.</small>
                    </label>
                  )}
                  <label>
                    LLM
                    <select
                      value={String(value.llm_provider || '')}
                      onChange={(event) => onUpdateConfig('llm_provider', event.target.value)}
                    >
                      <option value="">Gemini (default)</option>
                      <option value="gemini">Gemini</option>
                      <option value="openai">OpenAI</option>
                    </select>
                  </label>
                  {value.llm_provider === 'openai' && (
                    <label>
                      LLM model
                      <input
                        value={String(value.llm_model || '')}
                        placeholder="gpt-4.1 (default)"
                        onChange={(event) => onUpdateConfig('llm_model', event.target.value)}
                      />
                    </label>
                  )}
                </div>

                <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem', marginTop: '1rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <SlidersHorizontal size={12} /> Advanced / Admin settings
                </div>
                <div className="form-grid">
                  <label>
                    Temperature
                    <input type="number" min="0" max="2" step="0.1" value={Number(value.temperature ?? 0.4)} onChange={(event) => onUpdateConfig('temperature', Number(event.target.value))} />
                    <small>LLM (gemini-3.1-flash-lite) sampling temperature.</small>
                  </label>
                  <label>
                    Post-speech hold ms
                    <input type="number" value={Number(value.post_speech_hold_ms ?? 400)} onChange={(event) => onUpdateConfig('post_speech_hold_ms', Number(event.target.value))} />
                    <small>How long to hold after the caller stops speaking before the bot responds.</small>
                  </label>
                  <label>
                    Voice-activity threshold
                    <input type="number" min="0" max="1" step="0.05" value={Number(value.silero_threshold ?? 0.6)} onChange={(event) => onUpdateConfig('silero_threshold', Number(event.target.value))} />
                    <small>Sensitivity for detecting real speech vs. background noise during muted-window capture.</small>
                  </label>
                  <label>
                    Min speech duration ms
                    <input type="number" value={Number(value.silero_min_speech_ms ?? 1000)} onChange={(event) => onUpdateConfig('silero_min_speech_ms', Number(event.target.value))} />
                    <small>Minimum voiced audio duration to count as real speech.</small>
                  </label>
                  <label>
                    First rescue (s)
                    <input type="number" step="0.5" value={Number(value.inactivity_first_rescue_secs ?? 4)} onChange={(event) => onUpdateConfig('inactivity_first_rescue_secs', Number(event.target.value))} />
                    <small>Silence before the first inactivity check-in.</small>
                  </label>
                  <label>
                    First nudge gap (s)
                    <input type="number" step="0.5" value={Number(value.inactivity_first_nudge_gap_secs ?? 4)} onChange={(event) => onUpdateConfig('inactivity_first_nudge_gap_secs', Number(event.target.value))} />
                  </label>
                  <label>
                    Nudge interval (s)
                    <input type="number" step="0.5" value={Number(value.inactivity_nudge_secs ?? 10)} onChange={(event) => onUpdateConfig('inactivity_nudge_secs', Number(event.target.value))} />
                    <small>Gap between repeated nudges while the caller stays silent.</small>
                  </label>
                  <label>
                    Auto-close (s)
                    <input type="number" step="0.5" value={Number(value.inactivity_close_secs ?? 5)} onChange={(event) => onUpdateConfig('inactivity_close_secs', Number(event.target.value))} />
                    <small>Final silence window before the call ends automatically.</small>
                  </label>
                  <label>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                      <input
                        type="checkbox"
                        checked={Boolean(value.backchanneling_enabled)}
                        onChange={(event) => onUpdateConfig('backchanneling_enabled', event.target.checked)}
                      />
                      Backchanneling
                    </span>
                    <small>Plays a short hold/acknowledgment sound during tool calls. Read by the LiveKit runtime at call start.</small>
                  </label>
                  <label>
                    Noise filter sensitivity
                    <select
                      value={String(value.noise_filter_sensitivity || 'medium')}
                      onChange={(event) => onUpdateConfig('noise_filter_sensitivity', event.target.value)}
                    >
                      <option value="low">Low</option>
                      <option value="medium">Medium</option>
                      <option value="high">High</option>
                    </select>
                    <small>Controls the VAD noise threshold used by the LiveKit runtime. Applied per-call from this bot's config.</small>
                  </label>
                  <label>
                    Dialer service ID
                    <input
                      type="number"
                      value={Number((value.recording as RuntimeConfig['recording'] | undefined)?.service_id || 293)}
                      onChange={(event) => onUpdateConfig('recording', {
                        ...(typeof value.recording === 'object' && value.recording ? value.recording : {}),
                        service_id: Number(event.target.value)
                      })}
                    />
                  </label>
                  <label>
                    Dialer city
                    <input
                      value={String((value.recording as RuntimeConfig['recording'] | undefined)?.dialer_city || 'bangalore')}
                      onChange={(event) => onUpdateConfig('recording', {
                        ...(typeof value.recording === 'object' && value.recording ? value.recording : {}),
                        dialer_city: event.target.value
                      })}
                    />
                  </label>
                  <label>
                    MIS API base URL
                    <input
                      value={String((value.api_urls as Record<string,string> | undefined)?.mis_api_base || '')}
                      placeholder="Leave blank to use platform default"
                      onChange={(event) => onUpdateConfig('api_urls', {
                        ...(typeof value.api_urls === 'object' && value.api_urls ? value.api_urls : {}),
                        mis_api_base: event.target.value
                      })}
                    />
                    <small>Per-bot override for MIS lead fetch endpoint.</small>
                  </label>
                </div>
                <CloseMarkersEditor
                  markers={Array.isArray(value.close_markers) ? value.close_markers as string[] : []}
                  onChange={v => onUpdateConfig('close_markers', v)}
                />
                <label style={{ marginTop: '0.5rem', display: 'block' }}>
                  Function calling
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.3rem' }}>
                    <input type="checkbox" checked={Boolean(value.function_calling)} onChange={(e) => onUpdateConfig('function_calling', e.target.checked)} style={{ width: 'auto' }} />
                    <span style={{ fontSize: '0.85rem' }}>Enable function calling (requires functions list in JSON)</span>
                  </div>
                </label>
              </div>
            )}
          </div>
        </div>
        {isAdvanced && (
          <div className="panel json-panel">
            <div className="panel-header">
              <div>
                <h2>Developer JSON</h2>
                <p>Full runtime config. Only visible in Advanced mode.</p>
              </div>
              <Database size={18} />
            </div>
            <textarea
              className="json-editor"
              value={configText}
              onChange={(event) => onConfigTextChange(event.target.value)}
              spellCheck={false}
              aria-invalid={!config.ok}
            />
            {!config.ok && (
              <div className="notice error" role="alert" style={{ marginTop: '0.5rem' }}>
                <AlertTriangle size={16} /> Invalid JSON — fix before saving: {config.error}
              </div>
            )}
          </div>
        )}
      </div>
      <aside className="right-rail">
        <div className="panel compact">
          <h2>Publishing</h2>
          <div className="detail-list">
            <Detail label="Published versions" value={publishedCount.toString()} />
            <Detail label="Active version" value={activeVersion ? `v${activeVersion.version}` : '-'} />
            <Detail label="Latest draft" value={latestDraft ? `v${latestDraft.version}` : 'None'} />
          </div>
        </div>
        <div className="panel compact">
          <h2>Version history</h2>
          <div className="version-list">
            {versions.map((version, i) => {
              const isActive = version._id === selectedBot?.active_version_id;
              const rollbackKey = `rollback-${version._id}`;
              const rolling = rollbackState?.[rollbackKey] === 'running';
              const liveCalls = liveCallsByVersion?.[version._id] || 0;
              return (
                <div
                  className={`version-row${version._id === editingVersionId ? ' active' : ''}`}
                  key={version._id}
                  onClick={() => onSelectVersion?.(version)}
                  style={{ cursor: 'pointer' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                    <span>v{version.version}</span>
                    <StatusPill value={version.state} />
                    {isActive && <span style={{ fontSize: '0.68rem', background: 'var(--primary-bg)', color: 'var(--primary)', borderRadius: '4px', padding: '1px 5px', fontWeight: 600 }}>LIVE</span>}
                    {liveCalls > 0 && (
                      <span style={{ fontSize: '0.68rem', background: '#dcfce7', color: '#15803d', borderRadius: '4px', padding: '1px 5px', fontWeight: 600 }}>
                        {liveCalls} live
                      </span>
                    )}
                  </div>
                  <small>{version.published_at ? <>Published <TimeAgo value={version.published_at} /></> : <>Created <TimeAgo value={version.created_at} /></>}</small>
                  {version.notes && <small style={{ color: 'var(--muted)', fontStyle: 'italic' }}>{version.notes}</small>}
                  <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap', marginTop: '0.2rem' }}>
                    {version.state === 'published' && !isActive && onRollback && (
                      <button
                        style={{ fontSize: '0.72rem', padding: '2px 8px' }}
                        disabled={rolling}
                        onClick={(e) => { e.stopPropagation(); onRollback(version._id); }}
                      >
                        {rolling ? 'Rolling back…' : '↩ Rollback to this'}
                      </button>
                    )}
                    {i > 0 && onShowDiff && (
                      <button
                        style={{ fontSize: '0.72rem', padding: '2px 8px' }}
                        onClick={(e) => { e.stopPropagation(); onShowDiff(versions[i - 1]._id, version._id); }}
                      >
                        <GitBranch size={11} /> vs prev
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        <div className="callout">
          <Wand2 size={18} />
          V1 is prompt and settings only. Visual node routing can come later through Dograh.
        </div>
      </aside>
    </section>
  );
}
