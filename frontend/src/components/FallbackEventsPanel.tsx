import React, { useEffect, useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { api } from '../api';
import type { FallbackEvent } from '../api';

// Human-readable labels for bot.py's FALLBACK_REASON_* constants — keep in sync with that
// module; an unrecognized reason string still renders (falls back to the raw value) rather
// than breaking the panel, since a code-side rename here shouldn't be able to hide events.
const REASON_LABELS: Record<string, string> = {
  no_ids_in_room_metadata: 'No bot_id/version in room metadata (not a dashboard test call, or metadata was lost)',
  malformed_ids: 'bot_id/version in room metadata was not a valid Mongo ID',
  platform_db_unreachable: 'Could not reach the platform database (Mongo network issue)',
  version_not_found: 'No matching bot version found for that bot_id/version pair',
  version_doc_has_no_config: 'The bot version document exists but its config field was empty',
};

/** Every call that ran on the hardcoded default assistant instead of the bot you actually
 * configured, with why — see bot.py's record_fallback_event(). Added after a session where
 * three unrelated causes (a stale duplicate worker, a worker assignment timeout, and this
 * function's own None-returns) all produced the identical "hardcoded Simran bot" symptom,
 * and telling them apart required SSHing into workers and grepping raw log files. */
export function FallbackEventsPanel() {
  const [events, setEvents] = useState<FallbackEvent[] | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const res = await api.fallbackEvents({ limit: 20 });
      setEvents(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load fallback events');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  if (events === null && !error && !loading) return null;

  return (
    <section className="panel" style={{ marginTop: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', margin: 0 }}>
          <AlertTriangle size={16} />
          Bot config fallback events
        </h3>
        <button className="fallback-button" onClick={load} disabled={loading} title="Refresh">
          <RefreshCw size={14} />
        </button>
      </div>
      <p style={{ fontSize: '0.85rem', color: 'var(--muted)', marginTop: 0 }}>
        Calls that ran on the hardcoded default assistant instead of the bot you actually configured —
        this happens when a test call's bot_id/version couldn't be resolved (missing metadata, a bad ID,
        or the platform database being briefly unreachable). It is <em>not</em> caused by which LiveKit
        worker picked up the call — a worker either runs your bot's real config or this fallback; it
        doesn't matter which process answered.
      </p>
      {error && <p style={{ color: 'var(--error, #c0392b)' }}>{error}</p>}
      {events && events.length === 0 && !error && (
        <p style={{ fontSize: '0.85rem', color: 'var(--muted)' }}>None recorded — good sign.</p>
      )}
      {events && events.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: '0.82rem', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--muted)' }}>
                <th style={{ padding: '0.3rem 0.5rem' }}>When</th>
                <th style={{ padding: '0.3rem 0.5rem' }}>Room</th>
                <th style={{ padding: '0.3rem 0.5rem' }}>Reason</th>
                <th style={{ padding: '0.3rem 0.5rem' }}>Worker</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e._id} style={{ borderTop: '1px solid var(--border, #e5e5e5)' }}>
                  <td style={{ padding: '0.3rem 0.5rem', whiteSpace: 'nowrap' }}>
                    {new Date(e.created_at).toLocaleString()}
                  </td>
                  <td style={{ padding: '0.3rem 0.5rem', fontFamily: 'monospace' }}>{e.room_name}</td>
                  <td style={{ padding: '0.3rem 0.5rem' }} title={e.reason}>
                    {REASON_LABELS[e.reason] || e.reason}
                  </td>
                  <td style={{ padding: '0.3rem 0.5rem', fontFamily: 'monospace' }}>{e.worker || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
