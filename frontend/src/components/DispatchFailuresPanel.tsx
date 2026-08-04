import React, { useEffect, useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { api } from '../api';
import type { DispatchFailure } from '../api';

/** A call whose dispatch LiveKit accepted but never actually handed to any registered
 * worker — distinct from fallback events (a call ran, but on the wrong config) and
 * worker health (a worker process is down). This is "a worker was up and reachable, and
 * the call still never got assigned," which used to be invisible until someone was left
 * staring at an infinite "waiting for bot to join" with nothing to check. */
export function DispatchFailuresPanel() {
  const [failures, setFailures] = useState<DispatchFailure[] | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const res = await api.dispatchFailures({ limit: 20 });
      setFailures(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load dispatch failures');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  if (failures === null && !error && !loading) return null;

  return (
    <section className="panel" style={{ marginTop: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', margin: 0 }}>
          <AlertTriangle size={16} />
          Dispatch failures
        </h3>
        <button className="fallback-button" onClick={load} disabled={loading} title="Refresh">
          <RefreshCw size={14} />
        </button>
      </div>
      <p style={{ fontSize: '0.85rem', color: 'var(--muted)', marginTop: 0 }}>
        Calls where LiveKit accepted the dispatch but never handed it to any registered worker —
        this is why a test call can sit on "waiting for bot to join" forever with the worker
        itself perfectly healthy. Distinct from worker health (process down) and fallback events
        (ran, but on the wrong config).
      </p>
      {error && <p style={{ color: 'var(--error, #c0392b)' }}>{error}</p>}
      {failures && failures.length === 0 && !error && (
        <p style={{ fontSize: '0.85rem', color: 'var(--muted)' }}>None recorded — good sign.</p>
      )}
      {failures && failures.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: '0.82rem', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--muted)' }}>
                <th style={{ padding: '0.3rem 0.5rem' }}>When</th>
                <th style={{ padding: '0.3rem 0.5rem' }}>Room</th>
                <th style={{ padding: '0.3rem 0.5rem' }}>Agent name</th>
                <th style={{ padding: '0.3rem 0.5rem' }}>Timeout</th>
              </tr>
            </thead>
            <tbody>
              {failures.map((f) => (
                <tr key={f._id} style={{ borderTop: '1px solid var(--border, #e5e5e5)' }}>
                  <td style={{ padding: '0.3rem 0.5rem', whiteSpace: 'nowrap' }}>
                    {new Date(f.created_at).toLocaleString()}
                  </td>
                  <td style={{ padding: '0.3rem 0.5rem', fontFamily: 'monospace' }}>{f.room_name}</td>
                  <td style={{ padding: '0.3rem 0.5rem', fontFamily: 'monospace' }}>{f.agent_name || '—'}</td>
                  <td style={{ padding: '0.3rem 0.5rem' }}>{f.timeout_seconds}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
