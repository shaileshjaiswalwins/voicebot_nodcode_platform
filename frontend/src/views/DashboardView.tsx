import React, { useEffect, useRef, useState } from 'react';
import { Activity, Bot, LayoutDashboard, TrendingDown, TrendingUp } from 'lucide-react';
import type { DashboardSummary } from '../api';
import { api } from '../api';
import { ErrorState } from '../components/ErrorState';
import { EmptyState } from '../components/EmptyState';
import { MiniBarChart } from '../components/MiniCharts';

const REFRESH_MS = 60_000;

function useNowTicker() {
  const [, force] = useState(0);
  useEffect(() => {
    const id = setInterval(() => force(n => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
}

/** Compact "not enough data" placeholder for a single card slot — same icon/heading/description
 * shape as EmptyState, just sized down to sit inside a half-width detail-list panel instead of
 * taking the full-panel EmptyState's padding. */
function CardEmptyState({ text }: { text: string }) {
  return (
    <div style={{ textAlign: 'center', padding: '0.75rem 0.5rem', color: 'var(--muted)' }}>
      <p style={{ fontSize: '0.82rem', margin: 0 }}>{text}</p>
    </div>
  );
}

export function DashboardView({ onGoToAgents }: { onGoToAgents?: () => void }) {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastRefreshedAt, setLastRefreshedAt] = useState<number | null>(null);
  const mountedRef = useRef(true);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const data = await api.dashboardSummary();
      if (!mountedRef.current) return;
      setSummary(data);
      setLastRefreshedAt(Date.now());
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load dashboard.');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    mountedRef.current = true;
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(id);
    };
  }, []);

  useNowTicker();

  if (error && !summary) {
    return <ErrorState heading="Couldn't load dashboard" description={error} onRetry={load} />;
  }

  const secondsAgo = lastRefreshedAt ? Math.max(0, Math.round((Date.now() - lastRefreshedAt) / 1000)) : null;

  return (
    <section className="content-grid">
      <div className="panel">
        <div className="panel-header">
          <div>
            <h2><LayoutDashboard size={16} style={{ verticalAlign: '-2px', marginRight: '6px' }} />Overview</h2>
            <p>Across all active agents. Auto-refreshes every 60s.</p>
          </div>
          <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
            {loading ? 'Refreshing…' : secondsAgo !== null ? `Updated ${secondsAgo}s ago` : ''}
          </span>
        </div>

        {summary && summary.total_agents === 0 && (
          <EmptyState
            icon={<Bot size={32} />}
            heading="No agents yet"
            description="Create your first voice agent to start seeing call volume, minutes, and performance rankings here."
            action={onGoToAgents ? { label: 'Create an agent', onClick: onGoToAgents } : undefined}
          />
        )}

        {summary && summary.total_agents > 0 && (
          <>
            <div className="metric-board" style={{ marginBottom: '16px' }}>
              <div className="metric">
                <span>Total agents</span>
                <strong>{summary.total_agents}</strong>
              </div>
              <div className="metric">
                <span>Total calls (all-time)</span>
                <strong>{summary.total_calls_all_time.toLocaleString('en-IN')}</strong>
              </div>
              <div className="metric">
                <span>Total minutes (all-time)</span>
                <strong>{summary.total_minutes_all_time.toLocaleString('en-IN')}</strong>
              </div>
              <div className="metric">
                <span>Calls today</span>
                <strong>{summary.calls_today}</strong>
              </div>
            </div>

            {summary.total_calls_all_time === 0 ? (
              <EmptyState
                icon={<Activity size={32} />}
                heading="No calls yet"
                description="Once agents start taking calls, daily volume and performance rankings will appear here — try a test call from the Agents page."
                action={onGoToAgents ? { label: 'Go make a test call', onClick: onGoToAgents } : undefined}
              />
            ) : (
              <>
                <div className="content-grid two-col" style={{ marginBottom: '16px' }}>
                  <div className="detail-list">
                    <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                      <TrendingUp size={13} style={{ verticalAlign: '-2px', marginRight: '4px' }} />Best performing agent
                    </h3>
                    {summary.best_performing_bot ? (
                      <>
                        <div className="detail"><span>Name</span><strong>{summary.best_performing_bot.name || '(unnamed)'}</strong></div>
                        <div className="detail"><span>Success rate</span><strong>{summary.best_performing_bot.success_rate_pct}%</strong></div>
                        <div className="detail"><span>Calls</span><strong>{summary.best_performing_bot.call_count}</strong></div>
                      </>
                    ) : (
                      <CardEmptyState text="Not enough data yet — needs at least 2 agents with 5+ calls each." />
                    )}
                  </div>

                  <div className="detail-list">
                    <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                      <TrendingDown size={13} style={{ verticalAlign: '-2px', marginRight: '4px' }} />Least performing agent
                    </h3>
                    {summary.least_performing_bot ? (
                      <>
                        <div className="detail"><span>Name</span><strong>{summary.least_performing_bot.name || '(unnamed)'}</strong></div>
                        <div className="detail"><span>Success rate</span><strong>{summary.least_performing_bot.success_rate_pct}%</strong></div>
                        <div className="detail"><span>Calls</span><strong>{summary.least_performing_bot.call_count}</strong></div>
                      </>
                    ) : (
                      <CardEmptyState text="Not enough data yet — needs at least 2 agents with 5+ calls each." />
                    )}
                  </div>
                </div>

                <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>Daily call volume (last 14 days)</h3>
                <MiniBarChart data={summary.daily_volume.map(d => ({ label: d.date.slice(5), value: d.count }))} />
              </>
            )}
          </>
        )}
        {loading && !summary && <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Loading…</p>}
      </div>
    </section>
  );
}
