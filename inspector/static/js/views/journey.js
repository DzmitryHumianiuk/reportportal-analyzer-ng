// Item Journey — the main view. Picker (launch → item) + pipeline stepper with
// expandable stage cards, connected visually, plus the live Stage-B reconstruction.
import { api } from '../api.js';
import {
  h, clear, card, defectBadge, defectBadgeAbbr, defectName, idChip, setDefects, fmt,
  shortTime, highlightPattern, emptyState, loading, engDetails,
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
    sub: 'who else failed like this in the same run (co-failure launch group, spec §5)',
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
    h('p', { class: 'note', style: { marginTop: '8px' } }, 'co-failure launch group (spec §5)')));

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
  return h('div', { class: 'si-meter', title: 'burst signal (si_prior) — burst prior from spec §5 · range 0 to 0.9' },
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
function matchingCard(d) {
  const m = d.matching;
  const c = card('Matching trace', { step: 3, sub: m.stage_label });
  const body = c.querySelector('.card-body');

  const stageColor = { A: 'var(--lbl-nd)', AB: 'var(--lbl-si)', C: 'var(--accent)', abstain: 'var(--lbl-ti)', none: 'var(--muted)' }[m.stage] || 'var(--muted)';
  body.appendChild(h('div', { class: 'flex gap-8 center wrap mb-8' },
    h('span', { class: 'badge', style: { background: 'color-mix(in srgb,' + stageColor + ' 16%, transparent)', color: stageColor, borderColor: stageColor } },
      h('span', { class: 'dot', style: { background: stageColor } }), m.stage_label),
    m.matched_item_id ? idChip(`matched item ${m.matched_item_id}`, m.matched_item_url, 'chip mono') : null,
    m.matched_mode_id ? h('span', { class: 'chip mono' }, `matched mode ${m.matched_mode_id}`) : null));
  body.appendChild(h('p', { class: 'note' }, m.stage_note || m.explanation || ''));

  if (m.matched_mode) {
    const mm = m.matched_mode;
    body.appendChild(h('div', { class: 'flex gap-8 center wrap mt-8' },
      defectBadge(mm.label, mm.label_group),
      h('span', { class: 'chip' }, mm.title || `mode ${mm.mode_id}`),
      h('span', { class: 'chip' }, `purity ${fmt(mm.purity, 2)}`),
      h('span', { class: 'chip' }, `support ${mm.support}`),
      mm.seed_key ? h('span', { class: 'chip mono' }, `seed:${mm.seed_key}`) : null));
  }

  // ---- live reconstruction ----
  const r = d.reconstruction;
  body.appendChild(h('div', { class: 'section-title', style: { marginTop: '18px' } }, 'Live Stage‑B reconstruction'));
  body.appendChild(h('p', { class: 'note recon' }, r.note || ''));
  if (!r.candidates || !r.candidates.length) {
    body.appendChild(emptyState('🔍', 'No candidates retrieved',
      'The versioned hybrid RRF SQL returned no rows against current data (too few comparable signatures, or no lexical/dense hit).',
      'HYBRID_RETRIEVAL v' + (r.hybrid_retrieval_version ?? '?')));
    return c;
  }
  const maxRrf = Math.max(...r.candidates.map((x) => x.rrf_score || 0), 1e-9);
  const tbl = h('table', { class: 'data' },
    h('thead', {}, h('tr', {},
      ...['item', 'label', 'lex rank', 'dense rank', 'cosine', 'jaccard', 'RRF fused'].map((t) => h('th', {}, t)))),
    h('tbody', {}, ...r.candidates.map((cd) => h('tr', { class: cd.is_self ? 'is-self' : '' },
      h('td', {}, idChip(cd.item_id, cd.ui_url, 'mono'), cd.is_self ? h('span', { class: 'chip', style: { marginLeft: '6px' } }, 'this item') : ''),
      h('td', {}, defectBadge(cd.issue_type, grp(cd.issue_type))),
      h('td', { class: 'rank' }, cd.sparse_rank ?? '—'),
      h('td', { class: 'rank' }, cd.dense_rank ?? '—'),
      h('td', { class: 'num' }, cd.cosine == null ? '—' : fmt(cd.cosine, 3)),
      h('td', { class: 'num' }, fmt(cd.jaccard_templates, 2)),
      h('td', {}, h('div', { class: 'flex center gap-8' },
        h('div', { class: 'microbar', style: { width: '80px' } }, h('i', { style: { width: (100 * (cd.rrf_score || 0) / maxRrf) + '%' } })),
        h('span', { class: 'mono', style: { fontSize: '11px' } }, fmt(cd.rrf_score, 4))))))));
  body.appendChild(h('div', { class: 'table-wrap' }, tbl));
  return c;
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
  const c = card('Decision', { step: 4, sub: 'what the analyzer decided, how sure it was, and why (features & policy, spec §6)' });
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
  const c = card('Feedback', { step: 5, sub: 'label_event history' });
  const body = c.querySelector('.card-body');
  if (!d.feedback.length) {
    body.appendChild(emptyState('✍️', 'No label events',
      'This item has never been (re)labeled. label_events are appended on RP defect updates, accepted suggestions, or human UI edits.',
      'analyzer.label_event'));
    return c;
  }
  const list = h('div', { class: 'grid', style: { gap: '8px' } });
  for (const e of d.feedback) {
    list.appendChild(h('div', { class: 'flex between center', style: { padding: '9px 12px', background: 'var(--surface-2)', border: '1px solid var(--hairline)', borderRadius: '8px' } },
      h('div', { class: 'flex center gap-8 wrap' },
        e.old_label ? defectBadge(e.old_label, e.old_group) : h('span', { class: 'muted' }, '(new)'),
        h('span', { class: 'muted' }, '→'),
        defectBadge(e.new_label, e.new_group),
        h('span', { class: 'chip', style: { fontSize: '11px' } }, e.source)),
      h('span', { class: 'muted mono', style: { fontSize: '11px' } }, shortTime(e.ts))));
  }
  body.appendChild(list);
  return c;
}
