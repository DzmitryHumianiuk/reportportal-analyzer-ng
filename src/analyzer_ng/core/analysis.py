"""Analysis engine — analyze / suggest / cluster / search routes (spec 03 §5-§8).

Ties the pure analytical modules (:mod:`grouping`, :mod:`features`,
:mod:`decision`, :mod:`scope`) to the spec-02 stores and renders the legacy wire
shapes (spec 01 §4.2). The GBM is absent (T3.1); the rule-based cold fallback
(:func:`decision.decide`) is the decision function, but the full 39-feature vector
is snapshotted into ``suggestion.features`` on every decision.

Pipeline per launch: build signatures/embeddings (read-only) → launch grouping
(§5) → per-representative matching A/B/C (§6.1-§6.3) → decision + policy bands
(§6.6) → fan out to members, persist ``launch_group`` + ``suggestion`` rows.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from analyzer_ng.amqp.models import (
    ERROR_LOGGING_LEVEL,
    AnalysisResult,
    ClusterInfo,
    ClusterResult,
    Launch,
    LaunchInfoForClustering,
    Log,
    SearchLogInfo,
    SearchLogs,
    SuggestAnalysisResult,
    TestItem,
    TestItemInfo,
)
from analyzer_ng.core import scope
from analyzer_ng.core.decision import (
    ACTION_AUTO,
    METHOD_GBM,
    TAU_AUTO,
    TAU_SUGGEST,
    DecisionInputs,
    DecisionResult,
    GbmDecision,
    HashMatch,
    decide,
)
from analyzer_ng.core.features import (
    FEATURE_SCHEMA_VER,
    LLM_UNKNOWN,
    TIME_DECAY_PER_DAY,
    FeatureContext,
    SeedSignal,
    feature_names,
    src_weight,
    to_vector,
)
from analyzer_ng.core.grouping import BURST_X, GroupItem, LaunchGroup, group_launch
from analyzer_ng.core.ingest import IndexPipeline, ItemAnalysis
from analyzer_ng.db.repositories.models import (
    Candidate,
    CandidateFilters,
    QuerySignature,
    SuggestionIn,
)
from analyzer_ng.db.repositories.retrieval import PgRetrievalStore
from analyzer_ng.ml.hashing import xxh3_64_unsigned
from analyzer_ng.ml.serving import GbmPredictor
from analyzer_ng.seeds.loader import SeedKB

logger = logging.getLogger(__name__)

TOP_K = 20
# Retrieve wider than TOP_K so the analyzerMode hard-scope filter (§6.0) is not
# starved by out-of-scope rows dominating the top-20 before filtering.
STAGE_C_RETRIEVE_K = 60
SEARCH_COS_THRESHOLD = 0.75
INT53_MASK = (1 << 53) - 1  # Java long-safe positive cluster id
SUGGEST_MAX = 3
MSG_SALIENT_TERMS = 24
CLUSTER_MSG_LINES = 5


@dataclass
class AnalysisEngine:
    """Read-path orchestrator over the spec-02 stores + pure analytical modules."""

    retrieval: PgRetrievalStore
    kb: object  # KBStore
    stats: object  # StatsStore
    pipeline: IndexPipeline
    seed_kb: SeedKB | None = None
    emb_model_tag: str = "none"
    predictor: GbmPredictor | None = None  # shipped GBM; None => rule fallback (§6.5)
    # Optional LLM sidecar (spec 04). ``sidecar.enqueue`` is a no-op when the master
    # switch is off, so the suggest/analyze paths are byte-identical with it absent.
    sidecar: object | None = None
    # Feature-time extractor lookup (spec 04 §4.2): (project, exception_fp,
    # template_ids) -> (failing_layer, error_class) | None. Miss ⇒ ``unknown``.
    extractor_features: Callable[[int, int, Sequence[int]], tuple[str, str] | None] | None = None
    judge_tau: float = TAU_AUTO  # judge fires on τ_suggest ≤ p* < judge_tau (§4.3)
    # Operator-tunable knobs (spec 01 §5.2). Defaults equal the code constants so an
    # engine built without them is byte-identical to the pre-wiring build.
    auto_min_prob: float = TAU_AUTO  # ANALYZER_AUTO_MIN_PROB → decision auto band
    suggest_max: int = SUGGEST_MAX  # ANALYZER_SUGGEST_MAX → suggestions returned
    burst_si_share: float = BURST_X  # ANALYZER_BURST_SI_SHARE → grouping burst prior
    time_decay: float = TIME_DECAY_PER_DAY  # ANALYZER_TIME_DECAY → feature recency decay

    # ------------------------------------------------------------------ #
    # analyze (spec §6.6 analyze column)
    # ------------------------------------------------------------------ #
    def analyze(self, launches: Sequence[Launch]) -> list[AnalysisResult]:
        results: list[AnalysisResult] = []
        for launch in launches:
            results.extend(self._analyze_launch(launch))
        return results

    def _analyze_launch(self, launch: Launch) -> list[AnalysisResult]:
        project = launch.project
        entries = [(launch, item) for item in launch.testItems]
        analyses = self.pipeline.build_item_analyses(project, entries)
        by_id = {a.item.testItemId: a for a in analyses}
        groups = self._group(project, launch.launchId, analyses)
        total_failures = len(analyses)

        out: list[AnalysisResult] = []
        for group in groups:
            rep = by_id[group.representative.item_id]
            decision = self._decide(
                project,
                self._scope_query(launch),
                launch.analyzerConfig.analyzerMode,
                rep,
                group,
                total_failures,
                route="analyze",
            )
            group_id = self.retrieval.upsert_launch_group(
                project,
                launch.launchId,
                group.fingerprint,
                len(group.members),
                group.si_prior,
                dominant=group.si_prior > 0.0,
            )
            for member in group.members:
                self._write_suggestion(project, member.item_id, launch.launchId, group_id, decision)
                # §1.5: enqueue async LLM enrichment after the row is committed.
                self._enqueue_llm(project, member.item_id, launch.launchId, decision)
                if decision.action == ACTION_AUTO and decision.label != "ti":
                    self.retrieval.update_issue_type(
                        project, member.item_id, decision.issue_type, is_auto=True
                    )
                    out.append(
                        AnalysisResult(
                            testItem=member.item_id,
                            issueType=decision.issue_type,
                            relevantItem=decision.relevant_item_id or 0,
                        )
                    )
        return out

    # ------------------------------------------------------------------ #
    # suggest (spec §6.6 suggest column; read budget: embed + SQL only)
    # ------------------------------------------------------------------ #
    def suggest(self, info: TestItemInfo) -> list[SuggestAnalysisResult]:
        started = time.monotonic()
        project = info.project
        launch = self._pseudo_launch(info)
        analyses = self.pipeline.build_item_analyses(project, [(launch, launch.testItems[0])])
        rep = analyses[0]
        if not rep.signature.signature_text:
            return []  # empty signature (no ERROR logs) → [] (spec §3.4)

        group = self._singleton_group(rep)
        decision = self._decide(
            project,
            self._scope_query_info(info),
            info.analyzerConfig.analyzerMode,
            rep,
            group,
            total_failures=1,
            route="suggest",
        )
        # §6.6: EVERY decision writes a suggestion row — including an abstain that
        # renders an empty reply. Persist here, before rendering, so an abstained
        # item later labeled by a human still carries its feature snapshot into
        # training (the analyze route already writes per member; suggest must too).
        self._write_suggestion(info.project, info.testItemId, info.launchId, None, decision)
        elapsed = time.monotonic() - started
        # §4.3 read-path surfacing: honor a prior async judge verdict by promoting the
        # chosen candidate to resultPosition 0. Only when the sidecar is on, so the
        # LLM-off path skips the read entirely and stays byte-identical.
        judge_verdict = (
            self.retrieval.latest_judge(info.project, info.testItemId) if self._llm_on() else None
        )
        out = self._render_suggestions(info, rep, decision, elapsed, judge_verdict)
        # §1.5: enqueue async LLM enrichment *after* the classical reply is built and
        # the suggestion row committed. Never on the synchronous suggest budget.
        self._enqueue_llm(
            info.project,
            info.testItemId,
            info.launchId,
            decision,
            candidates=self._judge_candidates(decision),
        )
        return out

    def _llm_on(self) -> bool:
        return self.sidecar is not None and getattr(self.sidecar, "enabled", False)

    # ------------------------------------------------------------------ #
    # cluster (spec §8.1)
    # ------------------------------------------------------------------ #
    def cluster(self, info: LaunchInfoForClustering) -> ClusterResult:
        launch = info.launch
        project = info.project
        entries = [(launch, item) for item in launch.testItems]
        analyses = self.pipeline.build_item_analyses(project, entries)
        by_id = {a.item.testItemId: a for a in analyses}
        groups = self._group(project, launch.launchId, analyses)

        clusters: list[ClusterInfo] = []
        for group in groups:
            rep = by_id[group.representative.item_id]
            cluster_id = self._cluster_id(
                project, launch.launchId, group.representative.error_hash, info.forUpdate
            )
            log_ids: list[int] = []
            item_ids: list[int] = []
            for member in group.members:
                m = by_id[member.item_id]
                item_ids.append(member.item_id)
                log_ids.extend(log.logId for log in m.item.logs)
            clusters.append(
                ClusterInfo(
                    clusterId=cluster_id,
                    clusterMessage=self._cluster_message(rep),
                    logIds=log_ids,
                    itemIds=item_ids,
                )
            )
        return ClusterResult(project=project, launchId=launch.launchId, clusters=clusters)

    # ------------------------------------------------------------------ #
    # search (spec §8.2) — hybrid retrieval, no decision layer
    # ------------------------------------------------------------------ #
    def search(self, request: SearchLogs) -> list[SearchLogInfo]:
        project = request.projectId
        logs = [
            Log(logId=i, logLevel=ERROR_LOGGING_LEVEL, message=m)
            for i, m in enumerate(request.logMessages)
            if m.strip()
        ]
        if not logs:
            return []
        launch = Launch(launchId=request.launchId, project=project, launchName=request.launchName)
        item = TestItem(testItemId=request.itemId, isAutoAnalyzed=False, logs=logs)
        analyses = self.pipeline.build_item_analyses(project, [(launch, item)])
        rep = analyses[0]
        if not rep.signature.signature_text:
            return []

        q = self._query_signature(rep, launch_id=request.launchId, launch_number=0)
        cands = self.retrieval.find_candidates(project, q, k=TOP_K)
        filtered = set(request.filteredLaunchIds)
        out: list[SearchLogInfo] = []
        for c in cands:
            if c.item_id is None:
                continue
            if filtered and c.launch_id not in filtered:
                continue
            cos = c.cosine or 0.0
            fts_hit = (c.lex_score or 0.0) > 0.0
            if cos >= SEARCH_COS_THRESHOLD or fts_hit:
                out.append(
                    SearchLogInfo(
                        logId=c.relevant_log_id or 0,
                        testItemId=c.item_id,
                        matchScore=round(min(1.0, cos) * 100, 2),
                    )
                )
        return out

    # ------------------------------------------------------------------ #
    # Grouping + matching + decision helpers
    # ------------------------------------------------------------------ #
    def _group(
        self, project: int, launch_id: int, analyses: Sequence[ItemAnalysis]
    ) -> list[LaunchGroup]:
        items = [
            GroupItem(
                item_id=a.item.testItemId,
                exception_fp=a.signature.exception_fp,
                error_hash=a.signature.error_hash,
                emb=a.emb,
                has_stacktrace=a.signature.has_stacktrace,
                log_count=a.log_count,
                template_ids=tuple(
                    self.pipeline.template_id(h) for h in a.signature.template_hashes
                ),
            )
            for a in analyses
        ]
        return group_launch(
            items,
            burst_x=self.burst_si_share,
            is_error_hash_new=lambda h: not self.retrieval.error_hash_seen(project, h, launch_id),
        )

    def _decide(
        self,
        project: int,
        scope_q: scope.ScopeQuery,
        analyzer_mode: str,
        rep: ItemAnalysis,
        group: LaunchGroup,
        total_failures: int,
        *,
        route: str,
    ) -> DecisionResult:
        sig = rep.signature
        q = self._query_signature(
            rep, launch_id=rep.launch.launchId, launch_number=rep.launch.launchNumber
        )

        # Stage A — exact error_hash matches with label provenance, restricted to
        # the analyzerMode scope (§6.1: labeled items *in scope*). analyze applies
        # the hard filter; suggest keeps every labeled, non-ti match (base only).
        hash_matches: list[HashMatch] = []
        if sig.exception_fp != 0:
            for row in self.retrieval.find_hash_matches(project, sig.error_hash):
                if row["item_id"] == rep.item.testItemId:
                    continue
                sc = scope.ScopeCandidate(
                    launch_id=row["launch_id"],
                    launch_name=row["launch_name"] or "",
                    issue_type_group=row["issue_type_group"] or "",
                    is_labeled=True,
                )
                if route == "analyze":
                    if not scope.in_analyze_scope(analyzer_mode, scope_q, sc):
                        continue
                elif not scope.passes_base(sc):
                    continue
                hash_matches.append(
                    HashMatch(
                        item_id=row["item_id"],
                        issue_type=row["issue_type"],
                        issue_type_group=row["issue_type_group"] or "",
                        label_source=row["label_source"],
                        label_ts=row["label_ts"],
                        confidence=src_weight(row["label_source"]),
                        is_auto_analyzed=bool(row["is_auto_analyzed"]),
                    )
                )

        # Stage B — KB modes (exact scan) + seed prior.
        kb_candidates = list(self.kb.match_modes(project, q, k=10))  # type: ignore[attr-defined]
        seed = self._seed_signal(project, sig)

        # Stage C — hybrid item-history retrieval, scoped/boosted per analyzerMode.
        stage_c, ages = self._stage_c(
            project, q, scope_q, analyzer_mode, rep.item.testItemId, route
        )

        ctx = self._feature_ctx(rep, group, total_failures)
        # Serving hook (spec §6.5): the shipped GBM decides once Stage A / KB have
        # not short-circuited; when no model is live the predictor returns None per
        # vector and control falls through to the rule-based cold fallback.
        gbm_predict: Callable[[list[float]], GbmDecision | None] | None = None
        predictor = self.predictor
        if predictor is not None:
            gbm_predict = lambda vec: predictor.predict(vec, project)  # noqa: E731
        inputs = DecisionInputs(
            exception_fp=sig.exception_fp,
            hash_matches=hash_matches,
            kb_candidates=kb_candidates,
            seed=seed,
            stage_c=stage_c,
            stage_c_ages_days=ages,
            feature_ctx=ctx,
            gbm_predict=gbm_predict,
        )
        return decide(inputs, tau_auto=self.auto_min_prob)

    def _stage_c(
        self,
        project: int,
        q: QuerySignature,
        scope_q: scope.ScopeQuery,
        analyzer_mode: str,
        self_item_id: int,
        route: str,
    ) -> tuple[list[Candidate], list[float]]:
        # Retrieve WIDER than TOP_K, then apply the analyzerMode scope, then
        # truncate: a hard scope filter over exactly top-20 can be starved when the
        # top-20 is dominated by out-of-scope (e.g. same-launch) rows so the
        # in-scope history never surfaces (§6.0). Widening trades a little latency
        # for recall; the verbatim RRF fusion SQL is untouched (only the LIMIT
        # param widens). Final list is still capped at TOP_K for feature stability.
        cands = self.retrieval.find_candidates(
            project,
            q,
            k=STAGE_C_RETRIEVE_K,
            filters=CandidateFilters(exclude_item_ids=[self_item_id]),
        )
        items = [c for c in cands if c.item_id is not None]
        # Enrich with launch_name for name-based scope (single batched lookup).
        names = self.retrieval.item_launch_names(project, [c.item_id for c in items])  # type: ignore[arg-type]
        scored: list[tuple[float, Candidate]] = []
        for c in items:
            sc = scope.ScopeCandidate(
                launch_id=c.launch_id or 0,
                launch_name=names.get(c.item_id or 0, ""),
                issue_type_group="".join(x for x in (c.issue_type or "")[:2] if x.isalpha()),
                is_labeled=c.issue_type is not None,
            )
            if route == "analyze":
                if not scope.in_analyze_scope(analyzer_mode, scope_q, sc):
                    continue
                boost = scope.analyze_boost(analyzer_mode, scope_q, sc)
            else:
                # suggest never hard-filters, but ti/unlabeled must never leak into
                # suggestions/features (belt-and-suspenders over the Stage-B SQL).
                if not scope.passes_base(sc):
                    continue
                boost = scope.suggest_boost(analyzer_mode, scope_q, sc)
            scored.append((c.rrf_score * boost, c))
        # Deterministic re-rank by boosted rrf, tie-break by item_id desc, cap TOP_K.
        scored.sort(key=lambda pair: (pair[0], pair[1].item_id or 0), reverse=True)
        ordered = [c for _s, c in scored][:TOP_K]
        now = datetime.now(UTC)
        ages = [self._age_days(c.label_ts, now) for c in ordered]
        return ordered, ages

    def _seed_signal(self, project: int, sig) -> SeedSignal | None:
        if self.seed_kb is None:
            return None
        try:
            hit = self.seed_kb.match_and_seed(
                project,
                exc_classes=sig.exc_classes,
                msg_text=sig.msg_text,
                template_texts=(),
            )
        except Exception:  # noqa: BLE001 — seed persistence must never fail a decision
            logger.exception("seed match_and_seed failed for project %s", project)
            mode = self.seed_kb.match(exc_classes=sig.exc_classes, msg_text=sig.msg_text)
            return SeedSignal(mode.prior_label, mode.prior_confidence) if mode else None
        return SeedSignal(hit.label, hit.confidence) if hit is not None else None

    def _feature_ctx(
        self, rep: ItemAnalysis, group: LaunchGroup, total_failures: int
    ) -> FeatureContext:
        sig = rep.signature
        tch = rep.item.testCaseHash or None
        # spec 04 §4.2: fold the cached extractor categoricals in at feature time,
        # scoped to this project. Miss / LLM-off ⇒ the ``unknown`` sentinel, so the
        # feature vector is identical to a build without the sidecar.
        failing_layer, error_class = LLM_UNKNOWN, LLM_UNKNOWN
        if self.extractor_features is not None:
            tids = [self.pipeline.template_id(h) for h in sig.template_hashes]
            hit = self.extractor_features(rep.launch.project, sig.exception_fp, tids)
            if hit is not None:
                failing_layer, error_class = hit
        stats_row: dict = {}
        test_age_days: float | None = None
        if tch is not None:
            stats_row = self.stats.get_test_history(rep.launch.project, [tch]).get(tch, {})  # type: ignore[attr-defined]
            first_seen = self.retrieval.test_case_first_seen(rep.launch.project, tch)
            if first_seen is not None:
                test_age_days = self._age_days(first_seen, datetime.now(UTC))
        return FeatureContext(
            flakiness_score=stats_row.get("flakiness_score"),
            window_runs=stats_row.get("window_runs", 0) or 0,
            window_failures=stats_row.get("window_failures", 0) or 0,
            window_flips=stats_row.get("window_flips", 0) or 0,
            group_size=len(group.members),
            # We know the failing-item count; the launch's *total* item count is not
            # carried on the wire, so launch_fail_fraction stays 0 (unknown, §6.4 #31).
            launch_failures=total_failures,
            launch_items=0,
            si_prior=group.si_prior,
            test_age_days=test_age_days,
            item_log_count=rep.log_count,
            has_stacktrace=sig.has_stacktrace,
            is_assertion=sig.is_assertion,
            is_merged_small_logs=sig.is_merged_small_logs,
            exception_count=len(sig.exc_classes),
            llm_failing_layer=failing_layer,
            llm_error_class=error_class,
            time_decay_per_day=self.time_decay,
        )

    # ------------------------------------------------------------------ #
    # Wire rendering
    # ------------------------------------------------------------------ #
    def _write_suggestion(
        self,
        project: int,
        item_id: int,
        launch_id: int,
        group_id: int | None,
        decision: DecisionResult,
    ) -> None:
        self.retrieval.write_suggestion(
            SuggestionIn(
                project_id=project,
                item_id=item_id,
                launch_id=launch_id,
                group_id=group_id,
                predicted_label=decision.issue_type,
                confidence=decision.confidence,
                matched_mode_id=decision.matched_mode_id,
                matched_item_id=decision.relevant_item_id,
                features=decision.features,
                model_ver=self._model_ver(decision),
            )
        )

    # ------------------------------------------------------------------ #
    # LLM sidecar enqueue (spec 04 §1.5) — strictly async, best-effort
    # ------------------------------------------------------------------ #
    def _enqueue_llm(
        self,
        project: int,
        item_id: int,
        launch_id: int,
        decision: DecisionResult,
        candidates: list[dict] | None = None,
    ) -> None:
        """Enqueue the relevant LLM roles after the suggestion row is committed.

        A no-op when no sidecar is wired or the master switch is off (byte-identity,
        §0). Never raises into the decision path, never waits on the LLM (§0/§1.5).
        Per-role flags and the per-project kill-switch are enforced downstream.
        """
        sc = self.sidecar
        if sc is None or not getattr(sc, "enabled", False):
            return
        try:
            # Extractor runs regardless of band (feeds GBM features); the per-project
            # cache dedupes actual Ollama calls to once per novel template set (§4.2).
            sc.enqueue("extractor", project, item_id, {})  # type: ignore[attr-defined]
            if decision.label == "ti":
                # Abstain → cold-start rubric (fact_loader gates on a cold project).
                sc.enqueue("coldstart", project, item_id, {"launch_id": launch_id})  # type: ignore[attr-defined]
            elif decision.confidence >= TAU_SUGGEST:
                sc.enqueue("explainer", project, item_id, {})  # type: ignore[attr-defined]
                if (
                    TAU_SUGGEST <= decision.confidence < self.judge_tau
                    and candidates is not None
                    and len(candidates) >= 2
                ):
                    sc.enqueue(  # type: ignore[attr-defined]
                        "judge", project, item_id, {"candidates": candidates}
                    )
        except Exception:  # noqa: BLE001 — enrichment must never fail a decision
            logger.exception("LLM enqueue failed (project=%s item=%s)", project, item_id)

    @staticmethod
    def _judge_candidates(decision: DecisionResult) -> list[dict]:
        """Suggest-band candidate refs for the judge (§4.3): real item id + label +
        similarity. The worker loads each candidate's DB facts (exception chain, top
        templates, top frames) fresh from ``failure_signature`` — never empty stubs."""
        out: list[dict] = []
        for c in list(decision.stage_c)[:3]:
            if c.item_id is None or c.issue_type is None:
                continue
            out.append(
                {
                    "id": c.item_id,
                    "label": c.issue_type,
                    "similarity": round(min(1.0, c.cosine or 0.0), 3),
                }
            )
        return out

    def _render_suggestions(
        self,
        info: TestItemInfo,
        rep: ItemAnalysis,
        decision: DecisionResult,
        elapsed: float,
        judge_verdict: dict | None = None,
    ) -> list[SuggestAnalysisResult]:
        # Proxy max-prob for cold mode: the rule confidence, else top candidate cosine.
        stage_c = list(decision.stage_c)
        proxy = decision.confidence
        if not proxy and stage_c:
            proxy = min(1.0, stage_c[0].cosine or 0.0)
        if proxy < TAU_SUGGEST:
            return []

        candidates = self._suggestion_candidates(decision, stage_c)
        if not candidates:
            return []
        # §4.3: a fresh judge verdict promotes its chosen candidate — suggest-band only,
        # never for an auto-band decision (which the judge must never touch).
        candidates = self._reorder_for_judge(
            candidates, judge_verdict, is_auto=decision.action == ACTION_AUTO
        )

        names = ";".join(feature_names())
        values = ";".join(f"{v:.6f}" for v in to_vector(decision.features))
        log_id = info.logs[0].logId if info.logs else 0
        method = "auto_analysis" if decision.action == ACTION_AUTO else "suggestion"
        out: list[SuggestAnalysisResult] = []
        for rank, (issue_type, rel_item, score, es_score) in enumerate(
            candidates[: self.suggest_max]
        ):
            out.append(
                SuggestAnalysisResult(
                    project=info.project,
                    testItem=info.testItemId,
                    testItemLogId=log_id,
                    launchId=info.launchId,
                    launchName=info.launchName,
                    launchNumber=info.launchNumber,
                    issueType=issue_type,
                    relevantItem=rel_item,
                    relevantLogId=0,
                    isMergedLog=rep.signature.is_merged_small_logs,
                    matchScore=round(min(1.0, score) * 100, 2),
                    resultPosition=rank,
                    esScore=round(es_score, 6),
                    esPosition=rank,
                    modelFeatureNames=names,
                    modelFeatureValues=values,
                    modelInfo=self._model_info(decision),
                    usedLogLines=info.analyzerConfig.numberOfLogLines,
                    minShouldMatch=info.analyzerConfig.minShouldMatch,
                    processedTime=round(elapsed, 4),
                    methodName=method,
                    clusterId=info.clusterId,
                )
            )
        # The suggestion row is persisted by suggest() before rendering (§6.6), so
        # both the abstain and the non-abstain paths record exactly one row.
        return out

    @staticmethod
    def _reorder_for_judge(
        candidates: list[tuple[str, int, float, float]],
        judge_verdict: dict | None,
        *,
        is_auto: bool,
    ) -> list[tuple[str, int, float, float]]:
        """Promote the judge's chosen candidate to resultPosition 0 (§4.3).

        No-op for an auto-band decision (untouchable by the judge), for a ``none``/
        absent verdict, or when the chosen ``relevantItem`` is no longer among the
        current candidates. Only the *order* changes — never a label/score.
        """
        if is_auto or not judge_verdict:
            return candidates
        chosen = judge_verdict.get("chosen_item_id")
        if chosen is None:
            return candidates
        idx = next((i for i, c in enumerate(candidates) if c[1] == chosen), None)
        if idx is None or idx == 0:
            return candidates
        return [candidates[idx], *candidates[:idx], *candidates[idx + 1 :]]

    def _suggestion_candidates(
        self, decision: DecisionResult, stage_c: Sequence[Candidate]
    ) -> list[tuple[str, int, float, float]]:
        """(issueType, relevantItem, matchScore∈[0,1], esScore) tuples, best-first."""
        # A short-circuit (hash/kb) or auto decision surfaces its single answer first.
        result: list[tuple[str, int, float, float]] = []
        if decision.label != "ti" and decision.relevant_item_id is not None:
            result.append(
                (decision.issue_type, decision.relevant_item_id, decision.confidence, 0.0)
            )
        for c in stage_c:
            if c.item_id is None or c.issue_type is None:
                continue
            if any(c.item_id == rel for _l, rel, _s, _e in result):
                continue
            result.append((c.issue_type, c.item_id, min(1.0, c.cosine or 0.0), c.rrf_score))
        return result

    # ------------------------------------------------------------------ #
    # Signature / scope / id helpers
    # ------------------------------------------------------------------ #
    def _query_signature(
        self, rep: ItemAnalysis, *, launch_id: int, launch_number: int
    ) -> QuerySignature:
        sig = rep.signature
        template_ids = [self.pipeline.template_id(h) for h in sig.template_hashes]
        salient = (
            list(sig.exc_classes)
            + sig.msg_text.split()[:MSG_SALIENT_TERMS]
            + [f"HTTP_{c}" for c in sig.status_codes]
        )
        return QuerySignature(
            exception_fp=sig.exception_fp,
            error_hash=sig.error_hash,
            top_frames=list(sig.frames),
            template_ids=template_ids,
            salient_terms=salient,
            exception_names=list(sig.exc_classes),
            emb=rep.emb,
            emb_model_ver=rep.emb_model_ver,
            test_case_hash=rep.item.testCaseHash or None,
            launch_id=launch_id,
            launch_number=launch_number,
        )

    @staticmethod
    def _scope_query(launch: Launch) -> scope.ScopeQuery:
        return scope.ScopeQuery(
            project_id=launch.project,
            launch_id=launch.launchId,
            launch_name=launch.launchName,
            previous_launch_id=launch.previousLaunchId,
        )

    @staticmethod
    def _scope_query_info(info: TestItemInfo) -> scope.ScopeQuery:
        return scope.ScopeQuery(
            project_id=info.project,
            launch_id=info.launchId,
            launch_name=info.launchName,
            previous_launch_id=info.previousLaunchId,
        )

    @staticmethod
    def _pseudo_launch(info: TestItemInfo) -> Launch:
        item = TestItem(
            testItemId=info.testItemId,
            isAutoAnalyzed=False,
            testCaseHash=info.testCaseHash,
            testItemName=info.testItemName,
            uniqueId=info.uniqueId,
            logs=info.logs,
        )
        return Launch(
            launchId=info.launchId,
            project=info.project,
            launchName=info.launchName,
            launchNumber=info.launchNumber,
            previousLaunchId=info.previousLaunchId,
            analyzerConfig=info.analyzerConfig,
            testItems=[item],
        )

    @staticmethod
    def _singleton_group(rep: ItemAnalysis) -> LaunchGroup:
        gi = GroupItem(
            item_id=rep.item.testItemId,
            exception_fp=rep.signature.exception_fp,
            error_hash=rep.signature.error_hash,
            emb=rep.emb,
            has_stacktrace=rep.signature.has_stacktrace,
            log_count=rep.log_count,
        )
        return LaunchGroup(members=[gi], centroid=None, template_ids=set(), representative=gi)

    @staticmethod
    def _cluster_id(project: int, launch_id: int, error_hash: int, for_update: bool) -> int:
        # for_update=True → per-launch id; False → cross-launch reuse (same error_hash).
        key = f"{project}:{launch_id}:{error_hash}" if for_update else f"{project}:{error_hash}"
        return xxh3_64_unsigned(key) & INT53_MASK

    @staticmethod
    def _cluster_message(rep: ItemAnalysis) -> str:
        lines = [ln for ln in rep.clean_msg.split("\n") if ln.strip()]
        return "\n".join(lines[:CLUSTER_MSG_LINES])

    @staticmethod
    def _age_days(ts: datetime | None, now: datetime) -> float:
        if ts is None:
            return 3650.0  # unknown recency → effectively no boost
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return max(0.0, (now - ts).total_seconds() / 86400.0)

    def _model_ver(self, decision: DecisionResult) -> str:
        # Stamp the shipped GBM version on GBM-method decisions; rule paths keep the
        # cold tag (spec §6.5: every suggestion records the model it came from). The
        # version is carried on the decision (from the GbmPrediction) — no re-fetch.
        if decision.method == METHOD_GBM:
            gbm = decision.model_version or "gbm"
            return f"{gbm};fs={FEATURE_SCHEMA_VER};emb={self.emb_model_tag}"
        return f"rule_cold;fs={FEATURE_SCHEMA_VER};emb={self.emb_model_tag}"

    def _model_info(self, decision: DecisionResult) -> str:
        mode = decision.matched_mode_id if decision.matched_mode_id is not None else "none"
        gbm = decision.model_version if decision.method == METHOD_GBM else None
        return f"analyzer-ng;gbm={gbm or 'none'};emb={self.emb_model_tag};kb_mode={mode}"
