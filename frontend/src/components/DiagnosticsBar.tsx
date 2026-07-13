import React from 'react';
import { AlertTriangle, CheckCircle2, ClipboardList, RefreshCw } from 'lucide-react';
import type { Diagnostic } from '../types';

export function DiagnosticsBar({
  diagnostics,
  onClear,
  onRetry,
  onUseCache
}: {
  diagnostics: Diagnostic[];
  onClear: (scope?: string) => void;
  onRetry: () => void;
  onUseCache: () => void;
}) {
  if (!diagnostics.length) {
    return (
      <section className="diagnostics-bar healthy" id="diagnostics">
        <CheckCircle2 size={16} />
        <span>Dashboard diagnostics clear</span>
      </section>
    );
  }
  const latest = diagnostics[0];
  return (
    <section className={`diagnostics-bar ${latest.severity}`} id="diagnostics">
      <AlertTriangle size={17} />
      <div>
        <strong>{latest.scope}: {latest.message}</strong>
        <span>{latest.action || 'Retry, continue with cached data, or inspect the affected widget.'}</span>
      </div>
      <div className="diagnostics-actions">
        <button className="fallback-button" onClick={onRetry}><RefreshCw size={15} /> Retry</button>
        <button className="fallback-button" onClick={onUseCache}><ClipboardList size={15} /> Use cached</button>
        <button onClick={() => onClear(latest.scope)}>Dismiss</button>
      </div>
    </section>
  );
}
