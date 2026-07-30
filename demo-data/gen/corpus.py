"""Load demo-data/corpus/*.json and expand archetype variants into concrete
RP test items (test name + ordered log rows) with deterministic placeholder
substitution.
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass

from . import config

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


@dataclass
class LogRow:
    level: str
    message: str


@dataclass
class RenderedItem:
    archetype_id: str
    variant_idx: int
    seq: int
    project: str
    test_name: str
    status: str  # "failed" | "passed"
    ground_truth: str  # pb/ab/si/nd/ti (the CORRECT label; ti == abstain)
    logs: list[LogRow]
    scenario_refs: list[str]
    adv_case: str | None
    attachments: list[dict]


@dataclass
class Archetype:
    archetype_id: str
    framework: str
    label: str
    title: str
    test_name_pattern: str
    info_logs: list[dict]
    error_logs: list[dict]
    variants: list[dict]
    placeholders: list[str]
    scenario_refs: list[str]
    adv_case: str | None
    spam_repeat: dict | None
    attachments: list[dict]
    expected_behavior: str

    @property
    def project(self) -> str:
        return config.FRAMEWORK_PROJECT[self.framework]

    def variant_label(self, vidx: int, project: str | None = None) -> str:
        v = self.variants[vidx]
        if project and v.get("project_labels", {}).get(project):
            return v["project_labels"][project]
        return v.get("label_override") or self.label

    def variant_status(self, vidx: int) -> str:
        return self.variants[vidx].get("status", "failed")

    def variant_adv(self, vidx: int) -> str | None:
        return self.variants[vidx].get("adv_case") or self.adv_case


def _subst(template: str, params: dict) -> str:
    """Substitute {placeholder} tokens iteratively (params may nest)."""
    prev = None
    out = template
    for _ in range(6):
        if out == prev:
            break
        prev = out
        for k, val in params.items():
            out = out.replace("{" + k + "}", str(val))
    return out


def load_corpus(corpus_dir: str) -> dict[str, Archetype]:
    archetypes: dict[str, Archetype] = {}
    for path in sorted(glob.glob(os.path.join(corpus_dir, "*.json"))):
        doc = json.load(open(path))
        fw = doc["framework"]
        for a in doc["archetypes"]:
            arc = Archetype(
                archetype_id=a["archetype_id"],
                framework=a.get("framework", fw),
                label=a["label"],
                title=a.get("title", ""),
                test_name_pattern=a["test_name_pattern"],
                info_logs=a.get("info_logs", []),
                error_logs=a["error_logs"],
                variants=a["variants"],
                placeholders=a.get("placeholders", []),
                scenario_refs=a.get("scenario_refs", []),
                adv_case=a.get("adv_case"),
                spam_repeat=a.get("spam_repeat"),
                attachments=a.get("attachments", []),
                expected_behavior=a.get("expected_behavior", ""),
            )
            archetypes[arc.archetype_id] = arc
    return archetypes


def render_item(
    arc: Archetype,
    variant_idx: int,
    seq: int,
    project: str,
    *,
    spam_cap: int | None = None,
) -> RenderedItem:
    """Render one concrete item for a given variant occurrence."""
    params = dict(arc.variants[variant_idx]["params"])
    test_name = _subst(arc.test_name_pattern, params)

    logs: list[LogRow] = []
    for lg in arc.info_logs:
        logs.append(LogRow(lg.get("level", "info"), _subst(lg["template"], params)))

    # spam block (repeated line) -- rendered as ONE big log row, capped for smoke.
    spam_rows_before: list[LogRow] = []
    spam_rows_after: list[LogRow] = []
    if arc.spam_repeat:
        line = _subst(arc.spam_repeat["line"], params)
        count = int(arc.spam_repeat["count"])
        if spam_cap is not None:
            count = min(count, spam_cap)
        block = "\n".join(line for _ in range(count))
        row = LogRow("warn", block)
        if arc.spam_repeat.get("position", "before_error") == "after_error":
            spam_rows_after.append(row)
        else:
            spam_rows_before.append(row)

    logs.extend(spam_rows_before)
    for lg in arc.error_logs:
        logs.append(LogRow(lg.get("level", "error"), _subst(lg["template"], params)))
    logs.extend(spam_rows_after)

    status = arc.variant_status(variant_idx)
    gt = arc.variant_label(variant_idx, project)
    # passing decoys carry the same logs but pass -> no ground-truth defect label
    if status == "passed":
        gt = None

    return RenderedItem(
        archetype_id=arc.archetype_id,
        variant_idx=variant_idx,
        seq=seq,
        project=project,
        test_name=test_name,
        status=status,
        ground_truth=gt,
        logs=logs,
        scenario_refs=list(arc.scenario_refs),
        adv_case=arc.variant_adv(variant_idx),
        attachments=arc.attachments,
    )
