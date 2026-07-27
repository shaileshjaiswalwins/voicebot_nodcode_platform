import React from 'react';
import { GitBranch } from 'lucide-react';
import type { BotVersion } from '../api';
import { Dialog } from './Dialog';

export function VersionDiffModal({ versionA, versionB, onClose }: { versionA: BotVersion; versionB: BotVersion; onClose: () => void }) {
  const allKeys = Array.from(new Set([...Object.keys(versionA.config), ...Object.keys(versionB.config)]));
  const diffs = allKeys.filter(k => JSON.stringify(versionA.config[k]) !== JSON.stringify(versionB.config[k]));
  const same = allKeys.filter(k => !diffs.includes(k));

  return (
    <Dialog
      title={`Version diff — v${versionA.version} vs v${versionB.version}`}
      icon={<GitBranch size={17} />}
      maxWidth={720}
      onClose={onClose}
      footer={<button onClick={onClose}>Close</button>}
    >
      <div style={{ maxHeight: '70vh', overflowY: 'auto' }}>
        {diffs.length === 0 && <p className="muted">No differences found between these two versions.</p>}
        {diffs.length > 0 && (
          <>
            <div style={{ fontSize: '0.73rem', fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem' }}>Changed fields ({diffs.length})</div>
            {diffs.map(key => (
              <div key={key} style={{ marginBottom: '0.75rem', borderRadius: '6px', overflow: 'hidden', border: '1px solid var(--border)' }}>
                <div style={{ background: 'var(--surface-2)', padding: '4px 10px', fontSize: '0.78rem', fontWeight: 700 }}>{key}</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0 }}>
                  <div style={{ background: '#fef2f2', padding: '8px 10px', fontSize: '0.78rem', borderRight: '1px solid var(--border)' }}>
                    <div style={{ fontSize: '0.68rem', color: '#b91c1c', marginBottom: '3px' }}>v{versionA.version}</div>
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{JSON.stringify(versionA.config[key], null, 2)}</pre>
                  </div>
                  <div style={{ background: '#f0fdf4', padding: '8px 10px', fontSize: '0.78rem' }}>
                    <div style={{ fontSize: '0.68rem', color: '#15803d', marginBottom: '3px' }}>v{versionB.version}</div>
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{JSON.stringify(versionB.config[key], null, 2)}</pre>
                  </div>
                </div>
              </div>
            ))}
            {same.length > 0 && (
              <details style={{ marginTop: '0.5rem' }}>
                <summary style={{ fontSize: '0.78rem', color: 'var(--muted)', cursor: 'pointer' }}>Unchanged fields ({same.length})</summary>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '4px' }}>
                  {same.map(k => <code key={k} style={{ fontSize: '0.72rem', background: 'var(--surface-2)', padding: '2px 6px', borderRadius: '4px' }}>{k}</code>)}
                </div>
              </details>
            )}
          </>
        )}
      </div>
    </Dialog>
  );
}
