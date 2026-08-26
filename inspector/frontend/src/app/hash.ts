// Permalink hash state (shareable deep links).
//
// The address bar carries `#k=v&k2=v2` (custom encoding, not URLSearchParams —
// the scheme predates this rewrite and existing bookmarks must keep working).
// Writes go through history.replaceState: no history entries, no reload, and
// no `hashchange` event, so the app never re-enters its own router.

export type ViewName =
  | 'journey'
  | 'signatures'
  | 'drain'
  | 'modes'
  | 'groups'
  | 'loop'
  | 'llm'
  | 'rubric';

/** Tab order — also the order the shell renders the tab strip in. */
export const VIEW_NAMES: readonly ViewName[] = [
  'journey',
  'signatures',
  'drain',
  'modes',
  'groups',
  'loop',
  'llm',
  'rubric',
];

export function isViewName(value: string | null | undefined): value is ViewName {
  return value != null && (VIEW_NAMES as readonly string[]).includes(value);
}

/** Per-view selection mirrored into the hash. */
export interface LinkState {
  launch?: number | null; // journey
  item?: number | null; // journey
  glaunch?: number | null; // groups — SERIALIZED AS 'launch' when view === 'groups'
  q?: string | null; // signatures
  conflicts?: boolean; // signatures — serialized as '1' or omitted
  hash?: string | null; // signatures (expanded error_hash)
  lrole?: string | null; // llm
  loutcome?: string | null; // llm
  rule?: string | null; // rubric
}

// A hash written by hand can hold a stray '%'; decodeURIComponent would throw
// and take the whole boot down with it. Fall back to the raw text instead.
function decode(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

/** Parse `#k=v&k2=v2` from the address bar into a plain object (values decoded). */
export function readHashParams(): Record<string, string> {
  const raw = (window.location.hash || '').replace(/^#/, '');
  const out: Record<string, string> = {};
  for (const part of raw.split('&')) {
    if (!part) continue;
    const i = part.indexOf('=');
    const k = decode(i < 0 ? part : part.slice(0, i));
    const v = i < 0 ? '' : decode(part.slice(i + 1));
    if (k) out[k] = v;
  }
  return out;
}

type HashValue = string | number | boolean | null | undefined;

function writeHashParams(params: Array<[string, HashValue]>): void {
  const parts: string[] = [];
  for (const [k, v] of params) {
    if (v == null || v === '') continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  const hash = parts.length ? `#${parts.join('&')}` : '';
  window.history.replaceState(
    null,
    '',
    window.location.pathname + window.location.search + hash,
  );
}

/**
 * Write the hash for the active view. Only the params that belong to `view` are
 * serialized — stale params from other views are pruned — and null / '' /
 * undefined values are dropped so the URL stays clean.
 */
export function writeHash(view: ViewName, project: number | null, link: LinkState): void {
  const params: Array<[string, HashValue]> = [
    ['view', view],
    ['project', project],
  ];
  if (view === 'journey') {
    params.push(['launch', link.launch], ['item', link.item]);
  } else if (view === 'signatures') {
    params.push(
      ['q', link.q || null],
      ['conflicts', link.conflicts ? '1' : null],
      ['hash', link.hash],
    );
  } else if (view === 'groups') {
    params.push(['launch', link.glaunch]);
  } else if (view === 'llm') {
    params.push(['lrole', link.lrole], ['loutcome', link.loutcome]);
  } else if (view === 'rubric') {
    params.push(['rule', link.rule]);
  }
  writeHashParams(params);
}
