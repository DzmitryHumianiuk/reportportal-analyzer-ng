# Lens 2 (Round 2) — Progressive disclosure & dataviz: Signature, Matching, Feedback

Scope: the remaining three stage cards of the Item Journey —
**Signature** (stage 1), **Matching trace** (stage 3), **Feedback** (stage 5).
Round 1 (Grouping + Decision) is approved and defines the design language this
document extends: L1 terse engineer-grade takeaway (deterministic template of payload
data, expert terms visible in parens/mono), L2 self-explaining visual with plain
labels, L4 `<details class="eng">` drawer holding every raw field, tokens-only CSS,
no-dummy-data rule. See `lens-disclosure.md` §1 for the shared ladder and the
`engDetails` / `hoverCard` primitives — both are reused here unchanged.

Grounding (all paths repo-relative):

- Rendering today: `inspector/static/js/views/journey.js` — `signatureCard()` (l.144),
  `fpChip()` (l.195), `matchingCard()` (l.250), `feedbackCard()` (l.407).
- Payload: `inspector/backend/payloads.py` — `item_journey` (l.173): `signature`
  block (l.206: `exception_fp, error_hash, top_frames, template_ids, exc_text,
  msg_text, frames_text, tmpl_text, signature_text, only_numbers, status_codes,
  urls, paths, emb_model_ver, has_emb`), `templates` block (l.223), `feedback` block
  (l.333: `event_id, old_label, new_label, old_group, new_group, source,
  suggestion_id, ts`, `LIMIT 50` newest-first), `_matching_decision` (l.375:
  `stage ∈ {A, AB, C, abstain, none}`, `stage_label`, `stage_note`,
  `matched_item_id`, `matched_mode_id`, `matched_mode{label, title, purity,
  support, seed_key}`).
- Reconstruction: `inspector/backend/reconstruct.py` — per-candidate fields
  `item_id, is_self, sparse_rank, dense_rank, lex_score, cosine, rrf_score,
  jaccard_templates, error_hash, exception_fp, issue_type, launch_id,
  launch_number, is_auto_analyzed, label_source, mode_id`; `query{salient_terms,
  exc_text, template_ids, emb_model_ver, dense_active}`; honest `note` string.
- Analyzer semantics: `analyzer-ng-plan/specs/03-pipeline.md` §3 (signature format,
  `exception_fp`, `error_hash`, edge case `exception_fp = 0` disables Stage A),
  §6.1 Stage A exact `error_hash` inherit (guards: unanimity/human source, age ≤ 180d,
  no `ti`/auto-`nd`), §6.2 Stage B KB modes (`score_mode` blend, confirmed
  short-circuit), §6.3 Stage C hybrid retrieval (FTS field-boosted + pgvector
  cosine, **RRF k=60**, top-20), §6.7 feedback (`label_event` append on
  `defect_update`).
- FTS weights (structural DDL constants): `analyzer-ng-plan/specs/02-database.md`
  l.274–278 — `signature_tsv = setweight(exc_text[:2000], 'A') ||
  setweight(msg_text[:8000], 'B') || setweight(frames_text[:4000], 'C') ||
  setweight(tmpl_text[:16000], 'D')`; l.824 — `ts_rank_cd('{0.1, 0.2, 0.4, 1.0}')`
  i.e. rank weights **A=1.0, B=0.4, C=0.2, D=0.1** (array is `{D,C,B,A}`).
- Primitives available (`util.js` / `app.css`): `idChip` (l.104), `defectBadge`,
  `defectBadgeAbbr`, `highlightPattern` (Drain mask highlighting, l.157),
  `shortTime`, `fmt`, `card`, `emptyState`, `.chip/.badge/.microbar/.field-badge/
  .pattern/.section-title/.table-wrap/table.data/.stepper` CSS. Round 1 adds
  `engDetails(key, …)`, `hoverCard(anchor, buildEl)`, `details.eng` CSS.

Hard rules carried forward: **no invented strings** — every sentence below is a
conditional template over real payload fields, a constant bound to a spec predicate,
or an honest status. **No new libraries.** **Tokens only** — no new hues; every mark
reuses the already-validated `--lbl-*`, `--accent`, `--warning`, `--good`, `--muted`,
surface and band tokens, so there is no palette to re-validate. Identity is never
color-alone (every colored mark carries text). L1 wording here is a working default;
if a Round-2 hierarchy/microcopy lens refines phrasing, it owns the final strings —
the *slots and predicates* defined here are the contract.

New `engDetails` keys introduced: `"signature"`, `"matching"`, `"feedback"`
(localStorage-persisted like `"grouping"`/`"decision"`).

---

## 1. Signature card (stage 1)

### 1.1 What is wrong today

- Two full signed-64 hashes (`exception_fp`, `error_hash`) are the card's first
  content (`fpChip` row) — the same AP-G1 anti-pattern Round 1 evicted from Grouping.
- Field rows are labeled with bare tsvector letters via colored `.field-badge`s
  (`EXC/MSG/FRAMES/TEMPLATES/CODES`) but nothing says these are **Postgres tsvector
  weight classes** or what the weights are — the entire point of field separation
  (exception text outranks template noise 10:1 at query time) is invisible.
- `.field-badge` hues reuse **label** colors (`.fb-exc { background: var(--lbl-pb) }`
  …) — the EXC badge is painted Product-Bug red, a false identity association.
  Color must follow the entity; these fields are not defect classes.
- The template list renders **all** referenced templates unconditionally — up to 30
  by spec §3.1, each a multi-line masked pattern block; on template-heavy items the
  card becomes a wall.
- `has_emb` / `emb_model_ver` chips are fine data but carry no consequence ("what
  breaks if not embedded?").

### 1.2 L1 takeaway (default template)

First line of the card body, `class="note"`, one sentence assembled left-to-right
from predicates (each clause renders only when its field exists):

- Base (signature present):
  `Indexed as {n_fields} weighted FTS fields (tsvector A–D) from {template_count}
  Drain3 templates` — `n_fields` = count of non-empty among
  `exc/msg/frames/tmpl` (+`status_codes` is *not* counted, see §1.3),
  `template_count = signature.template_ids.length`.
- Embedding clause: `has_emb → "; embedded (emb_model_ver {emb_model_ver})"`,
  `!has_emb → "; NOT embedded — dense retrieval leg inactive, Stage-C ranking is
  lexical-only"` (constant bound to `reconstruct.py` l.91–111 behavior — the same
  fact the reconstruction note already states).
- Stage-A capability clause: `exception_fp === "0" → "; exception_fp = 0 — exact-hash
  Stage A disabled for this item (spec §3.4)"`. Renders only in that state.
- `signature == null` → keep the existing empty state verbatim (already honest).

### 1.3 L2 — FTS field rows with weight chips (the A/B/C/D honesty fix)

Replace the current badge+text grid with one row per field. **A/B/C/D are Postgres
tsvector weight classes; show them as labeled data, not bare letters.** Weights and
index caps are structural constants of the deployed schema (a DDL generated column +
the versioned retrieval SQL — they cannot drift without a migration), so they live in
one exported const with a source comment, not scattered literals:

```js
// 02-database.md l.274-278 (setweight caps) + l.824 (ts_rank_cd weights {D,C,B,A})
const FTS_FIELDS = [
  { key: 'exc_text',    name: 'Exception chain', cls: 'A', w: 1.0, cap: 2000  },
  { key: 'msg_text',    name: 'Message',         cls: 'B', w: 0.4, cap: 8000  },
  { key: 'frames_text', name: 'Stack frames',    cls: 'C', w: 0.2, cap: 4000  },
  { key: 'tmpl_text',   name: 'Template ids',    cls: 'D', w: 0.1, cap: 16000 },
];
```

Row component `ftsFieldRow(def, value)`:

```html
<div class="fts-row">
  <div class="fts-head">
    <span class="fts-name">Exception chain</span>
    <span class="chip mono" title="tsvector weight class A · ts_rank_cd weight 1.0">A ×1.0</span>
    <span class="fts-wbar microbar" aria-hidden="true"><i style="width:100%"></i></span>
    <span class="fts-len muted mono">143 ch</span>          <!-- value.length -->
  </div>
  <div class="fts-val mono">java.net.SocketTimeoutException …</div>
</div>
```

- **Weight bar** = magnitude → length (one hue, `--accent`), width `w/1.0 · 100%`
  (100 / 40 / 20 / 10%). The chip text `A ×1.0` is the direct label; the bar is the
  at-a-glance comparison. Never a hue ramp — same value-to-length rule as Round 1
  microbars.
- **Truncation honesty**: when `value.length > cap`, append chip
  `indexed first {cap} ch` (the tsvector only sees `left(value, cap)` — real DDL
  behavior). Otherwise show plain `{len} ch`.
- Empty fields are **skipped** (today's behavior — a field absent from the signature
  is honestly absent, spec §3.1 omits empty lines).
- **CODES row** (`status_codes`, e.g. `HTTP_503`): rendered after the four FTS rows
  in the same shape but with chip `not FTS-weighted` (muted, title: "status codes
  ride signature_text for the embedding but are not in signature_tsv — 02 §DDL").
  This is a true and previously-hidden asymmetry; hiding it would misstate how
  retrieval works.
- **`.field-badge` neutralization** (CSS fix, global): the five `fb-*` label-color
  backgrounds become one neutral treatment — identity is carried by the text
  (`EXC`, `MSG`…), which this design replaces with full names anyway:

```css
.fts-row { padding: 8px 10px; background: var(--surface-2);
  border: 1px solid var(--hairline); border-radius: var(--radius-s); }
.fts-head { display: flex; align-items: center; gap: 8px; }
.fts-name { font-size: 12px; font-weight: 600; min-width: 110px; }
.fts-wbar { width: 72px; flex: none; }
.fts-len  { margin-left: auto; font-size: 10.5px; }
.fts-val  { margin-top: 5px; font-size: 12.5px; color: var(--ink-2); word-break: break-word; }
.field-badge, .fb-exc, .fb-msg, .fb-frames, .fb-templates, .fb-codes {
  background: var(--surface-3); color: var(--ink-2); }   /* de-label-colored */
```

### 1.4 Template list — kept, capped, expandable

Keep the existing masked-pattern blocks (`highlightPattern` — the Drain mask
highlighting is load-bearing evidence and stays). Change only the disclosure:

- Render the first **5** templates (payload order = signature order, spec §3.1
  ordered-unique). Section title stays honest about the total, reusing today's
  string: `Referenced Drain3 templates ({N})`.
- When `N > 5`, append a toggle styled as a chip-button:
  `show all {N}` ⇄ `show first 5` — plain `<button class="chip tmpl-more">`
  toggling `hidden` on the tail blocks (no re-render, no ECharts involved).
- `missing: true` rows keep the existing honest `template row missing` state and
  count against the cap like any row (they are not more important than present ones).
- Per-block metadata line unchanged (`{token_count} tok · {match_count} matches`).

### 1.5 Fingerprint chips → L4 drawer

The `fpChip` row (`exception_fp`, `error_hash`) **moves off the card face** into the
engineer drawer. The embedding status chip stays on the face (it carries a
consequence, §1.2 clause); the two hashes answer no glance-level question.

`engDetails('signature', …)` contains, verbatim and monospace:

- `exception_fp` = full signed value (kept signed — real stored value) with the
  spec pointer `xxh3_64(classes + "#" + frames), spec §3.2`.
- `error_hash` = full signed value, pointer `xxh3_64(exception_fp + "#" +
  template_hashes), spec §3.3`.
- `emb_model_ver`, `has_emb`, `only_numbers` (kv rows).
- `top_frames[]` (one mono line each), `template_ids[]` raw, `urls[]`, `paths[]`
  (rendered only when non-empty; empty arrays are omitted, not shown as `[]`).
- **`signature_text` verbatim** in `<pre class="pattern">` — the exact embeddable
  document (TEST/EXC/MSG/FRAMES/TEMPLATES/CODES lines, spec §3.1). This is the one
  raw artifact the card never showed anywhere; it is the ground truth both retrieval
  legs consume and belongs in L4.

---

## 2. Matching card (stage 3) — the funnel

### 2.1 Naming fix first (copy/backend note)

The journey labels the hybrid stage **C** (`stage_label: "Stage C — hybrid retrieval
+ GBM"`, spec 03 §6.3), but `reconstruct.py`'s note string and docstring say
"Stage-B hybrid" (after 02-database §5.1's internal SQL name `STAGE_B_HYBRID_SQL`).
Two names for one mechanism on one card is a defect. Fix: the UI standardizes on the
pipeline taxonomy **A = exact hash, B = KB modes, C = hybrid** everywhere; the
backend `note` string in `reconstruct.py` l.104 changes "Stage-B" → "Stage-C
(internal SQL: STAGE_B_HYBRID_SQL)". The section title "Live Stage-B reconstruction"
in `matchingCard` becomes "Live Stage-C reconstruction". Internal constant names are
untouched.

### 2.2 L2 centerpiece — the three-stage funnel

Replace the single stage badge + note with a horizontal funnel of three nodes plus a
terminal. Everything is derived from `matching.stage` (a real field) and the spec's
stage ordering (A runs first, then B, then C — 03 §6.1–6.3):

| `matching.stage` | node A (exact hash) | node B (KB modes) | node C (hybrid + GBM) | terminal |
|---|---|---|---|---|
| `A` | **won** | skip | skip | decided at A |
| `AB` | fell | **won** | skip | decided at B |
| `C` | fell | fell | **won** | decided at C |
| `abstain` | fell | fell | fell | **abstain** node shown |
| `none` | — funnel not rendered; keep the existing "No suggestion recorded" state |

Node states (text always carries the state — never color-alone):

- **won** — full-strength: border + inset bar in the existing stage color map
  (`A: --lbl-nd, AB: --lbl-si, C: --accent` — unchanged tokens), caption
  `✓ decided here`, and the winning artifact inline:
  - A: `idChip("item " + matched_item_id, matched_item_url)` + the matched item's
    label via `defectBadge` when `matched_mode`-independent data allows (the
    inherit source; `stage_note` already names it).
  - B: `defectBadge(matched_mode.label)` + chip `{matched_mode.title || "mode " +
    matched_mode_id}` + chips `purity {fmt(purity,2)}` / `support {support}` /
    `seed:{seed_key}` (existing row, relocated into the node).
  - C: chip `top-1 of {reconstruction.candidates.length} candidates → GBM`
    (counts are real; the GBM confidence lives on the Decision card — do not
    duplicate it here).
- **fell** — normal surface, muted caption `passed — no decision`. The payload does
  not say *why* a stage declined (guards vs. no match), so the caption must not
  guess. Two **derivable** annotations may add honest detail:
  - node A, when `signature.exception_fp === "0"`: caption becomes
    `disabled — exception_fp = 0 (spec §3.4)` (a stronger true statement).
  - node A, when any non-self reconstruction candidate has
    `error_hash === signature.error_hash`: chip `same-hash history exists` (title:
    "an identical error_hash is in labeled history but Stage-A guards did not
    inherit — see spec §6.1 guards"). Purely client-side set membership on real
    fields.
- **skip** — dashed border, opacity .55, caption `not reached`.
- **abstain terminal** — `--lbl-ti` treatment (existing map), caption from the real
  `stage_note` ("No stage produced a confident match; item left as 'ti'.").

Markup/CSS (flex rail; nodes are plain divs, arrows reuse the stepper's glyph):

```html
<div class="funnel" role="list">
  <div class="fn-node" role="listitem" data-state="fell">
    <div class="fn-k">A · exact hash</div>
    <div class="fn-cap muted">passed — no decision</div>
  </div>
  <span class="fn-arrow" aria-hidden="true">→</span>
  <div class="fn-node" data-state="won" style="--stage-c: var(--lbl-si)">
    <div class="fn-k">B · KB modes</div>
    <div class="fn-cap">✓ decided here</div>
    <div class="fn-art"><!-- badges/chips per above --></div>
  </div>
  <span class="fn-arrow">→</span>
  <div class="fn-node" data-state="skip">…</div>
</div>
```

```css
.funnel { display: flex; align-items: stretch; gap: 8px; flex-wrap: wrap; }
.fn-node { flex: 1 1 160px; min-width: 150px; padding: 8px 10px;
  background: var(--surface-2); border: 1px solid var(--hairline);
  border-radius: var(--radius-s); }
.fn-node[data-state="won"] { border-color: var(--stage-c);
  box-shadow: inset 3px 0 0 var(--stage-c); }
.fn-node[data-state="won"] .fn-cap { color: var(--stage-c); font-weight: 600; }
.fn-node[data-state="skip"] { border-style: dashed; opacity: .55; }
.fn-k { font-size: 11px; text-transform: uppercase; letter-spacing: .5px;
  color: var(--ink-2); font-weight: 600; }
.fn-cap { font-size: 11.5px; margin-top: 3px; }
.fn-art { margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }
.fn-arrow { align-self: center; color: var(--muted); }
@media (max-width: 720px) { .funnel { flex-direction: column; }
  .fn-arrow { transform: rotate(90deg); align-self: flex-start; margin-left: 12px; } }
```

L1 above the funnel: keep `stage_label` as the bold lead (it is already the correct
terse string) + `stage_note` as the sentence — both real backend fields, unchanged.
The old standalone stage badge row is absorbed by the funnel (the won node *is* the
badge now); `matched_item_id` / `matched_mode` chips move into their node.

### 2.3 Top-3 evidence strip (L2, under the funnel)

Rendered whenever `reconstruction.candidates` is non-empty (independent of winning
stage — Stage-A/B winners still show what hybrid retrieval sees; the strip heading
makes the relationship honest). Heading (`.section-title`):
`Closest labeled history — top 3 of {N} retrieved (live)`.

`cands = reconstruction.candidates.filter(c => !c.is_self).slice(0, 3)` (SQL already
orders by fused score).

Per candidate, one `.cand-card` in a 3-up grid:

```html
<div class="cand-strip">
  <div class="cand-card">
    <div class="cand-head">
      <span class="cand-rank mono">#1</span>
      <!-- idChip(cd.item_id, cd.ui_url, 'chip mono') -->
      <!-- defectBadge(cd.issue_type, grp(cd.issue_type)) -->
    </div>
    <div class="cand-cos">
      <span class="cc-k">cos</span>
      <span class="microbar cc-bar"><i style="width:87%"></i></span>
      <span class="mono cc-v">0.874</span>
    </div>
    <div class="cand-chips">
      <span class="chip mono" title="candidate exception_fp equals this item's">fp =</span>
      <span class="chip mono" title="candidate error_hash equals this item's">hash =</span>
      <span class="chip mono" title="template-set Jaccard vs this item">jac 0.62</span>
      <span class="chip" title="label_source: defect_update">src human · RP</span>
      <span class="chip mono">launch #614</span>
    </div>
  </div>
  …
</div>
```

Component rules:

- **Cosine bar**: absolute scale 0→1 (`width: cosine*100%`), fill `--accent`
  (value → length, one hue), exact value 3 dp beside it — direct label, text in ink
  tokens. `cosine == null` (no dense leg / candidate un-embedded) → no fill, value
  `—`, title `no dense score — see reconstruction note` (honest gap, never a 0-width
  lie labeled 0).
- **Equality chips** appear only when true (absence = not equal; the drawer table
  carries the raw hashes for verification):
  - `fp =` iff `cd.exception_fp != null && cd.exception_fp ===
    d.signature.exception_fp` (title: `same exception_fp {value}`).
  - `hash =` iff `cd.error_hash === d.signature.error_hash` (title:
    `same error_hash — Stage-A grade match`).
  - `tch =` iff candidate `test_case_hash` is present in the payload **and** equals
    `d.item.test_case_hash` (same physical test case). `test_case_hash` is not in
    the reconstruction rows today — additive data-contract item §4; until then the
    chip is omitted, never derived from anything else.
- **`jac {fmt(jaccard_templates, 2)}`** always shown (real column, 0 is a real
  value here — the SQL computes it — so 0.00 renders as 0.00).
- **Provenance chip `src`** from `label_source` (real per-candidate field, the
  newest `label_event.source` for that item): map
  `defect_update → "human · RP"`, `human_confirm → "human · confirm"`,
  `ai_suggested → "analyzer"`, unknown token → the raw token verbatim (mono),
  `null → "src —"` with title `label source not recorded`. Raw token always in
  `title=`. Same map as §3.2 — one shared `SOURCE_LABELS` const.
- **`mode {mode_id}`** chip when `mode_id != null`; `launch #{launch_number}` chip
  (real fields); label badge is the existing `defectBadge` (identity by the
  validated label tokens + text).

```css
.cand-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px; }
@media (max-width: 860px) { .cand-strip { grid-template-columns: 1fr; } }
.cand-card { padding: 9px 11px; background: var(--surface-2);
  border: 1px solid var(--hairline); border-radius: var(--radius-s); }
.cand-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cand-rank { color: var(--muted); font-size: 11px; }
.cand-cos { display: flex; align-items: center; gap: 8px; margin-top: 7px; }
.cc-k { font-size: 10px; text-transform: uppercase; letter-spacing: .5px;
  color: var(--muted); }
.cc-bar { flex: 1; }
.cc-v { font-size: 11px; color: var(--ink-2); }
.cand-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 7px; }
```

### 2.4 Full reconstruction → expander with the RRF explainer

The full candidates table (all rows incl. `is_self`, columns exactly as today:
item / label / lex rank / dense rank / cosine / jaccard / RRF fused with the
normalized microbar) moves behind `engDetails('matching-recon', …)` with summary:

`Live Stage-C reconstruction — {N} candidates · HYBRID_RETRIEVAL v{hybrid_retrieval_version}`

Inside, above the table, two lines:

1. **RRF explainer** (constant, bound to spec §6.3 / 02 §5.1):
   `RRF fuses the two rank lists — lexical (weighted tsvector) and dense (cosine):
   each candidate scores Σ 1/(k + rank) over the lists it appears in, k = 60. The
   large k damps rank-1 dominance so one leg cannot outvote the other.`
2. `r.note` verbatim (the existing honest re-execution note, incl. the
   lexical-only warning when the dense leg is inactive) — unchanged behavior,
   relocated.

Table changes: add one `src` column (the §2.3 provenance map, raw token in
`title=`) so the table remains the strict superset of the strip; keep `is-self`
row highlight. The empty-candidates state keeps the existing `emptyState` verbatim,
rendered on the card face (not inside the drawer — an empty funnel input is
glance-relevant).

The table is the WCAG table-twin of the strip: every strip value (cosine, jaccard,
rank, label, src) is reachable as text in it.

### 2.5 L4 — Details for engineers (Matching)

`engDetails('matching', …)` (separate from the reconstruction drawer — different
audiences open them independently):

- raw `stage` token, `matched_item_id`, `matched_mode_id`, full `matched_mode`
  kv (label, title, purity raw float, support, seed_key).
- the reconstruction **query block**, verbatim: `salient_terms` (mono, wrapped),
  `template_ids[]`, `emb_model_ver`, `dense_active` — this is exactly what was
  bound into the versioned SQL, previously invisible.

---

## 3. Feedback card (stage 5)

### 3.1 L1 takeaway (default template, newest event = current state)

`feedback` arrives newest-first (payload `ORDER BY ts DESC`). Variants, first match
wins:

| # | Predicate | Template |
|---|---|---|
| F0 | `feedback.length === 0` | existing empty state verbatim |
| F1 | `length >= 1` | `Current label {NEW_LABEL} — set by {src_plain(latest.source)} {relTime(latest.ts)} · {length} label event(s).` |
| F1+ | F1 and `length >= 2 && latest.source === 'defect_update' && feedback[1].source === 'ai_suggested' && latest.old_group !== latest.new_group` | append ` Overrides the analyzer's {OLD_LABEL}.` |

`{NEW_LABEL}`/`{OLD_LABEL}` resolve through the RP defects mapping (`defectName`,
already loaded via `setDefects`) — real display names, honest fallback to the raw
locator. The F1+ clause is a pure predicate over two adjacent real events; it is the
single most useful sentence this card can say (human corrected the machine) and it
renders only when literally true.

### 3.2 Timeline (L2) — spine, relative time, provenance

Keep today's one-row-per-event structure (old badge → new badge), upgraded:

```html
<ol class="tl" reversed>
  <li class="tl-ev">
    <i class="tl-dot" style="--c: var(--lbl-pb)" aria-hidden="true"></i>
    <div class="tl-main">
      <span class="muted">(new)</span> <!-- or defectBadge(old) -->
      <span class="muted">→</span>
      <!-- defectBadge(e.new_label, e.new_group) -->
      <span class="chip" title="source: defect_update">human · RP</span>
      <span class="chip mono" title="suggestion_id">sug 512 ↑ stage 4</span>
    </div>
    <time class="muted mono" title="2026-07-14T09:31:02Z">3d ago</time>
  </li>
  …
</ol>
```

- **Order**: newest first (matches payload and the F1 takeaway; the `<ol reversed>`
  keeps semantic numbering honest).
- **Spine**: `.tl` draws a 1px `--hairline` vertical line; each event's `tl-dot`
  takes the **new** label-group token (`--lbl-{new_group}`) — identity is also in
  the adjacent `defectBadge` text, so the dot is redundant encoding, not
  color-alone.
- **Relative time**: new util `relTime(iso)` — `< 60s → "just now"`, `< 60m →
  "{m}m ago"`, `< 24h → "{h}h ago"`, `< 30d → "{d}d ago"`, else `shortTime(iso)`.
  The **full ISO always rides `title=`** (and lives in the L4 table) — relative
  time is a convenience layer, never the only record.
- **Provenance chips** — shared `SOURCE_LABELS` const (same map as §2.3):
  `defect_update → "human · RP"` (defect changed in the RP UI — spec §6.7),
  `human_confirm → "human · confirm"`, `ai_suggested → "analyzer"`; unknown token →
  raw token mono (honest fallback). Raw token always in `title=`. Neutral chip
  styling — provenance is identity-by-text, not a status; no reserved status colors.
- **Every id linked**: the event's `suggestion_id` renders as a chip when non-null.
  When it equals `d.decision.suggestion_id` (the suggestion shown on stage 4) the
  chip is a button `sug {id} ↑ stage 4` that scrolls to the Decision card (same
  `scrollIntoView` + 900ms pulse as the stepper `focus()`, motion suppressed under
  `prefers-reduced-motion`); otherwise a plain mono chip `sug {id}` (an older
  suggestion — nothing on-page to link to; the id itself is the record). Item ids
  are not repeated per event — all events belong to this item, whose `idChip` is in
  the page header.

```css
.tl { list-style: none; margin: 0; padding: 0; position: relative; }
.tl::before { content: ""; position: absolute; left: 5px; top: 8px; bottom: 8px;
  width: 1px; background: var(--hairline); }
.tl-ev { position: relative; display: flex; align-items: center; gap: 10px;
  padding: 7px 0 7px 20px; }
.tl-dot { position: absolute; left: 0; width: 11px; height: 11px;
  border-radius: 50%; background: var(--c, var(--lbl-none));
  box-shadow: 0 0 0 2px var(--surface-1); }
.tl-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex: 1; }
.tl-ev time { font-size: 11px; flex: none; }
```

- **Cap honesty**: the query is `LIMIT 50`. When `feedback.length === 50`, render a
  muted line under the list: `showing the latest 50 events (query cap)`. Exact
  totals need the additive `feedback_total` field (§4); until then the cap line is
  the honest statement (it is true whenever 50 rows return, even in the boundary
  case of exactly 50 existing).

### 3.3 L4 — Details for engineers (Feedback)

`engDetails('feedback', …)`: one `table.data` in `.table-wrap` — columns
`event_id · old_label · new_label · source (raw token) · suggestion_id · ts (ISO)`,
all verbatim. This is the table-twin of the timeline; nothing on the face is absent
here.

---

## 4. Build inventory

**JS (existing files, no new libs):**

| Function | File | Replaces / new |
|---|---|---|
| `FTS_FIELDS` const + `ftsFieldRow(def, value)` | `journey.js` | replaces the field-badge grid |
| `templateList(templates)` (cap 5 + toggle) | `journey.js` | wraps the existing block renderer |
| `funnel(matching, signature, reconstruction)` | `journey.js` | replaces the stage badge row |
| `candStrip(reconstruction, signature, item)` | `journey.js` | new |
| `rrfDrawer(reconstruction)` (`engDetails('matching-recon', …)`) | `journey.js` | wraps the existing table builder |
| `SOURCE_LABELS` const + `srcChip(token)` | `util.js` | new shared (used §2.3, §2.4, §3.2) |
| `relTime(iso)` | `util.js` | new shared |
| `timeline(feedback, decision)` + `feedbackGlance(feedback)` | `journey.js` | replaces the flat list |
| `engDetails` keys `signature` / `matching` / `feedback` | — | reuse Round-1 primitive |

**CSS additions (`app.css`, tokens only):** `.fts-row` family, `.field-badge`
neutralization, `.tmpl-more` (chip-button), `.funnel`/`.fn-*`, `.cand-strip`/
`.cand-*`/`.cc-*`, `.tl` family. No new tokens; no new hues → nothing to
re-validate against the palette checks.

**Backend (additive, UI degrades gracefully without each):**

1. `reconstruct.py` / `STAGE_B_HYBRID_SQL` projection: add candidate
   `test_case_hash` → enables the `tch =` chip (§2.3). Until then the chip is
   omitted.
2. `reconstruct.py` l.104 note string: "Stage-B" → "Stage-C (internal SQL:
   STAGE_B_HYBRID_SQL)" (§2.1). Pure copy fix.
3. `payloads.py` feedback block: add `feedback_total` (un-capped `count(*)`) →
   upgrades the §3.2 cap line to `showing 50 of {feedback_total}`.

**Explicitly unchanged:** `highlightPattern` mask rendering, the candidates-table
columns and `is-self` treatment (plus one additive `src` column), all `emptyState`
strings, `idChip`/`defectBadge` primitives, the stepper.

## 5. Verification pass (per the dataviz method — render and look)

Render one item per state and eyeball for collisions/overflow before calling it
done: signature with all 5 field rows vs. minimal (`MSG` only) vs. `exception_fp=0`
vs. un-embedded; ≥ 6 templates (cap + toggle) and a `missing` template row; each
funnel outcome (`A`, `AB`, `C`, `abstain`, `none`) at 1440px and 720px (arrow
rotation); strip with `cosine=null`, all three equality chips true, and `N<3`
candidates; feedback with 0 / 1 / 50 events, an F1+ override case, and an unknown
`source` token; both `eng` drawers open simultaneously on the Matching card. Confirm
every drawer is the strict superset of its card face.
