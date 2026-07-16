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
from collections.abc import Sequence
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
    TAU_SUGGEST,
    DecisionInputs,
    DecisionResult,
    HashMatch,
    decide,
)
from analyzer_ng.core.features import (
    FEATURE_SCHEMA_VER,
    FeatureContext,
    SeedSignal,
    feature_names,
    src_weight,
    to_vector,
)
from analyzer_ng.core.grouping import GroupItem, LaunchGroup, group_launch
from analyzer_ng.core.ingest import IndexPipeline, ItemAnalysis
from analyzer_ng.db.repositories.models import (
    Candidate,
    CandidateFilters,
    QuerySignature,
    SuggestionIn,
)
from analyzer_ng.db.repositories.retrieval import PgRetrievalStore
from analyzer_ng.ml.hashing import xxh3_64_unsigned
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
        elapsed = time.monotonic() - started
        return self._render_suggestions(info, rep, decision, elapsed)

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
        inputs = DecisionInputs(
            exception_fp=sig.exception_fp,
            hash_matches=hash_matches,
            kb_candidates=kb_candidates,
            seed=seed,
            stage_c=stage_c,
            stage_c_ages_days=ages,
            feature_ctx=ctx,
        )
        return decide(inputs)

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
                model_ver=self._model_ver(),
            )
        )

    def _render_suggestions(
        self, info: TestItemInfo, rep: ItemAnalysis, decision: DecisionResult, elapsed: float
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

        names = ";".join(feature_names())
        values = ";".join(f"{v:.6f}" for v in to_vector(decision.features))
        log_id = info.logs[0].logId if info.logs else 0
        method = "auto_analysis" if decision.action == ACTION_AUTO else "suggestion"
        out: list[SuggestAnalysisResult] = []
        for rank, (issue_type, rel_item, score, es_score) in enumerate(candidates[:SUGGEST_MAX]):
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
        # Persist the decision (every decision writes a suggestion row, §6.6).
        self._write_suggestion(info.project, info.testItemId, info.launchId, None, decision)
        return out

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

    def _model_ver(self) -> str:
        return f"rule_cold;fs={FEATURE_SCHEMA_VER};emb={self.emb_model_tag}"

    def _model_info(self, decision: DecisionResult) -> str:
        mode = decision.matched_mode_id if decision.matched_mode_id is not None else "none"
        return f"analyzer-ng;gbm=none;emb={self.emb_model_tag};kb_mode={mode}"
