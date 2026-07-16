# Analyzer-NG Plan Package

Полный пакет: исследование → дизайн → спецификации → план мультиагентной имплементации
нового анализатора ReportPortal (PostgreSQL + pgvector вместо OpenSearch, опциональный
локальный SLM через Ollama).

## Порядок чтения

1. `CONTEXT.md` — единый дизайн-бриф (источник правды для имплементирующих агентов).
2. `specs/01-architecture.md` — сервис, AMQP-контракт (drop-in замена legacy), docker.
3. `specs/02-database.md` — DDL, миграции, контракты сторов, гибридный SQL с RRF.
4. `specs/03-pipeline.md` — препроцессинг, Drain3, сигнатуры/fingerprints, группировка,
   матчинг, 39 фич LightGBM, decision policy, seed-каталог 50 failure modes, eval.
5. `specs/04-llm-sidecar.md` — опциональный Ollama-сайдкар: 4 роли, промпты, схемы,
   injection-защита, kill-switch.
6. `MASTER_PLAN.md` — фазовый план (6 фаз, 15 задач, 6 гейтов G0–G5, риск-реестр).

## Исследование (полные отчёты экспертов)

### `research/round1-legacy/` — анализ существующего service-auto-analyzer
- `01-opensearch-layer.md` — что хранится в ES, маппинги, все query-конструкции.
- `02-ml-pipeline.md` — модели, полный реестр фич BoostingFeaturizer, обучение.
- `03-text-processing-clustering.md` — пайплайн очистки текста, кластеризация.
- `04-architecture-storage.md` — AMQP-роуты, зависимости, конфиг, контракты analyze/suggest.
- `05-expert-postgresql.md` — вердикт 4/5: PG-native retrieval.
- `06-expert-lakehouse.md` — вердикт 2/5 онлайн, 5/5 обучение: Parquet/Iceberg/DuckDB.
- `07-expert-modern-ir-ml.md` — ROI-ранжирование: Drain3, LTR, калибровка, hybrid+RRF.
- `08-expert-architect-devils-advocate.md` — ловушка score-фичи, фазы с kill-criteria.

### `research/round2-greenfield/` — консилиум «с чистого листа»
- `01-vector-retrieval-backbone.md` — pgvector побеждает; сравнение векторных БД 2026.
- `02-local-slm-triage.md` — роли LLM (judge/explainer/extractor/cold-start = да;
  классификатор/LoRA/reasoning/агенты = нет), модели, injection-защита.
- `03-aiops-sota-survey.md` — что реально делают Google/Meta/Sentry/Develocity;
  не-текстовые сигналы; launch-level grouping; список overhyped.
- `04-chief-architect-synthesis.md` — 3 архитектуры, матрица, выбор «Knowledge-base-first».

## Артефакт

- `artifact/analyzer-ng-doc.html` — сводный HTML-документ (опубликован:
  https://claude.ai/code/artifact/1e766866-2407-4648-8ded-5978536d3a1e).

## Быстрый старт имплементации

Каждому агенту: CONTEXT.md → секция спеки его задачи → карточка задачи в MASTER_PLAN.md.
Начало: Phase 0 (T0.1 repo skeleton ∥ T0.2 dev/CI harness ∥ T0.3 config module).
