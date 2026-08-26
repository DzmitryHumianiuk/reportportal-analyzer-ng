// Label vocabulary + ReportPortal defect-name resolution.
//
// A per-project locator→{name,short_name,color} map is fetched from api/rp and
// installed with setDefects(). When the map is empty (RP unconfigured,
// unreachable, or the name is missing) every helper below degrades to the raw
// locator or the static group name — never an invented string.

import { createElement, Fragment, type ReactNode } from 'react';

import type { DefectInfo, DefectMap } from '../app/types';
import { labelColor } from './colors';

export type LabelGroup = 'pb' | 'ab' | 'si' | 'nd' | 'ti' | 'none' | 'other';

export const LABEL_NAMES: Record<string, string> = {
  pb: 'Product bug',
  ab: 'Automation bug',
  si: 'System issue',
  nd: 'No defect',
  ti: 'To investigate',
  none: 'Unlabeled',
  other: 'Other',
};

export function labelName(group: string): string {
  return LABEL_NAMES[group] || group;
}

/** The five real RP defect groups (everything else is 'none' or 'other'). */
export const DEFECT_GROUPS: readonly string[] = ['pb', 'ab', 'si', 'nd', 'ti'];

let defects: DefectMap = {};

export function setDefects(map: DefectMap | null | undefined): void {
  defects = map || {};
}

export function getDefects(): DefectMap {
  return defects;
}

export function defectInfo(locator?: string | null): DefectInfo | null {
  return locator ? defects[locator] || null : null;
}

export function labelGroup(locator?: string | null): LabelGroup {
  if (!locator) return 'none';
  for (const g of DEFECT_GROUPS) {
    if (locator.startsWith(g)) return g as LabelGroup;
  }
  return 'other';
}

/** Representative RP defect for a label group (its locator starts with the group). */
export function defectForGroup(group: string): DefectInfo | null {
  for (const loc of Object.keys(defects)) {
    if (loc.startsWith(group)) return defects[loc];
  }
  return null;
}

export function defectColor(locator?: string | null, group?: string | null): string {
  const info = defectInfo(locator);
  if (info && info.color) return info.color;
  const g = group || labelGroup(locator);
  const byGroup = defectForGroup(g);
  return (byGroup && byGroup.color) || labelColor(g);
}

export function defectName(locator?: string | null, group?: string | null): string {
  const info = defectInfo(locator);
  if (info && info.name) return info.name;
  // No exact defect-type name: the label is GROUP-level (a bare group code from
  // matching/decision, a legend entry, or an unresolvable locator). Say so with
  // a "group" suffix — "Product bug group" — so it cannot be misread as the
  // concrete pb001 "Product Bug" type. Never borrow a representative type's name.
  const g = group || labelGroup(locator);
  return DEFECT_GROUPS.includes(g) ? `${labelName(g)} group` : labelName(g);
}

// Word-boundary guard for locator matching inside prose.
const LOCATOR_CHAR = /[A-Za-z0-9_]/;

/**
 * Render untrusted/derived prose with raw defect-type locators (pb001,
 * ab_1iupzjso5bh9t, …) replaced by compact inline defect pills showing the
 * project's configured NAME (abbreviation when the name is long).
 *
 * SAFE by construction: the text is tokenized around EXACT occurrences of known
 * locators (keys of the per-project defects map, longest-first, word-boundary
 * guarded) and assembled as React text nodes + elements — never innerHTML.
 * Unknown locators stay plain text (honest).
 */
export function renderWithDefectNames(text: string): ReactNode {
  const s = String(text ?? '');
  const keys = Object.keys(defects).sort((a, b) => b.length - a.length);
  const parts: ReactNode[] = [];
  let i = 0;
  while (i < s.length) {
    let best: { at: number; k: string } | null = null;
    for (const k of keys) {
      const at = s.indexOf(k, i);
      if (at === -1) continue;
      const before = at > 0 ? s[at - 1] : '';
      const after = at + k.length < s.length ? s[at + k.length] : '';
      if ((before && LOCATOR_CHAR.test(before)) || (after && LOCATOR_CHAR.test(after))) continue;
      if (!best || at < best.at || (at === best.at && k.length > best.k.length)) best = { at, k };
    }
    if (!best) {
      parts.push(s.slice(i));
      break;
    }
    if (best.at > i) parts.push(s.slice(i, best.at));
    const info = defects[best.k];
    const shown =
      info.name && info.name.length > 18 && info.short_name ? info.short_name : info.name || best.k;
    parts.push(
      createElement(
        'span',
        {
          key: `d${best.at}`,
          className: 'defect-inline',
          title: `${info.name || shown} · ${best.k}`,
        },
        createElement('span', {
          className: 'dot',
          style: { background: info.color || 'var(--rp-e-300)' },
        }),
        shown,
      ),
    );
    i = best.at + best.k.length;
  }
  return createElement(Fragment, null, ...parts);
}

// --------------------------------------------------------------------------- //
// Label-provenance vocabulary
// --------------------------------------------------------------------------- //

export interface SourceLabel {
  k: string;
  text: string;
  plain: string;
  actor: string;
  weight: number | null;
}

export interface SourceInfo extends SourceLabel {
  raw: string | null;
}

/**
 * Covers the label_event.source CHECK values (rp_defect_update /
 * analyzer_suggestion_accepted / human_ui) AND the feature-vector label_source
 * tokens (rp / human / ai_suggested / seed), so both the Feedback timeline and
 * the Matching candidate strip read from one map. `weight` is the src_weight
 * (features.py _SRC_WEIGHT / spec §6.4) — the vote a label of this provenance
 * casts as future retrieval evidence.
 */
export const SOURCE_LABELS: Record<string, SourceLabel> = {
  rp_defect_update: {
    k: 'rp',
    text: 'defect update',
    plain: 'human · RP',
    actor: 'human (RP defect edit)',
    weight: 1.0,
  },
  analyzer_suggestion_accepted: {
    k: 'human',
    text: 'UI accept',
    plain: 'human · accepted',
    actor: 'human accepted analyzer suggestion',
    weight: 0.9,
  },
  human_ui: {
    k: 'human',
    text: 'UI edit',
    plain: 'human · UI',
    actor: 'human (Inspector UI)',
    weight: 0.9,
  },
  rp: {
    k: 'rp',
    text: 'defect update',
    plain: 'human · RP',
    actor: 'human (RP defect edit)',
    weight: 1.0,
  },
  human: {
    k: 'human',
    text: 'UI accept',
    plain: 'human',
    actor: 'human (Inspector UI)',
    weight: 0.9,
  },
  ai_suggested: {
    k: 'ai_suggested',
    text: 'auto',
    plain: 'analyzer',
    actor: "analyzer's auto-label",
    weight: 0.3,
  },
  seed: {
    k: 'seed',
    text: 'catalog',
    plain: 'seed',
    actor: 'seed catalog rule',
    weight: 0.6,
  },
};

export function srcInfo(token?: string | null): SourceInfo {
  if (token == null) {
    return { k: '—', text: '', plain: '—', actor: 'unrecorded actor', weight: null, raw: null };
  }
  const known = SOURCE_LABELS[token];
  return {
    ...(known || { k: token, text: '', plain: token, actor: token, weight: null }),
    raw: token,
  };
}
