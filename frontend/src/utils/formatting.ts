export function shortId(value: string) {
  if (!value) return '-';
  return value.length > 12 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

export function parseApiDate(value: string) {
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

export function formatDate(value?: string) {
  if (!value) return '-';
  const date = parseApiDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toLocaleString('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Kolkata'
  })} IST`;
}

export function formatTime(value?: string) {
  if (!value) return '-';
  const date = parseApiDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'Asia/Kolkata'
  })} IST`;
}

export function titleCase(value?: string) {
  if (!value) return '-';
  return value
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function timeAgo(value?: string): string {
  if (!value) return '-';
  const date = parseApiDate(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffSec = (Date.now() - date.getTime()) / 1000;
  if (diffSec < 60) return 'just now';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} min ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} hr ago`;
  if (diffSec < 172800) return 'yesterday';
  return formatDate(value);
}
