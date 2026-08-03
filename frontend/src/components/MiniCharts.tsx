import React from 'react';

/** Small dependency-free inline SVG charts — this codebase has no charting library
 * installed and prefers to avoid adding one for a couple of trend charts. */

const CHART_HEIGHT = 120;
const CHART_WIDTH = 480;
const PADDING = 24;

export function MiniBarChart({ data }: { data: { label: string; value: number }[] }) {
  if (data.length === 0) return null;
  const max = Math.max(1, ...data.map(d => d.value));
  const barWidth = (CHART_WIDTH - PADDING * 2) / data.length;
  return (
    <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} style={{ width: '100%', height: 'auto', maxHeight: 160 }}>
      {data.map((d, i) => {
        const h = (d.value / max) * (CHART_HEIGHT - PADDING * 2);
        const x = PADDING + i * barWidth;
        const y = CHART_HEIGHT - PADDING - h;
        return (
          <g key={d.label + i}>
            <rect x={x + 1} y={y} width={Math.max(1, barWidth - 2)} height={h} fill="var(--accent)" rx={1} />
            <title>{`${d.label}: ${d.value}`}</title>
            {(i === 0 || i === data.length - 1 || i === Math.floor(data.length / 2)) && (
              <text x={x + barWidth / 2} y={CHART_HEIGHT - 6} fontSize="8" textAnchor="middle" fill="var(--text-secondary)">
                {d.label}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function MiniLineChart({ data, unit }: { data: { label: string; value: number }[]; unit?: string }) {
  if (data.length === 0) return null;
  const max = Math.max(1, ...data.map(d => d.value));
  const stepX = (CHART_WIDTH - PADDING * 2) / Math.max(1, data.length - 1);
  const points = data.map((d, i) => {
    const x = PADDING + i * stepX;
    const y = CHART_HEIGHT - PADDING - (d.value / max) * (CHART_HEIGHT - PADDING * 2);
    return { x, y, ...d };
  });
  const polyline = points.map(p => `${p.x},${p.y}`).join(' ');
  return (
    <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} style={{ width: '100%', height: 'auto', maxHeight: 160 }}>
      <polyline points={polyline} fill="none" stroke="var(--accent)" strokeWidth={2} />
      {points.map((p, i) => (
        <g key={p.label + i}>
          <circle cx={p.x} cy={p.y} r={2.5} fill="var(--accent)" />
          <title>{`${p.label}: ${p.value}${unit || ''}`}</title>
          {(i === 0 || i === points.length - 1 || i === Math.floor(points.length / 2)) && (
            <text x={p.x} y={CHART_HEIGHT - 6} fontSize="8" textAnchor="middle" fill="var(--text-secondary)">
              {p.label}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}
