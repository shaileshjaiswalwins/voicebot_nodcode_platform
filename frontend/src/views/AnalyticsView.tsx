import React, { useState, useEffect } from 'react';
import { AlertTriangle, BarChart2, HelpCircle, RefreshCw } from 'lucide-react';
import type { Bot as BotType, Campaign, OutcomeAnalytics, QualityAlert } from '../api';
import { api } from '../api';
import { useCountUp } from '../hooks/useCountUp';
import { StatusPill } from '../components/StatusPill';
import { Detail } from '../components/Detail';
import { ErrorState } from '../components/ErrorState';
import { EmptyState } from '../components/EmptyState';
import { Tooltip } from '../components/Tooltip';

export function AnalyticsView({ bots, campaigns, onGoToAgents }: { bots: BotType[]; campaigns: Campaign[]; onGoToAgents?: () => void }) {
  const [analytics, setAnalytics] = useState<OutcomeAnalytics | null>(null);
  const [alert, setAlert] = useState<QualityAlert | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [botId, setBotId] = useState('');
  const [campaignId, setCampaignId] = useState('');
  const [hours, setHours] = useState<number | ''>('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [qualityWindowHours, setQualityWindowHours] = useState(1);

  async function load(qHours: number = qualityWindowHours) {
    setLoading(true);
    setError('');
    try {
      const params: Parameters<typeof api.outcomeAnalytics>[0] = {};
      if (botId) params.bot_id = botId;
      if (campaignId) params.campaign_id = campaignId;
      if (hours) params.hours = Number(hours);
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;
      const [a, q] = await Promise.all([
        api.outcomeAnalytics(params),
        api.qualityAlerts(qHours, 30),
      ]);
      setAnalytics(a);
      setAlert(q);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load analytics.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function onQualityWindowChange(next: number) {
    setQualityWindowHours(next);
    load(next);
  }

  const statusEntries = analytics ? Object.entries(analytics.by_status).sort((a, b) => b[1] - a[1]) : [];
  const outcomeEntries = analytics ? Object.entries(analytics.by_outcome).sort((a, b) => b[1] - a[1]) : [];
  const total = analytics?.total || 0;

  const { value: animatedTotal, ref: totalRef } = useCountUp(total);
  const { value: animatedNatural, ref: naturalRef } = useCountUp(analytics?.ended_naturally || 0);

  return (
    <section className="content-grid two-col">
      <div className="panel">
        <div className="panel-header">
          <div><h2>Call outcome analytics</h2><p>Aggregated over selected time window.</p></div>
          <button onClick={() => load()} disabled={loading}><RefreshCw size={14} /> {loading ? 'Loading…' : 'Refresh'}</button>
        </div>

        {alert?.alert && (
          <div className="callout" style={{ background: 'rgba(239,68,68,0.1)', borderColor: 'var(--error)', marginBottom: '12px' }}>
            <AlertTriangle size={18} style={{ color: 'var(--error)' }} />
            <strong style={{ color: 'var(--error)' }}>Quality alert:</strong> {alert.message}
          </div>
        )}

        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '14px' }}>
          <select value={botId} onChange={e => setBotId(e.target.value)} style={{ fontSize: 'var(--font-size-md)' }}>
            <option value="">All bots</option>
            {bots.map(b => <option key={b._id} value={b._id}>{b.name}</option>)}
          </select>
          <select value={campaignId} onChange={e => setCampaignId(e.target.value)} style={{ fontSize: 'var(--font-size-md)' }}>
            <option value="">All campaigns</option>
            {campaigns.map(c => <option key={c._id} value={c.campaign_key}>{c.name}</option>)}
          </select>
          <select value={hours} onChange={e => setHours(e.target.value === '' ? '' : Number(e.target.value))} style={{ fontSize: 'var(--font-size-md)' }}>
            <option value="">Custom date range</option>
            <option value={1}>Last 1 hour</option>
            <option value={6}>Last 6 hours</option>
            <option value={24}>Last 24 hours</option>
            <option value={168}>Last 7 days</option>
            <option value={720}>Last 30 days</option>
          </select>
          {!hours && (
            <>
              <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} style={{ fontSize: 'var(--font-size-md)' }} />
              <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} style={{ fontSize: 'var(--font-size-md)' }} />
            </>
          )}
          <button onClick={() => load()} disabled={loading}>Apply</button>
        </div>

        {error && !loading && (
          <ErrorState heading="Couldn't load analytics" description={error} onRetry={load} />
        )}

        {analytics && !error && (
          <>
            <div className="metric-board" style={{ marginBottom: '16px' }}>
              <div className="metric" ref={totalRef as React.RefObject<HTMLDivElement>}>
                <span>Total calls</span>
                <strong>{animatedTotal.toLocaleString('en-IN')}</strong>
              </div>
              <div className="metric" ref={naturalRef as React.RefObject<HTMLDivElement>}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                  Ended naturally
                  <Tooltip label="Calls that completed normally (status = completed or the call ended via a natural close-out), as opposed to a disconnect, error, or hangup.">
                    <HelpCircle size={12} style={{ color: 'var(--muted)', cursor: 'help' }} />
                  </Tooltip>
                </span>
                <strong>{animatedNatural} <small style={{ fontWeight: 400, fontSize: 'var(--font-size-sm)' }}>({total ? Math.round(analytics.ended_naturally / total * 100) : 0}%)</small></strong>
              </div>
              <div className="metric">
                <span>Avg duration</span>
                <strong>{analytics.avg_duration_sec}s</strong>
              </div>
            </div>

            {total === 0 ? (
              <EmptyState
                icon={<BarChart2 size={32} />}
                heading="No calls in this window"
                description="Outcome and status breakdowns appear here once at least one call has been made — try a test call, or widen the date range above."
                action={onGoToAgents ? { label: 'Go make a test call', onClick: onGoToAgents } : undefined}
              />
            ) : (
              <>
                <h3 style={{ fontSize: 'var(--font-size-lg)', color: 'var(--text-secondary)', marginBottom: '8px' }}>By status</h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '16px' }}>
                  {statusEntries.map(([status, count]) => (
                    <div key={status} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <StatusPill value={status} />
                      <div style={{ flex: 1, background: 'var(--bg-tertiary)', borderRadius: '3px', height: '8px', overflow: 'hidden' }}>
                        <div style={{ width: `${total ? count / total * 100 : 0}%`, background: 'var(--accent)', height: '100%', transition: 'width 0.3s' }} />
                      </div>
                      <span style={{ fontSize: 'var(--font-size-md)', minWidth: '50px', textAlign: 'right' }}>{count} ({total ? Math.round(count / total * 100) : 0}%)</span>
                    </div>
                  ))}
                </div>

                {outcomeEntries.length > 0 && (
                  <>
                    <h3 style={{ fontSize: 'var(--font-size-lg)', color: 'var(--text-secondary)', marginBottom: '8px' }}>By outcome tag</h3>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      {outcomeEntries.map(([outcome, count]) => (
                        <div key={outcome} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                          <span style={{ minWidth: '140px', fontSize: 'var(--font-size-md)' }}>{outcome}</span>
                          <div style={{ flex: 1, background: 'var(--bg-tertiary)', borderRadius: '3px', height: '8px', overflow: 'hidden' }}>
                            <div style={{ width: `${total ? count / total * 100 : 0}%`, background: 'var(--success)', height: '100%', transition: 'width 0.3s' }} />
                          </div>
                          <span style={{ fontSize: 'var(--font-size-md)', minWidth: '40px', textAlign: 'right' }}>{count}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </>
            )}
          </>
        )}
        {loading && !analytics && <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-lg)' }}>Loading…</p>}
      </div>

      <div className="panel">
        <h2>Quality monitoring</h2>
        <p style={{ fontSize: 'var(--font-size-lg)', color: 'var(--text-secondary)', marginBottom: '16px' }}>
          Alert fires when disconnected/error calls exceed 30% of calls in the selected window (min 5 calls).
        </p>
        <select
          value={qualityWindowHours}
          onChange={(e) => onQualityWindowChange(Number(e.target.value))}
          style={{ fontSize: 'var(--font-size-md)', marginBottom: '16px' }}
        >
          <option value={1}>Last 1 hour</option>
          <option value={24}>Last 24 hours</option>
          <option value={168}>Last 7 days</option>
        </select>
        {error && !loading && (
          <p style={{ fontSize: 'var(--font-size-lg)', color: 'var(--danger)' }}>Unavailable — quality alerts load together with analytics above.</p>
        )}
        {alert && !error && (
          <div className="detail-list">
            <Detail label="Window" value={`Last ${alert.hours}h`} />
            <Detail label="Total calls" value={String(alert.total_calls)} />
            <Detail label="Bad calls" value={String(alert.bad_calls)} />
            <Detail label="Bad rate" value={`${alert.bad_pct}%`} />
            <div className="detail">
              <span>Status</span>
              <StatusPill value={alert.alert ? 'alert' : (alert.total_calls === 0 ? 'no-data' : 'ok')} />
            </div>
            {alert.total_calls === 0 && (
              <p style={{ fontSize: 'var(--font-size-md)', color: 'var(--text-secondary)', marginTop: '8px' }}>
                No calls in this window — try a wider window above.
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
