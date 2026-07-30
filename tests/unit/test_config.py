"""Unit tests for the analyzer-ng configuration module (spec 01 §5).

Covers the acceptance criteria for task T0.3:
- defaults match the spec env table,
- env overrides win,
- invalid values fail fast with a clear message,
- legacy ES/datastore vars are accepted-but-ignored with a WARN.
"""

from __future__ import annotations

import logging
import os
import pathlib

import pytest
from pydantic import ValidationError

from analyzer_ng.config import AppConfig, load_config

# Every env var the settings model may read, grouped by spec section. Used to
# isolate each test from whatever the developer/CI shell happens to export.
_ENV_PREFIXES = (
    "AMQP_",
    "ANALYZER_",
    "LOGGING_",
    "DEBUG_",
    "OLLAMA_",
    "ES_",
    "DATASTORE_",
    "MINIO_",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Strip inherited config env vars so tests see spec defaults."""
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set only the required vars so AppConfig() constructs cleanly."""
    monkeypatch.setenv("AMQP_URL", "amqp://rabbitmq:5672")
    monkeypatch.setenv("ANALYZER_PG_DSN", "postgresql://u:p@postgres:5432/analyzer")


def _cfg() -> AppConfig:
    # _env_file=None keeps the model hermetic (never reads a dev .env).
    return AppConfig(_env_file=None)


# --------------------------------------------------------------------------- #
# Defaults (spec §5.1 / §5.2 tables)
# --------------------------------------------------------------------------- #


def test_defaults_match_spec(env: pytest.MonkeyPatch) -> None:
    _minimal(env)
    cfg = _cfg()

    # §5.1 legacy-compatible vars
    assert cfg.amqp_url == "amqp://rabbitmq:5672"
    assert cfg.amqp_virtual_host == "analyzer"
    assert cfg.amqp_exchange_name == "analyzer"
    assert cfg.amqp_heartbeat_interval == 30
    assert cfg.amqp_initial_retry_interval == 1
    assert cfg.amqp_max_retry_time == 300
    assert cfg.amqp_backoff_factor == 2
    assert cfg.amqp_handler_max_retries == 3
    assert cfg.amqp_handler_task_timeout == 600
    assert cfg.analyzer_priority == 1
    assert cfg.analyzer_index is True
    assert cfg.analyzer_log_search is True
    assert cfg.analyzer_suggest is True
    assert cfg.analyzer_cluster is True
    assert cfg.analyzer_http_port == 5001
    assert cfg.analyzer_file_logging_path == "/tmp/config.log"
    assert cfg.logging_level == "INFO"
    assert cfg.debug_mode is False

    # §5.2 new analyzer-ng vars
    assert cfg.analyzer_pg_host == "postgres"
    assert cfg.analyzer_pg_port == 5432
    assert cfg.analyzer_pg_db == "analyzer"
    assert cfg.analyzer_pg_schema == "analyzer"
    assert cfg.analyzer_pg_pool_min == 2
    assert cfg.analyzer_pg_pool_max == 10
    assert cfg.analyzer_pg_create_db is True
    assert cfg.analyzer_pg_admin_dsn == ""
    assert cfg.analyzer_ng_workers == 2
    assert cfg.analyzer_ng_prefetch == 1
    assert cfg.analyzer_ng_queue_prefix == "analyzer-ng."
    assert cfg.analyzer_ng_queue_size == 100
    assert cfg.analyzer_emb_model_path == "/opt/analyzer/models/e5-small-int8"
    assert cfg.analyzer_emb_dims == 384
    # Defaults equal the code constants they feed (wired knobs, behavior unchanged).
    assert cfg.analyzer_auto_min_prob == 0.75
    assert cfg.analyzer_suggest_max == 3
    assert cfg.analyzer_burst_si_share == 0.4
    assert cfg.analyzer_time_decay == 2.0 ** (-1.0 / 90.0)
    assert cfg.analyzer_llm_enabled is False
    assert cfg.ollama_url == "http://ollama:11434"
    assert cfg.analyzer_llm_model == "qwen3:4b-q4_K_M"
    assert cfg.analyzer_llm_judge_tau == 0.75
    assert cfg.analyzer_llm_api == "ollama"
    assert cfg.analyzer_llm_timeout_s == 20
    assert cfg.analyzer_llm_queue_max == 500
    assert cfg.analyzer_llm_num_ctx == 4096
    # Removed knobs are no longer config attributes (drain_sim_th pinned to a
    # constant; seed_kb_path is packaged data via importlib.resources).
    assert not hasattr(cfg, "analyzer_seed_kb_path")
    assert not hasattr(cfg, "analyzer_drain_sim_th")


def test_wired_defaults_equal_code_constants() -> None:
    # The newly-wired knobs must default to the exact code constants they feed, so
    # default behavior is byte-identical to the pre-wiring build (finding #4).
    from analyzer_ng.core.decision import TAU_AUTO
    from analyzer_ng.core.features import TIME_DECAY_PER_DAY
    from analyzer_ng.core.grouping import BURST_X

    cfg = AppConfig(
        amqp_url="amqp://guest:guest@localhost/",
        analyzer_pg_dsn="postgresql://u:p@localhost/analyzer",
    )  # type: ignore[call-arg]
    assert cfg.analyzer_auto_min_prob == TAU_AUTO
    assert cfg.analyzer_suggest_max == 3  # analysis.SUGGEST_MAX
    assert cfg.analyzer_suggest_below_enabled is False  # dock off until Bench ng2+
    assert cfg.analyzer_suggest_below_max == 2  # analysis.SUGGEST_BELOW_MAX
    assert cfg.analyzer_burst_si_share == BURST_X
    assert cfg.analyzer_time_decay == TIME_DECAY_PER_DAY
    assert cfg.analyzer_drain_max_lines == 40  # ml.drain.DEFAULT_MAX_LINES


# --------------------------------------------------------------------------- #
# Overrides + precedence
# --------------------------------------------------------------------------- #


def test_env_overrides_defaults(env: pytest.MonkeyPatch) -> None:
    _minimal(env)
    env.setenv("ANALYZER_NG_WORKERS", "8")
    env.setenv("LOGGING_LEVEL", "DEBUG")
    env.setenv("ANALYZER_HTTP_PORT", "9999")
    env.setenv("ANALYZER_AUTO_MIN_PROB", "0.75")
    cfg = _cfg()
    assert cfg.analyzer_ng_workers == 8
    assert cfg.logging_level == "DEBUG"
    assert cfg.analyzer_http_port == 9999
    assert cfg.analyzer_auto_min_prob == 0.75


def test_amqp_url_trailing_slashes_stripped(env: pytest.MonkeyPatch) -> None:
    _minimal(env)
    env.setenv("AMQP_URL", "amqp://rabbitmq:5672///")
    assert _cfg().amqp_url == "amqp://rabbitmq:5672"


# --------------------------------------------------------------------------- #
# Boolean parsing (legacy to_bool: spec §5)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["TRUE", "True", "true", "1", "Y", "y"])
def test_bool_true_forms(env: pytest.MonkeyPatch, value: str) -> None:
    _minimal(env)
    env.setenv("ANALYZER_LLM_ENABLED", value)
    assert _cfg().analyzer_llm_enabled is True


@pytest.mark.parametrize("value", ["FALSE", "False", "false", "0", "N", "n"])
def test_bool_false_forms(env: pytest.MonkeyPatch, value: str) -> None:
    _minimal(env)
    env.setenv("ANALYZER_INDEX", value)
    assert _cfg().analyzer_index is False


def test_bool_invalid_value_fails_with_clear_message(env: pytest.MonkeyPatch) -> None:
    _minimal(env)
    env.setenv("DEBUG_MODE", "maybe")
    with pytest.raises(ValidationError) as exc:
        _cfg()
    msg = str(exc.value)
    assert "debug_mode" in msg.lower()
    assert "maybe" in msg


# --------------------------------------------------------------------------- #
# Fail-fast validation (spec §5.3)
# --------------------------------------------------------------------------- #


def test_missing_amqp_url_is_required(env: pytest.MonkeyPatch) -> None:
    env.setenv("ANALYZER_PG_DSN", "postgresql://u:p@postgres:5432/analyzer")
    with pytest.raises(ValidationError) as exc:
        _cfg()
    assert "amqp_url" in str(exc.value).lower()


def test_no_pg_dsn_derivable_fails(env: pytest.MonkeyPatch) -> None:
    env.setenv("AMQP_URL", "amqp://rabbitmq:5672")
    # No DSN and no discrete user/password -> not derivable.
    with pytest.raises(ValidationError) as exc:
        _cfg()
    assert "dsn" in str(exc.value).lower()


def test_discrete_pg_vars_derive_dsn(env: pytest.MonkeyPatch) -> None:
    env.setenv("AMQP_URL", "amqp://rabbitmq:5672")
    env.setenv("ANALYZER_PG_USER", "rpuser")
    env.setenv("ANALYZER_PG_PASSWORD", "rppass")
    env.setenv("ANALYZER_PG_HOST", "db")
    env.setenv("ANALYZER_PG_PORT", "6543")
    env.setenv("ANALYZER_PG_DB", "reports")
    cfg = _cfg()
    assert cfg.pg_dsn_effective == "postgresql://rpuser:rppass@db:6543/reports"


def test_derived_dsn_url_escapes_credentials(env: pytest.MonkeyPatch) -> None:
    # A password with URL-significant chars must not corrupt the DSN structure.
    env.setenv("AMQP_URL", "amqp://rabbitmq:5672")
    env.setenv("ANALYZER_PG_USER", "user@corp")
    env.setenv("ANALYZER_PG_PASSWORD", "p@ss:w/rd?x")
    env.setenv("ANALYZER_PG_HOST", "db")
    env.setenv("ANALYZER_PG_PORT", "5432")
    env.setenv("ANALYZER_PG_DB", "analyzer")
    dsn = _cfg().pg_dsn_effective
    assert dsn == "postgresql://user%40corp:p%40ss%3Aw%2Frd%3Fx@db:5432/analyzer"

    # psycopg parses it back to the exact original credentials.
    from psycopg.conninfo import conninfo_to_dict

    parsed = conninfo_to_dict(dsn)
    assert parsed["user"] == "user@corp"
    assert parsed["password"] == "p@ss:w/rd?x"
    assert parsed["host"] == "db"
    assert parsed["dbname"] == "analyzer"


def test_explicit_dsn_wins_over_discrete_vars(env: pytest.MonkeyPatch) -> None:
    env.setenv("AMQP_URL", "amqp://rabbitmq:5672")
    env.setenv("ANALYZER_PG_DSN", "postgresql://a:b@h:5432/main")
    env.setenv("ANALYZER_PG_USER", "ignored")
    env.setenv("ANALYZER_PG_PASSWORD", "ignored")
    assert _cfg().pg_dsn_effective == "postgresql://a:b@h:5432/main"


def test_threshold_out_of_range_fails(env: pytest.MonkeyPatch) -> None:
    _minimal(env)
    env.setenv("ANALYZER_AUTO_MIN_PROB", "1.5")
    with pytest.raises(ValidationError) as exc:
        _cfg()
    assert "analyzer_auto_min_prob" in str(exc.value).lower()


def test_unparsable_numeric_fails(env: pytest.MonkeyPatch) -> None:
    _minimal(env)
    env.setenv("ANALYZER_NG_WORKERS", "lots")
    with pytest.raises(ValidationError) as exc:
        _cfg()
    assert "analyzer_ng_workers" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# Immutability
# --------------------------------------------------------------------------- #


def test_config_is_frozen(env: pytest.MonkeyPatch) -> None:
    _minimal(env)
    cfg = _cfg()
    with pytest.raises(ValidationError):
        cfg.analyzer_ng_workers = 99  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Legacy ES/datastore vars: accepted-but-ignored with a WARN (spec §5.1)
# --------------------------------------------------------------------------- #


def test_legacy_es_vars_warn_and_are_ignored(
    env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path
) -> None:
    _minimal(env)
    env.setenv("ANALYZER_EMB_MODEL_PATH", str(tmp_path))
    env.setenv("ES_HOSTS", "http://elasticsearch:9200")
    env.setenv("ES_USER", "elastic")
    env.setenv("ES_BOOST_AA", "2.0")
    env.setenv("DATASTORE_TYPE", "minio")
    env.setenv("MINIO_ENDPOINT", "minio:9000")
    with caplog.at_level(logging.WARNING, logger="analyzer_ng.config"):
        cfg = load_config()
    # Config still loads successfully.
    assert cfg.amqp_url == "amqp://rabbitmq:5672"
    warned = {r.getMessage() for r in caplog.records if r.levelno == logging.WARNING}
    joined = "\n".join(warned)
    for name in ("ES_HOSTS", "ES_USER", "ES_BOOST_AA", "DATASTORE_TYPE", "MINIO_ENDPOINT"):
        assert name in joined
    # One WARN line per ignored var.
    assert len(warned) == 5


def test_no_warn_when_no_legacy_vars(
    env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path
) -> None:
    _minimal(env)
    env.setenv("ANALYZER_EMB_MODEL_PATH", str(tmp_path))
    with caplog.at_level(logging.WARNING, logger="analyzer_ng.config"):
        load_config()
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


# --------------------------------------------------------------------------- #
# load_config(): fail fast with exit code 2 (spec §5.3)
# --------------------------------------------------------------------------- #


def test_load_config_exits_2_on_invalid(env: pytest.MonkeyPatch) -> None:
    env.setenv("ANALYZER_PG_DSN", "postgresql://u:p@postgres:5432/analyzer")
    # AMQP_URL missing -> invalid.
    with pytest.raises(SystemExit) as exc:
        load_config()
    assert exc.value.code == 2


def test_load_config_exits_2_when_model_path_missing(
    env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path
) -> None:
    _minimal(env)
    missing = tmp_path / "no-such-model-dir"
    env.setenv("ANALYZER_EMB_MODEL_PATH", str(missing))
    with caplog.at_level(logging.ERROR, logger="analyzer_ng.config"):
        with pytest.raises(SystemExit) as exc:
            load_config()
    assert exc.value.code == 2
    joined = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert "ANALYZER_EMB_MODEL_PATH" in joined
    assert str(missing) in joined


def test_load_config_accepts_existing_model_path(
    env: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    _minimal(env)
    env.setenv("ANALYZER_EMB_MODEL_PATH", str(tmp_path))
    cfg = load_config()
    assert cfg.analyzer_emb_model_path == str(tmp_path)


def test_early_item_analysis_defaults_off(env: pytest.MonkeyPatch) -> None:
    # docs/EARLY-ITEM-AA.md: ships dark — flag off, deterministic-only policy.
    _minimal(env)
    cfg = _cfg()
    assert cfg.analyzer_early_item_analysis is False
    assert cfg.analyzer_early_aa_label_policy == "kb_inherit_only"


def test_early_item_analysis_env_overrides(env: pytest.MonkeyPatch) -> None:
    _minimal(env)
    env.setenv("ANALYZER_EARLY_ITEM_ANALYSIS", "true")
    env.setenv("ANALYZER_EARLY_AA_LABEL_POLICY", "suggest_only")
    cfg = _cfg()
    assert cfg.analyzer_early_item_analysis is True
    assert cfg.analyzer_early_aa_label_policy == "suggest_only"


def test_early_aa_label_policy_rejects_unknown_value(env: pytest.MonkeyPatch) -> None:
    _minimal(env)
    env.setenv("ANALYZER_EARLY_AA_LABEL_POLICY", "yolo")
    with pytest.raises(ValidationError):
        _cfg()


def test_early_pb_gbm_policy_and_threshold(env: pytest.MonkeyPatch) -> None:
    # v2 (docs/EARLY-ITEM-AA.md): pb-only GBM early labeling behind its own
    # policy value and a stricter-than-auto confidence bar.
    _minimal(env)
    cfg = _cfg()
    assert cfg.analyzer_early_gbm_pb_min == 0.85
    env.setenv("ANALYZER_EARLY_AA_LABEL_POLICY", "kb_inherit_and_pb")
    env.setenv("ANALYZER_EARLY_GBM_PB_MIN", "0.9")
    cfg = _cfg()
    assert cfg.analyzer_early_aa_label_policy == "kb_inherit_and_pb"
    assert cfg.analyzer_early_gbm_pb_min == 0.9
