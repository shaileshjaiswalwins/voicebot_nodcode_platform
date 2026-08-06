import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, ArrowDown, ArrowUp, Bot, Copy, Download, FileText, LayoutGrid, List,
  Pencil, Plus, Rocket, Search, Tag, Trash2, Trash
} from 'lucide-react';
import type { Bot as BotType, Campaign, LanguageOption } from '../api';
import { api } from '../api';
import { OPENING_LINE_BY_GENDER } from '../constants/ui';
import { StatusPill } from '../components/StatusPill';
import { TimeAgo } from '../components/TimeAgo';
import { RowActionsMenu } from '../components/RowActionsMenu';
import { SkeletonTableBody } from '../components/SkeletonTableBody';
import { EmptyState } from '../components/EmptyState';
import { Dialog } from '../components/Dialog';
import { Spinner } from '../components/Spinner';
import { RecentlyDeletedModal } from '../components/RecentlyDeletedModal';

const PAGE_SIZE = 20;

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '?';
}

function formatDuration(sec?: number): string {
  const s = Math.round(sec || 0);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

type SortKey = 'updated_at' | 'name' | 'call_count';

export function BotsView({ bots, loading, onEdit, onDelete, onNew, onDuplicate, duplicatingBotId, onBotRestored }: {
  bots: BotType[];
  loading?: boolean;
  onEdit: (botId: string) => void;
  onDelete: (bot: BotType) => void;
  onNew?: () => void;
  /** Clone an agent's current config (published version if it has one, else its latest
   * draft) into a brand-new draft bot named "{name} (copy)". The most-requested action
   * missing from this list — every new agent otherwise starts from the same generic
   * defaultConfig, so teams running several similar bots have no fast path to "one like
   * this, slightly different." */
  onDuplicate?: (bot: BotType) => void;
  duplicatingBotId?: string;
  /** Refreshes the main Agents list after a restore from Recently Deleted — without this,
   * the restored bot only disappears from the modal's own local list and never actually
   * reappears in `bots` until an unrelated action happens to reload it. */
  onBotRestored?: () => void;
}) {
  const [search, setSearch] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagMenuOpen, setTagMenuOpen] = useState(false);
  const [tagSearch, setTagSearch] = useState('');
  const tagFilterRef = useRef<HTMLDivElement>(null);
  // Matches RowActionsMenu's own close-on-outside-click/Escape behavior — without this the
  // dropdown only ever closed by clicking the Tags button again, which reads as stuck/broken
  // once the tester clicks anywhere else on the page.
  useEffect(() => {
    if (!tagMenuOpen) return;
    function onDocClick(e: MouseEvent) {
      if (tagFilterRef.current && !tagFilterRef.current.contains(e.target as Node)) setTagMenuOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setTagMenuOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [tagMenuOpen]);
  const [sortKey, setSortKey] = useState<SortKey>('updated_at');
  const [sortDesc, setSortDesc] = useState(true);
  const [draftsOnly, setDraftsOnly] = useState(false);
  const [viewMode, setViewMode] = useState<'list' | 'grid'>(() => (localStorage.getItem('agentsViewMode') as 'list' | 'grid') || 'list');
  const [page, setPage] = useState(1);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const [deletedOpen, setDeletedOpen] = useState(false);
  const [deletedBots, setDeletedBots] = useState<BotType[] | null>(null);

  function setViewModePersist(mode: 'list' | 'grid') {
    setViewMode(mode);
    localStorage.setItem('agentsViewMode', mode);
  }

  function loadDeletedBots() {
    setDeletedOpen(true);
    api.deletedBots().then(setDeletedBots).catch(() => setDeletedBots([]));
  }

  const allTags = useMemo(() => {
    const set = new Set<string>();
    bots.forEach((b) => (b.tags || []).forEach((t) => set.add(t)));
    return Array.from(set).sort();
  }, [bots]);

  // Counts always reflect the full unfiltered set — the dropdown shows how many agents
  // each tag holds regardless of whatever search/drafts-only filter is currently active.
  const tagCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    bots.forEach((b) => (b.tags || []).forEach((t) => { counts[t] = (counts[t] || 0) + 1; }));
    return counts;
  }, [bots]);

  const draftCount = bots.filter((b) => !b.published).length;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let list = bots.filter((b) => {
      if (draftsOnly && b.published) return false;
      if (selectedTags.length && !selectedTags.every((t) => (b.tags || []).includes(t))) return false;
      if (!q) return true;
      return (
        b.name.toLowerCase().includes(q) ||
        (b.description || '').toLowerCase().includes(q) ||
        (b.tags || []).some((t) => t.toLowerCase().includes(q))
      );
    });
    list = [...list].sort((a, b) => {
      let cmp = 0;
      if (sortKey === 'name') cmp = a.name.localeCompare(b.name);
      else if (sortKey === 'call_count') cmp = (a.call_count ?? 0) - (b.call_count ?? 0);
      else cmp = new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime();
      return sortDesc ? -cmp : cmp;
    });
    return list;
  }, [bots, search, selectedTags, draftsOnly, sortKey, sortDesc]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageClamped = Math.min(page, totalPages);
  const paged = filtered.slice((pageClamped - 1) * PAGE_SIZE, pageClamped * PAGE_SIZE);

  async function handleExport() {
    setExporting(true);
    setExportError('');
    try {
      await api.exportBots();
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export failed.');
    } finally {
      setExporting(false);
    }
  }

  function rowActionsFor(bot: BotType) {
    return [
      { label: 'Edit', icon: <Pencil size={13} />, onClick: () => onEdit(bot._id) },
      ...(onDuplicate ? [{
        label: duplicatingBotId === bot._id ? 'Duplicating…' : 'Duplicate',
        icon: <Copy size={13} />,
        onClick: () => onDuplicate(bot),
      }] : []),
      { label: 'Delete', icon: <Trash2 size={13} />, onClick: () => onDelete(bot), danger: true },
    ];
  }

  const toolbar = (
    <div className="agents-toolbar">
      <div className="agents-search">
        <input
          placeholder="Search agents by name, description, or tags…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        />
      </div>

      <div className="agents-toolbar-controls">
        <div className="tag-filter" ref={tagFilterRef}>
          <button onClick={() => setTagMenuOpen((v) => !v)} className={selectedTags.length ? 'active' : ''}>
            <Tag size={14} /> Tags{selectedTags.length ? ` (${selectedTags.length})` : ''}
          </button>
          {tagMenuOpen && (
            <div className="tag-filter-dropdown">
              <div className="tag-filter-heading">Filter by Tags</div>
              <div className="tag-filter-search">
                <Search size={14} />
                <input
                  autoFocus
                  placeholder="Search tags…"
                  value={tagSearch}
                  onChange={(e) => setTagSearch(e.target.value)}
                />
              </div>
              <div className="tag-filter-list">
                {allTags.length === 0 ? (
                  <p className="muted" style={{ fontSize: 'var(--font-size-md)', margin: '0.4rem' }}>No tags assigned yet.</p>
                ) : (
                  <>
                    <div className="tag-filter-section-label">Available Tags</div>
                    {allTags.filter((tag) => tag.toLowerCase().includes(tagSearch.trim().toLowerCase())).map((tag) => (
                      <label key={tag} className="tag-filter-option">
                        <input
                          type="checkbox"
                          checked={selectedTags.includes(tag)}
                          onChange={(e) => {
                            setSelectedTags((prev) => (e.target.checked ? [...prev, tag] : prev.filter((t) => t !== tag)));
                            setPage(1);
                          }}
                        />
                        <span>{tag}</span>
                        <span className="count-badge">{tagCounts[tag] || 0}</span>
                      </label>
                    ))}
                  </>
                )}
              </div>
            </div>
          )}
        </div>

        <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
          <option value="updated_at">Last Updated</option>
          <option value="name">Name</option>
          <option value="call_count">Calls</option>
        </select>
        <button onClick={() => setSortDesc((v) => !v)} title={sortDesc ? 'Descending' : 'Ascending'}>
          {sortDesc ? <ArrowDown size={15} /> : <ArrowUp size={15} />}
        </button>

        <button
          className={draftsOnly ? 'active' : ''}
          onClick={() => { setDraftsOnly((v) => !v); setPage(1); }}
        >
          Show Drafts Only {draftCount > 0 && <span className="count-badge">{draftCount}</span>}
        </button>

        <div className="view-toggle">
          <button className={viewMode === 'list' ? 'active' : ''} onClick={() => setViewModePersist('list')} aria-label="List view" title="List view">
            <List size={15} />
          </button>
          <button className={viewMode === 'grid' ? 'active' : ''} onClick={() => setViewModePersist('grid')} aria-label="Grid view" title="Grid view">
            <LayoutGrid size={15} />
          </button>
        </div>
      </div>
    </div>
  );

  const header = (
    <div className="panel-header">
      <div>
        <h2>Agents</h2>
        <p>Manage your intelligent voice agents.</p>
      </div>
      <div className="button-row">
        <button onClick={handleExport} disabled={exporting}>
          <Download size={14} /> {exporting ? 'Exporting…' : 'Export Data'}
        </button>
        <button onClick={loadDeletedBots}>
          <Trash size={14} /> Recently Deleted
        </button>
        {onNew && bots.length > 0 && (
          <button id="onboarding-create-agent" className="primary" onClick={onNew}><Plus size={15} /> New agent</button>
        )}
      </div>
    </div>
  );

  const pagination = totalPages > 1 && (
    <div className="agents-pagination">
      <button disabled={pageClamped <= 1} onClick={() => setPage(pageClamped - 1)}>Previous</button>
      <span>Page {pageClamped} of {totalPages}</span>
      <button disabled={pageClamped >= totalPages} onClick={() => setPage(pageClamped + 1)}>Next</button>
    </div>
  );

  return (
    <section className="content-grid">
      <div className="table-panel">
        {header}
        {toolbar}
        {exportError && <div className="notice error" role="alert" style={{ margin: '0.5rem 0' }}>{exportError}</div>}

        {loading && !bots.length ? (
          <div className="table-scroll"><table><tbody><SkeletonTableBody cols={6} rows={4} /></tbody></table></div>
        ) : bots.length === 0 ? (
          <EmptyState
            icon={<Bot size={32} />}
            heading="No agents yet"
            description="Create your first voice agent to get started. Each agent has its own prompt, voice, and published versions."
            action={onNew ? { label: 'Create first agent', onClick: onNew, id: 'onboarding-create-agent' } : undefined}
          />
        ) : filtered.length === 0 ? (
          <EmptyState icon={<Bot size={32} />} heading="No matching agents" description="Try a different search term or clear your filters." />
        ) : viewMode === 'grid' ? (
          <div className="agents-grid">
            {paged.map((bot) => (
              <div key={bot._id} className="agent-card" onClick={() => onEdit(bot._id)}>
                <div className="agent-card-top">
                  <div className="agent-card-avatar">{initials(bot.name)}</div>
                  <RowActionsMenu actions={rowActionsFor(bot)} />
                </div>
                <strong>{bot.name}</strong>
                <small>{bot.description || 'Prompt + settings agent'}</small>
                {(bot.tags || []).length > 0 && (
                  <div className="agent-card-tags">
                    {(bot.tags || []).slice(0, 3).map((t) => <span key={t} className="pill">{t}</span>)}
                    {(bot.tags || []).length > 3 && <span className="pill">+{(bot.tags || []).length - 3}</span>}
                  </div>
                )}
                <div className="agent-card-metrics">
                  <div><strong>{bot.calls_today ?? 0}</strong><span>calls today</span></div>
                  <div><strong>{formatDuration(bot.avg_duration_sec)}</strong><span>avg duration</span></div>
                </div>
                <div className="agent-card-footer">
                  <StatusPill value={bot.published ? 'published' : 'draft'} />
                  <TimeAgo value={bot.updated_at} />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="table-scroll"><table>
            <thead>
              <tr><th>Agent</th><th>Calls Today</th><th>Avg Duration</th><th>Last Updated</th><th></th></tr>
            </thead>
            <tbody>
              {paged.map((bot) => (
                <tr key={bot._id} onClick={() => onEdit(bot._id)}>
                  <td>
                    <div className="agent-row-name">
                      <div className="agent-card-avatar small">{initials(bot.name)}</div>
                      <div>
                        <strong>{bot.name}</strong>
                        <small>{bot.agent_name ? `${bot.agent_name} — ` : ''}{bot.description || 'Prompt + settings agent'}</small>
                        {(bot.tags || []).length > 0 && (
                          <div className="agent-card-tags">
                            {(bot.tags || []).map((t) => <span key={t} className="pill">{t}</span>)}
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td>{bot.calls_today ?? 0}<br /><small className="muted">calls today</small></td>
                  <td>{formatDuration(bot.avg_duration_sec)}<br /><small className="muted">average duration</small></td>
                  <td><TimeAgo value={bot.updated_at} /><br /><small className="muted">last updated</small></td>
                  <td><RowActionsMenu actions={rowActionsFor(bot)} /></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}

        {pagination}
      </div>

      {deletedOpen && (
        <RecentlyDeletedModal
          bots={deletedBots}
          onClose={() => setDeletedOpen(false)}
          onRestored={(id) => {
            setDeletedBots((prev) => (prev || []).filter((b) => b._id !== id));
            onBotRestored?.();
          }}
        />
      )}
    </section>
  );
}

export function NewAgentWizard({
  form,
  onChange,
  languages,
  busy,
  onCancel,
  onDiscard,
  onConfirm
}: {
  form: {
    name: string;
    description: string;
    agent_name: string;
    organization_name: string;
    persona_gender: 'female' | 'male';
    language: string;
    initial_message: string;
  };
  onChange: (value: typeof form) => void;
  languages: LanguageOption[];
  busy: boolean;
  onCancel: () => void;
  onDiscard?: () => void;
  onConfirm: () => void;
}) {
  const canCreate = form.name.trim().length > 0 && form.agent_name.trim().length > 0;
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    onChange({ ...form, [key]: e.target.value });

  // Switching gender rewrites the opening line's verb, but only while the line is still
  // one of the two defaults — never clobber a line the user has typed themselves.
  function setGender(e: React.ChangeEvent<HTMLSelectElement>) {
    const persona_gender = e.target.value as 'female' | 'male';
    const untouched = Object.values(OPENING_LINE_BY_GENDER).includes(form.initial_message);
    onChange({
      ...form,
      persona_gender,
      initial_message: untouched ? OPENING_LINE_BY_GENDER[persona_gender] : form.initial_message,
    });
  }

  // Global replace: a line may use the same placeholder twice, and String.replace with a
  // string pattern only swaps the first — which previewed as half-substituted text.
  const previewOpening = form.initial_message
    .replace(/\{agent_name\}/g, form.agent_name || '<agent_name>')
    .replace(/\{organization_name\}/g, form.organization_name || '<org_name>')
    .replace(/\{product\}/g, '<product>');

  return (
    <Dialog
      title="New voice agent"
      icon={<Rocket size={17} />}
      maxWidth={560}
      onClose={onCancel}
      // Never dismiss a half-filled creation form on a stray backdrop click — releasing a
      // text-selection drag outside the panel used to wipe every field. Escape still closes,
      // but the caller keeps the draft so reopening restores it; only Cancel discards.
      closeOnBackdrop={false}
      closeOnEscape={!busy}
      footer={
        <>
          <button onClick={onDiscard ?? onCancel} disabled={busy}>Cancel</button>
          <button className="primary" onClick={onConfirm} disabled={!canCreate || busy}>
            {busy ? <Spinner label="Creating" size={13} /> : <Rocket size={15} />} {busy ? 'Creating…' : 'Create agent'}
          </button>
        </>
      }
    >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div className="form-grid">
            <label>
              <span>Bot name <span style={{ color: 'var(--danger)' }}>*</span></span>
              <input value={form.name} onChange={set('name')} placeholder="e.g. JD Outbound — Hindi" autoFocus />
              <small>A label for the agent</small>
            </label>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
            <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Persona</div>
            <div className="form-grid">
              <label>
                <span>Agent name <span style={{ color: 'var(--danger)' }}>*</span></span>
                <input value={form.agent_name} onChange={set('agent_name')} placeholder="e.g. Tarun, Priya, Aman" />
                <small>Name of the agent</small>
              </label>
              <label>
                Gender
                <select value={form.persona_gender} onChange={setGender}>
                  <option value="female">Female</option>
                  <option value="male">Male</option>
                </select>
                <small>Hindi conjugates the speaker's verb — this sets बोल रही हूँ vs बोल रहा हूँ.</small>
              </label>
            </div>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
            <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Language</div>
            <div className="form-grid">
              <label>
                Language
                <select value={form.language} onChange={set('language')}>
                  {languages.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                </select>
                <small>Hindi only for now — the runtime speaks Hindi (Sarvam STT/TTS).</small>
              </label>
            </div>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
            <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>Opening line</div>
            <label>
              <textarea
                value={form.initial_message}
                onChange={set('initial_message')}
                rows={3}
                style={{ fontFamily: 'inherit', fontSize: 'var(--font-size-lg)', resize: 'vertical' }}
              />
              <small>Use <code style={{ fontSize: 'var(--font-size-sm)' }}>{'{product}'}</code>, <code style={{ fontSize: 'var(--font-size-sm)' }}>{'{agent_name}'}</code>, <code style={{ fontSize: 'var(--font-size-sm)' }}>{'{organization_name}'}</code> as placeholders.</small>
            </label>
            {previewOpening && (
              <div style={{ background: 'var(--surface-2)', borderRadius: '6px', padding: '0.5rem 0.75rem', fontSize: 'var(--font-size-md)', fontStyle: 'italic', marginTop: '0.4rem', color: 'var(--text-2)' }}>
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
