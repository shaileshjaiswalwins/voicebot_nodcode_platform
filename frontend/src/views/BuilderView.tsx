import React, { useState, useEffect, useRef } from 'react';
import {
  AlertTriangle, ChevronRight, Database, GitBranch, History, Info, Pencil, PhoneCall, Plus,
  Rocket, Save, ShieldCheck, X
} from 'lucide-react';
import type { Bot as BotType, BotVersion, LanguageOption, PricingConfig } from '../api';
import type { RuntimeConfig, BuilderMode } from '../types';
import { defaultConfig, SARVAM_TTS_VOICES, SARVAM_TTS_LANGUAGES } from '../constants/ui';
import { StatusPill } from '../components/StatusPill';
import { TimeAgo } from '../components/TimeAgo';
import { Detail } from '../components/Detail';
import { CopyableId } from '../components/CopyableId';
import { BotConfigTabs } from '../components/BotConfigTabs';
import type { BuilderTab as BuilderConfigTab } from '../components/BotConfigTabs';
import { CostBreakdownPopover } from '../components/CostBreakdownPopover';
import { estimateAgentCost } from '../utils/agentCost';
import { api } from '../api';
import type { CustomFunction } from '../types';

// Re-exported from its own module for backward compatibility with existing imports.
export { CloseMarkersEditor } from '../components/CloseMarkersEditor';

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
  onToggleMode,
  testPanelSlot
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
  /** The Test Audio/Test LLM rail's JSX, built and state-owned by App.tsx (testForm,
   * testPanelMode, dynamicVariables, etc. all live there already) — passed in as an element
   * rather than lifting that state up here, so the drawer can host it without duplicating
   * where that state lives. */
  testPanelSlot?: React.ReactNode;
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

  // Test Agent and Version history are two independent triggers sharing one slide-over
  // slot — opening either one always closes the other (a single "which content" state
  // makes that automatic, rather than two independent booleans that could both be true).
  // Agent details/Cost moved to a separate small info popover instead, since it's a quick
  // glance, not something you'd want a full panel width for.
  const [sidePanel, setSidePanel] = useState<'none' | 'test' | 'versions'>('none');
  const [detailsOpen, setDetailsOpen] = useState(false);
  const detailsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!detailsOpen) return;
    function onDocClick(e: MouseEvent) {
      if (detailsRef.current && !detailsRef.current.contains(e.target as Node)) setDetailsOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setDetailsOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [detailsOpen]);

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
          <div className="details-popover-anchor" ref={detailsRef}>
            <button className={detailsOpen ? 'primary' : ''} onClick={() => setDetailsOpen((v) => !v)} title="Agent details" aria-label="Agent details">
              <Info size={15} />
            </button>
            {detailsOpen && (
              <div className="details-popover">
                <div className="panel-header-inline">
                  <h2>Agent details</h2>
                  {selectedBot && <CopyableId value={selectedBot._id} label="ID" />}
                </div>
                <div className="detail-list">
                  <Detail label="Owner" value={selectedBot?.owner || 'Unknown'} />
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
                  <Detail label="Published versions" value={publishedCount.toString()} />
                  <Detail label="Active version" value={activeVersion ? `v${activeVersion.version}` : '-'} />
                  <Detail label="Latest draft" value={latestDraft ? `v${latestDraft.version}` : 'None'} />
                </div>
              </div>
            )}
          </div>
          <button className={sidePanel === 'versions' ? 'primary' : ''} onClick={() => setSidePanel((p) => (p === 'versions' ? 'none' : 'versions'))} title="Version history" aria-label="Version history">
            <History size={15} />
          </button>
          <button className={sidePanel === 'test' ? 'primary' : ''} onClick={() => setSidePanel((p) => (p === 'test' ? 'none' : 'test'))}>
            <PhoneCall size={15} /> Test Agent
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
                  onBlur={() => nameChanged && onRename?.(draftName, draftDescription)}
                />
              </label>
              <label>
                Description
                <input
                  value={draftDescription}
                  placeholder="Short description (optional)"
                  onChange={(e) => setDraftDescription(e.target.value)}
                  onBlur={() => nameChanged && onRename?.(draftName, draftDescription)}
                />
              </label>
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
              onGenerateFunction={(description) => api.generateFunction(description)}
              onGeneratePrompt={
                selectedBot?._id
                  ? (mode, instruction, currentPrompt, target) => api.generatePrompt(selectedBot!._id, mode, instruction, currentPrompt, target)
                  : undefined
              }
              onRefineWorkflow={(instruction, currentWorkflow, currentFunctions, currentGlobalPrompt) =>
                api.refineWorkflow(instruction, currentWorkflow || { nodes: [], edges: [] }, currentFunctions, currentGlobalPrompt)
              }
            />
          </div>
        </div>
      </div>

      {sidePanel !== 'none' && <div className="side-panel-backdrop" onClick={() => setSidePanel('none')} />}
      <aside className={sidePanel !== 'none' ? 'side-panel open' : 'side-panel'} aria-hidden={sidePanel === 'none'}>
        <div className="side-panel-header">
          <h2 style={{ margin: 0, fontSize: '0.95rem' }}>{sidePanel === 'versions' ? 'Version history' : 'Test Agent'}</h2>
          <button className="modal-close" onClick={() => setSidePanel('none')} aria-label="Close"><X size={16} /></button>
        </div>

        {sidePanel === 'test' && (
          <div className="side-panel-body">
            {testPanelSlot}
          </div>
        )}

        {sidePanel === 'versions' && (
          <div className="side-panel-body">
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
        )}
      </aside>
    </section>
  );
}
