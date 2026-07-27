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
  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    handleCopy(e as unknown as React.MouseEvent);
  }
  return (
    <span
      className="copyable-id"
      role="button"
      tabIndex={0}
      aria-label={copied ? `Copied ${safeValue}` : `Copy ${safeValue}`}
      title={copied ? 'Copied' : `Click to copy: ${value}`}
      onClick={handleCopy}
      onKeyDown={handleKeyDown}
    >
      <span className="copyable-id-text">{display}</span>
      <span className="copyable-id-icon">{copied ? <Check size={11} /> : <Copy size={11} />}</span>
    </span>
  );
}
