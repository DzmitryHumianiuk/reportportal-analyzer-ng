# Consilium — Lens 2: ML signal value of per-test metadata

**Question.** Would launch PARAMETERS, test DESCRIPTION, ATTRIBUTES, and PARENT path raise triage
quality as retrieval/feature signals in analyzer-ng, and at what cost?

**Scope of this lens.** Signal value only — feature-by-feature against what the pipeline already
extracts. Verified against `src/analyzer_ng/core/features.py` (45-feature registry,
`FEATURE_SCHEMA_VER = 3`), `analyzer-ng-plan/specs/03-pipeline.md` §6, `src/analyzer_ng/amqp/models.py`,
`src/analyzer_ng/db/repositories/{stats,retrieval}.py`, and the legacy
`service-auto-analyzer` reference.

---

## 0. Verified baseline (what the pipeline can already see)

| Fact | Evidence |
|---|---|
| Wire carries `uniqueId`, `testCaseHash`, `testItemName`, `description`, logs — **no** parameters list, attributes, or parent path | `amqp/models.py:79–112` |
| `description` **is already on the wire but dropped** — no store, no feature, no retrieval use | only hit outside models.py is the unrelated `detect_log_description_and_stacktrace` text function |
| `testCaseHash` is computed by the RP client from `codeRef` + parameters (each dataset of a data-driven test gets its **own** hash) | RP client behavior; legacy mirrors it (`launch_objects.py:170`, ES field `test_case_hash` with `boost_test_case_hash` in `os_migration.py:231–255`) |
| Per-test history (`test_history_stats`: window_runs/failures/flips, flakiness) is keyed `(project_id, test_case_hash)` — i.e. **already per-parameter-set** | `db/repositories/stats.py:17–100` |
| `same_test_case_top1` (#8), `flakiness_score` (#26), `test_fail_rate_30d` (#27), `flips_30d` (#28), `test_age_days` (#33) all key off `test_case_hash` | `features.py`, `retrieval.py:664`, `test_case_first_seen` |
| "environment/agent match" is **design intent only** — listed in `analyzer-ng-plan/CONTEXT.md:43` among candidate signals, **absent** from the implemented §6.4 table and from `FEATURES`; unimplementable today because attributes never reach the service | grep of §6.4 table + `FEATURES` tuple |
| History is failure-only: `bump_test_history` fires on analyzed (failed) items (`ingest.py:154`), so `window_runs` ≈ observed failures — "fail rate" is conditioned on the analyzer seeing the item | `ingest.py`, `stats.py` |
| Feature additions are cheap by design: `to_vector_for` back-fills defaults for old snapshots, `FEATURE_SCHEMA_VER` bump, serving-identical training vectors (§6.4) | `features.py:431–448` |

The last row matters for cost: the *modeling* side of adding any of these is deliberately cheap. The
dominant costs are (1) the AMQP/RP-core wire-contract change, (2) storage + backfill, and (3) the
**sequencing tax** — features only become trainable after they are snapshotted at serving time, so
lift can only be measured after weeks of label accumulation (§10.1 replay works only on stored
vectors).

---

## 1. Parameters of data-driven tests

### What already exists (more than the question assumes)

Because `testCaseHash` folds parameters in, the headline scenario is **half-solved today**:
"dataset #9 fails 90% of runs" lives in `test_history_stats[hash(#9)]` and reaches the GBM as
`flakiness_score` / `test_fail_rate_30d` / `flips_30d`. When dataset #1 fails for the first time, it
presents as a cold key: `window_runs = 0` → both stats sit at their 0.5 defaults, `test_age_days ≈ 0`
(first failure = first stored item). The model *can* already learn "known-flaky hash vs. never-seen
hash" — weakly.

### What explicit parameter data adds beyond hash equality

1. **Cross-dataset linkage (`same_code_ref_top1`)** — the real gap. Today dataset #1 of `testFoo`
   and dataset #9 of `testFoo` are as unrelated as two random tests (different hash; `uniqueId` also
   parameter-dependent; `testItemName` is only an FTS-side fuzzy proxy). A candidate that is *the
   same test method with different data* carrying *the same error template* is strong evidence for
   "existing product bug now triggered by more data" → inherit the label; the same method with a
   *different* template is evidence for a new cause. Two features — `same_code_ref_top1` {0,1} and
   `param_jaccard_top1` [0,1] over key:value pairs — let the GBM separate these. This is the
   discrimination the product owner's scenario actually needs, and **hash equality cannot provide it**.
2. **Novelty disambiguation.** Cold-hash today is ambiguous: brand-new test vs. long-lived dataset
   that never failed. With codeRef-level aggregate stats (`code_ref_age_days`,
   `code_ref_fail_rate`), "dataset #1 of a 2-year-old, otherwise-healthy test suddenly fails" scores
   as high-novelty — a useful pb-leaning prior — instead of looking like a freshly added test
   (flakiness prior 0.5).
3. **De-fragmentation of history.** 10 datasets = 10 thin histories; per-method flakiness is diluted
   10×. codeRef aggregation fixes the sparsity that `test_case_hash` granularity creates.
4. **Cross-test param matching** ("dataset id X fails across many tests this launch") — a
   launch-scoped si/ab prior. Real but niche; partially shadowed by the existing launch-grouping
   signals (`co_failure_group_size`, `group_dominance`, `si_prior`), which already catch correlated
   bursts *when the error templates cluster*. Adds value only when the same bad dataset produces
   *heterogeneous* logs across tests. Defer.

### Risks

- **Cardinality/drift:** parameter values are unbounded strings (dates, run ids, URLs). Never use raw
  values as columns; Jaccard over masked key:value pairs only (reuse the §2.1 masking so
  `date=2026-07-18` doesn't make every run's param set unique).
- **Leakage:** low — parameters describe the input, not the verdict.
- **Sparsity:** projects without data-driven tests see constant features (LightGBM tolerates dead
  columns; defaults are well-defined).
- **Prerequisite:** `codeRef` (or the parameters themselves, from which RP's testCaseId string is
  reconstructable) must be added to the wire. RP core has both in its DB.

**Expected effect:** moderate on data-driven-heavy projects (the failure mode it fixes — blind label
inheritance across datasets vs. false novelty — is exactly the stated pain); near-zero elsewhere.
Honest estimate: low single-digit % lift in suggest accuracy install-wide, concentrated where it
matters. Cost: wire change + 1 column + ~4 features + codeRef-keyed aggregate in `stats.py`.

---

## 2. Attributes (key:value tags)

The **only truly orthogonal axis** on offer. All 45 current features are blind to *where* a failure
ran (browser, OS, env, region). Environment/agent match is already declared design intent
(CONTEXT.md:43) but never made the §6.4 cut — this closes acknowledged debt rather than opening a
new front.

Signal mechanisms:

- `env_match_top1` {0,1} (+ optionally `env_match_frac` over top-5): a historical match from the
  *same* env is worth more; the GBM currently cannot down-weight a chrome-only failure matched
  against a firefox-labeled candidate.
- **Env-spread within launch:** same `error_hash` failing across *all* envs → product bug; only one
  env → environment/si lean. Complements the seed catalog's env modes (§9) with evidence instead of
  regex.
- Feature-area tags give a soft locality prior where logs are generic (assertion-only failures).

Risks — the sharpest of the four:

- **Leakage is a first-order threat, not a corner case.** Teams tag `defect:known`, `flaky`,
  `quarantine`, `jira:XXX`, even `analyzed:true`. Any of these entering the vector trains a model
  that reads the answer off the tag and collapses when tagging habits change. Mitigation is
  structural: **allowlist of keys** (config per install: `browser`, `os`, `env`, `platform`,
  `feature`…), never freeform ingestion; drop unknown keys at ingest.
- **Cardinality:** bounded by the allowlist; encode as match-features (equality vs. top-1), not
  one-hots of values.
- **Drift:** tag vocabularies churn; match-features (same/different) are drift-resistant where
  value-encodings are not.

**Expected effect:** moderate on multi-env matrices (a common RP deployment shape); the env-spread
signal directly targets pb-vs-si confusion, which the decision layer currently resolves only via
launch-fraction heuristics. Cost: wire change + allowlist config + ~3 features. Best
orthogonality-per-feature of the four.

---

## 3. Parent path / suite location

A suite-locality prior: `path_overlap_top1` = normalized shared-prefix depth with the top
candidates. Mostly **shadowed** by existing signals — `testItemName` already field-boosts FTS
retrieval, launch grouping catches suite-wide bursts, and log content encodes app area far more
precisely than suite placement. The incremental case is narrow: log-poor failures (bare assertion,
no stacktrace — `has_stacktrace=0`, low `identifier_jaccard`) where suite locality is the only
app-area evidence left.

Risks: suite refactors rename paths (the classic RP `uniqueId`-churn problem) — the feature decays
silently; low leakage; low cardinality if encoded as overlap-depth rather than path identity.

**Expected effect:** small (<1% install-wide, a bit more on assertion-heavy UI suites). Cost: low
(one wire field, one feature) — but it still pays the same wire-contract tax as the bigger wins, so
it should ride along with (1)/(2), never drive the change alone.

---

## 4. Description

Weakest signal, and the evidence is already in: **it has been on the wire since day one
(`models.py:88`) and nothing in the pipeline ever missed it.**

- Prose is human-written, stale, and templated; for BDD frameworks it restates the scenario — a
  noisier proxy for identity that `testCaseHash`/`testItemName` already capture exactly.
- As a retrieval field it actively hurts: description similarity pulls candidates that share
  *purpose*, not *failure mode*, diluting the log-similarity ranking that is the system's core bet.
- As a feature, `desc_similarity_top1` is nearly collinear with `same_test_case_top1` + name-FTS.
- **Leakage/drift:** descriptions accrete triage notes ("known issue, see JIRA-123") — stale
  human verdicts entering the vector as text.
- Embedding cost per item for ~zero lift.

**Expected effect:** ≈0, plausibly negative in retrieval. Skip; revisit only as *display* context
for the LLM sidecar's explanation output (spec 04), where prose belongs — not as an ML signal.

---

## Ranking (value ÷ cost)

| Rank | Signal | Effect size | Where it lands | Cardinality/sparsity | Leakage | Verdict |
|---|---|---|---|---|---|---|
| 1 | **Attributes** (allowlisted) | moderate | orthogonal env axis; pb-vs-si; closes CONTEXT.md intent | bounded by allowlist | **high — allowlist is mandatory** | do |
| 2 | **Parameters** (+`codeRef`) | moderate, concentrated on data-driven projects | cross-dataset linkage the hash cannot give; novelty disambiguation; de-fragmented history | manageable via masked-pair Jaccard | low | do |
| 3 | **Parent path** | small | log-poor assertion failures only | low (encode as overlap depth) | low | ride-along only |
| 4 | **Description** | ≈0 / negative | none | n/a | medium (triage notes in prose) | skip |

**Sequencing note.** All of 1–3 share one wire-contract change (RP core → analyzer AMQP payload:
`attributes`, `parameters`/`codeRef`, `pathNames`). Batch it once. Then: ingest + snapshot the new
features at defaults-compatible `FEATURE_SCHEMA_VER=4` (the `to_vector_for` compat rule makes this
non-breaking), accumulate labels, and let the §10 eval gate — not intuition — decide whether each
column survives. Effect-size claims above are priors, cheap to falsify with the replay harness the
project already built.
