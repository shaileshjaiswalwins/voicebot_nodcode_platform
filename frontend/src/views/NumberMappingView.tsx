import React, { useMemo } from 'react';
import { Bot, Unlink } from 'lucide-react';
import type { Bot as BotType, NumberMapping } from '../api';
import { SkeletonTableBody } from '../components/SkeletonTableBody';
import { EmptyState } from '../components/EmptyState';
import { Spinner } from '../components/Spinner';

export function NumberMappingView({
  mappings,
  bots,
  loading,
  onMap,
  mapState,
  onGoToPhoneNumbers,
}: {
  mappings: NumberMapping[];
  bots: BotType[];
  loading?: boolean;
  onMap: (botId: string, phoneNumber: string | null) => void;
  mapState?: Record<string, 'idle' | 'running' | 'failed'>;
  /** Jumps to the Phone Numbers view — that's where a number's SIP trunk/provisioning
   * details live, and where it must first be added before it can be mapped here. */
  onGoToPhoneNumbers?: () => void;
}) {
  // A number belongs to at most one agent, so a given agent's options are the free
  // numbers plus the one it already holds.
  const numberByBotId = useMemo(
    () => new Map(mappings.filter((m) => m.bot_id).map((m) => [m.bot_id as string, m])),
    [mappings],
  );
  const freeNumbers = useMemo(() => mappings.filter((m) => !m.bot_id), [mappings]);

  return (
    <section className="content-grid">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Number mapping</h2>
            <p>Give each agent a number to answer on. One number belongs to one agent — this is what actually decides which bot picks up an inbound call.</p>
            <p className="muted" style={{ fontSize: 'var(--font-size-md)', marginTop: '0.2rem' }}>
              Need to add a new number or edit its SIP trunk details first? Do that on{' '}
              {onGoToPhoneNumbers ? (
                <button
                  type="button"
                  onClick={onGoToPhoneNumbers}
                  style={{ border: 'none', background: 'none', padding: 0, minHeight: 0, font: 'inherit', color: 'var(--accent)', textDecoration: 'underline', cursor: 'pointer' }}
                >
                  Phone Numbers
                </button>
              ) : (
                <strong>Phone Numbers</strong>
              )}.
            </p>
          </div>
        </div>

        <div className="table-scroll"><table>
          <thead>
            <tr><th>Bot name</th><th>Agent name</th><th>Number</th><th>Environment</th><th>Worker pool</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {loading && !bots.length ? (
              <SkeletonTableBody cols={6} rows={3} />
            ) : !bots.length ? (
              <tr><td colSpan={6}>
                <EmptyState
                  icon={<Bot size={32} />}
                  heading="No agents yet"
                  description="Create a voice agent first, then map a number to it here."
                />
              </td></tr>
            ) : bots.map((bot) => {
              const current = numberByBotId.get(bot._id);
              const options = current ? [current, ...freeNumbers] : freeNumbers;
              return (
                <tr key={bot._id}>
                  <td style={{ fontWeight: 700 }}>{bot.name}</td>
                  <td>
                    {bot.agent_name
                      ? bot.agent_name
                      : <small className="muted">—</small>}
                  </td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                      <select
                        value={current?.phone_number || ''}
                        disabled={mapState?.[bot._id] === 'running'}
                        onChange={(e) => onMap(bot._id, e.target.value || null)}
                      >
                        {/* No "unassign" entry here — clearing is the Unassign button's job.
                            The empty option only exists as the placeholder before a number is picked. */}
                        {!current && <option value="">— no number —</option>}
                        {options.map((m) => (
                          <option key={m.phone_number} value={m.phone_number}>{m.phone_number}</option>
                        ))}
                      </select>
                      {mapState?.[bot._id] === 'running' && <Spinner label="Saving" size={13} />}
                    </div>
                  </td>
                  <td>{current ? <span className="pill">{current.environment}</span> : <small className="muted">—</small>}</td>
                  <td><small className="muted">{current?.livekit_agent_name || '—'}</small></td>
                  <td>
                    {current ? (
                      <button
                        className="fallback-button"
                        disabled={mapState?.[bot._id] === 'running'}
                        onClick={() => onMap(bot._id, null)}
                        title={`Unassign ${current.phone_number} from ${bot.name}`}
                      >
                        <Unlink size={13} /> Unassign
                      </button>
                    ) : (
                      <small className="muted">—</small>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table></div>
      </div>
    </section>
  );
}
