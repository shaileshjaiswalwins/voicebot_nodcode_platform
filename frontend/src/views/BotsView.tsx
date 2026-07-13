import React, { useState } from 'react';
import { AlertTriangle, Bot, FileText, Headphones, Pencil, Plus, Rocket, ShieldCheck, Trash2 } from 'lucide-react';
import type { Bot as BotType, Campaign, LanguageOption } from '../api';
import type { Transcript } from '../api';
import { StatusPill } from '../components/StatusPill';
import { CopyableId } from '../components/CopyableId';
import { TimeAgo } from '../components/TimeAgo';
import { Detail } from '../components/Detail';
import { SkeletonTableBody } from '../components/SkeletonTableBody';
import { EmptyState } from '../components/EmptyState';
import { Dialog } from '../components/Dialog';
import { Spinner } from '../components/Spinner';
import { shortId } from '../utils/formatting';

export function BotsView({ bots, selectedBot, transcripts, loading, onSelect, onEdit, onDelete, onNew }: {
  bots: BotType[];
  selectedBot?: BotType;
  transcripts: Transcript[];
  loading?: boolean;
  onSelect: (botId: string) => void;
  onEdit: (botId: string) => void;
  onDelete: (bot: BotType) => void;
  onNew?: () => void;
}) {
  return (
    <section className="content-grid two-col">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Agents</h2>
            <p>Each agent owns its prompt, voice, language, versions, and runtime settings.</p>
          </div>
          {onNew && bots.length > 0 && (
            <button className="primary" onClick={onNew}><Plus size={15} /> New agent</button>
          )}
        </div>
        <div className="table-scroll"><table>
          <thead>
            <tr><th>Name</th><th>Status</th><th>Assistant</th><th>Updated</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {loading && !bots.length ? (
              <SkeletonTableBody cols={5} rows={4} />
            ) : bots.length === 0 ? (
              <tr><td colSpan={5}>
                <EmptyState
                  icon={<Bot size={32} />}
                  heading="No agents yet"
                  description="Create your first voice agent to get started. Each agent has its own prompt, voice, and published versions."
                  action={onNew ? { label: 'Create first agent', onClick: onNew } : undefined}
                />
              </td></tr>
            ) : bots.map((bot) => (
              <tr key={bot._id} onClick={() => onSelect(bot._id)} className={bot._id === selectedBot?._id ? 'selected-row' : ''}>
                <td>
                  <strong>{bot.name}</strong>
                  <small>{bot.description || 'Prompt + settings agent'}</small>
                </td>
                <td><StatusPill value={bot.status} /></td>
                <td><CopyableId value={bot.assistant_id} /></td>
                <td><TimeAgo value={bot.updated_at} /></td>
                <td>
                  <div className="table-actions">
                    <button onClick={(event) => { event.stopPropagation(); onEdit(bot._id); }}><Pencil size={13} /> Edit</button>
                    <button className="danger-button" onClick={(event) => { event.stopPropagation(); onDelete(bot); }}><Trash2 size={14} /> Delete</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
      <div className="panel">
        <div className="agent-profile">
          <div className="agent-avatar"><Headphones size={28} /></div>
          <div>
            <h2>{selectedBot?.name || 'No agent selected'}</h2>
            <p>{selectedBot?.description || 'Open an agent to edit its draft or test a browser call.'}</p>
          </div>
        </div>
        <div className="detail-list">
          <Detail label="Assistant ID" value={selectedBot?.assistant_id || '-'} />
          <Detail label="Active version" value={selectedBot?.active_version_id ? shortId(selectedBot.active_version_id) : '-'} />
          <Detail label="Owner" value={selectedBot?.owner || 'Unknown'} />
          <Detail label="Calls stored" value={transcripts.filter((item) => item.bot_id === selectedBot?._id).length.toString()} />
        </div>
        <div className="callout success">
          <ShieldCheck size={18} />
          Live calls keep their original published config snapshot. Publishing changes affects only new calls.
        </div>
        {selectedBot && (
          <div className="button-row">
            <button className="primary" onClick={() => onEdit(selectedBot._id)}><Pencil size={15} /> Edit agent</button>
            <button className="danger-button" onClick={() => onDelete(selectedBot)}><Trash2 size={16} /> Delete agent</button>
          </div>
        )}
      </div>
    </section>
  );
}

export function NewAgentWizard({
  form,
  onChange,
  languages,
  busy,
  onCancel,
  onConfirm
}: {
  form: {
    name: string;
    description: string;
    agent_name: string;
    organization_name: string;
    language: string;
    initial_message: string;
  };
  onChange: (value: typeof form) => void;
  languages: LanguageOption[];
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const canCreate = form.name.trim().length > 0 && form.agent_name.trim().length > 0;
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    onChange({ ...form, [key]: e.target.value });

  const previewOpening = form.initial_message
    .replace('{agent_name}', form.agent_name || '<agent_name>')
    .replace('{organization_name}', form.organization_name || '<org_name>')
    .replace('{product}', 'air conditioner');

  return (
    <Dialog
      title="New voice agent"
      icon={<Rocket size={17} />}
      maxWidth={560}
      onClose={onCancel}
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
      footer={
        <>
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary" onClick={onConfirm} disabled={!canCreate || busy}>
            {busy ? <Spinner label="Creating" size={13} /> : <Rocket size={15} />} {busy ? 'Creating…' : 'Create agent'}
          </button>
        </>
      }
    >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div className="form-grid">
            <label>
              Display name <span style={{ color: 'var(--danger)' }}>*</span>
              <input value={form.name} onChange={set('name')} placeholder="e.g. JD Outbound — Hindi" autoFocus />
            </label>
            <label>
              Description
              <input value={form.description} onChange={set('description')} placeholder="Short note (optional)" />
            </label>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Persona</div>
            <div className="form-grid">
              <label>
                Agent persona name <span style={{ color: 'var(--danger)' }}>*</span>
                <input value={form.agent_name} onChange={set('agent_name')} placeholder="e.g. Tarun, Priya, Aman" />
                <small>Used in the opening line and system prompt.</small>
              </label>
              <label>
                Organization name
                <input value={form.organization_name} onChange={set('organization_name')} placeholder="e.g. JustDial" />
              </label>
            </div>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Language</div>
            <div className="form-grid">
              <label>
                Language
                <select value={form.language} onChange={set('language')}>
                  {languages.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                </select>
                <small>Runtime currently always uses Sarvam STT/TTS in Hindi regardless of this setting.</small>
              </label>
            </div>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Opening line</div>
            <label>
              <textarea
                value={form.initial_message}
                onChange={set('initial_message')}
                rows={2}
                style={{ fontFamily: 'inherit', fontSize: '0.85rem' }}
              />
              <small>Use <code style={{ fontSize: '0.75rem' }}>{'{product}'}</code>, <code style={{ fontSize: '0.75rem' }}>{'{agent_name}'}</code>, <code style={{ fontSize: '0.75rem' }}>{'{organization_name}'}</code> as placeholders.</small>
            </label>
            {previewOpening && (
              <div style={{ background: 'var(--surface-2)', borderRadius: '6px', padding: '0.5rem 0.75rem', fontSize: '0.82rem', fontStyle: 'italic', marginTop: '0.4rem', color: 'var(--text-2)' }}>
                Preview: "{previewOpening}"
              </div>
            )}
          </div>
        </div>
    </Dialog>
  );
}

export function DeleteAgentDialog({
  bot,
  campaigns,
  transcriptCount,
  busy,
  onCancel,
  onConfirm
}: {
  bot: BotType;
  campaigns: Campaign[];
  transcriptCount: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [confirmText, setConfirmText] = useState('');
  const canDelete = confirmText.trim() === bot.name;
  const assignedCampaigns = campaigns.filter(c => c.bot_id === bot._id);

  return (
    <Dialog
      title="Delete agent?"
      icon={<AlertTriangle size={17} />}
      tone="danger"
      onClose={onCancel}
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
      footer={
        <>
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="danger-button" onClick={onConfirm} disabled={!canDelete || busy}>
            {busy ? <Spinner label="Deleting" size={13} /> : <Trash2 size={15} />} {busy ? 'Deleting…' : 'Delete agent'}
          </button>
        </>
      }
    >
      <p>
        This will permanently remove <strong style={{ color: 'var(--text)' }}>{bot.name}</strong> from the
        dashboard and disable its runtime config lookup. Existing transcripts and call records are kept for audit.
      </p>

      {/* Blast radius */}
      {(assignedCampaigns.length > 0 || transcriptCount > 0) && (
        <div className="blast-radius">
          <strong>What breaks if you delete this:</strong>
          <ul>
            {assignedCampaigns.length > 0 && (
              <li>
                <AlertTriangle size={13} />
                <span>
                  <strong>{assignedCampaigns.length} campaign{assignedCampaigns.length > 1 ? 's' : ''}</strong> will lose their bot assignment:{' '}
                  {assignedCampaigns.map(c => c.name).join(', ')}
                </span>
              </li>
            )}
            {transcriptCount > 0 && (
              <li>
                <FileText size={13} />
                <span><strong>{transcriptCount} transcript{transcriptCount > 1 ? 's' : ''}</strong> will be orphaned (kept, but no longer linked to a bot config)</span>
              </li>
            )}
          </ul>
        </div>
      )}

      <label>
        Type <strong style={{ fontFamily: 'monospace', fontWeight: 700, color: 'var(--text-2)' }}>{bot.name}</strong> to confirm
        <input
          value={confirmText}
          onChange={(event) => setConfirmText(event.target.value)}
          placeholder={bot.name}
          autoFocus
        />
      </label>
    </Dialog>
  );
}
