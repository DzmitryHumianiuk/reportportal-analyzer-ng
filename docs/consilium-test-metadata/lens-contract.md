# Lens 1 — Data Contract & Feasibility: per-test metadata for triage

Consilium question: should analyzer-ng use (a) launch parameters of data-driven tests,
(b) test description, (c) attributes, (d) parent path — and can it even get them?
This lens establishes **what data can physically reach the analyzer**, verified against
the code in this repo and the legacy reference.

Sources verified:

- `src/analyzer_ng/amqp/models.py` (analyzer-ng wire contract, mirrors legacy verbatim)
- `../service-auto-analyzer/app/commons/model/launch_objects.py` (legacy payload models)
- `../service-auto-analyzer/test_res/index_service_test_data.json` (real payload fixture)
- `../service-auto-analyzer/app/commons/request_factory.py`, `app/ml/boosting_featurizer.py`,
  `app/service/suggest_service.py` (legacy consumption of `uniqueId`/`testCaseHash`)
- `src/analyzer_ng/core/ingest.py`, `core/features.py`, `db/repositories/{retrieval,stats}.py`
  (analyzer-ng consumption)
- `inspector/backend/rp_names.py` (existing read-only RP-PostgreSQL side-channel)
- RP service-api conventions for `uniqueId` / `testCaseHash` generation (knowledge, flagged below)

---

## 1. What is on the wire today

The AMQP `TestItem` (identical in analyzer-ng `amqp/models.py:74` and legacy
`launch_objects.py:158`) carries per item:

| Field | Type | Populated in practice? |
|---|---|---|
| `testItemId` | int | yes |
| `isAutoAnalyzed` | bool | yes |
| `uniqueId` | str | yes (opaque MD5, see §3) |
| `testCaseHash` | int | yes (see §3) |
| `testItemName` | str | yes |
| `description` | `str \| None` | **field exists in schema; population by service-api unverified** — absent from every legacy test fixture (`index_service_test_data.json`, `cluster_service_test_data.json`) and never read by legacy code |
| `issueType`, `issueDescription`, `originalIssueType` | str | yes (issue fields, not test metadata) |
| `startTime`/`endTime`/`lastModified` | timestamps | yes |
| `linksToBts` | list[str] | schema-only; never consumed by legacy either |
| `logs` | list[Log] | yes — the payload the whole pipeline runs on |

**Not on the wire, at all:** parameters list (keys or values), attributes (key:value
tags), parent/suite path, codeRef. Confirmed by both model files and the legacy
fixture payloads, which contain exactly `testItemId, isAutoAnalyzed, issueType,
testItemName, testCaseHash, uniqueId, logs` per item.

The `suggest` request (`TestItemInfo`, models.py:107) is **narrower still**: it has
`uniqueId`, `testCaseHash`, `testItemName`, `logs` — no `description` field even in
the schema. Any metadata plan that only extends `TestItem` misses the suggest path.

## 2. What each side uses today

**Legacy** indexes `unique_id` and `test_case_hash` into every per-log OpenSearch doc
(`request_factory.py:193-194`), boosts query matches on them (`BoostTestCaseHash=2.0`
in `SearchConfig`), filters candidate sets so only the best hit per `test_case_hash`
survives (`boosting_featurizer.py:45,213`), and derives the
`is_the_same_test_case`-style feature by field equality (`boosting_featurizer.py:431,457-461`).
Suggest deliberately *blanks* `unique_id`/`test_case_hash` on cluster-based requests
to prevent self-boosting (`suggest_service.py:443-446`).

**analyzer-ng** persists `test_case_hash` and `unique_id` per item
(`retrieval.py:88-113`), computes `same_test_case` on retrieved candidates
(`retrieval.py:664`) feeding the `same_test_case_top1` LightGBM feature
(`features.py:132,338`), and maintains per-hash history/flakiness windows
(`stats.py`, bumped from `ingest.py:148-154,191-192`; spec 02 §2.10
`test_history_stats`). `unique_id` is stored but drives no feature.
`description` is accepted by the pydantic model and then **dropped** — no reference
in `ingest.py` or `analysis.py`.

## 3. What `testCaseHash` and `uniqueId` actually encode

(Per RP service-api conventions — `TestCaseHashGeneratorImpl` / `UniqueIdGeneratorImpl`.
Note: the hashes are computed **server-side in service-api at item start**, from
client-supplied inputs; agents influence them via `codeRef`, reported parameters,
and an optional explicit `testCaseId`. Flagged as knowledge-based — confirm against
the deployed RP version if a decision hinges on exact composition.)

- **`testCaseHash`** = hash of the *test case id*, which defaults to
  `codeRef` + the reported **parameter values** (`codeRef[param1,param2,...]`).
  If the agent supplies an explicit `testCaseId` (e.g. `@TestCaseId` annotation),
  that string is hashed instead — possibly *without* parameters.
  If `codeRef` is missing, service-api falls back to the item's name-path.
- **`uniqueId`** = `"auto:" + MD5(launch name + item name-path from root + parameter values)`.
  So parent path **and** parameter identity are already folded into `uniqueId` —
  but irreversibly, as an opaque digest.

**Consequence for the data-driven scenario (a):** when the agent reports parameters,
dataset #1 and dataset #9 of the same test already have **different `testCaseHash`
and `uniqueId`**. Parameter *identity* is therefore already usable — and analyzer-ng
already uses it: `same_test_case_top1` and `test_history_stats` are keyed per-hash,
so "dataset #9 is historically flaky, dataset #1 is not" is exactly what the
per-hash flakiness window expresses today. What is *missing* is parameter **values**
(semantic content: which dataset, which env, which browser), and any way to say
"these two hashes are datasets of the *same* test" (the parent relation is inside
the opaque hash).

**Failure modes (all real, all silent):**

1. Agent reports no parameters → all datasets collapse to one `testCaseHash`;
   dataset identity invisible; history stats blend 10 tests into one.
2. Explicit `testCaseId` without parameter placeholders → same collapse.
3. Missing `codeRef` → hash derived from name-path; any suite/test rename resets
   history (stats keyed on the hash start from zero).
4. `uniqueId` embeds the **launch name** → same test under two launch names gets
   two uniqueIds (one reason legacy leaned on `testCaseHash` instead).
5. Retries share the hash of the retried item (by design; benign for triage).

## 4. Per-datum feasibility verdict

| Datum | On wire? | Contract extension | DB side-channel |
|---|---|---|---|
| (a) parameter **identity** | **effectively yes** (via `testCaseHash`/`uniqueId`) | n/a — already used | n/a |
| (a) parameter **values** | no | service-api change | `parameters` table (item_id, key, value) |
| (b) description | schema slot exists on `TestItem`; population unverified; **no slot** on `TestItemInfo` | small if service-api already sends it (just consume); otherwise service-api change + `TestItemInfo` extension | `test_item.description` |
| (c) attributes | no | service-api change | `item_attribute` table (item_id, key, value, system flag) |
| (d) parent path | opaquely inside `uniqueId` only | service-api change | `test_item.path` (ltree of item ids) + name join — **the inspector already runs exactly this query** (`rp_names.py:46` `SELECT item_id, launch_id, path::text FROM test_item ...`) |

### Route A — extend the RP-side AMQP payload (contract change)

- Requires a **Java service-api change**: extend `IndexTestItem` (+`TestItemInfo`
  analog for suggest) and the `AnalyzerUtils` mapping, then a coordinated RP release.
- Additive JSON fields are *technically* safe for coexistence: both legacy and
  analyzer-ng pydantic models ignore unknown fields, so a patched service-api can
  feed either analyzer. But it **breaks the drop-in premise**: analyzer-ng could no
  longer claim to work against any stock RP installation; the feature would be dark
  on every unpatched deployment, so the code must treat the fields as optional
  forever. Upstreaming to ReportPortal is possible but on someone else's release
  train. Invasiveness: **high** organizationally, low mechanically.

### Route B — read-only side-channel to RP PostgreSQL

- Precedent already in this repo: the inspector connects read-only to RP's DB and
  resolves `test_item.path`/`launch_id` with memoized batch queries
  (`inspector/backend/rp_names.py`). Parameters, attributes, description, path are
  all one indexed `item_id = ANY(%s)` query away; at analyze/suggest time the items
  are already committed to RP's DB, so timing works, and one batched query per
  launch is negligible next to embedding cost.
- Costs: **breaks AMQP-only purity of the analyzer proper** (new deployment
  requirement: RP DB DSN + read-only role); couples analyzer-ng to RP's *internal*
  schema, which is not a public API and can shift between RP versions (the ltree
  `path`, `parameters`, `item_attribute` shapes have been stable for years, but
  there is no contract); needs graceful degradation (metadata features must be
  optional, exactly like the agent-doesn't-report-params case in §3 — which the
  feature layer must tolerate anyway).
- Invasiveness: **low mechanically** (the inspector's `db.py`/query pattern can be
  lifted), **medium** operationally.

### Route C — do nothing extra for identity

Defensible for (a)-identity specifically: the scenario in the question ("only
dataset #9 ever fails, suddenly #1 fails") is already *distinguishable* today —
different hash → `same_test_case_top1=0` against #9's history, and #1's own
`test_history_stats` show a first-time failure (low flakiness → stronger
"new failure" signal). What today's design cannot do is *relate* #1 to #9 as
siblings (shared codeRef) or reason over parameter values — that requires Route A
or B.

## 5. Bottom line for the consilium

- Parameter **identity** (a): already on the wire inside `testCaseHash`; already
  consumed by `same_test_case_top1` + per-hash `test_history_stats`. No new
  plumbing needed; the marginal win would come from *sibling grouping* (same test,
  different datasets), which needs `codeRef` or parameter values from Route A/B.
- Description (b): possibly one `if` away — the wire schema has the slot; verify
  with a live payload capture whether the deployed service-api populates it before
  designing anything. Suggest path (`TestItemInfo`) has no slot regardless.
- Attributes (c) and path (d): **not obtainable without Route A or B.** Route B is
  the pragmatic one — the repo already ships a working read-only RP-DB client with
  the exact `test_item.path` query, and the analyzer already has a PostgreSQL
  dependency of its own, so "AMQP-only purity" is the principle being traded, not
  a technical barrier.
- Whatever is added must be optional-by-design: agents that omit parameters/codeRef
  (§3 failure modes) silently degrade the same fields, so the feature layer needs
  the missing-metadata path anyway.
