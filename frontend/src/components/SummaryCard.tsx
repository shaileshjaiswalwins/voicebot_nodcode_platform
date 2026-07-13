import React from 'react';

export function SummaryCard({ icon, color, label, value, sub }: {
  icon: React.ReactNode;
  color: 'blue' | 'green' | 'amber' | 'red' | 'slate';
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div className="summary-card">
      <div className={`summary-icon ${color}`}>{icon}</div>
      <div className="summary-body">
        <div className="summary-label">{label}</div>
        <div className="summary-value">{value}</div>
        <div className="summary-sub">{sub}</div>
      </div>
    </div>
  );
}
