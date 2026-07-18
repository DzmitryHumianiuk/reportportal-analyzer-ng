// Item Journey — the main view. Picker (launch → item) + pipeline stepper with
// expandable stage cards, connected visually, plus the live Stage-B reconstruction.
import { api } from '../api.js';
import {
  h, clear, card, defectBadge, defectBadgeAbbr, defectName, idChip, setDefects, fmt,
  shortTime, relTime, highlightPattern, emptyState, loading, engDetails, engDrawer, srcInfo,
  FEATURE_GROUP_COLORS, INK, MUTED,
} from '../util.js';

const jstate = { launch: null, item: null, selectItem: null, itemEls: null };

// Navigate the journey to another item in the current launch (member-dot click).
function navToItem(itemId) {
  const rec = jstate.itemEls && jstate.itemEls.get(itemId);
  if (rec && jstate.selectItem) jstate.selectItem(rec.it, rec.el);
}

export async function renderJourney(root, app) {
  clear(root);
  const layout = h('div', { class: 'grid', style: { gridTemplateColumns: '300px 1fr', alignItems: 'start' } });
  const sidebar = h('div', { class: 'grid', style: { gap: '16px' } });
  const main = h('div', { id: 'journey-main' });
  layout.append(sidebar, main);
  root.appendChild(layout);

  // --- launch + item pickers ---
  const launchCard = card('Launches', { sub: 'pick one' });
  const itemCard = card('Failing items', { sub: 'pick one' });
  sidebar.append(launchCard, itemCard);
  const launchBody = launchCard.querySelector('.card-body');
  const itemBody = itemCard.querySelector('.card-body');
  clear(launchBody).appendChild(loading());
  main.appendChild(emptyState('🧭', 'Pick a launch, then an item',
    'The Item Journey traces one failing test item through every pipeline stage: signature → grouping → matching → decision → feedback.'));

  const { launches } = await api.launches(app.project);
  clear(launchBody);
  if (!launches.length) {
    launchBody.appendChild(emptyState('📭', 'No launches', 'This project has no test items yet.', 'analyzer.test_item'));
    clear(itemBody).appendChild(h('div', { class: 'muted', style: { fontSize: '12px' } }, '—'));
    return;
  }
  const launchList = h('div', { class: 'list' });
  launchBody.appendChild(launchList);
  for (const l of launches) {
    const el = h('div', { class: 'list-item', onclick: () => selectLaunch(l, el) },
      h('div', { class: 'li-main' },
        h('div', { class: 'li-title' }, l.launch_name || `launch ${l.launch_id}`),
        h('div', { class: 'li-sub' }, `#${l.launch_number ?? '—'} · id ${l.launch_id} · ${l.item_count} items`)),
      h('span', { class: 'chip' }, `${l.labeled_count} labeled`));
    launchList.appendChild(el);
    if (jstate.launch === l.launch_id) selectLaunch(l, el);
  }
  if (jstate.launch == null) selectLaunch(launches[0], launchList.firstChild);

  async function selectLaunch(l, el) {
    jstate.launch = l.launch_id;
    [...launchList.children].forEach((c) => c.classList.remove('active'));
    el.classList.add('active');
    clear(itemBody).appendChild(loading());
    const { items } = await api.items(app.project, l.launch_id);
    clear(itemBody);
    if (!items.length) { itemBody.appendChild(h('div', { class: 'muted' }, 'No items in this launch.')); return; }
    const itemList = h('div', { class: 'list' });
    itemBody.appendChild(itemList);
    // Expose the item-select path + an id→{it,el} index so grouping member dots
    // can navigate to a co-failing item (same launch, already in this list).
    jstate.selectItem = selectItem;
    jstate.itemEls = new Map();
    for (const it of items) {
      const iel = h('div', { class: 'list-item' + (it.is_auto_analyzed ? ' halo-auto' : ''), onclick: () => selectItem(it, iel) },
        h('div', { class: 'li-main' },
          h('div', { class: 'li-title' }, it.item_name || `item ${it.item_id}`),
          h('div', { class: 'li-sub' }, idChip(`id ${it.item_id}`, it.ui_url, ''), ` · ${it.exc_text || 'no exc'}`)),
        defectBadgeAbbr(it.issue_type, it.label_group));
      itemList.appendChild(iel);
      jstate.itemEls.set(it.item_id, { it, el: iel });
    }
    // auto-select first (or previously chosen) item
    const pick = items.find((x) => x.item_id === jstate.item) || items[0];
    const idx = items.indexOf(pick);
    selectItem(pick, itemList.children[idx]);
  }

  async function selectItem(it, el) {
    jstate.item = it.item_id;
    [...el.parentElement.children].forEach((c) => c.classList.remove('active'));
    el.classList.add('active');
    clear(main).appendChild(loading('Building journey…'));
    try {
      const data = await api.journey(app.project, it.item_id);
      clear(main);
      renderJourneyDetail(main, data);
    } catch (e) {
      clear(main).appendChild(emptyState('🚫', 'Journey failed', String(e.message || e)));
    }
  }
}

function renderJourneyDetail(root, d) {
  if (d.rp) setDefects(d.rp.defects);
  const it = d.item;
  // Header line
  root.appendChild(h('div', { class: 'flex between center wrap', style: { marginBottom: '14px' } },
    h('div', { class: 'flex center gap-12 wrap' },
      h('h2', { style: { margin: 0, fontSize: '18px' } }, it.item_name || `item ${it.item_id}`),
      defectBadge(it.issue_type, it.label_group),
      it.is_auto_analyzed ? h('span', { class: 'badge', style: { background: 'var(--accent-soft)', color: 'var(--accent)' } }, '⭑ auto‑analyzed') : null),
    h('div', { class: 'flex gap-8 wrap' },
      idChip(`item ${it.item_id}`, it.ui_url),
      idChip(`launch ${it.launch_id}`, it.launch_url),
      h('span', { class: 'chip' }, `${it.log_count} logs`),
      h('span', { class: 'chip mono' }, `tch ${it.test_case_hash ?? '—'}`))));

  // Stepper
  const stages = [
    ['Signature', d.signature ? d.signature.exc_text || 'built' : 'none'],
    ['Grouping', d.grouping ? `group ${d.grouping.group_id}` : 'none'],
    ['Matching', d.matching.stage_label || '—'],
    ['Decision', d.decision ? bandName(d.decision.band) : 'no suggestion'],
    ['Feedback', `${d.feedback.length} event(s)`],
  ];
  const stepper = h('div', { class: 'stepper' });
  const cards = [];
  stages.forEach(([k, v], i) => {
    const pill = h('div', { class: 'stage-pill' + (i === 0 ? ' active' : ''), onclick: () => focus(i) },
      h('div', { class: 's-k' }, `${i + 1} · ${k}`),
      h('div', { class: 's-v' }, v),
      h('div', { class: 's-flow' }, '→'));
    stepper.appendChild(pill);
    cards.push(pill);
  });
  root.appendChild(stepper);

  const container = h('div', { class: 'grid', style: { gap: '16px' } });
  root.appendChild(container);
  const sig = signatureCard(d);
  const grp = groupingCard(d);
  const mat = matchingCard(d);
  const dec = decisionCard(d);
  const fb = feedbackCard(d);
  container.append(sig, grp, mat, dec, fb);
  const secs = [sig, grp, mat, dec, fb];

  function focus(i) {
    cards.forEach((c, j) => c.classList.toggle('active', j === i));
    secs[i].scrollIntoView({ behavior: 'smooth', block: 'start' });
    secs[i].animate([{ boxShadow: '0 0 0 2px var(--accent)' }, { boxShadow: 'var(--shadow)' }], { duration: 900 });
  }
}

function bandName(b) { return { auto: 'Auto‑apply', suggest: 'Suggest', abstain: 'Abstain' }[b] || b; }

// ---- Stage 1: signature ----
function signatureCard(d) {
  const s = d.signature;
  const c = card('Signature', { step: 1, sub: 'field-separated FTS doc + fingerprints' });
  const body = c.querySelector('.card-body');
  if (!s) {
    body.appendChild(emptyState('🔤', 'No signature',
      'This item has no failure_signature row — it produced no error logs, so the signature builder had nothing to index.',
      'analyzer.failure_signature'));
    return c;
  }
  // fingerprints
  body.appendChild(h('div', { class: 'flex gap-8 wrap mb-8' },
    fpChip('exception_fp', s.exception_fp),
    fpChip('error_hash', s.error_hash),
    h('span', { class: 'chip' }, `emb_model_ver ${s.emb_model_ver}`),
    h('span', { class: 'chip', style: { color: s.has_emb ? 'var(--good)' : 'var(--muted)' } },
      s.has_emb ? '● embedded' : '○ not embedded')));

  // signature text with field badges
  const fields = [
    ['EXC', 'fb-exc', s.exc_text],
    ['MSG', 'fb-msg', s.msg_text],
    ['FRAMES', 'fb-frames', (s.top_frames || []).join('  ›  ')],
    ['TEMPLATES', 'fb-templates', (s.template_ids || []).join(', ')],
    ['CODES', 'fb-codes', (s.status_codes || []).join(', ')],
  ];
  const grid = h('div', { class: 'grid', style: { gap: '8px' } });
  for (const [name, cls, val] of fields) {
    if (!val) continue;
    grid.appendChild(h('div', { class: 'flex gap-8', style: { alignItems: 'baseline' } },
      h('span', { class: `field-badge ${cls}` }, name),
      h('span', { class: 'mono', style: { fontSize: '12.5px', color: 'var(--ink-2)', wordBreak: 'break-word' } }, val)));
  }
  body.appendChild(grid);

  // referenced drain templates
  if (d.templates && d.templates.length) {
    body.appendChild(h('div', { class: 'section-title', style: { marginTop: '16px' } },
      `Referenced Drain3 templates (${d.templates.length})`));
    for (const t of d.templates) {
      body.appendChild(h('div', { style: { padding: '8px 10px', background: 'var(--surface-2)', border: '1px solid var(--hairline)', borderRadius: '8px', marginBottom: '6px' } },
        h('div', { class: 'flex between center mb-8' },
          h('span', { class: 'chip mono' }, `#${t.template_id}`),
          t.missing ? h('span', { class: 'muted' }, 'template row missing') :
            h('span', { class: 'muted', style: { fontSize: '11px' } }, `${t.token_count} tok · ${t.match_count} matches`)),
        h('div', { class: 'pattern', html: highlightPattern(t.pattern) })));
    }
  }
  return c;
}

function fpChip(name, val) {
  return h('span', { class: 'chip mono-strong', title: name },
    h('span', { style: { color: 'var(--muted)', fontFamily: 'var(--sans)', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '.5px' } }, name),
    val ?? '—');
}

// ---- Stage 2: grouping ----
function groupingCard(d) {
  const g = d.grouping;
  // Degenerate guard (lens §3.1): dominant + solo cannot be a burst → render solo.
  const isBurst = !!(g && g.dominant && g.member_count > 1);
  const c = card('Grouping', {
    step: 2,
    sub: 'who else failed like this in the same run (co-failure launch group)',
    class: isBurst ? 'burst-accent' : null,
  });
  const body = c.querySelector('.card-body');
  if (!g) {
    body.appendChild(emptyState('🧩', 'No group',
      'No group — grouping needs at least one failure with a comparable error ID (error_hash) in this launch. This item’s failure was not comparable to any other.',
      'analyzer.launch_group'));
    return c;
  }
  if (isBurst) {
    c.querySelector('.card-title').appendChild(
      h('span', { class: 'badge', style: { marginLeft: '4px', background: 'color-mix(in srgb,var(--warning) 16%,transparent)', color: 'var(--warning)', borderColor: 'var(--warning)' } },
        h('span', { class: 'dot', style: { background: 'var(--warning)' } }), '🔥 burst'));
  }

  // L1 takeaway
  body.appendChild(groupTakeaway(g, isBurst));

  // L2 — member strip (+ count key)  |  si-meter (si_prior>0) or "no burst signal" note
  const l2 = h('div', { class: 'grp-l2' });
  l2.appendChild(memberStrip(g));
  if (g.si_prior > 0) {
    l2.appendChild(siMeter(g.si_prior));
  } else {
    l2.appendChild(h('div', { class: 'si-note' },
      h('div', { class: 'si-label' }, 'burst signal (si_prior)'),
      'No burst signal: fingerprint not new to history or share of launch failures below the gate (new fp · ≥5 members · >40% share).'));
  }
  body.appendChild(l2);

  // L4 — engineer details (every raw field, unchanged, one click away)
  body.appendChild(engDetails('grouping',
    h('dl', { class: 'kv' },
      h('dt', {}, 'group_id'), h('dd', { class: 'mono' }, String(g.group_id)),
      h('dt', {}, 'fingerprint'), h('dd', { class: 'mono' }, String(g.fingerprint)),
      h('dt', {}, 'member_count'), h('dd', { class: 'mono' }, String(g.member_count)),
      h('dt', {}, 'dominant'), h('dd', { class: 'mono' }, String(g.dominant)),
      h('dt', {}, 'si_prior'), h('dd', { class: 'mono' }, String(g.si_prior)),
      ...(g.launch_failed_count != null
        ? [h('dt', {}, 'launch_failed_count'), h('dd', { class: 'mono' }, String(g.launch_failed_count))]
        : [])),
    h('p', { class: 'note', style: { marginTop: '8px' } }, 'co-failure launch group')));

  if (isBurst) pulseOnce(c);
  return c;
}

// Single attention pulse on first render (respects prefers-reduced-motion).
function pulseOnce(el) {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  requestAnimationFrame(() => {
    if (el.animate) el.animate([{ boxShadow: '0 0 0 2px var(--warning)' }, { boxShadow: 'var(--shadow)' }], { duration: 900 });
  });
}

// L1 grouping takeaway — deterministic template (solo / shared / burst).
function groupTakeaway(g, isBurst) {
  const p = h('p', { class: 'takeaway' });
  const n = g.member_count;
  const si = fmt(g.si_prior, 2);
  if (n === 1) {
    p.append(h('b', {}, 'Alone in this launch'),
      ' — no other failure shares this signature; no group diagnosis applied ',
      h('span', { class: 'muted' }, '(solo group)'), '.');
  } else if (isBurst) {
    const lfc = g.launch_failed_count;
    const hasShare = lfc != null && lfc > 0 && n <= lfc;
    p.append(h('b', {}, 'Burst:'), ' a new fingerprint covers ');
    if (hasShare) {
      p.append(h('b', {}, `${n} of ${lfc} failures`), ` in this launch (${Math.round(100 * n / lfc)}%) — strong `);
    } else {
      p.append(h('b', {}, `${n} failures`), ' of this launch — strong ');
    }
    p.append(h('b', {}, 'System Issue'), ' prior applied ',
      h('span', { class: 'muted' }, `(dominant; si_prior ${si})`), '.');
  } else {
    p.append(h('b', {}, `${n} failures share this signature`),
      ` in this launch — one diagnosis should cover all ${n} `,
      h('span', { class: 'muted' }, '(shared-cause group)'), '.');
  }
  return p;
}

// L2 member dot-strip + count key; honest degradation when members not linkable.
function memberStrip(g) {
  const wrap = h('div', { style: { flex: '1 1 240px' } });
  wrap.appendChild(h('div', { class: 'si-label' }, 'failures with this error'));
  const members = g.members || [];
  if (!members.length) {
    wrap.appendChild(h('div', { class: 'dot-strip' },
      h('span', { class: 'chip' }, `${g.member_count} failures with this signature`)));
    wrap.appendChild(h('div', { class: 'dot-strip-key' }, 'individual members not linkable for this group.'));
    return wrap;
  }
  const strip = h('div', { class: 'dot-strip', role: 'list', 'aria-label': 'group members' });
  for (const m of members) {
    const cls = ['pb', 'ab', 'si', 'nd', 'ti'].includes(m.label_group) ? m.label_group : 'none';
    const title = `item ${m.item_id}${m.is_self ? ' · this item' : ''} · ${defectName(m.issue_type, m.label_group)}`;
    strip.appendChild(h('button', {
      class: 'm-dot ' + cls + (m.is_self ? ' self' : ''),
      role: 'listitem', title, 'aria-label': title,
      onclick: () => navToItem(m.item_id),
    }));
  }
  if (g.member_count > members.length) {
    strip.appendChild(h('span', { class: 'chip' }, `+${g.member_count - members.length} more`));
  }
  wrap.append(strip, memberKey(g, members));
  return wrap;
}

function memberKey(g, members) {
  const byGroup = new Map();
  for (const m of members) {
    const e = byGroup.get(m.label_group) || { count: 0, locator: m.issue_type, group: m.label_group };
    e.count += 1; byGroup.set(m.label_group, e);
  }
  const prefix = members.length < g.member_count
    ? `${members.length} of ${g.member_count} members: `
    : `${g.member_count} member${g.member_count === 1 ? '' : 's'}: `;
  const key = h('div', { class: 'dot-strip-key' }, prefix);
  const entries = [...byGroup.values()].sort((a, b) => b.count - a.count);
  entries.forEach((e, i) => {
    key.append(defectBadgeAbbr(e.locator, e.group), h('span', {}, `×${e.count}`));
    if (i < entries.length - 1) key.append(h('span', { class: 'muted' }, '·'));
  });
  return key;
}

// L2 banded si_prior meter (scale 0 → 0.9, the documented cap).
function siMeter(v) {
  const width = Math.max(0, Math.min(1, v / 0.9)) * 100;
  return h('div', { class: 'si-meter', title: 'burst signal (si_prior) — burst prior · range 0 to 0.9' },
    h('div', { class: 'si-label' }, 'burst signal (si_prior)'),
    h('div', { class: 'si-track' },
      h('i', { class: 'si-fill', style: { width: width + '%' } }),
      h('b', { class: 'si-tick', style: { left: '50%' } })),
    h('div', { class: 'si-scale' },
      h('span', {}, '0'), h('span', { class: 'si-val mono' }, fmt(v, 2)), h('span', {}, '0.9 max')),
    h('p', { class: 'note', style: { marginTop: '6px', maxWidth: '320px' } },
      'Prior evidence input for si, capped at 0.9 — one decision signal, not a verdict.'));
}

// ---- Stage 3: matching + reconstruction ----
const STAGE_COLOR = { A: 'var(--lbl-nd)', AB: 'var(--lbl-si)', C: 'var(--accent)' };

function matchingCard(d) {
  const m = d.matching;
  const c = card('Matching', { step: 3, sub: 'stage A → B → C cascade' });
  const body = c.querySelector('.card-body');

  if (!m.has_suggestion) {
    body.appendChild(emptyState('🎯', 'Not matched yet',
      'Not matched yet — no suggestion row; the matcher has not run for this item (or it was never a failure).',
      'analyzer.suggestion'));
    return c;
  }

  // L1 takeaway
  body.appendChild(matchTakeaway(m));

  // L2 — three-stage funnel
  body.appendChild(funnel(d));

  // L2 — matched-mode panel (when a mode is on the row) with thresholds printed
  if (m.matched_mode) {
    const mm = m.matched_mode;
    body.appendChild(h('div', { class: 'flex gap-8 center wrap', style: { marginTop: '12px' } },
      defectBadge(mm.label, mm.label_group),
      h('span', { class: 'chip' }, mm.title || `mode ${mm.mode_id}`),
      h('span', { class: 'chip', title: "Share of this mode's members carrying its label — 1.00 = unanimous. ≥ 0.95 required to short-circuit." },
        `purity ${fmt(mm.purity, 2)} `, h('span', { class: 'muted' }, '(≥ 0.95)')),
      h('span', { class: 'chip', title: 'Confirmed members of this mode. ≥ 10 required to short-circuit.' },
        `support ${mm.support} `, h('span', { class: 'muted' }, '(≥ 10)')),
      mm.status ? h('span', { class: 'chip mono' }, mm.status) : null,
      mm.seed_key ? h('span', { class: 'chip mono' }, `seed:${mm.seed_key}`) : null));
  }

  // L2 — top-3 evidence strip + full reconstruction
  const r = d.reconstruction;
  if (!r || !r.candidates || !r.candidates.length) {
    body.appendChild(h('div', { class: 'section-title', style: { marginTop: '16px' } },
      `Live retrieval re-run (Stage C, HYBRID_RETRIEVAL v${r ? r.hybrid_retrieval_version : '?'})`));
    body.appendChild(emptyState('🔍', 'No candidates retrieved',
      `0 candidates from HYBRID_RETRIEVAL v${r ? r.hybrid_retrieval_version : '?'} — no lexical or dense hit in scope against current data (too few comparable signatures).`));
    body.appendChild(matchingEng(d));
    return c;
  }
  body.appendChild(candStrip(d));
  body.appendChild(rrfDrawer(r));
  body.appendChild(matchingEng(d));
  return c;
}

function matchTakeaway(m) {
  const p = h('p', { class: 'takeaway' });
  const mono = (t) => h('span', { class: 'mono' }, t);
  const mut = (t) => h('span', { class: 'muted' }, t);
  const mm = m.matched_mode;
  if (m.stage === 'A') {
    p.append(h('b', {}, 'Inherited via exact hash'), ` from item ${m.matched_item_id} — same `,
      mono('error_hash'), ', guards passed (≤ 180 d, human-trusted); conf fixed 0.95 ', mut('(Stage A)'), '.');
  } else if (m.stage === 'AB' && mm) {
    p.append(h('b', {}, 'Matched KB mode'), ` “${mm.title || 'mode ' + m.matched_mode_id}” (#${m.matched_mode_id}) — purity ${fmt(mm.purity, 2)}, support ${mm.support}`,
      mm.seed_key ? `, seed:${mm.seed_key}` : '', ' ', mut('(Stage B; conf cap 0.93)'), '.');
  } else if (m.stage === 'AB') {
    p.append(h('b', {}, 'Matched KB mode'), ` #${m.matched_mode_id} — mode row not found in failure_mode (retired?); score details unavailable `, mut('(Stage B)'), '.');
  } else if (m.stage === 'abstain') {
    p.append(h('b', {}, 'Nothing matched'), ' — no inheritable hash (A), no confident mode (B), no trusted candidate (C); item stays ',
      mono('ti'), '. Reason on the Decision card.');
  } else { // C
    p.append(h('b', {}, 'No exact hash, no KB short-circuit'), ' — went to hybrid retrieval: FTS + cosine fused by ',
      mono('RRF'), ', top-20 → GBM scored the evidence ', mut('(Stage C)'), '.');
  }
  return p;
}

// Three-stage funnel derived from matching.stage (A runs first, then B, then C).
function funnel(d) {
  const m = d.matching;
  const sig = d.signature;
  const stage = m.stage;
  // state per node: won / fell / skip
  const states = {
    A: stage === 'A' ? 'won' : 'fell',
    B: stage === 'AB' ? 'won' : (stage === 'A' ? 'skip' : 'fell'),
    C: stage === 'C' ? 'won' : ((stage === 'A' || stage === 'AB') ? 'skip' : 'fell'),
  };
  const rail = h('div', { class: 'funnel', role: 'list' });
  const arrow = () => h('span', { class: 'fn-arrow', 'aria-hidden': 'true' }, '→');

  // Node A
  const aArt = h('div', { class: 'fn-art' });
  if (states.A === 'won') {
    aArt.append(idChip(`item ${m.matched_item_id}`, m.matched_item_url, 'chip mono'));
  } else if (states.A === 'fell') {
    if (sig && sig.exception_fp === '0') {
      // handled in caption below
    } else if (sameHashHistory(d)) {
      aArt.append(h('span', { class: 'chip', title: 'An identical error_hash is in labeled history but the Stage-A guards did not inherit it.' }, 'same-hash history exists'));
    } else {
      aArt.append(h('span', { class: 'chip mono', title: "No non-self candidate shares this item's error_hash — no inheritable exact match exists." }, 'no same-hash history'));
    }
  }
  const aCap = (states.A === 'fell' && sig && sig.exception_fp === '0')
    ? 'disabled — exception_fp = 0'
    : fnCaption(states.A, 'A');
  rail.append(fnNode('A · exact hash', states.A, aCap, aArt, 'A'));
  rail.append(arrow());

  // Node B
  const bArt = h('div', { class: 'fn-art' });
  if (states.B === 'won' && m.matched_mode) {
    bArt.append(defectBadge(m.matched_mode.label, m.matched_mode.label_group),
      h('span', { class: 'chip' }, m.matched_mode.title || `mode ${m.matched_mode_id}`));
  } else if (states.B === 'won') {
    bArt.append(h('span', { class: 'chip mono' }, `mode #${m.matched_mode_id}`));
  }
  rail.append(fnNode('B · KB modes', states.B, fnCaption(states.B, 'B'), bArt, 'B'));
  rail.append(arrow());

  // Node C
  const cArt = h('div', { class: 'fn-art' });
  if (states.C === 'won') {
    const nonSelf = (d.reconstruction && d.reconstruction.candidates || []).filter((x) => !x.is_self).length;
    cArt.append(h('span', { class: 'chip mono', title: 'Top-1 candidate fed the 46-feature vector → GBM. The GBM confidence lives on the Decision card.' },
      `top-1 of ${nonSelf} → GBM`));
  }
  rail.append(fnNode('C · hybrid + GBM', states.C, fnCaption(states.C, 'C'), cArt, 'C'));
  rail.append(arrow());

  // terminal
  const term = stage === 'abstain'
    ? h('span', { class: 'fn-term', style: { color: 'var(--lbl-ti)' } }, 'abstain → To Investigate')
    : h('span', { class: 'fn-term' }, `decided at ${stage === 'AB' ? 'B' : stage}`);
  rail.append(term);
  return rail;
}

function fnNode(kText, state, cap, art, letter) {
  const node = h('div', { class: 'fn-node', role: 'listitem', dataset: { state } },
    h('div', { class: 'fn-k' }, kText),
    h('div', { class: 'fn-cap' }, cap));
  if (state === 'won') node.style.setProperty('--stage-c', STAGE_COLOR[letter] || 'var(--accent)');
  if (art && art.childNodes.length) node.appendChild(art);
  return node;
}
function fnCaption(state, letter) {
  if (state === 'won') return '✓ decided here';
  if (state === 'skip') return 'not reached';
  return letter === 'B' ? 'passed — scored, no short-circuit' : 'passed — no decision';
}
// True when a non-self live candidate carries this item's exact error_hash.
function sameHashHistory(d) {
  const eh = d.signature && d.signature.error_hash;
  if (!eh || !d.reconstruction) return false;
  return (d.reconstruction.candidates || []).some((c) => !c.is_self && c.error_hash === eh);
}

// Top-3 non-self candidate evidence strip.
function candStrip(d) {
  const r = d.reconstruction;
  const sig = d.signature || {};
  const n = r.candidates.length;
  const cands = r.candidates.filter((c) => !c.is_self).slice(0, 3);
  const wrap = h('div', {});
  wrap.appendChild(h('div', { class: 'section-title', style: { marginTop: '16px' } },
    `Closest labeled history — top ${cands.length} of ${n} retrieved (live)`));
  const strip = h('div', { class: 'cand-strip' });
  for (let i = 0; i < cands.length; i++) {
    const cd = cands[i];
    const chips = h('div', { class: 'cand-chips' });
    if (cd.exception_fp != null && sig.exception_fp != null && cd.exception_fp === sig.exception_fp) {
      chips.append(h('span', { class: 'chip mono', title: `same exception_fp ${cd.exception_fp}` }, 'fp ='));
    }
    if (cd.error_hash != null && sig.error_hash != null && cd.error_hash === sig.error_hash) {
      chips.append(h('span', { class: 'chip mono', title: 'same error_hash — Stage-A grade match' }, 'hash ='));
    }
    chips.append(h('span', { class: 'chip mono', title: 'template-set Jaccard vs this item' }, `jac ${fmt(cd.jaccard_templates, 2)}`));
    const si = srcInfo(cd.label_source);
    chips.append(h('span', { class: 'chip', title: `label_source: ${si.raw ?? 'not recorded'}${si.weight != null ? ` — src_weight ${si.weight}` : ''}` }, `src ${si.plain}`));
    if (cd.launch_number != null) chips.append(h('span', { class: 'chip mono' }, `launch #${cd.launch_number}`));
    if (cd.mode_id != null) chips.append(h('span', { class: 'chip mono' }, `mode ${cd.mode_id}`));

    const cosBar = h('span', { class: 'microbar cc-bar' });
    if (cd.cosine != null) cosBar.appendChild(h('i', { style: { width: `${Math.max(0, Math.min(1, cd.cosine)) * 100}%` } }));
    strip.appendChild(h('div', { class: 'cand-card' },
      h('div', { class: 'cand-head' },
        h('span', { class: 'cand-rank' }, `#${i + 1}`),
        idChip(`item ${cd.item_id}`, cd.ui_url, 'chip mono'),
        defectBadge(cd.issue_type, grp(cd.issue_type))),
      h('div', { class: 'cand-cos' },
        h('span', { class: 'cc-k' }, 'cos'),
        cosBar,
        h('span', { class: 'cc-v', title: cd.cosine == null ? 'no dense score — dense leg inactive for this pair' : null }, cd.cosine == null ? '—' : fmt(cd.cosine, 3))),
      chips));
  }
  wrap.appendChild(strip);
  // drift check (both numbers real): stored top1_cosine vs live top non-self cosine
  const stored = decisionFeatureValue(d, 'top1_cosine');
  const liveTop = cands.find((c) => c.cosine != null);
  if (stored != null && liveTop && Math.abs(stored - liveTop.cosine) > 0.005) {
    wrap.appendChild(h('p', { class: 'note', style: { marginTop: '8px' } },
      `stored top1_cosine at decision time ${fmt(stored, 3)} vs ${fmt(liveTop.cosine, 3)} now — retrieval has drifted since the decision.`));
  }
  return wrap;
}
function decisionFeatureValue(d, key) {
  if (!d.decision || !d.decision.features) return null;
  const f = d.decision.features.find((x) => x.key === key);
  return f ? f.value : null;
}

// Full reconstruction table behind an expander, with the RRF explainer.
function rrfDrawer(r) {
  const maxRrf = Math.max(...r.candidates.map((x) => x.rrf_score || 0), 1e-9);
  const tbl = h('table', { class: 'data' },
    h('thead', {}, h('tr', {},
      ...['#', 'item', 'label', 'src', 'lex rank', 'dense rank', 'cosine', 'jaccard', 'RRF fused'].map((t) => h('th', {}, t)))),
    h('tbody', {}, ...r.candidates.map((cd, i) => {
      const si = srcInfo(cd.label_source);
      return h('tr', { class: cd.is_self ? 'is-self' : '' },
        h('td', { class: 'rank' }, String(i + 1)),
        h('td', {}, idChip(cd.item_id, cd.ui_url, 'mono'), cd.is_self ? h('span', { class: 'chip', style: { marginLeft: '6px' }, title: 'The query item retrieved itself — proof the index sees it; excluded from candidate features.' }, 'this item') : ''),
        h('td', {}, defectBadge(cd.issue_type, grp(cd.issue_type))),
        h('td', {}, h('span', { class: 'chip', title: `raw label_source: ${si.raw ?? 'none'}` }, si.plain)),
        h('td', { class: 'rank' }, cd.sparse_rank ?? '—'),
        h('td', { class: 'rank' }, cd.dense_rank ?? '—'),
        h('td', { class: 'num' }, cd.cosine == null ? '—' : fmt(cd.cosine, 3)),
        h('td', { class: 'num' }, fmt(cd.jaccard_templates, 2)),
        h('td', {}, h('div', { class: 'flex center gap-8' },
          h('div', { class: 'microbar', style: { width: '80px' } }, h('i', { style: { width: (100 * (cd.rrf_score || 0) / maxRrf) + '%' }, title: `1/(60+${cd.sparse_rank ?? '∞'}) + 1/(60+${cd.dense_rank ?? '∞'}) = ${fmt(cd.rrf_score, 4)}` })),
          h('span', { class: 'mono', style: { fontSize: '11px' } }, fmt(cd.rrf_score, 4)))));
    })));
  return engDrawer('matching-recon',
    `Live Stage-C reconstruction — ${r.candidates.length} candidates · HYBRID_RETRIEVAL v${r.hybrid_retrieval_version}`,
    h('p', { class: 'note', style: { marginBottom: '8px' } },
      h('b', {}, 'RRF'), ' fuses the two rank lists — lexical (weighted tsvector) and dense (cosine): each candidate scores ',
      h('span', { class: 'mono' }, 'Σ 1/(k + rank)'), ' over the lists it appears in, ',
      h('span', { class: 'mono' }, 'k = 60'), '. The large k damps rank-1 dominance so one leg cannot outvote the other.'),
    h('p', { class: 'note recon', style: { marginBottom: '10px' } }, r.note || ''),
    h('div', { class: 'table-wrap' }, tbl),
    h('p', { class: 'note', style: { marginTop: '8px' } }, 'ordered by ', h('span', { class: 'mono' }, 'rrf_score DESC, item_id DESC'), ' (tie-break).'));
}

function matchingEng(d) {
  const m = d.matching;
  const r = d.reconstruction || {};
  const q = r.query || {};
  const mm = m.matched_mode;
  return engDetails('matching',
    h('dl', { class: 'kv' },
      h('dt', {}, 'stage'), h('dd', { class: 'mono' }, m.stage),
      h('dt', {}, 'matched_item_id'), h('dd', { class: 'mono' }, m.matched_item_id ?? 'null'),
      h('dt', {}, 'matched_mode_id'), h('dd', { class: 'mono' }, m.matched_mode_id ?? 'null'),
      h('dt', {}, 'suggestion_id'), h('dd', { class: 'mono' }, m.suggestion_id ?? '—'),
      ...(mm ? [
        h('dt', {}, 'mode.label'), h('dd', { class: 'mono' }, mm.label),
        h('dt', {}, 'mode.purity'), h('dd', { class: 'mono' }, String(mm.purity)),
        h('dt', {}, 'mode.support'), h('dd', { class: 'mono' }, String(mm.support)),
        h('dt', {}, 'mode.seed_key'), h('dd', { class: 'mono' }, mm.seed_key ?? '—'),
      ] : []),
      h('dt', {}, 'hybrid_retrieval_version'), h('dd', { class: 'mono' }, String(r.hybrid_retrieval_version ?? '—')),
      h('dt', {}, 'stage_note'), h('dd', {}, m.stage_note || '—')),
    h('div', { class: 'section-title', style: { margin: '12px 0 6px' } }, 'reconstruction query (bound into the versioned SQL)'),
    h('dl', { class: 'kv' },
      h('dt', {}, 'salient_terms'), h('dd', { class: 'mono', style: { fontSize: '11px', wordBreak: 'break-word' } }, q.salient_terms || '—'),
      h('dt', {}, 'template_ids'), h('dd', { class: 'mono' }, `[${(q.template_ids || []).join(', ')}]`),
      h('dt', {}, 'emb_model_ver'), h('dd', { class: 'mono' }, String(q.emb_model_ver ?? '—')),
      h('dt', {}, 'dense_active'), h('dd', { class: 'mono' }, String(q.dense_active ?? '—'))));
}

function grp(label) {
  if (!label) return 'none';
  for (const g of ['pb', 'ab', 'si', 'nd', 'ti']) if (label.startsWith(g)) return g;
  return 'other';
}

// ---- Stage 4: features + decision ----
const GROUP_NAME = {
  retrieval: 'similar past failures (retrieval)',
  history: 'this test’s track record (history)',
  kb: 'known failure modes (kb)',
  grouping: 'this launch’s failure pattern (grouping)',
  signal: 'log contents (signal)',
  llm: 'LLM extractor (llm)',
  discriminant: 'exact-detail agreement (discriminant)',
  other: 'uncatalogued (other)',
};
const METHOD_PLAIN = { hash: 'exact match', kb: 'known failure mode', gbm: 'learned model', rule_cold: 'starter rules' };
const METHOD_TIP = {
  hash: 'An identical failure (same error ID, error_hash) was seen before and labeled by a human. That label is inherited. Extra guards: recent (≤ 180 days), trusted (confidence ≥ 0.9), and the two failures still agree on unmasked details such as status codes and identifiers (discriminant gate). Confidence is fixed at 0.95 (Stage A).',
  kb: 'This failure matches an entry in the catalog of known, confirmed failure modes (knowledge base): match score ≥ 0.85, catalog entry ≥ 95 % label-pure with ≥ 10 confirmed members. Confidence is capped at 0.93.',
  gbm: 'A trained model (gradient-boosted trees, LightGBM) weighed all evidence signals — similar past failures, this test’s track record, the launch failure pattern, log contents — and produced a calibrated probability for each label.',
  rule_cold: 'No trained model is available yet (cold start). Simple safety rules decide: exact match, then catalog, then a pre-configured seed rule for this failure kind (needs confidence ≥ 0.7); otherwise the analyzer abstains.',
};
const BAND_CHIP = { auto: 'auto-applied', suggest: 'suggested — needs a human', abstain: 'abstain → To Investigate' };
const BAND_TIP = {
  auto: 'Confidence is at or above 0.75 (tau_auto). The label was applied without waiting for a human. A human can still correct it later.',
  suggest: 'Confidence is between 0.45 (tau_suggest) and 0.75 (tau_auto). The label is shown as a suggestion; a human confirms or corrects it.',
  abstain: 'Confidence is below 0.45 (tau_suggest), or a safety guard fired. The analyzer says “I do not know” instead of guessing. The item goes to To Investigate.',
};
const OUTCOME_TIP = {
  accepted: 'A person reviewed the suggestion and kept it.',
  corrected: 'A person reviewed the suggestion and picked a different label. This correction feeds future training.',
  ignored: 'Nobody acted on the suggestion before it expired or the item moved on.',
  pending: 'The suggestion is still open; no person has acted on it yet.',
};

function decisionCard(d) {
  const dec = d.decision;
  const c = card('Decision', { step: 4, sub: 'what the analyzer decided, how sure it was, and why (features & policy)' });
  c.id = 'j-decision'; // scroll target for the Feedback → Decision cross-link
  const body = c.querySelector('.card-body');
  if (!dec) {
    body.appendChild(emptyState('🎯', 'No decision recorded',
      'No decision recorded — the analyzer has not scored this item yet (no suggestion row).',
      'analyzer.suggestion'));
    return c;
  }
  const method = dec.method || 'gbm';
  const label = defectName(dec.predicted_label, dec.predicted_group);
  const matchedItemId = d.matching && d.matching.matched_item_id;
  const matchedMode = d.matching && d.matching.matched_mode;
  const modeTitle = matchedMode && matchedMode.title;

  // L1 takeaway (band × method matrix)
  body.appendChild(decisionTakeaway(dec, method, label, matchedItemId, modeTitle));

  // verdict chip row: band + method + (label, unless abstain) + outcome
  const chips = h('div', { class: 'flex gap-8 wrap center', style: { marginBottom: '16px' } });
  chips.appendChild(bandChip(dec));
  chips.appendChild(methodChip(method));
  if (dec.band !== 'abstain') chips.appendChild(labelChip(dec, label));
  chips.appendChild(outcomeBadge(dec.outcome));
  if (dec.llm_used) {
    chips.appendChild(h('span', { class: 'badge', title: 'A large language model was asked to double-check this decision before it was stored.', style: { background: 'var(--accent-soft)', color: 'var(--accent)' } }, '🧠 LLM judge consulted'));
  }
  body.appendChild(chips);

  // L2 — banded confidence gauge + active-band legend
  const gaugeRow = h('div', { class: 'flex gap-12 wrap center', style: { marginBottom: '6px' } });
  const gaugeWrap = h('div', { class: 'gauge-wrap' });
  gaugeRow.append(gaugeWrap, h('div', { style: { flex: '1 1 220px' } }, bandLegend(dec)));
  body.appendChild(gaugeRow);
  requestAnimationFrame(() => drawGauge(gaugeWrap, dec));

  // abstain reason (only when the row stores one — verbatim, honest omission otherwise)
  if (dec.band === 'abstain' && dec.abstain_reason) body.appendChild(abstainReasonBlock(dec, label));

  // analyzer explanation (real stored field, no decorative quotes)
  if (dec.explanation) {
    body.appendChild(h('p', { class: 'note', style: { margin: '8px 0 16px' } },
      h('span', { class: 'muted', style: { textTransform: 'uppercase', letterSpacing: '.5px', fontSize: '10px' } }, 'analyzer’s explanation'),
      h('br'), dec.explanation));
  }

  // L2 — evidence groups (feature vector, grouped by evidence type, Σ|v|-sorted)
  body.appendChild(h('div', { class: 'section-title', style: { marginTop: '4px' } },
    `Evidence the decision weighed (${dec.feature_count} of ${dec.feature_total} signals, largest first) (feature vector)`));
  body.appendChild(evidenceGroups(dec.features));

  // L4 — engineer details (all raw fields, one click away)
  body.appendChild(engDetails('decision',
    h('dl', { class: 'kv' },
      h('dt', {}, 'model_ver'), h('dd', { class: 'mono', style: { fontSize: '12px' } }, dec.model_ver),
      h('dt', {}, 'method'), h('dd', { class: 'mono' }, method),
      ...(dec.abstain_reason ? [h('dt', {}, 'abstain_reason'), h('dd', { class: 'mono' }, dec.abstain_reason)] : []),
      ...(matchedItemId ? [h('dt', {}, 'matched_item_id'), h('dd', { class: 'mono' }, String(matchedItemId))] : []),
      ...(d.matching && d.matching.matched_mode_id ? [h('dt', {}, 'matched_mode_id'), h('dd', { class: 'mono' }, String(d.matching.matched_mode_id))] : []),
      h('dt', {}, 'llm_used'), h('dd', { class: 'mono' }, String(dec.llm_used)),
      h('dt', {}, 'outcome'), h('dd', { class: 'mono' }, dec.outcome))));
  return c;
}

function muted(t) { return h('span', { class: 'muted' }, t); }

// L1 decision takeaway — first matching (band, method) variant wins.
function decisionTakeaway(dec, method, label, matchedItemId, modeTitle) {
  const p = h('p', { class: 'takeaway' });
  const C = fmt(dec.confidence, 2);
  const TA = fmt(dec.tau_auto, 2);
  const TS = fmt(dec.tau_suggest, 2);
  const mode = modeTitle ? `“${modeTitle}”` : 'in the catalog';
  if (dec.band === 'auto') {
    if (method === 'hash' && matchedItemId) {
      p.append(h('b', {}, label), ', auto-applied — inherited from human-labeled identical failure ',
        h('span', { class: 'mono' }, `#${matchedItemId}`), ' ', muted(`(exact hash match; ${C} ≥ τ_auto ${TA})`));
    } else if (method === 'kb') {
      p.append(h('b', {}, label), `, auto-applied — matches known failure mode ${mode} `, muted(`(kb; ${C} ≥ τ_auto ${TA})`));
    } else if (method === 'rule_cold') {
      p.append(h('b', {}, label), ', auto-applied by starter rules — no trained model yet ', muted(`(rule_cold; ${C} ≥ τ_auto ${TA})`));
    } else {
      p.append(h('b', {}, label), ', auto-applied — the learned model was confident enough to act ', muted(`(gbm; ${C} ≥ τ_auto ${TA})`));
    }
  } else if (dec.band === 'suggest') {
    p.append(h('b', {}, `Suggests ${label}`), ' — ');
    if (method === 'hash' && matchedItemId) {
      p.append('resembles labeled failure ', h('span', { class: 'mono' }, `#${matchedItemId}`), ', a human confirms ');
    } else if (method === 'kb') {
      p.append(`matches known failure mode ${mode}, a human confirms `);
    } else if (method === 'rule_cold') {
      p.append('a starter rule points here, a human confirms ');
    } else {
      p.append('the learned model leans this way, a human confirms ');
    }
    p.append(muted(`(${method}; ${TS} ≤ ${C} < τ_auto ${TA})`));
  } else { // abstain
    p.append(h('b', {}, 'Abstained → To Investigate'), ' — ');
    if (method === 'hash' && matchedItemId) {
      p.append('an exact-match candidate ', h('span', { class: 'mono' }, `#${matchedItemId}`), ' fell below the suggest bar ');
    } else if (method === 'kb') {
      p.append('a catalog match fell below the suggest bar ');
    } else if (method === 'rule_cold') {
      p.append('no confident rule and no trained model yet ');
    } else {
      p.append('confidence below the suggest bar ');
    }
    p.append(muted(`(${method}; ${method === 'gbm' ? 'p* ' : ''}${C} < τ_suggest ${TS})`));
  }
  return p;
}

function bandChip(dec) {
  const col = `var(--band-${dec.band})`;
  const isAbstain = dec.band === 'abstain';
  return h('span', { class: 'badge', title: BAND_TIP[dec.band],
    style: { background: `color-mix(in srgb, ${col} ${isAbstain ? 22 : 18}%, transparent)`, color: isAbstain ? 'var(--ink-2)' : col, borderColor: col } },
    h('span', { class: 'dot', style: { background: col } }), BAND_CHIP[dec.band]);
}
function methodChip(method) {
  return h('span', { class: 'method-chip', title: METHOD_TIP[method] || '' },
    h('span', { class: 'k' }, method), METHOD_PLAIN[method] || method);
}
function labelChip(dec, label) {
  const g = dec.predicted_group;
  const cls = ['pb', 'ab', 'si', 'nd', 'ti'].includes(g) ? g : 'none';
  return h('span', { class: `badge lbl ${cls}` }, h('span', { class: 'dot' }), `label: ${label}`);
}
function outcomeBadge(o) {
  const map = { accepted: 'var(--good)', corrected: 'var(--warning)', ignored: 'var(--muted)', pending: 'var(--accent)' };
  const col = map[o] || 'var(--muted)';
  const text = o === 'pending' ? 'pending review' : o;
  return h('span', { class: 'badge', title: OUTCOME_TIP[o] || null, style: { background: `color-mix(in srgb, ${col} 16%, transparent)`, color: col } }, text);
}

function bandLegend(dec) {
  const TS = fmt(dec.tau_suggest, 2), TA = fmt(dec.tau_auto, 2);
  const item = (band, text, active) => h('span', { class: 'bl-item', role: 'listitem', ...(active ? { 'data-active': '' } : {}) },
    h('i', { class: 'dot', style: { background: `var(--band-${band})` } }), text);
  return h('div', { class: 'band-legend', role: 'list' },
    item('abstain', `abstain · < ${TS} → TI`, dec.band === 'abstain'),
    item('suggest', `suggest · ${TS}–${TA}`, dec.band === 'suggest'),
    item('auto', `auto · ≥ ${TA}`, dec.band === 'auto'));
}

function abstainReasonText(dec, label) {
  const C = fmt(dec.confidence, 2);
  const TS = fmt(dec.tau_suggest, 2);
  switch (dec.abstain_reason) {
    case 'no_confident_rule':
      return 'No rule was confident enough: no identical labeled failure (hash), no catalog match (kb), and no trusted seed rule. With no trained model yet, the honest answer is “investigate”.';
    case 'gbm_below_suggest':
      return dec.predicted_group === 'ti'
        ? `The model scored every label below the suggestion bar of ${TS} (tau_suggest).`
        : `The model scored every label below the suggestion bar of ${TS} (tau_suggest). Best guess was ${label} at ${C}, which is too weak to show.`;
    case 'gbm_boilerplate_only_neighbor':
      return 'The model’s only support was a look-alike failure that shares no concrete evidence with this one — no matching error ID, no shared log templates, no shared identifiers, only generic error text. That is not real support, so the suggestion was withdrawn.';
    default:
      return `The analyzer abstained for reason “${dec.abstain_reason}” (code not yet documented in the Inspector).`;
  }
}
function abstainReasonBlock(dec, label) {
  return h('div', { class: 'abstain-reason' },
    abstainReasonText(dec, label),
    h('div', { style: { marginTop: '6px' } }, h('span', { class: 'code' }, dec.abstain_reason)));
}

// ---- Banded confidence gauge (inline SVG, geometry per lens §2.2) ----
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888'; }
function gaugeAngle(v) { return 200 - 220 * v; }
function gaugePolar(cx, cy, r, deg) { const a = deg * Math.PI / 180; return { x: cx + r * Math.cos(a), y: cy - r * Math.sin(a) }; }
function gaugeArc(cx, cy, r, v0, v1) {
  const steps = Math.max(2, Math.round((v1 - v0) * 90));
  let dstr = '';
  for (let i = 0; i <= steps; i++) {
    const v = v0 + (v1 - v0) * i / steps;
    const p = gaugePolar(cx, cy, r, gaugeAngle(v));
    dstr += (i === 0 ? 'M' : 'L') + p.x.toFixed(2) + ' ' + p.y.toFixed(2) + ' ';
  }
  return dstr;
}
function drawGauge(el, dec) {
  clear(el);
  const W = 260, H = 150, cx = 130, cy = 118, R = 92, sw = 14;
  const NS = 'http://www.w3.org/2000/svg';
  const bands = [
    [0, dec.tau_suggest, cssVar('--band-abstain')],
    [dec.tau_suggest, dec.tau_auto, cssVar('--band-suggest')],
    [dec.tau_auto, 1, cssVar('--band-auto')],
  ];
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', W); svg.setAttribute('height', H); svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  for (const [a, b, col] of bands) {
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', gaugeArc(cx, cy, R, a, b));
    path.setAttribute('fill', 'none'); path.setAttribute('stroke', col);
    path.setAttribute('stroke-width', sw); path.setAttribute('stroke-linecap', 'butt');
    svg.appendChild(path);
  }
  const conf = Math.max(0, Math.min(1, dec.confidence));
  const np = gaugePolar(cx, cy, R - 4, gaugeAngle(conf));
  const needle = document.createElementNS(NS, 'line');
  needle.setAttribute('x1', cx); needle.setAttribute('y1', cy);
  needle.setAttribute('x2', np.x.toFixed(2)); needle.setAttribute('y2', np.y.toFixed(2));
  needle.setAttribute('stroke', INK); needle.setAttribute('stroke-width', 3); needle.setAttribute('stroke-linecap', 'round');
  svg.appendChild(needle);
  const hub = document.createElementNS(NS, 'circle');
  hub.setAttribute('cx', cx); hub.setAttribute('cy', cy); hub.setAttribute('r', 5); hub.setAttribute('fill', INK);
  svg.appendChild(hub);
  const val = document.createElementNS(NS, 'text');
  val.setAttribute('x', cx); val.setAttribute('y', cy - 26); val.setAttribute('text-anchor', 'middle');
  val.setAttribute('fill', INK); val.setAttribute('font-size', '26'); val.setAttribute('font-weight', '700');
  val.textContent = dec.confidence.toFixed(2);
  svg.appendChild(val);
  const cap = document.createElementNS(NS, 'text');
  cap.setAttribute('x', cx); cap.setAttribute('y', cy + 22); cap.setAttribute('text-anchor', 'middle');
  cap.setAttribute('fill', MUTED); cap.setAttribute('font-size', '10');
  cap.textContent = 'confidence';
  svg.appendChild(cap);
  el.appendChild(svg);
  // threshold chips (positioned HTML — values from the payload, never hardcoded)
  for (const t of [dec.tau_suggest, dec.tau_auto]) {
    const p = gaugePolar(cx, cy, R + 15, gaugeAngle(t));
    el.appendChild(h('span', { class: 'thresh-chip mono', style: { left: p.x + 'px', top: p.y + 'px' } }, t.toFixed(2).replace(/^0/, '')));
  }
}

// ---- Evidence groups (feature vector → group rows, lazy per-feature bars) ----
function evidenceGroups(features) {
  const wrap = h('div', {});
  if (!features || !features.length) {
    wrap.appendChild(h('p', { class: 'note' }, 'No stored feature vector for this suggestion.'));
    return wrap;
  }
  const byGroup = new Map();
  for (const f of features) {
    const g = f.group || 'other';
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g).push(f);
  }
  const models = [...byGroup.entries()].map(([group, feats]) => {
    const sum = feats.reduce((s, f) => s + Math.abs(f.value), 0);
    const top = feats.reduce((m, f) => (Math.abs(f.value) > Math.abs(m.value) ? f : m), feats[0]);
    const singleMax = Math.max(...feats.map((f) => Math.abs(f.value)));
    return { group, feats, sum, top, singleMax };
  });
  models.sort((a, b) => b.sum - a.sum); // "largest first" = Σ|v| desc
  const maxSum = Math.max(...models.map((m) => m.sum), 1e-9);
  // auto-expand the group holding the single largest |value| feature
  const autoGroup = models.reduce((a, b) => (b.singleMax > a.singleMax ? b : a), models[0]).group;
  for (const m of models) wrap.appendChild(eviRow(m, maxSum, m.group === autoGroup));
  return wrap;
}

function eviRow(m, maxSum, open) {
  const col = FEATURE_GROUP_COLORS[m.group] || FEATURE_GROUP_COLORS.other;
  const row = h('div', { class: 'evi-row', dataset: { group: m.group } });
  const head = h('button', { class: 'evi-head', 'aria-expanded': String(open) },
    h('i', { class: 'dot', style: { background: col } }),
    h('span', { class: 'evi-name' }, GROUP_NAME[m.group] || m.group),
    h('span', { class: 'chip' }, String(m.feats.length)),
    h('span', { class: 'evi-top' }, `top: ${m.top.label} ${fmt(m.top.value, 2)}`),
    h('span', { class: 'evi-bar-cell' },
      h('span', { class: 'microbar', style: { display: 'block' } },
        h('i', { style: { width: (m.sum / maxSum * 100).toFixed(0) + '%', background: col } }))),
    h('span', { class: 'evi-sum mono' }, `Σ|v| ${fmt(m.sum, 2)}`),
    h('span', { class: 'evi-caret' }, '▸'));
  const bodyEl = h('div', { class: 'evi-body' });
  if (!open) bodyEl.hidden = true;
  const feats = m.feats.slice().sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  const gmax = Math.max(...feats.map((f) => Math.abs(f.value)), 1e-9);
  for (const f of feats) {
    const title = `${f.key} = ${fmt(f.value, 4)} · ${f.definition} · range ${f.range} · default ${f.default}`;
    bodyEl.appendChild(h('div', { class: 'feat-row', title },
      h('span', {}, h('span', { class: 'feat-lbl' }, f.label), h('br'), h('span', { class: 'feat-key' }, f.key)),
      h('span', { class: 'feat-bar' }, h('i', { style: { width: (Math.abs(f.value) / gmax * 100) + '%', background: col } })),
      h('span', { class: 'feat-val' }, fmt(f.value, 2))));
  }
  head.addEventListener('click', () => {
    const now = head.getAttribute('aria-expanded') === 'true';
    head.setAttribute('aria-expanded', String(!now));
    bodyEl.hidden = now;
  });
  row.append(head, bodyEl);
  return row;
}

// ---- Stage 5: feedback ----
function feedbackCard(d) {
  const F = d.feedback; // newest-first (payload ORDER BY ts DESC)
  const c = card('Feedback', { step: 5, sub: 'label_event log — append-only' });
  const body = c.querySelector('.card-body');
  if (!F.length) {
    body.appendChild(emptyState('✍️', 'No label events',
      'No label events — never labeled or relabeled since ingest. Events append on RP defect updates (rp), UI accepts (human), auto-apply (ai_suggested), seed catalog (seed).',
      'analyzer.label_event'));
    return c;
  }

  // L1 takeaway
  body.appendChild(feedbackGlance(F, d.decision, d.matching));

  // L2 — timeline, oldest → newest, newest emphasized
  const ol = h('ol', { class: 'tl' });
  const chrono = F.slice().reverse(); // oldest → newest
  for (let i = 0; i < chrono.length; i++) {
    const e = chrono[i];
    const isNewest = i === chrono.length - 1;
    const si = srcInfo(e.source);
    const main = h('div', { class: 'tl-main' },
      e.old_label ? defectBadge(e.old_label, e.old_group) : h('span', { class: 'muted', title: 'First label event for this item — no prior label recorded.' }, '(first label)'),
      h('span', { class: 'muted', title: 'old_label → new_label as stored on the event; the log is append-only, current label = newest event.' }, '→'),
      defectBadge(e.new_label, e.new_group),
      srcChip(si),
      suggestionChip(e, d));
    const row = h('li', { class: 'tl-ev' + (isNewest ? ' now' : '') },
      h('i', { class: 'tl-dot', style: { '--c': `var(--lbl-${e.new_group || 'none'})` }, 'aria-hidden': 'true' }),
      h('div', { style: { flex: '1' } }, main, decayLine(e, si)),
      h('time', { datetime: e.ts, class: 'mono', title: e.ts || '' }, relTime(e.ts)));
    ol.appendChild(row);
  }
  body.appendChild(ol);

  if (F.length === 50) {
    body.appendChild(h('div', { class: 'cap-line' }, 'showing the latest 50 events (query cap).'));
  }

  // L4 — table twin
  body.appendChild(engDetails('feedback',
    h('div', { class: 'table-wrap' },
      h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, ...['event_id', 'old_label', 'new_label', 'source (raw)', 'suggestion_id', 'ts (ISO)'].map((t) => h('th', {}, t)))),
        h('tbody', {}, ...F.map((e) => h('tr', {},
          h('td', { class: 'mono' }, String(e.event_id)),
          h('td', { class: 'mono' }, e.old_label ?? '—'),
          h('td', { class: 'mono' }, e.new_label ?? '—'),
          h('td', { class: 'mono' }, e.source ?? '—'),
          h('td', { class: 'mono' }, e.suggestion_id ?? '—'),
          h('td', { class: 'mono' }, e.ts ?? '—')))))),
    h('p', { class: 'note', style: { marginTop: '8px' } }, 'append-only ', h('span', { class: 'mono' }, 'analyzer.label_event'), ', newest-first query, cap 50.')));
  return c;
}

function feedbackGlance(F, dec, matching) {
  const p = h('p', { class: 'takeaway' });
  const mono = (t) => h('span', { class: 'mono' }, t);
  const mut = (t) => h('span', { class: 'muted' }, t);
  const N = F.length;
  const last = F[0];
  const first = F[N - 1];
  const actor = (e) => srcInfo(e.source).actor;
  if (N === 1 && last.old_label == null) {
    p.append('Labeled once: ', mono(last.new_label), ` set by ${actor(last)} — `, mono(shortTime(last.ts)), '.');
  } else if (N === 1 && last.old_label != null && last.old_label !== last.new_label) {
    p.append('Relabeled once: ', mono(last.old_label), ' → ', mono(last.new_label), ` by ${actor(last)} — `, mono(shortTime(last.ts)), '.');
  } else if (N === 1) {
    p.append('Label ', mono(last.new_label), ` re-confirmed by ${actor(last)} — `, mono(shortTime(last.ts)), '.');
  } else {
    p.append(h('b', {}, `Labeled ${N}×`), ': ');
    const sugId = matching && matching.suggestion_id;
    if (last.suggestion_id != null && dec && sugId != null && last.suggestion_id === sugId && last.old_label === dec.predicted_label) {
      p.append("analyzer's ", mono(dec.predicted_label), ' overridden — ');
    }
    p.append('latest ', mono(last.old_label ?? '(none)'), ' → ', mono(last.new_label), ` by ${actor(last)}`);
    if (last.old_label != null && last.old_label !== last.new_label) p.append(' ', mut('(correction)'));
    p.append(' — ', mono(shortTime(last.ts)), '; first ', mono(shortTime(first.ts)), '.');
    if (N === 50) p.append(' Showing the last 50 events (query cap).');
  }
  return p;
}

function srcChip(si) {
  const title = `raw source: ${si.raw ?? 'not recorded'}${si.weight != null ? ` — src_weight ${si.weight}` : ''}`;
  return h('span', { class: 'method-chip', title }, h('span', { class: 'k' }, si.k), si.text);
}

// suggestion_id chip; when it equals the on-page decision's suggestion, it links to it.
function suggestionChip(e, d) {
  if (e.suggestion_id == null) return null;
  const onPage = d.matching && d.matching.suggestion_id;
  if (onPage != null && e.suggestion_id === onPage) {
    return h('button', {
      class: 'chip mono js-link',
      title: `Overrides suggestion ${e.suggestion_id} — the decision shown on stage 4. Click to locate.`,
      onclick: () => scrollPulse('j-decision'),
    }, `sug ${e.suggestion_id} ↑ stage 4`);
  }
  return h('span', { class: 'chip mono', title: 'The suggestion this event answered (an older decision — not the one shown on this page).' }, `sug ${e.suggestion_id}`);
}

function decayLine(e, si) {
  if (si.weight == null || !e.ts) return null;
  const ageDays = Math.max(0, Math.floor((Date.now() - new Date(e.ts).getTime()) / 86400000));
  const decay = Math.exp(-Math.LN2 * ageDays / 90);
  const eff = si.weight * decay;
  const line = h('div', { class: 'tl-decay', title: 'decay(d) = exp(−ln2 · d/90), half-life 90 d. Effective pull on future decisions = src_weight × decay(age).' },
    `evidence weight now: src_weight ${si.weight} × decay ${decay.toFixed(2)} = ${eff.toFixed(2)}`);
  if (si.weight >= 0.9) line.appendChild(h('span', { style: { color: 'var(--good)' } }, ' · trains the model'));
  return line;
}

// Scroll to a card by id and pulse it (motion suppressed under reduced-motion).
function scrollPulse(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (el.animate) el.animate([{ boxShadow: '0 0 0 2px var(--accent)' }, { boxShadow: 'var(--shadow)' }], { duration: 900 });
}
