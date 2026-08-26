import { afterEach, describe, expect, it, vi } from 'vitest';

import { fmt, pct, relTime, shortTime } from './format';

describe('fmt', () => {
  it('renders an em-dash placeholder for missing numbers', () => {
    expect(fmt(null)).toBe('—');
    expect(fmt(undefined)).toBe('—');
    expect(fmt(Number.NaN)).toBe('—');
  });

  it('localizes integers', () => {
    expect(fmt(12345)).toBe((12345).toLocaleString());
  });

  it('fixes floats to three digits by default', () => {
    expect(fmt(0.123456)).toBe('0.123');
    expect(fmt(0.123456, 2)).toBe('0.12');
  });

  it('passes non-numbers through as strings', () => {
    expect(fmt('n/a')).toBe('n/a');
  });

  it('keeps zero', () => {
    expect(fmt(0)).toBe('0');
  });
});

describe('pct', () => {
  it('renders a percentage with one digit by default', () => {
    expect(pct(0.123)).toBe('12.3%');
    expect(pct(1)).toBe('100.0%');
    expect(pct(0.5, 0)).toBe('50%');
  });

  it('renders an em-dash placeholder for null', () => {
    expect(pct(null)).toBe('—');
    expect(pct(undefined)).toBe('—');
  });
});

describe('shortTime', () => {
  it('renders an em-dash placeholder for missing input', () => {
    expect(shortTime(null)).toBe('—');
    expect(shortTime('')).toBe('—');
  });

  it('returns the raw string when it is not a date', () => {
    expect(shortTime('not a date')).toBe('not a date');
  });

  it('formats a valid ISO timestamp with the locale short form', () => {
    const iso = '2026-08-26T12:34:00Z';
    expect(shortTime(iso)).toBe(
      new Date(iso).toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      }),
    );
  });
});

describe('relTime', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  function at(now: string): void {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(now));
  }

  it('renders an em-dash placeholder for missing input', () => {
    expect(relTime(null)).toBe('—');
  });

  it('returns the raw string when it is not a date', () => {
    expect(relTime('not a date')).toBe('not a date');
  });

  it('walks the minute/hour/day boundaries', () => {
    at('2026-08-26T12:00:00Z');
    expect(relTime('2026-08-26T11:59:30Z')).toBe('just now');
    expect(relTime('2026-08-26T11:59:00Z')).toBe('1m ago');
    expect(relTime('2026-08-26T11:55:00Z')).toBe('5m ago');
    expect(relTime('2026-08-26T09:00:00Z')).toBe('3h ago');
    expect(relTime('2026-08-24T12:00:00Z')).toBe('2d ago');
  });

  it('falls back to shortTime beyond 30 days', () => {
    at('2026-08-26T12:00:00Z');
    const old = '2026-06-01T12:00:00Z';
    expect(relTime(old)).toBe(shortTime(old));
  });

  it('clamps future timestamps to "just now"', () => {
    at('2026-08-26T12:00:00Z');
    expect(relTime('2026-08-26T13:00:00Z')).toBe('just now');
  });
});
