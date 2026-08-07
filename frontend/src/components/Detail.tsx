import React from 'react';
import { CopyableId } from './CopyableId';

export function Detail({ label, value, copyable }: { label: string; value: string; copyable?: boolean }) {
  return (
    <div className="detail">
      <span>{label}</span>
      {copyable && value !== '-' ? <CopyableId value={value} label={value} /> : <strong>{value}</strong>}
    </div>
  );
}

/** For long free-text values (a summary, a description) that don't fit the compact
 * label-left/value-right row above — label sits as a small caption on its own line,
 * with the full sentence wrapping cleanly underneath instead of squeezing next to it. */
export function DetailText({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail-text">
      <span>{label}</span>
      <p>{value}</p>
    </div>
  );
}
