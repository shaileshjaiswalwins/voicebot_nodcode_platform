import React from 'react';
import { AlertTriangle, CheckCircle2, GitBranch, Rocket } from 'lucide-react';
import type { BotVersion } from '../api';
import { Dialog } from './Dialog';
import { Spinner } from './Spinner';

export function PublishConfirmModal({
  latestDraft,
  activeVersion,
  activeCallCount,
  busy,
  onCancel,
  onConfirm,
  onViewFullDiff
}: {
  latestDraft?: BotVersion;
  activeVersion?: BotVersion;
  activeCallCount: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  /** Opens the full VersionDiffModal (JSON-level, every field) for the two versions this
   * modal already knows about. Optional so this modal still works when there's no prior
   * active version to diff against (first-ever publish). */
  onViewFullDiff?: () => void;
}) {
  // Publish is the highest-stakes action in the builder — until now this modal told you
  // "this will go live" with no indication of *what* is changing versus what's live today.
  // A shallow top-level-key diff here (mirrors VersionDiffModal's own comparison) turns
  // this into an informed decision instead of a leap of faith; "View full diff" opens the
  // real thing for anyone who wants the actual before/after values.
  const changedKeys = activeVersion && latestDraft
    ? Array.from(new Set([...Object.keys(activeVersion.config), ...Object.keys(latestDraft.config)]))
        .filter((k) => JSON.stringify(activeVersion.config[k]) !== JSON.stringify(latestDraft.config[k]))
        .sort()
    : [];

  return (
    <Dialog
      title={`Publish v${latestDraft?.version}?`}
      icon={<Rocket size={17} />}
      maxWidth={480}
      onClose={onCancel}
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
      footer={
        <>
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary" onClick={onConfirm} disabled={busy}>
            {busy ? <Spinner label="Publishing" size={13} /> : <Rocket size={15} />} {busy ? 'Publishing…' : `Publish v${latestDraft?.version}`}
          </button>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {activeCallCount > 0 && (
          <div className="notice" style={{ background: 'var(--warning-bg)', borderColor: 'var(--warning-border)', color: 'var(--warning)' }}>
            <AlertTriangle size={16} />
            <strong>{activeCallCount} call{activeCallCount > 1 ? 's' : ''} are currently in progress</strong> on {activeVersion ? `v${activeVersion.version}` : 'the active version'}. Those calls keep their snapshot and will not be interrupted. Only new calls will use v{latestDraft?.version}.
          </div>
        )}
        {activeCallCount === 0 && (
          <div className="callout success">
            <CheckCircle2 size={16} />
            No active calls detected. Safe to publish.
          </div>
        )}
        <p style={{ fontSize: '0.88rem', color: 'var(--text-2)' }}>
          Publishing makes v{latestDraft?.version} the active config for all <strong>new</strong> inbound and outbound calls. Existing in-flight calls are unaffected.
        </p>
        {activeVersion && (
          <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', padding: '0.6rem 0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem' }}>
              <strong style={{ fontSize: '0.82rem' }}>
                Changes since v{activeVersion.version} ({changedKeys.length} field{changedKeys.length === 1 ? '' : 's'})
              </strong>
              {onViewFullDiff && changedKeys.length > 0 && (
                <button
                  type="button"
                  onClick={onViewFullDiff}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.78rem', background: 'none', border: 'none', color: 'var(--primary)', cursor: 'pointer', padding: 0 }}
                >
                  <GitBranch size={13} /> View full diff
                </button>
              )}
            </div>
            {changedKeys.length === 0 ? (
              <p className="muted" style={{ fontSize: '0.8rem', margin: '0.35rem 0 0' }}>No config fields differ from the currently live version.</p>
            ) : (
              <ul style={{ margin: '0.4rem 0 0', paddingLeft: '1.1rem', fontSize: '0.8rem', color: 'var(--text-2)' }}>
                {changedKeys.map((k) => <li key={k}><code>{k}</code></li>)}
              </ul>
            )}
          </div>
        )}
      </div>
    </Dialog>
  );
}
