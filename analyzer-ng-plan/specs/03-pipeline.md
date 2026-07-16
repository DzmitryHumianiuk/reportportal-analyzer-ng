# 03 — Analysis Pipeline & Seed Failure-Mode Catalog

Status: implementation-grade specification. Depends on `CONTEXT.md` (authoritative),
`specs/01-architecture.md` (AMQP routes, worker layout), `specs/02-database.md`
(schema, RetrievalStore contract). Table/column names used here (`test_item`,
`log_entry`, `template`, `kb_mode`, `label_event`, `suggestion`, `drain_state`,
`model_artifact`, `metrics_daily`, `test_stats`) are those defined in 02-database.md.

Pipeline: **ingest → preprocess → Drain3 templates → signature → embedding →
launch grouping → matching (A/B/C) → decision (LightGBM + policy) → feedback**.

---

## 1. Ingest & preprocessing

### 1.1 Log filtering semantics (replicate legacy)

Applied per test item, in this order, before any text processing:

1. **Level filter**: keep only logs with `logLevel >= 40000` (ERROR). Constant name:
   `ERROR_LOGGING_LEVEL = 40000` (same as legacy `app/commons/model/launch_objects.py`).
   Also drop logs whose `message.strip()` is empty.
2. **Near-duplicate drop**: run the ported `find_last_unique_texts(threshold, texts)`
   (legacy `text_processing.py`) with `similarity_threshold_to_drop = 0.95` over the
   remaining messages (TF cosine similarity, keeps the *last* occurrence of each
   near-duplicate cluster, preserves order). Returns indices of logs to keep.
3. **Cap**: keep the **last 20** surviving logs (`number_of_logs_to_index = 20`,
   config `ANALYZER_MAX_LOGS_PER_ITEM`). "Last" = tail of the kept-index list,
   matching legacy `logs_to_take[-number_of_logs_to_index:]`.

Items with zero surviving logs are indexed with an empty signature and are never
auto-analyzed (decision = abstain, reason `no_error_logs`).

### 1.2 Legacy functions to port verbatim

Copy these functions (and their module-level compiled regex constants) from the legacy
repo into `analyzer_ng/preprocessing/text_processing.py`. Do **not** import the legacy
package. All names verified against
`/private/tmp/.../service-auto-analyzer/app/utils/text_processing.py`:

| Function (exact name) | Purpose |
|---|---|
| `remove_starting_datetime` | strip leading timestamps |
| `remove_starting_log_level` | strip leading `ERROR`/`WARN`… tokens |
| `remove_starting_thread_id` | strip leading thread ids |
| `remove_starting_thread_name` | strip leading `[thread-name]` |
| `unify_line_endings` | `\r\n`/`\r` → `\n` |
| `delete_empty_lines` / `filter_empty_lines` | drop blank lines |
| `remove_markdown_mode` | strip ``` fences |
| `replace_code_separators` | normalize code separators |
| `remove_webdriver_auxiliary_info` | strip selenium session noise |
| `replace_tabs_for_newlines` | tabs → newlines |
| `fix_big_encoded_urls` | decode/trim giant URL-encoded blobs |
| `remove_generated_parts` | strip `$Proxy`, `$$EnhancerBy…`, lambda ids |
| `remove_guid_uuids_from_text` | UUID masking |
| `remove_access_tokens` | token/credential masking |
| `remove_hex_from_text` | hex-literal masking |
| `clean_html` / `clean_text_from_html_tags` | HTML stripping |
| `leave_only_unique_lines` | de-dup identical lines |
| `detect_log_description_and_stacktrace` | split message vs stacktrace |
| `is_line_from_stacktrace` | per-line stacktrace detector |
| `detect_log_parts_python` / `is_python_log` | python traceback split |
| `get_found_exceptions` | extract exception class names |
| `get_potential_status_codes` / `get_unique_potential_status_codes` | HTTP-ish status codes |
| `extract_urls`, `extract_paths`, `remove_urls`, `remove_credentials_from_url` | URL/path extraction & masking |
| `extract_message_params` | quoted-param extraction |
| `clean_from_brackets`, `clean_from_params`, `clean_colon_stacking` | bracket/param normalization |
| `remove_numbers`, `find_only_numbers`, `unify_spaces`, `first_lines`, `calculate_line_number` | numeric/space/line utilities |
| `find_last_unique_texts`, `calculate_text_similarity` (+ `SimilarityResult`) | near-dup drop (§1.1) |
| `preprocess_test_item_name`, `find_test_methods_in_text` | test-name normalization |
| `replace_patterns`, `remove_patterns`, `replace_text_pieces` | shared helpers used by the above |

From `app/utils/log_preparation.py` port verbatim: `basic_prepare`, `clean_message`,
`unify_message`, `prepare_exception_message_and_stacktrace`. The other `prepare_*`
functions (`prepare_message*`, `prepare_exception_message_no_params*`) are **not**
ported — Drain3 masking replaces their role.

From `app/commons/prepared_log.py` port the `PreparedLogMessage` class (lazy cached
properties: `basic_message`, `clean_message`, `exception_message`, `stacktrace`,
`exception_found`, `exception_message_potential_status_codes`,
`exception_message_urls_list`, `exception_message_paths`). Drop
`PreparedLogMessageClustering` and the `*_no_params/no_numbers/params/numbers`
properties (unused by analyzer-ng).

Port the legacy unit tests for these functions where they exist; add golden-file
tests (§11).

### 1.3 Ordered preprocessing steps (raw message → cleaned text)

For each surviving log message, exactly:

```
raw = log.message
b   = basic_prepare(raw)          # strip(), remove_starting_log_level, remove_starting_datetime,
                                  # remove_starting_log_level (again), remove_starting_thread_id,
                                  # remove_starting_thread_name, remove_starting_log_level (again),
                                  # unify_line_endings, remove_markdown_mode, delete_empty_lines
c   = clean_message(b)            # replace_code_separators, remove_webdriver_auxiliary_info,
                                  # replace_tabs_for_newlines, fix_big_encoded_urls,
                                  # remove_generated_parts, remove_guid_uuids_from_text,
                                  # remove_access_tokens, remove_hex_from_text, clean_html,
                                  # delete_empty_lines
u   = leave_only_unique_lines(c)  # == unify_message(b)
msg, stack = detect_log_description_and_stacktrace(u)   # via prepare_exception_message_and_stacktrace:
                                  # stacktrace additionally clean_from_brackets + remove_numbers
```

Extracted per-log features (stored on `log_entry`, see 02-database.md):
`exceptions = get_found_exceptions(u)`, `status_codes =
get_unique_potential_status_codes(msg)`, `urls = extract_urls(msg)`,
`paths = extract_paths(remove_urls(msg, urls))`, `has_stacktrace = bool(stack)`.

`u` (the unified cleaned text) is the input to Drain3 masking (§2). `msg`/`stack`
feed the signature builder (§3). All steps are pure functions; determinism is a hard
requirement (golden tests, §11).

---

## 2. Drain3 configuration

Drain3 mines log templates from cleaned messages. One Drain instance **per project**.

### 2.1 Masking (applied BEFORE drain, in this order)

Configured via `drain3.TemplateMinerConfig.masking_instructions`. Our preprocessing
already removed UUIDs/hex/tokens; masking is a second belt for anything left and for
values preprocessing deliberately keeps:

| order | mask_with | regex (Python, applied per line) |
|---|---|---|
| 1 | `<URL>`  | `https?://[^\s"'<>]+` |
| 2 | `<IP>`   | `\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b` |
| 3 | `<UUID>` | `\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b` |
| 4 | `<HEX>`  | `\b0[xX][0-9a-fA-F]+\b|\b[0-9a-fA-F]{16,}\b` |
| 5 | `<PATH>` | `(?:[A-Za-z]:)?(?:[\\/][\w.\-]+){2,}` |
| 6 | `<NUM>`  | `(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])` |

Drain sees only masked lines; template parameters therefore never contain volatile
values, which is what makes `template_id` stable.

### 2.2 Drain parameters (per project instance)

```ini
[DRAIN]
sim_th = 0.4          # default; config ANALYZER_DRAIN_SIM_TH
depth = 4
max_children = 100
max_clusters = 4096   # per project; LRU eviction beyond this
extra_delimiters = ["_", "|"]
[MASKING]             # §2.1
[SNAPSHOT]
snapshot_interval_minutes = 1
compress_state = false
```

Each log is fed to Drain **line-by-line** (templates are per-line); a message maps to
an ordered list of template ids. Only the first 40 lines of a message are template-mined
(config `ANALYZER_DRAIN_MAX_LINES=40`); stacktrace frame lines
(`is_line_from_stacktrace(line) == True`) are **skipped** — frames are handled by the
exception fingerprint (§3.2), not by Drain, to avoid cluster explosion.

### 2.3 Persistence & template_id stability

- Persistence uses the **Postgres adapter** from 02-database.md: a custom
  `PersistenceHandler` writing the serialized Drain snapshot to
  `drain_state(project_id, state bytea, updated_at)` (one row per project,
  `save_state` upsert, `load_state` select). Snapshot every 1 min and on worker
  graceful shutdown.
- **`template_id` is NOT Drain's internal cluster_id.** On first sight of a cluster,
  the worker upserts into `template(project_id, template_hash, template_text, drain_cluster_id, …)`
  where `template_hash = xxh3_64(masked_template_text)` and the surrogate PK
  `template.id` is our stable `template_id`. Drain cluster ids are volatile
  (LRU eviction, retraining); `template_hash` is the stable identity. When Drain
  updates a cluster's template text (wildcard widening), insert the new hash as a new
  template row and record `template.superseded_by` on the old row; signatures keep
  referencing the template rows they were built with.
- **State loss / rebuild**: if `drain_state` is missing or fails to deserialize
  (version bump), start an empty Drain and warm it by replaying
  `template.template_text` for the project ordered by `match_count DESC` (add each
  text once via `add_log_message`). This restores the tree approximately; identity is
  unaffected because identity is `template_hash`, not the tree. Log a WARN with counts.
- Drain3 dependency pinned exactly (`drain3==0.9.*`, MIT license) — snapshot format
  is version-coupled; a version bump requires the rebuild path above.

---

## 3. Failure signature construction

One **signature document** per test item — the single embeddable/searchable text.

### 3.1 Signature format (field markers, in this exact order)

```
TEST: <preprocess_test_item_name(testItemName)>
EXC: <exception fingerprint chain, root-cause first, space-joined>
MSG: <cleaned description of the primary log (first 8 lines, masked as §2.1)>
FRAMES: <top-N in-app frames "pkg.Class.method" space-joined>
TEMPLATES: <ordered unique template_hash hex ids of all logs, space-joined, max 30>
CODES: <status codes if any, e.g. "HTTP_503 HTTP_503">   # omit line if empty
```

Rules:
- Lines with empty payload are omitted entirely (except `MSG:` which is always present;
  fallback `MSG: <no message>`).
- **Primary log** = the last log (by time) that has a stacktrace; if none has one,
  the last log.
- **Token budget ≈ 512 tokens** (e5-small limit). Enforce by character proxy: cap the
  document at 2000 chars, truncating in priority order MSG (tail) → TEMPLATES (tail)
  → FRAMES (tail); never truncate TEST/EXC/CODES.
- The signature text is stored in `test_item.signature_text` and is the source for
  both the FTS `tsvector` and the embedding (02-database.md).

### 3.2 Exception fingerprint algorithm

Input: `exceptions` list + `stack` text of the primary log (plus other logs' exceptions
appended for the chain).

1. **Chain extraction**: collect exception class names in occurrence order from
   `get_found_exceptions` over the whole item; the *last* `Caused by:` (Java) or the
   *last* exception in a Python traceback chain is the **root cause** — reorder to
   root-cause-first. If no `Caused by:`/chain markers, keep occurrence order.
2. **Normalization** of each class name: strip package-private suffixes
   `$\d+`, `$$Lambda.*`, `$Proxy\d+`, `$$EnhancerBy\w+`, `..EnhancerBySpring.*`;
   lowercase is NOT applied (class names are case-meaningful); collapse duplicates
   while preserving order; keep at most 4 classes.
3. **Frame selection**: from `stack`, take lines where `is_line_from_stacktrace` is
   true, parse `package.Class.method` (Java `at x.y.Z.m(`; Python `File "...", line N, in m`
   → use `module.m` from the preceding path); drop generated/proxy frames (same regexes
   as step 2 plus `jdk.internal.`, `sun.reflect.`, `java.lang.reflect.`,
   `org.junit.`, `org.testng.`, `pytest`, `unittest`, `reflect.` prefixes).
4. **In-app heuristic (per project)**: maintain `project_namespace(project_id, prefix, weight)`
   — top package prefixes (first 2–3 dot-segments) by frequency across the project's
   stacktraces, refreshed nightly (this also serves `namespace_finder`, §8.4). A frame
   is *in-app* if its prefix is in the project's top-10 non-framework prefixes
   (framework denylist: `java.`, `javax.`, `jdk.`, `org.springframework.`, `org.apache.`,
   `com.google.`, `io.netty.`, `okhttp3.`, `requests.`, `urllib3.`, `selenium.`,
   `org.openqa.`, `node_modules`). Cold start (no namespace rows yet): all
   non-denylisted frames count as in-app.
5. **Top-N**: take the first `N=3` in-app frames (root-cause section of the trace
   first); if fewer than 3 in-app frames exist, pad with the first non-denylisted
   frames.
6. `exception_fp = xxh3_64( "|".join(normalized_classes) + "#" + "|".join(frames) )`,
   stored as signed bigint in `test_item.exception_fp`.

### 3.3 error_hash

```
error_hash = xxh3_64( str(exception_fp) + "#" + "|".join(ordered_template_hashes) )
```

`xxhash` package (BSD-2). Stable 64-bit; stored as bigint `test_item.error_hash`.
Same failure (same exception chain, frames, template sequence) always yields the same
`error_hash` across runs and processes — golden-tested (§11).

### 3.4 Edge cases

| case | handling |
|---|---|
| No stacktrace | `FRAMES:` omitted; `exception_fp` computed from classes only; if also no exception classes → `exception_fp = 0` and Stage A/exact matching disabled for the item (hash too weak). |
| Non-exception failure: assertion diff | `get_found_exceptions` catches `AssertionError` etc.; additionally if MSG matches `(?i)expected.*(but was|received|actual)` set flag `is_assertion=true` (feature §6.4) and strip the expected/actual literal values from MSG before embedding (mask as `<VAL>`) so diffs of different values still match. |
| Timeout without exception | seed-catalog keyword rules (§9) still match on MSG/TEMPLATES; `exception_fp=0` path. |
| Many tiny logs (all < 100 chars, no stacktrace) | merge: concatenate cleaned messages (order kept) into one pseudo-log before §1.3 splitting; set `is_merged_small_logs=true`. Mirrors legacy merged_small_logs behavior. |
| Empty signature (no logs) | item stored, never analyzed; suggest returns []. |

---

## 4. Embedding

- **Model: `intfloat/multilingual-e5-small`** — 384 dims, 512-token context, MIT
  license, ~118 MB fp32, strong multilingual retrieval at this size. Chosen over
  `nomic-embed-text-v2-moe` (bigger, Apache-2.0 but MoE = slower CPU int8) and over
  monolingual MiniLM (RP logs contain non-English test names/messages). Final.
- **Export & pin**: at image build time run `optimum-cli export onnx
  --model intfloat/multilingual-e5-small --task feature-extraction` then dynamic int8
  quantization (`onnxruntime.quantization.quantize_dynamic`, weights-only). Pin the HF
  revision hash in the Dockerfile (`ANALYZER_EMB_MODEL_REV`), bundle the `.onnx` +
  tokenizer files into the image (no runtime downloads). `emb_model_ver =
  "e5s-int8-r<rev8>"` stamped on every embedding row (02-database.md).
- **Prefix rules (e5 requirement)**: signature-vs-signature matching is *symmetric* →
  use the **`"query: "` prefix for both** indexed signatures and query-time signatures.
  Exception: KB-mode centroids are means of already-prefixed vectors — no extra prefix.
  Never embed unprefixed text. (Asymmetric `"passage: "` is not used anywhere; document
  this in code to prevent drift.)
- **Pooling & normalization**: mean pooling over token embeddings (attention-mask
  weighted), then L2-normalize. Cosine similarity = inner product on normalized
  vectors; pgvector operator `<#>` (negated inner product) per 02-database.md.
- **Batching**: async ingest embeds in batches of 32 (config), intra-op threads = 2,
  ORT session shared per worker process. Suggest path embeds a single query signature
  synchronously.
- **Latency budget**: single 512-token embed ≤ 120 ms p95 on 2 vCPU (int8); batch-32
  throughput ≥ 60 items/s. Verify in the integration perf test; fail CI if single-embed
  p95 > 200 ms on the CI runner class.

---

## 5. Launch grouping

Groups failures *within one launch*; one group → one diagnosis fanned out to members.

Parameters: `θ_group = 0.83` cosine (config `ANALYZER_GROUP_COS`), burst
`X = 0.40`, `N = 5`.

```python
def group_launch(items):  # items: analyzed test items of one launch, with fp + embedding
    groups = []                                # each: {fp_set, members, centroid}
    # pass 1: exact exception_fp buckets (fp != 0)
    by_fp = bucket(items, key=lambda i: i.exception_fp, skip=0)
    for fp, members in sorted(by_fp.items()):  # sort for determinism
        groups.append(new_group(members))
    leftovers = [i for i in items if i.exception_fp == 0]
    # pass 2: cosine attach / greedy agglomeration, deterministic order
    for it in sorted(leftovers, key=lambda i: i.item_id):
        best, best_sim = argmax((g, cos(it.emb, g.centroid)) for g in groups)
        if best_sim >= θ_group: best.add(it)      # centroid = running mean, re-normalized
        else: groups.append(new_group([it]))
    # pass 3: merge groups whose centroids cos >= θ_group AND template Jaccard >= 0.5
    groups = merge_close(groups, θ_group, jaccard_min=0.5)   # single pass, ordered by group id
    for g in groups:
        g.representative = max(g.members, key=lambda i: (i.has_stacktrace, i.log_count, -i.item_id))
        g.si_prior = 0.0
    # burst / system-issue prior
    total = len(items)
    for g in groups:
        is_new = not error_hash_seen_before(g.representative.error_hash)   # per 02 history query
        if is_new and len(g.members) >= N and len(g.members) / total > X:
            g.si_prior = min(0.9, 0.5 + len(g.members) / total)   # feature for §6, not a hard rule
    return groups
```

Determinism: fixed iteration orders (sorted ids), running-mean centroids in float32,
no RNG. Same input launch → identical groups (golden test §11). Groups are persisted
per 02-database.md (`launch_group` rows) and serve both the `analyze` fan-out and the
`cluster` route (§8.1).

---

## 6. Matching & decision

Executed per **group representative**; the resulting decision is fanned out to all
group members (each member still gets its own `suggestion` row, sharing `group_id`).

### 6.0 analyzerMode → retrieval scope (replicates legacy `analyzer_service.py`)

`launch_boost = 1.1` (config, mirrors legacy `BoostLaunch`). Applied as SQL filters
(hard) or post-retrieval score multipliers (soft) in Stages B/C:

| mode | analyze route (hard filters) | suggest route (soft boosts) |
|---|---|---|
| `LAUNCH_NAME` | `launch_name = :ln AND launch_id != :lid` | ×`launch_boost` same name; ×`1/launch_boost` same launch_id |
| `CURRENT_AND_THE_SAME_NAME` | `launch_name = :ln`; ×boost same launch_id | ×boost same name and same launch_id |
| `CURRENT_LAUNCH` | `launch_id = :lid` | ×boost same name and same launch_id |
| `PREVIOUS_LAUNCH` | `launch_id = :previousLaunchId` | ×boost previousLaunchId (if set) |
| `ALL` | `launch_id != :lid` | ×`launch_boost` same name; ×`1/launch_boost` same launch_id |
| default/unset | no filter; ×boost same name and same launch_id | ×boost same name and same launch_id |

All scopes additionally require `project_id = :pid AND issue_type NOT IN ('ti') AND
is_labeled = true` (retrieve only labeled/analyzable history).

### 6.1 Stage A — exact error_hash

Query: labeled items in scope with `error_hash = :h` (skip if `exception_fp = 0`,
§3.4). Inherit the label directly (no ML) iff ALL guards pass:

- ≥ 2 matching items, all sharing the same issue_type locator (unanimous), OR
  1 item whose label came from a human `label_event` (`source in
  ('human_confirm','defect_update')`) with `confidence >= 0.9`;
- newest match age ≤ 180 days;
- matched label is not `ti`/`nd`-from-auto (auto-suggested `nd` never propagates).

Result: `decision = inherited`, `confidence = 0.95`, `method = "hash"`, relevantItem =
newest match. Otherwise fall through to Stage B with `same_error_hash` candidates kept.

### 6.2 Stage B — KB mode match

Candidates: all `kb_mode` rows for the project with `state IN ('candidate','confirmed')`
(hundreds; exact scan). Per mode compute:

```
score_mode = 0.6 * cos(sig_emb, mode.centroid)
           + 0.25 * jaccard(item.template_hashes, mode.template_hashes)
           + 0.15 * fp_overlap          # 1.0 if exception_fp in mode.exception_fps,
                                        # 0.5 if root class matches, else 0
```

Thresholds: `score_mode >= 0.80` and mode `confirmed` → strong KB match;
`score_mode >= 0.70` → candidate KB match (features only). Seed modes (§9) match via
their rule sets (regex/keyword) *in addition to* centroid score once they have one; a
rule hit sets `seed_mode_matched=1` and contributes the mode's prior label/confidence
as features. KB match never decides alone — it feeds §6.4 features, except:
a *confirmed* mode with `purity >= 0.95`, `support >= 10`, `score_mode >= 0.85`
short-circuits like Stage A (`method="kb"`, confidence = min(0.93, purity·score_mode)).

### 6.3 Stage C — hybrid item-history retrieval

Execute the RetrievalStore hybrid query from 02-database.md (FTS field-boosted +
pgvector cosine, RRF k=60) under the §6.0 scope → **top-20 candidates**, each with
`(cosine, fts_rank, rrf, template_jaccard, label, label_meta, launch_meta)`.
Apply soft boosts (§6.0) multiplicatively to `rrf` before ranking.

### 6.4 Feature vector for LightGBM (exact order)

All floats; defined default when data missing (no NaNs reach the model).
`decay(d) = exp(-ln 2 · d / 90)` (half-life 90 days).
`src_w(e)` label-source weight: human confirm/`defect_update` = 1.0, human via UI
accept of suggestion = 0.9, `ai_suggested` accepted = 0.6, `ai_suggested`
unreviewed = 0.3.

| # | name | definition | range | default |
|---|---|---|---|---|
| 0 | top1_cosine | cosine of best Stage-C candidate | [0,1] | 0 |
| 1 | top1_rrf | RRF score of top1 (post-boost) | [0,~0.05] | 0 |
| 2 | top1_jaccard | template-set Jaccard vs top1 | [0,1] | 0 |
| 3 | margin_cos | top1_cosine − top2_cosine | [0,1] | 0 |
| 4 | mean_top5_cosine | mean cosine of top-5 | [0,1] | 0 |
| 5 | n_candidates | len(candidates)/20 | [0,1] | 0 |
| 6 | label_hist_entropy | Shannon entropy of candidate label histogram / ln 4 | [0,1] | 1 |
| 7 | top1_label_frac | fraction of candidates sharing top1's label | [0,1] | 0 |
| 8 | same_test_case_top1 | top1 has same test_case_hash | {0,1} | 0 |
| 9 | same_error_hash_top1 | top1 error_hash equal | {0,1} | 0 |
| 10 | same_exception_fp_top1 | top1 exception_fp equal | {0,1} | 0 |
| 11 | recency_top1 | decay(age_days(top1)) | (0,1] | 0 |
| 12 | src_weight_top1 | src_w(top1 label event) | [0.3,1] | 0 |
| 13 | hist_pb | Σ over candidates with label pb of cos·decay·src_w, ÷ Σ all | [0,1] | 0 |
| 14 | hist_ab | same for ab | [0,1] | 0 |
| 15 | hist_si | same for si | [0,1] | 0 |
| 16 | hist_nd | same for nd | [0,1] | 0 |
| 17 | kb_top1_score | best `score_mode` (§6.2) | [0,1] | 0 |
| 18 | kb_purity | purity of best mode | [0,1] | 0 |
| 19 | kb_support | log1p(support)/log1p(1000), capped 1 | [0,1] | 0 |
| 20 | kb_same_fp | exception_fp ∈ best mode fps | {0,1} | 0 |
| 21 | kb_prior_conf | prior confidence of matched seed mode | [0,1] | 0 |
| 22 | seed_pb / 23 seed_ab / 24 seed_si / 25 seed_nd | one-hot of matched seed mode's prior label (all 0 if none) | {0,1} | 0 |
| 26 | flakiness_score | from `test_stats` (02): 1 − p(same outcome as previous run), 30d window | [0,1] | 0.5 |
| 27 | test_fail_rate_30d | failures/runs, 30d, from test_stats | [0,1] | 0.5 |
| 28 | flips_30d | log1p(pass↔fail flips)/log1p(20), cap 1 | [0,1] | 0 |
| 29 | co_failure_group_size | log1p(group size)/log1p(200) | [0,1] | 0 |
| 30 | group_dominance | group size / launch failures | (0,1] | 0 |
| 31 | launch_fail_fraction | launch failures / launch items (0 if unknown total) | [0,1] | 0 |
| 32 | si_prior | burst prior from §5 | [0,0.9] | 0 |
| 33 | test_age_days | log1p(min(days since first seen, 365))/log1p(365) | [0,1] | 0 |
| 34 | item_log_count | surviving logs / 20 | [0,1] | 0 |
| 35 | has_stacktrace | §1.3 flag | {0,1} | 0 |
| 36 | is_assertion | §3.4 flag | {0,1} | 0 |
| 37 | is_merged_small_logs | §3.4 flag | {0,1} | 0 |
| 38 | exception_count | min(len(exceptions),5)/5 | [0,1] | 0 |

The exact ordered list lives in one module (`features.py`, `FEATURES: list[FeatureDef]`);
`feature_schema_ver` is stamped into `suggestion.features` and `model_artifact`.
**The full vector (as a name→value JSON object) is stored in `suggestion.features`
jsonb at prediction time — training reads these stored vectors, so training data
matches serving exactly by construction.**

### 6.5 LightGBM training

- Objective: `multiclass`, 4 classes `pb/ab/si/nd` (label = base issue type of the
  ground-truth locator from `label_event`; custom subtypes map to their base group).
  `ti` is never a class — it is the abstain outcome.
- Abstain: after calibration, if `max_prob < τ_abstain` → `ti`. Defaults:
  `τ_auto = 0.75` (auto-apply band), `τ_suggest = 0.45` (below → pure abstain).
- Training set: join `label_event` (ground truth) × the `suggestion.features` snapshot
  recorded when the suggestion was made (§6.4). One row per (item, final label);
  later corrections supersede earlier events per item.
- Model scope: **install-wide** (one model across projects; project effects enter via
  features). Per-project **isotonic calibration** of max-prob when the project has
  ≥ 300 label_events; otherwise install-wide isotonic (≥ 300 install-wide), otherwise
  raw softmax.
- Hyperparameters (fixed v1): `num_leaves=31, n_estimators=200, learning_rate=0.05,
  min_data_in_leaf=20, feature_fraction=0.9, class_weight=balanced, seed=42,
  deterministic=true`.
- Retrain trigger: every **N=100 new label_events** per install (counter check on
  `defect_update`/feedback ingestion) AND a nightly job at 02:00; whichever fires,
  debounced to ≥ 1 retrain/hour. Retrained model ships only if it passes the eval gate
  (§10).
- Cold model: with **< 50 label_events install-wide**, skip GBM entirely — rule-based
  fallback: Stage A → KB short-circuit → seed-mode prior label if
  `prior_confidence >= 0.7` and `seed_mode_matched` → else abstain (`ti`).
- Artifact storage: **PG `model_artifact` table, `bytea`** (`model_id, kind='gbm',
  version, feature_schema_ver, trained_at, metrics jsonb, blob bytea, is_active`).
  Chosen over files: containers are stateless, PG is the only required store; models
  are < 5 MB. Isotonic calibrators stored the same way (`kind='calib', project_id`).
  Every `suggestion` row stamps `model_id`.

### 6.6 Decision policy table

`p* = calibrated max-prob`, `label* = argmax`. RP mode: auto-analysis (`analyze`
route) vs suggest (`suggest` route).

| band | analyze route (auto-analysis on) | suggest route |
|---|---|---|
| Stage A/KB short-circuit | auto-label `label*`, relevantItem set | top-1 suggestion, matchScore = confidence·100 |
| `p* ≥ 0.75` (τ_auto) | auto-label `label*` | top-3 suggestions ordered by prob |
| `0.45 ≤ p* < 0.75` | **abstain → item stays `ti`**; store top-3 as precomputed suggestions | top-3 suggestions (matchScore = prob·100) |
| `p* < 0.45` | abstain → `ti`, reason stored | return [] (legacy returns empty when nothing clears min_should_match) |

Every decision writes a `suggestion` row (features, probs, model_id, method ∈
{hash, kb, gbm, rule_cold}, abstain_reason). Auto-labeled items respond on the
`analyze` reply as `{testItem, issueType, relevantItem}`; suggested-only items are
omitted from the analyze reply (legacy semantics: unanalyzed items remain ti).

### 6.7 Feedback

`defect_update` AMQP messages (user changed issue type in RP UI) → append
`label_event(item_id, old_label, new_label, source='defect_update', ts)`; update
`kb_mode` purity/support for the item's matched mode; bump retrain counter. Accepting
a suggestion in UI arrives as the same `defect_update`; correctness is judged by
comparing against the stored suggestion (populates `metrics_daily`, §10).

---

## 7. (reserved)

Section intentionally merged into §6.7/§10 — feedback and metrics.

---

## 8. cluster / search / suggest_patterns / namespace_finder routes

### 8.1 `cluster`

Input `LaunchInfoForClustering` (launch + `for_update` + `numberOfLogLines`). Serve
from §5 launch grouping: run grouping over the launch's items (reusing stored
signatures/embeddings when indexed, computing on the fly otherwise). Reply
`ClusterResult{project, launchId, clusters: [ClusterInfo{clusterId, clusterMessage,
logIds, itemIds}]}` — `clusterId = xxh3_64(project_id, launch_id?, representative
error_hash)` masked to positive int53 (Java long-safe); when
`analyzerConfig.uniqueErrorsMinShouldMatch`-style cross-launch clustering is on
(`for_update=false` semantics in legacy), reuse an existing cluster id if the same
representative `error_hash` already has one in the project (lookup table
`cluster_id_map` per 02). `clusterMessage` = first 5 lines of the representative's
cleaned primary message (unmasked text, matching legacy UX).

### 8.2 `search`

Input `SearchLogInfo` (RP "similar TI" search). = **hybrid retrieval without the
decision layer**: build a signature from the provided message/logs, run the Stage-C
query scoped to `project` and the request's filters (`filteredLaunchIds`,
`itemsToSearch...`), threshold `cosine >= 0.75` OR FTS rank hit, return matching
log ids with scores in legacy shape (`[{logId, testItemId, matchScore}]`).

### 8.3 `suggest_patterns`

Legacy mines frequent exception/keyword patterns per issue type. Minimal viable
implementation: SQL aggregation over labeled items — for each `issue_type`, top
exception classes and template texts by `count`, where P(label|pattern) ≥ 0.8 and
count ≥ 5 → reply in legacy `SuggestPatternsResult` shape
(`suggestionsWithLabels` / `suggestionsWithoutLabels`). No ML. If the project has
< 100 labeled items, return empty lists (logged INFO).

### 8.4 `namespace_finder`

Legacy persists per-project package-namespace frequencies used for boosting. We
already maintain `project_namespace` (§3.2 step 4) from ingested stacktraces, updated
incrementally at index time. The `namespace_finder` route therefore **acknowledges and
triggers a refresh of that table** for the given launches; no separate reply payload
is expected (fire-and-forget in legacy). Documented as implemented-internally.

`train_models` route: no-op with logged warning (training is trigger-based, §6.5).
`stats_info`/`index_suggest_info`: reply with counts from our tables per legacy shape.
`delete/clean/item_remove/launch_remove/remove_by_*`: delete per 02-database.md
retention API, reply legacy-shaped counts.

---

## 9. SEED FAILURE-MODE CATALOG

Shipped as `analyzer_ng/resources/seed_modes.yaml`, loaded by migration into
`kb_mode` template rows (`seed=true`, `project_id=NULL`). **Per-project copies are
created lazily on first match** (copy row with `project_id`, `state='candidate'`,
inherit priors; centroid starts NULL and is set from first matched items). Matching
rules are evaluated against: `EXC` classes (regex, case-sensitive), `MSG`+template
texts (`msg_re`, case-insensitive), keyword sets (`kw`, all lowercase substring,
ANY-of). A mode matches if any listed rule group matches. First matching mode by
`priority` (list order below) wins for `seed_mode_matched`.

```yaml
version: 1
modes:
  # ---- memory / resources -------------------------------------------------
  - mode_key: oom_java            # 1
    title: "Out of memory (JVM/Python)"
    rules: {exc_re: ['java\.lang\.OutOfMemoryError', '^MemoryError$'], kw: ["outofmemoryerror", "gc overhead limit"]}
    prior: {label: si, confidence: 0.8}
    rationale: "Heap exhaustion is an environment/resource issue, rarely the test's logic."
  - mode_key: oom_killed_container # 2
    title: "Container OOM-killed (exit 137)"
    rules: {msg_re: ['exit code 137', 'OOMKilled', 'Killed\s+process'], kw: ["oom-killed", "exit status 137"]}
    prior: {label: si, confidence: 0.85}
    rationale: "cgroup kill = infrastructure sizing, not test or product logic."
  - mode_key: stack_overflow      # 3
    title: "Stack overflow"
    rules: {exc_re: ['java\.lang\.StackOverflowError', '^RecursionError$']}
    prior: {label: pb, confidence: 0.5}
    rationale: "Usually runaway recursion in product code; sometimes test fixtures."
  - mode_key: disk_full           # 4
    title: "Disk full / no space left"
    rules: {msg_re: ['[Nn]o space left on device', 'DiskFull'], kw: ["disk quota exceeded"]}
    prior: {label: si, confidence: 0.9}
    rationale: "Storage exhaustion on the runner is purely environmental."
  - mode_key: too_many_open_files # 5
    title: "Too many open files"
    rules: {msg_re: ['[Tt]oo many open files'], kw: ["emfile"]}
    prior: {label: si, confidence: 0.75}
    rationale: "ulimit/leak on the agent; environment-dominated."

  # ---- network / connectivity ---------------------------------------------
  - mode_key: conn_refused        # 6
    title: "Connection refused"
    rules: {exc_re: ['ConnectException', 'ConnectionRefusedError'], msg_re: ['[Cc]onnection refused']}
    prior: {label: si, confidence: 0.75}
    rationale: "Target service down/unreachable — infra or deployment issue."
  - mode_key: conn_reset          # 7
    title: "Connection reset / broken pipe"
    rules: {msg_re: ['[Cc]onnection reset by peer', '[Bb]roken pipe', 'ECONNRESET']}
    prior: {label: si, confidence: 0.7}
    rationale: "Transient network/service instability."
  - mode_key: conn_timeout        # 8
    title: "Connection/read timeout"
    rules: {exc_re: ['SocketTimeoutException', 'ReadTimeout(Error)?$', 'ConnectTimeout'], msg_re: ['[Tt]imed? ?out (waiting for )?connection', 'read timed out']}
    prior: {label: si, confidence: 0.65}
    rationale: "Network/service latency; occasionally an undersized test timeout (ab)."
  - mode_key: dns_resolution      # 9
    title: "DNS resolution failure"
    rules: {exc_re: ['UnknownHostException', 'gaierror'], msg_re: ['[Nn]ame or service not known', 'getaddrinfo failed', 'NXDOMAIN']}
    prior: {label: si, confidence: 0.85}
    rationale: "Name resolution is environment config, never test logic."
  - mode_key: tls_cert            # 10
    title: "TLS/certificate error"
    rules: {exc_re: ['SSLHandshakeException', 'SSLError', 'CertificateException'], msg_re: ['certificate verify failed', 'PKIX path building failed', 'self[- ]signed certificate']}
    prior: {label: si, confidence: 0.8}
    rationale: "Cert/truststore problems are environment configuration."
  - mode_key: port_in_use         # 11
    title: "Port already in use"
    rules: {exc_re: ['BindException'], msg_re: ['[Aa]ddress already in use', 'EADDRINUSE']}
    prior: {label: si, confidence: 0.8}
    rationale: "Runner port collision — parallelism/environment issue."
  - mode_key: proxy_gateway       # 12
    title: "Proxy / gateway error"
    rules: {msg_re: ['502 Bad Gateway', '504 Gateway Time-?out', 'ProxyError']}
    prior: {label: si, confidence: 0.75}
    rationale: "Intermediary failure between test and system under test."

  # ---- HTTP-status families -------------------------------------------------
  - mode_key: http_5xx            # 13
    title: "HTTP 5xx server error"
    rules: {msg_re: ['\b(HTTP|status( code)?)[ :=]*5\d\d\b', 'Internal Server Error', 'Service Unavailable']}
    prior: {label: si, confidence: 0.6}
    rationale: "Server-side failure; could be product bug, but bursts are usually systemic."
  - mode_key: http_401_403        # 14
    title: "HTTP 401/403 auth failure"
    rules: {msg_re: ['\b(HTTP|status( code)?)[ :=]*40[13]\b', 'Unauthorized', 'Forbidden']}
    prior: {label: ab, confidence: 0.55}
    rationale: "Expired test credentials/tokens are the common cause."
  - mode_key: http_404            # 15
    title: "HTTP 404 not found"
    rules: {msg_re: ['\b(HTTP|status( code)?)[ :=]*404\b', 'Not Found']}
    prior: {label: ab, confidence: 0.45}
    rationale: "Often stale endpoint/fixture in the test; sometimes a real routing bug."
  - mode_key: http_429            # 16
    title: "HTTP 429 rate limit / quota"
    rules: {msg_re: ['\b429\b', '[Rr]ate limit', 'quota exceeded', 'Too Many Requests']}
    prior: {label: si, confidence: 0.75}
    rationale: "Throttling by shared services — environmental capacity."

  # ---- database / messaging -------------------------------------------------
  - mode_key: db_pool_exhausted   # 17
    title: "DB connection pool exhausted"
    rules: {msg_re: ['[Cc]onnection pool.*(exhaust|timeout)', 'HikariPool.*timed? ?out', 'too many clients already', 'QueuePool limit']}
    prior: {label: si, confidence: 0.75}
    rationale: "Pool sizing/leaks under load — systemic."
  - mode_key: db_deadlock         # 18
    title: "Deadlock / lock timeout"
    rules: {msg_re: ['[Dd]eadlock (found|detected)', 'LockAcquisitionException', 'lock wait timeout exceeded', 'could not obtain lock']}
    prior: {label: pb, confidence: 0.5}
    rationale: "Concurrency bugs in product code, sometimes test data collisions."
  - mode_key: db_conn_failed      # 19
    title: "Database connection failure"
    rules: {exc_re: ['SQLTransientConnectionException', 'OperationalError'], msg_re: ['(database|db).*(connection (refused|failed))', 'FATAL:\s+(the )?database', 'password authentication failed']}
    prior: {label: si, confidence: 0.75}
    rationale: "DB unreachable/misconfigured — environment."
  - mode_key: db_migration_failed # 20
    title: "Schema migration failed"
    rules: {msg_re: ['[Ff]lyway.*(failed|error)', 'liquibase.*(failed|error)', 'alembic.*(error|failed)', 'migration.*failed']}
    prior: {label: si, confidence: 0.7}
    rationale: "Migration/environment drift blocks the whole run."
  - mode_key: kafka_rabbit        # 21
    title: "Kafka/RabbitMQ connectivity"
    rules: {msg_re: ['kafka.*(timeout|not available|disconnect)', 'TimeoutException.*kafka', '(rabbitmq|amqp).*(refused|unreachable|timeout)'], kw: ["broker not available", "leader not available"]}
    prior: {label: si, confidence: 0.75}
    rationale: "Message broker availability is infrastructural."
  - mode_key: redis_conn          # 22
    title: "Redis connectivity"
    rules: {exc_re: ['JedisConnectionException', 'RedisConnectionError'], msg_re: ['redis.*(refused|timeout|unreachable)', 'MISCONF Redis']}
    prior: {label: si, confidence: 0.75}
    rationale: "Cache layer availability — infra."

  # ---- selenium / webdriver -------------------------------------------------
  - mode_key: wd_stale_element    # 23
    title: "StaleElementReferenceException"
    rules: {exc_re: ['StaleElementReferenceException']}
    prior: {label: ab, confidence: 0.8}
    rationale: "Classic race between DOM refresh and test — test synchronization bug."
  - mode_key: wd_no_such_element  # 24
    title: "NoSuchElementException (locator)"
    rules: {exc_re: ['NoSuchElementException'], msg_re: ['[Uu]nable to (locate|find) element']}
    prior: {label: ab, confidence: 0.65}
    rationale: "Broken/changed locator most often; occasionally a real missing UI element."
  - mode_key: wd_not_interactable # 25
    title: "ElementNotInteractable / ClickIntercepted"
    rules: {exc_re: ['ElementNotInteractableException', 'ElementClickInterceptedException'], msg_re: ['element not interactable', 'Other element would receive the click']}
    prior: {label: ab, confidence: 0.7}
    rationale: "Timing/overlay handling in the test."
  - mode_key: wd_wait_timeout     # 26
    title: "Explicit wait timeout"
    rules: {exc_re: ['org\.openqa\.selenium\.TimeoutException', 'selenium\.common\.exceptions\.TimeoutException'], msg_re: ['[Ee]xpected condition failed.*wait', 'Waited \d+ seconds for']}
    prior: {label: ab, confidence: 0.6}
    rationale: "Waits/synchronization tuned wrong; sometimes genuine slowness (si)."
  - mode_key: wd_session          # 27
    title: "WebDriver session error"
    rules: {exc_re: ['WebDriverException', 'SessionNotCreatedException', 'NoSuchSessionException'], msg_re: ['invalid session id', 'session deleted because of page crash', 'chrome not reachable']}
    prior: {label: si, confidence: 0.7}
    rationale: "Browser/driver infrastructure crashed, not the test's assertions."
  - mode_key: wd_driver_mismatch  # 28
    title: "ChromeDriver/browser version mismatch"
    rules: {msg_re: ['[Tt]his version of ChromeDriver only supports', 'session not created.*version', 'unsupported browser version']}
    prior: {label: si, confidence: 0.85}
    rationale: "Grid/agent image out of date — environment."
  - mode_key: browser_console     # 29
    title: "Browser console errors assertion"
    rules: {msg_re: ['console error(s)? (found|detected)', 'severe.*console', 'js console.*error']}
    prior: {label: pb, confidence: 0.5}
    rationale: "Tests asserting a clean console usually surface real front-end errors."

  # ---- assertions -----------------------------------------------------------
  - mode_key: assertion_java      # 30
    title: "JUnit/TestNG assertion failure"
    rules: {exc_re: ['java\.lang\.AssertionError', 'org\.junit\.ComparisonFailure', 'AssertionFailedError'], msg_re: ['expected:.*but was']}
    prior: {label: pb, confidence: 0.4}
    rationale: "Functional mismatch — pb-leaning, but stale expectations are common; low confidence."
  - mode_key: assertion_pytest    # 31
    title: "pytest assertion failure"
    rules: {exc_re: ['^AssertionError$'], msg_re: ['^assert\b', 'AssertionError:']}
    prior: {label: pb, confidence: 0.4}
    rationale: "As above, for Python."
  - mode_key: assertion_js        # 32
    title: "chai/jest expect failure"
    rules: {msg_re: ['expect\(.*\)\.to', 'expect\(received\)', 'AssertionError \[ERR_ASSERTION\]', 'jest.*expect'], kw: ["expected value to be"]}
    prior: {label: pb, confidence: 0.4}
    rationale: "As above, for JS."
  - mode_key: snapshot_mismatch   # 33
    title: "Snapshot/visual mismatch"
    rules: {msg_re: ['[Ss]napshot.*(mismatch|does not match|obsolete)', 'toMatchSnapshot', 'screenshot comparison failed', 'pixel difference']}
    prior: {label: ab, confidence: 0.5}
    rationale: "Snapshots drift with intended UI changes as often as with real bugs."
  - mode_key: soft_assert_bundle  # 34
    title: "Multiple soft-assert failures"
    rules: {msg_re: ['[Ss]oft ?assert', 'The following asserts failed']}
    prior: {label: pb, confidence: 0.4}
    rationale: "Aggregated functional mismatches."

  # ---- code-level exceptions ------------------------------------------------
  - mode_key: npe_undefined       # 35
    title: "NullPointerException / undefined access"
    rules: {exc_re: ['java\.lang\.NullPointerException'], msg_re: ["TypeError: Cannot read propert(y|ies) of (undefined|null)", "'NoneType' object (has no attribute|is not)"]}
    prior: {label: pb, confidence: 0.55}
    rationale: "Null-safety bugs are usually product-side, sometimes fixture gaps."
  - mode_key: illegal_state       # 36
    title: "IllegalState/IllegalArgument"
    rules: {exc_re: ['java\.lang\.IllegalStateException', 'java\.lang\.IllegalArgumentException', '^ValueError$']}
    prior: {label: pb, confidence: 0.45}
    rationale: "Contract violations lean product, but tests pass bad args too."
  - mode_key: index_key_error     # 37
    title: "IndexOutOfBounds / KeyError"
    rules: {exc_re: ['IndexOutOfBoundsException', 'ArrayIndexOutOfBoundsException', '^KeyError$', '^IndexError$']}
    prior: {label: pb, confidence: 0.5}
    rationale: "Boundary bugs; test-data shape mismatches also occur."
  - mode_key: class_not_found     # 38
    title: "ClassNotFound / NoClassDefFound / ImportError"
    rules: {exc_re: ['ClassNotFoundException', 'NoClassDefFoundError', 'ModuleNotFoundError', '^ImportError$']}
    prior: {label: ab, confidence: 0.6}
    rationale: "Classpath/packaging of the test run — automation/config, not product logic."
  - mode_key: file_not_found      # 39
    title: "File not found"
    rules: {exc_re: ['FileNotFoundException', 'FileNotFoundError'], msg_re: ['No such file or directory']}
    prior: {label: ab, confidence: 0.55}
    rationale: "Missing fixtures/paths in the test environment."
  - mode_key: permission_denied   # 40
    title: "Permission denied (fs/os)"
    rules: {exc_re: ['AccessDeniedException', 'PermissionError'], msg_re: ['[Pp]ermission denied', 'EACCES']}
    prior: {label: si, confidence: 0.7}
    rationale: "Filesystem/user permissions on the agent."
  - mode_key: encoding_error      # 41
    title: "Encoding/serialization error"
    rules: {exc_re: ['UnicodeDecodeError', 'JsonParseException', 'JsonMappingException', 'JSONDecodeError', 'SerializationException']}
    prior: {label: pb, confidence: 0.45}
    rationale: "Contract/format drift between components; sometimes bad test data."

  # ---- build / dependencies / CI -------------------------------------------
  - mode_key: compile_error       # 42
    title: "Compilation/build error"
    rules: {msg_re: ['COMPILATION ERROR', 'cannot find symbol', 'SyntaxError:', 'error TS\d+', 'BUILD FAILED']}
    prior: {label: ab, confidence: 0.75}
    rationale: "The test code/build is broken before any product behavior is exercised."
  - mode_key: dependency_resolution # 43
    title: "Dependency resolution failure (maven/npm/pip)"
    rules: {msg_re: ['Could not resolve dependencies', 'Could not transfer artifact', 'npm ERR!.*(404|ETIMEDOUT|ERESOLVE)', 'No matching distribution found', 'ResolutionImpossible']}
    prior: {label: si, confidence: 0.7}
    rationale: "Registry/proxy availability or lockfile drift — environmental."
  - mode_key: docker_testcontainers # 44
    title: "Docker/testcontainers failure"
    rules: {exc_re: ['ContainerLaunchException'], msg_re: ['Could not find a valid Docker environment', '(pull access denied|manifest unknown|toomanyrequests)', 'error during connect: .*docker']}
    prior: {label: si, confidence: 0.8}
    rationale: "Container runtime/registry issues on the agent."
  - mode_key: ci_agent_lost       # 45
    title: "CI agent lost / job cancelled"
    rules: {msg_re: ['(agent|node|runner).*(disconnect|went offline|lost)', 'job was cancell?ed', 'received SIGTERM', 'The operation was canceled']}
    prior: {label: si, confidence: 0.85}
    rationale: "Executor infrastructure died mid-run."
  - mode_key: env_config_missing  # 46
    title: "Missing env var / configuration"
    rules: {msg_re: ['environment variable.*(not set|missing|is required)', 'Missing required (property|configuration)', 'Could not resolve placeholder'], kw: ["config not found"]}
    prior: {label: ab, confidence: 0.6}
    rationale: "Run configuration gap in the automation setup."
  - mode_key: generic_timeout     # 47
    title: "Generic test timeout"
    rules: {msg_re: ['[Tt]est timed out after', 'TimeoutError(?!.*selenium)', 'exceeded timeout of \d+', 'DeadlineExceeded']}
    prior: {label: ab, confidence: 0.5}
    rationale: "Timeout budget vs genuine slowness — ambiguous, mild ab lean."
  - mode_key: flaky_retry_passed  # 48
    title: "Flaky: failed then passed on retry"
    rules: {msg_re: ['(passed|succeeded) (on|after) retry', 'Flaky ?[Tt]est', 'retr(y|ies).*succe']}
    prior: {label: nd, confidence: 0.7}
    rationale: "Retry-passed is the canonical no-defect signal."
  - mode_key: concurrent_mod      # 49
    title: "ConcurrentModification / race"
    rules: {exc_re: ['ConcurrentModificationException', 'RaceConditionError'], msg_re: ['optimistic lock(ing)? (failed|exception)']}
    prior: {label: pb, confidence: 0.55}
    rationale: "Concurrency defects in product code."
  - mode_key: auth_token_expired  # 50
    title: "Auth token/session expired"
    rules: {msg_re: ['token (is )?expired', 'JWT expired', 'session (has )?expired', 'invalid_grant']}
    prior: {label: ab, confidence: 0.6}
    rationale: "Test credentials lifecycle — automation maintenance."
```

Loader validation (unit-tested): every `exc_re`/`msg_re` compiles; `mode_key` unique;
`label ∈ {pb,ab,si,nd}`; `0 < confidence ≤ 1`. Synthetic fixture logs per mode live in
`tests/fixtures/seed_modes/` (§11).

---

## 10. Evaluation harness

### 10.1 Offline replay

CLI/job `analyzer-ng eval --model <candidate>` over accumulated data:

1. Take all `label_event`s with a stored `suggestion.features` snapshot (time-ordered).
2. Chronological split: train on the first 80%, evaluate on the last 20% (no shuffling —
   prevents temporal leakage). For the *active* model, just score the same eval slice.
3. Recompute predictions with the candidate model **from stored feature vectors**
   (serving-identical by construction, §6.4).
4. Report: per-label precision/recall/F1 (pb/ab/si/nd), macro-F1, **abstain rate**
   (fraction with `p* < τ_suggest`), **acceptance-simulated rate** (fraction of
   non-abstained predictions equal to ground truth — proxy for UI accept rate),
   auto-band precision (precision within `p* ≥ τ_auto` — the metric that guards
   auto-labeling), calibration ECE (10 bins).

### 10.2 Ship gate for a retrained model

Candidate replaces active iff, on the same frozen eval slice:
`macro-F1 ≥ active − 0.005` AND `auto-band precision ≥ max(active, 0.9 · target=0.95)`
AND abstain rate ≤ active + 0.05. Otherwise keep active, store candidate metrics in
`model_artifact.metrics` with `is_active=false`, log WARN. Every activation is an
auditable row.

### 10.3 metrics_daily

Nightly job aggregates per (project, day): suggestions_shown, accepted (defect_update
matching top suggestion), corrected (different label chosen), auto_labeled,
auto_corrected (auto label later changed — the key safety metric), abstained,
per-label counts, active model_id/emb_model_ver. Written to `metrics_daily`
(02-database.md); exposed on the health endpoint summary.

---

## 11. Acceptance criteria checklist

Preprocessing
- [ ] Every ported function has the legacy unit tests passing unmodified.
- [ ] Golden files: 25+ real-world raw logs (java, python, js, selenium, mixed) →
      committed expected `(cleaned, msg, stack, exceptions, codes)`; byte-identical
      across runs and platforms.
- [ ] Filtering: item with 30 logs (10 below ERROR, 5 near-dups at 0.96 sim) yields
      exactly the last ≤ 20 unique ERROR+ logs, matching a hand-computed fixture.

Drain3 / templates
- [ ] Same log stream fed twice (fresh state) → identical `template_hash` set.
- [ ] Kill worker mid-stream, restore from `drain_state` → no new hashes for replayed lines.
- [ ] Delete `drain_state`, rebuild from `template` table → previously seen messages
      map to existing `template_hash`es for ≥ 95% of a 1k-message fixture.

Signature / fingerprint
- [ ] Golden: 20 fixtures → exact expected `exception_fp`, `error_hash`, signature text.
- [ ] Same failure with different UUIDs/timestamps/hex/line numbers → same `error_hash`.
- [ ] Root-cause ordering: nested `Caused by` fixture puts root class first in `EXC:`.
- [ ] Signature ≤ 2000 chars for pathological 1 MB log input; TEST/EXC never truncated.

Embedding
- [ ] Same text → identical vector (bitwise) within one model version; ‖v‖₂ = 1 ± 1e-3.
- [ ] Single-embed p95 ≤ 200 ms on CI runner; batch-32 ≥ 60 items/s.

Grouping
- [ ] Deterministic: shuffled input item order → identical groups and representatives.
- [ ] Burst fixture (12/20 failures share a new fingerprint) → single group,
      `si_prior ≥ 0.5`; below-threshold fixture (3/20) → `si_prior = 0`.

Matching & decision
- [ ] Stage A guards: single ai-sourced match does NOT inherit; two unanimous human
      matches do.
- [ ] Feature extractor returns exactly `len(FEATURES)` floats, no NaN/inf, on: empty
      candidates, no KB, cold project, no stacktrace.
- [ ] `suggestion.features` stored on every prediction; training job reads only these.
- [ ] Cold install (< 50 events) never loads GBM; rule fallback produces decisions.
- [ ] analyzerMode fixtures: for each of the 6 modes, retrieval scope SQL matches the
      §6.0 table (unit test against generated SQL/filters).
- [ ] Auto band respects τ_auto; abstained items answered as absent from analyze reply.

Seed catalog
- [ ] YAML loads; all regexes compile; keys unique; 50 modes present.
- [ ] Synthetic fixture suite (≥ 2 logs per mode, 100+ total): first-matching mode is
      the intended one for **≥ 90%** of fixtures; zero matches on 20 benign INFO-style
      negatives.
- [ ] First match on a project creates exactly one lazy per-project copy (idempotent
      under concurrent workers).

Routes
- [ ] `cluster` reply validates against legacy ClusterResult pydantic model; stable
      clusterId across re-runs of the same launch.
- [ ] `search` returns log ids with scores; respects filteredLaunchIds.
- [ ] `suggest_patterns` and `namespace_finder` respond without error on empty projects.

Evaluation
- [ ] Replay on a synthetic 1k-event stream: metrics reproducible (fixed seed);
      ship gate correctly rejects a deliberately crippled candidate model.
- [ ] `metrics_daily` rows appear after a simulated accept/correct cycle via
      `defect_update`.
