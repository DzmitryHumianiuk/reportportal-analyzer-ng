# Lens 3 — Architecture Fit: Per-Test Metadata in analyzer-ng

Scope: WHERE parameters / description / attributes / parent-path would plug into the
existing design (specs 02 §2/§5, 03 §5–6), what migrations and version bumps each needs,
re-index and back-compat implications, and cost. Verified against the implementation, not
just the specs.

## 0. Ground constraints verified in the repo

| Fact | Where verified |
|---|---|
| Wire contract carries `uniqueId`, `testCaseHash`, `testItemName`, `description` (+ `issueDescription`); **no** parameters list, attributes, or parent path | `src/analyzer_ng/amqp/models.py` (`TestItem`, `TestItemInfo`); identical in legacy `service-auto-analyzer/app/commons/model/launch_objects.py` |
| `description` is **already on the wire** — carried but unused. Only (a) parameters-as-values, (c) attributes, (d) path need a contract extension | same files |
| Pydantic v2 models ignore unknown wire fields by default; new optional fields default cleanly → additive AMQP extension is non-breaking in both directions (old RP ⇄ new analyzer, new RP ⇄ old analyzer) | `amqp/models.py` (no `extra="forbid"` anywhere) |
| Migrations 0001–0005 applied; next slot is **0006**; runner is transactional, additive-preferred (spec 02 §3.3) | `src/analyzer_ng/db/migrations/` |
| `FEATURE_SCHEMA_VER = 3`; serving **gate already degrades to rule fallback** when the active GBM's `feature_schema_ver` mismatches, until retrain ships a new artifact | `core/features.py:28`, `ml/serving.py:142-153`, `ml/gate.py:156` |
| `HYBRID_RETRIEVAL_VERSION = 3`; bumped on any change to the stage-A/B SQL constants | `db/repositories/queries.py:27` |
| `signature_tsv` is a **GENERATED STORED** column over `exc_text/msg_text/frames_text/tmpl_text` using **all four** Postgres weight classes A–D | spec 02 §2.5 DDL, `0001_init.sql` |
| Test name is in the **embedding doc** (`TEST:` section, never truncated) but **not** in the FTS tsvector | `ml/signature.py:239-284`, spec 03 §3.1 vs spec 02 §2.5 |
| `test_history_stats` PK = `(project_id, test_case_hash)`, small, **non-partitioned** → PK change is a cheap transactional migration | spec 02 §2.10 |
| `uniqueId` is an opaque RP-side MD5 (launch/path/name/params folded in) — useless for structure; `testCaseHash` = client-side hash of codeRef+parameters → **param identity is already folded in**, param values and the “same test, different dataset” sibling relation are not | legacy reference + spec 02 §2.4 semantics |

Two hard architectural constraints fall out immediately:

1. **“A new FTS weight class” does not exist.** Postgres `setweight` has exactly four
   classes (A–D) and `signature_tsv` consumes all four. Any new text field either shares
   an existing class or displaces one.
2. **Touching `signature_tsv`'s expression = full table rewrite** of
   `failure_signature` (the biggest table, ~2.4 GB heap + 0.6 GB GIN per 1M items,
   spec 02 §7) plus GIN rebuild. Changing the *embedding* document composition likewise
   invalidates vector comparability and forces the §6 re-embed procedure (doc-version
   bump rides the `emb_model_ver` machinery). These are the two expensive levers; the
   design below avoids both wherever possible.

---

## 1. (a) Launch PARAMETERS of data-driven tests

### What testCaseHash already gives, and what it doesn't

`testCaseHash` folds codeRef+parameters → two runs of dataset #9 share a hash; dataset #1
and #9 have **different** hashes. So today:

- `same_test_case_top1` (feature 8) is already parameter-*identity*-aware. ✔
- `test_history_stats` history is already **per-dataset** (keyed by `test_case_hash`),
  so “dataset #9 is the flaky one” is already isolated from #1. ✔
- What is **missing** is the *sibling relation*: “candidate is the same test method but a
  different dataset.” That is exactly the product owner's scenario — #1 failing while
  only #9 ever failed should look *less* like inherited history, not more. No wire field
  carries codeRef, but `preprocess_test_item_name(item_name)` (already ported,
  `preprocessing/text_processing.py:710`) is a workable sibling key **available today**.

### Plug-in design (two phases)

**Phase 0 — no wire change (sibling identity from the name).**
- New generated/backfillable column `test_item.item_name_hash bigint`
  (= xxh3_64 of `preprocess_test_item_name(item_name)`), btree index
  `(project_id, item_name_hash)`.
- Stage-B SQL projects `ti.item_name_hash` → **HYBRID_RETRIEVAL_VERSION 3→4**
  (projection-only change; fusion/ordering untouched, ordering goldens stable).
- New features (Python-side, from projected columns):
  `same_test_sibling_top1` (same name-hash, different `test_case_hash`),
  `sibling_frac_candidates`. → **FEATURE_SCHEMA_VER 3→4**.
- Migration `0006`: additive column + backfill UPDATE in batches + index. No rewrite of
  generated columns, no re-embed. Back-compat: NULL name-hash → features default 0
  (no-NaN rule, spec 03 §6.4).

**Phase 1 — wire extension (parameter VALUES), gated on Lens 1.**
- AMQP: optional `parameters: list[dict] = []` on `TestItem`/`TestItemInfo` — additive,
  contract-safe; the long pole is the **RP backend** populating it, outside this repo.
- Storage: `test_item.param_hash bigint NOT NULL DEFAULT 0`
  (= xxh3_64 of sorted `key=value` pairs) + `test_item.parameters jsonb` (audit/LLM
  context only — never into FTS: values are volatile and the §2.1 masking philosophy
  would mask them anyway; never into the embedding doc for the same reason).
- **`test_history_stats` keyed by `(project_id, test_case_hash, param_hash)`**: since
  `testCaseHash` already folds param identity, the extra key is *defensive* (it
  distinguishes installs whose clients hash codeRef-only) — implement as PK recreation in
  the same `0006` with `param_hash DEFAULT 0`; legacy rows collapse to `param_hash=0` and
  reads fall back `(tch, param_hash) → (tch, 0)`. Cheap: table is small, non-partitioned.
- Features: `same_params_top1` (exact `param_hash` match), `param_first_failure`
  (this dataset has zero prior failures while siblings have many — the “suddenly #1
  fails” alarm). Batched into the same FEATURE_SCHEMA_VER bump as Phase 0 if shipped
  together.

**Cost:** Phase 0 **S**. Phase 1 analyzer-side **S–M**; end-to-end **M** (RP backend
change dominates). **Re-index:** none (backfill + defaults). No re-embed.

---

## 2. (b) Test DESCRIPTION

Already on the wire (`TestItem.description`) — no contract work. The question is purely
where it plugs, and the answer is: **not into retrieval.**

- **FTS**: no free weight class (constraint 1). Sharing class C/D means static,
  test-identity-correlated prose competes with frames/templates; and adding any
  `desc_text` into the `signature_tsv` expression triggers the full-table rewrite
  (constraint 2) plus GIN rebuild on every existing install.
- **Embedding doc**: adding a `DESC:` section changes document composition → re-embed of
  every row (§6 re-embed procedure) and, worse, *hurts the objective*: descriptions are
  constant per test, so they pull all failures of the same test together regardless of
  cause — the opposite of cause-matching. The `TEST:` section already contributes the
  benign share of this signal.
- **Correct plug points (cheap):**
  1. `test_item.description text` — additive `0006` column, stored for display and audit.
  2. **LLM context**: explainer/judge prompts (spec roles in `llm/`) get the description
     as grounding text — this is where human-written intent actually helps.
  3. Optional later: `suggest_patterns`/KB-mode summaries may quote it.
- No FEATURE_SCHEMA_VER, no HYBRID_RETRIEVAL_VERSION, no re-index, no re-embed.

**Cost:** **S** as context/display; **L** (and value-negative risk) if pushed into
FTS/embedding — do not.

---

## 3. (c) ATTRIBUTES (key:value tags)

Not on wire → contract extension required (`attributes: list[dict] = []`, additive).

- **Plug point: exact-match features + optional retrieval scoping — never FTS**
  (categorical tokens, not prose; would pollute weight classes) and **never the
  embedding doc**.
- Storage: `test_item.attributes jsonb NOT NULL DEFAULT '{}'` + expression GIN if we ever
  filter on them. `0006` additive.
- Features: `attr_jaccard_top1` (set Jaccard vs top-1 candidate) — Python-side from a new
  Stage-B projection (`HYBRID_RETRIEVAL_VERSION` bump shared with whatever release also
  touches the SQL) and one FEATURE_SCHEMA_VER slot.
- Optional stronger use: environment-ish keys (`env:`, `browser:`) as **soft boosts** in
  §6.0 scope handling (multiplicative, like `launch_boost`) — config-gated, no schema
  impact beyond the column.
- Back-compat: `'{}'` default, Jaccard over two empty sets = 0 (helper already handles
  this shape, spec 02 §2.13 analogue).

**Cost:** analyzer-side **S**; end-to-end **M** (RP backend). Value is the most
speculative of the four — adopt last, behind data showing attribute-conditioned label
skew.

---

## 4. (d) PARENT PATH / suite location

Not on wire (`uniqueId`'s MD5 folds pathNames but is irreversible) → contract extension
(`pathNames: list[str] = []` or `codeRef: str = ""`, additive). Partial proxy exists
today for Java-style names (package prefix of `item_name`), but it is weak for BDD/UI
suites.

- **Plug points:**
  1. `test_item.path_text text` (normalized `/`-joined suite path) + `path_hash bigint`,
     `0006` additive.
  2. Features: `same_suite_top1` (exact suite match), `suite_prefix_depth_top1`
     (longest-common-prefix depth / max depth). Python-side from projections.
  3. **Suite-level history**: `suite_history_stats(project_id, path_hash, ...)` mirroring
     §2.10 — captures “this whole suite is burning” which per-test stats and launch
     grouping only partially see (grouping is per-launch; suite stats are cross-launch).
     New small table, same upsert pattern, same weekly halving job.
- Not FTS, not embedding (identity-correlated static text — same argument as
  description).
- Back-compat: NULL path → features default 0; no suite stats row → defaults 0.5/0 like
  the existing `test_stats` defaults.

**Cost:** **S–M** analyzer-side (new stats table is the M part); end-to-end **M** (RP
backend). Value concentrated in `si` detection (suite-wide bursts across launches).

---

## 5. Cross-cutting mechanics

**Migration plan (one file, `db/migrations/0006_test_metadata.sql`):** all additive —
`test_item`: `item_name_hash`, `param_hash DEFAULT 0`, `parameters jsonb`,
`attributes jsonb`, `path_text`, `path_hash`, `description`; recreate
`test_history_stats` PK with `param_hash DEFAULT 0`; new `suite_history_stats`; batched
backfill of `item_name_hash`. Pre-provisioning all columns in one 0006 (even for kinds
adopted later) is deliberate: columns are cheap when NULL, and it avoids a migration per
adoption step. `failure_signature` is **not touched** — that is the load-bearing choice
that keeps re-index cost at zero.

**Version-bump batching:** each FEATURE_SCHEMA_VER bump throws serving back to rule
fallback until the next retrain gate passes (`ml/serving.py`, `ml/gate.py`) — an
existing, safe, but user-visible degradation. Therefore batch all features of a release
into **one** bump (3→4), never one bump per data kind. Same for
HYBRID_RETRIEVAL_VERSION: all new column projections in one SQL change (3→4);
fusion/ordering unchanged, so §5.1 ordering goldens survive, projection goldens update.

**Re-index / re-embed for existing installs:** none, by construction — no generated
column changes, no embedding-doc changes, exact-match features default to 0 on historical
rows and become active as new data flows in. This “metadata warms up over time” property
is what makes incremental adoption safe.

**AMQP contract:** stays drop-in. All extensions are optional-with-default fields that
old RP simply never sends and old analyzers silently ignore. The gate is Lens 1's call on
whether RP-side population is realistic; everything analyzer-side is designed to be
inert when the fields are absent.

## 6. Verdict and adoption order

| Order | Item | Wire change | Migration | Version bumps | Cost | Verdict |
|---|---|---|---|---|---|---|
| 1 | (a) Phase 0: name-sibling identity | none | 0006 (column+backfill) | FSV 3→4, HRV 3→4 | **S** | **use** — delivers the “dataset #1 vs #9” signal now |
| 2 | (b) description → storage + LLM context only | none (already carried) | 0006 column | none | **S** | **partially** — context yes, retrieval no |
| 3 | (a) Phase 1: param values + `(tch, param_hash)` stats | yes | in 0006 (pre-provisioned) | shares FSV 4 if same release | **S–M** (+RP) | **use when Lens 1 clears the wire path** |
| 4 | (d) path + suite_history_stats | yes | 0006 + new table | shares bumps | **S–M** (+RP) | **use, after (a)** |
| 5 | (c) attributes | yes | 0006 column | shares bumps | **S** (+RP) | **skip for v-next; revisit with evidence** |

This order never reworks anything: each later step only *populates* columns and feature
slots the earlier step's migration and version bumps already provisioned.
