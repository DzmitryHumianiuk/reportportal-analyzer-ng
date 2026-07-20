# Consilium VERDICT — per-test metadata in analyzer-ng triage

Synthesis of the four lenses: [contract](lens-contract.md), [ML value](lens-ml-value.md),
[architecture](lens-architecture.md), [adversarial/ops](lens-adversarial.md).
Question: should analyzer-ng use (a) launch parameters, (b) description, (c) attributes,
(d) parent path, beyond the log-similarity core — and is each worth the cost?

---

## 1. Verdict table

| Data kind | Verdict | Decisive argument | Mandatory guards |
|---|---|---|---|
| **(a) Parameters** | **USE-NARROWLY** | The one thing `testCaseHash` cannot express is the *sibling relation* ("same test method, different dataset") — and that is exactly the owner's #1-vs-#9 scenario; parameter **identity** is already consumed via the hash, so only sibling linkage + identity-hash features are worth adding, and **raw values must never enter retrieval**. | Identity/sibling **hashes only** — no raw values in FTS, embedding docs, or KB documents (they would undo Drain masking); cardinality cap on param-hash granularity (>~100 distinct per `testCaseHash` in window ⇒ collapse to hash-level stats); raw values not stored (PII: fixtures carry credentials/emails) — if display storage is ever wanted: Drain-mask + length-cap + TTL + deletion-flow coverage; missing-field sentinel (R0) so the GBM can't learn "old backend era". |
| **(b) Description** | **AVOID** | It has been on the wire since day one and every lens found the same thing: boilerplate/TMS-synced prose makes *unrelated* tests similar, poisoning the highest-trust KB-inherit path, and as a feature it is collinear with `same_test_case_top1` + name-FTS — ≈0 or negative lift for real cost. | If ever revisited: LLM-sidecar display context only, behind the existing `sanitize()` + nonce envelope, length-capped, extractor schema unchanged; never FTS, never embedding, never a feature. Boilerplate-frequency filter mandatory before any similarity use. |
| **(c) Attributes** | **DEFER** (to USE-NARROWLY once preconditions exist) | Highest orthogonal value on offer (env axis; closes CONTEXT.md:43 intent) **but** the worst ML failure class — auto-injected tags (`known_issue:*`, `flaky:true`) are label leakage by construction, producing a shortcut model with great offline metrics that collapses in production — and the machinery that makes it safe (allowlist config, leakage audit, time-split eval) does not exist yet. | Hard preconditions before any adoption: operator-maintained per-project **key allowlist, deny-by-default**; per-feature mutual-information leakage audit at train time; **time-split** (not random-split) eval in `ml/eval.py`; match/Jaccard features only, never value one-hots; prohibit tags written by automated triage (feedback-loop break); `sanitize()` + envelope for any prompt use. |
| **(d) Parent path** | **DEFER** (ride-along comparison features only) | Mostly shadowed by name-FTS, launch grouping, and log content (<1% install-wide lift), so it never justifies the wire change alone; adopt only as normalized comparison features riding along when (a)/(c) pay the contract tax anyway. | **Feature-only, never a retrieval or history-stats key** (suite refactors bulk-invalidate path identity — a zeroed feature degrades gracefully, a poisoned key collapses recall silently); normalize before compare (strip param indices, split separators), encode as prefix-overlap depth; never in FTS/embedding docs. |

Cross-cutting mandatory work for anything adopted (Lens 4 R0): optional-with-sentinel
wire semantics, one batched `FEATURE_SCHEMA_VER` bump per release (never per data kind),
snapshot-at-prediction train/serve parity, deletion/TTL coverage for any new stored field.

---

## 2. Adjudications where lenses disagree

**Attributes — Lens 2 (rank #1, "do") vs Lens 3 ("skip for v-next") vs Lens 4 ("adopt-narrowly, gated").**
Ruled **DEFER**. Lens 2's value case is real (only orthogonal axis; pb-vs-si evidence),
but Lens 4's leakage scenario is the *common* case, not the adversarial one, and the
current retrain path has no leakage audit because no existing feature can leak —
attributes would be the first. Lens 3 is right that adoption should be evidence-led and
last: value is contingent on install tagging discipline we haven't measured. Ship the
audit + allowlist machinery first (it is prerequisite work with independent value), then
adopt. Lens 2's own closing rule decides this: "let the §10 eval gate — not intuition —
decide," and that gate cannot even detect the failure mode today.

**Parent path — Lens 3's `suite_history_stats` table vs Lens 4's "never a history key."**
Ruled **for Lens 4**: no path-keyed history table. Rename brittleness makes a path-keyed
store bulk-cold-start on every suite refactor and creates a second rename-sensitive
identity that breaks at different times than `testCaseHash`, producing contradictory
states. The "suite is burning" signal it targeted is partially covered by existing
launch grouping (`co_failure_group_size`, `group_dominance`); the cross-launch remainder
does not justify a fragile key. Comparison features (`same_suite_top1`,
prefix-overlap depth) capture the safe part: worst case after a rename is a 0.0 feature —
today's behavior.

**Description as LLM context — Lens 3 ("partially: storage + LLM context") vs Lens 4 ("avoid, even context is net-negative").**
Ruled **AVOID now, door open**. Lens 4's point stands: description widens the injection
author set (anyone editing tests/TMS) and its Markdown is a richer smuggling format than
log lines, for a context benefit nobody has measured. The 0006 migration pre-provisions a
`description` column (cheap, additive, Lens 3's batching argument), but nothing populates
prompts with it until the LLM sidecar's explanation quality is measured and found wanting
*for lack of intent context* — then it enters only through `sanitize()` + envelope.

**Data route — Lens 1's Route A (extend AMQP via service-api change) vs Route B (read-only RP-Postgres side-channel).**
Ruled **Route B for measurement, Route A for permanence — final call is the owner's**
(open question §5). Route B has working precedent in this repo (`inspector/backend/rp_names.py`
already runs the exact `test_item.path` query), needs no coordinated RP release, and lets
us measure real lift before touching the contract; its cost is an optional RP-DB DSN and
coupling to an internal-but-stable schema. Route A keeps the analyzer AMQP-pure but puts
the feature on ReportPortal's release train and breaks the drop-in premise until
upstreamed. Either way the feature layer must tolerate absent metadata (agents that omit
parameters degrade the same fields), so Route B first costs nothing architecturally.
Crucially, **Phase 0 below needs neither route.**

---

## 3. Incremental plan (only for the USE-NARROWLY item + pre-provisioning)

Ordered so no step reworks an earlier one; each later phase only populates columns and
feature slots an earlier phase already provisioned.

### Phase 0 — sibling identity from the test name (no wire change, ships now)

Delivers the owner's #1-vs-#9 discrimination with zero contract or RP-side work.

1. **Migration `db/migrations/0006_test_metadata.sql`** (next free slot; one file,
   all additive, transactional):
   - `test_item.item_name_hash bigint` (= xxh3_64 of `preprocess_test_item_name(item_name)`,
     already ported at `preprocessing/text_processing.py:710`) + btree
     `(project_id, item_name_hash)`; batched backfill UPDATE.
   - Pre-provisioned for later phases (NULL/default, inert): `param_hash bigint NOT NULL DEFAULT 0`,
     `attributes jsonb NOT NULL DEFAULT '{}'`, `path_text text`, `path_hash bigint`,
     `description text`. **No raw `parameters` jsonb column** (PII ruling, §1).
   - Recreate `test_history_stats` PK as `(project_id, test_case_hash, param_hash)` with
     `param_hash DEFAULT 0` now (table is small, non-partitioned; legacy rows collapse to 0;
     reads fall back `(tch, ph) → (tch, 0)`) — avoids a second PK migration in Phase 1.
   - **`failure_signature` untouched** — the load-bearing choice: no generated-column change,
     no GIN rebuild, no re-embed, zero re-index for existing installs.
2. **`db/repositories/queries.py`**: Stage-B SQL projects `ti.item_name_hash` →
   `HYBRID_RETRIEVAL_VERSION 3→4` (projection-only; fusion/ordering untouched — §5.1
   ordering goldens must stay green, projection goldens updated).
3. **`core/features.py`**: `FEATURE_SCHEMA_VER 3→4`, adding in one batch:
   `same_test_sibling_top1` (same name-hash, different `test_case_hash`),
   `sibling_frac_candidates`, **plus** Phase-1 slots defined now at sentinel defaults
   (`same_params_top1`, `param_first_failure`) so Phase 1 activates them without another
   bump/fallback window. `to_vector_for` back-fills defaults for old snapshots.
4. **`db/repositories/retrieval.py`**, **`core/ingest.py`**: carry the projection, compute
   the features, no-NaN rule (NULL name-hash → 0).
5. **Live-stand verification:** (i) capture one real AMQP payload — confirm whether the
   deployed service-api populates `description` and whether agents report parameters
   (distinct `testCaseHash` per dataset; if all datasets collapse to one hash, Phase 0's
   sibling feature is the *only* dataset signal available and Phase 1's value drops);
   (ii) run 0006 backfill on production-scale volume, time it; (iii) confirm expected
   rule-fallback window after the FSV bump until retrain gate passes (existing safe path,
   but user-visible — announce it); (iv) replay harness on stored vectors to sanity-check
   the new columns are populated and non-constant.

### Phase 1 — parameter identity from RP (gated on route decision + Phase-0 payload capture)

1. Obtain per-item parameter pairs + `codeRef` via the chosen route: **Route A** — optional
   `parameters`/`codeRef` fields on `TestItem` **and** `TestItemInfo` (the suggest path has
   no description/metadata slots at all — must not be forgotten) in `amqp/models.py`,
   mirrored by a service-api change; or **Route B** — batched read-only query lifted from
   `inspector/backend/rp_names.py` patterns (`parameters`, `test_item` tables), optional
   DSN, graceful no-op when unconfigured.
2. Populate `test_item.param_hash` (xxh3_64 of sorted masked `key=value` pairs — reuse §2.1
   masking so volatile values don't shred identity); activate `same_params_top1`,
   `param_first_failure` (slots already exist at FSV 4 — no bump, no fallback window).
3. `stats.py`: cardinality cap — >~100 distinct param-hashes per `testCaseHash` in the
   window ⇒ treat the parameter dimension as noise, collapse to `param_hash=0` row.
4. Optional codeRef-level aggregates (`code_ref_age_days`, `code_ref_fail_rate`) for
   novelty disambiguation — only if the payload capture shows codeRef is reliably present.
5. Verify on stand: for a known data-driven test, dataset #1's first failure shows
   `same_test_sibling_top1=1` / `same_params_top1=0` / `param_first_failure=1` against
   #9's history; §10 eval gate decides whether the columns survive the next retrain.

### Phase 2 — conditional, evidence-gated (not scheduled)

- **Attributes:** first ship allowlist config + MI leakage audit + time-split eval in
  `ml/eval.py`/`ml/retrain.py`; then ingest allowlisted keys into the pre-provisioned
  `attributes` column and add `env_match_top1`/`attr_jaccard_top1` (next FSV bump, batched
  with whatever else that release adds).
- **Parent path:** ride-along only — populate `path_text`/`path_hash`, add
  `same_suite_top1` + prefix-overlap-depth features in the same release as attributes.
  No `suite_history_stats` (adjudication §2).

---

## 4. What we already get for free from `testCaseHash` (the honest baseline)

RP computes it from `codeRef` + reported parameter values, so **each dataset of a
data-driven test already has its own hash**, and analyzer-ng already exploits that:

- **Parameter identity**: dataset #9 and dataset #1 are distinct keys today. Two runs of
  #9 match (`same_test_case_top1=1`); #1 never inherits #9's identity.
- **Per-dataset history**: `test_history_stats` is keyed `(project_id, test_case_hash)` —
  "#9 is the flaky one" is already isolated from #1. `flakiness_score`,
  `test_fail_rate_30d`, `flips_30d`, `test_age_days` are all per-dataset.
- **The owner's scenario is half-solved**: when #1 fails for the first time it presents as
  a cold key (default stats, `test_age_days≈0`) — the model can already lean "new failure,
  don't trust flaky-history priors." What it *cannot* do is know #1 and #9 are siblings of
  the same method (that relation is irreversibly inside the hash) — which is precisely and
  only what Phase 0 adds.
- **Known free-rider caveats** (silent, worth monitoring): agents that report no
  parameters collapse all datasets to one hash (blended history); explicit `@TestCaseId`
  without placeholders does the same; missing `codeRef` makes the hash rename-sensitive;
  `uniqueId` embeds the launch name and is an opaque digest — stored today, drives nothing,
  and that is fine.

So the baseline is stronger than the question assumed: nothing here rescues a broken
status quo — Phase 0 sharpens an already-working per-dataset design at S-size cost.

---

## 5. Open questions for the owner

1. **Route A vs Route B** (§2 adjudication): is a read-only RP-PostgreSQL DSN an
   acceptable deployment requirement for the analyzer proper (precedent: the inspector
   already does it), or must metadata come only via an upstreamed service-api AMQP
   extension — accepting that the feature stays dark on stock RP until the RP release
   lands? This gates Phase 1's timeline; Phase 0 proceeds regardless.
2. **Fleet reality check**: do your agents actually report parameters / codeRef (Phase 0
   verification item 5-i answers this empirically)? If the answer is "mostly not," Phase 1
   shrinks to low priority and Phase 0 is the whole play.
3. **PII stance**: we ruled *no raw parameter-value storage*. If you want values visible
   in the UI/LLM explanations later, that reopens masking + TTL + deletion-flow work —
   confirm you don't need it before we design it.
4. **Attributes ops appetite**: the allowlist is operator-maintained per project,
   deny-by-default. If no one will own that config, attributes stay DEFER indefinitely —
   automatic ingestion of all tags is the leakage scenario and will not ship.
5. **Fallback window**: each FEATURE_SCHEMA_VER bump drops serving to rule fallback until
   the retrain gate passes. One bump is planned (Phase 0). Acceptable, or should Phase 0
   wait for a scheduled retrain window?
