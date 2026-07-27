import React, { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, ClipboardList, RefreshCw } from 'lucide-react';
import type { AuditLogEntry } from '../api';
import { api } from '../api';
import { CopyableId } from '../components/CopyableId';
import { TimeAgo } from '../components/TimeAgo';
import { EmptyState } from '../components/EmptyState';
import { SkeletonTableBody } from '../components/SkeletonTableBody';
import { titleCase } from '../utils/formatting';

const PAGE_SIZE = 50;

const RESOURCE_TYPES = ['bot', 'phone_number', 'campaign', 'platform_settings', 'runtime_settings', 'langfuse_settings'];

function summarizeDetails(details?: Record<string, unknown>): string {
  if (!details || Object.keys(details).length === 0) return '';
  return Object.entries(details).map(([k, v]) => `${k}: ${v}`).join(', ');
}

export function AuditLogView() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [resourceType, setResourceType] = useState('');
  const [error, setError] = useState('');

  const load = React.useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await api.auditLog({
        resource_type: resourceType || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setEntries(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit log');
    } finally {
      setLoading(false);
    }
  }, [resourceType, page]);

  useEffect(() => { load(); }, [load]);

  // Reset to page 1 whenever the filter changes.
  useEffect(() => { setPage(0); }, [resourceType]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="content-grid">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Audit log</h2>
            <p>Every admin mutation — publish, rollback, delete, reassign, and settings changes — recorded with who did it and when.</p>
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <select value={resourceType} onChange={(e) => setResourceType(e.target.value)} style={{ fontSize: '0.85rem' }}>
              <option value="">All resource types</option>
              {RESOURCE_TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
            </select>
            <button title="Refresh" onClick={load}>
              <RefreshCw size={14} />
            </button>
          </div>
        </div>

        {error && <p className="notice error" role="alert">{error}</p>}

        <div className="table-scroll"><table>
          <thead>
            <tr><th>Time</th><th>Actor</th><th>Action</th><th>Resource</th><th>Details</th></tr>
          </thead>
          <tbody>
            {loading && !entries.length ? (
              <SkeletonTableBody cols={5} rows={8} />
            ) : entries.length === 0 ? (
              <tr><td colSpan={5}>
                <EmptyState
                  icon={<ClipboardList size={32} />}
                  heading="No audit entries yet"
                  description="Publishing a bot, reassigning a phone number, or changing settings will show up here."
                />
              </td></tr>
            ) : entries.map((entry) => (
              <tr key={entry._id}>
                <td><TimeAgo value={entry.created_at} /></td>
                <td>{entry.actor}</td>
                <td>{titleCase(entry.action)}</td>
                <td>
                  {titleCase(entry.resource_type)}
                  {entry.resource_id && <><br /><CopyableId value={entry.resource_id} /></>}
                </td>
                <td><small>{summarizeDetails(entry.details) || '-'}</small></td>
              </tr>
            ))}
          </tbody>
        </table></div>

        {total > 0 && (
          <div className="pagination-bar">
            <span className="muted">
              {page * PAGE_SIZE + 1}–{Math.min(total, page * PAGE_SIZE + PAGE_SIZE)} of {total}
            </span>
            <div className="pagination-controls">
              <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0} aria-label="Previous page">
                <ChevronLeft size={14} /> Prev
              </button>
              <span className="muted">Page {page + 1} of {pageCount}</span>
              <button onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={page >= pageCount - 1} aria-label="Next page">
                Next <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
