"""Central configuration, loaded once from environment / .env (12-factor style)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env", override=False)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _opt_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


@dataclass(frozen=True)
class Settings:
    # --- PostgreSQL -------------------------------------------------------
    pg_host: str = field(default_factory=lambda: os.getenv("PG_HOST", "localhost"))
    pg_port: int = field(default_factory=lambda: int(os.getenv("PG_PORT", "5432")))
    pg_db: str = field(default_factory=lambda: os.getenv("PG_DATABASE", "laya_credit"))
    pg_user: str = field(default_factory=lambda: os.getenv("PG_USER", "laya"))
    pg_password: str = field(default_factory=lambda: os.getenv("PG_PASSWORD", ""))

    # --- Decision backend -------------------------------------------------
    backend: str = field(default_factory=lambda: os.getenv("DECISION_BACKEND", "laya").lower())
    laya_model: str = field(default_factory=lambda: os.getenv("LAYA_MODEL", "auto"))
    laya_revision: str | None = field(default_factory=lambda: os.getenv("LAYA_REVISION") or None)
    laya_device: str | None = field(default_factory=lambda: os.getenv("LAYA_DEVICE") or None)
    laya_max_len: int | None = field(default_factory=lambda: _opt_int("LAYA_MAX_LEN"))
    min_confidence: float = field(default_factory=lambda: float(os.getenv("LAYA_MIN_CONFIDENCE", "0.70")))

    # --- RAG --------------------------------------------------------------
    embedding_provider: str = field(default_factory=lambda: os.getenv("EMBEDDING_PROVIDER", "hashing"))
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    rag_top_k: int = field(default_factory=lambda: int(os.getenv("RAG_TOP_K", "5")))

    # --- Governance -------------------------------------------------------
    redact_pii: bool = field(default_factory=lambda: _bool("REDACT_PII", True))
    require_registered_model: bool = field(default_factory=lambda: _bool("REQUIRE_REGISTERED_MODEL", True))
    approver: str = field(default_factory=lambda: os.getenv("MODEL_APPROVER", "model-risk-committee"))

    # --- Paths ------------------------------------------------------------
    data_dir: Path = REPO_ROOT / "data"
    tests_dir: Path = REPO_ROOT / "tests" / "cases"
    reports_dir: Path = REPO_ROOT / "reports"

    @property
    def dsn(self) -> str:
        return (f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
                f"user={self.pg_user} password={self.pg_password}")


def get_settings() -> Settings:
    return Settings()
