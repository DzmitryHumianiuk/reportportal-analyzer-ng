// Rubric tab — the cold-start rules, exactly as the analyzer applies them.
//
// When a project has no decided failures there is nothing to match against, so
// the analyzer falls back to a fixed rubric and reports which rule fired. The
// modal names that rule ("Could not reach the service"); this page is where the
// rule itself lives, so a reader can see what it looks for, what it always
// produces, and what sits above it in the order.
//
// Rows come from the analyzer's own table (backend rubric_loader), never a copy
// kept here. Order is part of the contract: the FIRST matching rule wins.
import { api } from '../api.js';
import { h, clear, card, emptyState, loading, labelBadge } from '../util.js';

const LABEL_GROUP = { pb: 'pb', ab: 'ab', si: 'si', nd: 'nd' };

// The rule a reader arrived at, from #view=rubric&rule=R6. Handed in by the
// router: by render time the hash has already been rewritten from link state, so
// reading location.hash here would find it gone.
const rstate = { rule: null };
export function setRubricState({ rule }) {
  rstate.rule = rule || null;
}

// The rubric answers with a word, not a number. Say what the word buys.
const CONFIDENCE_GLOSS = {
  high: 'the pattern is unambiguous',
  med: 'the pattern usually holds',
  low: 'the closest rule, little more',
};

function ruleRow(r) {
  const anchorId = `rule-${r.rule}`;
  return h('tr', { id: anchorId, class: 'rubric-row' },
    h('td', { class: 'rubric-order' }, String(r.order)),
    h('td', { class: 'rubric-name' },
      h('div', { class: 'rubric-name-main' }, r.name),
      h('code', { class: 'rubric-id' }, r.rule)),
    h('td', { class: 'rubric-when' }, r.when),
    h('td', { class: 'rubric-verdict' },
      labelBadge(LABEL_GROUP[r.label] || 'none', r.label_name),
      h('div', { class: 'rubric-conf' },
        r.confidence,
        h('span', { class: 'rubric-conf-gloss' },
          CONFIDENCE_GLOSS[r.confidence] ? ` — ${CONFIDENCE_GLOSS[r.confidence]}` : ''))),
  );
}

export async function renderRubric(el) {
  clear(el);
  el.appendChild(loading('Reading the rubric…'));

  let rows = [];
  try {
    rows = (await api.rubric()).rules || [];
  } catch (e) {
    clear(el);
    el.appendChild(emptyState('warning', 'Could not read the rubric',
      String(e.message || e)));
    return;
  }

  clear(el);
  if (!rows.length) {
    el.appendChild(emptyState('info', 'No rubric available',
      'The analyzer rubric file was not found in this build.'));
    return;
  }

  const intro = h('p', { class: 'rubric-intro' },
    'A project with no decided failures has nothing to match against. The analyzer ',
    'reads the error and applies the first rule below that fits, then reports which ',
    'one it used. These guesses are never applied on their own: they are offered, ',
    'and a person decides.');

  const table = h('table', { class: 'rubric-table' },
    h('thead', {},
      h('tr', {},
        h('th', {}, '#'),
        h('th', {}, 'Rule'),
        h('th', {}, 'What it looks for'),
        h('th', {}, 'Always produces'))),
    h('tbody', {}, ...rows.map(ruleRow)));

  el.appendChild(card('Cold-start rules', {
    sub: `${rows.length} rules, applied in order`,
  }, intro, h('div', { class: 'rubric-scroll' }, table)));

  // Deep link from a guess: #view=rubric&rule=R6 scrolls to that rule.
  if (rstate.rule) {
    const target = el.querySelector(`#rule-${CSS.escape(rstate.rule)}`);
    if (target) {
      target.classList.add('rubric-row-focus');
      target.scrollIntoView({ block: 'center' });
    }
  }
}
