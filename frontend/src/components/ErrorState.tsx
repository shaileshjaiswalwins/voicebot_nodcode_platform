import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export function ErrorState({ heading, description, onRetry }: {
  heading: string;
  description: string;
  onRetry?: () => void;
}) {
  return (
    <div className="error-state" role="alert">
      <div className="error-state-icon"><AlertTriangle size={28} /></div>
      <strong>{heading}</strong>
      <p>{description}</p>
      {onRetry && <button className="primary" onClick={onRetry}><RefreshCw size={14} /> Retry</button>}
    </div>
  );
}
