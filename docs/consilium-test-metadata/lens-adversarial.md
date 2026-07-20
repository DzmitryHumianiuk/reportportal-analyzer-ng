# Consilium — Test-Metadata Enrichment, Lens 4: Adversarial / Ops Failure Modes

**Question:** should analyzer-ng consume (a) launch parameters, (b) test description,
(c) attributes, (d) parent path — and what can go wrong if it does?

**Scope of this lens:** only the ways each data kind can *hurt*. Benefits are other
lenses' business. Every claim below is anchored to the actual pipeline as built:

| Stage | Anchor in code |
|---|---|
| Wire contract | `src/analyzer_ng/amqp/models.py` — `TestItem` carries `uniqueId`, `testCaseHash`, `testItemName`, `description`, `logs`. **No** parameters list, attributes, or parent/path today. |
| Signature build | `src/analyzer_ng/ml/signature.py` — Drain masking (`mask_text`), exception fingerprint, `error_hash`, truncated signature document. |
| Retrieval | `src/analyzer_ng/db/repositories/retrieval.py` — Stage A KB modes (`PgKBStore`, purity/support, EWMA centroids), Stage B hybrid FTS + 384-dim halfvec fused with RRF k=60. |
| Features / GBM | `src/analyzer_ng/core/features.py` — versioned ordered list (`FEATURE_SCHEMA_VER = 3`), `same_test_case_top1`, `test_history_stats` block, `LLM_UNKNOWN` sentinel pattern for optional columns. |
| History stats | `src/analyzer_ng/core/ingest.py`, `db/repositories/stats.py` — keyed on `testCaseHash`. |
| LLM sidecar | `src/analyzer_ng/llm/sanitizer.py` — `sanitize()` + nonce envelope (`wrap_untrusted`) for *log* text; spec 04 §5 injection defense. |

A cross-cutting risk applies to **all four kinds** before any per-kind analysis:

> **R0 — wire-contract skew.** None of these fields exist on the AMQP contract. Adopting
> any of them requires an RP-backend change, and analyzer-ng will face mixed fleets
> (old backend omits the field). If "missing" is not modeled explicitly (the
> `LLM_UNKNOWN`-style constant-sentinel pattern already used in `features.py`), the GBM
> silently learns "field absent ⇒ old backend ⇒ whatever label mix that era had" —
> a time-leakage feature, not a failure signal. **Mandatory guard for anything adopted:**
> optional-with-sentinel wire semantics + `FEATURE_SCHEMA_VER` bump + train/serve
> parity via the existing snapshot-at-prediction mechanism.

---

## (a) Launch PARAMETERS of data-driven tests

### Failure modes

1. **High-cardinality / generated values poisoning retrieval.** Real parameterized
   suites pass UUIDs, timestamps, temp file paths, random seeds, generated emails.
   If raw parameter values enter the signature document, they hit both retrieval legs:
   - *FTS*: tsvector fills with near-unique tokens → dictionary bloat, degenerate
     rank (every item matches nothing or itself), RRF fusion skews toward the
     vector leg unpredictably.
   - *Embeddings*: 384-dim MiniLM-class vectors are dominated by shared surface junk;
     two unrelated failures sharing a parameter *shape* ("uuid uuid uuid") drift
     cosine-close. Drain's `mask_text` exists precisely because this already happens
     with log bodies — re-importing unmasked text through a side door undoes it.
   - **Likelihood:** very high — near-universal in data-driven suites, which are the
     exact suites this feature targets.
   - **Blast radius:** Stage B candidate lists (wrong neighbors → wrong inherit),
     KB centroid drift (EWMA pulls modes toward parameter noise → purity decays or,
     worse, stays numerically high while semantically wrong), and every downstream
     similarity feature the GBM consumes. This is the widest blast radius in the
     whole proposal: it poisons the *inputs* to everything.

2. **10k-distinct-dataset explosion in history stats.** If parameter identity keys
   `test_history_stats` (finer than `testCaseHash`), a suite with thousands of
   datasets shreds history into singleton rows: every flakiness/recurrence feature
   returns its cold-start default forever. The scenario in the question ("dataset #9
   always fails, suddenly #1 fails") is real, but the naive fix destroys the very
   history signal that detects it.
   - **Likelihood:** high in property-based / generated-matrix suites; medium elsewhere.
   - **Blast radius:** features stage only (`test_history_stats`, `same_test_case_*`),
     but it degrades silently — no error, just permanently-default features.

3. **PII / secrets in parameter values.** Login/password fixtures, tokens in URL
   params, customer emails as test data. Today analyzer-ng stores masked signatures;
   storing raw parameter values creates a *new* PII store that `item_remove` /
   `remove_by_launch_start_time` flows must fully cover, plus GDPR retention scope.
   - **Likelihood:** medium-high (auth tests exist everywhere).
   - **Blast radius:** compliance/legal, plus leakage into LLM prompts and into
     `SuggestAnalysisResult.modelFeatureValues` shown in the RP UI.

4. **Parameter identity is already half-adopted.** `testCaseHash` folds
   codeRef+parameters client-side, so "same dataset" is partially captured. Adding a
   second, differently-computed parameter identity creates two disagreeing notions of
   "same test case"; a mismatch (RP hash version change, client plugin variance)
   yields contradictory `same_test_case` features.

### Mandatory guards if adopted

- **Never** put raw parameter values into FTS or the embedded document. Hard rule.
- Consume **identity only**: a stable hash of the ordered `(name, value)` list
  (server-side, versioned), used as (i) an equality feature vs. top-K candidates
  ("same test, same dataset" / "same test, different dataset" as two booleans —
  this alone answers the dataset-#1-vs-#9 question) and (ii) an *optional secondary*
  key in history stats with a **cardinality cap** per `testCaseHash` (e.g. >100
  distinct param-hashes in the window ⇒ treat the parameter dimension as noise and
  collapse to the existing hash-level stats).
- Values, if stored at all for display: Drain-mask + length-cap + TTL, excluded from
  deletion-flow blind spots, and behind the existing `sanitize()`/envelope if ever
  shown to the LLM.

### Verdict: **adopt-narrowly** — parameter-identity hash as GBM features with a
cardinality cap; raw values stay out of retrieval, embeddings, and storage.

---

## (b) Test DESCRIPTION

### Failure modes

1. **Template spam / copy-paste convergence.** Descriptions in real RP installs are
   TMS-synced or scaffold-generated; hundreds of unrelated tests share boilerplate
   ("Verify that the user can…", identical Given/When/Then skeletons). Any
   similarity use (FTS or embedding) makes *unrelated* tests look alike — the exact
   inverse of the tool's purpose. Unlike log text, Drain cannot rescue this:
   descriptions are grammatical prose, not templated machine output, so masking
   heuristics don't separate signal from boilerplate.
   - **Likelihood:** high — boilerplate descriptions are the norm, not the edge case.
   - **Blast radius:** Stage B retrieval and KB mode formation. Boilerplate-driven
     candidate inflation directly corrupts mode membership → purity metric becomes
     unreliable → the Stage-A "trusted KB match" path inherits labels across
     unrelated tests. That is the highest-trust path in the pipeline; poisoning it
     is worse than poisoning ranked suggestions.

2. **Staleness.** Descriptions describe *intent at authoring time*; they are not
   updated when the test changes. A similarity signal that never expires and never
   reflects current behavior is noise with a trend: it correlates with test *age*,
   and age correlates with label mix — another subtle leakage channel for the GBM.
   - **Likelihood:** near-certain over any multi-year project.
   - **Blast radius:** GBM feature quality; slow, unmeasurable decay.

3. **Free-text injection surface.** `description` is user-authored rich text
   (Markdown in RP). If routed to the LLM sidecar as context, it is an injection
   channel with a *wider author set* than logs (anyone editing tests or the TMS).
   The sanitizer exists and is pure/golden-tested — but today it is applied to log
   excerpts; description would need the identical `sanitize()` + `wrap_untrusted`
   treatment, and its Markdown (links, images, HTML) is a richer smuggling format
   than log lines.
   - **Likelihood of deliberate attack:** low; of *accidental* prompt derailment
     (description containing instructions like "ignore this test"): medium.
   - **Blast radius:** LLM extractor outputs → two ordinal GBM columns + judge
     verdicts → suggestion promotion. Contained, but it touches the label path.

4. **It's already on the wire and already ignored — for a reason.** `TestItem.description`
   ships today and the legacy analyzer let it rot unused. Adopting it costs no
   contract change (the only kind with R0 = zero), which makes it *tempting*; the
   failure modes above are why cheap ≠ safe.

### Mandatory guards if adopted

- Never in FTS/embedding/KB documents. At most: LLM-sidecar context, sanitized and
  enveloped exactly like log excerpts, length-capped, with the extractor schema
  unchanged (so a hostile description can at worst bias an enum, not inject text
  into the UI).
- Boilerplate detector (per-project description-frequency count; a description shared
  by >N tests is dropped) if any similarity use is ever reconsidered.

### Verdict: **avoid** for retrieval/features; the only defensible use is optional
sanitized LLM context, and even that is low-value relative to the added injection
surface. Skip.

---

## (c) ATTRIBUTES (key:value tags)

### Failure modes

1. **Label leakage via auto-added tags — the classic shortcut trap.** Mature RP
   installs have CI-injected attributes (`build_type:nightly`, `env:staging`,
   `team:payments`, `known_issue:JIRA-123`, `flaky:true`). Several of these
   *correlate with the triage label by construction*: `known_issue:*` is literally
   applied because a human triaged it; environment tags correlate with `si`
   (system issue) rates. A GBM given these columns learns the tag, not the failure.
   Offline metrics inflate (the tag is present in historical training rows), then
   production collapses the day the tagging policy changes — or worse, becomes a
   **feedback loop**: analyzer labels item → automation tags item → tag trains
   analyzer. The pipeline's own retrain path (`ml/retrain.py`, labels from
   `defect_update`) has no leakage audit today because no current feature *can* leak;
   attributes would be the first.
   - **Likelihood:** very high — auto-tagging is standard practice, and the
     correlated-tag case is the *common* case, not the adversarial one.
   - **Blast radius:** GBM training and eval — the decision layer itself. Worst
     failure class in ML terms: silently wrong with great-looking metrics.

2. **Cardinality / churn.** Attribute vocabularies are unbounded and drift
   (release tags: `build:4711`, `commit:abc123`). One-hot or hashed encodings churn
   the feature space every release; `FEATURE_SCHEMA_VER` bumps become perpetual and
   model artifacts stale on arrival.
   - **Likelihood:** high. **Blast radius:** features/training ops burden.

3. **Injection channel to the LLM.** Attribute values are short but attacker-cheap
   (set from test code by any repo contributor). Same mitigation exists
   (`sanitize()` + envelope) — but only if attributes are actually routed through it;
   the temptation is to treat "structured tags" as trusted and interpolate them raw
   into prompts. They are not trusted input.
   - **Likelihood:** low-medium. **Blast radius:** LLM extractor/judge, contained by
     the enum-only output schema *if* the envelope discipline holds.

4. **Cross-project semantics.** `train_models` supports `additional_projects`;
   attribute keys mean different things per project (`severity:high` = test priority
   in one, defect severity in another). Pooled training over raw attribute features
   mixes vocabularies.

### Mandatory guards if adopted

- **Operator-maintained allowlist of keys per project** — deny by default. No
  allowlist, no attributes. Automatic ingestion of all tags is the leakage scenario.
- Leakage audit in the training pipeline: per-attribute-feature mutual information
  with the label reported at train time, plus a time-split (not random-split) eval —
  a mandatory precondition, since the current eval would not catch tag-shortcut
  learning.
- Encode as small ordinal/boolean sets (like `FAILING_LAYER_ORDINAL`), never raw
  strings; cap distinct values per key; sentinel for missing.
- Prohibit tags written by any automated triage/labeling process (breaks the
  feedback loop by construction). Sanitize + envelope for any prompt use.

### Verdict: **adopt-narrowly** — a handful of allowlisted, human-semantic,
low-cardinality keys as GBM features, gated on a leakage audit existing first.
Without the allowlist + audit machinery, avoid: this is the kind whose failure mode
is invisible until production.

---

## (d) PARENT path / suite location

### Failure modes

1. **Rename brittleness / history discontinuity.** Suite refactors (package moves,
   suite renames, re-nesting) change the path for hundreds of items at once. Any
   history or KB keying on path suffers a bulk cold-start: same failures, zero
   continuity. Note `testCaseHash` (codeRef-based) *already* has this weakness;
   path-keying doubles the number of rename-sensitive identities and they break at
   *different* times (file move vs. suite move), producing half-broken states where
   the two identities disagree.
   - **Likelihood:** medium frequency but bursty — a single refactor PR invalidates
     an entire project's path history overnight.
   - **Blast radius:** whichever stage keys on it. As a *retrieval filter* or
     *history key*: severe (silent recall collapse). As a *comparison feature*
     ("query and candidate share a suite prefix"): mild — the feature just reads
     0.0 after a rename, degrading gracefully to today's behavior.

2. **Path-as-text pollution.** Paths tokenize badly in FTS (dots, slashes,
   CamelCase) and embed meaninglessly; deep suites share long prefixes
   (`com.company.tests.regression...`) making everything in a monorepo "similar".
   Same class of harm as (a)-1, lower severity.
   - **Likelihood:** high if naively concatenated into the document.
   - **Blast radius:** Stage B ranking, KB centroids.

3. **Suite-level shortcut learning (mild leakage).** Some suites are structurally
   flaky (integration vs. unit); a suite-identity feature lets the GBM learn
   "suite X ⇒ ab". That is *partly legitimate signal* (unlike (c)-1, the
   correlation is causal, not annotation-induced), but it can dominate log evidence
   for rare-failure suites. Guarded by the same time-split eval as (c).

4. **PII/infra disclosure:** negligible — paths are code identifiers.

### Mandatory guards if adopted

- Feature-only, never a retrieval key or history-stats key: booleans/ordinals
  like `same_parent_suite_top1`, `parent_path_prefix_overlap` computed
  query-vs-candidate at feature time. Graceful zero on any rename.
- Normalize before compare (strip parameterization indices, lowercase, split on
  separators); compare by suffix/leaf and prefix-overlap depth, not exact string.
- Keep it out of FTS/embedding documents entirely.

### Verdict: **adoptable-with-guards** — as comparison features only. It is the
lowest-risk kind of the four *because* the guard is structural (feature-only ⇒
worst case is a zeroed feature), but it still requires the R0 sentinel treatment
and a contract change to obtain at all.

---

## Summary table

| Data kind | Nastiest failure mode | Stage poisoned | Likelihood | Verdict |
|---|---|---|---|---|
| (a) Parameters | UUID/generated values undo Drain masking; 10k-dataset history shredding; PII store | Retrieval + KB + features (widest) | Very high | **Adopt-narrowly**: identity-hash features + cardinality cap; raw values banned from retrieval/storage |
| (b) Description | Boilerplate makes unrelated tests similar; poisons highest-trust KB inherit path; Markdown injection surface | Retrieval + KB purity + LLM | High | **Avoid** (at most sanitized LLM context; low value) |
| (c) Attributes | Auto-tag label leakage → shortcut model + feedback loop; great offline metrics, wrong in prod | GBM training/eval (worst class) | Very high | **Adopt-narrowly**: allowlisted keys only, leakage audit + time-split eval as hard precondition |
| (d) Parent path | Suite refactor bulk-breaks any keying; prefix pollution in documents | Features (if guarded); retrieval (if not) | Medium, bursty | **Adoptable-with-guards**: comparison features only, normalized, never a key |

**Cross-cutting mandatory work for any adoption:** R0 optional-with-sentinel wire
semantics; `FEATURE_SCHEMA_VER` bump with snapshot parity; deletion/TTL coverage for
any newly stored field; `sanitize()` + nonce envelope for any metadata reaching the
LLM; time-split leakage-aware eval in `ml/eval.py` before any attribute feature ships.
