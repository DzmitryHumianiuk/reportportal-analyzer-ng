// Cold-start Rules — the rubric the analyzer falls back to, exactly as applied.
//
// When a project has no decided failures there is nothing to match against, so
// the analyzer reads the error and reports which fixed rule fired. The modal
// names that rule ("Could not reach the service"); this page is where the rule
// itself lives, so a reader can see what it looks for, what it always produces,
// and what sits above it in the order.
//
// Rows come from the analyzer's own table (backend rubric_loader), never a copy
// kept here. Order is part of the contract: the FIRST matching rule wins.

import { useEffect, useRef, useState } from 'react';

import { api } from '../../app/api';
import { useApp } from '../../app/state';
import type { RubricRule } from '../../app/types';
import { Card, EmptyState, LabelBadge, Loading } from '../../components';

/** Rubric label codes that carry a color; anything else renders unlabeled. */
const LABEL_GROUP: Record<string, string> = { pb: 'pb', ab: 'ab', si: 'si', nd: 'nd' };

/** The rubric answers with a word, not a number. Say what the word buys. */
const CONFIDENCE_GLOSS: Record<string, string> = {
  high: 'the pattern is unambiguous',
  med: 'the pattern usually holds',
  low: 'the closest rule, little more',
};

type Load =
  | { state: 'loading' }
  | { state: 'error'; message: string }
  | { state: 'ready'; rules: RubricRule[] };

export default function Rubric() {
  const { link, refreshTick } = useApp();
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  // The row a reader arrived at from a guess, so the effect can scroll to it.
  const focusRow = useRef<HTMLTableRowElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoad({ state: 'loading' });
    void (async () => {
      try {
        const payload = await api.rubric();
        if (!cancelled) setLoad({ state: 'ready', rules: payload.rules || [] });
      } catch (e) {
        if (!cancelled) {
          setLoad({ state: 'error', message: String((e as Error)?.message || e) });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  const rules = load.state === 'ready' ? load.rules : null;

  // Deep link from a guess: #view=rubric&rule=R6 scrolls to that rule.
  useEffect(() => {
    if (!link.rule || !rules?.length) return;
    focusRow.current?.scrollIntoView({ block: 'center' });
  }, [link.rule, rules]);

  if (load.state === 'loading') return <Loading text="Reading the rubric…" />;

  if (load.state === 'error') {
    return <EmptyState icon="warning" title="Could not read the rubric" body={load.message} />;
  }

  if (!load.rules.length) {
    return (
      <EmptyState
        icon="info"
        title="No rubric available"
        body="The analyzer rubric file was not found in this build."
      />
    );
  }

  return (
    <Card title="Cold-start rules" sub={`${load.rules.length} rules, applied in order`}>
      <p className="rubric-intro">
        A project with no decided failures has nothing to match against. The analyzer reads the
        error and applies the first rule below that fits, then reports which one it used. These
        guesses are never applied on their own: they are offered, and a person decides.
      </p>
      <div className="rubric-scroll">
        <table className="rubric-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Rule</th>
              <th>What it looks for</th>
              <th>Always produces</th>
            </tr>
          </thead>
          <tbody>
            {load.rules.map((r) => {
              const focused = link.rule != null && link.rule === r.rule;
              const confidence = r.confidence || '';
              const gloss = CONFIDENCE_GLOSS[confidence];
              return (
                <tr
                  key={r.rule}
                  id={`rule-${r.rule}`}
                  ref={focused ? focusRow : null}
                  className={`rubric-row${focused ? ' rubric-row-focus' : ''}`}
                >
                  <td className="rubric-order">{String(r.order)}</td>
                  <td className="rubric-name">
                    <div className="rubric-name-main">{r.name}</div>
                    <code className="rubric-id">{r.rule}</code>
                  </td>
                  <td className="rubric-when">{r.when}</td>
                  <td className="rubric-verdict">
                    <LabelBadge
                      group={LABEL_GROUP[r.label || ''] || 'none'}
                      text={r.label_name || ''}
                    />
                    <div className="rubric-conf">
                      {confidence}
                      <span className="rubric-conf-gloss">{gloss ? ` — ${gloss}` : ''}</span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
