from __future__ import annotations

import base64
import csv
import fnmatch
import hashlib
import io
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .config import settings


DENIED_TABLES = {
    "app_user",
    "app_user_scope",
    "ai_conversation_log",
    "ai_search_log",
    "code_agent_log",
    "meeting_audit_log",
    "monthly_report_log",
}
DENIED_PREFIXES = ("sqlite_",)
DANGEROUS_FUNCTIONS = {
    "load_extension",
    "readfile",
    "writefile",
    "fts3_tokenizer",
}
START_PATTERN = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


class QueryRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool
    elapsed_ms: int
    referenced_tables: set[str]
    query_sha256: str


def is_system_denied(name: str) -> bool:
    lower = name.lower()
    return lower in DENIED_TABLES or lower.startswith(DENIED_PREFIXES)


def table_allowed(name: str, patterns: list[str]) -> bool:
    if is_system_denied(name):
        return False
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def open_data_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = (path or settings.data_db).resolve()
    # immutable=1 prevents SQLite from creating WAL/SHM sidecar files.
    # A fresh connection is opened per operation, so atomically replaced or
    # checkpointed production snapshots are observed by subsequent requests.
    uri = f"file:{quote(str(db_path))}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=5, check_same_thread=False)
    conn.row_factory = None
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def list_visible_objects(patterns: list[str]) -> list[dict]:
    conn = open_data_db()
    try:
        rows = conn.execute(
            """
            SELECT name, type, sql
            FROM sqlite_master
            WHERE type IN ('table', 'view')
            ORDER BY type, name
            """
        ).fetchall()
        return [
            {"name": name, "type": obj_type}
            for name, obj_type, _ in rows
            if table_allowed(name, patterns)
        ]
    finally:
        conn.close()


def describe_object(name: str, patterns: list[str]) -> dict:
    if not table_allowed(name, patterns):
        raise QueryRejected("table_forbidden", "Table is unavailable or not authorized")
    conn = open_data_db()
    try:
        existing = conn.execute(
            "SELECT type, sql FROM sqlite_master "
            "WHERE name = ? AND type IN ('table','view')",
            (name,),
        ).fetchone()
        if not existing:
            raise QueryRejected("table_not_found", "Table or view does not exist")
        safe_name = name.replace('"', '""')
        columns = conn.execute(f'PRAGMA table_info("{safe_name}")').fetchall()
        foreign_keys = conn.execute(f'PRAGMA foreign_key_list("{safe_name}")').fetchall()
        return {
            "name": name,
            "type": existing[0],
            "columns": [
                {
                    "cid": c[0],
                    "name": c[1],
                    "declared_type": c[2],
                    "not_null": bool(c[3]),
                    "default": c[4],
                    "primary_key_position": c[5],
                }
                for c in columns
            ],
            "foreign_keys": [
                {
                    "id": f[0],
                    "sequence": f[1],
                    "target_table": f[2],
                    "from_column": f[3],
                    "to_column": f[4],
                }
                for f in foreign_keys
                if table_allowed(f[2], patterns)
            ],
        }
    finally:
        conn.close()


def _json_value(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def execute_readonly(
    sql: str,
    params: dict | list | None,
    *,
    patterns: list[str],
    max_rows: int,
    timeout_ms: int | None = None,
) -> QueryResult:
    query = sql.strip().rstrip(";").strip()
    if not START_PATTERN.match(query):
        raise QueryRejected("select_only", "Only SELECT or WITH queries are accepted")
    if len(query) > 100_000:
        raise QueryRejected("query_too_large", "SQL exceeds 100000 characters")
    if not patterns:
        raise QueryRejected("no_data_permission", "Account has no table permission")

    deadline = time.monotonic() + (timeout_ms or settings.query_timeout_ms) / 1000
    referenced: set[str] = set()
    conn = open_data_db()

    deny_actions = {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_REINDEX,
        sqlite3.SQLITE_ANALYZE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
    }

    def authorizer(action, arg1, arg2, db_name, source):
        if action in deny_actions:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ:
            table = arg1 or ""
            if not table_allowed(table, patterns):
                return sqlite3.SQLITE_DENY
            referenced.add(table)
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = (arg2 or arg1 or "").lower()
            if function_name in DANGEROUS_FUNCTIONS:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def progress():
        return 1 if time.monotonic() > deadline else 0

    conn.set_authorizer(authorizer)
    conn.set_progress_handler(progress, 10_000)
    started = time.monotonic()
    try:
        cursor = conn.execute(query, params or {})
        if cursor.description is None:
            raise QueryRejected("select_only", "Query did not return a result set")
        columns = [d[0] for d in cursor.description]
        raw_rows = cursor.fetchmany(max_rows + 1)
        truncated = len(raw_rows) > max_rows
        rows = [
            [_json_value(value) for value in row]
            for row in raw_rows[:max_rows]
        ]
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            referenced_tables=referenced,
            query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest(),
        )
    except sqlite3.DatabaseError as exc:
        message = str(exc)
        if (
            "not authorized" in message
            or "authorization denied" in message
            or " is prohibited" in message
        ):
            raise QueryRejected(
                "query_forbidden",
                "Query attempted an unauthorized table, function, or operation",
            ) from exc
        if "interrupted" in message:
            raise QueryRejected(
                "query_timeout",
                f"Query exceeded {timeout_ms or settings.query_timeout_ms} ms",
            ) from exc
        raise QueryRejected("invalid_query", message[:500]) from exc
    finally:
        conn.close()


def result_to_csv(result: QueryResult) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(result.columns)
    writer.writerows(result.rows)
    return stream.getvalue()
