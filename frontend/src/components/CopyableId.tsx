import React, { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { FEEDBACK_TIMEOUT_MS } from '../constants/ui';

export function CopyableId({ value, label }: { value?: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  if (!value || value === '-') return <span>{value || '-'}</span>;
  const safeValue: string = value;
  const display = label ?? safeValue;
  function flashCopied() {
    setCopied(true);
    setTimeout(() => setCopied(false), FEEDBACK_TIMEOUT_MS);
  }
  function handleCopy(e: React.MouseEvent) {
    e.stopPropagation();
    if (!navigator.clipboard) {
      // Fallback for non-HTTPS or older browsers
      try {
        const ta = document.createElement('textarea');
        ta.value = safeValue;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        flashCopied();
      } catch { /* silent */ }
      return;
    }
    navigator.clipboard.writeText(safeValue)
      .then(flashCopied)
      .catch(() => { /* permission denied or insecure context — fail silently */ });
  }
  return (
    <span className="copyable-id" title={`Click to copy: ${value}`} onClick={handleCopy}>
      <span className="copyable-id-text">{display}</span>
      <span className="copyable-id-icon">{copied ? <Check size={11} /> : <Copy size={11} />}</span>
    </span>
  );
}
