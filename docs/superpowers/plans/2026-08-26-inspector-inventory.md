# analyzer-ng inspector — Complete Frontend Inventory (Council Report 1, Fable5)

> Spec appendix for the ui-kit migration plan. This is the authoritative behavioral
> inventory of the current vanilla-JS inspector frontend. The React rewrite must
> reach feature parity with everything below. When in doubt, the original source
> under `inspector/static/` is the final word — read it.

All paths relative to `/Users/Dmitriy_Gumeniuk/IdeaProjects/analyzer-ng/inspector/`.

---

## 1. App Shell (`static/index.html`, `static/js/app.js`)

### Topbar (`<header class="topbar">`, sticky top, z-40)
- **Brand**: RP "diamond" SVG mark (22×22, colored `var(--rp-topaz)`), title `analyzer‑ng **inspector**` (non-breaking hyphen `&#8209;`, "inspector" bold topaz), and below it a subtitle line `#rp-status` with default text `RP names: —`, `title="ReportPortal name resolution status"`. The status text is set from `/api/rp` → `status.note`; class `ok` (green) when `status.reachable`, class `warn` (orange) when `configured && !reachable`. On fetch failure shows `RP names: unavailable` with warn state.
- **Project selector**: labeled field "Project" (`<select id="project-select" class="select">`). Options text: `` `${name} · ${item_count} items · ${launch_count} launches` `` where name = `p.project_name || "project #<id>"` (the `#` marks a raw-id fallback; never a fabricated name). Changing project resets id-scoped selections (launch/item/glaunch/hash → null) but keeps free-text filters (`q`, `conflicts`), reloads RP names, rewrites hash, remounts view, refreshes counters.
- **Counters** (`#counters`, `aria-live="polite"`): 4 cells from `/api/summary`: `[accent] suggestions "suggest"`, `[plain] abstains "abstain"`, `[good] accepted "accepted"`, `[warn] corrected "corrected"`. Best-effort — errors swallowed.
- **Auto-refresh toggle**: custom checkbox toggle (`.toggle` with track/thumb), label `Auto‑refresh`, `title="Poll every 10s"`. When on: `setInterval` 10 000 ms → `refreshCounters()` + `mount()` (full re-render of active view) + toast `Refreshed`. On enabling shows toast `Auto‑refresh on (10s)`. Interval cleared on toggle off.

### Tabs (`<nav class="tabs" role="tablist">`, sticky at top:62px, z-30)
Order and labels (buttons with `data-view`, `role="tab"`):
1. **Item Journey** (`journey`) — default
2. **Signatures** (`signatures`)
3. **Drain3 Explorer** (`drain`)
4. **Modes Map 3D** (`modes`)
5. **Launch Groups** (`groups`)
6. **Learning Loop** (`loop`)
7. **LLM** (`llm`)
8. **Cold-start Rules** (`rubric`)

Active tab gets `.active` (bottom 2px accent border, bold). Unknown view name in hash → falls back to `journey`.

### Routing / state model (important subtleties)
- Global `state = { project, view: 'journey', autorefresh, rp, _timer }` exported from app.js.
- **URL hash permalinks**: hash is `#k=v&k2=v2` (custom encoding, not URLSearchParams). Written via `history.replaceState` (no history entries, no reload); null/''/undefined params dropped. Parsed by `readHashParams()` in util.js.
- Per-view link state (module-level `linkState`): `launch`/`item` (journey), `glaunch` (groups, serialized as `launch` when view=groups), `q`/`conflicts`(='1')/`hash` (signatures), `lrole`/`loutcome` (llm), `rule` (rubric). `serializeHash()` writes **only** params for the active view (stale params pruned).
- `project` hash param resolves by **id or RP project name** (case-insensitive) via loaded projects list.
- Boot: `api.projects()` → populate selector → `applyHashState(readHashParams(), {initial:true})` → seed each view's module state via `setJourneyState/setSignaturesState/setGroupsState/setLlmState/setRubricState` → `loadRp()` → mount → `refreshCounters()`. `window.addEventListener('hashchange', ...)` re-applies (fires on browser back/forward and manual hash edits; replaceState never triggers it).
- **Empty/error boot states**: no projects → emptyState icon 🗄️ "No projects found" / "The analyzer database has no project rows. Ingest at least one launch through the analyzer, then reload." tag `analyzer.project`. API unreachable → 🚫 "Cannot reach the inspector API" + error message, tag `GET /api/projects`.
- `mount()`: clears `#view`, shows `loading()` spinner, calls async view renderer; on rejection → 🚫 "Failed to load view" + message.
- Views call exported `updateHash(patch)` to reflect selections in the URL.
- Toast: `#toast` fixed bottom-center, `role="status" aria-live="polite"`, shows 2200 ms.

---

## 2. Per-View Inventory

### 2.1 Item Journey (`views/journey.js`, 1437 lines) — the main view

**Purpose**: traces one failing test item through the pipeline: signature → grouping → matching → decision → feedback.

**Layout**: 2-col grid `300px | 1fr`. Left sidebar: two cards "Launches" (sub "pick one") and "Failing items" (sub "pick one"). Right: journey detail.

**Launch picker**: list of `.list-item` rows — title = `launch_name || "launch <id>"`, sub = `#<launch_number ?? —> · id <id> · <item_count> items`; right chip `` `${labeled_count}/${item_count} labeled` `` with tooltip "N of M indexed items carry a defect label". Click selects (`.active` highlight), writes `launch` to hash. Empty: 📭 "No launches" / "This project has no test items yet." tag `analyzer.test_item`. Permalinked launch restored if present, else first launch auto-selected.

**Item picker** (per launch, `/api/items`): rows with title=item_name, sub = `id <item_id>` (as RP deep-link chip when `ui_url`) + `· <exc_text || 'no exc'>`; right = compact defect badge (abbr). Auto-analyzed items get `.halo-auto` (accent ring box-shadow). An id→{item,el} Map is kept so grouping member-dots can navigate within the launch. Auto-selects permalinked item or first. **Subtle**: if the permalinked `item` id is not in the list, the main pane shows an honest empty state: 🕳️ `Item <id> is not in the analyzer's index` with long explanation ("No journey exists for it. Most often the failure produced no ERROR logs…"), tag `analyzer.test_item`, and the hash stays as the link said.

**Initial empty state** (before selection): 🧭 "Pick a launch, then an item" / "The Item Journey traces one failing test item through every pipeline stage: signature → grouping → matching → decision → feedback."

**Detail header**: h2 with item name + inline badges (full defect badge; if `is_auto_analyzed` an accent badge with bolt icon " auto‑analyzed"), right-aligned chips: `item <id>` (RP link), `launch <id>` (RP link), `<log_count> logs`, mono `tch <test_case_hash ?? —>`.

**Stepper** (`.stepper`, 5 `.stage-pill`s, clickable): `1 · Signature` (value: exc_text or 'built'/'none'), `2 · Grouping` (`group <id>`/'none'), `3 · Matching` (stage_label), `4 · Decision` (band name: Auto‑apply / Suggest / Abstain / 'no suggestion'), `5 · Feedback` (`N event(s)`). Click → sets active pill, smooth-scrolls to that card, plays a 900 ms accent box-shadow pulse animation.

**Card 1 — Signature** (step 1, sub "field-separated FTS doc + fingerprints"):
- Empty: 🔤 "No signature" / "This item has no failure_signature row — it produced no error logs…" tag `analyzer.failure_signature`.
- Fingerprint chips: `exception_fp`, `error_hash` (labeled mono chips), `emb_model_ver <v>`, embedded indicator `● embedded` (green) / `○ not embedded` (muted).
- Field grid (label column right-aligned): badges EXCEPTION/MESSAGE/FRAMES/TEMPLATES/CODES (classes `fb-exc` etc.) with mono values (frames joined with `  ›  `, templates/codes comma-joined). Absent fields skipped.
- "Referenced Drain3 templates (N)" — template mini-cards: chip `#<template_id>`, right meta `<token_count> tok · <match_count> matches` or "template row missing"; pattern rendered via `highlightPattern` (innerHTML). If >6 templates: show first 5, rest behind `<details class="out-drawer tpl-more">` with summary text `show N more templates` / `show fewer templates` (CSS swaps).

**Card 2 — Grouping** (step 2, sub "who else failed like this in the same run (co-failure launch group)"):
- Empty: 🧩 "No group" / "No group — grouping needs at least one failure with a comparable error ID (error_hash) in this launch…" tag `analyzer.launch_group`.
- **Burst** = `dominant && member_count > 1` (degenerate guard: solo+dominant is not a burst). Burst adds `.burst-accent` card class (warning left-accent), a warning "burst" badge in the title, and a one-time 900 ms warning pulse (skipped under `prefers-reduced-motion`).
- L1 takeaway sentence, 3 deterministic templates: solo ("**Alone in this launch** — no other failure shares this signature; no group diagnosis applied *(solo group)*."), burst ("**Burst:** a new fingerprint covers **N of M failures** in this launch (X%) — strong **System Issue** prior applied *(dominant; si_prior s)*." — falls back to "N failures of this launch" when launch_failed_count missing/invalid), shared ("**N failures share this signature** in this launch — one diagnosis should cover all N *(shared-cause group)*.").
- **Member dot-strip**: label "failures with this error"; one `<button class="m-dot {group}">` circle per member (colors from label group; `.self` variant bigger + accent double-ring), tooltip `item <id>[ · this item] · <defect name>`, ARIA list semantics, **click navigates the journey to that item** (same launch). Overflow chip `+N more` when member_count > shown. Below: count key line — `"X of Y members: "` or `"Y member(s): "` + per-group abbr badge ×count separated by `·`. Degradation when no members array: chip `N failures with this signature` + "individual members not linkable for this group."
- **si_prior meter** (`si_prior > 0`): banded bar scaled 0→0.9 with midpoint tick, scale labels `0 / <val> / 0.9 max`, note "Prior evidence input for si, capped at 0.9 — one decision signal, not a verdict." Else `.si-note`: "No burst signal: fingerprint not new to history or share of launch failures below the gate (new fp · ≥5 members · >40% share)."
- Technical Details drawer (`engDetails('grouping')`): kv of group_id, fingerprint, member_count, dominant, si_prior, optionally launch_failed_count; note "co-failure launch group".

**Card 3 — Matching** (step 3, sub "stage A → B → C cascade"):
- No suggestion: 🎯 "Not matched yet" empty state, tag `analyzer.suggestion`.
- L1 takeaway by `matching.stage`: `A` = "**Inherited via exact hash** from item N — same `error_hash`, guards passed (≤ 180 d, human-trusted); conf fixed 0.95 *(Stage A)*."; `AB` with mode = "**Matched KB mode** "title" (#id) — purity P, support S[, seed:key] *(Stage B; conf cap 0.93)*."; `AB` without mode row = "…mode row not found in failure_mode (retired?)…"; `abstain` = "**Nothing matched** — no inheritable hash (A), no confident mode (B), no trusted candidate (C); item stays `ti`. Reason on the Decision card."; `C` = "**No exact hash, no KB short-circuit** — went to hybrid retrieval: FTS + cosine fused by `RRF`, top-20 → GBM scored the evidence *(Stage C)*."
- **Funnel** (custom flex DOM, no chart lib): three `.fn-node`s `A · exact hash` / `B · KB modes` / `C · hybrid + GBM` with per-node state won/fell/skip (won: colored left-inset via `--stage-c`, colors: A=`--lbl-nd`, AB=`--lbl-si`, C=`--accent`; skip: dashed+faded). Captions: "decided here" / "not reached" / "passed — no decision" (B: "passed — scored, no short-circuit"); node A special caption `disabled — exception_fp = 0` when signature fp is '0'. Node artifacts: A won → matched item link chip; A fell → chip "same-hash history exists" (if a non-self live candidate shares error_hash) or "no same-hash history"; B won → defect badge + mode-title chip; C won → chip `top-1 of N → GBM` (tooltip about 46-feature vector). Terminal: `abstain → To Investigate` (ti color) or `decided at <A|B|C>`. `→` arrow separators (rotated 90° on mobile via CSS).
- **Matched-mode panel** (when `matched_mode`): defect badge, chip mode title, chip `purity P (≥ 0.95)` (tooltip re short-circuit), chip `support S (≥ 10)`, optional mono chips `status`, `seed:<key>`.
- **Candidate strip** ("Closest labeled history — top K of N retrieved (live)"): up to 3 non-self candidates as cards: rank `#1`, item link chip, defect badge; cosine row (label "cos", microbar filled to cosine, value or `—` w/ tooltip "no dense score — dense leg inactive for this pair"); chips: `fp =` (same exception_fp), `hash =` (same error_hash — "Stage-A grade match"), `jac <n>` (template-set Jaccard), `src <plain>` (label_source w/ src_weight tooltip), `launch #n`, `mode <id>`. Below: **drift check note** when stored `top1_cosine` feature differs from live top cosine by > 0.005: "stored top1_cosine at decision time X vs Y now — retrieval has drifted since the decision."
- No candidates: section title `Live retrieval re-run (Stage C, HYBRID_RETRIEVAL v<n>)` + 🔍 "No candidates retrieved" empty state.
- **RRF drawer** (`engDrawer('matching-recon', "Live Stage-C reconstruction — N candidates · HYBRID_RETRIEVAL vX")`): RRF explainer paragraph (`Σ 1/(k + rank)`, `k = 60`, damping note), payload's `note` paragraph, table columns `# | item | label | src | lex rank | dense rank | cosine | jaccard | RRF fused` (RRF cell = 80px microbar normalized to max + mono value; bar tooltip shows the `1/(60+r)+1/(60+r)=v` formula). Self row highlighted (`.is-self`, topaz tint) with chip "this item" ("proof the index sees it; excluded from candidate features"). Footer note: ordered by `rrf_score DESC, item_id DESC` (tie-break).
- Technical Details (`engDetails('matching')`): kv stage / matched_item_id / matched_mode_id / suggestion_id / mode.label/purity/support/seed_key / hybrid_retrieval_version / stage_note, plus "reconstruction query (bound into the versioned SQL)": salient_terms, template_ids, emb_model_ver, dense_active.

**Card 4 — Decision** (step 4, sub "what the analyzer decided, how sure it was, and why (features & policy)"; `id="j-decision"` — scroll target from Feedback):
- Empty: 🎯 "No decision recorded" / "…the analyzer has not scored this item yet (no suggestion row)." tag `analyzer.suggestion`.
- L1 takeaway: band × method matrix (12+ variants incl. coldstart-provisional; full text in journey.js lines 841–904; includes τ values from payload — `(gbm; 0.62 ≥ τ_auto 0.75)` style suffixes; abstain adds `p* ` prefix for gbm). **Cold-start provisional** variant: "**LLM cold-start provisional:** **label**@C `model` *(ai_suggested — not shown in RP Make Decision)*; the classical decision auto-applied/suggests/abstained …" or "; no classical decision row exists for this item."
- Chip row (dedup rule — only what the takeaway doesn't say): **method chip** (`.method-chip` — mono key + plain name; keys/plain: hash="exact match", kb="known failure mode", gbm="learned model", rule_cold="starter rules", coldstart="LLM cold-start rubric"; each with a long educational tooltip, METHOD_TIP) + **outcome badge** (accepted=good, corrected=warning, ignored=muted, pending=accent showing "pending review"; tooltips OUTCOME_TIP).
- **Decision summary block** (`.llm-quote` styling): stored explanation (classical row's on provisional items, else the decision's), skipped when empty or contained in the takeaway text. Header chips: "decision summary" or "decision summary — classical row", provenance chip (LLM model / `rubric+…` prefix / "deterministic" — each with tooltip), latency chip or "cache hit" chip, "grounded quotes validated" chip when LLM-validated. Body rendered via `renderWithDefectNames` (locators → inline defect pills; XSS-safe, text nodes only).
- **LLM involvement strip** (`.llm-strip`, grey inset panel): head "LLM sidecar" + count chip `LLM · N`, right note "async roles, decision-path-neutral". Its own takeaway sentence (skipped when redundant): variants for coldstart, explainer-ok, explainer-guarded ("**LLM explanation withheld** — output failed `outcome`, so nothing was persisted *(guardrail; the decision itself is unaffected)*."), judge, extractor-only ("**LLM touched features only**…"), all-unavailable ("**LLM was asked but unavailable** — N× timeout; the decision proceeded without it."), generic. Tiles (`.llm-tile`):
  - **coldstart tile**: role-key "coldstart", chip `rule <R>`, "superseded" chip when a later classical decision governs (with tooltip); rationale quote attributed "AI proposal's own comment, cold-start" / "…earlier cold-start (superseded)" (careful dedup vs the summary block — the LLM proposal's own comment is never suppressed by a different row's explanation).
  - **explainer tile**: role-key, model chip; quote (tooltip: prompt_hash + cache hit) attributed "model's rationale — model · latency · relTime".
  - **judge tile**: role-key, chip `chose <choice>`; chip `item <id>` or "demoted all"; note "order only — label/conf untouched".
  - **extractor tile**: role-key, oc-chip "cache hit"/"fresh call"; chips `layer: …`, `class: …`, mono root_exception; note "feeds the `'llm'` evidence group above."
  - **guard note** when explainer failed: `⛨ guardrail fired · <outcome>` + "The model's output failed validation, so nothing was persisted — no hallucinated explanation ever reaches the row. Retried once (seed 7 → 8), then dropped."
  - Technical Details (`engDetails('llm')`): kv llm_used/model_ver + table `role | outcome | model | cache | latency | when` of all events (outcome chips colored ok/guard/unavail), footer "append-only `analyzer.llm_event`, newest 20 for this item · **all events → LLM tab**" (link `#view=llm`).
- **Banded confidence gauge** — hand-built inline SVG (260×150, 220° arc from 200° to −20°): three band arcs (abstain grey / suggest yellow / auto green split at tau_suggest & tau_auto from payload), ink needle + hub, big confidence number (2 decimals) + "confidence" caption, absolutely-positioned HTML threshold chips (`.45`-style, leading zero stripped) at the τ angles. **Provisional items**: gauge shows the *classical* decision; the LLM proposal is a dashed topaz radial tick + small "LLM proposal" label; caption note "needle = classical decision (in effect) · dashed tick = LLM proposed <label> @ <conf> (provisional — not applied)". Judge note when present: "LLM judge re-ranked the suggest candidates (order only — label and confidence untouched)."
- **Band legend**: three items with band dot + text `abstain · < TS → TI`, `suggest · TS–TA`, `auto · ≥ TA`; active band emphasized (`data-active`); tooltips BAND_TIP (exact copy about 0.75/0.45/tau semantics).
- **Abstain reason block** (only when band=abstain and `abstain_reason` stored): explanatory text per code (`no_confident_rule`, `gbm_below_suggest` — differs when predicted_group is ti, `gbm_boilerplate_only_neighbor`; unknown codes: "The analyzer abstained for reason "X" (code not yet documented in the Inspector).") + mono code chip.
- **Evidence groups**: section title `Evidence the [classical ]decision weighed (N of M signals, largest first) (feature vector)`. Feature vector grouped by group key; group display names: retrieval="similar past failures (retrieval)", history="this test's track record (history)", kb="known failure modes (kb)", grouping="this launch's failure pattern (grouping)", signal="log contents (signal)", llm="LLM extractor (llm)", discriminant="exact-detail agreement (discriminant)", other="uncatalogued (other)". Rows sorted by Σ|v| desc; each collapsible row (button head, aria-expanded): color dot (FEATURE_GROUP_COLORS), name, count chip, `top: <label> <val>`, Σ|v| microbar (normalized to max group), mono `Σ|v| <n>`, caret. Auto-expands the group containing the single largest |value|. Expanded body: per-feature rows sorted by |v| desc — label + mono key, bar normalized within group, value; tooltip `key = v · definition · range … · default …`. No features → note "No stored feature vector for this suggestion."
- Technical Details (`engDetails('decision')`): kv (classical rows when provisional: "rubric row: no feature vector — rubric path", classical model_ver), model_ver, method, abstain_reason, matched_item_id, matched_mode_id, llm_used, outcome, `snapshot extras` / `classical snapshot extras` (JSON of numeric snapshot keys outside the feature registry).

**Card 5 — Feedback** (step 5, sub "label_event log — append-only"):
- Empty: 📈 "No label events" / "…Events append on RP defect updates (rp), UI accepts (human), auto-apply (ai_suggested), seed catalog (seed)." tag `analyzer.label_event`.
- L1 glance sentence variants: "Labeled once: `x` set by <actor> — <time>.", "Relabeled once: `a` → `b` by <actor> — <time>.", "Label `x` re-confirmed by…", multi: "**Labeled N×**: [analyzer's `label` overridden — ]latest `old` → `new` by <actor> [*(correction)*] — <time>; first <time>.[ Showing the last 50 events (query cap).]"
- **Timeline** (`ol.tl`, vertical spine, oldest→newest, newest emphasized `.now` with accent tint): per event — colored dot (new_group label color), old defect badge (or muted "(first label)" w/ tooltip), `→` (tooltip explains append-only/current-label semantics), new defect badge, **source chip** (method-chip style: k + text from SOURCE_LABELS, tooltip raw source + src_weight), **suggestion chip**: when `suggestion_id` equals the on-page decision's it's a *button* `sug <id> ↑ stage 4` that scroll-pulses the Decision card; otherwise plain chip `sug <id>` (tooltip: "an older decision — not the one shown on this page"); right `<time>` relTime with full ISO in title. **Decay line** under each event (when weight known): "evidence weight now: src_weight W × decay D = E" with tooltip `decay(d) = exp(−ln2 · d/90), half-life 90 d…`; appends green " · trains the model" when weight ≥ 0.9.
- Cap line when exactly 50 events: "showing the latest 50 events (query cap)."
- Technical Details: raw table `event_id | old_label | new_label | source (raw) | suggestion_id | ts (ISO)` + footer note.

**API calls**: `GET api/launches?project=`, `GET api/items?project=&launch=`, `GET api/item/{project}/{itemId}/journey`. Journey response fields consumed: `rp{defects}`, `item{item_id,item_name,issue_type,label_group,is_auto_analyzed,ui_url,launch_id,launch_url,log_count,test_case_hash}`, `signature{exc_text,msg_text,top_frames[],template_ids[],status_codes[],exception_fp,error_hash,emb_model_ver,has_emb}`, `templates[{template_id,pattern,token_count,match_count,missing}]`, `grouping{group_id,fingerprint,member_count,dominant,si_prior,launch_failed_count,members[{item_id,label_group,issue_type,is_self}]}`, `matching{has_suggestion,stage,stage_label,stage_note,matched_item_id,matched_item_url,matched_mode_id,matched_mode{label,label_group,title,mode_id,purity,support,status,seed_key},suggestion_id}`, `reconstruction{hybrid_retrieval_version,note,query{salient_terms,template_ids,emb_model_ver,dense_active},candidates[{item_id,ui_url,issue_type,label_source,sparse_rank,dense_rank,cosine,jaccard_templates,rrf_score,is_self,exception_fp,error_hash,launch_number,mode_id}]}`, `decision{band,method,predicted_label,predicted_group,confidence,tau_suggest,tau_auto,explanation,model_ver,llm_used,outcome,abstain_reason,judge{choice,chosen_item_id},features[{key,label,value,group,definition,range,default}],feature_count,feature_total,extra_snapshot,coldstart_provisional,coldstart{rule},classical{...same shape...}}`, `llm{events[{role,outcome,model,cache_hit,latency_ms,created_at,prompt_hash,output{reason,rubric_rule_matched,failing_layer,error_class,root_exception}}],role_state}`, `feedback[{event_id,old_label,old_group,new_label,new_group,source,suggestion_id,ts}]` (newest-first, cap 50).

**Cross-view**: LLM eng-drawer links to `#view=llm`; grouping member dots navigate items within journey; RP deep links (`ui_url`/`launch_url`) open in new tabs.

---

### 2.2 Signatures (`views/signatures.js`)

**Purpose**: explorer of the fingerprint space — one row per distinct `error_hash`, exposing Stage-A hash-collision "label bleed".

**Controls**: search input (`type=search`, placeholder `Search exc / msg / frames (ILIKE)…`, min-width 300px, **debounced 250 ms**, resets offset & expansion, writes `q` to hash) with field label "Search signatures"; conflicts toggle (custom `.toggle`) labeled `⚠ only conflicts`, tooltip "Only error_hashes whose members carry >1 distinct non-ti label", field label "Filter", writes `conflicts` to hash.

**Summary chips** (from `d.summary`): counters `error_hash` (accent), `exception_fp`, `signed items`, `embedded` (good), `conflicts` (warn when >0).

**Table** in card "Fingerprint space" (sub dynamically set to `N error_hash(es)[ · conflicts only][ · matching "q"]`). Columns: `error_hash | members | labels | exception class | status codes | first / last seen`. Hash shortened `abcdef…wxyz` when >12 chars (full in title). Conflict rows: `.conflict` styling (warning left-inset + tint) + `⚠` flag (tooltip "label-bleed candidate: members carry >1 distinct non-ti label"). Labels cell: abbr defect badges + `×count`. Empty cells `—`. Empty table states (🧬 "No signatures") with three different bodies for conflicts-filter / search / genuinely-empty.

**Pager**: shown when offset>0 or count≥limit; `rows X–Y` text + `← prev` / `next →` chips (disabled/faded appropriately). Paging **re-renders the whole view** via `renderSignatures(document.getElementById('view'), app)`.

**Row expansion** (click toggles; only one open at a time; state written to hash as `hash=`; permalinked expansion auto-opens after render via microtask): inserts a `tr.sig-detail` spanning 6 cols, fetches `GET api/signature-hash?project=&error_hash=`. Detail body: full hash chip, `fp <exception_fp>` chip, `representative item <id>` link chip; conflict badge `⚠ label-bleed candidate` + note paragraph ("This error_hash carries N distinct non-ti labels across its M members. Stage A inherits labels via exact error_hash equality, so members here risk inheriting the wrong ground-truth label."); signature field rows (EXC/MSG/FRAMES/CODES badges); referenced Drain3 templates (same mini-cards as journey); "Member items (X[ of Y])" — rows with item link chip, abbr defect badge, truncated item name, "auto" badge + halo, launch link chip, indexed_at time; cap note "N more members not shown (capped at X)." Failure → 🚫 "Detail failed".

**API**: `GET api/signatures?project=&q=&conflicts=1&offset=` → `{rp, summary{distinct_error_hash,distinct_exception_fp,items_with_signatures,embedded,conflict_hashes}, rows[{error_hash,member_count,labels[{locator,group,count}],exc_classes[],status_codes[],first_seen,last_seen,is_conflict}], count, offset, limit}`; `GET api/signature-hash` → `{rp,error_hash,exception_fp,representative_item_id,is_conflict,labels,member_total,member_shown,signature{...},templates[],members[{item_id,ui_url,issue_type,label_group,item_name,is_auto_analyzed,launch_id,launch_name,launch_url,indexed_at}]}`.

---

### 2.3 Drain3 Explorer (`views/drain.js`)

**Purpose**: searchable mirror of the `log_template` table plus a d3 icicle approximating Drain's parse tree.

- Search input, placeholder `Filter patterns (ILIKE)…`, debounced 250 ms, field label "Search templates". Filter state `_q` is module-level (not permalinked).
- Two cards in `grid-2`: "Parse tree (icicle)" (sub "grouped by leading masked tokens — approximates Drain's tree") and "Templates" (sub set to `N template(s)` after load).
- **Table** columns: `id | pattern | tok | matches | last seen` — pattern rendered with `highlightPattern` (token highlighting: `<NUM>`, `<UUID>`, URL/PATH/IP/HEX/TOKEN/DATE/TIME/NUMBER, generic `<...>`, `*`, bare numbers, UUID literals — colored `.tok-*` spans). Wrapper capped at 520px with vertical scroll.
- Empty states: table 🌵 "No templates" (two bodies: filter vs no-data, tag `analyzer.log_template`); tree 🌳 "Nothing to tree".
- **Icicle (d3)** — see §6.

**API**: `GET api/templates?project=&q=` → `{templates[{template_id,pattern,token_count,match_count,last_seen}], count}`.

---

### 2.4 Modes Map 3D (`views/modes.js`)

**Purpose**: PCA projection (backend numpy SVD) of 384-dim failure_signature embeddings, with failure_mode centroids as stars/diamonds.

- Single card "Modes Map" (sub "PCA of 384‑dim embeddings · items ● / centroids ★"), **breaks out of the 1440px view cap**: width `min(100vw - 48px, 1800px)` centered via negative-ish margin calc.
- Unavailable: 🪐 "Embedding space not available yet" + backend-provided `reason`, tag `analyzer.failure_signature.emb (halfvec 384)`, plus diagnostics chips `signatures N`, `embedded N`, `mode centroids N`.
- Available: card sub replaced with `N items · M centroids · D-D · variance X% / Y% / Z%` (explained_variance_ratio). Legend row: 5 colored dots (pb/ab/si/nd/ti with resolved RP defect names) + "★ = mode centroid" + a **fullscreen** chip button (maximize icon; uses Fullscreen API on a wrapper `.modes-fswrap`; chart height `max(560px, 72vh)` normally, `calc(100vh - 24px)` in fullscreen; `fullscreenchange` listener adjusts). A `ResizeObserver` on the chart div calls `echarts.getInstanceByDom(chart).resize()`.
- **3D** when `dimensions >= 3` (echarts-gl `scatter3D`, see §6); **graceful 2D fallback** (`scatter`) when < 3, with note "Only N principal dimension(s) carry variance (the embedded set is small/degenerate), so the map falls back to 2D — it becomes 3D once more distinct embeddings exist."
- Tooltip (both): for modes `★ <name> / label … · status … / purity … · support …`; for items linked `item <id> ↗` (RP deep link, `enterable: true` so the link is clickable) + name + label + `· ⭑ auto` marker.

**API**: `GET api/modes3d?project=` → `{available, reason?, diagnostics{total_signatures,embedded_signatures,modes_with_centroid}, rp, n_items, n_modes, dimensions, explained_variance_ratio[], points[{kind:'item'|'mode',x,y,z,label,label_group,item_id,name,ui_url,is_auto_analyzed,purity,support,status}]}`.

---

### 2.5 Launch Groups (`views/groups.js`)

**Purpose**: d3 force-directed co-failure graph — items clustered by launch_group; node color = final label; accent halo = auto-analyzed.

- Launch `<select>` (field label "Launch"): first option "All launches" (value ''), then `<launch_name || 'launch'> · #<number ?? —> (<item_count>)`. Selection permalinked (`glaunch`, serialized as `launch`).
- Layout: grid `minmax(0,1fr) | 320px` — "Co‑failure graph" card (sub "items clustered by launch_group") + "Groups" legend card.
- **Group list** (right): per-group mini cards — chip `group <id>`; burst badge `🔥 burst · si <n>` (warning) when `dominant`, else muted `si <n>`; second row: `launch <id>` link chip, `<member_count> members`, `fp <first8>…`. Empty: "No launch_group rows."
- **Graph** (see §6 for d3 detail). Legend row above the svg: 5 label dots + names, "◯ accent ring = auto‑analyzed", right-aligned hint "wheel: zoom · drag bg: pan", and a **fit** chip button (cycleArrows icon, "Fit graph to view") which resets `userTouched` and refits.
- Empty nodes: 🕸️ "No items" / "No test items for this selection." tag `analyzer.test_item`.

**API**: `GET api/launches?project=`, `GET api/groups?project=&launch=` → `{rp, groups[{group_id,launch_id,launch_url,dominant,si_prior,member_count,fingerprint}], nodes[{item_id,group_id,issue_type,label_group,is_auto_analyzed,name,ui_url}]}`.

---

### 2.6 Learning Loop (`views/loop.js`)

**Purpose**: training-loop observability — label events over time, model artifact versions, daily metrics, project maturity band, and optional live analyzer /health.

Layout: "Label events" card full width; then `grid-side` (1.7fr/1fr): "Model artifacts" + sticky "Analyzer /health" side panel; then "Daily metrics"; then "Project maturity" (sub "cold-start → warm → hot · training-frame contribution"). One `GET api/timeline?project=` feeds all but health.

- **Label events** (sub "ground-truth transitions over time"): ECharts **time × category scatter** (260px): x = time axis, y = category `['pb','ab','si','nd','ti','none']` (labels via defectName), symbolSize 15, per-point color = defectColor(new_label), white 1px border. Tooltip `enterable`, custom HTML: linked `item <id> ↗` + `old → new` transition text (`Name (locator)` format) + `source · time`. Below: last-12 transition list (newest first): item link chip, old badge or "(new)", →, new badge, source chip, time. Empty: 📈 "No label events" tag `analyzer.label_event`.
- **Model artifacts** (sub "versions · gate metrics · active swap"): cards per artifact — kind chip, mono version, muted scope; `● active` accent badge (accent border) or trained_at time; up to 6 metric chips (`k` muted + value fmt 3); footer `schema v<feature_schema_ver> · trained on <n_events> events`. Empty: 🧱 "No model artifacts" ("…cold-start / rule mode…") tag `analyzer.model_artifact`.
- **Daily metrics**: ECharts **stacked area line chart** (300px): series accepted (band-auto green) / corrected (warning) / abstained (band-abstain grey) / ignored (muted); areaStyle opacity 0.5, no symbols, axis-trigger tooltip, legend top. Empty: 📊 "No daily metrics" (nightly rollup note) tag `analyzer.metrics_daily`.
- **Project maturity**:
  - Stepper of 3 `.stage-pill`s: `COLD · rules decide` (`0–49 labeled items`), `WARM · GBM training frame` (`50–299`), `HOT · per-project calibration` (`≥ 300`) — ranges computed from payload thresholds (`gbm_min_events`, `calib_min_events`); active pill gets `● this project`.
  - Progress meter (`si-meter` reuse): label "training-frame position", value `N labeled item(s) · <M to Warm | M to Hot | Hot band reached>`, fill % = labeled/hot, colored by stage (hot=good, warm=band-suggest, cold=band-abstain), tick at warm threshold, scale `0 / 50 · Warm / 300 · Hot`.
  - 3-column stage cards (`.mat-stage`, active highlighted with "current" tag): long "who decides" copy + "Key:" line (verbatim strings in MAT_STAGES, loop.js 157–178).
  - Stat row: label_events (+"N human-sourced"), labeled items ("distinct · training frame"), `X / Y` KB modes ("confirmed / candidate"), embedded coverage % ("X of Y signatures").
  - **Honesty guards** (`.honesty` block "reality check") — up to 4 conditional notes: zero labels; hot-by-volume-no-calibrator; calibrator-but-0-confirmed-modes; warm-with-candidate-only-modes (exact copy at loop.js 249–265).
  - Technical Details: band metric definition, `GBM_MIN_EVENTS = 50` / `CALIB_MIN_EVENTS = 300` with source-file citations, install-wide GBM version + trained-events, project calibrator or "none — serving install-wide / raw calibration".
  - Missing maturity block: 📊 "Maturity unavailable".
- **Analyzer /health** side card: `GET api/analyzer-health`. Not configured → 🩺 "Health probe not configured" ("Set ANALYZER_HEALTH_URL…", tag `env: ANALYZER_HEALTH_URL`); unreachable → 🔌 "Analyzer unreachable" + error; ok → pretty-printed JSON `<pre>`. Catch → 🩺 "Health unavailable".

**API**: `GET api/timeline?project=` → `{rp, label_events[{item_id,ui_url,ts,old_label,old_group,new_label,new_group,source}], model_artifacts[{kind,version,scope,is_active,trained_at,metrics{},feature_schema_ver,n_events}], metrics_daily[{day,accepted,corrected,abstained,ignored}], maturity{stage,labeled_items,gbm_min_events,calib_min_events,label_events,human_events,modes_confirmed,modes_candidate,embedded,signatures,embedded_pct,install_gbm,install_gbm_events,project_calibrator,project_calibrator_events}}`; `GET api/analyzer-health`.

---

### 2.7 LLM (`views/llm.js`)

**Purpose**: per-project LLM-sidecar observability from spec-04 tables (llm_event / llm_cache / llm_role_state), read-only, honesty-first.

Header: h2 "LLM", note "llm_event · llm_cache · llm_role_state — read-only bookkeeping", optional "model `<model>` — from llm_event rows; the Inspector reads the DB, not live sidecar health." Zero events overall → single card with 🤖 "No LLM activity" (long body), tag `analyzer.llm_event`. Otherwise six cards:

1. **Roles at a glance** (sub "the four async roles, project-scoped"): auto-fit grid of role panels (coldstart/explainer/judge/extractor) with glosses (ROLE_GLOSS) — zero-event panels dashed (`.empty`) with "**0 events** — role enabled by default, wired, never fired in this project." + trigger contract note (judge has a special one). Non-zero: big ok counter, non-ok outcome chips (`schema_fail 2` etc., colored ok/guard/unavail), `cache N` chip, "last event <relTime>". Each panel ends with a **state line**: green dot "on (default — no llm_role_state row)" (tooltip about the nightly eval, N ≥ 50), warning dot "disabled — <reason> <when>", or green "on (re-enabled…)".
2. **Outcome mix** (sub "guardrails as signal — a rejection means the validator worked"): takeaway "**N LLM calls** · X% ok · G rejected by guardrails · U unavailable — rejections mean the validator worked, not that the pipeline erred."; per-role stacked bars (ok/guard/unavail proportional widths, color contract: ok=`--rp-status-passed`, guard=`--rp-topaz`, unavail=`--rp-sm-warning`); 3-group legend ("result: ok", "guardrail fired (output rejected): schema_fail / validation_fail", "sidecar unavailable: timeout / breaker_open / dropped").
3. **Latency** (sub "wall-clock per call, ok events only, cache hits excluded"): per role a track bar — fill = p50, tick = p90, all normalized to global max; numbers "p50 **x** · p90 **y** · max **z** · n N"; roles without latency show `—`. `latFmt`: null→'—', 0→'cache', ≥1000→'1.2 s', else 'N ms'.
4. **Activity stream** (sub "newest-first, cap 50"): filter chip rows — role: all/coldstart/explainer/judge/extractor; outcome: all/ok/guardrail/unavailable (`.fchip`, `.on` active). Filters permalinked as `lrole`/`loutcome`. List rows (`ol.llm-tl`): outcome dot, role, **item link chip navigating to `#view=journey&project=&launch=&item=`** (cross-view deep link!), outcome chip, model chip, optional "cache" chip, optional `<details class="out-drawer">` "output" with pretty JSON (**untrusted model text — always textContent, never innerHTML**); right latency · relTime. Cap note when count==limit. Empty: 🤖 "No events for this filter" (judge gets its special explanation). Errors → 🚫 "Events failed".
5. **Availability** (sub "breaker state is in-process on the analyzer — not persisted, not shown as fact"): honesty block "in-process state — not in the DB" + explanation ("A quiet log means either healthy-and-idle or disabled — the Inspector cannot distinguish."); stat row: last llm_event / breaker_open events / transport timeouts; drawer "Breaker limits (code contract, not runtime)" — "Opens after 3 consecutive transport failures; cooldown 60 s doubling to a 15 min cap; the first job after cooldown is the half-open probe. Schema / validation failures never trip it… From breaker.py; reset on analyzer restart."
6. **Extractor cache** (sub "one model call per template-set per project, then reuse"): takeaway "**N cached extractor outputs** · M hits — hits count role-call reuse only; feature-time reads are not counted, so real reuse is higher. Rows marked "no answer possible" hold a remembered failure, not an extraction, and are kept for 7 days."; table `template_hash | model | hits | fresh | created | last hit | output` — negative-cache rows get `.row-negative` styling and a `no answer possible` tag instead of a hash; fresh column "fresh"/"expired"; output JSON drawers. Empty rows → note.

**API**: `GET api/llm/summary?project=` → `{model, event_total, roles[{role,event_count,ok,outcomes{},from_cache,last_event,latency{p50,p90,max,n},state{enabled,reason,decided_at}}]}`; `GET api/llm/events?project=&role=&outcome=&limit=50` → `{events[{role,outcome,model,cache_hit,item_id,launch_id,latency_ms,created_at,output}],count,limit}` (note: `role`/`outcome` values `all` ARE sent as query params — the backend treats them; frontend passes them verbatim); `GET api/llm/cache?project=&role=extractor&limit=50` → `{entries,total_hits,rows[{template_hash,model,hits,fresh,created_at,last_hit_at,output,negative}]}`.

---

### 2.8 Cold-start Rules (`views/rubric.js`)

**Purpose**: reference table of the analyzer's cold-start rubric rules exactly as applied — first matching rule wins.

- Loading text "Reading the rubric…". Error → warning icon "Could not read the rubric". Empty → info icon "No rubric available" / "The analyzer rubric file was not found in this build."
- Intro paragraph (verbatim): "A project with no decided failures has nothing to match against. The analyzer reads the error and applies the first rule below that fits, then reports which one it used. These guesses are never applied on their own: they are offered, and a person decides."
- Card "Cold-start rules", sub `N rules, applied in order`. Table (`.rubric-table`, horizontal-scroll wrapper) columns: `# | Rule | What it looks for | Always produces`. Row: order number; rule name + mono rule id (`<code class="rubric-id">`); `when` text; verdict = labelBadge (group mapped pb/ab/si/nd, else 'none') with `label_name`, plus confidence word with gloss — high "— the pattern is unambiguous", med "— the pattern usually holds", low "— the closest rule, little more".
- **Deep link**: `#view=rubric&rule=R6` → row `id="rule-R6"` gets `.rubric-row-focus` (topaz tint) and `scrollIntoView({block:'center'})`. Rule id comes through `setRubricState` (the router hands it in; reading location.hash at render time would find it already rewritten).

**API**: `GET api/rubric` (no params) → `{rules[{rule,order,name,when,label,label_name,confidence}]}`.

---

## 3. Shared modules

### util.js (every export)
- `LABEL_COLORS` / `LABEL_NAMES` — group→color (resolved from CSS vars at load) and group→name maps (pb "Product bug", ab "Automation bug", si "System issue", nd "No defect", ti "To investigate", none "Unlabeled", other "Other"). `labelColor(g)`, `labelName(g)`.
- Resolved-hex color constants (ECharts canvas can't read CSS vars): `INK, INK2, MUTED, HAIRLINE, SURFACE, ACCENT, WARNING, GOOD`, `BAND{auto,suggest,abstain}`, `FEATURE_GROUP_COLORS{retrieval,history,kb,grouping,signal,discriminant,llm,other}`.
- **RP defect-name resolution**: `setDefects(map)` (locator→{name,short_name,color}), `defectInfo(locator)`, `labelGroup(locator)` (prefix match pb/ab/si/nd/ti else 'other'/'none'), `defectForGroup(g)`, `defectColor(locator, group)`, `defectName(locator, group)` (group-level labels get "`<Name> group`" suffix — never borrows a concrete type name), `defectBadge(locator, group)` (RP-style pill: white fill, e-200 border, radius 100px, 12px color dot, name in ink; bare group codes render as a plain chip "`<Name> group`" with tooltip, NOT a defect pill), `defectBadgeAbbr` (compact — RP abbreviation or PB/AB/SI/ND/TI fallback, 10px dot).
- `renderWithDefectNames(text)` — tokenizes untrusted prose around exact known locators (longest-first, word-boundary guarded on `[A-Za-z0-9_]`) and returns a DocumentFragment of text nodes + inline `.defect-inline` pills (name shortened to short_name when >18 chars); never innerHTML.
- `idChip(label, url, cls='chip')` — RP deep-link anchor (`target=_blank rel=noopener`, class `idlink`, title "Open in ReportPortal ↗") or plain span when no url.
- `h(tag, attrs, ...children)` — hyperscript; special attrs: `class`, `html` (innerHTML!), `style` object, `on*` listeners, `dataset`; children flattened, strings/numbers → text nodes, null/false skipped. `clear(el)`.
- `labelBadge(group, text)` — `.badge.lbl.<g>` pill with dot.
- Formatters: `fmt(n, digits=3)` (null→'—', ints localized, floats fixed), `pct(n, digits=1)` (×100 + '%'), `shortTime(iso)` (`Mon D, HH:MM` locale), `relTime(iso)` ('just now'/'Nm ago'/'Nh ago'/'Nd ago'/shortTime; >30 d → shortTime), `esc(s)` (HTML-escapes `&<>"`).
- `highlightPattern(pattern)` — returns HTML string with `.tok.tok-{num,uuid,url,wild}` spans; null → `<span class="muted">— pattern unavailable —</span>`.
- `SOURCE_LABELS` / `srcInfo(token)` — provenance vocabulary for both label_event.source values and feature-vector label_source tokens, each with `{k, text, plain, actor, weight}` (rp_defect_update/rp: weight 1.0; analyzer_suggestion_accepted/human_ui/human: 0.9; ai_suggested: 0.3; seed: 0.6). Unknown tokens pass through honestly.
- Hash helpers: `readHashParams()`, `writeHashParams(params)` (see §1).
- `toast(msg)` (2200 ms), `icon(name, {size=16,title,cls})` (RP_ICONS lookup, `info` fallback, inline SVG innerHTML), `EMOJI_ICON` map (legacy emoji → icon names — 🚫→error, 🩺/🔌→warning, 🧭/🔍→search, 📭→launchType, 🪐→diamond, 🕸️/🌳→tree, 🌵/🔤→details, 📈/📊→latestExecutions, 🧱→jar, 🗄️→moveToFolder, 🎯→checkmark, 🧠→info, 📄→fileOther; unmapped emoji render literally, e.g. 🕳️ 🧩 🧬 🤖).
- `emptyState(iconOrEmoji, title, body, stageTag)` — icon + h4 + p + optional mono `.stage-tag`.
- `loading(text='Loading…')` — spinner + text.
- `card(title, opts{step, sub, right, class}, ...body)` — card with head (optional step number square) + body.
- `engDrawer(key, summaryText, ...children)` / `engDetails(key, ...children)` — native `<details class="eng">` with `▸` caret; **open-state persisted in `localStorage['inspector.eng.<key>']`** ('1'/'0'), restored on render; storage failures swallowed. Keys in use: grouping, matching, matching-recon, decision, llm, llm-breaker, feedback, maturity.
- `echartsBase()` — shared ECharts option: Roboto textStyle in INK2, default grid, white tooltip card with hairline border + `0 8px 40px rgba(0,0,0,.15)` shadow, radius 8, `confine: true`.

### api.js
`get(path)` — fetch with `Accept: application/json`; non-ok → throws `Error("<status> <detail>")` where detail comes from the JSON body's `detail` field if parseable. All URLs are **relative** (`api/...`) so they resolve against the injected `<base href>`. `qs(o)` drops null/'' values. Exported `api` methods: `projects()`, `rp(project)`, `launches(project)`, `items(project, launch)`, `journey(project, itemId)` (path params), `rubric()`, `templates(project, q)`, `modes3d(project)`, `groups(project, launch)`, `timeline(project)`, `summary(project)`, `signatures(project, q, conflicts, offset)` (conflicts serialized as `1` or omitted), `signatureHash(project, errorHash)` (`error_hash` param), `analyzerHealth()`, `llmSummary(project)`, `llmEvents(project, role, outcome, limit)`, `llmCache(project, role, limit)`.

### rp-icons.js
`RP_ICONS` — 17 vendored ReportPortal ui-kit SVG icons normalized to `currentColor`, each `{vb: viewBox, body: inner SVG markup}`: `error, warning, info, search, bolt, maximize, arrowRight, cycleArrows, diamond, tree, details, jar, latestExecutions, launchType, moveToFolder, checkmark, fileOther`. (Note: `error`'s path has hardcoded `fill="white"` — renders white-on-transparent.)

---

## 4. CSS (`static/css/app.css`, 709 lines)

### Design-token block (`:root`, light-first, `color-scheme: light`)
**RP tokens**: `--rp-bg-000:#ffffff; --rp-bg-100:#f7f7f8; --rp-bg-200:#eceff4; --rp-topaz-100:#e5f5f8; --rp-topaz:#00829b; --rp-topaz-hover:#009dbb; --rp-topaz-focused:#00b0d1; --rp-topaz-pressed:#00758c; --rp-almost-black:#3f3f3f; --rp-charcoal:#464547; --rp-e-100:#e3e7ec; --rp-e-200:#c1c7d0; --rp-e-300:#a2aab5; --rp-e-400:#8d95a1; --rp-error:#dc5959; --rp-sm-error:#db3549; --rp-yellow-800:#ffc208; --rp-yellow-700:#d4a002; --rp-sm-warning:#d78706; --rp-status-passed:#3aa76d; --rp-status-skipped:#b8b8b8; --rp-shadow-rgb:55,67,98; --rp-shadow:0 1px 3px rgba(var(--rp-shadow-rgb),.1); --rp-shadow-hover:0 1px 3px rgba(...,.2); --rp-shadow-secondary:0 8px 40px rgba(0,0,0,.15); --rp-tooltip-bg:rgba(34,34,34,.91); --rp-font:Roboto,Arial,Helvetica,"Segoe UI",sans-serif; --rp-mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace`.

**Compatibility aliases** (old dark names → RP light): `--plane, --surface-1/2/3, --hairline(-2), --ink, --ink-2, --muted, --accent, --accent-soft, --good, --warning, --serious, --critical`. **Band semantics**: `--band-abstain:var(--rp-e-300); --band-suggest:var(--rp-yellow-700); --band-auto:var(--rp-status-passed)`. **Defect dot colors**: `--lbl-pb:#d32f2f; --lbl-ab:#ffc208; --lbl-si:#3e7be6; --lbl-nd:#76839b; --lbl-ti:#00829b; --lbl-none:var(--rp-e-400)`. **Radii/shadows/fonts**: `--radius:4px; --radius-s:8px; --shadow; --mono; --sans`.

### Global patterns
Body: Roboto 13px/20px on `--rp-bg-100`, antialiased. Links topaz, no underline. `.view`: max-width 1440px, centered, 22/24/60 padding, 0.2 s fade-in animation. `color-mix(in srgb, ...)` used pervasively for tints. `prefers-reduced-motion` disables caret transitions (and views skip pulse animations).

### Notable components
- **Cards**: white, radius 4, `--rp-shadow` (no border); head with 16px padding + hairline bottom; `.step-num` 24px topaz square.
- **Tables** (`table.data`): "floating white row-cards on a grey inset" — wrapper grey bg + hairline border + radius 8; `border-spacing: 0 4px`; td white with hover-shadow, rounded first/last cells; th muted 11px; `.num` right/tabular/mono; hover row grey; `.is-self` topaz tint.
- **Chips/badges**: `.chip` mono 12px grey pill radius 6; `.badge` 100px-radius pill w/ 8px dot; `.lbl` white defect pill with 12px dot (`--c` per group); `.field-badge` uppercase neutral; `.idlink` link affordance (accent+underline on hover, chip version gets accent border/soft bg); svg idlink styling for graph labels.
- **Toggle**: 34×19 track, 15px thumb, checked → accent-soft track + accent thumb translated 15px.
- **Tabs**: transparent buttons, 2px bottom border on active, focus-visible accent outline.
- **Stepper/stage-pill**: joined bordered segments with circular arrow puck overlapping right edge (`.s-flow`), active = surface-3 + 2px accent bottom bar.
- Microbars, si-meter (track/fill/tick/scale), gauge threshold chips, band legend, method chips, abstain-reason (grey band left border), evidence rows (7-col grid head, responsive collapse at 720px), funnel (won inset-shadow, skip dashed, column layout at 720px), candidate strip (3-col → 1-col at 860px), feedback timeline spine (1px vertical line + dots), LLM strip/tiles/quotes/guard-note (topaz left borders), role panels (dashed when empty), outcome bars/legend, latency tracks, fchips (pill filters, `.on` topaz), honesty callout (grey left border), stat rows, llm activity rows, out-drawer details, `pre.out-json`, `.defect-inline` prose pills, rubric table (plain collapsed borders — note: references undefined `var(--line)` and `--focus-bg` fallback `rgba(0,130,155,0.08)`), sig-row conflict styling, sig-detail panel (accent left border), `.halo-auto`, `.modes-fswrap:fullscreen`, toast, spinner, list picker (`.list` max-height 460px scroll, `.list-item` active accent), `.grid-side` (1.7fr/1fr, `.side-panel` sticky top:112px), responsive single-column at 1040px.

---

## 5. Backend API surface (`backend/app.py`)

FastAPI app, title "analyzer-ng inspector" v0.1.0, docs at `/api/docs`, openapi at `/api/openapi.json`. Config from env (`backend/config.py`): `INSPECTOR_PG_DSN`, `INSPECTOR_RP_PG_DSN` (optional RP-names resolver), `INSPECTOR_HTTP_PORT` (5005), `INSPECTOR_ROOT_PATH`, `ANALYZER_HEALTH_URL`, `INSPECTOR_QUERIES_PATH`, `INSPECTOR_QUERY_LIMIT` (500).

Routes (all GET, read-only):
| Route | Params | Response |
|---|---|---|
| `/healthz` | — | `{status, service, version}` (also a top-level probe when mounted) |
| `/api/health` | — | `{db_reachable, hybrid_note}` |
| `/api/rp` | `project` (required int) | RP name block: `{defects:{locator:{name,short_name,color}}, status:{configured,reachable,note}, ...}` |
| `/api/projects` | — | `{projects:[{project_id,project_name,item_count,launch_count}], rp_status}` |
| `/api/launches` | `project` | `{launches:[{launch_id,launch_name,launch_number,item_count,labeled_count}]}` |
| `/api/items` | `project, launch` | `{items:[{item_id,item_name,exc_text,issue_type,label_group,is_auto_analyzed,ui_url}]}` |
| `/api/item/{project}/{item_id}/journey` | path params | full journey payload (see §2.1); **404 `{detail:"item not found"}`** when absent |
| `/api/rubric` | — | `{rules:[...]}` |
| `/api/templates` | `project, q?` | `{templates:[...], count}` |
| `/api/modes3d` | `project` | PCA payload (see §2.4) |
| `/api/groups` | `project, launch?` | `{groups, nodes, rp}` |
| `/api/timeline` | `project` | `{label_events, model_artifacts, metrics_daily, maturity, rp}` |
| `/api/summary` | `project` | topbar counters `{suggestions,abstains,accepted,corrected,...}` |
| `/api/signatures` | `project, q?, conflicts=false, offset≥0` | `{rp,summary,rows,count,offset,limit}` |
| `/api/signature-hash` | `project, error_hash` | detail payload; **404** "error_hash not found" |
| `/api/llm/summary` | `project` | roles/outcomes/latency summary |
| `/api/llm/events` | `project, role?, outcome?, limit=50 (1–200)` | `{events,count,limit}` |
| `/api/llm/cache` | `project, role="extractor", limit=50 (1–200)` | `{entries,total_hits,rows}` |
| `/api/analyzer-health` | — | `{configured:false}` \| `{configured:true,reachable:true,health:{...}}` \| `{configured:true,reachable:false,error}` (3 s urllib timeout) |

**Static & base-path handling** (critical for the rewrite):
- `/static` is mounted via `StaticFiles` **after** all API routes (so `/api` wins). A middleware sets `Cache-Control: no-cache` on any path containing `/static/`.
- `index.html` is read at startup and the literal `<!--BASE-->` comment (line 5 of index.html) is replaced with `<base href="{root_path}/">` (or `/`). All SPA URLs (CSS, vendor scripts, module scripts, `api/...` fetches) are relative and resolve under this base — same bundle works at `/` (dev) and `/inspector/` (ingress, which does NOT strip the prefix).
- When `INSPECTOR_ROOT_PATH` is set, `create_app` wraps the FastAPI app in a Starlette app: top-level `Route("/healthz")` for container probes + `Mount(root_path, app=inner)`. The React rewrite must preserve this base-href-driven relative-URL model (or equivalent).

---

## 6. d3 and echarts-gl usage (replacement-decision detail)

### d3 use site 1 — Launch Groups force graph (`views/groups.js` `renderForce`)
Draws an SVG (viewBox `0 0 width 560`, width from container) of item nodes clustered by launch_group. Links are synthesized client-side: within each group, a star from the first member to every other (`arr[0] → arr[i]`).
- **d3 APIs used**: `d3.create('svg')`, selections/`data().join()`, `d3.forceSimulation` with `d3.forceLink` (id accessor `item_id`, distance 46, strength 0.5), `d3.forceManyBody` (−160), `d3.forceX` (per-group x-anchor: groups evenly spaced across width, strength 0.35), `d3.forceY` (center, 0.06), `d3.forceCollide(20)`; `d3.drag` on nodes (with `sourceEvent.stopPropagation()` so drag doesn't pan; alphaTarget 0.3 restart / fx,fy pinning); `d3.zoom` (`scaleExtent [0.1, 8]`) on the svg, transform applied to a single root `<g>`; `d3.zoomIdentity` for programmatic **auto-fit** (computes node bbox, pads 40, cap scale 2, re-fits every 20 ticks and on simulation end until the user interacts — `userTouched` flips only when `ev.sourceEvent` exists).
- **Rendering per node**: circle r=11 filled with defectColor, stroke = accent 3px when auto-analyzed else white 1.2px; native SVG `<title>` tooltip (multi-line: item id, name, label, group, ⭑ auto flag, ↗ hint); item-id text label wrapped in an SVG `<a class="idlink">` deep-linking to RP (pointer-events only on linked text so it doesn't block node drag). Links: hairline lines, opacity 0.7.
- **Interactions**: node drag (pins during drag), wheel zoom, background-drag pan, fit button.

### d3 use site 2 — Drain3 icicle (`views/drain.js` `renderIcicle`)
Builds a token-prefix tree client-side (first 4 whitespace tokens of each pattern, counts = match_count) then draws a **horizontal icicle** (partition layout, root depth hidden).
- **d3 APIs used**: `d3.create('svg')`, `d3.hierarchy(data).sum(...)` (leaves count, internal 0, fallback 1) `.sort(by value desc)`, `d3.partition().size([height, width])` (note: transposed — x is vertical), `d3.scaleSequential([0,4], t => d3.interpolateBlues(0.35 + t*0.14))` for depth color, selections. Cells: `<rect>` (1px gutters, rx 3) with native `<title>` tooltip (`ancestor token path \n N matches`); `<text>` label at x=6 vertically centered, 10px mono, only when cell height > 14px, truncated to `floor(cellWidth/7)` chars with `…`; `pointer-events: none` on text.
- **No zoom/click interactions** — static with native tooltips.

### echarts-gl use site — Modes Map 3D (`views/modes.js` `draw3d`)
The **only** echarts-gl consumer. `scatter3D` series (one per label group, colored via defectColor, symbolSize 9, opacity 0.9) plus a centroid series (`symbol: 'diamond'`, size 20, per-point color, white border). `xAxis3D/yAxis3D/zAxis3D` named PC1/PC2/PC3; `grid3D` with `viewControl: { autoRotate: true, autoRotateSpeed: 6, distance: 190 }`, white environment, hairline axis/split lines. Rich HTML tooltip with clickable RP link (`enterable: true`). The 2D fallback path (`draw2d`) uses plain ECharts `scatter`.

### Other custom visualizations (no library)
- **Confidence gauge** (journey): hand-rolled SVG via `document.createElementNS` — banded 220° arc (polyline approximation, ~90 segments per unit), needle, hub, value text, dashed LLM-proposal tick, HTML threshold chips. Pure geometry, framework-agnostic.
- Pure-DOM/CSS: matching funnel, member dot-strip, si-meter, microbars, RRF bars, evidence bars, outcome stacked bars, latency tracks, maturity meter, feedback timeline.
- **ECharts (2D, core lib)**: Learning Loop label-event scatter (time × category) and stacked-area daily metrics; Modes 2D fallback scatter. All use `echartsBase()` white tooltip styling and resolved hex colors.

### Vendored libs
`static/vendor/echarts.min.js`, `echarts-gl.min.js`, `d3.min.js` — loaded as classic scripts in `<head>` (globals `echarts`, `d3`); app code is ES modules (`type="module"`).
