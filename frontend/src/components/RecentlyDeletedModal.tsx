import React, { useState } from 'react';
import { RotateCcw, X } from 'lucide-react';
import type { Bot } from '../api';
import { api } from '../api';
import { TimeAgo } from './TimeAgo';
import { Spinner } from './Spinner';

/** Soft-deleted bots (status='deleted', see backend/routers/bots.py delete_bot) are never
 * actually removed — this just surfaces them with a Restore action, the mirror-image of the
 * Agents list's own list_bots query. */
export function RecentlyDeletedModal({
  bots,
  onClose,
  onRestored,
}: {
  bots: Bot[] | null;
  onClose: () => void;
  onRestored: (botId: string) => void;
}) {
  const [restoringId, setRestoringId] = useState('');
  const [error, setError] = useState('');

  async function handleRestore(bot: Bot) {
    setRestoringId(bot._id);
    setError('');
    try {
      await api.restoreBot(bot._id);
      onRestored(bot._id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Restore failed.');
    } finally {
      setRestoringId('');
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel" style={{ maxWidth: 560 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Recently Deleted</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        {/* .modal-body is what actually scrolls — .modal-panel is overflow:hidden and only
            scrolls this child (styles.css). Without the wrapper a long deleted-agents list
            is simply clipped at the panel edge with no way to reach the rest. */}
        <div className="modal-body">
          <p className="muted" style={{ fontSize: 'var(--font-size-lg)', marginTop: '-0.4rem' }}>
            Deleted agents are kept — nothing is permanently removed. Restore brings an agent back to the active list.
          </p>
          {error && <div className="notice error" role="alert" style={{ margin: '0.5rem 0' }}>{error}</div>}
          {bots === null ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem' }}><Spinner /></div>
          ) : bots.length === 0 ? (
            <p className="muted" style={{ fontSize: 'var(--font-size-lg)' }}>Nothing here — no deleted agents.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem' }}>
              {bots.map((bot) => (
                <div
                  key={bot._id}
                  style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    border: '1px solid var(--border)', borderRadius: 6, padding: '0.5rem 0.75rem',
                  }}
                >
                  <div>
                    <strong style={{ fontSize: 'var(--font-size-lg)' }}>{bot.name}</strong>
                    <div className="muted" style={{ fontSize: 'var(--font-size-sm)' }}>
                      Deleted <TimeAgo value={bot.deleted_at || bot.updated_at} />
                    </div>
                  </div>
                  <button disabled={restoringId === bot._id} onClick={() => handleRestore(bot)}>
                    <RotateCcw size={13} /> {restoringId === bot._id ? 'Restoring…' : 'Restore'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
