import React from 'react';

export function SkeletonTableBody({ cols, rows = 4 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="skeleton-row" aria-hidden>
          {Array.from({ length: cols }).map((_, c) => (
            <td key={c}><span className="skeleton-cell" style={{ width: `${55 + ((r * 37 + c * 29) % 35)}%` }} /></td>
          ))}
        </tr>
      ))}
    </>
  );
}
