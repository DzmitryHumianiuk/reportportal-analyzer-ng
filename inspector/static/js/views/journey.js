// Item Journey — the main view. Picker (launch → item) + pipeline stepper with
// expandable stage cards, connected visually, plus the live Stage-B reconstruction.
import { api } from '../api.js';
import {
  h, clear, card, defectBadge, defectBadgeAbbr, idChip, setDefects, fmt, pct, shortTime,
  highlightPattern, emptyState, loading, echartsBase, INK, INK2, MUTED, HAIRLINE,
  FEATURE_GROUP_COLORS,
} from '../util.js';

const jstate = { launch: null, item: null };

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
    for (const it of items) {
      const iel = h('div', { class: 'list-item' + (it.is_auto_analyzed ? ' halo-auto' : ''), onclick: () => selectItem(it, iel) },
        h('div', { class: 'li-main' },
          h('div', { class: 'li-title' }, it.item_name || `item ${it.item_id}`),
          h('div', { class: 'li-sub' }, idChip(`id ${it.item_id}`, it.ui_url, ''), ` · ${it.exc_text || 'no exc'}`)),
        defectBadgeAbbr(it.issue_type, it.label_group));
      itemList.appendChild(iel);
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
  const c = card('Grouping', { step: 2, sub: 'co-failure launch group (spec §5)' });
  const body = c.querySelector('.card-body');
  if (!g) {
    body.appendChild(emptyState('🧩', 'No launch group',
      'No launch_group covers this item’s error_hash in its launch. Grouping runs when ≥1 failure shares a fingerprint.',
      'analyzer.launch_group'));
    return c;
  }
  body.appendChild(h('div', { class: 'flex gap-12 wrap center' },
    siDial(g.si_prior),
    h('dl', { class: 'kv' },
      h('dt', {}, 'group_id'), h('dd', { class: 'mono' }, g.group_id),
      h('dt', {}, 'fingerprint'), h('dd', { class: 'mono' }, g.fingerprint),
      h('dt', {}, 'members'), h('dd', {}, String(g.member_count)),
      h('dt', {}, 'dominant'), h('dd', {}, g.dominant ? h('span', { class: 'badge', style: { background: 'var(--accent-soft)', color: 'var(--accent)' } }, '🔥 burst') : 'no'),
      h('dt', {}, 'si_prior'), h('dd', {}, fmt(g.si_prior, 2)))));
  return c;
}

function siDial(v) {
  const size = 92, r = 38, cx = size / 2, cy = size / 2;
  const frac = Math.max(0, Math.min(1, (v || 0) / 0.9));
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', size); svg.setAttribute('height', size);
  const bg = document.createElementNS(NS, 'circle');
  const fg = document.createElementNS(NS, 'circle');
  const circ = 2 * Math.PI * r;
  for (const [ci, col, dash] of [[bg, HAIRLINE, 0], [fg, 'var(--warning)', frac]]) {
    ci.setAttribute('cx', cx); ci.setAttribute('cy', cy); ci.setAttribute('r', r);
    ci.setAttribute('fill', 'none'); ci.setAttribute('stroke', col); ci.setAttribute('stroke-width', 8);
    ci.setAttribute('stroke-linecap', 'round');
    ci.setAttribute('transform', `rotate(-90 ${cx} ${cy})`);
    if (ci === fg) { ci.setAttribute('stroke-dasharray', `${dash * circ} ${circ}`); }
    svg.appendChild(ci);
  }
  const wrap = h('div', { class: 'dial', style: { display: 'grid', placeItems: 'center' } });
  wrap.appendChild(svg);
  const label = h('div', { style: { position: 'absolute', textAlign: 'center' } },
    h('div', { style: { fontSize: '17px', fontWeight: 700 } }, fmt(v || 0, 2)),
    h('div', { class: 'muted', style: { fontSize: '9px', textTransform: 'uppercase', letterSpacing: '.5px' } }, 'si prior'));
  wrap.appendChild(label);
  return wrap;
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
function decisionCard(d) {
  const dec = d.decision;
  const c = card('Features & decision', { step: 4, sub: dec ? dec.model_ver : 'no suggestion' });
  const body = c.querySelector('.card-body');
  if (!dec) {
    body.appendChild(emptyState('🎯', 'No decision recorded',
      'No suggestion row exists for this item — the LightGBM policy has not scored it.',
      'analyzer.suggestion'));
    return c;
  }
  // top: gauge + predicted/outcome
  const top = h('div', { class: 'flex gap-12 wrap center between', style: { marginBottom: '14px' } });
  const gaugeWrap = h('div', { class: 'chart', style: { width: '280px', height: '150px' } });
  top.appendChild(gaugeWrap);
  const info = h('div', { class: 'flex gap-8 wrap' },
    h('dl', { class: 'kv' },
      h('dt', {}, 'predicted'), h('dd', {}, defectBadge(dec.predicted_label, dec.predicted_group)),
      h('dt', {}, 'band'), h('dd', {}, h('span', { class: 'badge', style: bandStyle(dec.band) }, bandName(dec.band))),
      h('dt', {}, 'outcome'), h('dd', {}, outcomeBadge(dec.outcome)),
      h('dt', {}, 'model_ver'), h('dd', { class: 'mono', style: { fontSize: '12px' } }, dec.model_ver),
      h('dt', {}, 'llm'), h('dd', {}, dec.llm_used ? h('span', { class: 'badge', style: { background: 'var(--accent-soft)', color: 'var(--accent)' } }, '🧠 judge used') : h('span', { class: 'muted' }, 'no'))));
  top.appendChild(info);
  body.appendChild(top);
  if (dec.explanation) body.appendChild(h('p', { class: 'note', style: { marginBottom: '12px' } }, '“' + dec.explanation + '”'));

  // feature waterfall
  body.appendChild(h('div', { class: 'section-title' }, `Feature vector (${dec.feature_count} of ${dec.feature_total}, sorted by magnitude)`));
  const chart = h('div', { class: 'chart', style: { height: Math.max(220, dec.features.length * 15) + 'px' } });
  body.appendChild(chart);

  requestAnimationFrame(() => {
    drawGauge(gaugeWrap, dec);
    drawFeatureBars(chart, dec.features);
  });
  return c;
}

function bandStyle(band) {
  const col = { auto: 'var(--band-auto)', suggest: 'var(--band-suggest)', abstain: 'var(--band-abstain)' }[band];
  return { background: `color-mix(in srgb, ${col} 18%, transparent)`, color: col, borderColor: col };
}
function outcomeBadge(o) {
  const map = { accepted: 'var(--good)', corrected: 'var(--warning)', ignored: 'var(--muted)', pending: 'var(--accent)' };
  const col = map[o] || 'var(--muted)';
  return h('span', { class: 'badge', style: { background: `color-mix(in srgb, ${col} 16%, transparent)`, color: col } }, o);
}

function drawGauge(el, dec) {
  const chart = echarts.init(el, null, { renderer: 'canvas' });
  chart.setOption({
    series: [{
      type: 'gauge', min: 0, max: 1, radius: '100%', center: ['50%', '68%'],
      startAngle: 200, endAngle: -20, splitNumber: 5,
      axisLine: { lineStyle: { width: 14, color: [
        [dec.tau_suggest, '#5a5d61'], [dec.tau_auto, '#c98500'], [1, '#34a853']] } },
      pointer: { width: 4, length: '62%', itemStyle: { color: INK } },
      axisTick: { show: false }, splitLine: { length: 10, lineStyle: { color: '#0d0d0d' } },
      axisLabel: { color: MUTED, fontSize: 9, distance: -18 },
      anchor: { show: true, size: 8, itemStyle: { color: INK } },
      detail: { valueAnimation: true, formatter: (v) => v.toFixed(2), color: INK, fontSize: 26, offsetCenter: [0, '38%'] },
      title: { offsetCenter: [0, '68%'], color: MUTED, fontSize: 10 },
      data: [{ value: dec.confidence, name: 'confidence' }],
    }],
  });
}

function drawFeatureBars(el, features) {
  const chart = echarts.init(el, null, { renderer: 'canvas' });
  const names = features.map((f) => f.label).reverse();
  const vals = features.map((f) => f.value).reverse();
  const colors = features.map((f) => groupColor(f.group)).reverse();
  chart.setOption({
    ...echartsBase(),
    grid: { left: 8, right: 40, top: 6, bottom: 6, containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: MUTED }, splitLine: { lineStyle: { color: HAIRLINE } } },
    yAxis: { type: 'category', data: names, axisLabel: { color: INK2, fontSize: 10 }, axisLine: { lineStyle: { color: HAIRLINE } }, axisTick: { show: false } },
    tooltip: {
      ...echartsBase().tooltip,
      formatter: (p) => {
        const f = features[features.length - 1 - p.dataIndex];
        return `<b>${f.label}</b> <span style="color:${MUTED}">#${f.index}</span><br>` +
          `<span style="font-family:monospace">${f.key}</span> = <b>${fmt(f.value, 4)}</b><br>` +
          `<span style="color:${INK2}">${f.definition}</span><br>` +
          `<span style="color:${MUTED}">range ${f.range} · default ${f.default} · ${f.group}</span>`;
      },
    },
    series: [{
      type: 'bar', data: vals.map((v, i) => ({ value: v, itemStyle: { color: colors[i], borderRadius: [0, 3, 3, 0] } })),
      barMaxWidth: 12,
      label: { show: true, position: 'right', color: INK2, fontSize: 9, formatter: (p) => (p.value ? fmt(p.value, 2) : '') },
    }],
  });
}

function groupColor(group) {
  return FEATURE_GROUP_COLORS[group] || FEATURE_GROUP_COLORS.other;
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
