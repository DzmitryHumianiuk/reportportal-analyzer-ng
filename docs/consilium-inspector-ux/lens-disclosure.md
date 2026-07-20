# Lens 2 — Progressive disclosure & dataviz mechanics

Scope: the **Grouping** card and the **Features & decision** card of the Item Journey view.
This lens specifies *disclosure layers* (what a QA engineer sees at a glance vs. what an ML
engineer expands) and the *exact visual mechanics* of the three data displays: the banded
confidence gauge, the grouped feature waterfall, and the group-member dot strip.

Grounding (all paths repo-relative):

- Rendering today: `inspector/static/js/views/journey.js` — `groupingCard()` (l.202),
  `siDial()` (l.223), `decisionCard()` (l.308), `drawGauge()` (l.355), `drawFeatureBars()` (l.374).
- Tokens: `inspector/static/css/app.css` — `--band-auto/--band-suggest/--band-abstain`,
  `--lbl-*`, `--warning`, surfaces, `.chip/.badge/.kv/.microbar/.section-title` primitives.
- Feature schema: `inspector/backend/features_meta.py` — **39 features** in 5 real groups
  (`retrieval` 13, `history` 8, `kb` 9, `grouping` 4, `signal` 5), each with key, human
  label, definition, range, default. `FEATURE_GROUP_COLORS` in `util.js` already maps
  these groups to tokens.
- Payload: `inspector/backend/payloads.py` — `grouping_block` (l.276), `_matching_decision`
  (l.375; `band`, `tau_suggest=0.45`, `tau_auto=0.75`, per-feature rows with metadata).

Hard rule respected throughout: **no invented strings**. Every visible sentence below is a
conditional template over real payload fields, a documented definition copied from
`features_meta.py`, or an honest status. Constraints: existing dark design system, ECharts
already loaded, **no new libraries**. Native `<details>/<summary>` carries all expand/collapse
(keyboard- and screen-reader-free-of-charge).

---

## 1. The disclosure ladder (shared model for both cards)

Every card has exactly four layers. A QA engineer should never *need* layer 3–4; an ML
engineer loses nothing because layers 3–4 contain every raw field currently shown.

| Layer | Who | Mechanic | Content rule |
|---|---|---|---|
| L1 Glance | everyone | always visible, first line of card body | one derived sentence + verdict badges; no raw hashes, no feature keys |
| L2 Visual | everyone | always visible | the banded gauge / dot strip / grouped bars; direct-labeled, self-explaining |
| L3 Hover | curious | tooltip on any mark | exact value + definition + range (already the pattern in `drawFeatureBars` tooltip) |
| L4 Engineer | ML eng | `<details class="eng">` "Details for engineers" | every raw field the card shows today (group_id, fingerprint, feature keys, model_ver…), verbatim |

### 1.1 The `eng-details` component

One shared builder in `util.js`:

```js
// engDetails(key, ...children) -> <details class="eng" data-key>
//   <summary>Details for engineers <span class="eng-caret">▸</span></summary>
//   <div class="eng-body">…children…</div>
// open state persisted per card: localStorage `inspector.eng.<key>` = "1"|"0"
export function engDetails(key, ...children)
```

- `key` values used here: `"grouping"`, `"decision"`. Persist on `toggle` event; default
  **closed**. Persistence means an ML engineer opens it once and it stays open across
  items/launches — the two audiences each get a stable default without a settings UI.
- CSS (new, tokens only):

```css
details.eng { margin-top: 14px; border-top: 1px solid var(--hairline); padding-top: 10px; }
details.eng > summary {
  cursor: pointer; list-style: none; display: inline-flex; align-items: center; gap: 6px;
  font-size: 11.5px; text-transform: uppercase; letter-spacing: .6px; color: var(--muted);
}
details.eng > summary::-webkit-details-marker { display: none; }
details.eng > summary:hover { color: var(--ink-2); }
details.eng > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
details.eng .eng-caret { transition: transform .15s; }
details.eng[open] .eng-caret { transform: rotate(90deg); }
details.eng .eng-body { margin-top: 10px; }
@media (prefers-reduced-motion: reduce) { details.eng .eng-caret { transition: none; } }
```

### 1.2 Tooltip conventions (L3)

- ECharts marks keep the existing rich tooltip (key, exact value to 4 dp, definition,
  range, default, group) — unchanged from `drawFeatureBars`.
- Plain-HTML marks (member dots, meter, band legend) use `title=` at minimum; where the
  content is multi-field (member dots) use the existing `.chip`-styled positioned div
  pattern (one shared `hoverCard(anchor, buildEl)` helper, shown/hidden on
  mouseenter/focus + mouseleave/blur — dots are focusable, see §3.2).
- Tooltips **enhance, never gate**: every tooltip value is also present in L4 (engineer
  details / table view).

---

## 2. Confidence gauge with visible bands (Decision card, L2)

### 2.1 What is wrong today

`drawGauge()` colors the arc by band but nothing on screen says what the zones *are*,
where the needle must reach for auto-apply, or what falling short means. Colors are
hardcoded hex, duplicating the `--band-*` tokens.

### 2.2 Target design — "gauge + threshold ticks + band legend"

Three pieces, one component `bandedGauge(el, legendEl, dec)`:

**(a) ECharts arc (canvas, 280×150 as today).** Replace the option in `drawGauge`:

```js
const BAND = {                     // read tokens at runtime like FEATURE_GROUP_COLORS does
  abstain: getVar('--band-abstain'), suggest: getVar('--band-suggest'), auto: getVar('--band-auto'),
};
series: [{
  type: 'gauge', min: 0, max: 1, radius: '100%', center: ['50%', '68%'],
  startAngle: 200, endAngle: -20,
  axisLine: { lineStyle: { width: 14, color: [
    [dec.tau_suggest, BAND.abstain],
    [dec.tau_auto,    BAND.suggest],
    [1,               BAND.auto],
  ] } },
  // zone boundaries carried by ticks AT the thresholds, not arbitrary splitNumber ticks:
  splitNumber: 1, axisTick: { show: false }, splitLine: { show: false },
  axisLabel: { show: false },                    // numeric labels move to HTML (b)
  pointer: { width: 4, length: '62%', itemStyle: { color: INK } },
  anchor: { show: true, size: 8, itemStyle: { color: INK } },
  detail: { valueAnimation: true, formatter: (v) => v.toFixed(2), color: INK,
            fontSize: 26, offsetCenter: [0, '38%'] },
  title: { offsetCenter: [0, '68%'], color: MUTED, fontSize: 10 },
  data: [{ value: dec.confidence, name: 'confidence' }],
}]
```

**(b) Threshold ticks as positioned HTML chips** (canvas text at 9px is illegible; HTML
inherits the design system). The gauge sweeps 220° from `startAngle: 200` to `-20`, so for
value `v` the angle is `θ = 200 − 220·v` degrees. With center `(0.5·W, 0.68·H)` and arc
radius `R ≈ 0.5·min(W, 2·0.68·H) − 7` (half the 14px band), place a chip at radius
`R + 12`:

```
x = cx + (R+12)·cos(θ·π/180)
y = cy − (R+12)·sin(θ·π/180)
```

Two chips, absolutely positioned inside the (position:relative) gauge wrap, recomputed on
resize:

```html
<span class="thresh-chip mono" style="left:…;top:…">.45</span>
<span class="thresh-chip mono" style="left:…;top:…">.75</span>
```

```css
.thresh-chip { position: absolute; transform: translate(-50%, -50%); font-size: 10px;
  color: var(--muted); background: var(--surface-2); border: 1px solid var(--hairline-2);
  border-radius: 4px; padding: 0 4px; pointer-events: none; }
```

Values come from `dec.tau_suggest` / `dec.tau_auto` (real payload fields), formatted
`.toFixed(2).replace(/^0/, '')` — never hardcoded.

**(c) Band legend row** — the piece that makes the zones *mean something* to a QA
engineer. Rendered under the gauge, one row, three entries; the **active band** (from
`dec.band`, a real field) is full-strength, the others muted:

```html
<div class="band-legend" role="list">
  <span class="bl-item" role="listitem" data-active>
    <i class="dot" style="background:var(--band-abstain)"></i>
    Don't know · &lt; 0.45 → To Investigate
  </span>
  <span class="bl-item">
    <i class="dot" style="background:var(--band-suggest)"></i>
    Suggest · 0.45–0.74 → human confirms
  </span>
  <span class="bl-item">
    <i class="dot" style="background:var(--band-auto)"></i>
    Auto-apply · ≥ 0.75
  </span>
</div>
```

Copy audit (honesty): the numeric ranges are printed from `tau_suggest`/`tau_auto`; the
phrases describe the real pipeline behavior — abstain routes to `ti` (To Investigate,
`_matching_decision` l.391), suggest-band suggestions await human outcome, auto band is
auto-applied. Nothing invented.

```css
.band-legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 4px; }
.bl-item { display: inline-flex; align-items: center; gap: 6px; font-size: 11px;
  color: var(--muted); }
.bl-item .dot { width: 8px; height: 8px; border-radius: 50%; opacity: .45; }
.bl-item[data-active] { color: var(--ink-2); font-weight: 600; }
.bl-item[data-active] .dot { opacity: 1; }
```

Identity is never color-alone: each zone carries its text label; the active one is also
bolded (second encoding). The band badge already in the `.kv` block stays.

### 2.3 Mechanism tag (which stage decided) — L1/L3

The payload's `matching.stage` (`A` | `AB` | `C` | `abstain` | `none`) is the mechanism.
The Decision card L1 line reuses the existing stage badge from `matchingCard` (same colors)
with the already-real `matching.stage_label` text ("Stage A — exact hash inherit", "Stage
A/B — KB mode match", "Stage C — hybrid retrieval + GBM", "Abstained") and `title=
matching.stage_note` as the L3 hover. No new strings; the badge simply also appears on the
Decision card so the "which mechanism" answer sits next to the gauge instead of one card up.

---

## 3. Grouping card: member dot strip + burst treatment

### 3.1 L1 glance sentence (derived template, three states)

Rendered above the visual, `class="note"`, all numbers from payload:

- `member_count === 1` →
  `Alone in its group — no other failure in this launch shares this signature.`
- `member_count > 1 && !dominant` →
  `${member_count} failures in this launch share this signature — one diagnosis covers all of them.`
- `dominant === true` →
  `${member_count} failures share this signature and it dominates the launch — burst pattern, system-issue prior ${fmt(si_prior,2)}.`

Each clause maps 1:1 to a payload field (`member_count`, `dominant`, `si_prior`); "one
diagnosis covers all of them" states what grouping *is* (co-failure group, spec §5) — a
derived description, not invention.

### 3.2 Member dot strip (L2)

**Data requirement (payload addition).** `grouping_block` today has only `member_count`.
Add to the journey payload (backend already has the member-list machinery for the
Signatures detail view, `payloads.py` l.1019–1090 with its member cap — reuse it):

```jsonc
"grouping": {
  …existing fields…,
  "members": [ { "item_id": 123, "item_name": "…", "issue_type": "pb001",
                 "label_group": "pb", "is_self": true, "ui_url": "…" }, … ],  // capped
  "member_shown": 60,        // len(members) after cap
  "launch_failed_count": 41  // failures in launch — makes dominance/si_prior concrete
}
```

Cap 60 (mirrors the existing member-cap convention); `is_self` computed server-side by
item_id equality. If members cannot be fetched (e.g. group matched by fallback, fingerprint
≠ this item's error_hash), omit `members` and the strip degrades to the count chip — honest
status, no fake dots.

**Component `memberStrip(g)`:**

```html
<div class="dot-strip" role="list" aria-label="group members">
  <button class="m-dot pb" role="listitem" aria-label="item 123 · Product Bug"></button>
  <button class="m-dot si self" …></button>
  …
  <span class="chip">+14 more</span>   <!-- only when member_count > member_shown -->
</div>
<div class="dot-strip-key">…count summary, see below…</div>
```

```css
.dot-strip { display: flex; flex-wrap: wrap; gap: 4px; align-items: center;
  padding: 10px 12px; background: var(--surface-2); border: 1px solid var(--hairline);
  border-radius: var(--radius-s); }
.m-dot { width: 10px; height: 10px; border-radius: 50%; border: none; padding: 0;
  cursor: pointer; background: var(--c, var(--lbl-none)); }
.m-dot.pb { --c: var(--lbl-pb); } .m-dot.ab { --c: var(--lbl-ab); }
.m-dot.si { --c: var(--lbl-si); } .m-dot.nd { --c: var(--lbl-nd); }
.m-dot.ti { --c: var(--lbl-ti); } .m-dot.none { --c: var(--lbl-none); }
.m-dot.self { width: 12px; height: 12px;
  box-shadow: 0 0 0 2px var(--surface-2), 0 0 0 4px var(--accent); }  /* surface ring + accent ring */
.m-dot:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
```

- Dots are `<button>`s: keyboard-focusable, hover/focus shows the L3 hover-card (item
  name, id chip deep-linking via existing `idChip`/`ui_url`, defect badge); click
  navigates the journey to that member (same `selectItem` path — it is in the same
  launch, so the item list already contains it).
- **This item** is the 12px dot with the double ring (`--accent` halo — same affordance
  as `.halo-auto` and `tr.is-self`), always rendered even if it lands in the "+N more"
  tail (force-include self before capping).
- **Never color-alone:** below the strip, a count summary line derived from `members`
  (`.dot-strip-key`, 11px muted): `12 members: ` + one `defectBadgeAbbr` chip per distinct
  `label_group` with its count (e.g. `SI ×8 · TI ×3 · PB ×1`). Uses only real labels
  present in the data; counts computed client-side.
- `member_count === 1`: strip renders the single self-dot — the loneliness *is* the
  visual; the L1 sentence carries the meaning.

### 3.3 si_prior — replace the bare dial with a banded horizontal meter

`siDial()` (unlabeled circular arc, warning-colored) is replaced by `siMeter(v)`:

```html
<div class="si-meter" title="si_prior — burst prior from §5 · range [0, 0.9]">
  <div class="si-label">System-issue prior</div>
  <div class="si-track">
    <i class="si-fill" style="width: calc(100% * v / 0.9)"></i>
    <b class="si-tick" style="left: 50%"></b>       <!-- 0.45 midpoint tick -->
  </div>
  <div class="si-scale"><span>0</span><span class="si-val mono">0.90</span><span>0.9 max</span></div>
</div>
```

```css
.si-meter { min-width: 180px; flex: 1 1 180px; }
.si-label { font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); margin-bottom: 4px; }
.si-track { position: relative; height: 8px; border-radius: 4px; background: var(--surface-3); }
.si-fill  { position: absolute; inset: 0 auto 0 0; border-radius: 4px;
  background: var(--lbl-si); }               /* the prior argues FOR the SI class → SI hue */
.si-tick  { position: absolute; top: -2px; bottom: -2px; width: 1px; background: var(--hairline-2); }
.si-scale { display: flex; justify-content: space-between; font-size: 10px; color: var(--muted); margin-top: 3px; }
.si-val   { color: var(--ink-2); font-weight: 600; }
```

- Scale is **0 → 0.9** because 0.9 is the documented hard max of the prior
  (`features_meta.py` idx 32 range `[0,0.9]`) — the current dial already normalizes by
  0.9 but hides that; the meter says it (`0.9 max` end label).
- Fill hue is `--lbl-si` (the class the prior argues for), not `--warning` — warning is a
  status color and this is not a status; the burst *alarm* (below) owns the warning hue.
- The exact value renders as text (`si-val`), tooltip carries the definition string
  copied verbatim from `features_meta.py` ("burst prior from §5").
- Layout: meter and dot strip share the L2 row (`flex wrap`): strip grows, meter fixed
  min-width; on narrow cards they stack.

### 3.4 Burst state — the alarm treatment (`dominant === true`)

- Card head keeps the existing `🔥 burst` badge (real derived status), and the card gets
  `class="burst-accent"`:

```css
.card.burst-accent { border-color: color-mix(in srgb, var(--warning) 45%, var(--hairline)); }
.card.burst-accent .card-head { box-shadow: inset 3px 0 0 var(--warning); } /* same accent as .sig-row.conflict */
.card.burst-accent .dot-strip { border-color: color-mix(in srgb, var(--warning) 35%, var(--hairline));
  background: color-mix(in srgb, var(--warning) 6%, var(--surface-2)); }
```

- One **single** 900ms attention pulse on first render (same pattern as the stepper
  `focus()` animate call: `el.animate([{boxShadow:'0 0 0 2px var(--warning)'},
  {boxShadow:'none'}], {duration: 900})`), suppressed under
  `prefers-reduced-motion: reduce`. No looping animation.
- With `launch_failed_count` in the payload the L1 burst sentence can be upgraded to
  `${member_count} of ${launch_failed_count} launch failures share this signature…` —
  build the template conditionally on field presence.

### 3.5 L4 — Details for engineers (Grouping)

`engDetails('grouping', kv)` containing exactly today's `.kv` block, unchanged:
`group_id` (mono), `fingerprint` (mono, full signed-64 hash), `members`, `dominant`,
`si_prior`, plus `created_at`. Nothing is deleted — it moves one click away.

---

## 4. Feature waterfall → grouped evidence bars (Decision card, L2)

### 4.1 Evidence group mapping (data-driven, no invention)

Groups come from the real `group` field of `features_meta.py`. Plain-language display
names are fixed UI labels for those real groups (same status as any column header), with
the raw group key always shown beside on hover/expand:

| `group` key | Display name | n | Color (existing `FEATURE_GROUP_COLORS`) |
|---|---|---|---|
| `retrieval` | Similarity to past failures | 13 | `--accent` |
| `history` | This test's history | 8 | `--lbl-ab` |
| `kb` | Knowledge base | 9 | `--lbl-si` |
| `grouping` | Launch context | 4 | `--warning` |
| `signal` | Log signal quality | 5 | `--lbl-nd` |
| `other` | Unmapped features | — | `--muted` |

Note: the brief's candidate name "Safety checks" is **not used** — no feature in the
39-feature schema is a safety gate, and inventing a group with nothing in it violates the
no-dummy-text rule. `signal` features (log count, has_stacktrace, is_assertion…) are
honestly named "Log signal quality". `other` renders only if the stored vector contains a
key unknown to the schema (`describe()` fallback) — an honest drift indicator, never
hidden. If the schema later grows a gate/safety group, it appears automatically: the
mapping is `DISPLAY_NAME[group] || group`.

### 4.2 Collapsed state (default): one row per group

Replace the always-on 39-row ECharts chart with 5 (or 6) HTML group rows, sorted by
`Σ|value|` descending. Pure HTML/CSS (the existing `.microbar` idiom) — ECharts is only
instantiated for *expanded* groups, which also kills the current 600px-tall initial render.

```html
<div class="evi-row" data-group="retrieval">
  <button class="evi-head" aria-expanded="false">
    <i class="dot" style="background:var(--accent)"></i>
    <span class="evi-name">Similarity to past failures</span>
    <span class="chip">13</span>
    <span class="evi-top muted">top: Top-1 cosine 0.87</span>
    <span class="evi-bar microbar"><i style="width:64%"></i></span>
    <span class="evi-sum mono">Σ|v| 3.42</span>
    <span class="eng-caret">▸</span>
  </button>
  <div class="evi-body" hidden><!-- ECharts bar chart, rendered lazily --></div>
</div>
```

- **Aggregate = `Σ|value|` across the group's present features**, bar width normalized to
  the max group sum. It is labeled exactly `Σ|v|` (mono) so it never masquerades as a
  probability or a SHAP value — it is what it says: summed magnitudes. (The current chart
  already sorts by `|value|`; this is the same statistic, grouped.)
- `evi-top` previews the group's strongest feature: existing human `label` + value at 2 dp
  — real schema label, real value.
- Bar fill = the group color (identity), never a value ramp. Bars ≤ 8px tall (microbar),
  rounded data-end.

```css
.evi-row { border: 1px solid var(--hairline); border-radius: var(--radius-s);
  background: var(--surface-2); margin-bottom: 6px; overflow: hidden; }
.evi-head { display: grid; grid-template-columns: 10px minmax(140px, 1fr) auto minmax(0,1.2fr) 120px 70px 14px;
  gap: 10px; align-items: center; width: 100%; padding: 8px 12px; cursor: pointer;
  background: none; border: none; color: var(--ink); font: inherit; text-align: left; }
.evi-head:hover { background: var(--surface-3); }
.evi-head:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.evi-head .dot { width: 10px; height: 10px; border-radius: 50%; }
.evi-name { font-size: 12.5px; font-weight: 600; }
.evi-top { font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.evi-sum { font-size: 11px; color: var(--ink-2); text-align: right; }
.evi-body { border-top: 1px solid var(--hairline); padding: 8px 8px 4px; background: var(--surface-1); }
@media (max-width: 720px) { .evi-head { grid-template-columns: 10px 1fr auto 70px 14px; }
  .evi-top, .evi-bar { display: none; } }
```

Section title above the block stays honest about coverage, reusing the existing string
with grouping appended: `Feature vector (${feature_count} of ${feature_total}, grouped by
evidence type)`.

### 4.3 Expanded state: raw features with exact values

Clicking a row toggles `aria-expanded` / `hidden` and lazily renders **the existing
`drawFeatureBars`**, filtered to that group's features (sorted by `|value|`, same 15px/row
height formula, same per-feature rich tooltip with key/definition/range/default). One
chart instance per open group, disposed on collapse. Multiple groups may be open at once.
Default: **all collapsed**; the group containing the single largest `|value|` feature
auto-expands (the "why" headline is one click closer without flooding the card).

### 4.4 L4 — Details for engineers (Decision)

`engDetails('decision', …)` containing:

1. The raw `.kv` fields exactly as today: `model_ver` (mono), `llm_used`, `outcome`,
   `outcome_ts`, `suggestion_id`, `created_at`.
2. **Full-schema table twin** (`table.data`, inside `.table-wrap`): all 39
   `FEATURE_DEFS` rows joined with the stored vector — columns
   `# · key · value · definition · range · default · group`. Features absent from the
   stored vector render value `—` plus chip `default ${default}` — honest: the stored
   vector simply doesn't contain them, and the documented default is printed as such.
   This is the WCAG table-view twin of the bars: every charted value is reachable as text.

---

## 5. Decision card layout (assembled)

```
┌ 4 · Features & decision ────────────────── model_ver ┐
│ L1  [stage badge  Stage C — hybrid retrieval + GBM]  │
│     [predicted badge] [band badge] [outcome badge]   │
│ L2  ┌ gauge (banded arc + .45/.75 chips) ┐  .kv col  │
│     └ band-legend (active band bolded)   ┘           │
│     “explanation” note (unchanged, real field)       │
│     FEATURE VECTOR (n of 39, grouped by evidence)    │
│     ▸ Similarity to past failures  13  ▬▬▬▬  Σ|v|…   │
│     ▾ Launch context 4  ▬▬  Σ|v|…  [ECharts bars]    │
│     ▸ …                                              │
│ L4  ▸ Details for engineers                          │
└──────────────────────────────────────────────────────┘
```

Grouping card: L1 sentence → L2 row (`dot-strip` + key line | `si-meter`) → L4 details.
Burst adds the `.burst-accent` treatment.

---

## 6. Build inventory

**JS (all in existing files, no new libs):**

| Function | File | Replaces / new |
|---|---|---|
| `engDetails(key, ...children)` | `util.js` | new shared |
| `hoverCard(anchor, buildEl)` | `util.js` | new shared |
| `bandedGauge(el, legendEl, dec)` | `journey.js` | replaces `drawGauge` |
| `memberStrip(g)` + key line | `journey.js` | new in `groupingCard` |
| `siMeter(v)` | `journey.js` | replaces `siDial` |
| `evidenceGroups(features)` → `[{group, name, color, feats, sum, top}]` | `journey.js` | new (pure derivation) |
| `eviRow(groupModel)` (lazy `drawFeatureBars` on expand) | `journey.js` | replaces the single 39-row chart |
| `groupGlance(g)` (3-state template) | `journey.js` | new in `groupingCard` |

**CSS additions** (`app.css`, tokens only): `details.eng`, `.thresh-chip`, `.band-legend`
/`.bl-item`, `.dot-strip`/`.m-dot`/`.dot-strip-key`, `.si-meter` family, `.burst-accent`,
`.evi-row` family. No token changes; `drawGauge` hardcoded hexes replaced by
`getVar('--band-*')`.

**Backend (one payload change, `payloads.py` journey `grouping_block`):** add `members[]`
(capped 60, `is_self`, `label_group`, `ui_url` via the existing RP link batch),
`member_shown`, `launch_failed_count`. Frontend degrades gracefully when fields are absent
(strip → count chip; burst sentence → member-only variant), so the UI can ship first.

**Verification pass (per dataviz method):** render an item in each state — members=1,
members>1, burst, abstain/suggest/auto band, an unknown feature key — and eyeball for
label collisions, chip placement at both thresholds, dot-strip wrap at 60 dots, and the
table twin. Colors are existing validated tokens; no new palette to re-validate.
