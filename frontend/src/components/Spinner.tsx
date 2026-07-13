import React from 'react';

export function Spinner({ label = 'Loading', size = 14 }: { label?: string; size?: number }) {
  return (
    <span className="spinner" role="status" aria-label={label} style={{ width: size, height: size }} />
  );
}
