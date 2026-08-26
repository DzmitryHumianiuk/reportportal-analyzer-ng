// Takeaway-sentence builder.
//
// The original view assembled the L1 takeaway with DOM nodes and then read the
// paragraph's textContent back to decide whether the stored decision summary
// repeats it (journey.js decisionCard). React has no DOM to read at that point,
// so the builder keeps the plain text alongside the nodes: `text` is exactly
// what the rendered paragraph's textContent will be.

import type { ReactNode } from 'react';

export class Sentence {
  private parts: ReactNode[] = [];

  private key = 0;

  /** Plain text of everything appended so far — same as the DOM textContent. */
  text = '';

  /** Plain text run. */
  add(...runs: Array<string | number>): this {
    for (const run of runs) {
      const s = String(run);
      if (!s) continue;
      this.parts.push(s);
      this.text += s;
    }
    return this;
  }

  /** Bold run. */
  b(s: string): this {
    return this.el(<b key={this.next()}>{s}</b>, s);
  }

  /** Monospace run (`code`-like). */
  mono(s: string): this {
    return this.el(
      <span key={this.next()} className="mono">
        {s}
      </span>,
      s,
    );
  }

  /** Muted parenthetical run. */
  muted(s: string): this {
    return this.el(
      <span key={this.next()} className="muted">
        {s}
      </span>,
      s,
    );
  }

  /** Any node, with the text it contributes to textContent. */
  el(node: ReactNode, text: string): this {
    this.parts.push(node);
    this.text += text;
    return this;
  }

  private next(): string {
    this.key += 1;
    return `s${this.key}`;
  }

  render(className = 'takeaway'): JSX.Element {
    return <p className={className}>{this.parts}</p>;
  }
}

export function sentence(): Sentence {
  return new Sentence();
}
