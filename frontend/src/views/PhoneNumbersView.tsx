import React, { useEffect, useMemo, useState } from 'react';
import { Phone, Pencil, Plus, HelpCircle } from 'lucide-react';
import type { Bot as BotType, PhoneNumber, PhoneNumberEnvironment } from '../api';
import { StatusPill } from '../components/StatusPill';
import { SkeletonTableBody } from '../components/SkeletonTableBody';
import { EmptyState } from '../components/EmptyState';
import { Dialog } from '../components/Dialog';
import { Spinner } from '../components/Spinner';
import { RowActions } from '../components/RowActions';
import { Tooltip } from '../components/Tooltip';

type EnvFilter = 'all' | PhoneNumberEnvironment;

type CreatePayload = {
  number: string;
  environment: PhoneNumberEnvironment;
  service_id?: string;
  aod_ports?: number;
  name?: string;
  ip?: string;
  sip_trunk?: string;
  sip_username?: string;
  sip_password?: string;
};

type UpdatePayload = {
  status?: string;
  service_id?: string;
  aod_ports?: number;
  name?: string;
  ip?: string;
  sip_trunk?: string;
  sip_username?: string;
  sip_password?: string;
};

export function PhoneNumbersView({
  phoneNumbers,
  bots,
  loading,
  onCreate,
  createState,
  onUpdate,
  updateState,
  onReassign,
  reassignState,
  onDelete,
}: {
  phoneNumbers: PhoneNumber[];
  bots: BotType[];
  loading?: boolean;
  onCreate: (payload: CreatePayload) => Promise<void>;
  createState?: 'idle' | 'running' | 'failed';
  onUpdate: (id: string, payload: UpdatePayload) => Promise<void>;
  updateState?: 'idle' | 'running' | 'failed';
  onReassign: (id: string, botId: string) => void;
  reassignState?: Record<string, 'idle' | 'running' | 'failed'>;
  onDelete: (id: string) => void;
}) {
  const botById = useMemo(() => new Map(bots.map((b) => [b._id, b])), [bots]);
  const [envFilter, setEnvFilter] = useState<EnvFilter>('all');
  const [showAddModal, setShowAddModal] = useState(false);
  const [newNumber, setNewNumber] = useState('');
  const [newEnv, setNewEnv] = useState<PhoneNumberEnvironment>('dev');
  const [newServiceId, setNewServiceId] = useState('');
  const [newAodPorts, setNewAodPorts] = useState('1');
  const [newName, setNewName] = useState('');
  const [newIp, setNewIp] = useState('');
  const [newSipTrunk, setNewSipTrunk] = useState('');
  const [newSipUsername, setNewSipUsername] = useState('');
  const [newSipPassword, setNewSipPassword] = useState('');
  const [expandedId, setExpandedId] = useState<string>('');

  const [editTarget, setEditTarget] = useState<PhoneNumber | null>(null);
  const [editStatus, setEditStatus] = useState('active');
  const [editServiceId, setEditServiceId] = useState('');
  const [editAodPorts, setEditAodPorts] = useState('1');
  const [editName, setEditName] = useState('');
  const [editIp, setEditIp] = useState('');
  const [editSipTrunk, setEditSipTrunk] = useState('');
  const [editSipUsername, setEditSipUsername] = useState('');
  const [editSipPassword, setEditSipPassword] = useState('');
  const [editBotId, setEditBotId] = useState('');

  useEffect(() => {
    if (!editTarget) return;
    setEditStatus(editTarget.status || 'active');
    setEditServiceId(editTarget.service_id || '');
    setEditAodPorts(String(editTarget.aod_ports ?? 1));
    setEditName(editTarget.name || '');
    setEditIp(editTarget.ip || '');
    setEditSipTrunk(editTarget.sip_trunk || '');
    setEditSipUsername(editTarget.sip_username || '');
    setEditSipPassword('');
    setEditBotId(editTarget.assigned_bot_id || '');
  }, [editTarget]);

  const filtered = envFilter === 'all' ? phoneNumbers : phoneNumbers.filter((p) => p.environment === envFilter);

  async function handleAdd() {
    if (!newNumber.trim()) return;
    await onCreate({
      number: newNumber.trim(),
      environment: newEnv,
      service_id: newServiceId.trim() || undefined,
      aod_ports: newAodPorts.trim() ? Number(newAodPorts) : undefined,
      name: newName.trim() || undefined,
      ip: newIp.trim() || undefined,
      sip_trunk: newSipTrunk.trim() || undefined,
      sip_username: newSipUsername.trim() || undefined,
      sip_password: newSipPassword || undefined,
    });
    setNewNumber(''); setNewServiceId(''); setNewAodPorts('1'); setNewName('');
    setNewIp(''); setNewSipTrunk(''); setNewSipUsername(''); setNewSipPassword('');
    setShowAddModal(false);
  }

  async function handleSaveEdit() {
    if (!editTarget) return;
    await onUpdate(editTarget._id, {
      status: editStatus,
      service_id: editServiceId.trim() || undefined,
      aod_ports: editAodPorts.trim() ? Number(editAodPorts) : undefined,
      name: editName.trim() || undefined,
      ip: editIp.trim() || undefined,
      sip_trunk: editSipTrunk.trim() || undefined,
      sip_username: editSipUsername.trim() || undefined,
      sip_password: editSipPassword || undefined,
    });
    if (editBotId !== (editTarget.assigned_bot_id || '') && editBotId) {
      onReassign(editTarget._id, editBotId);
    }
    setEditTarget(null);
  }

  return (
    <section className="content-grid">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Phone numbers</h2>
            <p>Map a real phone number to a bot and environment. Reassign hot-swaps which bot answers the next call.</p>
          </div>
          <button className="primary" onClick={() => setShowAddModal(true)}>
            <Plus size={14} /> Add number
          </button>
        </div>

        <div className="library-tabs" role="tablist" aria-label="Environment filter" style={{ marginBottom: '0.75rem' }}>
          {(['all', 'dev', 'preprod', 'prod'] as EnvFilter[]).map((env) => (
            <button
              key={env}
              type="button"
              role="tab"
              aria-selected={envFilter === env}
              className={envFilter === env ? 'library-tab active' : 'library-tab'}
              onClick={() => setEnvFilter(env)}
            >
              <strong>{env === 'all' ? 'All' : env}</strong>
            </button>
          ))}
        </div>

        <div className="table-scroll"><table>
          <thead>
            <tr><th>Number</th><th>Name</th><th>Environment</th><th>Assigned bot</th><th>Status</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {loading && !filtered.length ? (
              <SkeletonTableBody cols={6} rows={3} />
            ) : !filtered.length ? (
              <tr><td colSpan={6}>
                <EmptyState
                  icon={<Phone size={32} />}
                  heading="No phone numbers yet"
                  description="Add a phone number and assign it to a published bot to enable inbound routing."
                />
              </td></tr>
            ) : filtered.map((phone) => (
              <React.Fragment key={phone._id}>
                <tr>
                  <td>
                    <button
                      onClick={() => setExpandedId((cur) => (cur === phone._id ? '' : phone._id))}
                      style={{ border: 'none', background: 'none', padding: 0, minHeight: 0, fontWeight: 700 }}
                      title="Show SIP trunk details"
                    >
                      {phone.number}
                    </button>
                  </td>
                  <td>{phone.name || <small className="muted">—</small>}</td>
                  <td><span className="pill">{phone.environment}</span></td>
                  <td>
                    {phone.assigned_bot_id ? (botById.get(phone.assigned_bot_id)?.name || <small className="muted">Unknown bot ({phone.assigned_bot_id})</small>) : <small className="muted">— unassigned —</small>}
                    {reassignState?.[phone._id] === 'running' && <Spinner label="Reassigning" size={13} />}
                  </td>
                  <td><StatusPill value={phone.status || 'active'} /></td>
                  <td>
                    <RowActions
                      item={phone}
                      onEdit={setEditTarget}
                      editTitle="Edit number"
                      onDelete={(target) => onDelete(target._id)}
                      deleteTitle="Delete phone number?"
                      deleteDescription={(target) => `"${target.number}" will be removed. This does not affect the bot it was assigned to.`}
                    />
                  </td>
                </tr>
                {expandedId === phone._id && (
                  <tr>
                    <td colSpan={6} style={{ background: 'var(--surface-2)' }}>
                      <div className="phone-detail-grid" style={{ padding: '0.5rem 0.25rem', fontSize: '0.8rem' }}>
                        <div><small className="muted">Service ID</small><div>{phone.service_id || '—'}</div></div>
                        <div><small className="muted">AOD ports</small><div>{phone.aod_ports ?? '—'}</div></div>
                        <div><small className="muted">IP</small><div>{phone.ip || '—'}</div></div>
                        <div><small className="muted">SIP trunk</small><div>{phone.sip_trunk || '—'}</div></div>
                        <div><small className="muted">SIP username</small><div>{phone.sip_username || '—'}</div></div>
                        <div><small className="muted">SIP password</small><div>•••••• (write-only)</div></div>
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table></div>
      </div>

      {showAddModal && (
        <Dialog
          title="Add phone number"
          icon={<Phone size={17} />}
          maxWidth={560}
          onClose={() => setShowAddModal(false)}
          closeOnBackdrop={createState !== 'running'}
          closeOnEscape={createState !== 'running'}
          footer={
            <>
              <button onClick={() => setShowAddModal(false)} disabled={createState === 'running'}>Cancel</button>
              <button
                className={createState === 'failed' ? 'fallback-button' : 'primary'}
                onClick={handleAdd}
                disabled={createState === 'running' || !newNumber.trim()}
              >
                {createState === 'running' ? 'Adding...' : createState === 'failed' ? 'Retry add' : 'Save'}
              </button>
            </>
          }
        >
          <div className="form-grid">
            <label>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                Number (DNI)
                <Tooltip label="Direct Number Identifier — the actual number that gets dialed.">
                  <HelpCircle size={12} style={{ color: 'var(--muted)', cursor: 'help' }} />
                </Tooltip>
              </span>
              <input value={newNumber} onChange={(e) => setNewNumber(e.target.value)} placeholder="08069625582" />
            </label>
            <label>
              Environment
              <select value={newEnv} onChange={(e) => setNewEnv(e.target.value as PhoneNumberEnvironment)}>
                <option value="dev">dev</option>
                <option value="preprod">preprod</option>
                <option value="prod">prod</option>
              </select>
            </label>
            <label>
              Name
              <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="AI_Boot_15" />
            </label>
            <label>
              Service ID
              <input value={newServiceId} onChange={(e) => setNewServiceId(e.target.value)} placeholder="300" />
            </label>
            <label>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                AOD ports
                <Tooltip label="Number of simultaneous audio-on-demand lines this number supports.">
                  <HelpCircle size={12} style={{ color: 'var(--muted)', cursor: 'help' }} />
                </Tooltip>
              </span>
              <input type="number" min={1} value={newAodPorts} onChange={(e) => setNewAodPorts(e.target.value)} />
            </label>
            <label>
              IP
              <input value={newIp} onChange={(e) => setNewIp(e.target.value)} placeholder="192.168.29.196" />
            </label>
            <label>
              SIP trunk
              <input value={newSipTrunk} onChange={(e) => setNewSipTrunk(e.target.value)} placeholder="9017" />
            </label>
            <label>
              SIP username
              <input value={newSipUsername} onChange={(e) => setNewSipUsername(e.target.value)} placeholder="voice_bot_nocode" />
            </label>
            <label className="full">
              SIP password
              <input type="password" value={newSipPassword} onChange={(e) => setNewSipPassword(e.target.value)} placeholder="••••••••" />
              <small>Stored, never shown again once saved — re-enter to change it later.</small>
            </label>
          </div>
        </Dialog>
      )}

      {editTarget && (
        <Dialog
          title={`Edit ${editTarget.number}`}
          icon={<Pencil size={17} />}
          maxWidth={560}
          onClose={() => setEditTarget(null)}
          closeOnBackdrop={updateState !== 'running'}
          closeOnEscape={updateState !== 'running'}
          footer={
            <>
              <button onClick={() => setEditTarget(null)} disabled={updateState === 'running'}>Cancel</button>
              <button
                className={updateState === 'failed' ? 'fallback-button' : 'primary'}
                onClick={handleSaveEdit}
                disabled={updateState === 'running'}
              >
                {updateState === 'running' ? 'Saving...' : updateState === 'failed' ? 'Retry save' : 'Save'}
              </button>
            </>
          }
        >
          <div className="form-grid">
            <label>
              Assigned bot
              <select value={editBotId} onChange={(e) => setEditBotId(e.target.value)}>
                <option value="">— unassigned —</option>
                {bots.map((b) => <option key={b._id} value={b._id}>{b.name}</option>)}
              </select>
            </label>
            <label>
              Status
              <select value={editStatus} onChange={(e) => setEditStatus(e.target.value)}>
                <option value="active">active</option>
                <option value="inactive">inactive</option>
              </select>
            </label>
            <label>
              Name
              <input value={editName} onChange={(e) => setEditName(e.target.value)} />
            </label>
            <label>
              Service ID
              <input value={editServiceId} onChange={(e) => setEditServiceId(e.target.value)} />
            </label>
            <label>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                AOD ports
                <Tooltip label="Number of simultaneous audio-on-demand lines this number supports.">
                  <HelpCircle size={12} style={{ color: 'var(--muted)', cursor: 'help' }} />
                </Tooltip>
              </span>
              <input type="number" min={1} value={editAodPorts} onChange={(e) => setEditAodPorts(e.target.value)} />
            </label>
            <label>
              IP
              <input value={editIp} onChange={(e) => setEditIp(e.target.value)} />
            </label>
            <label>
              SIP trunk
              <input value={editSipTrunk} onChange={(e) => setEditSipTrunk(e.target.value)} />
            </label>
            <label>
              SIP username
              <input value={editSipUsername} onChange={(e) => setEditSipUsername(e.target.value)} />
            </label>
            <label className="full">
              SIP password
              <input type="password" value={editSipPassword} onChange={(e) => setEditSipPassword(e.target.value)} placeholder="Leave blank to keep unchanged" />
            </label>
          </div>
        </Dialog>
      )}

    </section>
  );
}
