import { beforeEach, describe, expect, it } from 'vitest';

import { isViewName, readHashParams, VIEW_NAMES, writeHash } from './hash';

function resetHash(): void {
  window.history.replaceState(null, '', window.location.pathname + window.location.search);
}

describe('readHashParams', () => {
  beforeEach(resetHash);

  it('parses #view=journey&project=1&launch=42', () => {
    window.location.hash = '#view=journey&project=1&launch=42';
    expect(readHashParams()).toEqual({ view: 'journey', project: '1', launch: '42' });
  });

  it('returns an empty object when there is no hash', () => {
    expect(readHashParams()).toEqual({});
  });

  it('decodes percent-escaped keys and values', () => {
    window.location.hash = '#q=a%20b%26c';
    expect(readHashParams()).toEqual({ q: 'a b&c' });
  });

  it('treats a bare key as an empty value and skips empty segments', () => {
    window.location.hash = '#conflicts&&view=llm';
    expect(readHashParams()).toEqual({ conflicts: '', view: 'llm' });
  });
});

describe('writeHash', () => {
  beforeEach(resetHash);

  it('writes view and project for a view with no own params', () => {
    writeHash('drain', 3, {});
    expect(window.location.hash).toBe('#view=drain&project=3');
  });

  it('serializes journey launch and item', () => {
    writeHash('journey', 1, { launch: 42, item: 7 });
    expect(window.location.hash).toBe('#view=journey&project=1&launch=42&item=7');
  });

  it('serializes only active-view params: switching to signatures drops launch/item', () => {
    writeHash('signatures', 1, { launch: 42, item: 7, q: 'boom' });
    expect(window.location.hash).toBe('#view=signatures&project=1&q=boom');
  });

  it('serializes glaunch as launch for groups view', () => {
    writeHash('groups', 2, { glaunch: 9, launch: 42, item: 7 });
    expect(window.location.hash).toBe('#view=groups&project=2&launch=9');
  });

  it('drops null/empty params and writes conflicts as 1', () => {
    writeHash('signatures', 1, { q: '', conflicts: true, hash: null });
    expect(window.location.hash).toBe('#view=signatures&project=1&conflicts=1');
  });

  it('omits conflicts when false', () => {
    writeHash('signatures', 1, { q: 'x', conflicts: false });
    expect(window.location.hash).toBe('#view=signatures&project=1&q=x');
  });

  it('drops a null project', () => {
    writeHash('journey', null, { launch: null, item: null });
    expect(window.location.hash).toBe('#view=journey');
  });

  it('serializes llm and rubric params', () => {
    writeHash('llm', 1, { lrole: 'judge', loutcome: 'ok' });
    expect(window.location.hash).toBe('#view=llm&project=1&lrole=judge&loutcome=ok');
    writeHash('rubric', 1, { rule: 'R6' });
    expect(window.location.hash).toBe('#view=rubric&project=1&rule=R6');
  });

  it('percent-encodes values', () => {
    writeHash('signatures', 1, { q: 'a&b c' });
    expect(window.location.hash).toBe('#view=signatures&project=1&q=a%26b%20c');
  });

  it('round-trips through readHashParams', () => {
    writeHash('signatures', 1, { q: 'a&b', conflicts: true, hash: 'deadbeef' });
    expect(readHashParams()).toEqual({
      view: 'signatures',
      project: '1',
      q: 'a&b',
      conflicts: '1',
      hash: 'deadbeef',
    });
  });

  it('keeps path and search untouched (replaceState, no reload)', () => {
    const before = window.location.pathname + window.location.search;
    writeHash('modes', 5, {});
    expect(window.location.pathname + window.location.search).toBe(before);
  });
});

describe('view names', () => {
  it('lists the eight views in tab order', () => {
    expect(VIEW_NAMES).toEqual([
      'journey',
      'signatures',
      'drain',
      'modes',
      'groups',
      'loop',
      'llm',
      'rubric',
    ]);
  });

  it('rejects unknown view names', () => {
    expect(isViewName('journey')).toBe(true);
    expect(isViewName('nope')).toBe(false);
    expect(isViewName(null)).toBe(false);
  });
});
