# Demo-Data Coverage Plan — Analyzer Pipeline Lens

Consilium deliverable. Lens: **every mechanism in `specs/03-pipeline.md` must fire at least
once on the demo corpus AND be measurable** (we can point at the `suggestion` row / group row /
metric that proves it fired). Grounded in thresholds from the spec:

| knob | value | knob | value |
|---|---|---|---|
| ERROR level filter | `>= 40000` | near-dup drop | TF-cos `0.95`, keep last |
| logs cap / item | last 20 | Drain `sim_th` | 0.4, first 40 lines, stack frames skipped |
| group cosine `θ_group` | 0.83 | merge Jaccard | 0.5 |
| burst | new fp, `n ≥ 5`, `> 40%` of launch failures | `si_prior` | `min(0.9, 0.5 + share)` |
| Stage A guards | ≥2 unanimous OR 1 human `conf ≥ 0.9`; age ≤ 180d; never ti / auto-nd | Stage A out | `method=hash`, conf 0.95 |
| KB strong match | `score_mode ≥ 0.80` confirmed | KB short-circuit | confirmed, purity ≥ 0.95, support ≥ 10, score ≥ 0.85 |
| decision bands | `τ_auto = 0.75`, `τ_suggest = 0.45` | cold model | `< 50` label_events install-wide → rule fallback |
| per-project isotonic | ≥ 300 label_events | retrain trigger | every 100 events / nightly |
| Stage A skip | `exception_fp = 0` | launch_boost | 1.1 |

## 0. Corpus skeleton (the axis everything hangs on)

Three ReportPortal projects — the maturity axis is itself a coverage requirement, because the
decision layer branches on label-event counts:

| project | role | label_events target | what it proves |
|---|---|---|---|
| **DEMO-MATURE** | rich history | **≥ 320 human-sourced** (clears 300 → per-project isotonic) | GBM path, isotonic calibration, KB confirmed modes, Stage A |
| **DEMO-MID** | just enough for GBM | **~80** (clears 50 install threshold, gives ≥ 60/project for training signal) | GBM trained but install-wide calibration only; margins/abstain band exercised |
| **DEMO-COLD** | cold start | **< 20** | rule-based fallback: Stage A → KB short-circuit → seed prior ≥ 0.7 → abstain |

Sizing (sum of the tree below, with slack): **~45–55 launches, ~1,400–1,800 failed test items,
~430 label_events install-wide**. Upload order matters: history launches first (weeks-old
timestamps where the RP importer allows), then "today's" probe launches whose analysis we score.
Every probe node below states the *expected analyzer outcome* — that is the assertion the demo
runs against `suggestion` rows.

---

## 1. Deviation tree

Notation per node: **Name** — mechanism targeted — volume — **expected outcome**
(auto-label X / suggest / abstain-ti). `H` = history (pre-labeled via defect_update events),
`P` = probe (arrives unlabeled, analyzer must decide).

### Branch A — Ingest & preprocessing (§1.1–1.3, §3.4)

| # | node | mechanism | volume | expected outcome |
|---|---|---|---|---|
| A1 | **Noise-heavy item**: 30 logs — 10 INFO/WARN (< 40000), 5 near-dups at ~0.96 TF-cos, 22 ERROR | level filter + `find_last_unique_texts` + cap 20 | 3 items, inside any DEMO-MATURE launch | exactly last ≤ 20 unique ERROR logs indexed; downstream decision unaffected vs clean twin item (pairing makes it measurable) |
| A2 | **Zero-error item**: failed status, only INFO/DEBUG logs | empty-signature path | 4 items across 2 launches | never analyzed; **abstain, reason `no_error_logs`**; absent from analyze reply |
| A3 | **Many tiny logs**: 8 logs < 100 chars, no stacktrace | merged_small_logs pseudo-log, `is_merged_small_logs=1` (feature 37) | 6 items (3 H labeled `ab`, 3 P) | P items retrieve their H twins; suggest or auto-`ab`; feature 37 = 1 in stored vector |
| A4 | **Filth gauntlet**: raw logs with leading timestamps, log levels, thread ids, markdown fences, HTML, giant encoded URLs, `$Proxy42`/`$$EnhancerBy` frames, tokens, UUIDs, hex | the whole ported `text_processing` chain + masking belt (§2.1) | 5 items — same failure as a clean twin, dirtied | **same `error_hash` as clean twin** → Stage A fires despite filth |
| A5 | **Non-English logs**: Cyrillic/German assertion messages + test names | multilingual e5 embedding; FTS behavior on non-English | 6 items (4 H `pb`, 2 P) | dense retrieval carries it where FTS is weak → suggest/auto `pb`; also feeds node D3 (dense-vs-lexical) |
| A6 | **1 MB pathological log** | signature 2000-char cap; truncation order MSG→TEMPLATES→FRAMES; TEST/EXC preserved | 1 item | indexed, signature ≤ 2000 chars, still matches its mode (EXC survives truncation) |

### Branch B — Drain3 templates & identity (§2)

| # | node | mechanism | volume | expected outcome |
|---|---|---|---|---|
| B1 | **Parameter-only variants** (MUST group): same failure with different ids, ports, IPs, timings, UUIDs, order numbers — e.g. `Order 8842 not found on node 10.0.3.17:9443 after 3021 ms` | masking → identical `template_hash` set → identical `error_hash` | 12 items spread over 3 launches + 5 H labeled `pb` | one fingerprint; probe items **auto-label `pb` via Stage A (`method=hash`)**, all 12 share the hash |
| B2 | **Template widening**: a message family whose variable position forces Drain wildcard update mid-corpus | `template.superseded_by` chaining; old signatures keep old template rows | ~15 items streamed across 4 launches | two template rows linked by `superseded_by`; retrieval still connects the family via EXC/embedding (suggest, not miss) |
| B3 | **Cluster-count pressure**: long-tail of ~120 distinct one-off message shapes in DEMO-MATURE | Drain tree growth well below `max_clusters=4096`; per-project instance isolation (same texts also sent to DEMO-COLD) | 120 items over history launches | distinct `template_hash` per project namespace; no cross-project template bleed (check `template.project_id`) |
| B4 | **Stacktrace-heavy log**: 60-frame Java trace + 2 message lines | frame lines skipped by Drain (handled by `exception_fp`), 40-line cap | 3 items | few templates mined (message lines only); `FRAMES:` populated; grouping driven by fp not templates |

### Branch C — Signature & fingerprint (§3)

| # | node | mechanism | volume | expected outcome |
|---|---|---|---|---|
| C1 | **Exact `error_hash` fast path — unanimous**: probe identical (post-mask) to ≥ 2 H items labeled `pb` by humans, < 180d old | Stage A inherit, guard "≥2 unanimous" | 2 H + 3 P (DEMO-MATURE) | **auto-label `pb`, conf 0.95, `method=hash`**, relevantItem = newest H |
| C2 | **Fast path — single human high-conf**: probe matches exactly 1 H item with `label_event` source `defect_update` | guard "1 human, conf ≥ 0.9" | 1 H + 1 P | auto-label via hash |
| C3 | **Fast path DENIED — single AI-sourced match**: only match is `ai_suggested` unreviewed | Stage A guard rejection → falls to B/C with `same_error_hash` kept (feature 9 = 1) | 1 H (ai_suggested `ab`) + 1 P | **no inherit**; GBM decides; expect suggest band (feature `same_error_hash_top1=1` visible in stored vector) |
| C4 | **Fast path DENIED — stale**: exact matches exist but > 180 days old | age guard | 2 old H + 1 P | no inherit; Stage C still retrieves them with low `recency_top1` → suggest at best |
| C5 | **Fast path DENIED — conflicting labels**: exact-hash history split `pb` vs `ab` | unanimity guard; `label_hist_entropy` high | 2 H `pb` + 2 H `ab` + 2 P | no inherit; GBM sees entropy ≈ 1 → **abstain or low-band suggest** (this is also the purity-depressor feed for E4) |
| C6 | **`exception_fp` match, different templates**: same root exception chain + same in-app frames, but log wording/templates differ (different retry wrapper logs) | fp equality with template divergence; feature 10 = 1 while feature 2 (jaccard) low | 4 H `pb` + 3 P | Stage A misses (different `error_hash`); Stage C top1 has `same_exception_fp_top1=1` → **suggest/auto `pb`**; proves fp survives template drift |
| C7 | **Template-only overlap**: same masked template sequence, but different exception class (fp differs) — e.g. same "payment declined" log family thrown as `IllegalStateException` vs `PaymentException` | template Jaccard as independent signal (features 2, 22) | 3 H `ab` + 2 P | no hash/fp match; jaccard ≈ 1 pulls candidates → suggest `ab` (not auto — margin modest) |
| C8 | **Root-cause reorder**: nested `Caused by:` chain where the outer wrapper is common (`RuntimeException`) and root is specific (`SQLTransientConnectionException`) | chain extraction root-first; normalization stripping `$1`, `$$Lambda` | 4 items (2 H `si`, 2 P) | `EXC:` begins with root class; matches `db_conn_failed` seed mode + H twins → suggest/auto `si` |
| C9 | **No-stacktrace, no-exception timeout**: bare `Test timed out after 30000 ms` | `exception_fp = 0` path; Stage A disabled; seed `generic_timeout` kw rule | 3 P (DEMO-COLD) | cold rules: seed prior `ab@0.5 < 0.7` → **abstain `ti`** in DEMO-COLD; same items in DEMO-MATURE with 4 H `ab` twins → suggest `ab` (contrast pair) |
| C10 | **Assertion value masking**: `expected: 41 but was: 42` vs `expected: 7 but was: 9` in another launch | `is_assertion=1`, `<VAL>` masking so different literals still match | 3 H `pb` + 2 P with different literals | probes retrieve H despite differing values → suggest/auto `pb`; feature 36 = 1 |

### Branch D — Retrieval Stage C mechanics (§6.0, §6.3)

| # | node | mechanism | volume | expected outcome |
|---|---|---|---|---|
| D1 | **Plain hybrid hit**: paraphrased failure (same semantics, moderately different wording, same exception) | RRF fusion of FTS + pgvector; top-20 assembly | 6 H `pb` + 3 P | suggest or auto `pb` via `method=gbm`; top1_cosine ≈ 0.85–0.92 |
| D2 | **Lexical-wins case**: rare distinctive token (`ERR_QUOTA_FROBNICATOR_2291`) in short message; embedding near-generic | FTS rank rescues where cosine is mediocre | 3 H `si` + 2 P | candidate found via fts_rank leg of RRF; suggest `si`; measurable: top1_rrf high while top1_cosine < 0.8 |
| D3 | **Dense-wins case**: semantically identical failure written in two languages / fully reworded (no shared content words) | pgvector leg rescues where FTS ≈ 0 | reuse A5 + 2 extra P | suggest correct label with fts_rank ≈ 0, cosine ≥ 0.85 — the disagreement pair with D2 is the demo talking point |
| D4 | **Retrieval-scope modes**: same probe failure sent 6× with analyzerMode = LAUNCH_NAME / CURRENT_AND_THE_SAME_NAME / CURRENT_LAUNCH / PREVIOUS_LAUNCH / ALL / unset; history planted so scopes disagree (matching item exists in *another* launch name only) | §6.0 hard filters + `launch_boost` soft boosts | 1 failure × 6 requests; history: 2 H same-name launch, 2 H different-name | LAUNCH_NAME/ALL find it (suggest); CURRENT_LAUNCH/PREVIOUS_LAUNCH miss (**abstain**) — outcome *differs by mode*, proving filters bind |
| D5 | **`ti`/auto-nd exclusion**: closest neighbors are `ti`-labeled and auto-suggested-`nd` items | retrieval predicate `issue_type NOT IN ('ti') AND is_labeled` + Stage A "auto-nd never propagates" | 3 H (`ti`), 2 H (nd, ai_suggested) + 2 P | probes get **no usable candidates → abstain**, despite near-identical text existing |

### Branch E — KB modes & seed catalog (§6.2, §9)

| # | node | mechanism | volume | expected outcome |
|---|---|---|---|---|
| E1 | **KB short-circuit**: DEMO-MATURE mode "Payment gateway 502 flaps" — confirmed, support ≥ 12, purity ≥ 0.95 (built from H items all labeled `si`) | confirmed-mode short-circuit (`method=kb`, conf = min(0.93, purity·score)) | 14 H `si` + 4 P | **auto-label `si`, `method=kb`** — must appear at least once in demo, distinct from `method=hash` |
| E2 | **KB features-only**: candidate-state mode (score 0.70–0.80 or purity < 0.95) | KB feeds features 17–21 but cannot decide | 8 H + 3 P | `method=gbm` with kb_top1_score ≈ 0.75 in stored vector; suggest band |
| E3 | **Raw-history beats KB**: probe whose best KB score < 0.70 but with 5 strong labeled item-history twins | KB-vs-history contrast: Stage C alone carries decision | 5 H `pb` + 2 P | auto/suggest `pb` with kb_* features at defaults (0) — the measurable inverse of E1 |
| E4 | **Purity depression via relabel conflict**: mode seeded from `wd_no_such_element` items first labeled `pb`, then relabeled `ab` via defect_update (same signature, pb→ab) | §6.7 purity/support update; label conflicts depress purity below short-circuit bar | 10 H items; 6 relabel events; +2 P | mode stays candidate / purity ≈ 0.6; probes get **suggest not auto**; `kb_purity` feature visibly low; label_events show pb→ab pairs |
| E5 | **Seed-mode sampler**: fixtures for ~14 representative seeds spanning all rule types & priors: `oom_java`, `disk_full`, `conn_refused`, `conn_timeout`, `dns_resolution`, `tls_cert`, `http_401_403`, `http_429`, `db_deadlock`, `wd_stale_element`, `wd_driver_mismatch`, `assertion_pytest`, `class_not_found`, `flaky_retry_passed` | exc_re / msg_re / kw rule matching; priority order; lazy per-project copy creation | 2–3 items per mode ≈ 34 items, mostly in DEMO-COLD | in DEMO-COLD: seeds with prior conf ≥ 0.7 (`disk_full` si@0.9, `dns` si@0.85, `wd_stale` ab@0.8, `oom` si@0.8, `driver_mismatch` si@0.85, `flaky_retry` nd@0.7, …) → **rule-cold auto-label**; conf < 0.7 (`conn_timeout` 0.65, `http_401_403` 0.55, `db_deadlock` 0.5, `assertion` 0.4) → **abstain `ti`** — both sides of the 0.7 bar covered |
| E6 | **Seed priority tie**: log matching both `conn_timeout` (#8) and `generic_timeout` (#47) | first-match-by-priority wins | 2 items | `seed_mode_matched` = conn_timeout; observable in features 22–25 one-hot |
| E7 | **Seed negatives**: 10 benign noisy ERROR logs (verbose config dumps, retry notices) engineered to graze no rule | zero-false-positive check on rules | 10 items | no seed match; in DEMO-COLD → abstain `ti` |

### Branch F — Launch grouping & burst (§5)

| # | node | mechanism | volume | expected outcome |
|---|---|---|---|---|
| F1 | **Burst launch → si prior**: launch of 30 failures, 14 share one *never-seen* fingerprint (`db_conn_failed`-shaped), 16 assorted | new-fp check + `n≥5` + share 0.47 > 0.40 → `si_prior = min(0.9, 0.5+0.47) = 0.9`; one diagnosis fanned out | 1 launch, 30 items (P) | one group of 14, `si_prior ≈ 0.9`; **all 14 auto/suggest `si` sharing `group_id`**, one representative analyzed |
| F2 | **Below-threshold non-burst**: 4/30 share a new fp (n<5 and share 0.13) | burst guard negative | same launch as F1 (the "assorted" side) or dedicated 30-item launch | group formed but `si_prior = 0`; members decided individually |
| F3 | **Old-fp mass failure (regression, NOT si)**: 12/25 failures share a fp *already seen & labeled `pb` months ago* | `is_new` check — burst prior must NOT fire for known fp | 1 launch, 25 items; 3 H `pb` | `si_prior = 0`; group **auto-labels `pb` via hash/history** — the discriminating twin of F1 |
| F4 | **Cosine attach of fp=0 leftovers**: three timeout items (fp=0) whose embeddings sit ≥ 0.83 to a group centroid | pass-2 greedy attach | inside F1 launch | leftovers join group (same `group_id`), inherit fan-out decision |
| F5 | **Group merge pass**: two fp-buckets of the same incident (different wrapper exceptions) with centroid cos ≥ 0.83 AND template Jaccard ≥ 0.5 | pass-3 `merge_close` | 1 launch, 2×5 items | single merged group; representative = max(has_stacktrace, log_count) |
| F6 | **Anti-merge look-alikes (MUST NOT group)**: two `NullPointerException` families — one in `checkout.CartService.applyDiscount`, one in `auth.SessionFilter.doFilter`; similar message shells, different frames/templates | fp separates; centroid cos deliberately in 0.6–0.7; Jaccard < 0.5 | 1 launch, 2×6 items; history: 4 H `pb` for cart, 4 H `ab` for auth (test-fixture null) | **two groups, two different labels** (`pb` vs `ab`); the corpus's headline "same exception ≠ same cause" proof |
| F7 | **Small mixed launch**: 5 failures, all different fps, no history for 2 of them | grouping degenerate case (5 singleton groups); per-item decisions | 2 launches × 5 items | mix of auto (known), suggest, and ≥ 1 abstain; exercises `group_dominance = 0.2`, `co_failure_group_size` small |
| F8 | **`cluster` route replay**: run RP clustering on the F1 launch; re-run it | §8.1 stable clusterId (`xxh3` of representative hash), `cluster_id_map` reuse | same data, 2 route calls | identical clusterIds across runs; clusterMessage = first 5 lines of representative |

### Branch G — Decision layer, training depth & abstention (§6.4–6.6)

| # | node | mechanism | volume | expected outcome |
|---|---|---|---|---|
| G1 | **Auto band**: probes engineered for `p* ≥ 0.75` (dense history, unanimous labels, high recency, matching test_case_hash) | τ_auto crossing; analyze route auto-label | ≥ 6 P in DEMO-MATURE | auto-label, present in analyze reply with relevantItem |
| G2 | **Suggest band**: probes with genuine ambiguity (moderate cosine ~0.78, 60/40 label split) | `0.45 ≤ p* < 0.75` → stays `ti`, top-3 precomputed suggestions stored | ≥ 6 P | analyze reply omits them; suggest route returns top-3 with matchScore = prob·100 |
| G3 | **Novel failure MUST abstain**: brand-new subsystem failure (`QuantumLedgerDesyncError`, unseen frames/templates, no seed rule) | `p* < 0.45` → pure abstain; the safety story | 4 P (2 in MATURE, 2 in COLD) | **abstain `ti`, reason stored; suggest returns []** — must stay `ti` even in the label-rich project |
| G4 | **Training depth**: label_event streams per §0 — DEMO-MATURE ≥ 320, DEMO-MID ~80, ≥ 60 usable rows with stored feature snapshots per project; all 4 classes represented ≥ 12× each per project (GBM is 4-class, `class_weight=balanced`) | §6.5 training join (label_event × suggestion.features); retrain trigger at 100 new events | events generated by "human" defect_update replay against H items after their suggestions exist | GBM trains; eval harness (§10.1) produces per-label F1 on 20% chronological tail; ship gate evaluable |
| G5 | **Isotonic split**: DEMO-MATURE crosses 300 → per-project calibrator; DEMO-MID stays on install-wide | calibration selection logic | derived from G4 counts | `model_artifact` rows: `kind='calib', project_id=MATURE` exists; MID has none |
| G6 | **Cold-model proof**: run DEMO-COLD analysis *before* install crosses 50 events (upload ordering!) | `< 50 events → skip GBM` rule fallback chain | first demo phase: only COLD uploaded | all COLD decisions have `method ∈ {hash, kb, rule_cold}`, never `gbm` |
| G7 | **Retries / flaky `nd`**: test `LoginSmokeTest` fails-then-passes across 8 launches (RP retries + alternating statuses); failures match `flaky_retry_passed` seed + high `flakiness_score`/`flips_30d` from test_stats | features 26–28; seed nd@0.7; auto-nd non-propagation (ties to D5) | 8 launches touched, ~10 failure items, 5 H labeled `nd` by human | probes suggest/auto **`nd`**; stored vectors show flakiness ≈ high, flips_30d > 0; and a *stable* control test shows flakiness ≈ 0 |
| G8 | **Feature-default integrity probe**: item with no candidates, no KB, no stacktrace, cold project | §6.4 defaults (no NaN), entropy default 1 | 2 P in DEMO-COLD | abstain; stored feature vector = documented defaults — cheap high-value assertion |

### Branch H — Feedback loop & metrics (§6.7, §10)

| # | node | mechanism | volume | expected outcome |
|---|---|---|---|---|
| H1 | **Accept cycle**: human sets defect = top suggestion for 10 suggested items | defect_update → label_event; `metrics_daily.accepted` | 10 events | metrics_daily shows accepted ≥ 10; mode support bumps |
| H2 | **Correct cycle**: human overrides 5 suggestions with a different label | `corrected` metric; purity update; retrain counter | 5 events | metrics_daily.corrected = 5; affected mode purity drops (ties E4) |
| H3 | **Auto-corrected (safety metric)**: human flips 2 auto-labeled items | `auto_corrected` — the key guard metric must be non-zero once in demo | 2 events | metrics_daily.auto_corrected = 2 |
| H4 | **Retrain fire**: cumulative events cross 100 after H1–H3 replay | debounced retrain; ship gate §10.2 runs | — | new `model_artifact` row; gate decision logged (accept or reject both fine — the *row* is the proof) |
| H5 | **`search` route**: RP "similar TI" query for a G2 item | §8.2 hybrid-without-decision, cosine ≥ 0.75 threshold, filteredLaunchIds respected | 2 route calls (one with launch filter excluding the match) | filtered call returns fewer/no hits than unfiltered |
| H6 | **`suggest_patterns`**: called on MATURE (≥ 100 labeled) and COLD (< 100) | §8.3 aggregation + emptiness rule | 2 calls | MATURE returns patterns incl. P(label|pattern) ≥ 0.8 entries; COLD returns empty lists |

---

## 2. Coverage cross-check (mechanism → node)

Every named mechanism fires: level/near-dup/cap **A1**, empty-sig **A2**, merged-small **A3**,
preprocess+mask **A4**, multilingual **A5**, truncation **A6**; masking-stable template ids **B1**,
widening **B2**, per-project isolation **B3**, frame-skip **B4**; Stage A inherit **C1–C2** and
all three guard denials **C3–C5**; fp-match-different-templates **C6**; template-only overlap
**C7**; root-cause ordering **C8**; fp=0 **C9**; `<VAL>` assertion masking **C10**; hybrid RRF
**D1**, lexical-vs-dense disagreement **D2+D3**, all 6 analyzerModes **D4**, ti/auto-nd exclusion
**D5**; KB short-circuit **E1**, KB-as-features **E2**, history-beats-KB **E3**, conflict-depressed
purity **E4**, seed rules both sides of 0.7 **E5**, priority **E6**, negatives **E7**; burst→si
**F1**, non-burst **F2**, old-fp regression (burst suppressed) **F3**, cosine attach **F4**, merge
**F5**, look-alike anti-merge **F6**, small mixed **F7**, cluster route **F8**; τ_auto/τ_suggest/
abstain bands **G1–G3**, training depth & retrain **G4/H4**, isotonic split **G5**, cold fallback
**G6**, flaky-nd + test_stats **G7**, feature defaults **G8**; feedback & metrics **H1–H6**.
All four decision `method` values appear: `hash` (C1), `kb` (E1), `gbm` (G1), `rule_cold` (E5/G6).
All five RP labels are produced: `pb` (B1/C1/F3/F6), `ab` (C7/E4/F6), `si` (E1/F1/C8), `nd` (G7),
`ti` (C5/C9/D5/E7/G2/G3/G8).

## 3. Volume roll-up

| project | launches | failed items | label_events | notes |
|---|---|---|---|---|
| DEMO-MATURE | ~22 (16 H + 6 P) | ~900 | ~320 | carries C, D, E1–E4, F, G1–G5, H |
| DEMO-MID | ~10 | ~300 | ~80 | ambiguity/suggest-band emphasis, D4 scope tests |
| DEMO-COLD | ~8 | ~200 | ~15 | E5–E7, G6, G8, C9 contrast |
| **total** | **~40–55** | **~1,400–1,800** | **~415–430** | crosses the 50 / 100 / 300 thresholds in that order |

## 4. Generation & measurement notes

1. **Upload phasing is part of the design**: (i) COLD probes first (proves G6), (ii) MATURE/MID
   history + defect_update replay (crosses 50 then 100 → first GBM), (iii) probe launches,
   (iv) H1–H3 feedback replay (crosses next 100 → H4 retrain), (v) route calls F8/H5/H6.
2. Timestamps must span ~7 months so C4 (>180d) and recency decay (half-life 90d) are real;
   30d windows for test_stats features (G7) need dated launches inside the window.
3. Every probe node's assertion is a query against `suggestion` (`method`, `issue_type`,
   `abstain_reason`, `features` jsonb) or `launch_group` (`si_prior`, membership) — write the
   expected-outcome table above into a machine-readable manifest next to the generator so the
   demo doubles as an end-to-end acceptance run of §11.
4. Hard-to-hit bands (G1/G2 probabilities) can't be dialed exactly ex ante: tune by history
   density/label-split ratio, not by text similarity alone — margin (`f3`) and entropy (`f6`)
   are the controllable levers.
