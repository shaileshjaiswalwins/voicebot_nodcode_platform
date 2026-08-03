import { describe, it, expect, vi, afterEach } from 'vitest';
import { shortId, parseApiDate, formatDate, formatTime, titleCase, timeAgo } from './formatting';

describe('shortId', () => {
  it('returns "-" for an empty string', () => {
    expect(shortId('')).toBe('-');
  });

  it('leaves short ids unchanged', () => {
    expect(shortId('abc123')).toBe('abc123');
  });

  it('truncates ids longer than 12 chars to first8...last4', () => {
    expect(shortId('abcdefghijklmnop')).toBe('abcdefgh...mnop');
  });
});

describe('parseApiDate', () => {
  it('parses an already-timezoned ISO string as-is', () => {
    const d = parseApiDate('2026-01-01T00:00:00Z');
    expect(d.toISOString()).toBe('2026-01-01T00:00:00.000Z');
  });

  it('appends "Z" to a bare timestamp lacking timezone info', () => {
    const d = parseApiDate('2026-01-01T00:00:00');
    expect(d.toISOString()).toBe('2026-01-01T00:00:00.000Z');
  });

  it('respects an explicit +hh:mm offset', () => {
    const d = parseApiDate('2026-01-01T00:00:00+05:30');
    expect(d.toISOString()).toBe('2025-12-31T18:30:00.000Z');
  });
});

describe('formatDate / formatTime', () => {
  it('formatDate returns "-" for an undefined value', () => {
    expect(formatDate(undefined)).toBe('-');
  });

  it('formatDate returns the raw string for an invalid date', () => {
    expect(formatDate('not-a-date')).toBe('not-a-date');
  });

  it('formatDate appends "IST"', () => {
    expect(formatDate('2026-01-01T00:00:00Z')).toContain('IST');
  });

  it('formatTime returns "-" for an undefined value', () => {
    expect(formatTime(undefined)).toBe('-');
  });

  it('formatTime returns the raw string for an invalid date', () => {
    expect(formatTime('garbage')).toBe('garbage');
  });
});

describe('titleCase', () => {
  it('returns "-" for a falsy value', () => {
    expect(titleCase(undefined)).toBe('-');
    expect(titleCase('')).toBe('-');
  });

  it('splits on spaces, underscores, and hyphens and capitalizes each word', () => {
    expect(titleCase('short_hangup-call type')).toBe('Short Hangup Call Type');
  });

  it('collapses consecutive separators without producing empty words', () => {
    expect(titleCase('a__b--c')).toBe('A B C');
  });
});

describe('timeAgo', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns "-" for an undefined value', () => {
    expect(timeAgo(undefined)).toBe('-');
  });

  it('returns the raw string for an invalid date', () => {
    expect(timeAgo('not-a-date')).toBe('not-a-date');
  });

  it('returns "just now" under 60 seconds', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:30Z'));
    expect(timeAgo('2026-01-01T00:00:00Z')).toBe('just now');
  });

  it('returns "N min ago" under an hour', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:10:00Z'));
    expect(timeAgo('2026-01-01T00:00:00Z')).toBe('10 min ago');
  });

  it('returns "N hr ago" under a day', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T05:00:00Z'));
    expect(timeAgo('2026-01-01T00:00:00Z')).toBe('5 hr ago');
  });

  it('returns "yesterday" between 1 and 2 days', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-02T01:00:00Z'));
    expect(timeAgo('2026-01-01T00:00:00Z')).toBe('yesterday');
  });

  it('returns "N days ago" between 2 days and a week', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-04T00:00:00Z'));
    expect(timeAgo('2026-01-01T00:00:00Z')).toBe('3 days ago');
  });

  it('returns singular "week" for exactly one week-bucket', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-08T12:00:00Z'));
    expect(timeAgo('2026-01-01T00:00:00Z')).toBe('1 week ago');
  });

  it('returns plural "weeks" for more than one week-bucket', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-16T00:00:00Z'));
    expect(timeAgo('2026-01-01T00:00:00Z')).toBe('2 weeks ago');
  });

  it('returns "N month(s) ago" under a year', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-01T00:00:00Z'));
    expect(timeAgo('2026-01-01T00:00:00Z')).toBe('3 months ago');
  });

  it('returns "N year(s) ago" beyond a year', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2028-01-01T00:00:00Z'));
    expect(timeAgo('2026-01-01T00:00:00Z')).toBe('2 years ago');
  });
});
