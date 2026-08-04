import React from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from 'recharts';

const CHART_HEIGHT = 160;

function tickIndexes(length: number) {
  return new Set([0, Math.floor(length / 2), length - 1]);
}

export function MiniBarChart({ data }: { data: { label: string; value: number }[] }) {
  if (data.length === 0) return null;
  const shown = tickIndexes(data.length);
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <BarChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
        <XAxis
          dataKey="label"
          axisLine={false}
          tickLine={false}
          interval={0}
          tick={(props) => {
            const { x, y, payload, index } = props;
            if (!shown.has(index)) return <g />;
            return (
              <text x={x} y={Number(y) + 12} fontSize={10} textAnchor="middle" fill="var(--text-secondary)">
                {payload.value}
              </text>
            );
          }}
        />
        <Tooltip
          cursor={{ fill: 'var(--bg-tertiary)' }}
          contentStyle={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 6,
            fontSize: 12,
          }}
        />
        <Bar dataKey="value" fill="var(--accent)" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function MiniLineChart({ data, unit }: { data: { label: string; value: number }[]; unit?: string }) {
  if (data.length === 0) return null;
  const shown = tickIndexes(data.length);
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <LineChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
        <XAxis
          dataKey="label"
          axisLine={false}
          tickLine={false}
          interval={0}
          tick={(props) => {
            const { x, y, payload, index } = props;
            if (!shown.has(index)) return <g />;
            return (
              <text x={x} y={Number(y) + 12} fontSize={10} textAnchor="middle" fill="var(--text-secondary)">
                {payload.value}
              </text>
            );
          }}
        />
        <Tooltip
          cursor={{ stroke: 'var(--border)' }}
          formatter={(value) => [`${value ?? ''}${unit || ''}`, '']}
          contentStyle={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 6,
            fontSize: 12,
          }}
        />
        <Line type="monotone" dataKey="value" stroke="var(--accent)" strokeWidth={2} dot={{ r: 2.5 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
