import React, { useEffect, useMemo, useState } from 'react';
import { Bell, BellRing, Plus, Trash2 } from 'lucide-react';
import { api } from '../api';
import type { Bot } from '../api';
import type { AlertComparator, AlertFrequency, AlertIncident, AlertMetric, AlertRule, AlertRuleInput, AlertWindow } from '../types';
import {
  ALERT_COMPARATOR_LABELS,
  ALERT_FREQUENCY_OPTIONS,
  ALERT_METRIC_GROUPS,
  ALERT_METRIC_LABELS,
  ALERT_WINDOW_OPTIONS,
  ALERTS_LAST_VIEWED_HISTORY_KEY,
  WINDOW_FREQUENCY_COMPAT,
} from '../constants/alerts';
import { Dialog } from './Dialog';
import { EmptyState } from './EmptyState';
import { Spinner } from './Spinner';
import { TimeAgo } from './TimeAgo';

/** Retell caps at 10 rules/workspace; this platform has no workspace concept so the same
 * cap applies per-user (see plan doc "Rule limit"). Client-side mirror of the backend's
 * own enforcement — just disables "Create Alert" early with an explanatory message. */
const RULE_LIMIT = 10;

type AlertsSubTab = 'rules' | 'history';

function blankRuleDraft(): AlertRuleInput {
  return {
    name: '',
    metric: 'call_count',
    comparator: 'lt',
    threshold_value: 0,
    window: '1h',
    frequency: '5m',
    filters: { bot_ids: [] },
    enabled: true,
  };
}

function formatCondition(metric: AlertMetric, comparator: AlertComparator, value: number): string {
  return `${metric} ${ALERT_COMPARATOR_LABELS[comparator] || comparator} ${value}`;
}

function CreateAlertDialog({
  initial,
  bots,
  existingCount,
  onSave,
  onClose,
}: {
  initial?: AlertRule;
  bots: Bot[];
  existingCount: number;
  onSave: (payload: AlertRuleInput) => Promise<void>;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<AlertRuleInput>(
    initial
      ? {
          name: initial.name,
          metric: initial.metric,
          comparator: initial.comparator,
          threshold_value: initial.threshold_value,
          window: initial.window,
          frequency: initial.frequency,
          filters: initial.filters,
          enabled: initial.enabled,
        }
      : blankRuleDraft(),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const compatibleFrequencies = WINDOW_FREQUENCY_COMPAT[draft.window];
  const supportsOutcomeFilter = draft.metric === 'task_completion_rate_pct';

  function setWindow(window: AlertWindow) {
    // Editing the window can invalidate the currently-selected frequency — snap to the
    // window's first compatible frequency rather than leaving an invalid pair selected,
    // matching the live-validated dropdown described in the plan.
    const compat = WINDOW_FREQUENCY_COMPAT[window];
    setDraft((d) => ({ ...d, window, frequency: compat.includes(d.frequency) ? d.frequency : compat[0] }));
  }

  function toggleBot(botId: string) {
    setDraft((d) => {
      const has = d.filters.bot_ids.includes(botId);
      return { ...d, filters: { ...d.filters, bot_ids: has ? d.filters.bot_ids.filter((b) => b !== botId) : [...d.filters.bot_ids, botId] } };
    });
  }

  const nameValid = draft.name.trim().length > 0;
  const canSave = nameValid && !saving && (initial || existingCount < RULE_LIMIT);

  async function submit() {
    if (!canSave) return;
    setSaving(true);
    setError('');
    try {
      await onSave({ ...draft, name: draft.name.trim() });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save this alert.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      title={initial ? 'Edit alert' : 'Create alert'}
      icon={<BellRing size={18} />}
      onClose={onClose}
      maxWidth={560}
      footer={
        <div className="button-row">
          <button className="primary" onClick={submit} disabled={!canSave}>
            {saving ? 'Saving…' : initial ? 'Save changes' : 'Create alert'}
          </button>
          <button onClick={onClose}>Cancel</button>
        </div>
      }
    >
      {!initial && existingCount >= RULE_LIMIT && (
        <p style={{ color: 'var(--danger)', fontSize: 'var(--font-size-md)' }}>
          You've reached the limit of {RULE_LIMIT} alerts. Delete one before creating another.
        </p>
      )}
      <div className="form-grid">
        <label className="full">
          Name
          <input
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder="e.g. Call volume dropped"
          />
        </label>

        <label className="full">
          Metric
          <select
            value={draft.metric}
            onChange={(e) => {
              const metric = e.target.value as AlertMetric;
              const stillSupportsOutcomeFilter = metric === 'task_completion_rate_pct';
              setDraft((d) => ({
                ...d,
                metric,
                filters: stillSupportsOutcomeFilter
                  ? d.filters
                  : { ...d.filters, call_outcome: undefined },
              }));
            }}
          >
            {ALERT_METRIC_GROUPS.map((g) => (
              <optgroup key={g.group} label={g.group}>
                {g.metrics.map((m) => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>

        <label>
          Comparator
          <select value={draft.comparator} onChange={(e) => setDraft({ ...draft, comparator: e.target.value as AlertComparator })}>
            <option value="gt">Greater than (&gt;)</option>
            <option value="ge">Greater than or equal (&gt;=)</option>
            <option value="lt">Less than (&lt;)</option>
            <option value="le">Less than or equal (&lt;=)</option>
          </select>
        </label>
        <label>
          Threshold value
          <input
            type="number"
            value={draft.threshold_value}
            onChange={(e) => setDraft({ ...draft, threshold_value: Number(e.target.value) })}
          />
        </label>

        <label>
          Check every… (frequency)
          <select
            value={draft.frequency}
            onChange={(e) => setDraft({ ...draft, frequency: e.target.value as AlertFrequency })}
          >
            {ALERT_FREQUENCY_OPTIONS.map((f) => (
              <option key={f.value} value={f.value} disabled={!compatibleFrequencies.includes(f.value)}>
                {f.label}{!compatibleFrequencies.includes(f.value) ? ' (incompatible with window)' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          …for the last (window)
          <select value={draft.window} onChange={(e) => setWindow(e.target.value as AlertWindow)}>
            {ALERT_WINDOW_OPTIONS.map((w) => (
              <option key={w.value} value={w.value}>{w.label}</option>
            ))}
          </select>
        </label>
        <small className="full" style={{ color: 'var(--muted)' }}>
          Check every {ALERT_FREQUENCY_OPTIONS.find((f) => f.value === draft.frequency)?.label.toLowerCase()} for
          the last {ALERT_WINDOW_OPTIONS.find((w) => w.value === draft.window)?.label.toLowerCase()}.
        </small>

        <label className="full">
          Bots (leave empty for all bots you own)
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.4rem' }}>
            {bots.length === 0 && <small style={{ color: 'var(--muted)' }}>No bots available.</small>}
            {bots.map((bot) => {
              const selected = draft.filters.bot_ids.includes(bot._id);
              return (
                <button
                  key={bot._id}
                  type="button"
                  onClick={() => toggleBot(bot._id)}
                  className={selected ? 'primary' : undefined}
                  style={{ fontSize: 'var(--font-size-sm)', padding: '0.25rem 0.6rem' }}
                >
                  {bot.name}
                </button>
              );
            })}
          </div>
        </label>

        {supportsOutcomeFilter && (
          <label className="full">
            Call outcome filter (optional)
            <input
              value={draft.filters.call_outcome || ''}
              onChange={(e) => setDraft({ ...draft, filters: { ...draft.filters, call_outcome: e.target.value || undefined } })}
              placeholder="e.g. Approved — leave blank for all outcomes"
            />
          </label>
        )}

        <div className="full" style={{ borderTop: '1px solid var(--border)', paddingTop: '0.6rem', marginTop: '0.3rem' }}>
          <div style={{ fontSize: 'var(--font-size-xs)', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '0.4rem' }}>
            Notify via
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexDirection: 'row' }}>
              <input type="checkbox" checked readOnly style={{ width: 'auto' }} />
              In-app
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexDirection: 'row', color: 'var(--muted)' }}>
              <input type="checkbox" disabled style={{ width: 'auto' }} />
              Email <small>(Coming soon)</small>
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexDirection: 'row', color: 'var(--muted)' }}>
              <input type="checkbox" disabled style={{ width: 'auto' }} />
              Webhook <small>(Coming soon)</small>
            </label>
          </div>
        </div>
      </div>
      {error && <p style={{ color: 'var(--danger)', fontSize: 'var(--font-size-md)', marginTop: '0.5rem' }}>{error}</p>}
    </Dialog>
  );
}

function RulesTab({ rules, bots, loading, onCreate, onUpdate, onToggleEnabled, onDelete }: {
  rules: AlertRule[];
  bots: Bot[];
  loading: boolean;
  onCreate: (payload: AlertRuleInput) => Promise<void>;
  onUpdate: (id: string, payload: AlertRuleInput) => Promise<void>;
  onToggleEnabled: (rule: AlertRule) => Promise<void>;
  onDelete: (rule: AlertRule) => Promise<void>;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [editingRule, setEditingRule] = useState<AlertRule | undefined>(undefined);

  function openCreate() {
    setEditingRule(undefined);
    setShowDialog(true);
  }
  function openEdit(rule: AlertRule) {
    setEditingRule(rule);
    setShowDialog(true);
  }

  if (loading && !rules.length) {
    return (
      <div className="panel" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '160px' }}>
        <Spinner label="Loading alerts" size={20} />
      </div>
    );
  }

  return (
    <div className="table-panel">
      <div className="panel-header">
        <div>
          <h2>Alert rules</h2>
          <p>Fires an in-app alert when a metric crosses a threshold. Checked on the frequency you set below.</p>
        </div>
        <button className="primary" onClick={openCreate} disabled={rules.length >= RULE_LIMIT} title={rules.length >= RULE_LIMIT ? `Limit of ${RULE_LIMIT} reached` : undefined}>
          <Plus size={15} /> Create Alert
        </button>
      </div>

      <div className="table-scroll"><table>
        <thead>
          <tr><th>Name</th><th>Condition</th><th>Window / frequency</th><th>Bots</th><th>Enabled</th><th /></tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <tr key={rule.id}>
              <td><strong style={{ cursor: 'pointer' }} onClick={() => openEdit(rule)}>{rule.name}</strong></td>
              <td><code>{formatCondition(rule.metric, rule.comparator, rule.threshold_value)}</code></td>
              <td><small>every {rule.frequency} / last {rule.window}</small></td>
              <td><small>{rule.filters.bot_ids.length ? `${rule.filters.bot_ids.length} bot(s)` : 'All owned bots'}</small></td>
              <td>
                <input
                  type="checkbox"
                  checked={rule.enabled}
                  onChange={() => onToggleEnabled(rule)}
                  style={{ width: 'auto' }}
                  aria-label={rule.enabled ? 'Disable rule' : 'Enable rule'}
                />
              </td>
              <td>
                <div className="button-row">
                  <button onClick={() => openEdit(rule)}>Edit</button>
                  <button className="danger-button" onClick={() => onDelete(rule)}><Trash2 size={13} /></button>
                </div>
              </td>
            </tr>
          ))}
          {!rules.length && (
            <tr><td colSpan={6}>
              <EmptyState
                icon={<Bell size={32} />}
                heading="No alerts yet"
                description='Click "Create Alert" to get notified when a metric crosses a threshold.'
              />
            </td></tr>
          )}
        </tbody>
      </table></div>

      {showDialog && (
        <CreateAlertDialog
          initial={editingRule}
          bots={bots}
          existingCount={rules.length}
          onClose={() => setShowDialog(false)}
          onSave={(payload) => (editingRule ? onUpdate(editingRule.id, payload) : onCreate(payload))}
        />
      )}
    </div>
  );
}

function HistoryTab({ incidents, loading }: { incidents: AlertIncident[]; loading: boolean }) {
  if (loading && !incidents.length) {
    return (
      <div className="panel" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '160px' }}>
        <Spinner label="Loading alert history" size={20} />
      </div>
    );
  }

  return (
    <div className="table-panel">
      <div className="panel-header">
        <div>
          <h2>Alert history</h2>
          <p>Every time an alert opened or resolved. One row per rule breach — updates in place when it resolves.</p>
        </div>
      </div>
      <div className="table-scroll"><table>
        <thead>
          <tr><th>Active period</th><th>Alert name</th><th>Condition</th><th>Triggered value</th><th>Channel</th></tr>
        </thead>
        <tbody>
          {incidents.map((incident) => (
            <tr key={incident.id}>
              <td>
                <small>
                  <TimeAgo value={incident.triggered_at} />
                  {incident.status === 'resolved' && incident.resolved_at ? <> → <TimeAgo value={incident.resolved_at} /></> : <> → <em>ongoing</em></>}
                </small>
              </td>
              <td><strong>{incident.rule_name}</strong></td>
              <td><code>{formatConditionFromIncident(incident)}</code></td>
              <td><code>{incident.threshold_value} → {incident.current_value}</code></td>
              <td>In-app</td>
            </tr>
          ))}
          {!incidents.length && (
            <tr><td colSpan={5}>
              <EmptyState
                icon={<Bell size={32} />}
                heading="No incidents yet"
                description="Nothing has fired yet. Incidents show up here once an alert rule's condition is met."
              />
            </td></tr>
          )}
        </tbody>
      </table></div>
    </div>
  );
}

function formatConditionFromIncident(incident: AlertIncident): string {
  return `${incident.metric} vs ${incident.threshold_value}`;
}

export function AlertsPanel({ bots, onUnreadCountChange }: { bots: Bot[]; onUnreadCountChange?: (count: number) => void }) {
  const [subTab, setSubTab] = useState<AlertsSubTab>('rules');
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [incidents, setIncidents] = useState<AlertIncident[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [ruleList, incidentList] = await Promise.all([api.listAlertRules(), api.listAlertIncidents()]);
      setRules(ruleList);
      setIncidents(incidentList);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load alerts.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  // Unread badge: count open incidents triggered after the last time the History sub-tab
  // was viewed. Kept as simple local state (localStorage timestamp) per the plan — no
  // server-tracked read/unread flag.
  const unreadCount = useMemo(() => {
    const lastViewed = localStorage.getItem(ALERTS_LAST_VIEWED_HISTORY_KEY);
    const lastViewedMs = lastViewed ? new Date(lastViewed).getTime() : 0;
    return incidents.filter((i) => i.status === 'open' && new Date(i.triggered_at).getTime() > lastViewedMs).length;
  }, [incidents]);

  useEffect(() => {
    onUnreadCountChange?.(unreadCount);
  }, [unreadCount, onUnreadCountChange]);

  function viewHistory() {
    setSubTab('history');
    localStorage.setItem(ALERTS_LAST_VIEWED_HISTORY_KEY, new Date().toISOString());
    onUnreadCountChange?.(0);
  }

  async function handleCreate(payload: AlertRuleInput) {
    const created = await api.createAlertRule(payload);
    setRules((prev) => [...prev, created]);
  }

  async function handleUpdate(id: string, payload: AlertRuleInput) {
    const updated = await api.updateAlertRule(id, payload);
    setRules((prev) => prev.map((r) => (r.id === id ? updated : r)));
  }

  async function handleToggleEnabled(rule: AlertRule) {
    const payload: AlertRuleInput = {
      name: rule.name,
      metric: rule.metric,
      comparator: rule.comparator,
      threshold_value: rule.threshold_value,
      window: rule.window,
      frequency: rule.frequency,
      filters: rule.filters,
      enabled: !rule.enabled,
    };
    setError('');
    try {
      const updated = await api.updateAlertRule(rule.id, payload);
      setRules((prev) => prev.map((r) => (r.id === rule.id ? updated : r)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update this alert.');
    }
  }

  async function handleDelete(rule: AlertRule) {
    if (!window.confirm(`Delete alert "${rule.name}"? This can't be undone.`)) return;
    setError('');
    try {
      await api.deleteAlertRule(rule.id);
      setRules((prev) => prev.filter((r) => r.id !== rule.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete this alert.');
    }
  }

  return (
    <section>
      <div className="library-tabs" style={{ marginBottom: '0.75rem' }}>
        <button
          className={subTab === 'rules' ? 'library-tab active' : 'library-tab'}
          onClick={() => setSubTab('rules')}
        >
          <strong>Rules</strong>
          <small>{rules.length} saved</small>
        </button>
        <button
          className={subTab === 'history' ? 'library-tab active' : 'library-tab'}
          onClick={viewHistory}
        >
          <strong>History</strong>
          <small>
            {incidents.length} incident(s)
            {unreadCount > 0 && subTab !== 'history' ? ` · ${unreadCount} new` : ''}
          </small>
        </button>
      </div>

      {error && <p style={{ color: 'var(--danger)', fontSize: 'var(--font-size-md)' }}>{error}</p>}

      {subTab === 'rules' ? (
        <RulesTab
          rules={rules}
          bots={bots}
          loading={loading}
          onCreate={handleCreate}
          onUpdate={handleUpdate}
          onToggleEnabled={handleToggleEnabled}
          onDelete={handleDelete}
        />
      ) : (
        <HistoryTab incidents={incidents} loading={loading} />
      )}
    </section>
  );
}
