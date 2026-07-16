"""Application configuration (spec 01 §5).

`AppConfig` is a `pydantic-settings` model; every var is read once at startup
into a frozen instance. Precedence is process env > ``.env`` (dev only) > coded
default. Env-var names are kept legacy-compatible so an unmodified ReportPortal
compose block can start this service unchanged.

Use :func:`load_config` as the startup entry point: it emits a one-line WARN for
each ignored legacy Elasticsearch/datastore var and exits with code 2 on invalid
configuration (spec §5.3).
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

from pydantic import BeforeValidator, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Legacy `to_bool` accepted forms (spec §5). Anything else is a validation error.
_BOOL_TRUE = frozenset({"TRUE", "True", "true", "1", "Y", "y"})
_BOOL_FALSE = frozenset({"FALSE", "False", "false", "0", "N", "n"})

# Env-var prefixes for datastore vars that legacy operators may still set. They
# are accepted-but-ignored (WARN) so dropping this image into an old env block
# does not crash-loop. Covers ES_HOSTS, ES_USER, ES_BOOST_*, DATASTORE_*, MINIO_*.
_LEGACY_IGNORED_PREFIXES = ("ES_", "DATASTORE_", "MINIO_")


def _parse_bool(value: object) -> bool:
    """Parse a legacy-style boolean; reject anything outside the accepted set."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip()
        if token in _BOOL_TRUE:
            return True
        if token in _BOOL_FALSE:
            return False
    raise ValueError(
        f"invalid boolean {value!r}: expected one of "
        "TRUE/True/true/1/Y/y or FALSE/False/false/0/N/n"
    )


# A bool that only accepts the legacy `to_bool` spellings.
LegacyBool = Annotated[bool, BeforeValidator(_parse_bool)]

# A float threshold constrained to the closed unit interval [0, 1] (spec §5.3).
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]


class AppConfig(BaseSettings):
    """Frozen settings model for analyzer-ng (spec 01 §5.1–§5.3).

    Fields are named as the lowercase of their env var, so pydantic-settings
    maps e.g. ``AMQP_URL`` -> ``amqp_url`` case-insensitively.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # ------------------------------------------------------------------ #
    # §5.1 Legacy-compatible vars (names MUST stay).
    # ------------------------------------------------------------------ #
    amqp_url: str = Field(description="AMQP broker URL; trailing slashes stripped")
    amqp_virtual_host: str = "analyzer"
    amqp_exchange_name: str = "analyzer"
    amqp_heartbeat_interval: int = 30
    amqp_initial_retry_interval: int = 1
    amqp_max_retry_time: int = 300
    amqp_backoff_factor: int = 2
    amqp_handler_max_retries: int = 3
    amqp_handler_task_timeout: int = 600
    analyzer_priority: int = 1
    analyzer_index: LegacyBool = True
    analyzer_log_search: LegacyBool = True
    analyzer_suggest: LegacyBool = True
    analyzer_cluster: LegacyBool = True
    analyzer_http_port: int = 5001
    analyzer_file_logging_path: str = "/tmp/config.log"
    logging_level: str = "INFO"
    debug_mode: LegacyBool = False

    # ------------------------------------------------------------------ #
    # §5.2 New analyzer-ng vars.
    # ------------------------------------------------------------------ #
    analyzer_pg_dsn: str = ""
    analyzer_pg_host: str = "postgres"
    analyzer_pg_port: int = 5432
    analyzer_pg_user: str | None = None
    analyzer_pg_password: str | None = None
    analyzer_pg_db: str = "analyzer"
    analyzer_pg_schema: str = "analyzer"
    analyzer_pg_pool_min: int = 2
    analyzer_pg_pool_max: int = 10
    analyzer_pg_create_db: LegacyBool = True
    analyzer_pg_admin_dsn: str = ""
    analyzer_ng_workers: int = 2
    analyzer_ng_prefetch: int = 1
    analyzer_ng_queue_prefix: str = "analyzer-ng."
    analyzer_ng_queue_size: int = 100
    analyzer_emb_model_path: str = "/opt/analyzer/models/e5-small-int8"
    analyzer_emb_dims: int = 384
    analyzer_auto_min_prob: UnitInterval = 0.6
    analyzer_suggest_max: int = 3
    analyzer_burst_si_share: UnitInterval = 0.5
    analyzer_time_decay: UnitInterval = 0.999
    analyzer_llm_enabled: LegacyBool = False
    ollama_url: str = "http://ollama:11434"
    analyzer_llm_model: str = "qwen3:4b"
    analyzer_llm_judge_tau: UnitInterval = 0.5
    analyzer_seed_kb_path: str = "/opt/analyzer/seeds/failure_modes.json"

    @field_validator("amqp_url")
    @classmethod
    def _strip_trailing_slashes(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _require_derivable_pg_dsn(self) -> AppConfig:
        """A DSN must be set, or discrete user+password must allow deriving one."""
        if not self.analyzer_pg_dsn.strip() and not (
            self.analyzer_pg_user and self.analyzer_pg_password
        ):
            raise ValueError(
                "no PostgreSQL DSN derivable: set ANALYZER_PG_DSN, or set "
                "ANALYZER_PG_USER and ANALYZER_PG_PASSWORD (with optional "
                "ANALYZER_PG_HOST/_PORT/_DB)"
            )
        return self

    @property
    def pg_dsn_effective(self) -> str:
        """The DSN to connect with: explicit ``ANALYZER_PG_DSN`` wins, else derived."""
        if self.analyzer_pg_dsn.strip():
            return self.analyzer_pg_dsn
        return (
            f"postgresql://{self.analyzer_pg_user}:{self.analyzer_pg_password}"
            f"@{self.analyzer_pg_host}:{self.analyzer_pg_port}/{self.analyzer_pg_db}"
        )


def _warn_ignored_legacy_env() -> None:
    """Emit one WARN per set legacy ES/datastore env var (spec §5.1)."""
    for name in sorted(os.environ):
        if name.startswith(_LEGACY_IGNORED_PREFIXES):
            logger.warning(
                "Ignoring legacy env var %s: not used by analyzer-ng "
                "(Elasticsearch/datastore layer removed)",
                name,
            )


def load_config() -> AppConfig:
    """Load, validate, and return the frozen :class:`AppConfig` (spec §5.3).

    Warns-and-ignores legacy datastore env vars, then constructs the settings
    from the process environment. On invalid configuration it logs a clear
    message and exits the process with code 2 (fail fast).
    """
    _warn_ignored_legacy_env()
    try:
        # Values come from the environment; mypy can't see that and treats the
        # env-sourced fields as required positional args.
        return AppConfig()  # type: ignore[call-arg]
    except ValidationError as exc:
        logger.error("Invalid analyzer-ng configuration:\n%s", exc)
        raise SystemExit(2) from exc
