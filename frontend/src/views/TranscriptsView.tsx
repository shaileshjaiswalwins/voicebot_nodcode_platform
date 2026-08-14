import React, { useState, useMemo, useEffect } from 'react';
import {
  Bot, ChevronLeft, ChevronRight, Download, FileText, Filter, Mic, Search
} from 'lucide-react';
import type { Bot as BotType, Campaign, CallEvent, Transcript, TestRecordingLookup } from '../api';
import { api, apiUrl, getToken } from '../api';
import { StatusPill } from '../components/StatusPill';
import { CopyableId } from '../components/CopyableId';
import { TimeAgo } from '../components/TimeAgo';
import { Detail, DetailText } from '../components/Detail';
import { AudioPlayer } from '../components/AudioPlayer';
import { SkeletonTableBody } from '../components/SkeletonTableBody';
import { EmptyState } from '../components/EmptyState';
import { shortId, formatTime, titleCase } from '../utils/formatting';
import { buildConversationItems, maxResponseDelayLabel, outcomeTone } from '../utils/transcript';
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
  onNavigateTest,
  hasMore,
  loadingMore,
  onLoadMore
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
  /** Whether the server has more transcripts beyond what's currently loaded — distinct
   * from filteredTranscripts.length, which only reflects what's already in memory. */
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
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
  const [recordingBlobUrl, setRecordingBlobUrl] = useState('');

  // <audio src> issues a plain browser GET with no Authorization header, but the
  // recording endpoint requires one (Depends(require_user)) — that 401 was showing up
  // as "Recording is unavailable" even when the file exists. Fetch it ourselves (with
  // the auth header) and hand the audio element a local blob URL instead.
  useEffect(() => {
    setRecordingBlobUrl('');
    setBrokenRecordingId('');
    if (!recordingUrl) return;
    let cancelled = false;
    let objectUrl = '';
    fetch(apiUrl(recordingUrl), { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status}`);
        return res.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setRecordingBlobUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setBrokenRecordingId(selectedTranscript?._id || '');
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [recordingUrl, selectedTranscript?._id]);
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

  // Reset to page 1 when the user changes what they're searching/filtering for —
  // deliberately NOT keyed on filteredTranscripts.length, which also changes on
  // load-more appending rows and would otherwise bounce the user back to page 1
  // mid-navigation (that was the actual bug here, not an intended flow).
  useEffect(() => {
    setPage(0);
  }, [searchText, filters]);

  // Safety net for the case the reset above exists for: a filter/search shrinking the
  // result set out from under whatever page the user was on. Clamps down, never up —
  // so it's a no-op while load-more is growing the list (page is already in range).
  useEffect(() => {
    setPage((p) => Math.min(p, pageCount - 1));
  }, [pageCount]);

  const displayedTranscripts = useMemo(
    () => filteredTranscripts.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE),
    [filteredTranscripts, page]
  );

  const [exporting, setExporting] = useState(false);

  async function handleExportCsv() {
    setExporting(true);
    try {
      const blob = await api.exportTranscriptsCsv({
        bot_id: filters.bot_id || undefined,
        campaign_id: filters.campaign_id || undefined,
        status: filters.status || undefined,
        outcome: filters.outcome || undefined,
        start_date: filters.start_date || undefined,
        end_date: filters.end_date || undefined,
        text: searchText.trim() || undefined,
        source: source || undefined,
      });
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = `transcripts-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch {
      // Best-effort — a failed export isn't worth its own error banner here.
    } finally {
      setExporting(false);
    }
  }

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
              <Filter size={14} /> Filters {activeFilterCount > 0 && <span className="pill active" style={{ marginLeft: '4px', fontSize: 'var(--font-size-xs)', padding: '0 6px' }}>{activeFilterCount}</span>}
            </button>
            <button
              title={dateRangeInvalid ? 'Fix the date range before exporting' : 'Export CSV'}
              disabled={dateRangeInvalid || exporting}
              onClick={handleExportCsv}
            >
              <Download size={14} /> {exporting ? 'Exporting…' : 'Export'}
            </button>
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
                fontSize: 'var(--font-size-md)',
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
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: 'var(--font-size-md)' }}>
              Status
              <select value={filters.status} onChange={e => onFiltersChange({ ...filters, status: e.target.value })} style={{ fontSize: 'var(--font-size-md)' }}>
                <option value="">All</option>
                {['completed', 'not_interested', 'disconnected', 'voicemail', 'dnc', 'error', 'busy', 'no_answer'].map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: 'var(--font-size-md)' }}>
              Campaign
              <select value={filters.campaign_id} onChange={e => onFiltersChange({ ...filters, campaign_id: e.target.value })} style={{ fontSize: 'var(--font-size-md)' }}>
                <option value="">All</option>
                {campaigns.map(c => <option key={c._id} value={c.campaign_key}>{c.name}</option>)}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: 'var(--font-size-md)' }}>
              Bot
              <select value={filters.bot_id} onChange={e => onFiltersChange({ ...filters, bot_id: e.target.value })} style={{ fontSize: 'var(--font-size-md)' }}>
                <option value="">All</option>
                {bots.map(b => <option key={b._id} value={b._id}>{b.name}</option>)}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: 'var(--font-size-md)' }}>
              From date
              <input type="date" value={filters.start_date} onChange={e => onFiltersChange({ ...filters, start_date: e.target.value })} style={{ fontSize: 'var(--font-size-md)' }} />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: 'var(--font-size-md)' }}>
              To date
              <input
                type="date"
                value={filters.end_date}
                min={filters.start_date || undefined}
                onChange={e => onFiltersChange({ ...filters, end_date: e.target.value })}
                style={{ fontSize: 'var(--font-size-md)' }}
                aria-invalid={dateRangeInvalid}
              />
            </label>
            {dateRangeInvalid && (
              <span role="alert" style={{ fontSize: 'var(--font-size-sm)', color: 'var(--danger)', alignSelf: 'center' }}>
                "To date" can't be before "From date".
              </span>
            )}
            <button onClick={() => onFiltersChange({ status: '', outcome: '', campaign_id: '', bot_id: '', start_date: '', end_date: '' })} style={{ fontSize: 'var(--font-size-md)' }}>
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
                onClick={() => {
                  const atLastLoadedPage = page >= pageCount - 1;
                  if (atLastLoadedPage && hasMore && onLoadMore) {
                    onLoadMore();
                    setPage((p) => p + 1);
                  } else {
                    setPage((p) => Math.min(pageCount - 1, p + 1));
                  }
                }}
                disabled={page >= pageCount - 1 && !hasMore}
                aria-label="Next page"
              >
                {loadingMore && page >= pageCount - 1 ? 'Loading…' : <>Next <ChevronRight size={14} /></>}
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
              {selectedTranscript.bot_version_id && (
                <Detail label="Bot version" value={shortId(selectedTranscript.bot_version_id)} />
              )}
              {selectedTranscript.callback_status && (
                <Detail label="Callback" value={selectedTranscript.callback_status} />
              )}
              {maxResponseDelayLabel(selectedTranscript) !== '-' && (
                <Detail label="Max response delay" value={maxResponseDelayLabel(selectedTranscript)} />
              )}
              {!recordingUrl && <Detail label="Recording" value="No recording saved" />}
            </div>
            {((selectedTranscript.transcript_quality_flags || []).length > 0 || selectedTranscript.verified_transcript_error) && (
              <div className="source-strip">
                {(selectedTranscript.transcript_quality_flags || []).map((flag) => (
                  <span className="quality-flag" key={flag}>{titleCase(flag)}</span>
                ))}
                {selectedTranscript.verified_transcript_error && (
                  <span className="quality-flag error">{selectedTranscript.verified_transcript_error}</span>
                )}
              </div>
            )}
            {selectedTranscript.analysis ? (
              <div className="analysis-section">
                <div className="section-heading">
                  <h3>Call analysis</h3>
                </div>
                <div className={`outcome-callout outcome-${outcomeTone(selectedTranscript.analysis.call_outcome || '')}`}>
                  <span className="outcome-pill">{selectedTranscript.analysis.call_outcome || 'Unknown'}</span>
                  {selectedTranscript.analysis.call_outcome_description && (
                    <p>{selectedTranscript.analysis.call_outcome_description}</p>
                  )}
                </div>
                {selectedTranscript.analysis.call_summary && (
                  <DetailText label="Summary" value={selectedTranscript.analysis.call_summary} />
                )}
                <div className="detail-list">
                  {selectedTranscript.analysis.is_business && (
                    <Detail label="Business" value={selectedTranscript.analysis.is_business} />
                  )}
                  {selectedTranscript.analysis.business_name && (
                    <Detail label="Business name" value={selectedTranscript.analysis.business_name} />
                  )}
                  {selectedTranscript.analysis.business_city && (
                    <Detail label="Business city" value={selectedTranscript.analysis.business_city} />
                  )}
                  {selectedTranscript.analysis.lead_intent_score && (
                    <Detail label="Lead intent score" value={selectedTranscript.analysis.lead_intent_score} />
                  )}
                  {selectedTranscript.analysis.deal_value && (
                    <Detail label="Deal value" value={selectedTranscript.analysis.deal_value} />
                  )}
                  {selectedTranscript.analysis.urgency_flag && selectedTranscript.analysis.urgency_flag !== 'no' && (
                    <Detail label="Urgency" value={selectedTranscript.analysis.urgency_flag} />
                  )}
                  {selectedTranscript.analysis.rescheduled_to && (
                    <Detail label="Rescheduled to" value={selectedTranscript.analysis.rescheduled_to} />
                  )}
                </div>
                {(selectedTranscript.analysis.qna || []).length > 0 && (
                  <div className="analysis-qna">
                    {(selectedTranscript.analysis.qna || []).map((qa, index) => (
                      <div className="qna-item" key={qa.id || index}>
                        <strong>{qa.question || qa.id || `Q${index + 1}`}</strong>
                        <p>{String(qa.answer ?? '-')}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : !(selectedTranscript.analysis_fields_result && Object.keys(selectedTranscript.analysis_fields_result).length > 0) ? (
              // Skip the empty-state placeholder when "PM analysis fields" below already
              // covers this call — a Workflow bot with a configured schema has no legacy
              // `analysis` object at all (bot.py no longer fabricates one), and showing an
              // empty "Call analysis" card next to a populated "PM analysis fields" card
              // reads as broken rather than as "nothing to show here".
              <div className="analysis-section">
                <div className="section-heading">
                  <h3>Call analysis</h3>
                </div>
                <p className="muted">No analysis available for this call yet.</p>
              </div>
            ) : null}
            {selectedTranscript.analysis_fields_result && Object.keys(selectedTranscript.analysis_fields_result).length > 0 && (
              <div className="analysis-section">
                <div className="section-heading">
                  <h3>PM analysis fields</h3>
                </div>
                {selectedTranscript.analysis_fields_status === 'failed' && (
                  <p className="notice error" role="alert">
                    Analysis failed — results may be incomplete
                  </p>
                )}
                {selectedTranscript.analysis_fields_status === 'skipped' && (
                  <p className="muted">Not analyzed (no transcript content)</p>
                )}
                <div className="detail-list">
                  {Object.entries(selectedTranscript.analysis_fields_result).map(([key, value]) => (
                    <Detail key={key} label={titleCase(key)} value={value === null || value === undefined || value === '' ? '-' : String(value)} />
                  ))}
                </div>
              </div>
            )}
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
                ) : recordingBlobUrl ? (
                  <AudioPlayer src={recordingBlobUrl} />
                ) : (
                  <div className="audio-player-skeleton" aria-label="Loading recording" />
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
            {callEvents.length > 0 && (
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
                </div>
              </div>
            )}
          </>
        ) : <p className="muted">Select a transcript to inspect details.</p>}
      </div>
    </section>
  );
}
