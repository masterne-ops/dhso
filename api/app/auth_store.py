from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pwdlib import PasswordHash

from .config import settings


PASSWORD_HASH = PasswordHash.recommended()
DUMMY_HASH = PASSWORD_HASH.hash("not-a-real-password-for-timing-equalization")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_auth(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or settings.auth_db
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_auth_schema(path: Path | None = None) -> None:
    conn = connect_auth(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_user (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                full_name TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                token_version INTEGER NOT NULL DEFAULT 1,
                max_rows INTEGER NOT NULL DEFAULT 5000,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login TEXT
            );

            CREATE TABLE IF NOT EXISTS api_user_table (
                username TEXT NOT NULL,
                table_pattern TEXT NOT NULL,
                PRIMARY KEY (username, table_pattern),
                FOREIGN KEY (username) REFERENCES api_user(username) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS api_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                username TEXT,
                client_ip TEXT,
                endpoint TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                query_sha256 TEXT,
                referenced_tables TEXT,
                rows_returned INTEGER,
                elapsed_ms INTEGER,
                error_code TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_api_audit_time
                ON api_audit_log(occurred_at);
            CREATE INDEX IF NOT EXISTS idx_api_audit_user
                ON api_audit_log(username, occurred_at);
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_user(username: str) -> dict | None:
    conn = connect_auth()
    try:
        row = conn.execute(
            "SELECT * FROM api_user WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            return None
        user = dict(row)
        patterns = conn.execute(
            "SELECT table_pattern FROM api_user_table "
            "WHERE username = ? ORDER BY table_pattern",
            (username,),
        ).fetchall()
        user["table_patterns"] = [r[0] for r in patterns]
        return user
    finally:
        conn.close()


def authenticate(username: str, password: str) -> dict | None:
    user = get_user(username)
    stored_hash = user["password_hash"] if user else DUMMY_HASH
    try:
        valid = PASSWORD_HASH.verify(password, stored_hash)
    except Exception:
        valid = False
    if not user or not valid or not bool(user["enabled"]):
        return None
    conn = connect_auth()
    try:
        conn.execute(
            "UPDATE api_user SET last_login = ?, updated_at = ? WHERE username = ?",
            (_now(), _now(), username),
        )
        conn.commit()
    finally:
        conn.close()
    return get_user(username)


def upsert_user(
    username: str,
    password: str | None,
    *,
    full_name: str | None = None,
    max_rows: int = 5000,
    enabled: bool = True,
) -> None:
    if not username or len(username) > 64:
        raise ValueError("username must be 1-64 characters")
    if password is not None and len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    conn = connect_auth()
    try:
        exists = conn.execute(
            "SELECT 1 FROM api_user WHERE username = ?",
            (username,),
        ).fetchone()
        now = _now()
        if exists:
            sets = ["full_name = ?", "max_rows = ?", "enabled = ?", "updated_at = ?"]
            values: list = [full_name, max_rows, int(enabled), now]
            if password is not None:
                sets.extend(["password_hash = ?", "token_version = token_version + 1"])
                values.append(PASSWORD_HASH.hash(password))
            values.append(username)
            conn.execute(
                f"UPDATE api_user SET {', '.join(sets)} WHERE username = ?",
                values,
            )
        else:
            if password is None:
                raise ValueError("new user requires password")
            conn.execute(
                """
                INSERT INTO api_user
                  (username, password_hash, full_name, enabled, token_version,
                   max_rows, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    username,
                    PASSWORD_HASH.hash(password),
                    full_name,
                    int(enabled),
                    max_rows,
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def set_user_enabled(username: str, enabled: bool) -> bool:
    conn = connect_auth()
    try:
        cur = conn.execute(
            "UPDATE api_user SET enabled = ?, token_version = token_version + 1, "
            "updated_at = ? WHERE username = ?",
            (int(enabled), _now(), username),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def revoke_tokens(username: str) -> bool:
    conn = connect_auth()
    try:
        cur = conn.execute(
            "UPDATE api_user SET token_version = token_version + 1, updated_at = ? "
            "WHERE username = ?",
            (_now(), username),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_user(username: str) -> bool:
    conn = connect_auth()
    try:
        cur = conn.execute("DELETE FROM api_user WHERE username = ?", (username,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_table_patterns(username: str, patterns: Iterable[str]) -> int:
    cleaned = sorted({p.strip() for p in patterns if p and p.strip()})
    conn = connect_auth()
    try:
        if not conn.execute(
            "SELECT 1 FROM api_user WHERE username = ?",
            (username,),
        ).fetchone():
            raise ValueError(f"unknown user: {username}")
        conn.execute("DELETE FROM api_user_table WHERE username = ?", (username,))
        conn.executemany(
            "INSERT INTO api_user_table (username, table_pattern) VALUES (?, ?)",
            [(username, p) for p in cleaned],
        )
        conn.execute(
            "UPDATE api_user SET token_version = token_version + 1, updated_at = ? "
            "WHERE username = ?",
            (_now(), username),
        )
        conn.commit()
        return len(cleaned)
    finally:
        conn.close()


def list_users() -> list[dict]:
    conn = connect_auth()
    try:
        rows = conn.execute(
            "SELECT username, full_name, enabled, token_version, max_rows, "
            "created_at, updated_at, last_login FROM api_user ORDER BY username"
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            patterns = conn.execute(
                "SELECT table_pattern FROM api_user_table "
                "WHERE username = ? ORDER BY table_pattern",
                (item["username"],),
            ).fetchall()
            item["table_patterns"] = [p[0] for p in patterns]
            out.append(item)
        return out
    finally:
        conn.close()


def write_audit(
    *,
    request_id: str,
    username: str | None,
    client_ip: str | None,
    endpoint: str,
    status_code: int,
    query_sha256: str | None = None,
    referenced_tables: Iterable[str] = (),
    rows_returned: int | None = None,
    elapsed_ms: int | None = None,
    error_code: str | None = None,
) -> None:
    try:
        conn = connect_auth()
        try:
            conn.execute(
                """
                INSERT INTO api_audit_log
                  (request_id, occurred_at, username, client_ip, endpoint,
                   status_code, query_sha256, referenced_tables, rows_returned,
                   elapsed_ms, error_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    _now(),
                    username,
                    client_ip,
                    endpoint,
                    status_code,
                    query_sha256,
                    json.dumps(sorted(set(referenced_tables)), ensure_ascii=False),
                    rows_returned,
                    elapsed_ms,
                    error_code,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # An audit failure must not expose internals or replace the API response.
        pass
