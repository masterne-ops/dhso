from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_db: Path
    auth_db: Path
    jwt_secret_file: Path
    database_doc: Path
    catalog_file: Path
    claude_binary: Path
    claude_workdir: Path
    token_minutes: int
    query_timeout_ms: int
    default_max_rows: int
    hard_max_rows: int
    resolver_timeout_seconds: int
    resolver_queue_seconds: int
    resolver_concurrency: int
    resolver_max_resources: int
    resolver_doc_chars: int
    resolver_model: str
    resolver_max_budget_usd: float
    api_title: str


def load_settings() -> Settings:
    return Settings(
        data_db=Path(os.getenv(
            "SO_DATA_API_DB_PATH",
            "/opt/so-data-analytics/db/product_flow.db",
        )),
        auth_db=Path(os.getenv(
            "SO_DATA_API_AUTH_DB",
            "/var/lib/so-data-api/auth.db",
        )),
        jwt_secret_file=Path(os.getenv(
            "SO_DATA_API_JWT_SECRET_FILE",
            "/etc/so-data-api/jwt-secret",
        )),
        database_doc=Path(os.getenv(
            "SO_DATA_API_DATABASE_DOC",
            "/opt/so-data-api/knowledge/数据库说明.md",
        )),
        catalog_file=Path(os.getenv(
            "SO_DATA_API_CATALOG_FILE",
            "/opt/so-data-api/knowledge/api-catalog.json",
        )),
        claude_binary=Path(os.getenv(
            "SO_DATA_API_CLAUDE_BINARY",
            "/usr/bin/claude",
        )),
        claude_workdir=Path(os.getenv(
            "SO_DATA_API_CLAUDE_WORKDIR",
            "/opt/so-data-api",
        )),
        token_minutes=max(5, int(os.getenv("SO_DATA_API_TOKEN_MINUTES", "60"))),
        query_timeout_ms=max(100, int(os.getenv("SO_DATA_API_QUERY_TIMEOUT_MS", "8000"))),
        default_max_rows=max(1, int(os.getenv("SO_DATA_API_DEFAULT_MAX_ROWS", "5000"))),
        hard_max_rows=max(1, int(os.getenv("SO_DATA_API_HARD_MAX_ROWS", "20000"))),
        resolver_timeout_seconds=max(
            10, int(os.getenv("SO_DATA_API_RESOLVER_TIMEOUT_SECONDS", "110"))
        ),
        resolver_queue_seconds=max(
            0, int(os.getenv("SO_DATA_API_RESOLVER_QUEUE_SECONDS", "30"))
        ),
        resolver_concurrency=max(
            1, min(4, int(os.getenv("SO_DATA_API_RESOLVER_CONCURRENCY", "2")))
        ),
        resolver_max_resources=max(
            4, min(14, int(os.getenv("SO_DATA_API_RESOLVER_MAX_RESOURCES", "10")))
        ),
        resolver_doc_chars=max(
            2000, int(os.getenv("SO_DATA_API_RESOLVER_DOC_CHARS", "8000"))
        ),
        resolver_model=(
            os.getenv("SO_DATA_API_RESOLVER_MODEL")
            or os.getenv("ANTHROPIC_SMALL_FAST_MODEL")
            or os.getenv("ANTHROPIC_MODEL")
            or "sonnet"
        ),
        resolver_max_budget_usd=max(
            0.01, float(os.getenv("SO_DATA_API_RESOLVER_MAX_BUDGET_USD", "0.80"))
        ),
        api_title="SO Production Data Read-only API",
    )


settings = load_settings()
