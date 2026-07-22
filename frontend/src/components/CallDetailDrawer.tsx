import React, { useEffect, useState } from 'react';
import { Dialog } from './Dialog';
import { api, type CallDetail } from '../api';

/** Call detail drawer for a campaign lead: transcript, cost breakdown, latency breakdown.
 * Fetches from GET /api/campaigns/{key}/calls/{call_id}, which merges the campaign's own
 * cost/latency call_logs with the pre-existing transcripts collection. */
export function CallDetailDrawer({ campaignKey, callId, onClose }: { campaignKey: string; callId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getCallDetail(campaignKey, callId)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch(() => { if (!cancelled) setError('Could not load call detail.'); });
    return () => { cancelled = true; };
  }, [campaignKey, callId]);

  const totalCost = detail ? Object.values(detail.cost_inr || {}).reduce((a, b) => a + b, 0) : 0;

  return (
    <Dialog title={`Call ${callId}`} onClose={onClose}>
      {error && <p className="csv-dropzone-error">{error}</p>}
      {!error && !detail && <p>Loading...</p>}
      {detail && (
        <div className="call-detail-drawer">
          <div className="call-detail-section">
            <strong>Cost breakdown</strong>
            <ul>
              {Object.entries(detail.cost_inr || {}).map(([k, v]) => <li key={k}>{k}: ₹{v.toFixed(2)}</li>)}
              {Object.keys(detail.cost_inr || {}).length > 0 && <li><strong>Total: ₹{totalCost.toFixed(2)}</strong></li>}
              {Object.keys(detail.cost_inr || {}).length === 0 && <li className="muted">No cost data recorded</li>}
            </ul>
          </div>
          <div className="call-detail-section">
            <strong>Latency breakdown (ms)</strong>
            <ul>
              {Object.entries(detail.latency_ms || {}).map(([k, v]) => <li key={k}>{k}: {v}ms</li>)}
              {Object.keys(detail.latency_ms || {}).length === 0 && <li className="muted">No latency data recorded</li>}
            </ul>
          </div>
          <div className="call-detail-section">
            <strong>Transcript</strong>
            {detail.transcript.length === 0 ? (
              <p className="muted">No transcript available</p>
            ) : (
              <div className="call-detail-transcript">
                {detail.transcript.map((turn, i) => (
                  <p key={i}><strong>{turn.role || 'unknown'}:</strong> {String(turn.text || '')}</p>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}
