"""Settings. Fail-closed if anything looks like exchange credentials (ADR-0002/0004)."""

from __future__ import annotations

import os
from datetime import timedelta

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_FORBIDDEN_SUFFIXES = ("_API_KEY", "_API_SECRET", "_SECRET_KEY", "_PASSPHRASE", "_SECRET")
_FORBIDDEN_PREFIXES = ("BINANCE_", "OKX_")


class CredentialsPresent(RuntimeError):
    pass


def assert_no_exchange_credentials(environ: dict[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    bad = sorted(
        k
        for k in env
        if k.upper().endswith(_FORBIDDEN_SUFFIXES) and k.upper().startswith(_FORBIDDEN_PREFIXES)
    )
    if bad:
        raise CredentialsPresent(
            "exchange credential variables are forbidden in M0: " + ", ".join(bad)
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASTA_", extra="ignore")

    database_url: str = "postgresql+pg8000://asta:asta_dev_only@127.0.0.1:5640/asta"
    session_ttl_minutes: int = Field(default=30, ge=1, le=480)
    cookie_secure: bool = False  # True behind TLS; local dev is http://localhost
    stale_after_seconds: int = Field(default=15, ge=1)
    max_clock_drift_seconds: int = Field(default=2, ge=1)
    enable_market_ingest: bool = True
    allowed_origins: str = "http://localhost:3640"
    data_dir: str = "data/store"  # content-addressed dataset files (local)
    import_dir: str = "data/import"  # LOCAL files the research importer may read (names only)
    worker_queue_size: int = Field(default=4, ge=1, le=32)

    @property
    def stale_after(self) -> timedelta:
        return timedelta(seconds=self.stale_after_seconds)

    @property
    def max_clock_drift(self) -> timedelta:
        return timedelta(seconds=self.max_clock_drift_seconds)


def load_settings() -> Settings:
    assert_no_exchange_credentials()
    return Settings()
