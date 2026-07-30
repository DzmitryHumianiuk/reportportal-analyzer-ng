"""The four adopted LLM roles (spec 04 §4). Rejected roles are not implemented."""

from __future__ import annotations

from analyzer_ng.llm.roles.base import Role
from analyzer_ng.llm.roles.coldstart import ColdStartRole
from analyzer_ng.llm.roles.explainer import AbstainExplainerRole, ExplainerRole
from analyzer_ng.llm.roles.extractor import ExtractorRole
from analyzer_ng.llm.roles.judge import JudgeRole

__all__ = [
    "Role",
    "ExplainerRole",
    "AbstainExplainerRole",
    "ExtractorRole",
    "JudgeRole",
    "ColdStartRole",
]
