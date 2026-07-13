import React from 'react';
import { CheckCircle2 } from 'lucide-react';

export function ConnectionLine({ icon, label, value, done }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  done: boolean;
}) {
  return (
    <div className="connection-line">
      <div className={done ? 'connection-icon done' : 'connection-icon'}>{done ? <CheckCircle2 size={17} /> : icon}</div>
      <div><strong>{label}</strong><span>{value}</span></div>
    </div>
  );
}
