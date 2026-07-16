"""Seed-KB startup loader + lazy per-project persistence (spec 03 §9, spec 01 §6).

Two responsibilities, one small surface:

* **Startup load (idempotent)** — :func:`load_seed_kb` validates the packaged
  catalog and logs its size. It writes nothing, so re-running it on every start
  (spec 01 §6 step 6) can never create duplicates. Wired into the startup
  sequence by ``AnalyzerService.load_seed_kb``.
* **Lazy per-project copies** — the catalog itself is the global template (package
  data, not a DB row, since ``failure_mode.project_id`` is ``NOT NULL``). On the
  first match in a project, :meth:`SeedKB.match_and_seed` materializes one
  ``failure_mode`` row via ``KBStore.ensure_seed_mode`` — idempotent under
  concurrent workers.

:class:`SeedKB` is the clean API T2.3 calls: pure :meth:`SeedKB.match` for the
feature signal, or :meth:`SeedKB.match_and_seed` when a project copy is wanted.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from analyzer_ng.db.repositories.models import ModeIn
from analyzer_ng.db.repositories.protocols import KBStore
from analyzer_ng.seeds.catalog import SeedCatalog, SeedMode, default_catalog

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedHit:
    """Result of matching a signature to a seed mode and persisting its copy."""

    mode_key: str
    mode_id: int
    label: str
    confidence: float


class SeedKB:
    """Startup-loaded catalog plus optional per-project persistence.

    ``kb_store`` is optional: startup validation and the pure feature-signal path
    (:meth:`match`) need no database; only :meth:`match_and_seed` /
    :meth:`ensure_project_copy` require it.
    """

    def __init__(self, catalog: SeedCatalog, kb_store: KBStore | None = None) -> None:
        self._catalog = catalog
        self._kb_store = kb_store

    @property
    def catalog(self) -> SeedCatalog:
        return self._catalog

    def match(
        self,
        exc_classes: Sequence[str] = (),
        msg_text: str = "",
        template_texts: Sequence[str] = (),
    ) -> SeedMode | None:
        """Pure classify — no DB write (the ``seed_mode_matched`` feature signal)."""
        return self._catalog.match(exc_classes, msg_text, template_texts)

    def ensure_project_copy(self, project_id: int, mode: SeedMode) -> int:
        """Materialize (idempotently) this project's copy of a catalog mode."""
        if self._kb_store is None:
            raise RuntimeError("SeedKB has no KBStore; cannot create per-project copy")
        mode_in = ModeIn(
            project_id=project_id,
            status="candidate",  # spec 03 §9: per-project copy starts as a candidate
            label=mode.prior_label,
            label_source="seed",
            title=mode.title,
            centroid=None,  # set from first matched items later (spec 03 §9)
            emb_model_ver=None,
        )
        return self._kb_store.ensure_seed_mode(project_id, mode.mode_key, mode_in)

    def match_and_seed(
        self,
        project_id: int,
        exc_classes: Sequence[str] = (),
        msg_text: str = "",
        template_texts: Sequence[str] = (),
    ) -> SeedHit | None:
        """Classify and, on a hit, ensure the project's lazy copy exists."""
        mode = self._catalog.match(exc_classes, msg_text, template_texts)
        if mode is None:
            return None
        mode_id = self.ensure_project_copy(project_id, mode)
        return SeedHit(
            mode_key=mode.mode_key,
            mode_id=mode_id,
            label=mode.prior_label,
            confidence=mode.prior_confidence,
        )


def load_seed_kb(kb_store: KBStore | None = None) -> SeedKB:
    """Load & validate the packaged catalog (spec 01 §6 step 6). Idempotent.

    Raises :class:`~analyzer_ng.seeds.catalog.SeedCatalogError` if the catalog is
    invalid — a hard startup failure, by design (a broken KB must not boot).
    """
    catalog = default_catalog()
    logger.info("Seed KB loaded: %d failure modes", len(catalog))
    return SeedKB(catalog, kb_store)
