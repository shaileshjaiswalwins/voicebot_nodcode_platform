import React from 'react';

export function NavItem({ icon, label, active, onClick, tourId }: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  tourId?: string;
}) {
  return (
    <button id={tourId} className={`nav-item ${active ? 'active' : ''}`} onClick={onClick} title={label}>
      {icon}<span>{label}</span>
    </button>
  );
}
