"""Structural store contracts (spec 02 §4.2–§4.4).

These are ``typing.Protocol`` classes so tests can substitute in-memory fakes;
the production implementations live one-per-file in this package. Signatures
mirror spec 02 §4 (rendered synchronous to match the threaded service — see
``_common``).
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import date, datetime
from typing import Any, Literal, Protocol

from analyzer_ng.db.repositories.models import (
    Candidate,
    CandidateFilters,
    LabelEventIn,
    MatchedBy,
    ModeIn,
    QuerySignature,
    SignatureIn,
    TestItemIn,
)


class RetrievalStore(Protocol):
    def transaction(self) -> AbstractContextManager[Any]: ...
    def upsert_items(self, items: Sequence[TestItemIn], *, conn: object | None = None) -> int: ...
    def upsert_signatures(
        self, sigs: Sequence[SignatureIn], *, conn: object | None = None
    ) -> int: ...
    def update_issue_type(
        self, project_id: int, item_id: int, issue_type: str | None, is_auto: bool
    ) -> bool: ...
    def delete_items(self, project_id: int, item_ids: Sequence[int]) -> int: ...
    def delete_launches(self, project_id: int, launch_ids: Sequence[int]) -> int: ...
    def delete_project(self, project_id: int) -> int: ...
    def delete_by_time_range(
        self,
        project_id: int,
        field: Literal["start_time", "log_time"],
        before: datetime,
        after: datetime | None = None,
    ) -> int: ...
    def get_items_labels(
        self, project_id: int, item_ids: Sequence[int]
    ) -> dict[int, str | None]: ...
    def record_feedback_outcome(self, project_id: int, item_id: int, new_label: str) -> int: ...
    def find_by_error_hash(
        self, project_id: int, error_hash: int, limit: int = 5
    ) -> list[Candidate]: ...
    def find_candidates(
        self,
        project_id: int,
        q: QuerySignature,
        k: int = 20,
        filters: CandidateFilters | None = None,
    ) -> list[Candidate]: ...


class KBStore(Protocol):
    def match_modes(self, project_id: int, q: QuerySignature, k: int = 10) -> list[Candidate]: ...
    def member_mode_id(self, project_id: int, item_id: int) -> int | None: ...
    def spawn_candidate_mode(self, mode: ModeIn, seed_item_ids: Sequence[int]) -> int: ...
    def ensure_seed_mode(self, project_id: int, seed_key: str, mode: ModeIn) -> int: ...
    def add_members(
        self, project_id: int, mode_id: int, members: Sequence[tuple[int, float, MatchedBy]]
    ) -> int: ...
    def update_purity(self, project_id: int, mode_id: int) -> float: ...
    def merge_modes(self, project_id: int, src_mode_id: int, dst_mode_id: int) -> None: ...
    def split_mode(
        self, project_id: int, mode_id: int, partition: dict[int, list[int]]
    ) -> list[int]: ...
    def set_status(
        self,
        project_id: int,
        mode_id: int,
        status: str,
        label: str | None = None,
        label_source: str | None = None,
    ) -> None: ...
    def list_modes(self, project_id: int, statuses: Sequence[str] | None = None) -> list[dict]: ...


class LabelStore(Protocol):
    def append_event(self, ev: LabelEventIn) -> int: ...
    def count_events_since(
        self, since: datetime | None = None, project_id: int | None = None
    ) -> int: ...
    def fetch_training_frame(
        self,
        project_id: int | None = None,
        since: datetime | None = None,
        limit: int = 500_000,
    ) -> list[dict]: ...


class StatsStore(Protocol):
    def bump_test_history(
        self,
        project_id: int,
        test_case_hash: int,
        failed: bool,
        ts: datetime,
        *,
        conn: object | None = None,
    ) -> None: ...
    def get_test_history(
        self, project_id: int, test_case_hashes: Sequence[int]
    ) -> dict[int, dict]: ...
    def bump_metrics(
        self,
        project_id: int,
        day: date,
        *,
        suggestions: int = 0,
        accepted: int = 0,
        corrected: int = 0,
        ignored: int = 0,
        abstained: int = 0,
        label: str | None = None,
    ) -> None: ...
    def get_metrics(self, project_id: int, frm: date, to: date) -> list[dict]: ...


class Drain3StateStore(Protocol):
    def load(self, project_id: int) -> tuple[bytes, int] | None: ...
    def save(self, project_id: int, state: bytes, expected_version: int, config: dict) -> bool: ...
    def load_template_texts(self, project_id: int) -> list[str]: ...
    def upsert_templates(self, project_id: int, templates: Sequence[dict]) -> int: ...


class LlmCacheStore(Protocol):
    def get(self, project_id: int, cache_key: str) -> dict | None: ...
    def get_fresh(self, project_id: int, cache_key: str, ttl_days: int) -> dict | None: ...
    def put(
        self,
        project_id: int,
        cache_key: str,
        role: str,
        model: str,
        output: dict,
        template_hash: int | None = None,
    ) -> None: ...


class LlmEventStore(Protocol):
    def record(
        self,
        *,
        project_id: int,
        item_id: int,
        role: str,
        model: str,
        prompt_hash: str,
        cache_hit: bool,
        outcome: str,
        output: dict | None = None,
        latency_ms: int | None = None,
    ) -> int: ...


class LlmRoleStateStore(Protocol):
    def is_enabled(self, project_id: int, role: str) -> bool: ...
    def set_state(
        self,
        project_id: int,
        role: str,
        *,
        enabled: bool,
        reason: str | None = None,
        stats: dict | None = None,
    ) -> None: ...
