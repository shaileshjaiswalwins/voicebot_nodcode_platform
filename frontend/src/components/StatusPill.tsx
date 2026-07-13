import React from 'react';
import { STATUS_ICONS } from '../constants/ui';

export function StatusPill({ value }: { value: string }) {
  const key = value.toLowerCase().replace(/\s+/g, '_');
  const icon = STATUS_ICONS[key];
  return (
    <span className={`pill ${key}`}>
      {icon && <span className="pill-icon">{icon}</span>}
      {value}
    </span>
  );
}
