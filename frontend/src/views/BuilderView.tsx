import React, { useState, useEffect, useRef } from 'react';
import {
  AlertTriangle, ChevronRight, Database, GitBranch, Pencil, Plus,
  Rocket, Save, ShieldCheck, SlidersHorizontal, Wand2
} from 'lucide-react';
import type { Bot as BotType, BotVersion, LanguageOption, PricingConfig } from '../api';
import type { RuntimeConfig, BuilderMode } from '../types';
import { defaultConfig, SARVAM_TTS_VOICES, SARVAM_TTS_LANGUAGES } from '../constants/ui';
import { StatusPill } from '../components/StatusPill';
import { TimeAgo } from '../components/TimeAgo';
import { Detail } from '../components/Detail';
import { CopyableId } from '../components/CopyableId';
import { BotConfigTabs } from '../components/BotConfigTabs';
import { CostBreakdownPopover } from '../components/CostBreakdownPopover';
import { estimateAgentCost } from '../utils/agentCost';
import { api } from '../api';
import type { CustomFunction } from '../types';

// Re-exported from its own module for backward compatibility with existing imports.
export { CloseMarkersEditor } from '../components/CloseMarkersEditor';

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

  // Self-fetched, same pattern as BudgetCostWidget.tsx's own pricing fetch — the "Agent
  // details" cost card doesn't need this wired through App.tsx.
  const [pricingConfig, setPricingConfig] = useState<PricingConfig | null>(null);
  useEffect(() => {
    api.getPricingAdminConfig().then(setPricingConfig).catch(() => setPricingConfig(null));
  }, []);
  const costEstimate = pricingConfig ? estimateAgentCost(value, pricingConfig) : null;

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

            {/* Tabbed settings: Agent · Speed · STT · TTS · LLM · Functions · Advanced */}
            <BotConfigTabs
              value={value}
              onUpdateConfig={onUpdateConfig}
              onUpdateLanguage={onUpdateLanguage}
              languages={languages}
              configText={configText}
              onConfigTextChange={onConfigTextChange}
              configOk={config.ok}
              configError={config.ok ? undefined : config.error}
              costEstimate={costEstimate}
              pricing={pricingConfig}
              botId={selectedBot?._id}
              onTestFunction={
                selectedBot?._id
                  ? (fn: CustomFunction, args: Record<string, unknown>) => api.testCustomFunction(selectedBot!._id, fn, args)
                  : undefined
              }
            />
          </div>
        </div>
        <aside className="right-rail">
          {selectedBot && (
            <div className="panel compact">
              <div className="panel-header-inline">
                <h2>Agent details</h2>
                <CopyableId value={selectedBot._id} label="ID" />
              </div>
              <div className="detail-list">
                {costEstimate ? (
                  <>
                    <CostBreakdownPopover estimate={costEstimate}>
                      <div className="detail dotted-underline-row">
                        <span>Cost</span>
                        <strong className="dotted-underline">₹{costEstimate.totalCostInrPerMin.toFixed(2)}/min</strong>
                      </div>
                    </CostBreakdownPopover>
                    <Detail
                      label="Latency"
                      value={
                        costEstimate.latencyMinMs != null && costEstimate.latencyMaxMs != null
                          ? `${costEstimate.latencyMinMs}-${costEstimate.latencyMaxMs}ms`
                          : '-'
                      }
                    />
                    <Detail
                      label="Tokens"
                      value={
                        costEstimate.tokensMin != null && costEstimate.tokensMax != null
                          ? `${costEstimate.tokensMin} - ${costEstimate.tokensMax >= 1000 ? `${(costEstimate.tokensMax / 1000).toFixed(costEstimate.tokensMax % 1000 === 0 ? 0 : 1)}k` : costEstimate.tokensMax}`
                          : '-'
                      }
                    />
                  </>
                ) : (
                  <Detail label="Cost" value="Loading…" />
                )}
              </div>
            </div>
          )}
          <div className="panel compact">
            <h2>Publishing</h2>
            <div className="detail-list">
              <Detail label="Published versions" value={publishedCount.toString()} />
              <Detail label="Active version" value={activeVersion ? `v${activeVersion.version}` : '-'} />
              <Detail label="Latest draft" value={latestDraft ? `v${latestDraft.version}` : 'None'} />
            </div>
          </div>
          <div className="panel compact right-rail-versions">
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
      </div>
    </section>
  );
}
