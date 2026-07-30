# analyzer-ng demo-data generator

Compiles [`SCENARIOS.md`](SCENARIOS.md) (the authoritative timeline + scenario
spec) and [`corpus/*.json`](corpus/) (per-framework failure archetypes) into
concrete ReportPortal launches / items / logs and uploads them to the live
stand via the RP **async v2 reporting API**, with client-supplied **back-dated**
timestamps so the ~4-week history exists in one sitting.

The output is a self-scoring demo: every generated item is tagged with
`scenario:Sxx` (and `adv_case:ADV-n.x` where applicable), and
[`expected.json`](expected.json) records what the analyzer *should* do for each
probe, so predictions can be joined to ground truth after a run.

---

## 1. Requirements

- Python 3.11+, `requests` (only third-party dep). No other deps.
- The RP stand reachable via two port-forwards:

  ```bash
  kubectl port-forward svc/reportportal-uat 9999:9999 &
  kubectl port-forward svc/reportportal-api 8585:8585 &
  ```

  Auth base `http://localhost:9999/uat`, API base `http://localhost:8585/api`,
  superadmin / superadmin (token flow as in
  `deploy/minikube/scripts/_lib.sh`). Override with `--rp-uat` / `--rp-api`.

The generator **creates** the three projects (`webshop-ui`, `payments-services`,
`frontend-apps`) if missing and configures per-project analyzer settings itself.

---

## 2. Running

```bash
cd demo-data

# 1. Inspect the whole-timeline plan (uploads nothing):
python3 generate.py --dry-run

# 2. (Re)write the expected-outcome manifest:
python3 generate.py --emit-expected

# 3. Tiny end-to-end smoke against the live stand
#    (creates/reuses webshop-ui, uploads one small launch, verifies items+logs):
python3 generate.py --smoke

# 4. Full upload, phase by phase (recommended: run in order, watch each phase):
python3 generate.py --phase 0     # backdated pre-history pack
python3 generate.py --phase 1     # FEA cold analysis (install-wide events < 50)
python3 generate.py --phase 2     # WSU+PSV history + defect_update replay
python3 generate.py --phase 3     # probe launches, analyzed unlabeled
#   ...or the entire timeline at once:
python3 generate.py

# Filters (compose with any mode):
python3 generate.py --dry-run --phase 2
python3 generate.py --day 2026-07-01           # just the outage day
python3 generate.py --log-workers 4            # gentler on the laptop VM
```

Determinism: all expansion/placement is seeded (`gen/config.SEED = 20260717`),
so re-runs produce byte-for-byte the same corpus (RP launch UUIDs differ, the
item content does not).

---

## 2a. Remediation / operations modes

These exist for recovering a partial run (a crash between upload and triage) and
for the analyzer coverage the phased upload alone does not produce:

```bash
# Re-send history triage that a crashed/resumed run never applied. Idempotent:
# reads each item's stamped ground_truth attribute (order-independent) and skips
# item ids listed in the exclude file (items that already have a label_event).
python3 generate.py --replay-only --exclude-items-file /tmp/exclude_items.txt

# Give history items a suggestion.features snapshot so the GBM can train.
# (reset already-triaged items to To-Investigate, analyze, then --replay-only
#  re-applies the human labels, whose label_events now join to those features.)
python3 generate.py --analyze-history
```

**Why `--analyze-history` is needed (hard lesson).** GBM training is
snapshot-only: `ml/trainer.py` drops any label_event whose item has no stored
`suggestion.features`. History launches uploaded in phase 2 with auto-analysis
**off** never produced that snapshot, so their labels — however many
label_events you replay — contribute **zero** training rows and the GBM never
ships. The correct phase-2 flow is **analyze first (features), then replay the
human triage (labels)**. A future generator revision should enable AA during
phase-2 history upload and analyze each launch before its `defect_update` replay,
making `--analyze-history` unnecessary. Also note: `defect_update` only creates a
`label_event` if the item is already in `analyzer.test_item`; firing triage
immediately after launch-finish races indexing and silently drops most events —
replay once indexing has settled.

If the retrain scheduler is stuck in its cold-phase 1/hour debounce, force a
retrain out-of-band by publishing `train_models` (route reason bypasses the
event-trigger throttle):

```bash
kubectl exec pod/reportportal-rabbitmq-0 -c rabbitmq -- curl -s \
  -u rabbitmq:rabbitmqpassword -H 'content-type:application/json' -XPOST \
  http://localhost:15672/api/exchanges/analyzer/analyzer-default/publish \
  -d '{"properties":{"content_type":"application/json"},"routing_key":"train_models",
       "payload":"{\"model_type\":1,\"project\":3,\"gathered_metric_total\":0}","payload_encoding":"string"}'
```

## 3. Phase meanings (SCENARIOS.md §6)

| Phase | What runs | Analyzer state | Why |
|---|---|---|---|
| **0** | Backdated pre-history pack (Dec 2025 – May 2026) | AA off | > 180 d stale-guard items, recency ladder, old fingerprints |
| **1** | `frontend-apps` launches (Jun 29) | AA **on, FEA only** | proves rule fallback / cold model while install-wide `label_events` < 50 (`method ∈ {hash,kb,rule_cold}`, never `gbm`) |
| **2** | `webshop-ui` + `payments-services` history (Jun 29 → Jul 14) + `defect_update` replay | AA off on WSU/PSV | builds label history; install crosses 50 → 100 → 300 → GBM train, then WSU isotonic |
| **3** | Probe launches (Jul 17 demo day) analyzed unlabeled | AA **on, all** | the probe decisions are the assertions scored against `expected.json` |
| **4** | S43 feedback sweep (Jul 16) | — | accept/override/flip `defect_update`s → retrain + ship-gate |
| **5** | S44 route calls (cluster / search / suggest_patterns) + S45 isolation sequence | — | grouping stability, cross-project isolation invariants |

Phases 0–3 are fully implemented. Phases 4–5 print the actions they cover and
run best-effort route calls against the uploaded probe launches; deepen them
against the real DB once phase 3 has landed (see "Deviations").

### 3a. The "Make Decision Showcase" launch (S46)

Phase 3 also uploads ONE dedicated launch — **`Make Decision Showcase`**
(webshop-ui, Jul 17 21:00, 18 failed items) — whose items cover every
Make Decision / Bench branch in a single walkthrough: exact-hash auto
(Past decision agrees), KB-mode auto, suggest-confirm, suggest-disagree
(conflicting defects), declined dock (`band=below_suggest` — needs
`ANALYZER_SUGGEST_BELOW_ENABLED=true` on the analyzer and service-ui ≥ ng4),
AI-guess-only (cold-start rubric over a novel failure), a silent modal
(failed item with INFO-only logs → empty signature), a 3-member exact
launch group, and an 8-member fresh-fingerprint `si` burst. Items are tagged
`scenario:S46` plus `scenario:BENCH-<case>`; the case list lives in
`gen/schedule.py::BENCH_SHOWCASE`, the spec row in SCENARIOS.md §2.8.
Open the launch, walk its items top-to-bottom with the Make Decision modal,
and every modal variant appears exactly once (the group/burst blocks show the
group cues).

### 3b. "Make Decision Showcase 2" (S46, the fixed branches)

On a live stand the original showcase left four branches undemonstrable:
`suggest-confirm`, `suggest-disagree`, `declined-dock` and `ai-guess-only`
all collapsed into the auto-hash modal. Root cause: those cases reused
archetypes (JAVA-SEL-24/27) whose other items were human-labeled, so the
showcase item shared an `error_hash` with labeled history and stage-A
exact-hash inheritance won before the GBM ever ran; JAVA-SEL-32 additionally
retrieved 0.92 neighbors through shared TestNG boilerplate frames.

The fix is purely additive, NEW uploads only. Four fresh archetype families
(`JAVA-SEL-35..38`, placed nowhere else: POLICY `target=0`) obey a hash-drift
rule: the showcase variant's first ERROR line differs from every history
variant in words that survive masking (never only numbers, ids or paths),
while frames are shared exactly where fuzzy similarity is wanted:

- **JAVA-SEL-35** (`suggest-confirm`): v1..v3 labeled pb history, one frame
  set, four wordings. No exact hash, unanimous retrieval, `band=suggest`,
  p* in [0.45, 0.75).
- **JAVA-SEL-36** (`suggest-disagree`): eight history items, v1..v4 pb and
  v5..v8 ab, two wording families over one frame set. Entropy near 1,
  conflicting pb/ab suggest rows.
- **JAVA-SEL-37** (`declined-dock`): v1..v2 labeled pb but heavily drifted
  (only the exception class and one frame shared); v3..v4 textually near the
  showcase item but `ground_truth=ti`, so the replay skips them and they stay
  unlabeled. Calibrated p* lands in [0.30, 0.45): `ek=decline` rows,
  `band=below_suggest` (needs `ANALYZER_SUGGEST_BELOW_ENABLED=true`).
- **JAVA-SEL-38** (`ai-guess-only`): one alien variant, never-seen exception
  class, unseen `io.hawkinslab.*` frames, no TestNG boilerplate. Retrieval
  stays below the floor, classical abstain, `coldstart_rubric` row only.

The labeled history lives in three dedicated phase-2 launches
(`gen/schedule.py::BENCH_HISTORY`): `Bench History Confirm` (Jul 8, SEL-35
v1..v3 twice = 6 H), `Bench History Split` (Jul 8, SEL-36 v1..v8) and
`Bench History Weak` (Jul 9, SEL-37 v1..v4). The probes live in
`Make Decision Showcase 2` (webshop-ui, Jul 17 22:00, 4 P items, one per
fixed branch; `gen/schedule.py::BENCH_SHOWCASE_2`). The original
`Make Decision Showcase` launch and its items are never re-uploaded or
edited.

**Upload sequence** to take a live stand from "current state" to "all four
branches demonstrable" (run from `demo-data/`):

```bash
# 1. sanity: inspect exactly what will be uploaded
python3 generate.py --dry-run --launch "Bench History"
python3 generate.py --dry-run --launch "Make Decision Showcase 2"

# 2..4. upload the three history launches (phase 2, analyzer off, labels replayed)
python3 generate.py --phase 2 --day 2026-07-08 --launch "Bench History Confirm"
python3 generate.py --phase 2 --day 2026-07-08 --launch "Bench History Split"
python3 generate.py --phase 2 --day 2026-07-09 --launch "Bench History Weak"

# 5. make sure every labelable history item is triaged
#    (gt=ti items in Bench History Weak stay unlabeled by design)
python3 generate.py --replay-only --launch "Bench History"

# 6. reset -> analyze so the history items get suggestion.features for the GBM
python3 generate.py --analyze-history --launch "Bench History"

# 7. re-attach the human labels to those features
python3 generate.py --replay-only --launch "Bench History"

# 8. upload the new showcase launch and analyze it (phase 3, analyzer on)
python3 generate.py --phase 3 --day 2026-07-17 --launch "Make Decision Showcase 2"

# 9. refresh the expected-outcome manifest
python3 generate.py --emit-expected
```

`--launch` scopes every pass (upload, `--replay-only`, `--analyze-history`)
to launches whose name contains the given substring, so the passes above
never touch the rest of the stand.

Acceptance, via the Inspector journey API and the modal on the four new
items: the confirm item has `decision.method != hash`, `band=suggest` and
unanimous pb rows with conf in [0.45, 0.75); the disagree item has
`band=suggest` with two defect groups in its rows; the declined item's rows
carry `ek=decline` with conf in [0.30, 0.45) and `decision.band=below_suggest`;
the ai-guess item's reply contains only the `coldstart_rubric` row. Negative
checks: no new item shares an `error_hash` with labeled history (method is
never `hash`), and the original showcase launch is byte-identical (nothing
re-uploaded or edited).

Roles per item: **H** = history (a `defect_update` replays its ground-truth
label after the launch finishes); **P** = probe (stays To-Investigate, gets
analyzed); **decoy** = passing item carrying the same logs (S23 spam / S24
herrings) with plausible info-level pass logs.

---

## 4. Scoring against `expected.json`

`expected.json` has one row per probe item:

```json
{
  "archetype_id": "JAVA-SEL-01", "project": "webshop-ui",
  "scenario": ["S09"], "adv_case": null, "ground_truth": "pb",
  "expected_action": "auto",        // auto | suggest | abstain
  "expected_label": "pb",
  "expected_method": "hash"          // hash | kb | gbm | rule_cold | "" (unspecified)
}
```

After a full run, join predictions to these rows via the item attributes the
generator stamps (`scenario`, `adv_case`, `archetype`, `ground_truth`) plus the
analyzer's `suggestion` / `launch_group` rows:

- **auto** probe → analyzer must confidently apply `expected_label` with
  `suggestion.method == expected_method` (where specified).
- **suggest** probe → analyzer must return `expected_label` in its top-k but not
  auto-apply.
- **abstain** probe (`ground_truth == "ti"`) → analyzer must abstain; a confident
  label here is a **hard fail** (SCENARIOS.md §6 scoring rules).

Scoring hard rules (inherited): abstaining on a decidable item = soft miss;
confident wrong label = hard fail; any cross-project leak (S45) = release blocker.

The scoring harness itself is out of scope for the generator; `expected.json`
plus the stamped attributes are everything it needs to join.

---

## 5. Layout

```
demo-data/
  generate.py          CLI + phase orchestration
  gen/
    config.py          endpoints, projects, issue-locator map, timeline dates
    corpus.py          load corpus/*.json, expand variants, substitute {placeholders}
    schedule.py        POLICY table + Scheduler -> the concrete upload plan (+ filler)
    rp.py              RP client (auth, project create, analyzer config, v2 reporting,
                       defect_update replay, analyze, route calls, verification)
    expected.py        build expected.json
  expected.json        generated manifest (checked in; regen with --emit-expected)
  corpus/*.json        input archetypes (not modified)
  SCENARIOS.md         the spec (not modified)
```

---

## 6. Deviations (SCENARIOS.md said "note the reading you chose")

The spec warns it may be internally ambiguous and instructs choosing the reading
that maximises analyzer-mechanism coverage. Choices made:

1. **Volume is corpus + filler driven, not §3.1's per-day counts.** SCENARIOS.md
   §3.1's small parenthetical failed-counts (e.g. "12 f") sum to far less than
   §3.2's roll-up (~1450 failed, ~418 label_events) once the large scenario
   populations (S16/S18/S19/S20 …) are included. The dry-run therefore prioritises
   uploading the full corpus expansion (what actually proves the mechanisms) over
   matching the daily hints. Current plan: **~144 launches, ~880 failed + ~850
   passing items, ~465 label_events** (WSU 349 → clears 300/isotonic, PSV 101,
   FEA 15 → stays < 20/cold, crossing 50→100→300 in order). Launch count and
   label thresholds match the spec; raw failed-item count is lower than 1450 but
   the same order of magnitude and every archetype/scenario is represented. Bump
   `POLICY[...]['target']` or `_filler_targets` to push volume higher.

2. **Generic "filler" failures/passes** (`scenario:filler`) pad nightly launches
   so they look real and the launch skeleton isn't half-empty. They are tagged so
   scoring ignores them, and ~35% carry a real label to feed the threshold walk.

3. **No custom defect sub-types.** All five labels map onto RP's built-in default
   groups (`pb001/ab001/si001/nd001/ti001`); no scenario needs a bespoke subtype,
   so none are created. `ti` (`ti001`, To-Investigate) doubles as "abstain".

4. **`frontend-apps` label replay is capped at 15** (`FEA_LABEL_BUDGET`) — per
   SCENARIOS §1 the FEA team quarantines via a `flaky-quarantine` attribute
   instead of setting defect types, so it must stay < 20 `label_events`. FEA
   history items above the cap stay To-Investigate (unlabeled), which is realistic
   and preserves the cold-model proof (S42).

5. **Probe items land on the Jul 17 demo-day probe launches** rather than being
   sprinkled across Jul 15–17. This keeps the "arrive unlabeled, analyzer decides"
   population in one clearly-analyzable phase-3 batch.

6. **Spam blocks are posted as one large multi-line log row** (not N rows), and
   are **capped to 40 lines under `--smoke`** so the smoke stays fast. The full
   run uses the corpus counts (200×, 5000×).

7. **Phases 4–5 are functional-but-light.** Phase 3 must land first (probe launches
   uploaded + analyzed) before the S43 sweep and S44/S45 route calls have real
   `testItemId`s / launch ids to act on. The scaffolding, endpoints, and the exact
   `defect_update` / `cluster` / `suggest_patterns` call shapes are in `gen/rp.py`;
   wire the concrete item selections once a phase-3 run exists.

8. **Log batching** uses a small thread pool of per-log POSTs (`--log-workers`,
   default 6) rather than the array endpoint, matching the proven single-object
   shape in `_lib.sh` while staying polite to the laptop VM.
