import React from 'react';
import { AlertTriangle, CheckCircle2, Rocket } from 'lucide-react';
import type { BotVersion } from '../api';
import { Dialog } from './Dialog';
import { Spinner } from './Spinner';

export function PublishConfirmModal({
  latestDraft,
  activeVersion,
  activeCallCount,
  busy,
  onCancel,
  onConfirm
}: {
  latestDraft?: BotVersion;
  activeVersion?: BotVersion;
  activeCallCount: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
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
      </div>
    </Dialog>
  );
}
