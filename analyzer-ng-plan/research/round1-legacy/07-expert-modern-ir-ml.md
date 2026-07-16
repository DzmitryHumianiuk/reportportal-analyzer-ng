# Modernizing ReportPortal Auto-Analysis for BETTER Results

> Round 1 expert panel. Role: senior ML/IR research engineer (log analysis, semantic retrieval, LTR).
> Ranked by ROI (biggest cheap win first).

## TL;DR verdict

For short, templated, identifier-heavy machine logs, **dense embeddings do NOT reliably beat well-tuned BM25 alone, and often lose** — BM25 wins on error codes, stack frames, symbols ([BM25 vs dense](https://ranjankumar.in/bm25-vs-dense-retrieval-for-rag-engineers), [denser.ai](https://denser.ai/blog/hybrid-search-for-rag/)). Highest-ROI moves are NOT "add embeddings": (1) fix normalization with a real parser, (2) exploit labeled feedback via proper LTR and a better classifier, (3) build an evaluation harness BEFORE swapping anything. Embeddings belong as a *hybrid* addition for the semantic-gap tail.

## Rank 1 — Build the evaluation harness FIRST

- Gold data exists: `issue_history` + analyzed-item labels. Build: frozen retrieval test set (Recall@k, MRR, nDCG@k), classification test set (per-class P/R/F1 + calibration/Brier), online metrics (acceptance rate, correction rate per project), shadow evaluation.
- Highest ROI of anything: without it every change is a guess; multi-tenant means a global "win" can be a per-project regression. Cost negligible; risk low; datastore-independent.

## Rank 2 — Real log parsing/templating (Drain3)

- Replace/augment hand-rolled regex normalization with **Drain3** ([logpai/Drain3](https://github.com/logpai/Drain3)) — fixed-depth parse tree clusters raw lines into templates + params online.
- Beats regex: regex is a fixed English-centric denylist of *known* noise; Drain discovers variable positions structurally; every new noise class currently = code change.
- Gain: high, compounds into BM25 recall, TF-IDF precision, clustering purity, classifier features. Cost: pure-Python, μs/line, tiny memory; persist template state per project.
- Caveats: needs masking hints + tuning (over-merge risk); keep `found_exceptions`/error codes verbatim — strongest exact signals.

## Rank 3 — Learning-to-rank (LightGBM LambdaMART) instead of XGBoost binary classifier

- Current final ranker optimizes classification loss, not ranking. With graded relevance feedback, switch to `lambdarank` / `rank:ndcg` — same tree family, same features, change the loss + query groups ([XGBoost LTR docs](https://xgboost.readthedocs.io/en/latest/tutorials/learning_to_rank.html)).
- Unbiased LambdaMART can debias position/selection bias in confirm/correct logs.
- Keep monotone constraints (strong prior, stabilizes small per-project data). Keep F1 gate for auto-assign; add nDCG gate for suggestions.
- Gain: medium-high on suggestion ordering; cost ≈ identical; risk low.

## Rank 4 — Upgrade defect-type classifier + close the active-learning loop

- Replace per-base-type RandomForest+TF-IDF(binary) with a single multiclass LightGBM with class weighting/focal loss; **probability calibration** (isotonic/Platt) so thresholds are meaningful; **active learning** on the confirm/correct stream (prioritize low-confidence items; route uncertain to `ti`).
- With tiny per-project data, calibration + abstention discipline matter more than model family.

## Rank 5 — Proper near-duplicate clustering: MinHash+LSH (datasketch)

- Current MD5-of-top-64-bigrams fingerprint is lossy and non-metric — no similarity-preserving guarantee, threshold behaves inconsistently across projects.
- MinHash sketch collision probability *equals* Jaccard; LSH banding gives tunable near-dup buckets sub-linearly — the standard in LLM-data dedup ([datasketch/Milvus](https://milvus.io/blog/minhash-lsh-in-milvus-the-secret-weapon-for-fighting-duplicates-in-llm-training-data.md)).
- Pairs naturally with Drain3: cluster on templates. Embedding+HNSW clustering only if embeddings already computed.

## Rank 6 — Hybrid retrieval: keep BM25, ADD a small dense retriever, fuse with RRF

- RRF (k≈60) on ranks, no score normalization; consistently ≥ either alone; BM25 and dense capture orthogonal relevance.
- Encoders on CPU, descending cheapness: **Model2Vec/POTION static embeddings** (~500x faster, small quality drop — best first experiment); **MiniLM/E5/BGE/GTE small** (384-dim, sub-30ms CPU); code-aware models only if stacktraces dominate (test — general code embedders underperform on error logs, cf. SweRank).
- **Multilingual**: multilingual-e5-small or bge-m3 (dense+sparse) — a capability the lexical stack can't have.
- Quantization: int8 ≈4x smaller; binary ≈32x with rescoring ~96% retained.
- Honest: value is the **recall tail** (semantically same, lexically divergent). Won't help projects already separated by exception class + error code. Enable per-project based on measured lift. This is the ONE item touching the datastore (any ANN backend: pgvector/FAISS/hnswlib/Qdrant).

## Rank 7 — Cross-encoder re-ranker (probably skip on CPU)

- 100-300ms per ~50 candidates on CPU; you already have a strong learned reranker with 40 engineered features + real feedback; generic web-QA cross-encoders can't use non-text features. Only if diagnostics prove a *semantic* ranking gap — then domain-fine-tuned, not off-the-shelf.

## What I'd actually do first
1. Evaluation harness from issue_history (+ shadow mode).
2. Drain3 templating (keep raw + exception/code fields verbatim).
3. LightGBM LambdaMART (keep monotone constraints; F1 gate + nDCG gate).
4. Calibrated multiclass classifier + abstention + active learning.
5. MinHash+LSH on templates.
6. Hybrid BM25+dense via RRF, per-project gated; multilingual encoder for non-English tenants.
7. Cross-encoder only if proven semantic gap.

## Honest caveats — where modern ML will NOT move the needle
- Dense embeddings are not a magic upgrade for logs (hybrid-for-recall-tail, not replacement).
- SPLADE/learned sparse: ~2% over BM25 out-of-domain on BEIR, markedly less efficient — not worth it here.
- Code embedding models are a hypothesis to test, not a default.
- On tiny per-project datasets, model sophistication saturates fast.
- Cross-encoders on CPU are a latency liability.
- Multi-tenancy: enable heavy additions per-project, gated on measured lift.

Sources: BM25-vs-dense (Ranjan Kumar), denser.ai hybrid, digitalapplied hybrid ref 2026, RRF writeups, Drain3/TPLogAD, XGBoost LTR docs, LambdaMART (Shaped/apxml), focal+LightGBM, Small-Text active learning, MinHash/LSH (Milvus, emergentmind, iunera), MTEB/CodeSOTA, Model2Vec/HF static embeddings, bge-m3, HF embedding-quantization, SPLADE-v3, SweRank, cross-encoder guides (BigData Boutique, Local AI Master), Pinecone offline eval.
