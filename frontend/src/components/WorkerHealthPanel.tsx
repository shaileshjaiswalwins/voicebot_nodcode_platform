import React, { useEffect, useState } from 'react';
import { Activity, RefreshCw } from 'lucide-react';
import { api } from '../api';
import type { WorkerHealth } from '../api';

const REFRESH_INTERVAL_MS = 15000;

/** Liveness of every LiveKit worker process that has ever registered a heartbeat
 * (bot.py's start_worker_heartbeat) — a worker that's crashed or lost its network path
 * shows up red here, with how long it's been silent, instead of only being discoverable
 * by placing a real call and watching it hang on "waiting for bot to join". */
export function WorkerHealthPanel() {
  const [workers, setWorkers] = useState<WorkerHealth[] | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    try {
      setWorkers(await api.workerHealth());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load worker health');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const id = window.setInterval(load, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  if (workers === null && !error && !loading) return null;

  return (
    <section className="panel" style={{ marginTop: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', margin: 0 }}>
          <Activity size={16} />
          Worker health
        </h3>
        <button className="fallback-button" onClick={load} disabled={loading} title="Refresh">
          <RefreshCw size={14} />
        </button>
      </div>
      <p style={{ fontSize: '0.85rem', color: 'var(--muted)', marginTop: 0 }}>
        Whether a LiveKit worker process is actually alive and reachable, per agent name — check this
        <em> before</em> placing a test call if you want to rule out "nothing is listening" as the cause
        of a stuck call.
      </p>
      {error && <p style={{ color: 'var(--error, #c0392b)' }}>{error}</p>}
      {workers && workers.length === 0 && !error && (
        <p style={{ fontSize: '0.85rem', color: 'var(--muted)' }}>
          No worker has ever reported a heartbeat yet.
        </p>
      )}
      {workers && workers.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: '0.82rem', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--muted)' }}>
                <th style={{ padding: '0.3rem 0.5rem' }}>Agent name</th>
                <th style={{ padding: '0.3rem 0.5rem' }}>Status</th>
                <th style={{ padding: '0.3rem 0.5rem' }}>Last seen</th>
                <th style={{ padding: '0.3rem 0.5rem' }}>PID / host</th>
              </tr>
            </thead>
            <tbody>
              {workers.map((w) => (
                <tr key={w.agent_name} style={{ borderTop: '1px solid var(--border, #e5e5e5)' }}>
                  <td style={{ padding: '0.3rem 0.5rem', fontFamily: 'monospace' }}>{w.agent_name}</td>
                  <td style={{ padding: '0.3rem 0.5rem' }}>
                    <span
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
                        color: w.stale ? 'var(--danger, #c0392b)' : 'var(--success, #2e7d32)',
                        fontWeight: 600,
                      }}
                    >
                      <span style={{
                        width: '8px', height: '8px', borderRadius: '50%',
                        background: w.stale ? 'var(--danger, #c0392b)' : 'var(--success, #2e7d32)',
                        display: 'inline-block',
                      }} />
                      {w.stale ? 'Not responding' : 'Alive'}
                    </span>
                  </td>
                  <td style={{ padding: '0.3rem 0.5rem', whiteSpace: 'nowrap' }}>
                    {w.age_seconds !== null ? `${w.age_seconds}s ago` : '—'}
                    {' · '}
                    {new Date(w.last_seen).toLocaleString()}
                  </td>
                  <td style={{ padding: '0.3rem 0.5rem', fontFamily: 'monospace' }}>
                    {w.pid ?? '—'} @ {w.host || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
