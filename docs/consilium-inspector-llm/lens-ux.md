# Lens 2 — UX placement & components for LLM-sidecar visibility

Scope: where and how the Inspector shows the four async LLM roles (coldstart,
explainer, judge, extractor — spec 04) and their guardrails. Extends the approved
round-1/round-2 design language (`docs/consilium-inspector-ux/`) without change:
L1 terse deterministic takeaway, L2 visual with units printed, L3 hover, L4
`engDetails`/`engDrawer`, tokens-only CSS, hash permalinks, hard no-dummy-data.

Grounding (verified in source, not assumed):

- `src/analyzer_ng/db/migrations/0004_llm.sql` — `llm_event(event_id, project_id,
  item_id, role ∈ {explainer,extractor,judge,coldstart}, model, prompt_hash,
  cache_hit, outcome ∈ {ok, schema_fail, validation_fail, timeout, breaker_open,
  dropped}, output jsonb (NULL unless ok), latency_ms, created_at)`, index
  `(project_id, role, created_at)`; `llm_role_state(project_id, role, enabled,
  reason, stats jsonb, decided_at)`.
- `0001_init.sql` §2.11 — `llm_cache(project_id, cache_key, role, template_hash,
  output, model, hits, created_at, last_hit_at)`, index `(project_id, role,
  template_hash)`.
- `src/analyzer_ng/llm/breaker.py` — breaker state is **in-process only**
  (closed/open/half-open, 3 consecutive transport failures, 60 s cooldown
  doubling to 15 min). Nothing persists the state itself; only `breaker_open`
  outcomes land in `llm_event`. Schema/validation failures do **not** trip it.
- `src/analyzer_ng/llm/apply.py` — explainer fills only
  `suggestion.explanation` + `llm_used`; judge only reorders/annotates
  suggest-band rows (never label/confidence, never auto-band); coldstart inserts
  a suggest-band suggestion (`methodName='llm_coldstart'`, conf 0.65,
  `model_ver='rubric+qwen3:4b-q4_K_M'`).
- `src/analyzer_ng/llm/engine.py` l.139–147 — `schema_fail` vs
  `validation_fail` split; `sanitizer.py` — injection envelope (§5).
- Live data (`demo-data/LLM-DEMO.md`): coldstart launch 181 / project `llm-demo`
  (id 6), 12 ok; explainer items 2942/2943 ok + 2941 `validation_fail`;
  honest-abstain 2959 (extractor-only); **judge = 0 events** (suggest-path
  divergence, documented — the UI must render that zero honestly).
- UI: `inspector/static/js/app.js` (VIEWS map, `linkState`, `serializeHash`),
  `index.html` l.39–45 (six tabs), `views/journey.js` (stage cards; note
  `GROUP_NAME.llm = 'LLM extractor (llm)'` already exists in the evidence
  groups, l.655), `util.js` (`card, chips, defectBadge/Abbr, idChip, engDetails,
  engDrawer, echartsBase, relTime, srcInfo, BAND/GOOD/WARNING/ACCENT tokens`).

---

## 1. Placement decision — (c) both, with a hard division of labor

**Recommendation: (c).** Neither (a) nor (b) alone can answer both questions the
LLM raises, and the two questions have different natural scopes:

| Question | Scope | Home |
|---|---|---|
| "Did the LLM touch **this** decision, and how?" | one item | Journey — Decision card strip (§3) |
| "Is the sidecar **healthy, honest, and guarded** in this project?" | project | new 7th tab **LLM** (§4) |

Why not (a) alone: `suggestion.llm_used`, `explanation`, `model_ver`, and the
per-item `llm_event` rows are *decision provenance* — an engineer auditing item
2942 must see the explainer's involvement **where the decision is shown**, not
by cross-referencing an activity stream. Burying per-item provenance in a tab
breaks the journey's promise ("trace one item through every stage").

Why not (b) alone: outcome mix, latency distribution, kill-switch state, cache
re-use, and event-gap honesty have **no item anchor**. `llm_role_state` is
per-project; `llm_cache` is per-template-set; the judge's zero-event story is a
project-level fact. Forcing these onto a journey card would show fleet data in
an item context — a category error the current design language forbids.

The division rule (enforceable in review): **the Decision-card strip renders
only rows `WHERE item_id = <this item>` plus the suggestion's own LLM columns;
the LLM tab renders only project-scoped aggregates and streams.** The two link
to each other via existing permalinks (idChip → `#view=journey&…`; strip footer
→ `#view=llm&…`).

Explicitly rejected: a **6th stage card**. The sidecar is not a pipeline stage —
it is an async annotator of stages 3/4 (judge reorders candidates, explainer
annotates the decision, coldstart substitutes for a decision, extractor feeds
features). A stepper stage would misstate the architecture and violate the
funnel semantics (A→B→C→decide) the Matching card already teaches.

---

## 2. Data plumbing (minimal, read-only)

New payload pieces (all read-only SELECTs, same style as `payloads.py`):

1. **Journey payload** gains `d.llm`:
   `{ events: [...llm_event WHERE item_id=? ORDER BY created_at DESC LIMIT 20],
      role_state: [...llm_role_state WHERE project_id=?] }`.
   Each event: `role, outcome, model, cache_hit, latency_ms, created_at, output`
   (output already NULL unless ok — pass through verbatim).
2. **`GET /api/llm/summary?project=`** — per-role: counts by outcome, last
   event `created_at`, latency percentiles over `outcome='ok'` (p50/p90/max),
   plus the `llm_role_state` row (or its absence — absence = never evaluated,
   default enabled; render that honestly, do not synthesize a row).
3. **`GET /api/llm/events?project=&role=&outcome=&limit=50`** — the stream,
   newest-first, cap 50 like `feedback[]`; join `test_item` for `item_name`,
   `launch_id`, RP `ui_url` so `idChip` links work.
4. **`GET /api/llm/cache?project=&role=extractor&limit=50`** — cache rows
   ordered `hits DESC, last_hit_at DESC`.

One fix to existing code (a no-dummy-data violation already shipped):
`journey.js` l.706 renders `'🧠 LLM judge consulted'` whenever
`dec.llm_used` is true — but `llm_used=true` is also set by explainer and
coldstart (`apply.py`), and judge has **zero** events on the stand. That badge
asserts a judge consult that never happened. It is **replaced** by the
role-accurate strip in §3.

---

## 3. Journey integration — the "LLM involvement" strip (Decision card)

Position: inside `decisionCard`, after the verdict chip row, before the gauge.
Component `llmStrip(d)` — rendered **only when** `d.llm.events.length > 0` or
`dec.llm_used`. Total function of the payload; first matching variant wins.

### 3.1 L1 takeaway (deterministic template per role-set present on the item)

Let `E = d.llm.events`, `roles = distinct(E.role)`, `okRoles = distinct(role where outcome='ok')`.

- **Coldstart** (`dec.method === 'llm_coldstart'` or `model_ver` startsWith `rubric+`):
  `LLM cold-start: provisional <label> — rubric over <model>, conf fixed 0.65;
  suggests, never auto-confirms (methodName llm_coldstart).`
- **Explainer ok** (explainer ∈ okRoles and `dec.explanation`):
  `LLM explained this decision — rationale below is model output (<model>,
  <latency_ms> ms<, cache hit>); label and confidence are the analyzer's, not the LLM's.`
- **Explainer guarded** (explainer ∈ roles, latest explainer outcome ∈
  {schema_fail, validation_fail}):
  `LLM explanation withheld — output failed <outcome> so nothing was persisted
  (guardrail; the decision itself is unaffected).`
- **Judge ok** (judge ∈ okRoles): `LLM judge re-ranked the suggest-band
  candidates — chose item <chosen_item_id>; label/confidence untouched by design.`
- **Extractor-only** (roles ⊆ {extractor}): `LLM touched features only —
  extractor fed the 'llm' evidence group; it never saw the label path.`
  (This is the honest-abstain contrast, item 2959.)
- **Availability outcomes only** (all outcomes ∈ {timeout, breaker_open, dropped}):
  `LLM was asked but unavailable — <n>× <outcome(s)>; the decision proceeded
  without it.`

### 3.2 L2 — per-role visual treatments (four distinct, one strip)

One horizontal strip of **role tiles** (reuse `.chip`/`.badge` + a new
`.llm-tile` container, tokens only). Each role present gets its tile; roles
absent are absent (no placeholder tiles — no dummy data).

1. **Coldstart — "provisional label" treatment.** `defectBadge(label, group)`
   wrapped in a dashed border (`border: 1px dashed var(--accent)`) + tag
   `provisional` + mono chip `rubric+qwen3:4b-q4_K_M` + chip `conf 0.65 (fixed)`.
   The `llm_event.output.reason` string (real model output, e.g. the ENOSPC
   rationale) renders as the quote block of §3.3. Dashed border is the
   provisional signature — reused nowhere else.
2. **Explainer — "attributed quote" treatment.** The existing
   `dec.explanation` note (l.721) is upgraded when `llm_used` and an explainer
   ok-event exists: left border `3px solid var(--accent)`, caption line
   `model's rationale — <model> · <latency_ms> ms · <relTime>`, and the L3
   hover carries `prompt_hash` + `cache_hit`. When the newest explainer event
   is `validation_fail`: **no quote**; instead a guard note (§3.4). Never both.
3. **Judge — "verdict" treatment.** Chip pair: `judge chose` +
   `idChip(item <chosen_item_id>)` (from the features patch `apply.py`
   writes), plus muted `order only — label/conf untouched`. On this stand it
   never renders (0 events) — correct behavior is its absence, and the LLM tab
   carries the project-level honesty (§4.2).
4. **Extractor — "feature chips" treatment.** Chip row of extracted keys from
   `output` + `cache_hit` chip (`cache ✓` / `fresh call`), and a cross-link
   note: `feeds the 'llm' evidence group below` — clicking pulses the existing
   `evi-row[data-group=llm]` via the shipped `scrollPulse` pattern.

### 3.3 Guard note (validation_fail / schema_fail as guardrail, not error)

Styling rule for the whole feature (strip **and** tab): `validation_fail` and
`schema_fail` use `var(--accent)` (guardrail-worked, informational), **never**
`var(--warning)` or red. Warning tones are reserved for availability problems
(`timeout`, `breaker_open`, `dropped`) and for `llm_role_state.enabled=false`.
Copy template (item 2941's story):

> `Guardrail fired: the model's output failed <schema|content> validation —
> nothing was persisted (no hallucinated explanation ever reaches the row).`

### 3.4 L4 — `engDetails('llm', …)`

Raw table of `d.llm.events`: `event_id, role, outcome, model, prompt_hash,
cache_hit, latency_ms, created_at`, plus `dl.kv` rows `suggestion.llm_used`,
`suggestion.model_ver`. Footer note: `append-only analyzer.llm_event, newest
20 for this item` + link chip `all events → LLM tab`
(`#view=llm&project=<id>` — see §5 permalinks).

---

## 4. The 7th tab — "LLM"

`index.html`: `<button class="tab" data-view="llm">LLM</button>` after Learning
Loop; `app.js`: add `llm: renderLlm` to `VIEWS`, import from `views/llm.js`.
Layout mirrors Learning Loop: stacked cards, full width, ECharts via
`echartsBase()`.

### 4.1 Card 1 — "Roles at a glance" (the four role-stories, project-scoped)

Four fixed role panels (this is the one place all four always render — the
role *set* is schema-constant, so empty panels are honest states, not dummies).
Each panel: role name + one-line plain gloss (`coldstart — provisional labels
in zero-label projects`, `explainer — rationale on confident suggestions`,
`judge — mid-band candidate re-ranking`, `extractor — features from logs`),
then:

- **ok count** (large numeral, existing `.counter` style), outcome breakdown
  chips, `last event <relTime>` (or `no events`).
- **State line** from `llm_role_state`: `enabled` (dot `var(--good)`) /
  `disabled — <reason>` (dot `var(--warning)`, reason verbatim:
  `auto_disabled_precision`, `admin`); when no row exists:
  `no eval verdict yet — enabled by default`.
- **Zero-event honesty** (the judge story). L1 template when count = 0 and
  enabled: `0 events — role enabled, wired, never fired in this project.` L3
  hover adds the firing contract as *documentation*, not data: `fires only on
  the suggest route at confidence [0.45, 0.75) with ≥2 candidates
  (core/analysis.py)`. No speculation about why in the L1; the contract line
  lets an engineer conclude "the population is empty" themselves.

### 4.2 Card 2 — outcome mix (guardrails as signal)

Horizontal stacked bar per role (ECharts, `echartsBase()`, same idiom as
`loop.js`). Series order and color mapping (tokens only):

- `ok` → `var(--good)`
- `validation_fail`, `schema_fail` → `var(--accent)` — legend group titled
  **"guardrail fired (output rejected)"**
- `timeout`, `breaker_open`, `dropped` → `var(--warning)` /
  `var(--muted)` shades — legend group **"sidecar unavailable"**

L1 above the chart: `"<N> LLM calls · <ok%> ok · <g> rejected by guardrails ·
<u> unavailable — rejections mean the validator worked, not that the pipeline
erred."` (counts real, sentence deterministic). L3 tooltip per segment: count,
share, role.

### 4.3 Card 3 — latency

Grouped bar or box-plot per role over `outcome='ok'` events: p50 / p90 / max
`latency_ms` (computed server-side in `/api/llm/summary`). Caption prints the
unit and the honest caveat: `wall-clock per call incl. queue-to-model;
cache hits excluded` (or included — decide in the endpoint and print which).
Empty roles: `—` row, no bar. Reuses the `loop.js` axis/tooltip conventions.

### 4.4 Card 4 — activity stream (grouped by role)

Filter chip row (`role: all | coldstart | explainer | judge | extractor`,
`outcome: all | ok | guardrail | unavailable`) → `/api/llm/events`. Rows reuse
the Feedback timeline idiom (`.tl` list): outcome dot (color per §4.2), role
chip, `idChip(item <id>)` linking to
`#view=journey&project=<p>&launch=<l>&item=<id>` (in-app hash — same-tab
navigation for free), model mono chip, `cache ✓` when `cache_hit`,
`<latency_ms> ms`, `relTime`. `output` (ok rows only) behind a per-row
`engDrawer` as pretty-printed JSON — model text is untrusted content, always
rendered as text (the codebase's `h()` text nodes already guarantee no HTML
injection; keep it that way, never `html:` for LLM output). Cap line at 50,
same as Feedback.

### 4.5 Card 5 — availability honesty (the breaker panel that refuses to lie)

The breaker is in-process (`breaker.py`) — the Inspector **cannot** know its
current state. This card says so and shows what the event log *does* support:

- L1: `Breaker state is in-process and not persisted — shown below is the
  event log's view, not the live breaker.`
- Facts: `last event <relTime>` overall; count of `breaker_open` +
  `timeout` events in the last 24 h / 7 d; the longest gap between events in
  the window (a real, derivable number). No "status: healthy" synthesis, no
  green/red lamp — that would be dummy data about unpersisted state.
- L4 drawer: breaker constants as documentation (3 consecutive transport
  failures to open; cooldown 60 s doubling, cap 15 min; schema/validation
  failures never trip it) — clearly captioned `from breaker.py, config not
  runtime`.

### 4.6 Card 6 — extractor cache explorer

Table (reuse `.data` + `.table-wrap`): `template_hash` (mono; **no link** —
it is a template-*set* hash, not a `template_id`, so no honest Drain3 deep-link
exists), `model`, `hits`, `created_at`, `last_hit_at`, per-row `engDrawer`
with the cached `output` JSON. Header L1: `"<n> cached extractor outputs ·
<Σhits> hits — one model call per template-set per project, then reuse."`
Non-extractor cache rows stay reachable via a role filter chip (explainer/
coldstart/judge cache entries exist per §2.11) but extractor is the default.

---

## 5. Permalinks

Extend `linkState`/`serializeHash` in `app.js` with the existing pattern:

- `#view=llm&project=<id>` — tab.
- `&lrole=<role>` `&loutcome=<ok|guardrail|unavailable>` — stream filters
  (names prefixed `l` to avoid colliding with journey's `launch`/`item`).
- Cross-links: strip footer → `#view=llm&project=<p>`; stream item →
  `#view=journey&project=<p>&launch=<l>&item=<i>` (already round-trips through
  `applyHashState` / `setJourneyState`).

---

## 6. Build order (each step ships alone)

1. Fix the `llm_used` badge lie (l.706) + journey `d.llm` payload + `llmStrip`
   with coldstart/explainer/extractor treatments and `engDetails('llm')`.
   Verifiable immediately on items 2942/2943 (quote), 2941 (guard note),
   2932–2940 (provisional), 2959 (extractor-only).
2. LLM tab: roles-at-a-glance + activity stream (`/api/llm/summary`,
   `/api/llm/events`). Judge zero-state verifiable immediately.
3. Charts (outcome mix, latency) + availability card.
4. Cache explorer + permalink filters.

Definition of done per the house rule: every string on both surfaces is a real
column value, a deterministic derivation, or an honest absence — including
"0 judge events" and "breaker state not persisted".
