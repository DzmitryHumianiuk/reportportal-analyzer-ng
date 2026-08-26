import { Fragment, type ReactNode } from 'react';

// Drain / masking token rules, most specific first. The original util.js applied
// these as successive regex replaces over an HTML string, which let the generic
// `<...>` rule re-wrap tokens the specific rules had already matched (so every
// angle-bracket token ended up painted with the generic colour). This scanner
// keeps the same rule list and the same order but stops at the first rule that
// matches at a position, which is what the class names were always meant to do.
const RULES: ReadonlyArray<{ re: RegExp; cls: string; wordStart?: boolean }> = [
  { re: /^(?:<NUM>|<\*>|<:NUM:>)/, cls: 'tok tok-num' },
  { re: /^(?:<UUID>|<GUID>)/, cls: 'tok tok-uuid' },
  { re: /^<(?:URL|PATH|IP|HEX|TOKEN|DATE|TIME|NUMBER)>/, cls: 'tok tok-url' },
  { re: /^<[A-Za-z0-9_:*]+>/, cls: 'tok tok-wild' },
  { re: /^\*/, cls: 'tok tok-wild' },
  { re: /^\d{2,}\b/, cls: 'tok tok-num', wordStart: true },
  {
    re: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i,
    cls: 'tok tok-uuid',
    wordStart: true,
  },
];

const WORD = /[A-Za-z0-9_]/;

export interface HighlightedPatternProps {
  pattern: string | null;
}

/**
 * Renders a Drain3 template pattern with its masking tokens highlighted.
 * Text nodes + spans only — the pattern comes from the database and is never
 * treated as markup.
 */
export function HighlightedPattern({ pattern }: HighlightedPatternProps): JSX.Element {
  if (pattern == null) return <span className="muted">— pattern unavailable —</span>;

  const parts: ReactNode[] = [];
  let plain = '';
  let i = 0;
  const flush = () => {
    if (plain) {
      parts.push(plain);
      plain = '';
    }
  };

  while (i < pattern.length) {
    const rest = pattern.slice(i);
    const atWordStart = i === 0 || !WORD.test(pattern[i - 1]);
    let hit: { text: string; cls: string } | null = null;
    for (const rule of RULES) {
      if (rule.wordStart && !atWordStart) continue;
      const m = rule.re.exec(rest);
      if (m && m[0]) {
        hit = { text: m[0], cls: rule.cls };
        break;
      }
    }
    if (!hit) {
      plain += pattern[i];
      i += 1;
      continue;
    }
    flush();
    parts.push(
      <span key={`t${i}`} className={hit.cls}>
        {hit.text}
      </span>,
    );
    i += hit.text.length;
  }
  flush();

  return <>{parts.map((p, idx) => (typeof p === 'string' ? <Fragment key={`s${idx}`}>{p}</Fragment> : p))}</>;
}
