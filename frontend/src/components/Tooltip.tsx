import React, { useState } from 'react';

export function Tooltip({ label, children }: { label: string; children: React.ReactElement }) {
  const [visible, setVisible] = useState(false);
  return (
    <span
      className="tooltip-wrapper"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
      onKeyDown={e => { if (e.key === 'Escape') setVisible(false); }}
    >
      {children}
      {visible && <span className="tooltip-bubble" role="tooltip">{label}</span>}
    </span>
  );
}
