import React, { useState, useMemo, useEffect } from 'react';
import {
  Ban, CheckCircle2, ChevronRight, GitBranch, HelpCircle, Layers,
  Megaphone, Pause, Pencil, PhoneOff, Plus, RefreshCw, Save, Trash2
} from 'lucide-react';
import type {
  AttemptStep, Bot as BotType, CallWindow, Campaign,
  DialingStrategy, LanguageOption, OutcomeEntry, OutcomeRule
} from '../api';
import { StatusPill } from '../components/StatusPill';
import { SkeletonTableBody } from '../components/SkeletonTableBody';
import { EmptyState } from '../components/EmptyState';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Dialog } from '../components/Dialog';
import { RowActions } from '../components/RowActions';
import { Spinner } from '../components/Spinner';
import { Tooltip } from '../components/Tooltip';
import { OUTCOME_CATEGORIES, DEFAULT_OUTCOME_RULES } from '../constants/outcomes';
import { DAY_OPTIONS } from '../constants/dialing';

export function buildDefaultStrategy(): DialingStrategy {
  return {
    enabled: true,
    outcome_rules: DEFAULT_OUTCOME_RULES,
    call_windows: [{ days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'], start_time: '09:00', end_time: '20:00', timezone: 'Asia/Kolkata' }],
    attempt_sequence: [
      { attempt: 1, language: 'hindi' },
      { attempt: 2, language: 'hindi' },
      { attempt: 3, language: 'english' },
    ],
    max_attempts_total: 5,
    max_attempts_per_day: 2,
    lead_expiry_days: 30,
    priority: 'normal',
  };
}

export function mergeWithDefaults(existing: DialingStrategy | undefined): DialingStrategy {
  const defaults = buildDefaultStrategy();
  if (!existing) return defaults;
  // Merge outcome_rules: start from defaults, overlay existing rules by outcome key
  const existingByOutcome = new Map((existing.outcome_rules || []).map((r) => [r.outcome, r]));
  const mergedRules = defaults.outcome_rules.map((defaultRule) => existingByOutcome.get(defaultRule.outcome) || defaultRule);
  // Also include any rules in existing that aren't in defaults
  (existing.outcome_rules || []).forEach((r) => {
    if (!mergedRules.find((mr) => mr.outcome === r.outcome)) mergedRules.push(r);
  });
  return {
    ...defaults,
    ...existing,
    outcome_rules: mergedRules,
  };
}

export function formatDelay(min?: number): string {
  if (min === undefined || min === null) return '-';
  if (min === 0) return 'immediate';
  if (min < 60) return `${min}m`;
  const hours = Math.floor(min / 60);
  const remainder = min % 60;
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}

export function OutcomeRuleRow({
  rule,
  bots,
  languages,
  onUpdate
}: {
  rule: OutcomeRule;
  bots: BotType[];
  languages: LanguageOption[];
  onUpdate: (outcome: string, patch: Partial<OutcomeRule>) => void;
}) {
  const cat = OUTCOME_CATEGORIES[rule.outcome];
  const group = cat?.group || 'stop';
  const isRetry = rule.action === 'retry';

  return (
    <div className={`outcome-rule-row outcome-row-${group}`}>
      <div className="outcome-rule-name">
        <span className={`outcome-action-icon ${rule.action}`}>
          {rule.action === 'completed' && <CheckCircle2 size={14} />}
          {rule.action === 'retry' && <RefreshCw size={14} />}
          {rule.action === 'stop' && <PhoneOff size={14} />}
          {rule.action === 'dnc' && <Ban size={14} />}
        </span>
        <strong>{cat?.label || rule.outcome}</strong>
      </div>
      <div className="outcome-rule-controls">
        <select
          value={rule.action}
          onChange={(e) => onUpdate(rule.outcome, { action: e.target.value as OutcomeRule['action'] })}
          className="outcome-action-select"
        >
          <option value="retry">Retry</option>
          <option value="stop">Stop</option>
          <option value="dnc">DNC</option>
          <option value="completed">Completed</option>
        </select>
        {isRetry && (
          <>
            <label className="inline-label">
              <span>Attempts</span>
              <input
                type="number"
                min={1} max={20}
                value={rule.max_attempts ?? 2}
                onChange={(e) => onUpdate(rule.outcome, { max_attempts: Number(e.target.value) })}
                className="attempts-input"
              />
            </label>
            <label className="inline-label">
              <span>Delay (min)</span>
              <input
                type="number"
                min={0} max={10080}
                value={rule.retry_after_min ?? 60}
                onChange={(e) => onUpdate(rule.outcome, { retry_after_min: Number(e.target.value) })}
                className="delay-input"
              />
            </label>
            <label className="inline-label">
              <span>Language</span>
              <select
                value={rule.language_override || ''}
                onChange={(e) => onUpdate(rule.outcome, { language_override: e.target.value || undefined })}
                className="language-override-select"
              >
                <option value="">Same as strategy</option>
                {languages.map((lang) => (
                  <option key={lang.id} value={lang.id}>{lang.label}</option>
                ))}
              </select>
            </label>
          </>
        )}
        {!isRetry && (
          <span className="outcome-rule-summary">
            {rule.action === 'completed' ? 'Mark qualified, no follow-up' :
             rule.action === 'dnc' ? 'Block number permanently' :
             'Remove from queue'}
          </span>
        )}
      </div>
    </div>
  );
}

export function OutcomeRulesTable({
  rules,
  bots,
  languages,
  onUpdate
}: {
  rules: OutcomeRule[];
  bots: BotType[];
  languages: LanguageOption[];
  onUpdate: (outcome: string, patch: Partial<OutcomeRule>) => void;
}) {
  const ruleByOutcome = new Map(rules.map((r) => [r.outcome, r]));

  return (
    <div className="outcome-rules-table">
      <div className="outcome-group-header completed-group">Positive outcomes — stop calling, lead is qualified</div>
      {['Approved', 'Enriched', 'Interested'].map((outcome) => {
        const rule = ruleByOutcome.get(outcome) || { outcome, action: 'completed' as const };
        return <OutcomeRuleRow key={outcome} rule={rule} bots={bots} languages={languages} onUpdate={onUpdate} />;
      })}

      <div className="outcome-group-header retry-group">Retry outcomes — call again after a delay</div>
      {['Short Hangup', 'Voicemail', 'Could Not Confirm', 'Call Rescheduled', 'Technical Issue - Call Connected', 'Language Issue', 'Other Cases'].map((outcome) => {
        const rule = ruleByOutcome.get(outcome) || { outcome, action: 'retry' as const, max_attempts: 2, retry_after_min: 60 };
        return <OutcomeRuleRow key={outcome} rule={rule} bots={bots} languages={languages} onUpdate={onUpdate} />;
      })}

      <div className="outcome-group-header stop-group">Stop outcomes — no more calls for this lead</div>
      {['Not Interested', 'Wrong Number', 'Already Spoken', 'Will do it Myself', 'Alternate Number', 'Seller Intent'].map((outcome) => {
        const rule = ruleByOutcome.get(outcome) || { outcome, action: 'stop' as const };
        return <OutcomeRuleRow key={outcome} rule={rule} bots={bots} languages={languages} onUpdate={onUpdate} />;
      })}

      <div className="outcome-group-header dnc-group">Do Not Call — permanently block this number</div>
      {["DNC Client : Don't Call Further", 'Abusive Lead'].map((outcome) => {
        const rule = ruleByOutcome.get(outcome) || { outcome, action: 'dnc' as const };
        return <OutcomeRuleRow key={outcome} rule={rule} bots={bots} languages={languages} onUpdate={onUpdate} />;
      })}
    </div>
  );
}

export function CallWindowsEditor({
  windows,
  onChange
}: {
  windows: CallWindow[];
  onChange: (windows: CallWindow[]) => void;
}) {
  const window = windows[0] || { days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'], start_time: '09:00', end_time: '20:00', timezone: 'Asia/Kolkata' };

  function updateWindow(patch: Partial<CallWindow>) {
    onChange([{ ...window, ...patch }]);
  }

  function toggleDay(day: string) {
    const days = window.days.includes(day)
      ? window.days.filter((d) => d !== day)
      : [...window.days, day];
    updateWindow({ days });
  }

  return (
    <div className="call-window-editor">
      <div className="day-picker">
        {DAY_OPTIONS.map((day) => (
          <button
            key={day.id}
            className={window.days.includes(day.id) ? 'day-btn active' : 'day-btn'}
            onClick={() => toggleDay(day.id)}
            type="button"
          >
            {day.label}
          </button>
        ))}
      </div>
      <div className="form-grid">
        <label>
          From
          <input
            type="time"
            value={window.start_time}
            onChange={(e) => updateWindow({ start_time: e.target.value })}
          />
        </label>
        <label>
          To
          <input
            type="time"
            value={window.end_time}
            onChange={(e) => updateWindow({ end_time: e.target.value })}
          />
        </label>
        <label className="full">
          Timezone
          <input
            value={window.timezone}
            onChange={(e) => updateWindow({ timezone: e.target.value })}
            placeholder="Asia/Kolkata"
          />
        </label>
      </div>
    </div>
  );
}

export function AttemptSequenceEditor({
  steps,
  bots,
  languages,
  onChange
}: {
  steps: AttemptStep[];
  bots: BotType[];
  languages: LanguageOption[];
  onChange: (steps: AttemptStep[]) => void;
}) {
  function updateStep(attempt: number, patch: Partial<AttemptStep>) {
    onChange(steps.map((s) => s.attempt === attempt ? { ...s, ...patch } : s));
  }

  function removeStep(attempt: number) {
    onChange(steps.filter((s) => s.attempt !== attempt));
  }

  if (!steps.length) {
    return <p className="muted" style={{ padding: '12px 0' }}>No steps defined. All attempts use the campaign default bot and language.</p>;
  }

  return (
    <div className="attempt-sequence">
      {steps.sort((a, b) => a.attempt - b.attempt).map((step) => (
        <div key={step.attempt} className="attempt-step">
          <span className="attempt-badge">#{step.attempt}</span>
          <div className="attempt-controls">
            <select
              value={step.language || ''}
              onChange={(e) => updateStep(step.attempt, { language: e.target.value || undefined })}
            >
              <option value="">Default language</option>
              {languages.map((lang) => (
                <option key={lang.id} value={lang.id}>{lang.label}</option>
              ))}
            </select>
            <select
              value={step.bot_id || ''}
              onChange={(e) => updateStep(step.attempt, { bot_id: e.target.value || undefined })}
            >
              <option value="">Default bot</option>
              {bots.map((bot) => (
                <option key={bot._id} value={bot._id}>{bot.name}</option>
              ))}
            </select>
          </div>
          <button className="danger-button" onClick={() => removeStep(step.attempt)} style={{ padding: '0 8px', minHeight: 32 }}>
            <Trash2 size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function DialingStrategyBuilder({
  campaign,
  bots,
  languages,
  outcomes,
  onBack,
  onSave,
  saveState
}: {
  campaign: Campaign;
  bots: BotType[];
  languages: LanguageOption[];
  outcomes: OutcomeEntry[];
  onBack: () => void;
  onSave: (strategy: DialingStrategy) => Promise<void>;
  saveState?: 'idle' | 'running' | 'failed';
}) {
  const [strategy, setStrategy] = useState<DialingStrategy>(() =>
    mergeWithDefaults(campaign.dialing_strategy)
  );
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setStrategy(mergeWithDefaults(campaign.dialing_strategy));
    setDirty(false);
  }, [campaign.campaign_key]);

  function updateStrategy(patch: Partial<DialingStrategy>) {
    setStrategy((prev) => ({ ...prev, ...patch }));
    setDirty(true);
  }

  function updateRule(outcome: string, patch: Partial<OutcomeRule>) {
    setStrategy((prev) => ({
      ...prev,
      outcome_rules: prev.outcome_rules.map((r) =>
        r.outcome === outcome ? { ...r, ...patch } : r
      )
    }));
    setDirty(true);
  }

  async function handleSave() {
    await onSave(strategy);
    setDirty(false);
  }

  const ruleCounts = useMemo(
    () => strategy.outcome_rules.reduce<Record<string, number>>(
      (acc, r) => { acc[r.action] = (acc[r.action] ?? 0) + 1; return acc; }, {}
    ),
    [strategy.outcome_rules]
  );
  const retryCount = ruleCounts.retry ?? 0;
  const stopCount = ruleCounts.stop ?? 0;
  const dncCount = ruleCounts.dnc ?? 0;
  const completedCount = ruleCounts.completed ?? 0;

  return (
    <section className="strategy-builder">
      <div className="strategy-topbar">
        <div className="strategy-breadcrumb">
          <button onClick={onBack}><ChevronRight className="rotate-180" size={16} /> Campaigns</button>
          <ChevronRight size={14} />
          <strong>{campaign.name}</strong>
        </div>
        <div className="strategy-header-right">
          <div className="strategy-stats">
            <span className="strategy-stat retry">{retryCount} retry</span>
            <span className="strategy-stat stop">{stopCount} stop</span>
            <span className="strategy-stat dnc">{dncCount} DNC</span>
            <span className="strategy-stat completed">{completedCount} done</span>
          </div>
          <label className="toggle-inline">
            <input
              type="checkbox"
              checked={strategy.enabled}
              onChange={(e) => updateStrategy({ enabled: e.target.checked })}
            />
            {strategy.enabled ? 'Strategy active' : 'Strategy paused'}
          </label>
          <button
            className={saveState === 'failed' ? 'fallback-button' : 'primary'}
            onClick={handleSave}
            disabled={saveState === 'running' || !dirty}
          >
            <Save size={16} />
            {saveState === 'running' ? 'Saving...' : saveState === 'failed' ? 'Retry save' : dirty ? 'Save strategy' : 'Saved'}
          </button>
        </div>
      </div>

      <div className="strategy-layout">
        <div className="strategy-main">
          <div className="panel">
            <div className="panel-header">
              <div>
                <h2>Outcome rules</h2>
                <p>For each call outcome, set whether to retry, stop, or block the number (DNC).</p>
              </div>
              <GitBranch size={18} />
            </div>
            <OutcomeRulesTable
              rules={strategy.outcome_rules}
              bots={bots}
              languages={languages}
              onUpdate={updateRule}
            />
          </div>
        </div>

        <aside className="strategy-rail">
          <div className="panel compact">
            <div className="panel-header">
              <div><h2>Global limits</h2><p>Caps that apply across all outcome rules.</p></div>
            </div>
            <div className="form-section">
              <div className="form-grid">
                <label>
                  Max total attempts
                  <input
                    type="number"
                    min={1} max={50}
                    value={strategy.max_attempts_total}
                    onChange={(e) => updateStrategy({ max_attempts_total: Number(e.target.value) })}
                  />
                </label>
                <label>
                  Max attempts per day
                  <input
                    type="number"
                    min={1} max={20}
                    value={strategy.max_attempts_per_day}
                    onChange={(e) => updateStrategy({ max_attempts_per_day: Number(e.target.value) })}
                  />
                </label>
                <label>
                  Lead expiry (days)
                  <input
                    type="number"
                    min={1} max={365}
                    value={strategy.lead_expiry_days}
                    onChange={(e) => updateStrategy({ lead_expiry_days: Number(e.target.value) })}
                  />
                </label>
                <label>
                  Priority
                  <select
                    value={strategy.priority}
                    onChange={(e) => updateStrategy({ priority: e.target.value as DialingStrategy['priority'] })}
                  >
                    <option value="low">Low</option>
                    <option value="normal">Normal</option>
                    <option value="high">High</option>
                    <option value="urgent">Urgent</option>
                  </select>
                </label>
              </div>
            </div>
          </div>

          <div className="panel compact">
            <div className="panel-header">
              <div><h2>Call windows</h2><p>Only dial during these hours.</p></div>
            </div>
            <CallWindowsEditor
              windows={strategy.call_windows}
              onChange={(windows) => { updateStrategy({ call_windows: windows }); }}
            />
          </div>

          <div className="panel compact">
            <div className="panel-header">
              <div><h2>Attempt sequence</h2><p>Override bot or language per attempt number.</p></div>
              <button
                onClick={() => {
                  const nextAttempt = (strategy.attempt_sequence.length ? Math.max(...strategy.attempt_sequence.map((s) => s.attempt)) : 0) + 1;
                  updateStrategy({ attempt_sequence: [...strategy.attempt_sequence, { attempt: nextAttempt }] });
                }}
              >
                <Plus size={14} /> Add step
              </button>
            </div>
            <AttemptSequenceEditor
              steps={strategy.attempt_sequence}
              bots={bots}
              languages={languages}
              onChange={(steps) => { updateStrategy({ attempt_sequence: steps }); }}
            />
          </div>
        </aside>
      </div>
    </section>
  );
}

export function CampaignsView({
  campaigns,
  bots,
  languages,
  outcomes,
  loading,
  workspaceMode,
  selectedCampaignKey,
  onSelectCampaign,
  onBackToList,
  onSaveStrategy,
  saveState,
  onAssignBot,
  assignBotState,
  onSetStatus,
  onCreateCampaign,
  createState,
  onDelete,
}: {
  campaigns: Campaign[];
  bots: BotType[];
  languages: LanguageOption[];
  outcomes: OutcomeEntry[];
  loading?: boolean;
  workspaceMode: 'list' | 'strategy';
  selectedCampaignKey: string;
  onSelectCampaign: (key: string) => void;
  onBackToList: () => void;
  onSaveStrategy: (key: string, name: string, strategy: DialingStrategy) => Promise<void>;
  saveState?: 'idle' | 'running' | 'failed';
  onAssignBot?: (campaignKey: string, botId: string) => void;
  assignBotState?: Record<string, 'idle' | 'running' | 'failed'>;
  onSetStatus?: (campaignKey: string, status: string) => void;
  onCreateCampaign?: (campaignKey: string, name: string, botId?: string) => Promise<void>;
  createState?: 'idle' | 'running' | 'failed';
  onDelete?: (campaignKey: string) => void;
}) {
  const selectedCampaign = campaigns.find((c) => c.campaign_key === selectedCampaignKey) || campaigns[0];
  const botById = useMemo(() => new Map(bots.map((b) => [b._id, b])), [bots]);
  const [pendingStatusChange, setPendingStatusChange] = useState<{ campaign: Campaign; status: string } | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newCampaignKey, setNewCampaignKey] = useState('');
  const [newCampaignName, setNewCampaignName] = useState('');
  const [newCampaignBotId, setNewCampaignBotId] = useState('');
  const [editTarget, setEditTarget] = useState<Campaign | null>(null);
  const [editName, setEditName] = useState('');
  const [editBotId, setEditBotId] = useState('');

  useEffect(() => {
    if (!editTarget) return;
    setEditName(editTarget.name || '');
    setEditBotId(editTarget.bot_id || '');
  }, [editTarget]);

  function confirmStatusChange() {
    if (!pendingStatusChange || !onSetStatus) return;
    onSetStatus(pendingStatusChange.campaign.campaign_key, pendingStatusChange.status);
    setPendingStatusChange(null);
  }

  async function handleCreate() {
    if (!onCreateCampaign || !newCampaignKey.trim() || !newCampaignName.trim()) return;
    await onCreateCampaign(newCampaignKey.trim(), newCampaignName.trim(), newCampaignBotId || undefined);
    setNewCampaignKey(''); setNewCampaignName(''); setNewCampaignBotId('');
    setShowCreateModal(false);
  }

  async function handleSaveEdit() {
    if (!editTarget) return;
    await onSaveStrategy(editTarget.campaign_key, editName.trim() || editTarget.name, editTarget.dialing_strategy || buildDefaultStrategy());
    if (onAssignBot && editBotId !== (editTarget.bot_id || '')) {
      onAssignBot(editTarget.campaign_key, editBotId);
    }
    setEditTarget(null);
  }

  if (workspaceMode === 'strategy' && selectedCampaign) {
    return (
      <DialingStrategyBuilder
        campaign={selectedCampaign}
        bots={bots}
        languages={languages}
        outcomes={outcomes}
        onBack={onBackToList}
        onSave={(strategy) => onSaveStrategy(selectedCampaign.campaign_key, selectedCampaign.name, strategy)}
        saveState={saveState}
      />
    );
  }

  return (
    <section className="content-grid two-col">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Campaign mappings</h2>
            <p>One bot belongs to one campaign, with lead API and callback mapping owned in Mongo.</p>
          </div>
          {onCreateCampaign && (
            <button className="primary" onClick={() => setShowCreateModal(true)}>
              <Plus size={14} /> New campaign
            </button>
          )}
        </div>
        <div className="table-scroll"><table>
          <thead>
            <tr><th>Campaign</th><th>Bot</th><th>Status</th><th>Strategy</th><th>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                Lead API
                <Tooltip label="The endpoint this campaign pulls leads from.">
                  <HelpCircle size={12} style={{ color: 'var(--muted)', cursor: 'help' }} />
                </Tooltip>
              </span>
            </th><th>Actions</th></tr>
          </thead>
          <tbody>
            {loading && !campaigns.length ? (
              <SkeletonTableBody cols={6} rows={3} />
            ) : !campaigns.length ? (
              <tr><td colSpan={6}>
                <EmptyState
                  icon={<Megaphone size={32} />}
                  heading="No campaigns yet"
                  description="Campaigns are created via the backend API. Each campaign maps a bot to a lead source and callback endpoint."
                />
              </td></tr>
            ) : campaigns.map((campaign) => (
              <tr key={campaign._id}>
                <td><strong>{campaign.name}</strong><small>{campaign.campaign_key}</small></td>
                <td>
                  {onAssignBot ? (
                    <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                      <select
                        value={campaign.bot_id || ''}
                        onChange={e => onAssignBot(campaign.campaign_key, e.target.value)}
                        disabled={assignBotState?.[campaign.campaign_key] === 'running'}
                        style={{ fontSize: '0.82rem', width: '100%' }}
                      >
                        <option value="">— unassigned —</option>
                        {bots.map(b => <option key={b._id} value={b._id}>{b.name}</option>)}
                      </select>
                      {assignBotState?.[campaign.campaign_key] === 'running' && <Spinner label="Assigning" size={13} />}
                    </span>
                  ) : (
                    botById.get(campaign.bot_id ?? '')?.name || '-'
                  )}
                </td>
                <td><StatusPill value={campaign.status || 'draft'} /></td>
                <td>
                  {campaign.dialing_strategy
                    ? <span className={`pill ${campaign.dialing_strategy.enabled ? 'active' : 'draft'}`}>{campaign.dialing_strategy.enabled ? 'Active' : 'Paused'}</span>
                    : <span className="pill draft">Default</span>
                  }
                </td>
                <td><code>{String(campaign.lead_api?.url || campaign.lead_api?.endpoint || '-')}</code></td>
                <td style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {onSetStatus && campaign.status !== 'active' && (
                    <button
                      className="btn-success"
                      title="Activate campaign"
                      onClick={() => setPendingStatusChange({ campaign, status: 'active' })}
                    >
                      <ChevronRight size={13} /> Activate
                    </button>
                  )}
                  {onSetStatus && campaign.status === 'active' && (
                    <button
                      className="btn-warn"
                      title="Pause campaign"
                      onClick={() => setPendingStatusChange({ campaign, status: 'paused' })}
                    >
                      <Pause size={13} /> Pause
                    </button>
                  )}
                  <button onClick={() => onSelectCampaign(campaign.campaign_key)}>
                    <Layers size={14} /> Strategy
                  </button>
                  <RowActions
                    item={campaign}
                    onEdit={setEditTarget}
                    editTitle="Edit campaign"
                    onDelete={onDelete ? (target) => onDelete(target.campaign_key) : undefined}
                    deleteTitle="Delete campaign?"
                    deleteDescription={(target) => `"${target.name}" and its dialing strategy will be removed. This does not affect the bot it was assigned to.`}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
      <div className="panel">
        <h2>Strategy</h2>
        <div className="callout">
          <Layers size={18} />
          Click "Edit Strategy" on any campaign to configure retry rules, time windows, and attempt sequencing.
        </div>
      </div>

      {pendingStatusChange && (
        <ConfirmDialog
          title={pendingStatusChange.status === 'active' ? 'Activate campaign?' : 'Pause campaign?'}
          description={
            pendingStatusChange.status === 'active'
              ? `"${pendingStatusChange.campaign.name}" will start dialing leads again on its normal schedule.`
              : `"${pendingStatusChange.campaign.name}" will stop dialing new leads immediately. Calls already in progress are unaffected.`
          }
          confirmLabel={pendingStatusChange.status === 'active' ? 'Activate' : 'Pause'}
          tone={pendingStatusChange.status === 'active' ? 'default' : 'danger'}
          onCancel={() => setPendingStatusChange(null)}
          onConfirm={confirmStatusChange}
        />
      )}

      {showCreateModal && (
        <Dialog
          title="New campaign"
          icon={<Megaphone size={17} />}
          onClose={() => setShowCreateModal(false)}
          closeOnBackdrop={createState !== 'running'}
          closeOnEscape={createState !== 'running'}
          footer={
            <>
              <button onClick={() => setShowCreateModal(false)} disabled={createState === 'running'}>Cancel</button>
              <button
                className={createState === 'failed' ? 'fallback-button' : 'primary'}
                onClick={handleCreate}
                disabled={createState === 'running' || !newCampaignKey.trim() || !newCampaignName.trim()}
              >
                {createState === 'running' ? 'Creating...' : createState === 'failed' ? 'Retry create' : 'Create'}
              </button>
            </>
          }
        >
          <div className="form-grid">
            <label>
              Campaign key
              <input value={newCampaignKey} onChange={(e) => setNewCampaignKey(e.target.value)} placeholder="justdial_leads_bangalore" />
            </label>
            <label>
              Name
              <input value={newCampaignName} onChange={(e) => setNewCampaignName(e.target.value)} placeholder="Bangalore Leads" />
            </label>
            <label className="full">
              Assigned bot (optional)
              <select value={newCampaignBotId} onChange={(e) => setNewCampaignBotId(e.target.value)}>
                <option value="">— unassigned —</option>
                {bots.map((b) => <option key={b._id} value={b._id}>{b.name}</option>)}
              </select>
            </label>
          </div>
        </Dialog>
      )}

      {editTarget && (
        <Dialog
          title={`Edit ${editTarget.name}`}
          icon={<Pencil size={17} />}
          onClose={() => setEditTarget(null)}
          closeOnBackdrop={saveState !== 'running'}
          closeOnEscape={saveState !== 'running'}
          footer={
            <>
              <button onClick={() => setEditTarget(null)} disabled={saveState === 'running'}>Cancel</button>
              <button
                className={saveState === 'failed' ? 'fallback-button' : 'primary'}
                onClick={handleSaveEdit}
                disabled={saveState === 'running' || !editName.trim()}
              >
                {saveState === 'running' ? 'Saving...' : saveState === 'failed' ? 'Retry save' : 'Save'}
              </button>
            </>
          }
        >
          <div className="form-grid">
            <label className="full">
              Name
              <input value={editName} onChange={(e) => setEditName(e.target.value)} />
            </label>
            <label className="full">
              Assigned bot
              <select value={editBotId} onChange={(e) => setEditBotId(e.target.value)}>
                <option value="">— unassigned —</option>
                {bots.map((b) => <option key={b._id} value={b._id}>{b.name}</option>)}
              </select>
            </label>
          </div>
        </Dialog>
      )}

    </section>
  );
}
