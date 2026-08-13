import type { AlertFrequency, AlertMetric, AlertWindow } from '../types';

/** Window → supported frequencies, copied verbatim from Retell's own compatibility table
 * (see the "Part 1: Custom Alerting" plan doc) — a 1-minute window can't pair with a
 * 24-hour frequency, etc. Drives the Create Alert dialog's live validation: the frequency
 * dropdown disables any option not present here for the currently-selected window. */
export const WINDOW_FREQUENCY_COMPAT: Record<AlertWindow, AlertFrequency[]> = {
  '5m': ['1m', '5m'],
  '30m': ['5m', '30m'],
  '1h': ['5m', '30m', '1h'],
  '12h': ['30m', '1h', '12h'],
  '24h': ['1h', '12h'],
};

export const ALERT_WINDOW_OPTIONS: Array<{ value: AlertWindow; label: string }> = [
  { value: '5m', label: '5 minutes' },
  { value: '30m', label: '30 minutes' },
  { value: '1h', label: '1 hour' },
  { value: '12h', label: '12 hours' },
  { value: '24h', label: '24 hours' },
];

export const ALERT_FREQUENCY_OPTIONS: Array<{ value: AlertFrequency; label: string }> = [
  { value: '1m', label: '1 minute' },
  { value: '5m', label: '5 minutes' },
  { value: '30m', label: '30 minutes' },
  { value: '1h', label: '1 hour' },
  { value: '12h', label: '12 hours' },
];

/** V1 metric catalog (6), grouped Call / Latency / System since there's no API/Chat/QA
 * grouping to mirror on this platform (see plan doc's metric parity matrix). */
export const ALERT_METRIC_GROUPS: Array<{ group: string; metrics: Array<{ value: AlertMetric; label: string }> }> = [
  {
    group: 'Call',
    metrics: [
      { value: 'call_count', label: 'Number of calls' },
      { value: 'task_completion_rate_pct', label: 'Task completion rate (%)' },
    ],
  },
  {
    group: 'Latency',
    metrics: [
      { value: 'p95_turn_latency_ms', label: 'P95 turn latency (ms)' },
    ],
  },
  {
    group: 'System',
    metrics: [
      { value: 'session_error_count', label: 'Session error count' },
      { value: 'concurrency_used', label: 'Concurrency used' },
      { value: 'platform_error_rate_pct', label: 'Platform error rate (%)' },
    ],
  },
];

export const ALERT_METRIC_LABELS: Record<AlertMetric, string> = ALERT_METRIC_GROUPS
  .flatMap((g) => g.metrics)
  .reduce((acc, m) => ({ ...acc, [m.value]: m.label }), {} as Record<AlertMetric, string>);

export const ALERT_COMPARATOR_LABELS: Record<string, string> = {
  gt: '>',
  lt: '<',
  ge: '>=',
  le: '<=',
};

/** localStorage key holding the ISO timestamp of the last time the History sub-tab was
 * viewed — the unread badge on the Alerts settings tab counts open incidents triggered
 * after this timestamp. Kept simple per the plan (local state, not a server-tracked flag). */
export const ALERTS_LAST_VIEWED_HISTORY_KEY = 'alerts_history_last_viewed_at';
