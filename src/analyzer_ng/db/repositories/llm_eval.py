"""PG comparison source for the nightly LLM eval (spec 04 §6.2).

Turns the resolved ``suggestion`` outcomes over the eval window into the paired
``RoleComparison`` the kill-switch job scores. Per-project by construction — every
row is grouped by ``project_id`` and never mixed across tenants (§5.4).

Cold-start (§6.2) is the cleanly-derivable label comparison: the accepted rate of
``llm_coldstart`` suggestions (``model_ver`` ``rubric+…``) versus the accepted rate
of the seed-KB rule path (``model_ver`` ``rule_cold…``) in the same project/window.
A suggestion is *accepted* when its predicted base group matched the human label
(``outcome='accepted'``, stamped by ``defect_update``); *resolved* means accepted
or corrected. The judge comparison needs per-candidate label persistence the v1
single-row ``suggestion`` model does not carry, so it is intentionally not emitted
here (judge stays health-only via ``llm_event``); the job disables any role it is
given data for.
"""

from __future__ import annotations

from datetime import datetime

from analyzer_ng.db.repositories._common import StoreBase
from analyzer_ng.llm.eval import RoleComparison


class PgLlmComparisonSource(StoreBase):
    """Builds ``{(project_id, role): RoleComparison}`` from resolved suggestions."""

    def role_comparisons(self, since: datetime) -> dict[tuple[int, str], RoleComparison]:
        return self._coldstart_comparisons(since)

    def _coldstart_comparisons(self, since: datetime) -> dict[tuple[int, str], RoleComparison]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT project_id,
                    count(*) FILTER (
                        WHERE model_ver LIKE 'rubric+%%' AND outcome IN ('accepted','corrected')
                    ) AS cs_n,
                    count(*) FILTER (
                        WHERE model_ver LIKE 'rubric+%%' AND outcome = 'accepted'
                    ) AS cs_s,
                    count(*) FILTER (
                        WHERE model_ver LIKE 'rule_cold%%' AND outcome IN ('accepted','corrected')
                    ) AS rule_n,
                    count(*) FILTER (
                        WHERE model_ver LIKE 'rule_cold%%' AND outcome = 'accepted'
                    ) AS rule_s
                FROM analyzer.suggestion
                WHERE created_at >= %s
                GROUP BY project_id
                """,
                (since,),
            ).fetchall()
        out: dict[tuple[int, str], RoleComparison] = {}
        for project_id, cs_n, cs_s, rule_n, rule_s in rows:
            if cs_n == 0:
                continue  # no cold-start LLM cases to evaluate this window
            out[(int(project_id), "coldstart")] = RoleComparison(
                n=int(cs_n),
                s_llm=int(cs_s),
                n_llm=int(cs_n),
                s_classical=int(rule_s),
                n_classical=int(rule_n),
            )
        return out
