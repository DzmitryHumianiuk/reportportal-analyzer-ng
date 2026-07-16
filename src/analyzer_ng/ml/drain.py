"""Per-project Drain3 template mining (spec 03 §2).

One :class:`DrainManager` wraps a single ``drain3.TemplateMiner`` for one
project. Masking (§2.1) is applied *before* Drain sees a line, so template
parameters never contain volatile values — that is what makes a template's
identity stable. Identity is the **template hash** (XXH3-64 of the mined,
masked template text), *not* Drain's internal ``cluster_id``, which is volatile
under LRU eviction and rebuilds (§2.3).

Persistence is driven through the spec-02 ``Drain3StateStore`` CAS contract:
the serialized Drain snapshot is the opaque state blob, and the live clusters
are mirrored into ``log_template`` for queryability and for the state-loss
rebuild path.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.persistence_handler import PersistenceHandler
from drain3.template_miner_config import TemplateMinerConfig

from analyzer_ng.ml.hashing import xxh3_64_signed, xxh3_64_unsigned
from analyzer_ng.preprocessing.text_processing import is_line_from_stacktrace

logger = logging.getLogger(__name__)

# §2.1 masking instructions, in this exact order. Drain wraps ``mask_with`` in
# the configured prefix/suffix (defaults ``<``/``>``), producing e.g. ``<URL>``.
MASKING_RULES: list[tuple[str, str]] = [
    ("URL", r"https?://[^\s\"'<>]+"),
    ("IP", r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b"),
    ("UUID", r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    ("HEX", r"\b0[xX][0-9a-fA-F]+\b|\b[0-9a-fA-F]{16,}\b"),
    ("PATH", r"(?:[A-Za-z]:)?(?:[\\/][\w.\-]+){2,}"),
    ("NUM", r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])"),
]

# Standalone compiled masking pipeline (used for the signature MSG field, §3.1,
# which must be masked identically to what Drain mines).
_MASK_PIPELINE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), f"<{token}>") for token, pattern in MASKING_RULES
]

# §2.2 Drain parameters.
DEFAULT_SIM_TH = 0.4
DEFAULT_DEPTH = 4
DEFAULT_MAX_CHILDREN = 100
DEFAULT_MAX_CLUSTERS = 4096
DEFAULT_EXTRA_DELIMITERS: list[str] = ["_", "|"]
# Only the first N lines of a message are template-mined (§2.2).
DEFAULT_MAX_LINES = 40


def mask_text(text: str) -> str:
    """Apply the §2.1 masking pipeline to arbitrary text (line-agnostic)."""
    result = text
    for pattern, replacement in _MASK_PIPELINE:
        result = pattern.sub(replacement, result)
    return result


def build_config(
    *,
    sim_th: float = DEFAULT_SIM_TH,
    depth: int = DEFAULT_DEPTH,
    max_children: int = DEFAULT_MAX_CHILDREN,
    max_clusters: int = DEFAULT_MAX_CLUSTERS,
    extra_delimiters: Sequence[str] = tuple(DEFAULT_EXTRA_DELIMITERS),
) -> TemplateMinerConfig:
    """Build a :class:`TemplateMinerConfig` with the §2.1/§2.2 settings."""
    config = TemplateMinerConfig()
    config.drain_sim_th = sim_th
    config.drain_depth = depth
    config.drain_max_children = max_children
    config.drain_max_clusters = max_clusters
    config.drain_extra_delimiters = list(extra_delimiters)
    config.snapshot_interval_minutes = 1
    config.snapshot_compress_state = False
    config.masking_instructions = [
        MaskingInstruction(pattern, mask_with) for mask_with, pattern in MASKING_RULES
    ]
    return config


def config_summary(config: TemplateMinerConfig) -> dict:
    """JSON-serializable summary of the Drain config, stored alongside the CAS state."""
    return {
        "sim_th": config.drain_sim_th,
        "depth": config.drain_depth,
        "max_children": config.drain_max_children,
        "max_clusters": config.drain_max_clusters,
        "extra_delimiters": list(config.drain_extra_delimiters),
        "masking": [token for token, _ in MASKING_RULES],
    }


@dataclass(frozen=True, slots=True)
class TemplateHit:
    """One mined template for one message line."""

    cluster_id: int
    template: str
    token_count: int
    hash_signed: int  # bigint-safe identity for storage (spec 02 log_template)
    hash_hex: str  # unsigned hex id used in the signature TEMPLATES field (§3.1)


class _MemoryPersistence(PersistenceHandler):
    """In-memory persistence handler so we control (de)serialization for CAS."""

    def __init__(self) -> None:
        self.state: bytes | None = None

    def save_state(self, state: bytes) -> None:
        self.state = state

    def load_state(self) -> bytes | None:
        return self.state


def _template_hash_hex(template: str) -> str:
    return format(xxh3_64_unsigned(template), "016x")


class DrainManager:
    """A single project's Drain3 miner plus stable-identity helpers."""

    def __init__(
        self, config: TemplateMinerConfig | None = None, *, max_lines: int = DEFAULT_MAX_LINES
    ) -> None:
        self._config = config or build_config()
        self._persistence = _MemoryPersistence()
        self._miner = TemplateMiner(persistence_handler=self._persistence, config=self._config)
        self.max_lines = max_lines

    @property
    def config(self) -> TemplateMinerConfig:
        return self._config

    # -- persistence -------------------------------------------------------
    def serialize(self) -> bytes:
        """Serialize current Drain state (drain3's own codec) for the CAS blob."""
        self._miner.save_state("analyzer-ng snapshot")
        assert self._persistence.state is not None
        return self._persistence.state

    def load_state(self, state: bytes) -> None:
        """Restore Drain state from a previously serialized CAS blob."""
        self._persistence.state = state
        self._miner.load_state()

    def rebuild_from_templates(self, template_texts: Iterable[str]) -> int:
        """Warm an empty tree by replaying stored template texts (§2.3 state-loss).

        Templates are replayed most-frequent-first by the caller. Identity is
        unaffected because it is the template hash, not the tree shape.
        """
        count = 0
        for text in template_texts:
            if text and text.strip():
                self._miner.add_log_message(text)
                count += 1
        logger.warning("Rebuilt Drain tree from %d stored templates (state loss)", count)
        return count

    # -- mining ------------------------------------------------------------
    def _minable_lines(self, cleaned_message: str) -> list[str]:
        lines: list[str] = []
        for line in cleaned_message.split("\n")[: self.max_lines]:
            if not line.strip():
                continue
            if is_line_from_stacktrace(line):
                continue  # frames feed the exception fingerprint (§3.2), not Drain
            lines.append(line)
        return lines

    def add_message(self, cleaned_message: str) -> list[TemplateHit]:
        """Feed a cleaned message to Drain line-by-line; return the per-line hits."""
        hits: list[TemplateHit] = []
        for line in self._minable_lines(cleaned_message):
            result = self._miner.add_log_message(line)
            template = result["template_mined"]
            hits.append(
                TemplateHit(
                    cluster_id=int(result["cluster_id"]),
                    template=template,
                    token_count=len(template.split()),
                    hash_signed=xxh3_64_signed(template),
                    hash_hex=_template_hash_hex(template),
                )
            )
        return hits

    def match_message(self, cleaned_message: str) -> list[str]:
        """Return the template hashes a message matches *without* mutating the tree.

        Used by the rebuild-recovery check: after a rebuild, previously seen
        messages must map back to their existing template hashes.
        """
        hashes: list[str] = []
        for line in self._minable_lines(cleaned_message):
            cluster = self._miner.match(line)
            if cluster is not None:
                hashes.append(_template_hash_hex(cluster.get_template()))
        return hashes

    def cluster_mirror_rows(self) -> list[dict]:
        """Snapshot of all live clusters for ``Drain3StateStore.upsert_templates``.

        The mirror row identity (``template_id`` = the ``log_template`` PK) is the
        **content hash** ``xxh3_64(masked_template_text)`` — signed for the bigint
        column — **not** Drain's ``cluster_id`` (spec 03 §2.3). Drain cluster ids
        are volatile: LRU eviction / state rebuild reassign them, so keying the
        mirror on ``cluster_id`` would fragment or collide rows for the same
        template. Hashing the template text keeps identity stable across evictions
        and rebuilds, and a widened (re-generalized) template naturally becomes a
        new hash → a new row, per §2.3. ``template_hash`` is emitted redundantly to
        make the identity explicit at the call site (spec 02's ``template_hash`` is
        a signed-bigint content hash, §2 "IDs").
        """
        rows: list[dict] = []
        for cluster in self._miner.drain.clusters:
            template = cluster.get_template()
            template_hash = xxh3_64_signed(template)
            rows.append(
                {
                    "template_id": template_hash,
                    "template_hash": template_hash,
                    "pattern": template,
                    "token_count": len(template.split()),
                    "example": template,
                    "match_count": int(cluster.size),
                }
            )
        return rows


# --- store integration (spec 02 Drain3StateStore CAS) --------------------


def load_manager(
    store,
    project_id: int,
    *,
    config: TemplateMinerConfig | None = None,
    template_loader: Callable[[int], list[str]] | None = None,
) -> tuple[DrainManager, int]:
    """Load a project's :class:`DrainManager` and the CAS version to save against.

    Tries the serialized state first; on missing/undeserializable state it
    rebuilds from stored template texts (via ``template_loader``) and logs a
    WARN. Returns ``(manager, expected_version)`` — pass the version back to
    :func:`save_manager`.
    """
    manager = DrainManager(config=config)
    loaded = store.load(project_id)
    if loaded is not None:
        state, version = loaded
        try:
            manager.load_state(state)
            return manager, version
        except Exception:  # noqa: BLE001 — any deserialization failure triggers rebuild
            logger.warning(
                "Drain state for project %s failed to deserialize; rebuilding", project_id
            )
            manager = DrainManager(config=config)
            if template_loader is not None:
                manager.rebuild_from_templates(template_loader(project_id))
            return manager, version
    # No state row yet.
    if template_loader is not None:
        manager.rebuild_from_templates(template_loader(project_id))
    return manager, 0


def save_manager(store, project_id: int, manager: DrainManager, expected_version: int) -> bool:
    """Persist the CAS state blob and mirror the clusters into ``log_template``.

    Returns the CAS result: ``False`` means another worker won the race and the
    caller should reload, replay and retry.
    """
    saved = store.save(
        project_id, manager.serialize(), expected_version, config_summary(manager.config)
    )
    if saved:
        store.upsert_templates(project_id, manager.cluster_mirror_rows())
    return saved
