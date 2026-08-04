import React, { useState, useMemo, useEffect } from 'react';
import {
  Bot, ChevronLeft, ChevronRight, Download, FileText, Filter, Mic, Search
} from 'lucide-react';
import type { Bot as BotType, Campaign, CallEvent, Transcript, TestRecordingLookup } from '../api';
import { api, apiUrl } from '../api';
import { StatusPill } from '../components/StatusPill';
import { CopyableId } from '../components/CopyableId';
import { TimeAgo } from '../components/TimeAgo';
import { Detail } from '../components/Detail';
import { SkeletonTableBody } from '../components/SkeletonTableBody';
import { EmptyState } from '../components/EmptyState';
import { shortId, formatTime, titleCase } from '../utils/formatting';
import { buildConversationItems, transcriptSourceLabel, maxResponseDelayLabel } from '../utils/transcript';
import { summarizeDetails } from '../utils/errors';

export function TranscriptsView({
  transcripts,
  selectedTranscript,
  localRecordingByRoom,
  callEvents,
  bots,
  campaigns,
  loading,
  searchText,
  onSearchText,
  filters,
  onFiltersChange,
  source,
  onSourceChange,
  onSelect,
  onNavigateTest
}: {
  transcripts: Transcript[];
  selectedTranscript?: Transcript;
  localRecordingByRoom: Record<string, TestRecordingLookup>;
  callEvents: CallEvent[];
  bots: BotType[];
  campaigns: Campaign[];
  loading?: boolean;
  searchText: string;
  onSearchText: (value: string) => void;
  filters: { status: string; outcome: string; campaign_id: string; bot_id: string; start_date: string; end_date: string };
  onFiltersChange: (f: typeof filters) => void;
  source: '' | 'web_test' | 'batch';
  onSourceChange: (s: '' | 'web_test' | 'batch') => void;
  onSelect: (id: string) => void;
  onNavigateTest?: () => void;
}) {
  const [showFilters, setShowFilters] = useState(false);
  const [brokenRecordingId, setBrokenRecordingId] = useState('');
  const conversationItems = useMemo(
    () => buildConversationItems(selectedTranscript, callEvents),
    [selectedTranscript, callEvents]
  );
  const localRecording = selectedTranscript?.room_name
    ? localRecordingByRoom[selectedTranscript.room_name]
    : undefined;
  const recordingUrl = selectedTranscript?.recording_url || localRecording?.recording_url || '';
  const recordingSource = selectedTranscript?.recording_source || localRecording?.recording_source || '';
  const assistantBotName = bots.find((b) => b._id === selectedTranscript?.bot_id)?.name || 'Assistant';

  const activeFilterCount = Object.values(filters).filter(Boolean).length;
  const dateRangeInvalid = Boolean(filters.start_date && filters.end_date && filters.end_date < filters.start_date);

  // Client-side apply active filters on top of the already-searched list
  const filteredTranscripts = useMemo(() => {
    return transcripts.filter(t => {
      if (filters.status && t.status !== filters.status) return false;
      if (filters.campaign_id && t.campaign_id !== filters.campaign_id) return false;
      if (filters.bot_id && t.bot_id !== filters.bot_id) return false;
      return true;
    });
  }, [transcripts, filters]);

  const PAGE_SIZE = 25;
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(filteredTranscripts.length / PAGE_SIZE));

  // Reset to page 1 whenever the underlying result set changes (new search/filter/data),
  // otherwise a filter change can strand the user on a now-out-of-range page.
  useEffect(() => {
    setPage(0);
  }, [filteredTranscripts.length, searchText, filters]);

  const displayedTranscripts = useMemo(
    () => filteredTranscripts.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE),
    [filteredTranscripts, page]
  );

  const csvUrl = api.exportCsvUrl({
    bot_id: filters.bot_id || undefined,
    campaign_id: filters.campaign_id || undefined,
    status: filters.status || undefined,
    outcome: filters.outcome || undefined,
    start_date: filters.start_date || undefined,
    end_date: filters.end_date || undefined,
    text: searchText.trim() || undefined,
    source: source || undefined,
  });

  return (
    <section className="content-grid transcripts-grid">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Saved transcripts</h2>
            <p>Raw transcripts are stored forever with call config snapshots.</p>
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            <div className="search-box">
              <Search size={15} aria-hidden />
              <input
                value={searchText}
                onChange={(event) => onSearchText(event.target.value)}
                placeholder="Search call, lead, campaign or text"
                aria-label="Search call, lead, campaign or text"
              />
            </div>
            <button
              title="Filters"
              onClick={() => setShowFilters(v => !v)}
              style={{ position: 'relative' }}
            >
              <Filter size={14} /> Filters {activeFilterCount > 0 && <span className="pill active" style={{ marginLeft: '4px', fontSize: '0.72rem', padding: '0 6px' }}>{activeFilterCount}</span>}
            </button>
            <a
              href={dateRangeInvalid ? undefined : csvUrl}
              download
              style={{ textDecoration: 'none', pointerEvents: dateRangeInvalid ? 'none' : undefined }}
              aria-disabled={dateRangeInvalid}
            >
              <button title={dateRangeInvalid ? 'Fix the date range before exporting' : 'Export CSV'} disabled={dateRangeInvalid}>
                <Download size={14} /> Export
              </button>
            </a>
          </div>
        </div>
        <div className="tab-toggle" role="tablist" aria-label="Call source" style={{ display: 'flex', gap: '4px', padding: '8px 16px 0' }}>
          {([
            ['', 'All'],
            ['web_test', 'Web Call'],
            ['batch', 'Batch Call'],
          ] as const).map(([value, label]) => (
            <button
              key={value || 'all'}
              role="tab"
              aria-selected={source === value}
              onClick={() => onSourceChange(value)}
              className={source === value ? 'tab active' : 'tab'}
              style={{
                fontSize: '0.8rem',
                padding: '4px 12px',
                border: '1px solid var(--border)',
                borderRadius: '999px',
                background: source === value ? 'var(--accent)' : 'transparent',
                color: source === value ? 'var(--accent-contrast, #fff)' : 'inherit',
              }}
            >
              {label}
            </button>
          ))}
        </div>
        {showFilters && (
          <div style={{ padding: '10px 16px', background: 'var(--bg-tertiary)', borderBottom: '1px solid var(--border)', display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.8rem' }}>
              Status
              <select value={filters.status} onChange={e => onFiltersChange({ ...filters, status: e.target.value })} style={{ fontSize: '0.8rem' }}>
                <option value="">All</option>
                {['completed', 'not_interested', 'disconnected', 'voicemail', 'dnc', 'error', 'busy', 'no_answer'].map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.8rem' }}>
              Campaign
              <select value={filters.campaign_id} onChange={e => onFiltersChange({ ...filters, campaign_id: e.target.value })} style={{ fontSize: '0.8rem' }}>
                <option value="">All</option>
                {campaigns.map(c => <option key={c._id} value={c.campaign_key}>{c.name}</option>)}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.8rem' }}>
              Bot
              <select value={filters.bot_id} onChange={e => onFiltersChange({ ...filters, bot_id: e.target.value })} style={{ fontSize: '0.8rem' }}>
                <option value="">All</option>
                {bots.map(b => <option key={b._id} value={b._id}>{b.name}</option>)}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.8rem' }}>
              From date
              <input type="date" value={filters.start_date} onChange={e => onFiltersChange({ ...filters, start_date: e.target.value })} style={{ fontSize: '0.8rem' }} />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.8rem' }}>
              To date
              <input
                type="date"
                value={filters.end_date}
                min={filters.start_date || undefined}
                onChange={e => onFiltersChange({ ...filters, end_date: e.target.value })}
                style={{ fontSize: '0.8rem' }}
                aria-invalid={dateRangeInvalid}
              />
            </label>
            {dateRangeInvalid && (
              <span role="alert" style={{ fontSize: '0.78rem', color: 'var(--danger)', alignSelf: 'center' }}>
                "To date" can't be before "From date".
              </span>
            )}
            <button onClick={() => onFiltersChange({ status: '', outcome: '', campaign_id: '', bot_id: '', start_date: '', end_date: '' })} style={{ fontSize: '0.8rem' }}>
              Clear
            </button>
          </div>
        )}

        <div className="table-scroll"><table>
          <thead>
            <tr><th>Call</th><th>Lead</th><th>Status</th><th>Duration</th><th>Turns</th><th>Created</th></tr>
          </thead>
          <tbody>
            {loading && !transcripts.length ? (
              <SkeletonTableBody cols={6} rows={5} />
            ) : displayedTranscripts.length === 0 ? (
              <tr><td colSpan={6}>
                {transcripts.length === 0 ? (
                  <EmptyState
                    icon={<FileText size={32} />}
                    heading="No transcripts yet"
                    description="Transcripts appear here after calls complete. Start a test call to see your first one."
                    action={onNavigateTest ? { label: 'Go to Test Call', onClick: onNavigateTest } : undefined}
                  />
                ) : (
                  <EmptyState
                    icon={<Search size={28} />}
                    heading="No matching transcripts"
                    description="Try adjusting your filters or search text."
                  />
                )}
              </td></tr>
            ) : displayedTranscripts.map((item) => (
              <tr key={item._id} onClick={() => onSelect(item._id)} className={item._id === selectedTranscript?._id ? 'selected-row' : ''}>
                <td>
                  <CopyableId value={item.call_id} label={item.call_id} />
                  <small>{item.campaign_id || '-'}</small>
                </td>
                <td><CopyableId value={item.lead_id} /></td>
                <td><StatusPill value={item.status || 'unknown'} /></td>
                <td>{item.call_duration_sec || 0}s</td>
                <td>{item.transcript_count ?? item.transcript?.length ?? 0}</td>
                <td><TimeAgo value={item.created_at} /></td>
              </tr>
            ))}
          </tbody>
        </table></div>
        {filteredTranscripts.length > 0 && (
          <div className="pagination-bar">
            <span className="muted">
              {page * PAGE_SIZE + 1}–{Math.min(filteredTranscripts.length, page * PAGE_SIZE + PAGE_SIZE)} of {filteredTranscripts.length}
            </span>
            <div className="pagination-controls">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                aria-label="Previous page"
              >
                <ChevronLeft size={14} /> Prev
              </button>
              <span className="muted">Page {page + 1} of {pageCount}</span>
              <button
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                disabled={page >= pageCount - 1}
                aria-label="Next page"
              >
                Next <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>
      <div className="panel transcript-detail">
        <div className="conversation-header">
          <div>
            <h2>Transcript detail</h2>
            <p>{selectedTranscript?.call_id || 'Select a call'} · {selectedTranscript?.status || 'unknown'}</p>
          </div>
          {selectedTranscript && <StatusPill value={selectedTranscript.status || 'unknown'} />}
        </div>
        {selectedTranscript ? (
          <>
            <div className="detail-list">
              <Detail label="Call ID" value={selectedTranscript.call_id || '-'} copyable />
              <Detail label="Bot version" value={selectedTranscript.bot_version_id ? shortId(selectedTranscript.bot_version_id) : '-'} />
              <Detail label="Callback" value={selectedTranscript.callback_status || '-'} />
              <Detail label="Transcript source" value={transcriptSourceLabel(selectedTranscript)} />
              <Detail label="Verification" value={selectedTranscript.verified_transcript_status || 'legacy'} />
              <Detail label="Max response delay" value={maxResponseDelayLabel(selectedTranscript)} />
              <Detail label="Recording" value={recordingUrl || 'No recording saved'} />
            </div>
            <div className="source-strip">
              <span className={`source-badge ${selectedTranscript.verified_transcript_status || 'legacy'}`}>
                {transcriptSourceLabel(selectedTranscript)}
              </span>
              {(selectedTranscript.transcript_quality_flags || []).map((flag) => (
                <span className="quality-flag" key={flag}>{titleCase(flag)}</span>
              ))}
              {selectedTranscript.verified_transcript_error && (
                <span className="quality-flag error">{selectedTranscript.verified_transcript_error}</span>
              )}
            </div>
            {recordingUrl && (
              <div className="recording-player">
                <div>
                  <strong>Call recording</strong>
                  <span>{recordingSource === 'dashboard_test_local' ? 'Local dashboard test recording' : 'Dialer recording'}</span>
                </div>
                {brokenRecordingId === selectedTranscript._id ? (
                  <p className="notice error" role="alert" style={{ margin: 0 }}>
                    Recording is unavailable — the file may have been moved or deleted from storage.
                  </p>
                ) : (
                  <audio
                    controls
                    preload="metadata"
                    src={apiUrl(recordingUrl)}
                    onError={() => setBrokenRecordingId(selectedTranscript._id)}
                  />
                )}
              </div>
            )}
            <div className="chat-transcript">
              {conversationItems.map((item) => (
                item.kind === 'event' ? (
                  <div className={`chat-event ${item.severity}`} key={item.id}>
                    <span>{item.time}</span>
                    <strong>{item.title}</strong>
                    <p>{item.text}</p>
                  </div>
                ) : (
                  <div className={`chat-turn ${item.role}`} key={item.id}>
                    <div className="chat-avatar">{item.role === 'assistant' ? <Bot size={15} /> : <Mic size={15} />}</div>
                    <div className="chat-bubble">
                      <div className="chat-meta">
                        <strong>{item.role === 'assistant' ? assistantBotName : item.role === 'user' ? 'User' : item.role === 'recording' ? 'Verified recording' : titleCase(item.role)}</strong>
                        <span>{item.time}</span>
                      </div>
                      <p>{item.text}</p>
                      {item.interrupted && <small>Interrupted during this turn</small>}
                    </div>
                  </div>
                )
              ))}
              {!conversationItems.length && <p className="muted">No transcript turns saved for this call yet.</p>}
            </div>
            <div className="timeline-section">
              <div className="section-heading">
                <h3>Call timeline</h3>
                <span>{callEvents.length} events</span>
              </div>
              <div className="event-timeline">
                {callEvents.map((event) => (
                  <div className={`call-event ${event.severity || 'info'}`} key={event._id}>
                    <div className="event-time">{formatTime(event.created_at)}</div>
                    <div>
                      <strong>{event.event_type}</strong>
                      <p>{event.message}</p>
                      {event.details && Object.keys(event.details).length > 0 && (
                        <small>{summarizeDetails(event.details)}</small>
                      )}
                    </div>
                  </div>
                ))}
                {!callEvents.length && <p className="muted">No technical timeline events have been stored for this call yet.</p>}
              </div>
            </div>
          </>
        ) : <p className="muted">Select a transcript to inspect details.</p>}
      </div>
    </section>
  );
}
