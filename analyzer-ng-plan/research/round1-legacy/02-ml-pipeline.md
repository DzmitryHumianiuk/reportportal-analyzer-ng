# ML Ranking/Scoring Pipeline Analysis — ReportPortal `service-auto-analyzer`

> Round 1 code analysis. Agent: ML ranking pipeline.

## 1. Overall Auto-Analysis Task & End-to-End Flow

The service takes a **failed test log**, finds **similar past failures** already labeled with a defect/issue type in OpenSearch, extracts features describing how well each candidate matches, and runs a **gradient-boosting binary classifier** to decide whether the best candidate per issue-type/test-item is a genuine match. Two entry points exist: **auto-analysis** (auto-assign issue type) and **suggestion** (rank suggestions for the "Make Decision" modal).

**Auto-analysis flow** (`app/service/auto_analyzer_service.py`):
1. `analyze_logs` (`auto_analyzer_service.py:332`) is the entry. Logs are prepared into request logs via `prepare_request_logs_for_launch` (`:76`) → `request_factory.prepare_test_items`.
2. **ES candidate retrieval**: `_get_analysis_candidates` (`:295`) → `_query_candidates_for_test_item` (`:250`) builds a nested `more_like_this` query in `_build_nested_analyze_query` (`:155`) and runs `self.os_client.msearch` (`:272`). Inner-hit logs are extracted (`extract_inner_hit_logs`) and re-aligned to request logs with `bucket_sort_logs_by_similarity` / `build_search_results` (`:282-287`).
3. **Feature extraction + model**: an `AutoAnalysisPredictor` (`:356`) is built with a boosting config from `_get_config_for_boosting` (`:133`). `predictor.predict(candidate.candidates)` (`:396`) runs the pipeline.
4. **Decision**: keep `label == 1` predictions (`:408`), group by test item (`utils.group_predictions_by_test_item`, `:415`), rank by central-weighted score (`utils.score_and_rank_test_items`, `:416`), take the top result (`:423`) and emit an `AnalysisResult` (`to_analysis_result`, `:104`).

**Predictor core** (`app/ml/predictor.py`), `MlPredictor.predict` (`:154`):
- create featurizer (`:166`) → `gather_features_info()` returns feature matrix + identifiers (`:169`) → collect model-info tags (`:172`) → `boosting_decision_maker.predict(feature_data)` returns labels + probabilities (`:182`) → assemble `PredictionResult` per candidate (`:193-205`), attaching the "most relevant" hit from `find_most_relevant_by_type()` (`:189`).

**Suggestion flow** (`app/service/suggest_service.py`): `suggest_items` (`:458`) mirrors this using `_build_nested_suggest_query` (`:249`), `SuggestionPredictor`/`SimilarityPredictor` chosen via `PREDICTION_CLASSES[self.search_cfg.MlModelForSuggestions]` (`:489`), then `deduplicate_results` (`:199`) removes near-duplicates and results above `suggest_threshold=0.4` (`:233`, `:529`) are returned.

---

## 2. ML Models

Three model types are enumerated in `app/commons/model/ml.py:21` (`ModelType`: `defect_type`, `suggestion`, `auto_analysis`).

**A. Boosting Decision Maker** — the ranking/scoring model (`app/ml/models/boosting_decision_maker.py`):
- **Library/type**: `xgboost.XGBClassifier` — gradient-boosted trees, **binary classification** (match / no-match) (`:18`, `:61`, `:114`).
- Hyperparameters: `n_estimators=75`, `max_depth=5`, `learning_rate=0.2`, `random_state=43` defaults (`:28-31`); **monotone constraints** applied per-feature via `monotone_constraints` (`:112-118`).
- `predict` returns `predict()` labels + `predict_proba()` probabilities (`:134-137`). Trained with F1 scoring (`:124`).
- Used for both `auto_analysis` and `suggestion` (`model_chooser.py:44-46`).
- `CustomBoostingDecisionMaker` (`app/ml/models/custom_boosting_decision_maker.py:21`) is the per-project variant (`is_custom = True`).

**B. Defect Type Model** (`app/ml/models/defect_type_model.py`):
- **Library/type**: `sklearn.ensemble.RandomForestClassifier` (**not** XGBoost) with `class_weight="balanced"`, `n_estimators=10` default, plus a `TfidfVectorizer(binary=True, min_df=5, analyzer=preprocess_words)` per issue-type base name (`:88-99`, `:121-137`).
- One vectorizer+classifier pair per defect base type; dispatched by regex `BASE_DEFECT_TYPE_PATTERN` (`:34`, `:65-83`). Falls back to `DummyVectorizer`/`DummyClassifier` returning zeros when a type is missing (`:41-62`).
- `predict(data, model_name)` TF-IDF-transforms text then predicts probability (`:159-165`). Consumed as **feature 51** inside the featurizer, not as a top-level decision.

**C. SimilarityPredictor** (`app/ml/predictor.py:330`) — a non-ML fallback for suggestions: pure `calculate_text_similarity` (TF-IDF cosine), thresholded at 0.5 (`:346`, `:356`, `:383`).

**Model storage / loading** (`app/commons/model_chooser.py`, `app/commons/object_saving/`):
- `ObjectSaver` (`object_saver.py:47`) delegates to a `Storage` backend selected by `app_config.datastoreType` from `STORAGE_FACTORIES` (`object_saving/__init__.py:40`): **`"minio"` → MinioClient, `"filesystem"` → FilesystemSaver, `"s3"` → Boto3Client** (`__init__.py:28-44`).
- Models are Python pickles: boosting model files `["boost_model.pickle", "data_features_config.pickle"]` (`boosting_decision_maker.py:27`); defect-type files `["count_vectorizer_models.pickle", "models.pickle"]` (`defect_type_model.py:32`). Loaded via `MlModel._load_models` / saved via `_save_models` using `get_project_object`/`put_project_object` (`ml_model.py:31-42`).
- **Global** models load from filesystem folders configured in `SearchConfig` (`BoostModelFolder`, `SuggestBoostModelFolder`, `GlobalDefectTypeModelFolder`) via `object_saving.create_filesystem` (`model_chooser.py:65-81`). **Custom** per-project models load from the configured datastore (MinIO/S3/FS) at `{model_type}_model/{folder}/` (`model_chooser.py:95-100`). `choose_model` (`:83`) picks custom vs global using an MD5-hash-mod-100 vs `custom_model_prob` gate (`:91-94`).

---

## 3. Features Computed by `BoostingFeaturizer`

Registered in `feature_functions` (`app/ml/boosting_featurizer.py:101-148`). Each returns `{issue_type -> value}`. Enumerated by ID:

| ID | Method | What it measures | Source |
|----|--------|------------------|--------|
| 0 | `_calculate_score` (`:468`) | Normalized ES score summed per issue type | **ES score** |
| 1 | `_calculate_place` (`:588`) | Inverse rank order of issue type by score | ES-derived |
| 3 | `_calculate_max_score_and_pos` → `max_score_pos` (`:600`) | Inverse position of max-scoring hit | ES-derived |
| 5 | `_calculate_min_score_and_pos` → `min_score_pos` (`:621`) | Inverse position of min-scoring hit | ES-derived |
| 7 | `_calculate_percent_count_items_and_mean` → `cnt_items_percent` (`:642`) | Fraction of hits of this issue type | ES-derived |
| 9 | `_calculate_percent_issue_types` (`:550`) | 1/number-of-issue-types weight | ES-derived |
| 11 | `_calculate_similarity_percent` field=`message` (`:700`) | TF-IDF cosine similarity | **Python** |
| 12 | `is_only_merged_small_logs` (`:488`) | Both logs are only merged small logs | Python (sim) |
| 13 | field=`merged_small_logs` | TF-IDF cosine similarity | **Python** |
| 14 | `_has_test_item_several_logs` (`:562`) | Relevant item has merged small logs | Field value |
| 15 | `_has_query_several_logs` (`:575`) | Query item has merged small logs | Field value |
| 18 | field=`detected_message` | TF-IDF cosine | **Python** |
| 19 | field=`detected_message_with_numbers` | TF-IDF cosine | **Python** |
| 23 | field=`stacktrace` | TF-IDF cosine | **Python** |
| 25 | `_calculate_similarity_by_values` field=`only_numbers` (`:684`) | Token-overlap similarity | **Python** |
| 26 | `max_score` | Max normalized ES score | **ES score** |
| 27 | `min_score` | Min normalized ES score | **ES score** |
| 28 | `mean_score` | Mean normalized ES score | **ES score** |
| 29 | field=`message_params` | TF-IDF cosine | **Python** |
| 34 | field=`found_exceptions` | TF-IDF cosine | **Python** |
| 35 | `_is_analyzed_manually` (`:477`) | Relevant hit was manually analyzed | Field value |
| 36 | field=`detected_message_extended` | TF-IDF cosine | **Python** |
| 37 | field=`detected_message_without_params_extended` | TF-IDF cosine | **Python** |
| 38 | field=`stacktrace_extended` | TF-IDF cosine | **Python** |
| 40 | field=`message_without_params_extended` | TF-IDF cosine | **Python** |
| 41 | field=`message_extended` | TF-IDF cosine | **Python** |
| 42 | `is_the_same_test_case` (`:426`) | Same `test_case_hash` | Field equality |
| 43 | `has_the_same_test_case_in_all_results` (`:447`) | Same test case anywhere in results | Field equality |
| 48 | `is_text_of_particular_defect_type` label=`ab` (`:369`) | Relevant hit is Automation-Bug | Field prefix |
| 49 | label=`pb` | Product-Bug | Field prefix |
| 50 | label=`si` | System-Issue | Field prefix |
| 51 | `predict_particular_defect_type` (`:343`) | **DefectTypeModel** RF probability | **ML sub-model** |
| 52 | `fields_equal` field=`urls` (`:397`) | URLs equal | Field equality |
| 53 | field=`detected_message_without_params_and_brackets` | TF-IDF cosine | **Python** |
| 55 | field=`potential_status_codes` | Status codes equal | Field equality |
| 56 | `is_the_same_launch` (`:433`) | Same launch name | Field equality |
| 57 | `is_the_same_launch_id` (`:440`) | Same launch id | Field equality |
| 59 | field=`found_tests_and_methods` | TF-IDF cosine | **Python** |
| 61 | field=`test_item_name` | TF-IDF cosine | **Python** |
| 64 | `_calculate_decay_function_score` field=`start_time` (`:312`) | Exponential time decay `exp(log(time_weight_decay)*days/7)` | Python (field) |
| 65 | `_calculate_test_item_logs_similar_percent` (`:286`) | Fraction of queried logs found for item | ES-derived counts |
| 66 | `_count_test_item_logs` (`:253`) | Number of DB requests made | ES-derived counts |

Features **51, 58, 67–73** are forced to always recalculate (`:163`). Actual features used at inference come from `boosting_decision_maker.feature_ids` (loaded from `data_features_config.pickle`), fed via `create_featurizer` (`predictor.py:257`).

**`SuggestBoostingFeaturizer`** (`app/ml/suggest_boosting_featurizer.py`) overrides the "most relevant" grouping to be **per test-item** rather than per issue-type (`find_most_relevant_by_type`, `:45`) and overrides ID 9, 3, 5, 7 aggregation accordingly.

### Feature source split
- **Directly from OpenSearch relevance scoring**: **0, 1, 3, 5, 7, 9, 26, 27, 28, 65, 66**.
- **Computed in Python (TF-IDF cosine)**: **11, 13, 18, 19, 23, 29, 34, 36, 37, 38, 40, 41, 53, 59, 61**.
- **Python token-overlap**: **25**.
- **Python field comparisons/flags**: **14, 15, 35, 42, 43, 48, 49, 50, 52, 55, 56, 57, 64**.
- **ML sub-model**: **51**.

---

## 4. Text Similarity Computation

**No neural embeddings** are used in the ML ranking/scoring pipeline. `gensim==4.3.3` is imported only in `app/commons/namespace_finder.py:16` (`gensim.models.phrases.Phrases`) for namespace/phrase detection — **not** in featurization or ranking. No word2vec/fasttext/embedding references exist anywhere else.

Text similarity is **lexical TF-IDF cosine**, implemented in `app/utils/text_processing.py`:
- `calculate_text_similarity` (`:964`): preprocess with `preprocess_text_for_similarity` (`:903` — special-char split, CamelCase split, lowercase, NLTK stopword removal + WordNet lemmatization), then `__calculate_tfidf_matrix` (`:948`) using `sklearn TfidfVectorizer(ngram_range=(1,2), use_idf=False by default)` and `cosine_similarity` (`:1029`). Identical texts short-circuit to 1.0, empties to 0.0.
- Wrapped by `SimilarityCalculator.find_similarity` (`app/commons/similarity_calculator.py:48`) — per-field similarity dicts keyed by `(hit_id, log_id)`, cached.
- The boosting feature vectorizer uses `use_idf=False` (pure TF cosine); `find_last_unique_texts` (`text_processing.py:1070`) and the defect-type model use IDF. The suggest ES query log comment calls it "FTS (KNN)" but the underlying OS query is `more_like_this` (BM25-style), not vector KNN.
- The DefectTypeModel uses its own `TfidfVectorizer(binary=True, analyzer=preprocess_words)` (`defect_type_model.py:122`).

Candidate retrieval relevance is OpenSearch `more_like_this` (BM25 over term stats), built in `utils.build_more_like_this_query` (`app/utils/utils.py:239`) with `min_doc_freq`, `min_term_freq`, `minimum_should_match`, `max_query_terms`, `boost`.

---

## 5. Training & Labels

**Two independent trainers** under `app/ml/training/`, both querying OpenSearch for labeled history.

**Analysis (boosting) model** (`train_analysis_model.py`, `AnalysisModelTraining`):
- **Data source**: `_query_data` (`:532`) runs `build_issue_history_query` (`training/__init__.py:85`) against `os_client.search` per project. Entries built by `build_entries_from_item` (`:290`).
- **Label**: binary. Positive = latest confirmed history entry `issue_history[-1].issue_type` (`:294`, `:560`); negatives = other/history issue types. Selection in `select_candidate_entries` (`:186`) with negative:positive ratios `NEGATIVE_RATIO_MIN=2`/`MAX=4`. Synthetic history-negative hits fabricated when needed (`build_history_negative_hits`, `:260`).
- **Featurization for training**: same `more_like_this` OS query (`_build_similar_items_query`, `:419`), then featurizers produce feature rows (`_featurize_data`, `:541`).
- **Training loop**: `train_several_times` (`:113`) — stratified split (10% test), optional `BorderlineSMOTE` when positives scarce (`:136-143`), fits `XGBClassifier`, scores **F1**. Custom model saved only if it beats baseline with ANOVA `p_value < 0.05` and `mean_metric >= 0.4` (`:712`).

**Defect type model** (`train_defect_type_model.py`): same OS `issue_history` query (`:225`). Per-defect-type binary target via `create_binary_target_data` (`:70`). One RandomForest per label (`:285-335`), F1-gated (`p<0.05`, `>=0.4`). `balance_data` (`training/__init__.py:98`).

**Retraining triggering** (`retraining_service.py`, `retraining_triggering.py`): count-based — training fires when `GATHERED_METRIC_TOTAL >= 100` and `METRIC_SINCE_TRAINING >= 100` (`retraining_triggering.py:75-78`); counters reset after training.

---

## 6. Parts Affected if OpenSearch Were Removed

1. **Candidate retrieval entirely** — all candidates come from OS `msearch`/`search` with `more_like_this` (`auto_analyzer_service.py:272`, `suggest_service.py:381`, training queries).
2. **Features derived from ES relevance score**: 0, 1, 3, 5, 7, 9, 26, 27, 28 (+65, 66 counts) — computed in `find_most_relevant_by_type`, `normalize_results`, etc. All consume `hit.score` from ES BM25.
3. **min_should_match filtering** relies on the ES query but the similarity re-check itself is Python.

**NOT ES-scoring dependent** (run on hit field contents once candidates exist): all TF-IDF cosine similarities, token overlap (25), field-equality/flags, time-decay (64), DefectTypeModel probability (51) — pure Python/sklearn, but still require the candidate documents ES supplies.
