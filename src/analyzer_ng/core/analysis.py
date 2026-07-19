"""Analysis engine — analyze / suggest / cluster / search routes (spec 03 §5-§8).

Ties the pure analytical modules (:mod:`grouping`, :mod:`features`,
:mod:`decision`, :mod:`scope`) to the spec-02 stores and renders the legacy wire
shapes (spec 01 §4.2). The GBM is absent (T3.1); the rule-based cold fallback
(:func:`decision.decide`) is the decision function, but the full 39-feature vector
is snapshotted into ``suggestion.features`` on every decision.

Pipeline per launch: build signatures/embeddings (read-only) → launch grouping
(§5) → per-representative matching A/B/C (§6.1-§6.3) → decision + policy bands
(§6.6) → fan out to members, persist ``launch_group`` + ``suggestion`` rows.

Identity invariant (§6.1): hash-identity comparisons only ever compare values
computed the same way. The read path mines against a *read-only Drain3 clone*
(:meth:`IndexPipeline.build_item_analyses` never saves), and Drain templates drift
as new logs are mined, so a read-time recompute of the query item's ``error_hash``
diverges from the value each history row was indexed under — causing false Stage-A
matches (a drifted hash colliding with an unrelated row → wrong inherited label)
*and* false non-matches. So the decision path resolves the query item's CANONICAL
identity — its persisted ``failure_signature`` row (:meth:`_resolve_identity`) —
and every hash comparison (Stage A, KB ``exception_fps``, burst novelty, launch
fingerprint) stays stored-vs-stored. A never-indexed item recomputes then persists.
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
    KB_CANDIDATE_SCORE,
    METHOD_GBM,
    METHOD_KB,
    TAU_AUTO,
    TAU_SUGGEST,
    DecisionInputs,
    DecisionResult,
    GbmDecision,
    HashMatch,
    best_kb_match,
    decide,
    score_kb_candidate,
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
    SignatureIn,
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


def _provenance(label_source: str | None, is_auto_analyzed: bool) -> str:
    """Human-readable label provenance for a suggest candidate's modelInfo.

    Real data only: derived from the matched item's label_event source and its
    is_auto_analyzed flag — 'human-confirmed' (rp/human triage), 'auto-analyzed'
    (the analyzer's own label, incl. ai_suggested), 'seed' (shipped catalog),
    'unlabeled' when no source is recorded."""
    if is_auto_analyzed or label_source == "ai_suggested":
        return "auto-analyzed"
    if label_source in ("rp", "human"):
        return "human-confirmed"
    if label_source == "seed":
        return "seed"
    return "unlabeled"


@dataclass(frozen=True)
class ModeMatch:
    """The failure_mode an item was matched to, for membership persistence (§6.7/§9).

    ``matched_by`` is one of the ``mode_membership`` CHECK values: a seed rule hit is
    ``'lexical'`` (matched on its regex/keyword rules, spec §9), a KB centroid/fp
    match carries the candidate's own ``matched_by`` (``'hash'``/``'vector'``).
    """

    mode_id: int
    score: float
    matched_by: str  # 'hash' | 'vector' | 'lexical' | 'human'


@dataclass(frozen=True)
class _QueryIdentity:
    """The canonical hash-identity of the item under analysis (spec §3.2-§3.3, §6.1).

    Every hash-identity comparison on the read path must compare values computed
    the same way. ``error_hash`` folds the ordered Drain3 template ids, which drift
    as new logs re-cluster the miner; the analyze/suggest path mines against a
    *read-only Drain clone* (never ``save_manager``) whose templates have moved on
    from the state each history row was indexed under. So a recompute of the query
    item's ``error_hash`` at analyze time can silently collide with an unrelated
    history row (false match → wrong inherited label) or fail to match its own past
    self (false non-match). This struct carries the *persisted* identity — the value
    written at index time — so Stage A, KB ``exception_fps`` matching, the burst
    novelty check and the launch-group fingerprint stay stored-vs-stored. It is
    recomputed (and then persisted) only for an item that was never indexed.
    """

    exception_fp: int
    error_hash: int
    template_ids: tuple[int, ...]
    top_frames: tuple[str, ...]
    status_codes: tuple[str, ...]
    msg_text: str
    exception_names: tuple[str, ...]


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
        scope_q = self._scope_query(launch)
        analyzer_mode = launch.analyzerConfig.analyzerMode
        # §6.1 identity invariant on the fan-out: the canonical (stored/recompute) FULL
        # discriminant per item — error_hash AND status_codes AND masked message tokens —
        # so the representative's decision only fans to members whose discriminant is
        # truly identical to it.
        canon_disc = self._canonical_discriminants(project, analyses)

        out: list[AnalysisResult] = []
        for group in groups:
            rep = by_id[group.representative.item_id]
            decision, mode_match = self._decide(
                project, scope_q, analyzer_mode, rep, group, total_failures, route="analyze"
            )
            group_id = self.retrieval.upsert_launch_group(
                project,
                launch.launchId,
                group.fingerprint,
                len(group.members),
                group.si_prior,
                dominant=group.si_prior > 0.0,
            )
            # A launch group is bucketed by exception_fp (§5), so distinct failures of one
            # exception class share a group; §3.3 further collapses failures that raise the
            # identical exception through the identical stack into one error_hash even when
            # their near-error CONTEXT differs (ADV-1: SLOW QUERY→pb vs pool-exhausted→si vs
            # bare→abstain). Fanning the representative's high-confidence decision — a Stage-A
            # inherit OR a GBM/KB auto-label + its feature snapshot — to members whose
            # discriminant differs is the near-miss trap that lands them confidently-wrong.
            # So the group decision is fanned ONLY to members whose FULL canonical
            # discriminant (error_hash AND status_codes set AND masked message tokens) is
            # identical to the representative's; every other member is decided on ITS OWN
            # identity (its own Stage A / KB / GBM, its own discriminant features). Deciding a
            # truly-identical member individually would yield the same result, so this only
            # ever corrects — never regresses — the fan-out (§6.1).
            rep_disc = canon_disc.get(rep.item.testItemId)
            inherit_ids: list[int] = []
            for member in group.members:
                m_decision = decision
                m_mode = mode_match
                if canon_disc.get(member.item_id) != rep_disc:
                    m_ana = by_id[member.item_id]
                    m_decision, m_mode = self._decide(
                        project, scope_q, analyzer_mode, m_ana, group, total_failures,
                        route="analyze",
                    )
                    # §6.7/§9: this member joins the mode ITS own decision matched.
                    self._record_membership(project, [member.item_id], m_mode, m_ana)
                else:
                    inherit_ids.append(member.item_id)
                self._write_suggestion(
                    project, member.item_id, launch.launchId, group_id, m_decision
                )
                # §1.5: enqueue async LLM enrichment after the row is committed.
                self._enqueue_llm(project, member.item_id, launch.launchId, m_decision)
                if m_decision.action == ACTION_AUTO and m_decision.label != "ti":
                    self.retrieval.update_issue_type(
                        project, member.item_id, m_decision.issue_type, is_auto=True
                    )
                    out.append(
                        AnalysisResult(
                            testItem=member.item_id,
                            issueType=m_decision.issue_type,
                            relevantItem=m_decision.relevant_item_id or 0,
                        )
                    )
            # §6.7/§9: record the members that kept the group decision as members of the
            # matched mode so the KB-mode loop can bootstrap — purity/support/centroid
            # then move as those items are labeled (defect_update → update_purity).
            self._record_membership(project, inherit_ids, mode_match, rep)
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
        decision, mode_match = self._decide(
            project,
            self._scope_query_info(info),
            info.analyzerConfig.analyzerMode,
            rep,
            group,
            total_failures=1,
            route="suggest",
        )
        # §6.7/§9: link this item to the matched mode so the KB-mode loop can learn.
        self._record_membership(info.project, [info.testItemId], mode_match, rep)
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
        # §6.1 identity invariant: the representative's ``error_hash`` feeds the burst
        # novelty check (``error_hash_seen``, stored history) and the persisted
        # launch-group fingerprint — both stored-vs-stored comparisons. Use each
        # item's canonical (index-time) ``error_hash`` when it exists so a drifted
        # read-time recompute cannot fake/miss novelty or churn the fingerprint.
        # ``exception_fp`` and ``template_ids`` (used only for *within-batch* cohesion
        # bucketing / Jaccard) stay recomputed — they don't fold Drain templates
        # (fp) or are compared only against their same-batch peers (templates).
        canonical = self.retrieval.get_signatures(project, [a.item.testItemId for a in analyses])
        items = [
            GroupItem(
                item_id=a.item.testItemId,
                exception_fp=a.signature.exception_fp,
                error_hash=(
                    stored.error_hash
                    if (stored := canonical.get(a.item.testItemId)) is not None
                    else a.signature.error_hash
                ),
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

    def _canonical_discriminants(
        self, project: int, analyses: Sequence[ItemAnalysis]
    ) -> dict[int, tuple[int, frozenset[str], frozenset[str]]]:
        """Canonical FULL discriminant per item for the fan-out identity split (§6.1).

        Returns ``(error_hash, status_codes set, masked-message-token set)`` — the same
        un-masked discriminants the Stage-A inherit gate compares (decision.py), so the
        fan-out only inherits a member that is truly indistinguishable from the
        representative. ``error_hash`` alone is insufficient: §3.3 collapses the
        near-error CONTEXT into a shared hash, so the message tokens (which now carry that
        folded context, ADV-1) are what separate a SLOW-QUERY-pb member from a
        pool-exhausted-si member that share the identical exception+stack.

        Prefers each item's persisted ``failure_signature`` value (the stored-vs-stored
        invariant, §6.1); a never-indexed item uses its read-time recompute — the same
        value :meth:`_resolve_identity` would make canonical when that member is decided.
        Batched in one query so the split adds no per-member DB round-trips.
        """
        stored = self.retrieval.get_signatures(
            project, [a.item.testItemId for a in analyses]
        )
        out: dict[int, tuple[int, frozenset[str], frozenset[str]]] = {}
        for a in analyses:
            s = stored.get(a.item.testItemId)
            if s is not None:
                out[a.item.testItemId] = (
                    s.error_hash,
                    frozenset(s.status_codes),
                    frozenset(s.msg_text.split()),
                )
            else:
                sig = a.signature
                out[a.item.testItemId] = (
                    sig.error_hash,
                    frozenset(sig.status_codes),
                    frozenset(sig.msg_text.split()),
                )
        return out

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
    ) -> tuple[DecisionResult, ModeMatch | None]:
        # §6.1 identity invariant: resolve the query item's CANONICAL identity (its
        # persisted failure_signature row) rather than the drifted read-time recompute
        # in ``rep.signature``. This keeps Stage A (below), the KB ``exception_fps``
        # GIN / template Jaccard match (``kb.match_modes`` on ``q``) and the Stage-C
        # ``same_error_hash`` feature all stored-vs-stored. A never-indexed item falls
        # back to the recompute AND persists it, so the value is canonical next time.
        ident = self._resolve_identity(project, rep)
        q = self._query_from_identity(
            ident, rep, launch_id=rep.launch.launchId, launch_number=rep.launch.launchNumber
        )

        # Stage A — exact error_hash matches with label provenance, restricted to
        # the analyzerMode scope (§6.1: labeled items *in scope*). analyze applies
        # the hard filter; suggest keeps every labeled, non-ti match (base only).
        hash_matches: list[HashMatch] = []
        if ident.exception_fp != 0:
            for row in self.retrieval.find_hash_matches(project, ident.error_hash):
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
                        exception_fp=row["exception_fp"] or 0,
                        status_codes=tuple(row["status_codes"] or ()),
                        msg_tokens=frozenset((row["msg_text"] or "").split()),
                    )
                )

        # Stage B — KB modes (exact scan) + seed prior. Seed rules match on the
        # exception classes and the Drain-*masked* message text (static mask regexes,
        # not the mined cluster set), so they do not drift with template re-clustering
        # — no stored-vs-recomputed mismatch class; the read-time recompute is fine.
        kb_candidates = list(self.kb.match_modes(project, q, k=10))  # type: ignore[attr-defined]
        seed, seed_mode_id = self._seed_signal(project, rep.signature)

        # Stage C — hybrid item-history retrieval, scoped/boosted per analyzerMode.
        stage_c, ages = self._stage_c(
            project, q, scope_q, analyzer_mode, rep.item.testItemId, route
        )

        ctx = self._feature_ctx(rep, group, total_failures)
        # Serving hook (spec §6.5): the shipped GBM decides once Stage A / KB have
        # not short-circuited; when no model is live the predictor returns None per
        # snapshot and control falls through to the rule-based cold fallback. The hook
        # receives the name→value snapshot so serving assembles the vector from the
        # model's own stored feature list (schema-robust, 2026-07-18 errata).
        gbm_predict: Callable[[dict[str, float]], GbmDecision | None] | None = None
        predictor = self.predictor
        if predictor is not None:
            gbm_predict = lambda feats: predictor.predict(feats, project)  # noqa: E731
        inputs = DecisionInputs(
            exception_fp=ident.exception_fp,
            hash_matches=hash_matches,
            query_status_codes=ident.status_codes,
            query_msg_tokens=frozenset(ident.msg_text.split()),
            kb_candidates=kb_candidates,
            seed=seed,
            stage_c=stage_c,
            stage_c_ages_days=ages,
            feature_ctx=ctx,
            gbm_predict=gbm_predict,
        )
        decision = decide(inputs, tau_auto=self.auto_min_prob)
        mode_match = self._resolve_mode_match(decision, kb_candidates, seed_mode_id, seed)
        return decision, mode_match

    def _resolve_mode_match(
        self,
        decision: DecisionResult,
        kb_candidates: Sequence[Candidate],
        seed_mode_id: int | None,
        seed: SeedSignal | None,
    ) -> ModeMatch | None:
        """The failure_mode this item should join so the KB-mode loop can learn (§6.7).

        Precedence mirrors the decision stages: a KB short-circuit mode (already on
        the decision) wins; else a seed rule hit (its lazily-materialized per-project
        copy); else the best KB candidate scoring ≥ 0.70 (§6.2 candidate match). The
        chosen mode is stamped onto ``decision.matched_mode_id`` so the suggestion row
        records it (the exercise saw this stay NULL) and membership is written for it.
        """
        # KB short-circuit already picked a confirmed mode.
        if decision.matched_mode_id is not None:
            for c in kb_candidates:
                if c.mode_id == decision.matched_mode_id:
                    return ModeMatch(
                        c.mode_id, score_kb_candidate(c).score_mode, c.matched_by or "vector"
                    )
            return ModeMatch(decision.matched_mode_id, 1.0, "vector")
        # Seed rule hit — the authoritative match for a lazily-copied seed mode (§9).
        if seed_mode_id is not None:
            decision.matched_mode_id = seed_mode_id
            return ModeMatch(seed_mode_id, seed.confidence if seed else 1.0, "lexical")
        # Best KB candidate above the §6.2 candidate threshold (features-only match,
        # but strong enough to grow the mode's membership).
        best = best_kb_match(kb_candidates)
        if best is not None:
            kbm, cand = best
            if cand.mode_id is not None and kbm.score_mode >= KB_CANDIDATE_SCORE:
                decision.matched_mode_id = cand.mode_id
                return ModeMatch(cand.mode_id, kbm.score_mode, cand.matched_by or "vector")
        return None

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

    def _seed_signal(self, project: int, sig) -> tuple[SeedSignal | None, int | None]:
        """(feature signal, per-project seed mode_id). The mode_id is set only when
        the lazy per-project copy was materialized (``match_and_seed``), so the
        engine can record the item as a member of that mode (§6.7/§9). A degraded
        pure-``match`` fallback carries the signal but no mode_id (nothing persisted)."""
        if self.seed_kb is None:
            return None, None
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
            return (SeedSignal(mode.prior_label, mode.prior_confidence) if mode else None), None
        if hit is None:
            return None, None
        return SeedSignal(hit.label, hit.confidence), hit.mode_id

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

    def _record_membership(
        self,
        project: int,
        item_ids: Sequence[int],
        mode_match: ModeMatch | None,
        rep: ItemAnalysis,
    ) -> None:
        """Persist ``mode_membership`` rows for a mode-matched decision (§6.7/§9).

        This is the link the KB-mode learning loop was missing: without it
        ``matched_mode_id`` stays NULL and ``update_purity`` (called from
        ``defect_update``) has no members, so purity/support/centroid never move.
        A no-op when no mode matched. Best-effort: a membership write must never fail
        an analyze/suggest decision (the row is already committed). The mode's
        ``emb_model_ver`` is stamped from the representative's real embedding so the
        EWMA centroid can later be computed from member vectors (§9)."""
        if mode_match is None or not item_ids:
            return
        # Only stamp a version when the representative carried a real vector — a
        # lexical-only degrade (emb=None) must not pin the mode to the emb=0 sentinel.
        emb_ver = rep.emb_model_ver if rep.emb is not None else None
        members = [
            (int(iid), float(mode_match.score), mode_match.matched_by) for iid in item_ids
        ]
        try:
            self.kb.add_members(project, mode_match.mode_id, members, emb_ver)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — membership persistence must never fail a decision
            logger.exception(
                "mode membership write failed (project=%s mode=%s)", project, mode_match.mode_id
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
        for rank, (issue_type, rel_item, score, es_score, provenance) in enumerate(
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
                    modelInfo=f"{self._model_info(decision)};src={provenance}",
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
        candidates: list[tuple[str, int, float, float, str]],
        judge_verdict: dict | None,
        *,
        is_auto: bool,
    ) -> list[tuple[str, int, float, float, str]]:
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
    ) -> list[tuple[str, int, float, float, str]]:
        """(issueType, relevantItem, matchScore∈[0,1], esScore, provenance) tuples,
        best-first. ``provenance`` names where the candidate's label came from
        (human-confirmed / auto-analyzed / seed / kb-mode / unlabeled) so the UI can
        tell a human-vouched answer from a machine-inherited one."""
        # A short-circuit (hash/kb) or auto decision surfaces its single answer first.
        result: list[tuple[str, int, float, float, str]] = []
        if decision.label != "ti" and decision.relevant_item_id is not None:
            prov = (
                "kb-mode"
                if decision.method == METHOD_KB
                else _provenance(
                    decision.relevant_label_source, decision.relevant_is_auto_analyzed
                )
            )
            result.append(
                (
                    decision.issue_type,
                    decision.relevant_item_id,
                    decision.confidence,
                    0.0,
                    prov,
                )
            )
        for c in stage_c:
            if c.item_id is None or c.issue_type is None:
                continue
            if any(c.item_id == rel for _l, rel, _s, _e, _p in result):
                continue
            result.append(
                (
                    c.issue_type,
                    c.item_id,
                    min(1.0, c.cosine or 0.0),
                    c.rrf_score,
                    _provenance(c.label_source, False),
                )
            )
        return result

    # ------------------------------------------------------------------ #
    # Signature / scope / id helpers
    # ------------------------------------------------------------------ #
    def _resolve_identity(self, project: int, rep: ItemAnalysis) -> _QueryIdentity:
        """The canonical hash-identity of ``rep`` for the read path (spec §6.1).

        Index precedes analyze, so the item almost always has a persisted
        ``failure_signature`` row: use ITS ``error_hash`` / ``exception_fp`` /
        ``template_ids`` / ``top_frames`` / ``status_codes`` / ``msg_text`` — the
        values written at index time under that index's Drain3 state — as the query
        identity, never a recompute against the drifted read-only Drain clone. A
        never-indexed item falls back to the recompute in ``rep.signature`` and, so
        the value becomes canonical for any later comparison, PERSISTS it (upsert).
        """
        stored = self.retrieval.get_signatures(project, [rep.item.testItemId]).get(
            rep.item.testItemId
        )
        if stored is not None:
            return _QueryIdentity(
                exception_fp=stored.exception_fp,
                error_hash=stored.error_hash,
                template_ids=tuple(stored.template_ids),
                top_frames=tuple(stored.top_frames),
                status_codes=tuple(stored.status_codes),
                msg_text=stored.msg_text,
                # exc_text is the space-joined normalized chain (class names carry no
                # internal whitespace), so split() recovers the exception name list.
                exception_names=tuple(stored.exc_text.split()),
            )
        self._persist_recomputed_signature(project, rep)
        sig = rep.signature
        return _QueryIdentity(
            exception_fp=sig.exception_fp,
            error_hash=sig.error_hash,
            template_ids=tuple(self.pipeline.template_id(h) for h in sig.template_hashes),
            top_frames=tuple(sig.frames),
            status_codes=tuple(sig.status_codes),
            msg_text=sig.msg_text,
            exception_names=tuple(sig.exc_classes),
        )

    def _persist_recomputed_signature(self, project: int, rep: ItemAnalysis) -> None:
        """Upsert a never-indexed item's recomputed signature (spec §6.1 fallback).

        Makes the just-computed identity canonical so a later analyze of the same
        item compares stored-vs-stored. Best-effort — a persist failure must never
        fail the decision (the read path can proceed on the in-memory recompute).
        ``tmpl_text`` (FTS-only, not part of any hash identity) is left empty here;
        the next real ``index`` of the item fills it from the live miner patterns.
        """
        sig = rep.signature
        if not sig.signature_text:
            return  # empty signature (no ERROR logs) → nothing indexable (§3.4)
        try:
            self.retrieval.upsert_signatures(
                [
                    SignatureIn(
                        project_id=project,
                        item_id=rep.item.testItemId,
                        exception_fp=sig.exception_fp,
                        error_hash=sig.error_hash,
                        top_frames=list(sig.frames),
                        template_ids=[self.pipeline.template_id(h) for h in sig.template_hashes],
                        exc_text=" ".join(sig.exc_classes),
                        msg_text=sig.msg_text,
                        frames_text=" ".join(sig.frames),
                        tmpl_text="",
                        status_codes=list(sig.status_codes),
                        emb=rep.emb,
                        emb_model_ver=rep.emb_model_ver,
                    )
                ]
            )
        except Exception:  # noqa: BLE001 — fallback persist must never fail a decision
            logger.exception(
                "fallback signature persist failed (project=%s item=%s)",
                project,
                rep.item.testItemId,
            )

    def _query_from_identity(
        self, ident: _QueryIdentity, rep: ItemAnalysis, *, launch_id: int, launch_number: int
    ) -> QuerySignature:
        """Build the retrieval :class:`QuerySignature` from a canonical identity.

        The hash/lexical identity fields come from ``ident`` (stored-when-indexed);
        only the dense vector (``emb``) is the live read-time embedding — vector
        similarity is a separate, non-identity signal and the current embedder tag
        must match the DB's stored vectors' ``emb_model_ver``.
        """
        salient = (
            list(ident.exception_names)
            + ident.msg_text.split()[:MSG_SALIENT_TERMS]
            + [f"HTTP_{c}" for c in ident.status_codes]
        )
        return QuerySignature(
            exception_fp=ident.exception_fp,
            error_hash=ident.error_hash,
            top_frames=list(ident.top_frames),
            template_ids=list(ident.template_ids),
            salient_terms=salient,
            exception_names=list(ident.exception_names),
            emb=rep.emb,
            emb_model_ver=rep.emb_model_ver,
            test_case_hash=rep.item.testCaseHash or None,
            launch_id=launch_id,
            launch_number=launch_number,
        )

    def _query_signature(
        self, rep: ItemAnalysis, *, launch_id: int, launch_number: int
    ) -> QuerySignature:
        """Recompute a QuerySignature from the in-memory analysis (``search`` route).

        Used only by :meth:`search` (§8.2), whose query is an ad-hoc set of log
        messages with a synthetic item id that is typically not indexed — there is
        no persisted identity to prefer, and search has no hash-inherit stage (it
        ranks purely on cosine/FTS), so a read-time recompute is correct here. The
        analyze/suggest decision path instead resolves the item's *stored* identity
        via :meth:`_resolve_identity` (spec §6.1 identity invariant).
        """
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
