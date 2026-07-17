"""Startup embedder wiring (spec 01 §6 step 5 / spec 03 §4).

Regression guard for the T5.2 finding: the ONNX embedder was never constructed in
the production path, so ``handlers.bind`` received ``embedder=None`` and retrieval
silently degraded to lexical-only (``emb=none``, ``top1_cosine=0``) even with a
model on disk. These prove the service now constructs the embedder and threads it
(plus its numeric version + tag) through to the pipeline, and soft-degrades — never
crashes — when the model cannot load.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from analyzer_ng.config import AppConfig
from analyzer_ng.metrics import Metrics
from analyzer_ng.service import AnalyzerService


def _config(**overrides: object) -> AppConfig:
    base = dict(
        amqp_url="amqp://guest:guest@localhost/",
        analyzer_pg_dsn="postgresql://u:p@localhost/analyzer",
    )
    base.update(overrides)
    return AppConfig(**base)  # type: ignore[arg-type]


class _FakeEmbedder:
    emb_model_ver = "e5s-int8-rdeadbeef"
    dims = 384

    def embed(self, text: str) -> np.ndarray:
        return np.zeros(self.dims, dtype=np.float32)


class _SpyHandlers:
    """Records the kwargs the service passes to ``bind`` (nothing else runs)."""

    def __init__(self) -> None:
        self.bind_kwargs: dict = {}

    def bind(self, pool: object, **kwargs: object) -> None:
        self.bind_kwargs = {"pool": pool, **kwargs}


def _service_with_spy(
    config: AppConfig, *, embedder: object | None
) -> tuple[_SpyHandlers, AnalyzerService]:
    svc = AnalyzerService(config, "v1", metrics=Metrics(), embedder=embedder)
    spy = _SpyHandlers()
    svc._handlers = spy  # type: ignore[assignment]
    svc.set_pg_pool(object())  # fake pool: bind is spied, no queries run
    return spy, svc


def test_startup_wires_constructed_embedder_into_handlers() -> None:
    emb = _FakeEmbedder()
    spy, svc = _service_with_spy(_config(), embedder=emb)
    # The embedder (and its numeric version + tag) reaches the pipeline — not None.
    assert spy.bind_kwargs["embedder"] is emb
    assert spy.bind_kwargs["emb_model_ver"] == 1  # EMB_MODEL_VERSION (non-zero)
    assert spy.bind_kwargs["emb_model_tag"] == "e5s-int8-rdeadbeef"
    # /health now reports the real embedding version once the pool is bound.
    assert svc.emb_model_ver == "e5s-int8-rdeadbeef"


def test_startup_soft_degrades_when_model_unloadable(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    # Model directory exists (config fail-fast covers absence) but has no model.onnx
    # → the ONNX session fails to load → soft-degrade to lexical-only, never crash.
    config = _config(analyzer_emb_model_path=str(tmp_path))
    with caplog.at_level(logging.ERROR):
        spy, svc = _service_with_spy(config, embedder=None)
    assert spy.bind_kwargs["embedder"] is None
    assert spy.bind_kwargs["emb_model_ver"] == 0  # not embedded
    assert spy.bind_kwargs["emb_model_tag"] == "none"
    assert svc.emb_model_ver is None  # /health honestly reports no dense retrieval
    assert "LEXICAL-ONLY" in caplog.text
