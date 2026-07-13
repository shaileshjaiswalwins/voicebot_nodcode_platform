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
