"""Build expected.json -- the machine-readable expected-outcome manifest.

For every probe (P) item in the plan we record what the analyzer *should* do:
the expected action (auto-label / suggest / abstain), the expected label, and
where SCENARIOS.md states it, the expected decision method
(hash | kb | gbm | rule_cold). Scoring joins predictions to these rows via the
`scenario` / `adv_case` item attributes plus the archetype id.
"""
from __future__ import annotations

from . import config
from .schedule import P

# Per-archetype expected analyzer behaviour for its probe items.
# action: "auto" (confident label) | "suggest" (top-k, human confirms) | "abstain"
# method: hash | kb | gbm | rule_cold | ""(unspecified)
EXPECTED: dict[str, dict] = {
    "JAVA-SEL-01": dict(action="auto", label="pb", method="hash"),
    "JAVA-SEL-02": dict(action="auto", label="ab", method="kb"),
    "JAVA-SEL-03": dict(action="auto", label="pb", method="hash"),
    "JAVA-SEL-05": dict(action="auto", label="pb", method="gbm"),
    "JAVA-SEL-06": dict(action="auto", label="si", method="gbm"),
    "JAVA-SEL-07": dict(action="abstain", label="ti", method="gbm"),
    "JAVA-SEL-08": dict(action="auto", label="pb", method="kb"),
    "JAVA-SEL-09": dict(action="suggest", label="per_variant", method="gbm"),
    "JAVA-SEL-10": dict(action="auto", label="pb", method="gbm"),
    "JAVA-SEL-11": dict(action="auto", label="pb", method="gbm"),
    "JAVA-SEL-12": dict(action="suggest", label="per_variant", method="gbm"),
    "JAVA-SEL-13": dict(action="auto", label="pb", method="hash"),
    "JAVA-SEL-14": dict(action="auto", label="pb", method="hash"),
    "JAVA-SEL-15": dict(action="auto", label="pb", method="hash"),
    "JAVA-SEL-16": dict(action="suggest", label="pb", method="gbm"),
    "JAVA-SEL-17": dict(action="suggest", label="si", method="gbm"),
    "JAVA-SEL-18": dict(action="suggest", label="per_variant", method="gbm"),
    "JAVA-SEL-19": dict(action="suggest", label="pb", method="gbm"),
    "JAVA-SEL-20": dict(action="suggest", label="per_variant", method="gbm"),
    "JAVA-SEL-21": dict(action="suggest", label="pb", method="gbm"),
    "JAVA-SEL-22": dict(action="suggest", label="per_variant", method="gbm"),
    "JAVA-SEL-23": dict(action="auto", label="si", method="kb"),
    "JAVA-SEL-24": dict(action="suggest", label="pb", method="gbm"),
    "JAVA-SEL-25": dict(action="suggest", label="ab", method="gbm"),
    "JAVA-SEL-26": dict(action="suggest", label="pb", method="gbm"),
    "JAVA-SEL-27": dict(action="suggest", label="ab", method="gbm"),
    "JAVA-SEL-28": dict(action="suggest", label="pb", method="gbm"),
    "JAVA-SEL-29": dict(action="suggest", label="si", method="gbm"),
    "JAVA-SEL-30": dict(action="abstain", label="ti", method=""),
    "JAVA-SEL-31": dict(action="auto", label="pb", method="hash"),
    "NET-XUN-01": dict(action="auto", label="pb", method="hash"),
    "NET-XUN-02": dict(action="suggest", label="pb", method="gbm"),
    "NET-XUN-03": dict(action="suggest", label="si", method="gbm"),
    "NET-XUN-04": dict(action="suggest", label="pb", method="gbm"),
    "NET-XUN-05": dict(action="auto", label="si", method="rule_cold"),
    "NET-XUN-06": dict(action="abstain", label="ti", method=""),
    "NET-XUN-07": dict(action="suggest", label="si", method="gbm"),
    "NET-XUN-08": dict(action="suggest", label="nd", method="gbm"),
    "NET-XUN-10": dict(action="suggest", label="pb", method="gbm"),
    "NET-XUN-11": dict(action="suggest", label="pb", method="gbm"),
    "NET-XUN-12": dict(action="suggest", label="si", method="gbm"),
    "NET-XUN-13": dict(action="auto", label="si", method="hash"),
    "NET-XUN-14": dict(action="abstain", label="ti", method=""),
    "NET-XUN-15": dict(action="abstain", label="ti", method=""),
    "NET-XUN-16": dict(action="suggest", label="varies", method="gbm"),
    "TS-PW-01": dict(action="suggest", label="nd", method="rule_cold"),
    "TS-PW-02": dict(action="auto", label="si", method="rule_cold"),
    "TS-PW-03": dict(action="abstain", label="ti", method=""),
    "TS-PW-04": dict(action="auto", label="si", method="rule_cold"),
    "TS-PW-05": dict(action="auto", label="si", method="rule_cold"),
    "TS-PW-06": dict(action="auto", label="si", method="rule_cold"),
    "TS-PW-07": dict(action="abstain", label="ti", method="rule_cold"),
    "TS-PW-08": dict(action="abstain", label="ti", method="rule_cold"),
    "TS-PW-09": dict(action="abstain", label="ti", method=""),
    "TS-PW-10": dict(action="auto", label="nd", method="rule_cold"),
    "TS-PW-11": dict(action="abstain", label="ti", method=""),
    "TS-PW-12": dict(action="abstain", label="ti", method=""),
    "TS-PW-13": dict(action="abstain", label="ti", method=""),
    "TS-CY-02": dict(action="suggest", label="pb", method="gbm"),
}


def build_manifest(plan) -> dict:
    probes = []
    for la in plan:
        for pi in la.items:
            if pi.role != P:
                continue
            it = pi.item
            exp = dict(EXPECTED.get(it.archetype_id,
                                    dict(action="suggest", label=it.ground_truth or "ti",
                                         method="")))
            label = exp["label"]
            if label in ("per_variant", "varies"):
                label = it.ground_truth or "ti"
            probes.append({
                "archetype_id": it.archetype_id,
                "project": it.project,
                "launch": la.name,
                "date": la.date,
                "test_name": it.test_name,
                "scenario": it.scenario_refs,
                "adv_case": it.adv_case,
                "ground_truth": it.ground_truth,
                "expected_action": exp["action"],
                "expected_label": label,
                "expected_method": exp["method"],
            })
    by_project = {}
    for p in probes:
        by_project[p["project"]] = by_project.get(p["project"], 0) + 1
    return {
        "generated_by": "demo-data/generate.py",
        "seed": config.SEED,
        "note": ("Per-probe expected analyzer outcome. Join to suggestion rows via "
                 "item attributes scenario:Sxx / adv_case + archetype id. "
                 "ground_truth 'ti' means the correct outcome is abstain."),
        "probe_count": len(probes),
        "probe_count_by_project": by_project,
        "probes": probes,
    }
