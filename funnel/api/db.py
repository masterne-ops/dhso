"""
SQLite database helpers for the funnel app.
DB file: data/funnel.db  (relative to project root)
"""
from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).parent.parent / "data" / "funnel.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
        -- ── State slices (one row per geo+period; upserted in place) ──────────
        CREATE TABLE IF NOT EXISTS funnel_state (
            geo_key     TEXT    NOT NULL,
            period_type TEXT    NOT NULL,  -- 'week' | 'month'
            period_key  TEXT    NOT NULL,  -- '2026-W31' | '2026-07'
            state_json  TEXT    NOT NULL,  -- JSON blob
            created_at  TEXT    DEFAULT (datetime('now','localtime')),
            updated_at  TEXT    DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (geo_key, period_key)
        );

        -- ── Factor library ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS funnel_factor_defs (
            id             TEXT PRIMARY KEY,
            conv_key       TEXT NOT NULL,
            name           TEXT NOT NULL,
            unit           TEXT DEFAULT '%',
            source         TEXT NOT NULL,  -- 'auto' | 'manual'
            fetch_query    TEXT,           -- parameterised SQL for auto factors
            calc_expr      TEXT,           -- optional Python post-processing
            default_target REAL,
            display_order  INT  DEFAULT 0,
            enabled        INT  DEFAULT 1,
            created_at     TEXT DEFAULT (datetime('now','localtime')),
            updated_at     TEXT DEFAULT (datetime('now','localtime'))
        );

        -- ── Auto-factor computed value cache ───────────────────────────────────
        CREATE TABLE IF NOT EXISTS funnel_factor_value_cache (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            factor_id   TEXT NOT NULL REFERENCES funnel_factor_defs(id),
            geo_key     TEXT NOT NULL,
            period_key  TEXT NOT NULL,
            value       REAL,
            computed_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE (factor_id, geo_key, period_key)
        );
        """)


# ── State helpers ──────────────────────────────────────────────────────────────

def save_state(geo_key: str, period_type: str, period_key: str, state: dict) -> str:
    """
    Upsert the slice for this geo+period — one row per (geo_key, period_key),
    always the latest. Returns the new updated_at timestamp.
    """
    with get_conn() as conn:
        row = conn.execute(
            """INSERT INTO funnel_state(geo_key,period_type,period_key,state_json)
               VALUES(?,?,?,?)
               ON CONFLICT(geo_key,period_key) DO UPDATE SET
                   period_type = excluded.period_type,
                   state_json  = excluded.state_json,
                   updated_at  = datetime('now','localtime')
               RETURNING updated_at""",
            (geo_key, period_type, period_key, json.dumps(state, ensure_ascii=False))
        ).fetchone()
    return row["updated_at"] if row else ""


def load_latest_state(geo_key: str, period_key: str) -> Optional[Dict[str, Any]]:
    """Return the stored slice for this geo+period, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT state_json FROM funnel_state WHERE geo_key=? AND period_key=?",
            (geo_key, period_key)
        ).fetchone()
    if row:
        return json.loads(row["state_json"])
    return None


def get_state_meta(geo_key: str, period_key: str) -> Optional[Dict[str, Any]]:
    """Return slice metadata (period_type, created_at, updated_at) without the blob."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT period_type, created_at, updated_at FROM funnel_state
               WHERE geo_key=? AND period_key=?""",
            (geo_key, period_key)
        ).fetchone()
    return dict(row) if row else None


def list_slices(geo_key: Optional[str] = None,
                limit: int = 200) -> List[Dict[str, Any]]:
    """List stored slices (metadata only), most recently updated first."""
    sql = """SELECT geo_key, period_type, period_key, created_at, updated_at
             FROM funnel_state {where} ORDER BY updated_at DESC LIMIT ?"""
    with get_conn() as conn:
        if geo_key:
            rows = conn.execute(sql.format(where="WHERE geo_key=?"),
                                (geo_key, limit)).fetchall()
        else:
            rows = conn.execute(sql.format(where=""), (limit,)).fetchall()
    return [dict(r) for r in rows]


# ── Factor-def helpers ─────────────────────────────────────────────────────────

def seed_factor_defs(defs: List[Dict[str, Any]]):
    """
    写入/更新因子定义（幂等，按 id UPSERT）。

    只覆盖 name/unit/source/default_target/display_order —— 这些是代码维护的
    元数据。enabled 不覆盖：管理员可能在库里手工停用某个因子，重启服务不该
    把它重新打开。
    """
    with get_conn() as conn:
        for d in defs:
            conn.execute(
                """INSERT INTO funnel_factor_defs
                     (id, conv_key, name, unit, source, default_target, display_order)
                   VALUES(:id,:conv_key,:name,:unit,:source,:default_target,:display_order)
                   ON CONFLICT(id) DO UPDATE SET
                     conv_key=excluded.conv_key, name=excluded.name,
                     unit=excluded.unit, source=excluded.source,
                     default_target=excluded.default_target,
                     display_order=excluded.display_order,
                     updated_at=datetime('now','localtime')""",
                {"unit": "%", "default_target": None, "display_order": 0, **d})


def list_factor_defs(conv_key: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if conv_key:
            rows = conn.execute(
                "SELECT * FROM funnel_factor_defs WHERE enabled=1 AND conv_key=? ORDER BY display_order",
                (conv_key,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM funnel_factor_defs WHERE enabled=1 ORDER BY conv_key, display_order"
            ).fetchall()
    return [dict(r) for r in rows]


# ── Factor value cache helpers ────────────────────────────────────────────────

def get_cached_factor_values(geo_key: str, period_key: str,
                              max_age_hours: int = 1) -> Dict[str, Any]:
    """Return {factor_id: value} for non-expired cache entries."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT factor_id, value FROM funnel_factor_value_cache
               WHERE geo_key=? AND period_key=?
                 AND datetime(computed_at) > datetime('now','localtime',? || ' hours')""",
            (geo_key, period_key, f"-{max_age_hours}")
        ).fetchall()
    return {r["factor_id"]: r["value"] for r in rows}


def upsert_factor_cache(geo_key: str, period_key: str, factor_id: str, value: float):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO funnel_factor_value_cache(factor_id,geo_key,period_key,value)
               VALUES(?,?,?,?)
               ON CONFLICT(factor_id,geo_key,period_key)
               DO UPDATE SET value=excluded.value,
                             computed_at=datetime('now','localtime')""",
            (factor_id, geo_key, period_key, value)
        )
