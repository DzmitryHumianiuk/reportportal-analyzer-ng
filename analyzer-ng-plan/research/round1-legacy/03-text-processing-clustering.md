# ReportPortal auto-analyzer: text-processing, preparation & clustering

> Round 1 code analysis. Agent: text processing & clustering.

All paths under the cloned `service-auto-analyzer` repo.

## 1. Raw log → indexed/searched transformation (cleaning/normalization pipeline)

Two entry-point pipelines exist, both orchestrated in `app/utils/log_preparation.py` and driven lazily via the property chain in `app/commons/prepared_log.py`.

### Stage A — `basic_prepare` (`log_preparation.py:18-33`)
Applied first to the raw message:
1. `strip()` (`:19`)
2. Strip leading **log level** (TRACE/DEBUG/INFO/WARN/ERROR/FATAL, incl. `[..]`/`(..)` forms) — `text_processing.remove_starting_log_level` (`:21`, patterns `text_processing.py:90-100`)
3. Strip leading **datetime** (EU/US date+time, bracketed) — `remove_starting_datetime` (`:22`, patterns `text_processing.py:66-87`)
4. Strip leading log level again (`:23`)
5. Strip leading **thread id** (`\d+ --- `) — `remove_starting_thread_id` (`:24`, `text_processing.py:103-111`)
6. Strip leading **thread name** (`[...]`) — `remove_starting_thread_name` (`:25`, `text_processing.py:114-120`)
7. Strip leading log level a third time (after thread name) (`:27`)
8. **Unify line endings** — `unify_line_endings` (`:30`, `text_processing.py:589-596`)
9. Remove `!!!MARKDOWN_MODE!!!` marker — `remove_markdown_mode` (`:31`, `text_processing.py:797-803`)
10. **Delete empty lines** — `delete_empty_lines` (`:32`, `text_processing.py:127-129`)

### Stage B — `clean_message` / `unify_message` (`log_preparation.py:36-53`)
11. Replace markdown/fancy **code separators** (` ``` `, `---`, `===`, `___`) with `TEXTDELIMITER` — `replace_code_separators` (`:37`, `text_processing.py:806-831`)
12. Remove **Selenium/WebDriver auxiliary info** (screenshot captured, Build info, Driver info, System info, Capabilities, page source) — `remove_webdriver_auxiliary_info` (`:38`, `text_processing.py:834-855`)
13. Convert **tabs → newlines** — `replace_tabs_for_newlines` (`:39`, `text_processing.py:585-586`)
14. **URL-decode** big encoded URLs and split with brackets — `fix_big_encoded_urls` (`:40`, `text_processing.py:613-622`)
15. Remove **generated/synthetic stacktrace parts**: `<generated>` lines, inner-class `$` markers, `@<hex>` memory refs, `... N more` truncations — `remove_generated_parts` (`:41`, `text_processing.py:625-646`)
16. Replace **UUIDs/GUIDs** with `SPECIALUUID` — `remove_guid_uuids_from_text` (`:42`, `text_processing.py:559-571`)
17. Redact **access/refresh/JWT tokens & Authorization headers** → `SPECIALTOKEN` — `remove_access_tokens` (`:43`, `text_processing.py:780-794`)
18. Replace **hex literals** with `SPECIALHEX` — `remove_hex_from_text` (`:44`, `text_processing.py:574-582`)
19. Strip **HTML** — `clean_html`/`clean_text_from_html_tags` (`:45`, `text_processing.py:373-415`)
20. Delete empty lines (`:46`)
21. `unify_message` adds **de-duplication of identical lines** + drops "documentation…error…visit" lines — `leave_only_unique_lines` (`:52`, `text_processing.py:649-659`)

### Stage C — final message building
- `prepare_message` (`log_preparation.py:56-59`): keep first N lines (`first_lines`, `text_processing.py:342-344`) + remove test/step method names (`replace_text_pieces`, `text_processing.py:769-773`).
- `prepare_message_no_numbers` (`:62-65`): + **strip numbers** — `remove_numbers` replaces whole-word ints with `SPECIALNUMBER` then deletes remaining digit fragments (`text_processing.py:330-339`). This `message` feeds `logs.whole_message`.

### Tokenization / lowercasing / camelCase
- **Tokenization + stopword removal**: `split_words` → `split_text_on_words` (`text_processing.py:434-448`, `418-431`). `PUNCTUATION_MAP_*` (`:41-50`; `.` and optionally `/ \` preserved to keep class paths/URLs), optional lowercasing, NLTK English stopwords (`STOPWORDS_ALL`, `:36-37`), min word length, optional unique.
- **camelCase / UPPER-lower splitting**: `SPLIT_WORDS_PATTERN` (`:478`) and `CAMEL_CASE_PATTERN` + `UPPER_LOWER_CASE_PATTERN` (`:899-900`, e.g. `XMLHttpRequest → XML Http Request`, `:918-922`).
- **Lemmatization + lowercasing + stopwords**: `preprocess_text_for_similarity` (`:903-945`) — special-char split, camelCase split, lowercase, whitespace collapse, NLTK `WordNetLemmatizer`, stopword drop.

Portability note: the whole pipeline is **English- and JVM/JS/Python-log-centric** (English stopwords/lemmatizer, `FILE_EXTENSIONS` list `:38`, Java/C#/Python stacktrace regexes) — heavily heuristic/regex-tuned, not language-agnostic.

## 2. Clustering feature & algorithm

Clustering ("Unique Errors") lives in `app/service/cluster_service.py`, core algorithm in `app/commons/clustering.py`.

**It is NOT ES-based and NOT true MinHash/SimHash.** Two-stage cosine-similarity clustering with an MD5 n-gram-hash pre-filter:

- `find_clusters` (`clustering.py:150-170`):
  1. **Light dedup** by normalized text (`__perform_light_deduplication`, `:132-147`, uses `preprocess_text_for_similarity`).
  2. **Hash pre-grouping** (`__unite_groups_by_hashes`, `:95-105`): `__calculate_hashes` builds a fingerprint set per message from **MD5 hashes of word n-grams** (default `n_gram_length=2`), keeping `heapq.nlargest(64)` hashes (`:31-48`). MinHash-*style* but ad-hoc (largest-k MD5 hexdigests), no LSH library.
  3. **Similarity grouping** (`__similarity_grouping`, `:51-92`): blocked (block_size 1000) `CountVectorizer(binary=True)` → `cosine_similarity`; union-find-like assignment at similarity ≥ **dynamically recalculated threshold** (`utils.calculate_threshold`, `utils.py:221-231`, loosens by word count). Default 0.95.
  4. Refinement within hash groups (`__find_groups_by_similarity`, `:108-129`).

- **Cluster identity hash**: `calculate_hash` (`cluster_service.py:113-144`) — `CountVectorizer` of **bigrams** (`token_pattern="[^ ]+", ngram_range=(2,2)`), bitwise-AND across group for shared bigrams, **SHA-1** of shared bigrams mod `10**16`, `*10 + cleanNumbers` bit (`:140-143`).

- ES used only *secondarily* to find pre-existing similar clusters via `more_like_this` (`_build_search_similar_items_query`, `cluster_service.py:309-397`) — to reuse an existing cluster id/message, not to cluster.

Flow in `ClusterService.find_clusters` (`:477-565`): prepare → `generate_clustering_messages` → `cluster_messages_with_grouping_by_error` (regroup by exception+status-code first, `:81-110`) → local `clustering.find_clusters` → ES lookup for existing clusters → `gather_cluster_results`.

## 3. Special extracted log fields, namespaces/patterns

`PreparedLogMessage` (`prepared_log.py:29-213`) materializes cached fields:
- **Detected message vs stacktrace split**: `detect_log_description_and_stacktrace` (`text_processing.py:227-246`); Python-specific `detect_log_parts_python` (`:172-197`); Java/C#/generic `is_line_from_stacktrace` (`:200-224`). Fields: `exception_message`, `stacktrace` (`prepared_log.py:102-111`).
- **Exception types**: `get_found_exceptions` — tokens ending in `error`/`exception`/`failure` (`text_processing.py:156-169`); `enrich_found_exceptions` (`:740-752`).
- **HTTP status codes**: `get_potential_status_codes` via `STATUS_CODES_PATTERNS` (`:269-327`).
- **URLs**: `extract_urls` (`:670-683`), `remove_urls`→`SPECIALURL`, `remove_credentials_from_url` (`:732-737`).
- **File paths**: `extract_paths` via `PATH_REGEX` (`:686-711`).
- **Quoted params / numbers**: `extract_message_params` (`:714-724`), `find_only_numbers` (`:451-454`).
- **Test/step methods**: `find_test_methods_in_text` (`:494-519`), `enrich_text_with_method_and_classes` (`:457-475`).

**Namespaces**: `app/commons/namespace_finder.py` uses **gensim `Phrases`** collocation detection (`:51`). Words (split on `.`) accumulated per project; `Phrases(min_count=1, threshold=1)`; candidates with `_` appearing **>10×** become chosen namespaces (`:51-65`). Persisted via `ObjectSaver`.

**Pattern suggestion**: `SuggestPatternsService.suggest_patterns` (`suggest_patterns_service.py:91-121`) mines exceptions over history, aggregates exception→issue-type counts, suggests by `PatternLabelMinCountToSuggest`/`PatternLabelMinPercentToSuggest`/`PatternMinCountToSuggest` (`:60-89`).

## 4. Vocabulary/tokenization reusable for embeddings

- Reusable normalized token stream: `preprocess_text_for_similarity` (`:903-945`) → lemmatized, lowercased, stopword-filtered, camelCase-split tokens. Feeds:
  - **TF-IDF**: `__calculate_tfidf_matrix` (`:948-961`, `TfidfVectorizer(ngram_range=(1,2))`), `calculate_text_similarity` (`:964-1038`), `find_last_unique_texts` (`:1041-1084`).
  - **Binary count vectors** for clustering (`clustering.py:71-72`).
- `preprocess_words` (`:533-556`) — ≥3-char lowercased tokens + merged bigram sub-tokens from `_`/camelCase splits.
- Repo only uses classic sparse TF-IDF/count + cosine; no dense embeddings anywhere in these files.

## 5. Module size & external NLP dependencies

- **`text_processing.py` = 1137 lines** — the heavy central module. clustering.py 170, log_preparation.py 90, prepared_log.py 213.
- External deps: **nltk 3.9.4** (stopwords, WordNetLemmatizer; data downloaded in Dockerfile), **scikit-learn 1.5.2** (TfidfVectorizer, CountVectorizer, cosine_similarity), **scipy 1.13.1** (csr_matrix), **numpy 1.26.4**, **gensim 4.3.3** (only Phrases in namespace_finder).
- The bulk is hand-written `re` regex patterns (datetime, log level, UUID/hex/token, URL/path, HTTP status, HTML, webdriver, stacktrace); NLP libs limited to stopwords/lemmatization/vectorizers/collocations.
