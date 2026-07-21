"""Query builders that assemble the JSON payloads each view consumes.

All queries are read-only, parameterized and LIMIT-bounded, and every one is
scoped by ``project_id`` (tenancy). 64-bit identifiers (error_hash,
exception_fp, template_ids, item_id) are stringified in the output so the
browser's JSON parser never loses precision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import features_meta
from .db import Database
from .pca import parse_halfvec, pca_project
from .reconstruct import reconstruct_stage_b

if TYPE_CHECKING:
    from .rp_names import RPNameResolver


def _rp_block(rp: RPNameResolver | None, project_id: int) -> dict[str, Any]:
    """Per-project ReportPortal name block (real names/colors + honest status).

    When no resolver is configured the block still carries an honest status note
    and empty maps, so the UI degrades to raw locators without special-casing.
    """
    if rp is not None:
        return rp.block(project_id)
    return {
        "project_id": project_id,
        "project_name": None,
        "status": {"configured": False, "reachable": False, "note": "RP names: not configured"},
        "defects": {},
    }

# Label class palette keys (frontend maps to colors). pb/ab/si/nd + ti/other.
LABEL_GROUPS = ("pb", "ab", "si", "nd", "ti")

# Decision policy thresholds (spec §6.6). Defined once here with a comment rather
# than magic numbers scattered in the view; the payload always carries them so a
# config change can never desync the takeaway sentence from the gauge.
TAU_SUGGEST = 0.45
TAU_AUTO = 0.75

# Learning-loop maturity thresholds. Spec constants, RE-DECLARED here (never
# imported from src/analyzer_ng) so the read-only inspector keeps zero dependency
# on the analyzer package. Provenance — keep in sync by hand if the spec moves:
#   GBM_MIN_EVENTS   = 50  → src/analyzer_ng/ml/trainer.py     (cold-model floor)
#   CALIB_MIN_EVENTS = 300 → src/analyzer_ng/ml/calibration.py (per-project isotonic)
# Band metric is the project's count of DISTINCT LABELED ITEMS (one training
# example per item — the training-frame proxy after the ti/unlabeled drop, §6.4/
# §6.5), NOT the raw label_event churn (an item re-triaged N times is one example,
# so raw events run several× higher). The payload carries the thresholds so the
# view's stepper can never desync from the boundary a retrain actually enforces.
GBM_MIN_EVENTS = 50
CALIB_MIN_EVENTS = 300

# label_event.source values that count as HUMAN triage (mirrors util.js
# SOURCE_LABELS): RP defect edits, Inspector UI edits, and accepted suggestions.
# 'ai_suggested'/'seed' are machine provenance and excluded from "human-sourced".
_HUMAN_SOURCES = (
    "rp",
    "human",
    "rp_defect_update",
    "human_ui",
    "analyzer_suggestion_accepted",
)

# ``method`` and ``abstain_reason`` are produced by DecisionResult
# (src/analyzer_ng/core/decision.py) but are not part of the original suggestion
# DDL; they are persisted once the analyzer migration adds the columns. Detect
# them at runtime so the Inspector surfaces the stored values verbatim when they
# exist and degrades to a deterministic derivation otherwise. Memoized per
# process (schema is stable within a deployed image).
# Cap for the grouping member dot-strip (mirrors the signatures member cap).
_GROUP_MEMBER_CAP = 60

_SUGGESTION_OPT_COLS: set[str] | None = None


def _suggestion_opt_cols(db: Database) -> set[str]:
    global _SUGGESTION_OPT_COLS
    if _SUGGESTION_OPT_COLS is None:
        rows = db.rows(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'analyzer' AND table_name = 'suggestion'
              AND column_name = ANY(%s)
            """,
            (["method", "abstain_reason"],),
        )
        _SUGGESTION_OPT_COLS = {r["column_name"] for r in rows}
    return _SUGGESTION_OPT_COLS


def _grp(label: str | None) -> str:
    if not label:
        return "none"
    for g in LABEL_GROUPS:
        if label.startswith(g):
            return g
    return "other"


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def _slist(v: Any) -> list[str]:
    return [str(x) for x in (v or [])]


def _ui_url(url: str | None, key: str = "ui_url") -> dict[str, str]:
    """Spread-in a real RP UI link field, or nothing when there is no link.

    Keeps the no-dummy-data rule: absent RP resolution → the field is simply
    omitted and the frontend renders exactly today's plain-text id.
    """
    return {key: url} if url else {}


# --------------------------------------------------------------------------- #
# Pickers
# --------------------------------------------------------------------------- #
def list_projects(
    db: Database, limit: int, rp: RPNameResolver | None = None
) -> list[dict[str, Any]]:
    rows = db.rows(
        """
        SELECT p.project_id,
               COALESCE(ti.n_items, 0)   AS item_count,
               COALESCE(ti.n_launches,0) AS launch_count
        FROM analyzer.project p
        LEFT JOIN LATERAL (
            SELECT count(*) AS n_items, count(DISTINCT launch_id) AS n_launches
            FROM analyzer.test_item t WHERE t.project_id = p.project_id
        ) ti ON true
        ORDER BY p.project_id
        LIMIT %s
        """,
        (limit,),
    )
    return [
        {
            "project_id": r["project_id"],
            # Real RP name when available; None (never a fabricated "Project N")
            # otherwise — the frontend renders an honest numeric fallback.
            "project_name": rp.project_name(r["project_id"]) if rp is not None else None,
            "item_count": r["item_count"],
            "launch_count": r["launch_count"],
        }
        for r in rows
    ]


def list_launches(
    db: Database, project_id: int, limit: int, rp: RPNameResolver | None = None
) -> list[dict[str, Any]]:
    rows = db.rows(
        """
        SELECT launch_id,
               max(launch_name)     AS launch_name,
               max(launch_number)   AS launch_number,
               count(*)             AS item_count,
               count(*) FILTER (WHERE issue_type IS NOT NULL AND issue_type_group <> 'ti')
                                    AS labeled_count,
               max(start_time)      AS last_start
        FROM analyzer.test_item
        WHERE project_id = %s
        GROUP BY launch_id
        ORDER BY max(start_time) DESC NULLS LAST, launch_id DESC
        LIMIT %s
        """,
        (project_id, limit),
    )
    return [
        {
            "launch_id": r["launch_id"],
            "launch_name": r["launch_name"],
            "launch_number": r["launch_number"],
            "item_count": r["item_count"],
            "labeled_count": r["labeled_count"],
            "last_start": _iso(r["last_start"]),
            # Real RP UI deep link when resolvable; omitted otherwise (plain text).
            **_ui_url(rp.launch_link(project_id, r["launch_id"]) if rp else None),
        }
        for r in rows
    ]


def list_items(
    db: Database, project_id: int, launch_id: int, limit: int, rp: RPNameResolver | None = None
) -> list[dict[str, Any]]:
    rows = db.rows(
        """
        SELECT ti.item_id, ti.item_name, ti.issue_type, ti.issue_type_group,
               ti.is_auto_analyzed, ti.log_count,
               fs.exc_text,
               (fs.emb IS NOT NULL) AS has_emb
        FROM analyzer.test_item ti
        LEFT JOIN analyzer.failure_signature fs USING (project_id, item_id)
        WHERE ti.project_id = %s AND ti.launch_id = %s
        ORDER BY ti.item_id
        LIMIT %s
        """,
        (project_id, launch_id, limit),
    )
    links = rp.item_links(project_id, [r["item_id"] for r in rows]) if rp else {}
    return [
        {
            "item_id": r["item_id"],
            "item_name": r["item_name"],
            "issue_type": r["issue_type"],
            "label_group": _grp(r["issue_type"]),
            "is_auto_analyzed": r["is_auto_analyzed"],
            "log_count": r["log_count"],
            "exc_text": r["exc_text"],
            "has_emb": r["has_emb"],
            **_ui_url(links.get(r["item_id"])),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Item Journey (aggregated)
# --------------------------------------------------------------------------- #
def item_journey(
    db: Database, project_id: int, item_id: int, rp: RPNameResolver | None = None
) -> dict[str, Any] | None:
    item = db.one(
        """
        SELECT item_id, launch_id, launch_name, launch_number, test_case_hash,
               unique_id, item_name, start_time, is_auto_analyzed, issue_type,
               issue_type_group, log_count
        FROM analyzer.test_item WHERE project_id = %s AND item_id = %s
        """,
        (project_id, item_id),
    )
    if item is None:
        return None

    sig = db.one(
        """
        SELECT item_id, exception_fp, error_hash, top_frames, template_ids,
               exc_text, msg_text, frames_text, tmpl_text, signature_text,
               only_numbers, status_codes, urls, paths,
               emb_model_ver, (emb IS NOT NULL) AS has_emb,
               CASE WHEN emb IS NOT NULL THEN emb::text ELSE NULL END AS emb_text
        FROM analyzer.failure_signature WHERE project_id = %s AND item_id = %s
        """,
        (project_id, item_id),
    )

    # ---- Signature stage + referenced drain templates ----
    signature_block: dict[str, Any] | None = None
    templates_block: list[dict[str, Any]] = []
    reconstruction: dict[str, Any] = {"reconstructed": False, "candidates": [], "note": ""}
    if sig is not None:
        template_ids = sig["template_ids"] or []
        signature_block = {
            "exception_fp": _s(sig["exception_fp"]),
            "error_hash": _s(sig["error_hash"]),
            "top_frames": sig["top_frames"] or [],
            "template_ids": _slist(template_ids),
            "exc_text": sig["exc_text"],
            "msg_text": sig["msg_text"],
            "frames_text": sig["frames_text"],
            "tmpl_text": sig["tmpl_text"],
            "signature_text": sig["signature_text"],
            "only_numbers": sig["only_numbers"],
            "status_codes": sig["status_codes"] or [],
            "urls": sig["urls"] or [],
            "paths": sig["paths"] or [],
            "emb_model_ver": sig["emb_model_ver"],
            "has_emb": sig["has_emb"],
        }
        if template_ids:
            trows = db.rows(
                """
                SELECT template_id, pattern, token_count, example, match_count,
                       first_seen, last_seen
                FROM analyzer.log_template
                WHERE project_id = %s AND template_id = ANY(%s)
                """,
                (project_id, template_ids),
            )
            by_id = {r["template_id"]: r for r in trows}
            for tid in template_ids:
                r = by_id.get(tid)
                if r:
                    templates_block.append(_template_row(r))
                else:
                    templates_block.append(
                        {"template_id": str(tid), "pattern": None, "missing": True}
                    )

        # ---- Live reconstruction of Stage-B hybrid retrieval ----
        sig_for_recon = dict(sig)
        sig_for_recon["emb_vec"] = parse_halfvec(sig["emb_text"])
        reconstruction = reconstruct_stage_b(db, project_id, sig_for_recon)

    # ---- Grouping stage ----
    # Prefer the group whose fingerprint == this item's error_hash; else fall
    # back to the largest group in the launch.
    error_hash = sig["error_hash"] if sig else None
    if error_hash is not None:
        grouping = db.one(
            """
            SELECT group_id, fingerprint, member_count, dominant, si_prior, created_at
            FROM analyzer.launch_group
            WHERE project_id = %s AND launch_id = %s
            ORDER BY (fingerprint = %s) DESC, member_count DESC
            LIMIT 1
            """,
            (project_id, item["launch_id"], error_hash),
        )
    else:
        grouping = db.one(
            """
            SELECT group_id, fingerprint, member_count, dominant, si_prior, created_at
            FROM analyzer.launch_group
            WHERE project_id = %s AND launch_id = %s
            ORDER BY member_count DESC
            LIMIT 1
            """,
            (project_id, item["launch_id"]),
        )
    grouping_block = None
    group_members: list[dict[str, Any]] = []
    if grouping:
        # Total analyzed failing items in this launch — makes the burst share /
        # dominance concrete (same count the launches list reports as item_count).
        lfc = db.one(
            "SELECT count(*) AS n FROM analyzer.test_item "
            "WHERE project_id = %s AND launch_id = %s",
            (project_id, item["launch_id"]),
        )
        launch_failed_count = lfc["n"] if lfc else 0

        # Group members. Groups are built by SIGNATURE SIMILARITY (θ-chains inside an
        # exception_fp bucket), not by identical hashes — so exact-hash equality
        # UNDERCOUNTS real-world groups (identical twins only). The authoritative
        # persisted membership is suggestion.group_id: the analyze fan-out writes one
        # suggestion row per member. Union it with hash-equality as the fallback for
        # groups whose members never got suggestion rows (partial TO_INVESTIGATE
        # analyzes, suggest-route groups). Self is forced ahead of the cap so the
        # "this item" dot is never dropped; capped at 60 (mirrors the signatures
        # member cap). May still be short of member_count when neither source covers
        # a member — the frontend key line shows "N of M" honestly.
        member_rows = db.rows(
            """
            SELECT item_id, item_name, issue_type, issue_type_group, is_auto_analyzed
            FROM (
                SELECT DISTINCT ti.item_id, ti.item_name, ti.issue_type,
                       ti.issue_type_group, ti.is_auto_analyzed
                FROM analyzer.suggestion s
                JOIN analyzer.test_item ti
                  ON ti.project_id = s.project_id AND ti.item_id = s.item_id
                WHERE s.project_id = %s AND s.launch_id = %s AND s.group_id = %s
                UNION
                SELECT DISTINCT ti.item_id, ti.item_name, ti.issue_type,
                       ti.issue_type_group, ti.is_auto_analyzed
                FROM analyzer.failure_signature fs
                JOIN analyzer.test_item ti USING (project_id, item_id)
                WHERE fs.project_id = %s AND ti.launch_id = %s AND fs.error_hash = %s
            ) m
            ORDER BY (item_id = %s) DESC, item_id
            LIMIT %s
            """,
            (
                project_id, item["launch_id"], grouping["group_id"],
                project_id, item["launch_id"], grouping["fingerprint"],
                item_id, _GROUP_MEMBER_CAP,
            ),
        )
        for m in member_rows:
            group_members.append(
                {
                    "item_id": m["item_id"],
                    "item_name": m["item_name"],
                    "issue_type": m["issue_type"],
                    "label_group": _grp(m["issue_type"]),
                    "is_auto_analyzed": m["is_auto_analyzed"],
                    "is_self": m["item_id"] == item_id,
                }
            )

        grouping_block = {
            "group_id": grouping["group_id"],
            "fingerprint": _s(grouping["fingerprint"]),
            "member_count": grouping["member_count"],
            "dominant": grouping["dominant"],
            "si_prior": float(grouping["si_prior"]),
            "created_at": _iso(grouping["created_at"]),
            "launch_failed_count": launch_failed_count,
            "members": group_members,
            "member_shown": len(group_members),
        }

    # ---- Matching + decision (latest suggestion for this item) ----
    # method / abstain_reason appended only when the columns exist (see
    # _suggestion_opt_cols); _matching_decision derives method otherwise.
    opt_cols = _suggestion_opt_cols(db)
    extra_cols = "".join(f", {c}" for c in ("method", "abstain_reason") if c in opt_cols)
    sug = db.one(
        f"""
        SELECT suggestion_id, group_id, predicted_label, confidence, matched_mode_id,
               matched_item_id, features, model_ver, llm_used, explanation, outcome,
               outcome_ts, created_at{extra_cols}
        FROM analyzer.suggestion
        WHERE project_id = %s AND item_id = %s
        ORDER BY created_at DESC, suggestion_id DESC
        LIMIT 1
        """,
        (project_id, item_id),
    )
    matching_block, decision_block = _matching_decision(db, project_id, sug)

    # Honesty split for LLM cold-start rows: a rubric row (model_ver 'rubric+…') is a
    # PROVISIONAL ai_suggested hint — never surfaced in RP's Make Decision. When it is
    # the latest row, also expose the latest CLASSICAL decision (if any) so the UI can
    # tell both stories instead of dressing the rubric up as a normal suggest.
    if decision_block is not None and str(sug.get("model_ver") or "").startswith("rubric+"):
        decision_block["coldstart_provisional"] = True
        classical = db.one(
            """
            SELECT predicted_label, confidence, model_ver, created_at
            FROM analyzer.suggestion
            WHERE project_id = %s AND item_id = %s AND model_ver NOT LIKE 'rubric+%%'
            ORDER BY created_at DESC, suggestion_id DESC
            LIMIT 1
            """,
            (project_id, item_id),
        )
        if classical:
            c_conf = float(classical["confidence"] or 0.0)
            decision_block["classical"] = {
                "predicted_label": classical["predicted_label"],
                "predicted_group": _grp(classical["predicted_label"]),
                "confidence": c_conf,
                "band": (
                    "auto" if c_conf >= TAU_AUTO
                    else "suggest" if c_conf >= TAU_SUGGEST
                    else "abstain"
                ),
                "model_ver": classical["model_ver"],
            }
        else:
            decision_block["classical"] = None

    # ---- RP UI deep links (batched): the item, a matched item, candidates,
    #      grouping members ----
    if rp is not None:
        want: list[Any] = [item_id]
        if matching_block.get("matched_item_id"):
            want.append(matching_block["matched_item_id"])
        want.extend(c["item_id"] for c in reconstruction.get("candidates", []))
        want.extend(m["item_id"] for m in group_members)
        links = rp.item_links(project_id, want)
        for c in reconstruction.get("candidates", []):
            url = links.get(c["item_id"])
            if url:
                c["ui_url"] = url
        for m in group_members:  # same dict objects held by grouping_block["members"]
            url = links.get(m["item_id"])
            if url:
                m["ui_url"] = url
        mi = matching_block.get("matched_item_id")
        if mi is not None:
            murl = links.get(int(mi)) if str(mi).lstrip("-").isdigit() else None
            if murl:
                matching_block["matched_item_url"] = murl
        item_url = links.get(int(item_id))
        launch_url = rp.launch_link(project_id, item["launch_id"])
    else:
        item_url = None
        launch_url = None

    # ---- Feedback (label events) ----
    events = db.rows(
        """
        SELECT event_id, old_label, new_label, source, suggestion_id, ts
        FROM analyzer.label_event
        WHERE project_id = %s AND item_id = %s
        ORDER BY ts DESC
        LIMIT 50
        """,
        (project_id, item_id),
    )
    feedback_block = [
        {
            "event_id": e["event_id"],
            "old_label": e["old_label"],
            "new_label": e["new_label"],
            "old_group": _grp(e["old_label"]),
            "new_group": _grp(e["new_label"]),
            "source": e["source"],
            "suggestion_id": e["suggestion_id"],
            "ts": _iso(e["ts"]),
        }
        for e in events
    ]

    return {
        "item": {
            "item_id": item["item_id"],
            "launch_id": item["launch_id"],
            "launch_name": item["launch_name"],
            "launch_number": item["launch_number"],
            "test_case_hash": _s(item["test_case_hash"]),
            "unique_id": item["unique_id"],
            "item_name": item["item_name"],
            "start_time": _iso(item["start_time"]),
            "is_auto_analyzed": item["is_auto_analyzed"],
            "issue_type": item["issue_type"],
            "label_group": _grp(item["issue_type"]),
            "log_count": item["log_count"],
            **_ui_url(item_url),
            **_ui_url(launch_url, "launch_url"),
        },
        "signature": signature_block,
        "templates": templates_block,
        "grouping": grouping_block,
        "matching": matching_block,
        "reconstruction": reconstruction,
        "decision": decision_block,
        "feedback": feedback_block,
        "llm": _item_llm(db, project_id, item_id),
        "rp": _rp_block(rp, project_id),
    }


# --------------------------------------------------------------------------- #
# LLM sidecar observability (read-only; spec 04 tables). All graceful when the
# llm_* tables are empty — absence of events is itself a signal, never faked.
# --------------------------------------------------------------------------- #
_LLM_ROLES = ("coldstart", "explainer", "judge", "extractor")


def _item_llm(db: Database, project_id: int, item_id: int) -> dict[str, Any]:
    """Per-item LLM involvement: this item's llm_event rows (newest 20) + the
    project's llm_role_state. `output` passes through verbatim (jsonb → dict);
    it is NULL unless outcome='ok', by engine design."""
    events = db.rows(
        """
        SELECT event_id, role, model, prompt_hash, cache_hit, outcome, output,
               latency_ms, created_at
        FROM analyzer.llm_event
        WHERE project_id = %s AND item_id = %s
        ORDER BY created_at DESC, event_id DESC
        LIMIT 20
        """,
        (project_id, item_id),
    )
    role_state = db.rows(
        "SELECT role, enabled, reason, decided_at FROM analyzer.llm_role_state "
        "WHERE project_id = %s",
        (project_id,),
    )
    return {
        "events": [
            {
                "event_id": e["event_id"],
                "role": e["role"],
                "model": e["model"],
                "prompt_hash": e["prompt_hash"],
                "cache_hit": e["cache_hit"],
                "outcome": e["outcome"],
                "output": e["output"],
                "latency_ms": e["latency_ms"],
                "created_at": _iso(e["created_at"]),
            }
            for e in events
        ],
        "role_state": [
            {
                "role": r["role"],
                "enabled": r["enabled"],
                "reason": r["reason"],
                "decided_at": _iso(r["decided_at"]),
            }
            for r in role_state
        ],
    }


def llm_summary(db: Database, project_id: int) -> dict[str, Any]:
    """Per-role counts by outcome, last-event time, latency (p50/p90/max over
    non-cache-hit ok calls), and the kill-switch state (absence = default-on)."""
    mix = db.rows(
        "SELECT role, outcome, count(*) AS n, count(*) FILTER (WHERE cache_hit) AS from_cache "
        "FROM analyzer.llm_event WHERE project_id = %s GROUP BY role, outcome",
        (project_id,),
    )
    lat = db.rows(
        """
        SELECT role,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY latency_ms) AS p90,
               max(latency_ms) AS mx, count(*) AS n
        FROM analyzer.llm_event
        WHERE project_id = %s AND NOT cache_hit AND latency_ms IS NOT NULL AND outcome = 'ok'
        GROUP BY role
        """,
        (project_id,),
    )
    last = db.rows(
        "SELECT role, max(created_at) AS last_event FROM analyzer.llm_event "
        "WHERE project_id = %s GROUP BY role",
        (project_id,),
    )
    states = db.rows(
        "SELECT role, enabled, reason, stats, decided_at FROM analyzer.llm_role_state "
        "WHERE project_id = %s",
        (project_id,),
    )
    lat_by = {r["role"]: r for r in lat}
    last_by = {r["role"]: r for r in last}
    state_by = {r["role"]: r for r in states}
    mix_by: dict[str, dict[str, int]] = {}
    cache_by: dict[str, int] = {}
    for m in mix:
        mix_by.setdefault(m["role"], {})[m["outcome"]] = m["n"]
        cache_by[m["role"]] = cache_by.get(m["role"], 0) + (m["from_cache"] or 0)

    roles_out = []
    for role in _LLM_ROLES:
        outcomes = mix_by.get(role, {})
        lr = lat_by.get(role)
        st = state_by.get(role)
        roles_out.append(
            {
                "role": role,
                "outcomes": outcomes,
                "event_count": sum(outcomes.values()),
                "ok": outcomes.get("ok", 0),
                "from_cache": cache_by.get(role, 0),
                "last_event": (
                    _iso(last_by.get(role, {}).get("last_event")) if role in last_by else None
                ),
                "latency": (
                    {
                        "p50": _num(lr["p50"]),
                        "p90": _num(lr["p90"]),
                        "max": _num(lr["mx"]),
                        "n": lr["n"],
                    }
                    if lr
                    else None
                ),
                "state": (
                    {
                        "enabled": st["enabled"],
                        "reason": st["reason"],
                        "stats": st["stats"],
                        "decided_at": _iso(st["decided_at"]),
                    }
                    if st
                    else None  # absence = default-on
                ),
            }
        )

    model_row = db.one(
        "SELECT model FROM analyzer.llm_event WHERE project_id = %s "
        "ORDER BY created_at DESC, event_id DESC LIMIT 1",
        (project_id,),
    )
    return {
        "roles": roles_out,
        "event_total": sum(r["event_count"] for r in roles_out),
        "model": model_row["model"] if model_row else None,
    }


def llm_events(
    db: Database,
    project_id: int,
    role: str | None,
    outcome: str | None,
    limit: int,
    rp: RPNameResolver | None = None,
) -> dict[str, Any]:
    """Activity stream, newest-first, capped. `outcome` is a GROUP filter:
    ok | guardrail (schema_fail/validation_fail) | unavailable (timeout/
    breaker_open/dropped)."""
    where = ["e.project_id = %s"]
    params: list[Any] = [project_id]
    if role and role != "all":
        where.append("e.role = %s")
        params.append(role)
    if outcome == "ok":
        where.append("e.outcome = 'ok'")
    elif outcome == "guardrail":
        where.append("e.outcome IN ('schema_fail','validation_fail')")
    elif outcome == "unavailable":
        where.append("e.outcome IN ('timeout','breaker_open','dropped')")
    rows = db.rows(
        f"""
        SELECT e.event_id, e.item_id, e.role, e.model, e.cache_hit, e.outcome,
               e.output, e.latency_ms, e.created_at, ti.item_name, ti.launch_id
        FROM analyzer.llm_event e
        LEFT JOIN analyzer.test_item ti
               ON ti.project_id = e.project_id AND ti.item_id = e.item_id
        WHERE {" AND ".join(where)}
        ORDER BY e.created_at DESC, e.event_id DESC
        LIMIT %s
        """,
        (*params, limit),
    )
    links = rp.item_links(project_id, [r["item_id"] for r in rows]) if rp else {}
    return {
        "events": [
            {
                "event_id": r["event_id"],
                "item_id": r["item_id"],
                "item_name": r["item_name"],
                "launch_id": r["launch_id"],
                "role": r["role"],
                "model": r["model"],
                "cache_hit": r["cache_hit"],
                "outcome": r["outcome"],
                "output": r["output"],
                "latency_ms": r["latency_ms"],
                "created_at": _iso(r["created_at"]),
                **_ui_url(links.get(r["item_id"])),
            }
            for r in rows
        ],
        "count": len(rows),
        "limit": limit,
    }


def llm_cache(
    db: Database, project_id: int, role: str, limit: int
) -> dict[str, Any]:
    """Cache rows for a role (default extractor), ordered by reuse. Includes the
    honest 'hits undercounts feature-time reads' via the caption on the client."""
    rows = db.rows(
        """
        SELECT template_hash, cache_key, model, hits, output, created_at, last_hit_at,
               (created_at >= now() - make_interval(days =>
                   CASE %s WHEN 'judge' THEN 14 ELSE 90 END)) AS fresh
        FROM analyzer.llm_cache
        WHERE project_id = %s AND role = %s
        ORDER BY hits DESC, last_hit_at DESC NULLS LAST
        LIMIT %s
        """,
        (role, project_id, role, limit),
    )
    summ = db.one(
        "SELECT count(*) AS entries, COALESCE(sum(hits), 0) AS hits "
        "FROM analyzer.llm_cache WHERE project_id = %s AND role = %s",
        (project_id, role),
    )
    return {
        "role": role,
        "entries": summ["entries"] if summ else 0,
        "total_hits": summ["hits"] if summ else 0,
        "rows": [
            {
                "template_hash": _s(r["template_hash"]),
                "cache_key": r["cache_key"],
                "model": r["model"],
                "hits": r["hits"],
                "output": r["output"],
                "created_at": _iso(r["created_at"]),
                "last_hit_at": _iso(r["last_hit_at"]),
                "fresh": r["fresh"],
            }
            for r in rows
        ],
    }


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _matching_decision(
    db: Database, project_id: int, sug: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if sug is None:
        matching = {
            "has_suggestion": False,
            "stage": "none",
            "stage_label": "No suggestion recorded",
            "explanation": "No suggestion row exists for this item yet — the matcher "
            "has not run, or the item was never a failure.",
        }
        return matching, None

    predicted = sug["predicted_label"]
    matched_item = sug["matched_item_id"]
    matched_mode = sug["matched_mode_id"]
    abstained = predicted == "ti" or (sug["confidence"] or 0) < 0.45

    if matched_item is not None:
        stage, stage_label = "A", "Stage A — exact hash inherit"
        stage_note = (
            f"Item inherited its label from a prior item ({matched_item}) with the "
            "same error_hash (Stage A short-circuit)."
        )
    elif matched_mode is not None:
        stage, stage_label = "AB", "Stage A/B — KB mode match"
        stage_note = f"Matched knowledge-base failure mode #{matched_mode}."
    elif abstained:
        stage, stage_label = "abstain", "Abstained"
        stage_note = "No stage produced a confident match; item left as 'ti'."
    else:
        stage, stage_label = "C", "Stage C — hybrid retrieval + GBM"
        stage_note = "Decision came from Stage-C hybrid retrieval scored by the GBM."

    mode_info = None
    if matched_mode is not None:
        mode_info = db.one(
            """
            SELECT mode_id, status, label, title, purity, support, seed_key
            FROM analyzer.failure_mode WHERE project_id = %s AND mode_id = %s
            """,
            (project_id, matched_mode),
        )

    matching = {
        "has_suggestion": True,
        "suggestion_id": sug["suggestion_id"],
        "stage": stage,
        "stage_label": stage_label,
        "stage_note": stage_note,
        "matched_item_id": _s(matched_item),
        "matched_mode_id": _s(matched_mode),
        "matched_mode": (
            {
                "mode_id": mode_info["mode_id"],
                "status": mode_info["status"],
                "label": mode_info["label"],
                "label_group": _grp(mode_info["label"]),
                "title": mode_info["title"],
                "purity": float(mode_info["purity"]),
                "support": mode_info["support"],
                "seed_key": mode_info["seed_key"],
            }
            if mode_info
            else None
        ),
    }

    # Decision block — feature vector + gauge.
    features = sug["features"] or {}
    feat_rows = []
    for key, val in features.items():
        try:
            fv = float(val)
        except (TypeError, ValueError):
            continue
        meta = features_meta.describe(key)
        feat_rows.append({**meta, "value": fv})
    feat_rows.sort(key=lambda r: abs(r["value"]), reverse=True)

    confidence = float(sug["confidence"] or 0.0)
    if confidence >= TAU_AUTO:
        band = "auto"
    elif confidence >= TAU_SUGGEST:
        band = "suggest"
    else:
        band = "abstain"

    # Deciding mechanism. Stored verbatim when the suggestion row carries it;
    # otherwise derived deterministically from the stage + model_ver (lens §4.1) —
    # an honest derivation of real fields, never an invented value.
    method = sug.get("method")
    if not method:
        if stage == "A":
            method = "hash"
        elif stage == "AB":
            method = "kb"
        elif str(sug["model_ver"] or "").startswith("rubric+"):
            # LLM cold-start rubric row — provisional ai_suggested, not a served path.
            method = "coldstart"
        elif (sug["model_ver"] or "").split(";")[0] == "rule_cold":
            method = "rule_cold"
        else:
            method = "gbm"

    decision = {
        "predicted_label": predicted,
        "predicted_group": _grp(predicted),
        "confidence": confidence,
        "band": band,
        "tau_suggest": TAU_SUGGEST,
        "tau_auto": TAU_AUTO,
        "model_ver": sug["model_ver"],
        # Non-numeric LLM annotations riding suggestion.features (dropped from the
        # numeric feature vector above): coldstart rubric + judge verdict, verbatim.
        "coldstart": features.get("coldstart") if isinstance(features, dict) else None,
        "judge": features.get("judge") if isinstance(features, dict) else None,
        # Verbatim when stored; derived (see above) so the field is always present.
        "method": method,
        # Verbatim when the suggestion row stores it; None otherwise (the frontend
        # omits the reason clause rather than guessing).
        "abstain_reason": sug.get("abstain_reason"),
        "llm_used": sug["llm_used"],
        "explanation": sug["explanation"],
        "outcome": sug["outcome"],
        "outcome_ts": _iso(sug["outcome_ts"]),
        "created_at": _iso(sug["created_at"]),
        "features": feat_rows,
        "feature_count": len(feat_rows),
        # Full schema width, derived from the real feature table (not a magic
        # number in the UI) so "N of M" stays honest if the schema grows.
        "feature_total": len(features_meta.FEATURE_DEFS),
    }
    return matching, decision


def _template_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_id": str(r["template_id"]),
        "pattern": r["pattern"],
        "token_count": r["token_count"],
        "example": r.get("example"),
        "match_count": r["match_count"],
        "first_seen": _iso(r.get("first_seen")),
        "last_seen": _iso(r.get("last_seen")),
    }


# --------------------------------------------------------------------------- #
# Drain3 Explorer
# --------------------------------------------------------------------------- #
def templates(db: Database, project_id: int, q: str | None, limit: int) -> dict[str, Any]:
    if q:
        rows = db.rows(
            """
            SELECT template_id, pattern, token_count, example, match_count,
                   first_seen, last_seen
            FROM analyzer.log_template
            WHERE project_id = %s AND pattern ILIKE %s
            ORDER BY match_count DESC, template_id
            LIMIT %s
            """,
            (project_id, f"%{q}%", limit),
        )
    else:
        rows = db.rows(
            """
            SELECT template_id, pattern, token_count, example, match_count,
                   first_seen, last_seen
            FROM analyzer.log_template
            WHERE project_id = %s
            ORDER BY match_count DESC, template_id
            LIMIT %s
            """,
            (project_id, limit),
        )
    items = [_template_row(r) for r in rows]
    return {"templates": items, "count": len(items)}


# --------------------------------------------------------------------------- #
# Modes Map 3D (PCA of embeddings)
# --------------------------------------------------------------------------- #
def modes3d(
    db: Database, project_id: int, limit: int, rp: RPNameResolver | None = None
) -> dict[str, Any]:
    sig_rows = db.rows(
        """
        SELECT fs.item_id, ti.issue_type, ti.item_name, ti.is_auto_analyzed,
               fs.emb::text AS emb_text, fs.emb_model_ver
        FROM analyzer.failure_signature fs
        JOIN analyzer.test_item ti USING (project_id, item_id)
        WHERE fs.project_id = %s AND fs.emb IS NOT NULL
        LIMIT %s
        """,
        (project_id, limit),
    )
    mode_rows = db.rows(
        """
        SELECT mode_id, status, label, title, purity, support,
               centroid::text AS centroid_text, emb_model_ver
        FROM analyzer.failure_mode
        WHERE project_id = %s AND centroid IS NOT NULL
        LIMIT %s
        """,
        (project_id, limit),
    )

    # Count how many signatures exist at all (for honest empty-state messaging).
    total_sig = db.one(
        "SELECT count(*) AS n, count(*) FILTER (WHERE emb IS NOT NULL) AS n_emb "
        "FROM analyzer.failure_signature WHERE project_id = %s",
        (project_id,),
    )

    item_links = rp.item_links(project_id, [r["item_id"] for r in sig_rows]) if rp else {}
    points: list[dict[str, Any]] = []
    vectors: list[list[float]] = []
    for r in sig_rows:
        vec = parse_halfvec(r["emb_text"])
        if vec is None:
            continue
        vectors.append(vec)
        points.append(
            {
                "kind": "item",
                "item_id": r["item_id"],
                "label": r["issue_type"],
                "label_group": _grp(r["issue_type"]),
                "name": r["item_name"],
                "is_auto_analyzed": r["is_auto_analyzed"],
                **_ui_url(item_links.get(r["item_id"])),
            }
        )
    n_items = len(vectors)
    for r in mode_rows:
        vec = parse_halfvec(r["centroid_text"])
        if vec is None:
            continue
        vectors.append(vec)
        points.append(
            {
                "kind": "mode",
                "mode_id": r["mode_id"],
                "label": r["label"],
                "label_group": _grp(r["label"]),
                "name": r["title"] or f"mode {r['mode_id']}",
                "status": r["status"],
                "purity": float(r["purity"]),
                "support": r["support"],
            }
        )

    if len(vectors) < 2:
        return {
            "available": False,
            "reason": (
                "Fewer than 2 embedded points. The embedding stage "
                "(failure_signature.emb, halfvec(384)) has not populated vectors "
                "for this project yet — items are stored with emb_model_ver=0. "
                "The 3D map appears once embeddings backfill."
            ),
            "diagnostics": {
                "total_signatures": total_sig["n"] if total_sig else 0,
                "embedded_signatures": total_sig["n_emb"] if total_sig else 0,
                "modes_with_centroid": len(mode_rows),
            },
            "rp": _rp_block(rp, project_id),
        }

    import numpy as np

    result = pca_project(np.array(vectors), n_components=3)
    coords = result.coords
    dim = coords.shape[1]
    for i, p in enumerate(points):
        p["x"] = float(coords[i, 0]) if dim > 0 else 0.0
        p["y"] = float(coords[i, 1]) if dim > 1 else 0.0
        p["z"] = float(coords[i, 2]) if dim > 2 else 0.0

    return {
        "available": True,
        "dimensions": dim,
        "explained_variance_ratio": result.explained_variance_ratio,
        "n_items": n_items,
        "n_modes": len(points) - n_items,
        "points": points,
        "rp": _rp_block(rp, project_id),
    }


# --------------------------------------------------------------------------- #
# Launch Groups (force graph)
# --------------------------------------------------------------------------- #
def groups(
    db: Database,
    project_id: int,
    launch_id: int | None,
    limit: int,
    rp: RPNameResolver | None = None,
) -> dict[str, Any]:
    group_rows = db.rows(
        """
        SELECT group_id, launch_id, fingerprint, member_count, dominant, si_prior
        FROM analyzer.launch_group
        WHERE project_id = %s AND (%s::bigint IS NULL OR launch_id = %s::bigint)
        ORDER BY launch_id DESC, member_count DESC
        LIMIT %s
        """,
        (project_id, launch_id, launch_id, limit),
    )
    groups_out = [
        {
            "group_id": g["group_id"],
            "launch_id": g["launch_id"],
            "fingerprint": _s(g["fingerprint"]),
            "member_count": g["member_count"],
            "dominant": g["dominant"],
            "si_prior": float(g["si_prior"]),
            **_ui_url(rp.launch_link(project_id, g["launch_id"]) if rp else None, "launch_url"),
        }
        for g in group_rows
    ]

    # Items in the target launch(es), tagged with the group whose fingerprint
    # matches the item's error_hash.
    item_rows = db.rows(
        """
        SELECT ti.item_id, ti.item_name, ti.launch_id, ti.issue_type,
               ti.is_auto_analyzed, fs.error_hash
        FROM analyzer.test_item ti
        LEFT JOIN analyzer.failure_signature fs USING (project_id, item_id)
        WHERE ti.project_id = %s AND (%s::bigint IS NULL OR ti.launch_id = %s::bigint)
        ORDER BY ti.item_id
        LIMIT %s
        """,
        (project_id, launch_id, launch_id, limit),
    )
    fp_to_group = {(g["launch_id"], str(g["fingerprint"])): g["group_id"] for g in group_rows}
    links = rp.item_links(project_id, [it["item_id"] for it in item_rows]) if rp else {}
    nodes = []
    for it in item_rows:
        eh = _s(it["error_hash"])
        gid = fp_to_group.get((it["launch_id"], eh)) if eh else None
        nodes.append(
            {
                "item_id": it["item_id"],
                "name": it["item_name"],
                "launch_id": it["launch_id"],
                "issue_type": it["issue_type"],
                "label_group": _grp(it["issue_type"]),
                "is_auto_analyzed": it["is_auto_analyzed"],
                "group_id": gid,
                "error_hash": eh,
                **_ui_url(links.get(it["item_id"])),
            }
        )
    return {"groups": groups_out, "nodes": nodes, "rp": _rp_block(rp, project_id)}


# --------------------------------------------------------------------------- #
# Learning Loop timeline
# --------------------------------------------------------------------------- #
def timeline(
    db: Database, project_id: int, limit: int, rp: RPNameResolver | None = None
) -> dict[str, Any]:
    events = db.rows(
        """
        SELECT event_id, item_id, old_label, new_label, source, ts
        FROM analyzer.label_event
        WHERE project_id = %s
        ORDER BY ts
        LIMIT %s
        """,
        (project_id, limit),
    )
    ev_links = rp.item_links(project_id, [e["item_id"] for e in events]) if rp else {}
    label_events = [
        {
            "event_id": e["event_id"],
            "item_id": e["item_id"],
            "old_label": e["old_label"],
            "new_label": e["new_label"],
            "old_group": _grp(e["old_label"]),
            "new_group": _grp(e["new_label"]),
            "source": e["source"],
            "ts": _iso(e["ts"]),
            **_ui_url(ev_links.get(e["item_id"])),
        }
        for e in events
    ]

    artifacts = db.rows(
        """
        SELECT model_id, kind, project_id, version, feature_schema_ver, n_events,
               metrics, is_active, trained_at
        FROM analyzer.model_artifact
        WHERE project_id = %s OR project_id IS NULL
        ORDER BY trained_at
        LIMIT %s
        """,
        (project_id, limit),
    )
    model_artifacts = [
        {
            "model_id": a["model_id"],
            "kind": a["kind"],
            "scope": "install-wide" if a["project_id"] is None else f"project {a['project_id']}",
            "version": a["version"],
            "feature_schema_ver": a["feature_schema_ver"],
            "n_events": a["n_events"],
            "metrics": a["metrics"] or {},
            "is_active": a["is_active"],
            "trained_at": _iso(a["trained_at"]),
        }
        for a in artifacts
    ]

    metrics = db.rows(
        """
        SELECT day, suggestions, accepted, corrected, ignored, abstained,
               auto_labeled, auto_corrected, model_ver, emb_model_ver, per_label
        FROM analyzer.metrics_daily
        WHERE project_id = %s
        ORDER BY day
        LIMIT %s
        """,
        (project_id, limit),
    )
    metrics_daily = [
        {
            "day": _iso(m["day"]),
            "suggestions": m["suggestions"],
            "accepted": m["accepted"],
            "corrected": m["corrected"],
            "ignored": m["ignored"],
            "abstained": m["abstained"],
            "auto_labeled": m["auto_labeled"],
            "auto_corrected": m["auto_corrected"],
            "model_ver": m["model_ver"],
            "emb_model_ver": m["emb_model_ver"],
        }
        for m in metrics
    ]

    return {
        "label_events": label_events,
        "model_artifacts": model_artifacts,
        "metrics_daily": metrics_daily,
        "maturity": _maturity(db, project_id),
        "rp": _rp_block(rp, project_id),
    }


def _maturity(db: Database, project_id: int) -> dict[str, Any]:
    """Learning-loop maturity for the selected project (informational).

    Places the project on the Cold → Warm → Hot band by its DISTINCT LABELED
    ITEMS (training-frame contribution), and reports the live counters plus the
    real model machinery (install-wide GBM + the per-project isotonic calibrator
    whose presence is the Hot marker). All read-only; every number is real or a
    derived ratio — no placeholders.
    """
    ev = (
        db.one(
            """
            SELECT count(*)                                    AS label_events,
                   count(*) FILTER (WHERE source = ANY(%s))    AS human_events,
                   count(DISTINCT item_id) FILTER (
                       WHERE new_label IS NOT NULL AND new_label <> 'ti'
                   )                                           AS labeled_items
            FROM analyzer.label_event WHERE project_id = %s
            """,
            (list(_HUMAN_SOURCES), project_id),
        )
        or {}
    )
    modes = (
        db.one(
            """
            SELECT count(*) FILTER (WHERE status = 'confirmed') AS confirmed,
                   count(*) FILTER (WHERE status = 'candidate') AS candidate,
                   count(*) FILTER (WHERE status = 'seed')      AS seed,
                   count(*)                                     AS total
            FROM analyzer.failure_mode WHERE project_id = %s
            """,
            (project_id,),
        )
        or {}
    )
    sig = (
        db.one(
            "SELECT count(*) AS signatures, "
            "count(*) FILTER (WHERE emb IS NOT NULL) AS embedded "
            "FROM analyzer.failure_signature WHERE project_id = %s",
            (project_id,),
        )
        or {}
    )
    # Active model machinery: the install-wide GBM (project_id IS NULL) and — the
    # Hot marker — a per-project isotonic calibrator row for THIS project.
    gbm = db.one(
        "SELECT version, n_events FROM analyzer.model_artifact "
        "WHERE project_id IS NULL AND kind = 'gbm' AND is_active LIMIT 1"
    )
    calib = db.one(
        "SELECT version, n_events FROM analyzer.model_artifact "
        "WHERE project_id = %s AND kind = 'calib' AND is_active LIMIT 1",
        (project_id,),
    )

    labeled = ev.get("labeled_items", 0) or 0
    if labeled >= CALIB_MIN_EVENTS:
        stage = "hot"
    elif labeled >= GBM_MIN_EVENTS:
        stage = "warm"
    else:
        stage = "cold"

    signatures = sig.get("signatures", 0) or 0
    embedded = sig.get("embedded", 0) or 0
    return {
        "stage": stage,
        "labeled_items": labeled,
        "gbm_min_events": GBM_MIN_EVENTS,
        "calib_min_events": CALIB_MIN_EVENTS,
        "label_events": ev.get("label_events", 0) or 0,
        "human_events": ev.get("human_events", 0) or 0,
        "modes_confirmed": modes.get("confirmed", 0) or 0,
        "modes_candidate": modes.get("candidate", 0) or 0,
        "modes_seed": modes.get("seed", 0) or 0,
        "modes_total": modes.get("total", 0) or 0,
        "signatures": signatures,
        "embedded": embedded,
        "embedded_pct": (embedded / signatures) if signatures else None,
        "install_gbm": gbm["version"] if gbm else None,
        "install_gbm_events": gbm["n_events"] if gbm else None,
        "project_calibrator": calib["version"] if calib else None,
        "project_calibrator_events": calib["n_events"] if calib else None,
    }


# --------------------------------------------------------------------------- #
# Summary counters
# --------------------------------------------------------------------------- #
def summary(db: Database, project_id: int, rp: RPNameResolver | None = None) -> dict[str, Any]:
    row = db.one(
        """
        SELECT
          count(*)                                             AS suggestions,
          count(*) FILTER (WHERE outcome = 'accepted')         AS accepted,
          count(*) FILTER (WHERE outcome = 'corrected')        AS corrected,
          count(*) FILTER (WHERE outcome = 'ignored')          AS ignored,
          count(*) FILTER (WHERE predicted_label = 'ti')       AS abstains,
          count(*) FILTER (WHERE outcome = 'pending')          AS pending,
          count(*) FILTER (WHERE llm_used)                     AS llm_used
        FROM analyzer.suggestion WHERE project_id = %s
        """,
        (project_id,),
    ) or {}
    counts = db.one(
        """
        SELECT
          (SELECT count(*) FROM analyzer.test_item WHERE project_id = %s)          AS items,
          (SELECT count(*) FROM analyzer.failure_signature WHERE project_id = %s)  AS signatures,
          (SELECT count(*) FROM analyzer.failure_signature
             WHERE project_id = %s AND emb IS NOT NULL)                            AS embedded,
          (SELECT count(*) FROM analyzer.log_template WHERE project_id = %s)       AS templates,
          (SELECT count(*) FROM analyzer.launch_group WHERE project_id = %s)       AS groups,
          (SELECT count(*) FROM analyzer.failure_mode WHERE project_id = %s)       AS modes,
          (SELECT count(*) FROM analyzer.label_event WHERE project_id = %s)        AS label_events
        """,
        (project_id,) * 7,
    ) or {}
    return {
        "project_id": project_id,
        "rp": _rp_block(rp, project_id),
        "suggestions": row.get("suggestions", 0),
        "accepted": row.get("accepted", 0),
        "corrected": row.get("corrected", 0),
        "ignored": row.get("ignored", 0),
        "abstains": row.get("abstains", 0),
        "pending": row.get("pending", 0),
        "llm_used": row.get("llm_used", 0),
        "items": counts.get("items", 0),
        "signatures": counts.get("signatures", 0),
        "embedded": counts.get("embedded", 0),
        "templates": counts.get("templates", 0),
        "groups": counts.get("groups", 0),
        "modes": counts.get("modes", 0),
        "label_events": counts.get("label_events", 0),
    }


# --------------------------------------------------------------------------- #
# Signatures Explorer (fingerprint space + hash-collision / label-bleed)
# --------------------------------------------------------------------------- #
# A member cap for the detail member list — mirrors the journey/reconstruction
# caps. The list row reports the true total so "N more" stays honest.
_SIG_MEMBER_CAP = 50


def _label_breakdown(issue_types: list[Any]) -> list[dict[str, Any]]:
    """Ordered per-locator counts (e.g. ``[{pb001, pb, 6}, {si001, si, 2}]``).

    Built from the members' *real* issue_type locators — the frontend renders
    each as a defect abbreviation badge with its count. ``None`` (unlabeled)
    collapses to the honest ``none`` group, never a fabricated label.
    """
    from collections import Counter

    counts = Counter(issue_types)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0] or "￿"))
    return [{"locator": loc, "group": _grp(loc), "count": n} for loc, n in ordered]


def signatures(
    db: Database,
    project_id: int,
    q: str | None,
    conflicts_only: bool,
    limit: int,
    offset: int = 0,
    rp: RPNameResolver | None = None,
) -> dict[str, Any]:
    """One row per distinct ``error_hash`` — the fingerprint space, ranked by
    member count, with a distinct-label breakdown that surfaces hash collisions
    (Stage-A label-bleed candidates: members carrying >1 distinct non-ti label).
    """
    # ---- project-wide fingerprint-space summary (never search-scoped) ----
    summ = db.one(
        """
        SELECT count(DISTINCT error_hash)  AS distinct_error_hash,
               count(DISTINCT exception_fp) AS distinct_exception_fp,
               count(*)                     AS items_with_signatures,
               count(*) FILTER (WHERE emb IS NOT NULL) AS embedded
        FROM analyzer.failure_signature WHERE project_id = %s
        """,
        (project_id,),
    ) or {}
    conflict_total = db.one(
        """
        SELECT count(*) AS n FROM (
            SELECT fs.error_hash
            FROM analyzer.failure_signature fs
            JOIN analyzer.test_item ti USING (project_id, item_id)
            WHERE fs.project_id = %s
            GROUP BY fs.error_hash
            HAVING count(DISTINCT ti.issue_type_group)
                     FILTER (WHERE ti.issue_type_group IS NOT NULL
                                 AND ti.issue_type_group <> 'ti') > 1
        ) t
        """,
        (project_id,),
    ) or {}

    params: list[Any] = [project_id]
    search_clause = ""
    if q:
        like = f"%{q}%"
        search_clause = (
            " AND (fs.exc_text ILIKE %s OR fs.msg_text ILIKE %s OR fs.frames_text ILIKE %s)"
        )
        params += [like, like, like]

    # Aggregate one row per error_hash. status_codes are unnested in a separate
    # CTE so the join never inflates member_count; the main pass never touches a
    # per-row subquery, so there is no N+1 as pages grow.
    sql = (
        """
        WITH base AS (
            SELECT fs.error_hash, fs.exception_fp,
                   (fs.emb IS NOT NULL) AS has_emb,
                   NULLIF(split_part(fs.exc_text, ' ', 1), '') AS exc_class,
                   fs.status_codes,
                   ti.issue_type, ti.issue_type_group, ti.indexed_at
            FROM analyzer.failure_signature fs
            JOIN analyzer.test_item ti USING (project_id, item_id)
            WHERE fs.project_id = %s"""
        + search_clause
        + """
        ),
        codes AS (
            SELECT error_hash, array_agg(DISTINCT sc ORDER BY sc) AS status_codes
            FROM base, unnest(status_codes) AS sc
            GROUP BY error_hash
        )
        SELECT b.error_hash::text AS error_hash,
               count(*) AS member_count,
               count(DISTINCT b.exception_fp) AS distinct_fp,
               count(*) FILTER (WHERE b.has_emb) AS embedded_count,
               min(b.indexed_at) AS first_seen,
               max(b.indexed_at) AS last_seen,
               count(DISTINCT b.issue_type_group)
                 FILTER (WHERE b.issue_type_group IS NOT NULL
                             AND b.issue_type_group <> 'ti') AS distinct_nonti,
               array_agg(b.issue_type) AS issue_types,
               array_remove(array_agg(DISTINCT b.exc_class), NULL) AS exc_classes,
               COALESCE(max(c.status_codes), '{}') AS status_codes
        FROM base b
        LEFT JOIN codes c USING (error_hash)
        GROUP BY b.error_hash"""
        # HAVING references the base column names via the join below; but base is
        # already grouped, so re-derive the conflict predicate on b.* here.
        + (
            " HAVING count(DISTINCT b.issue_type_group)"
            " FILTER (WHERE b.issue_type_group IS NOT NULL AND b.issue_type_group <> 'ti') > 1"
            if conflicts_only
            else ""
        )
        + """
        ORDER BY member_count DESC, b.error_hash
        LIMIT %s OFFSET %s
        """
    )
    params += [limit, offset]
    rows = db.rows(sql, tuple(params))

    out_rows = []
    for r in rows:
        breakdown = _label_breakdown(r["issue_types"] or [])
        out_rows.append(
            {
                "error_hash": r["error_hash"],
                "member_count": r["member_count"],
                "distinct_fp": r["distinct_fp"],
                "embedded_count": r["embedded_count"],
                "first_seen": _iso(r["first_seen"]),
                "last_seen": _iso(r["last_seen"]),
                "exc_classes": r["exc_classes"] or [],
                "status_codes": r["status_codes"] or [],
                "labels": breakdown,
                "distinct_nonti": r["distinct_nonti"],
                "is_conflict": (r["distinct_nonti"] or 0) > 1,
            }
        )

    return {
        "rows": out_rows,
        "count": len(out_rows),
        "offset": offset,
        "limit": limit,
        "conflicts_only": conflicts_only,
        "q": q or "",
        "summary": {
            "distinct_error_hash": summ.get("distinct_error_hash", 0),
            "distinct_exception_fp": summ.get("distinct_exception_fp", 0),
            "items_with_signatures": summ.get("items_with_signatures", 0),
            "embedded": summ.get("embedded", 0),
            "conflict_hashes": conflict_total.get("n", 0),
        },
        "rp": _rp_block(rp, project_id),
    }


def signature_hash(
    db: Database, project_id: int, error_hash: Any, rp: RPNameResolver | None = None
) -> dict[str, Any] | None:
    """Detail for one ``error_hash``: a representative signature (exc/msg/frames/
    templates + status_codes) and the member items list, capped at
    :data:`_SIG_MEMBER_CAP` with an honest total for the "N more" note."""
    try:
        eh = int(error_hash)
    except (TypeError, ValueError):
        return None

    # Representative signature — prefer a real (non-ti) labeled member so the
    # exc/msg/frames shown are the human-triaged exemplar, not a probe.
    rep = db.one(
        """
        SELECT fs.item_id, fs.exception_fp, fs.error_hash, fs.top_frames, fs.template_ids,
               fs.exc_text, fs.msg_text, fs.frames_text, fs.tmpl_text, fs.signature_text,
               fs.only_numbers, fs.status_codes, fs.urls, fs.paths,
               fs.emb_model_ver, (fs.emb IS NOT NULL) AS has_emb
        FROM analyzer.failure_signature fs
        JOIN analyzer.test_item ti USING (project_id, item_id)
        WHERE fs.project_id = %s AND fs.error_hash = %s
        ORDER BY (ti.issue_type_group IS NOT NULL AND ti.issue_type_group <> 'ti') DESC,
                 fs.item_id
        LIMIT 1
        """,
        (project_id, eh),
    )
    if rep is None:
        return None

    template_ids = rep["template_ids"] or []
    templates_block: list[dict[str, Any]] = []
    if template_ids:
        trows = db.rows(
            """
            SELECT template_id, pattern, token_count, example, match_count,
                   first_seen, last_seen
            FROM analyzer.log_template
            WHERE project_id = %s AND template_id = ANY(%s)
            """,
            (project_id, template_ids),
        )
        by_id = {t["template_id"]: t for t in trows}
        for tid in template_ids:
            t = by_id.get(tid)
            templates_block.append(
                _template_row(t)
                if t
                else {"template_id": str(tid), "pattern": None, "missing": True}
            )

    total_row = db.one(
        "SELECT count(*) AS n FROM analyzer.failure_signature "
        "WHERE project_id = %s AND error_hash = %s",
        (project_id, eh),
    )
    member_total = total_row["n"] if total_row else 0

    # Accurate distinct-label breakdown (independent of the member cap).
    label_rows = db.rows(
        """
        SELECT ti.issue_type AS locator, count(*) AS n
        FROM analyzer.failure_signature fs
        JOIN analyzer.test_item ti USING (project_id, item_id)
        WHERE fs.project_id = %s AND fs.error_hash = %s
        GROUP BY ti.issue_type
        ORDER BY n DESC, ti.issue_type
        """,
        (project_id, eh),
    )
    labels = [
        {"locator": r["locator"], "group": _grp(r["locator"]), "count": r["n"]} for r in label_rows
    ]
    distinct_nonti = len({r["group"] for r in labels if r["group"] not in ("ti", "none")})

    member_rows = db.rows(
        """
        SELECT ti.item_id, ti.item_name, ti.issue_type, ti.issue_type_group,
               ti.is_auto_analyzed, ti.launch_id, ti.launch_name, ti.indexed_at,
               (fs.emb IS NOT NULL) AS has_emb
        FROM analyzer.failure_signature fs
        JOIN analyzer.test_item ti USING (project_id, item_id)
        WHERE fs.project_id = %s AND fs.error_hash = %s
        ORDER BY (ti.issue_type_group IS NOT NULL AND ti.issue_type_group <> 'ti') DESC,
                 ti.issue_type_group, ti.item_id
        LIMIT %s
        """,
        (project_id, eh, _SIG_MEMBER_CAP),
    )
    links = rp.item_links(project_id, [m["item_id"] for m in member_rows]) if rp else {}
    members = [
        {
            "item_id": m["item_id"],
            "item_name": m["item_name"],
            "issue_type": m["issue_type"],
            "label_group": _grp(m["issue_type"]),
            "is_auto_analyzed": m["is_auto_analyzed"],
            "launch_id": m["launch_id"],
            "launch_name": m["launch_name"],
            "indexed_at": _iso(m["indexed_at"]),
            "has_emb": m["has_emb"],
            **_ui_url(links.get(m["item_id"])),
            **_ui_url(rp.launch_link(project_id, m["launch_id"]) if rp else None, "launch_url"),
        }
        for m in member_rows
    ]

    return {
        "error_hash": _s(rep["error_hash"]),
        "exception_fp": _s(rep["exception_fp"]),
        "representative_item_id": rep["item_id"],
        "distinct_nonti": distinct_nonti,
        "is_conflict": distinct_nonti > 1,
        "signature": {
            "exc_text": rep["exc_text"],
            "msg_text": rep["msg_text"],
            "frames_text": rep["frames_text"],
            "tmpl_text": rep["tmpl_text"],
            "top_frames": rep["top_frames"] or [],
            "template_ids": _slist(template_ids),
            "signature_text": rep["signature_text"],
            "only_numbers": rep["only_numbers"],
            "status_codes": rep["status_codes"] or [],
            "urls": rep["urls"] or [],
            "paths": rep["paths"] or [],
            "emb_model_ver": rep["emb_model_ver"],
            "has_emb": rep["has_emb"],
        },
        "templates": templates_block,
        "labels": labels,
        "members": members,
        "member_total": member_total,
        "member_shown": len(members),
        "rp": _rp_block(rp, project_id),
    }


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)
