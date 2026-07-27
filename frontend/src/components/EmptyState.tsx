import React from 'react';

export function EmptyState({ icon, heading, description, action }: {
  icon: React.ReactNode;
  heading: string;
  description: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">{icon}</div>
      <strong>{heading}</strong>
      <p>{description}</p>
      {action && <button className="primary" onClick={action.onClick}>{action.label}</button>}
    </div>
  );
}
