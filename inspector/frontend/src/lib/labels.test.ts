import { Children, isValidElement, type ReactElement, type ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';

import {
  defectColor,
  defectForGroup,
  defectInfo,
  defectName,
  labelGroup,
  labelName,
  renderWithDefectNames,
  setDefects,
  srcInfo,
} from './labels';

afterEach(() => {
  setDefects({});
});

describe('labelGroup', () => {
  it('matches the defect-group prefix of a locator', () => {
    expect(labelGroup('pb001')).toBe('pb');
    expect(labelGroup('ab_1iupzjso5bh9t')).toBe('ab');
    expect(labelGroup('si')).toBe('si');
    expect(labelGroup('nd001')).toBe('nd');
    expect(labelGroup('ti001')).toBe('ti');
  });

  it('returns none for an empty locator and other for an unknown prefix', () => {
    expect(labelGroup(null)).toBe('none');
    expect(labelGroup('')).toBe('none');
    expect(labelGroup('zz9')).toBe('other');
  });
});

describe('labelName', () => {
  it('names the known groups', () => {
    expect(labelName('pb')).toBe('Product bug');
    expect(labelName('ab')).toBe('Automation bug');
    expect(labelName('si')).toBe('System issue');
    expect(labelName('nd')).toBe('No defect');
    expect(labelName('ti')).toBe('To investigate');
    expect(labelName('none')).toBe('Unlabeled');
    expect(labelName('other')).toBe('Other');
  });

  it('passes an unknown group through', () => {
    expect(labelName('zz')).toBe('zz');
  });
});

describe('defect resolution', () => {
  it('falls back to "<Name> group" when there is no RP defect type', () => {
    expect(defectName('pb001')).toBe('Product bug group');
    expect(defectName(null, 'si')).toBe('System issue group');
    expect(defectName('zz9')).toBe('Other');
  });

  it('resolves names, colors and info once RP names are loaded', () => {
    setDefects({
      pb001: { name: 'Product Bug', short_name: 'PB', color: '#d32f2f' },
      ab_custom: { name: 'Flaky infrastructure automation bug', short_name: 'FIAB', color: '#ffc208' },
    });
    expect(defectInfo('pb001')).toEqual({ name: 'Product Bug', short_name: 'PB', color: '#d32f2f' });
    expect(defectInfo('nope')).toBeNull();
    expect(defectInfo(null)).toBeNull();
    expect(defectName('pb001')).toBe('Product Bug');
    expect(defectColor('pb001')).toBe('#d32f2f');
    expect(defectForGroup('ab')?.short_name).toBe('FIAB');
    expect(defectForGroup('si')).toBeNull();
  });

  it('borrows the group representative colour for a bare group code', () => {
    setDefects({ pb001: { name: 'Product Bug', short_name: 'PB', color: '#123456' } });
    expect(defectColor('pb')).toBe('#123456');
    // ...but never its name — a group code is not a defect type.
    expect(defectName('pb')).toBe('Product bug group');
  });

  it('falls back to the palette colour when nothing is resolvable', () => {
    expect(defectColor('zz9')).toMatch(/^#/);
  });
});

describe('srcInfo', () => {
  it('describes a known label_event source', () => {
    expect(srcInfo('rp_defect_update')).toEqual({
      k: 'rp',
      text: 'defect update',
      plain: 'human · RP',
      actor: 'human (RP defect edit)',
      weight: 1.0,
      raw: 'rp_defect_update',
    });
  });

  it('describes a known feature-vector label_source token', () => {
    expect(srcInfo('ai_suggested').weight).toBe(0.3);
    expect(srcInfo('seed').weight).toBe(0.6);
    expect(srcInfo('human_ui').weight).toBe(0.9);
  });

  it('passes an unknown token through honestly', () => {
    expect(srcInfo('mystery')).toEqual({
      k: 'mystery',
      text: '',
      plain: 'mystery',
      actor: 'mystery',
      weight: null,
      raw: 'mystery',
    });
  });

  it('reports an unrecorded actor for a missing token', () => {
    expect(srcInfo(null)).toEqual({
      k: '—',
      text: '',
      plain: '—',
      actor: 'unrecorded actor',
      weight: null,
      raw: null,
    });
  });
});

function childrenOf(node: ReactNode): ReactNode[] {
  const el = node as ReactElement<{ children?: ReactNode }>;
  return Children.toArray(el.props.children);
}

describe('renderWithDefectNames', () => {
  it('leaves prose untouched when no locator is known', () => {
    expect(childrenOf(renderWithDefectNames('inherited pb001 from item 7'))).toEqual([
      'inherited pb001 from item 7',
    ]);
  });

  it('replaces a known locator with an inline defect pill', () => {
    setDefects({ pb001: { name: 'Product Bug', short_name: 'PB', color: '#d32f2f' } });
    const parts = childrenOf(renderWithDefectNames('inherited pb001 from item 7'));
    expect(parts).toHaveLength(3);
    expect(parts[0]).toBe('inherited ');
    expect(parts[2]).toBe(' from item 7');
    const pill = parts[1] as ReactElement<{ className: string; title: string; children: ReactNode }>;
    expect(isValidElement(pill)).toBe(true);
    expect(pill.props.className).toBe('defect-inline');
    expect(pill.props.title).toBe('Product Bug · pb001');
    expect(Children.toArray(pill.props.children).at(-1)).toBe('Product Bug');
  });

  it('shortens names longer than 18 characters', () => {
    setDefects({ ab1: { name: 'Flaky infrastructure automation bug', short_name: 'FIAB', color: '#ffc208' } });
    const pill = childrenOf(renderWithDefectNames('ab1'))[0] as ReactElement<{ children: ReactNode }>;
    expect(Children.toArray(pill.props.children).at(-1)).toBe('FIAB');
  });

  it('respects word boundaries — pb001x is not pb001', () => {
    setDefects({ pb001: { name: 'Product Bug', short_name: 'PB', color: '#d32f2f' } });
    expect(childrenOf(renderWithDefectNames('code pb001x here'))).toEqual(['code pb001x here']);
  });

  it('prefers the longest match at the same position', () => {
    setDefects({
      pb: { name: 'Short', short_name: 'S', color: '#111111' },
      pb001: { name: 'Long', short_name: 'L', color: '#222222' },
    });
    const pill = childrenOf(renderWithDefectNames('pb001'))[0] as ReactElement<{ title: string }>;
    expect(pill.props.title).toBe('Long · pb001');
  });

  it('renders empty text as no children', () => {
    expect(childrenOf(renderWithDefectNames(''))).toEqual([]);
  });
});
