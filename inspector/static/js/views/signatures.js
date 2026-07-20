// Signatures Explorer — the fingerprint space, one row per distinct error_hash.
// Exposes Stage-A hash collisions / label bleed: rows whose members carry >1
// distinct non-ti ground-truth label are flagged as label-bleed candidates. Row
// click fetches the signature detail (representative exc/msg/frames/templates +
// member items) on demand — no N+1, the list stays a single aggregate query.
import { api } from '../api.js';
import { updateHash } from '../app.js';
import {
  h, icon, clear, card, defectBadge, defectBadgeAbbr, idChip, setDefects, shortTime, fmt,
  highlightPattern, emptyState, loading,
} from '../util.js';

const sstate = { q: '', conflicts: false, offset: 0, expanded: null };

// Seed search / conflicts-toggle / expanded error_hash from a permalink.
export function setSignaturesState({ q, conflicts, expanded }) {
  sstate.q = q || '';
  sstate.conflicts = !!conflicts;
  sstate.expanded = expanded || null;
  sstate.offset = 0;
}

export async function renderSignatures(root, app) {
  clear(root);

  // --- controls: search + conflicts-only toggle ---
  const search = h('input', {
    class: 'select', type: 'search', placeholder: 'Search exc / msg / frames (ILIKE)…',
    value: sstate.q, style: { minWidth: '300px' },
    oninput: debounce((e) => { sstate.q = e.target.value; sstate.offset = 0; sstate.expanded = null; updateHash({ q: sstate.q, hash: null }); load(); }, 250),
  });
  const conflictToggle = h('label', { class: 'toggle', title: 'Only error_hashes whose members carry >1 distinct non-ti label' },
    h('input', { type: 'checkbox', checked: sstate.conflicts,
      onchange: (e) => { sstate.conflicts = e.target.checked; sstate.offset = 0; updateHash({ conflicts: sstate.conflicts }); load(); } }),
    h('span', { class: 'toggle-track' }, h('span', { class: 'toggle-thumb' })),
    h('span', { class: 'toggle-label' }, '⚠ only conflicts'));
  root.appendChild(h('div', { class: 'picker-row' },
    h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Search signatures'), search),
    h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Filter'), conflictToggle)));

  // --- summary chips ---
  const summaryRow = h('div', { class: 'flex gap-8 wrap mb-8', id: 'sig-summary' });
  root.appendChild(summaryRow);

  const tblCard = card('Fingerprint space', { sub: 'one row per distinct error_hash' });
  root.appendChild(tblCard);
  const tblBody = tblCard.querySelector('.card-body');

  async function load() {
    clear(tblBody).appendChild(loading());
    const d = await api.signatures(app.project, sstate.q, sstate.conflicts, sstate.offset);
    if (d.rp) setDefects(d.rp.defects);
    renderSummary(summaryRow, d.summary);
    tblCard.querySelector('.card-sub').textContent =
      `${d.count} error_hash${d.count === 1 ? '' : 'es'}` +
      (sstate.conflicts ? ' · conflicts only' : '') +
      (sstate.q ? ` · matching “${sstate.q}”` : '');
    clear(tblBody);
    if (!d.rows.length) {
      tblBody.appendChild(emptyState('🧬', 'No signatures',
        sstate.conflicts
          ? 'No error_hash in this project has members carrying more than one distinct non-ti label — no hash-collision label bleed detected.'
          : (sstate.q
            ? 'No signature matches your search across exc / msg / frames text.'
            : 'This project has no failure_signature rows yet — signatures are built as error logs are indexed.'),
        'analyzer.failure_signature'));
      return;
    }
    renderTable(tblBody, d, app);
  }
  await load();
}

function renderSummary(el, s) {
  clear(el);
  const chip = (n, k, cls) => h('div', { class: `counter ${cls || ''}` },
    h('span', { class: 'n' }, fmt(n)), h('span', { class: 'k' }, k));
  el.append(
    chip(s.distinct_error_hash, 'error_hash', 'accent'),
    chip(s.distinct_exception_fp, 'exception_fp'),
    chip(s.items_with_signatures, 'signed items'),
    chip(s.embedded, 'embedded', 'good'),
    chip(s.conflict_hashes, 'conflicts', s.conflict_hashes ? 'warn' : ''),
  );
}

function renderTable(el, d, app) {
  const tbl = h('table', { class: 'data' },
    h('thead', {}, h('tr', {},
      ...['error_hash', 'members', 'labels', 'exception class', 'status codes', 'first / last seen'].map((t) => h('th', {}, t)))),
    h('tbody', {}));
  const tbody = tbl.querySelector('tbody');

  for (const r of d.rows) {
    const short = String(r.error_hash).length > 12
      ? String(r.error_hash).slice(0, 6) + '…' + String(r.error_hash).slice(-4)
      : r.error_hash;
    const labelChips = h('div', { class: 'flex gap-8 wrap' },
      ...r.labels.map((l) => h('span', { class: 'flex center', style: { gap: '3px' } },
        defectBadgeAbbr(l.locator, l.group),
        h('span', { class: 'muted mono', style: { fontSize: '10.5px' } }, `×${l.count}`))));

    const row = h('tr', { class: 'sig-row' + (r.is_conflict ? ' conflict' : ''), style: { cursor: 'pointer' },
      onclick: () => toggle(r, row) },
      h('td', {},
        h('span', { class: 'flex center gap-8' },
          r.is_conflict ? h('span', { class: 'conflict-flag', title: 'label-bleed candidate: members carry >1 distinct non-ti label' }, '⚠') : null,
          h('span', { class: 'mono', title: r.error_hash }, short))),
      h('td', { class: 'num' }, fmt(r.member_count)),
      h('td', {}, labelChips),
      h('td', {}, r.exc_classes.length
        ? h('span', { class: 'mono', style: { fontSize: '11.5px' } }, r.exc_classes.join(', '))
        : h('span', { class: 'muted' }, '—')),
      h('td', {}, r.status_codes.length
        ? h('span', { class: 'flex gap-8 wrap' }, ...r.status_codes.map((c) => h('span', { class: 'chip mono' }, c)))
        : h('span', { class: 'muted' }, '—')),
      h('td', { class: 'muted mono', style: { fontSize: '11px' } },
        `${shortTime(r.first_seen)} → ${shortTime(r.last_seen)}`));
    tbody.appendChild(row);
    // Restore a permalinked expansion once its row is in the DOM.
    if (sstate.expanded != null && String(r.error_hash) === String(sstate.expanded)) {
      Promise.resolve().then(() => toggle(r, row));
    }
  }
  el.appendChild(h('div', { class: 'table-wrap' }, tbl));

  // pager
  const canPrev = d.offset > 0;
  const canNext = d.count >= d.limit;
  if (canPrev || canNext) {
    el.appendChild(h('div', { class: 'flex between center mt-16' },
      h('span', { class: 'muted', style: { fontSize: '12px' } },
        `rows ${d.offset + 1}–${d.offset + d.count}`),
      h('div', { class: 'flex gap-8' },
        h('button', { class: 'chip', disabled: !canPrev ? '' : null,
          style: { cursor: canPrev ? 'pointer' : 'default', opacity: canPrev ? 1 : 0.4 },
          onclick: () => { if (canPrev) { sstate.offset = Math.max(0, d.offset - d.limit); reload(app); } } }, '← prev'),
        h('button', { class: 'chip', disabled: !canNext ? '' : null,
          style: { cursor: canNext ? 'pointer' : 'default', opacity: canNext ? 1 : 0.4 },
          onclick: () => { if (canNext) { sstate.offset = d.offset + d.limit; reload(app); } } }, 'next →'))));
  }

  async function toggle(r, row) {
    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains('sig-detail')) {
      existing.remove();
      row.classList.remove('open');
      sstate.expanded = null;
      updateHash({ hash: null });
      return;
    }
    // collapse any other open detail
    [...tbody.querySelectorAll('tr.sig-detail')].forEach((n) => n.remove());
    [...tbody.querySelectorAll('tr.sig-row.open')].forEach((n) => n.classList.remove('open'));
    row.classList.add('open');
    sstate.expanded = r.error_hash;
    updateHash({ hash: String(r.error_hash) });
    const detailRow = h('tr', { class: 'sig-detail' }, h('td', { colspan: 6 }, loading('Loading signature…')));
    row.after(detailRow);
    try {
      const detail = await api.signatureHash(app.project, r.error_hash);
      const cell = detailRow.firstChild;
      clear(cell);
      renderDetail(cell, detail);
    } catch (e) {
      const cell = detailRow.firstChild;
      clear(cell).appendChild(emptyState('🚫', 'Detail failed', String(e.message || e)));
    }
  }
}

function reload(app) {
  // re-run the current view (offset changed)
  const root = document.getElementById('view');
  renderSignatures(root, app);
}

function renderDetail(el, d) {
  if (d.rp) setDefects(d.rp.defects);
  const wrap = h('div', { class: 'sig-detail-body' });

  // header: full hash + representative + conflict banner
  wrap.appendChild(h('div', { class: 'flex between center wrap mb-8' },
    h('div', { class: 'flex center gap-8 wrap' },
      h('span', { class: 'chip mono-strong', title: 'error_hash' }, d.error_hash),
      h('span', { class: 'chip mono', title: 'exception_fp' }, `fp ${d.exception_fp}`),
      idChip(`representative item ${d.representative_item_id}`,
        (d.members.find((m) => m.item_id === d.representative_item_id) || {}).ui_url, 'chip mono')),
    d.is_conflict
      ? h('span', { class: 'badge', style: { background: 'color-mix(in srgb, var(--warning) 16%, transparent)', color: 'var(--warning)', borderColor: 'var(--warning)' } },
        '⚠ label-bleed candidate')
      : null));

  if (d.is_conflict) {
    wrap.appendChild(h('p', { class: 'note recon', style: { marginBottom: '12px' } },
      `This error_hash carries ${d.labels.filter((l) => l.group !== 'ti' && l.group !== 'none').length} distinct non-ti labels across its ` +
      `${d.member_total} member${d.member_total === 1 ? '' : 's'}. Stage A inherits labels via exact error_hash equality, so members here risk inheriting the wrong ground-truth label.`));
  }

  // signature fields (reuse journey field-badge look)
  const s = d.signature;
  const fields = [
    ['EXC', 'fb-exc', s.exc_text],
    ['MSG', 'fb-msg', s.msg_text],
    ['FRAMES', 'fb-frames', (s.top_frames || []).join('  ›  ') || s.frames_text],
    ['CODES', 'fb-codes', (s.status_codes || []).join(', ')],
  ];
  const grid = h('div', { class: 'grid', style: { gap: '8px' } });
  for (const [name, cls, val] of fields) {
    if (!val) continue;
    grid.appendChild(h('div', { class: 'flex gap-8', style: { alignItems: 'baseline' } },
      h('span', { class: `field-badge ${cls}` }, name),
      h('span', { class: 'mono', style: { fontSize: '12.5px', color: 'var(--ink-2)', wordBreak: 'break-word' } }, val)));
  }
  wrap.appendChild(grid);

  // templates with highlightPattern
  if (d.templates && d.templates.length) {
    wrap.appendChild(h('div', { class: 'section-title', style: { marginTop: '16px' } },
      `Referenced Drain3 templates (${d.templates.length})`));
    for (const t of d.templates) {
      wrap.appendChild(h('div', { style: { padding: '8px 10px', background: 'var(--surface-2)', border: '1px solid var(--hairline)', borderRadius: '8px', marginBottom: '6px' } },
        h('div', { class: 'flex between center mb-8' },
          h('span', { class: 'chip mono' }, `#${t.template_id}`),
          t.missing ? h('span', { class: 'muted' }, 'template row missing') :
            h('span', { class: 'muted', style: { fontSize: '11px' } }, `${t.token_count} tok · ${fmt(t.match_count)} matches`)),
        h('div', { class: 'pattern', html: highlightPattern(t.pattern) })));
    }
  }

  // member items
  wrap.appendChild(h('div', { class: 'section-title', style: { marginTop: '16px' } },
    `Member items (${d.member_shown}${d.member_total > d.member_shown ? ` of ${d.member_total}` : ''})`));
  const list = h('div', { class: 'grid', style: { gap: '6px' } });
  for (const m of d.members) {
    list.appendChild(h('div', { class: 'flex between center wrap' + (m.is_auto_analyzed ? ' halo-auto' : ''),
      style: { padding: '8px 11px', background: 'var(--surface-2)', border: '1px solid var(--hairline)', borderRadius: '8px', gap: '10px' } },
      h('div', { class: 'flex center gap-8 wrap', style: { minWidth: 0 } },
        idChip(`item ${m.item_id}`, m.ui_url, 'chip mono'),
        defectBadgeAbbr(m.issue_type, m.label_group),
        h('span', { style: { fontSize: '12.5px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '360px' } }, m.item_name || '—'),
        m.is_auto_analyzed ? h('span', { class: 'badge', style: { background: 'var(--accent-soft)', color: 'var(--accent)' } }, icon('bolt', { size: 12 }), ' auto') : null),
      h('div', { class: 'flex center gap-8' },
        idChip(m.launch_name || `launch ${m.launch_id}`, m.launch_url, 'chip'),
        h('span', { class: 'muted mono', style: { fontSize: '11px' } }, shortTime(m.indexed_at)))));
  }
  wrap.appendChild(list);
  if (d.member_total > d.member_shown) {
    wrap.appendChild(h('p', { class: 'note', style: { marginTop: '8px' } },
      `${d.member_total - d.member_shown} more member${d.member_total - d.member_shown === 1 ? '' : 's'} not shown (capped at ${d.member_shown}).`));
  }

  el.appendChild(wrap);
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
