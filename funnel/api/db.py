"""
SQLite database helpers for the funnel app.
DB file: data/funnel.db  (relative to project root)
"""
from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

        -- ── 当年筛后 SO 事件（首台 / 过 1 万日），避免每次切周期扫 install_redpack ──
        CREATE TABLE IF NOT EXISTS so_event_cache (
            year     TEXT NOT NULL,
            stamp    TEXT NOT NULL,
            code     TEXT NOT NULL,
            first_so TEXT,
            v3_time  TEXT,
            PRIMARY KEY (year, stamp, code)
        );
        CREATE TABLE IF NOT EXISTS so_event_meta (
            year     TEXT PRIMARY KEY,
            stamp    TEXT NOT NULL,
            n        INTEGER,
            built_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 拜访目的原文 → 图上 6 类标准跑动标签（业务员能力评估用）
        CREATE TABLE IF NOT EXISTS visit_purpose_map (
            token       TEXT PRIMARY KEY,  -- 库内标签，如「新签」
            label_id    TEXT NOT NULL,     -- connect/policy/product_talk/...
            label_name  TEXT NOT NULL,     -- 建联拓客 / …
            necessary   INT  NOT NULL,     -- 1=必要 Y，0=非必要 N
            sort_order  INT  DEFAULT 0
        );

        -- 专项目标：某周期在某级「设置」的因子钉（省设 → 全市/区县自动可见）
        CREATE TABLE IF NOT EXISTS special_pin (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            period_key     TEXT NOT NULL,
            factor_id      TEXT NOT NULL,
            owner_geo_key  TEXT NOT NULL,
            created_at     TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE (period_key, factor_id, owner_geo_key)
        );

        -- 专项目标本期数值（按地区）；与关键因素 factor_targets 同步写入
        CREATE TABLE IF NOT EXISTS special_target (
            period_key  TEXT NOT NULL,
            factor_id   TEXT NOT NULL,
            geo_key     TEXT NOT NULL,
            target      REAL,
            updated_at  TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (period_key, factor_id, geo_key)
        );

        -- ── 业务员评估（独立于漏斗切片，不关联 funnel_state）────────────────
        -- 规定动作手调阈值：群体 / 组织 / 个人各存各的
        CREATE TABLE IF NOT EXISTS sales_eval_preset (
            geo_key      TEXT NOT NULL,
            period_key   TEXT NOT NULL,
            side         TEXT NOT NULL,  -- all | dahua | dealer
            org          TEXT NOT NULL,  -- all | all_dealers | dahua | 公司名
            person       TEXT NOT NULL,  -- '' = 整组织/群体
            item_id      TEXT NOT NULL,  -- prospect_screen / v0_dense / …
            preset_value REAL,
            updated_at   TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (geo_key, period_key, side, org, person, item_id)
        );
        -- 某对象某周期的评估切片（KPI / 规定动作状态 / 小结）
        CREATE TABLE IF NOT EXISTS sales_eval_slice (
            geo_key     TEXT NOT NULL,
            period_key  TEXT NOT NULL,
            side        TEXT NOT NULL,
            org         TEXT NOT NULL,
            person      TEXT NOT NULL,
            slice_json  TEXT NOT NULL,
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            updated_at  TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (geo_key, period_key, side, org, person)
        );

        -- 数据汇总页：各地市/区县 × 周期 × 漏斗档位的预算总额（元，手填）
        CREATE TABLE IF NOT EXISTS funnel_budget (
            geo_key    TEXT NOT NULL,
            period_key TEXT NOT NULL,
            level      TEXT NOT NULL,
            amount     REAL NOT NULL,
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (geo_key, period_key, level)
        );
        """)
    seed_visit_purpose_map()


# 图上 6 类标准标签（与渠道生意关注指标表对齐）
PURPOSE_LABELS = [
    {"id": "connect", "name": "建联拓客", "necessary": 1, "order": 1,
     "utility": "高", "benefit": "意向跑动归因签约（本周期）",
     "roi_hint": "意向跑动次数/意向归因签约数（括号内全省基准）",
     "alt": "无"},
    {"id": "distribute", "name": "铺货", "necessary": 1, "order": 2,
     "utility": "高", "benefit": "铺货（跑动当日）", "roi_hint": "暂空",
     "alt": "无"},
    {"id": "policy", "name": "合作政策讲解", "necessary": 0, "order": 3,
     "utility": "低", "benefit": "SO（当日、7日）", "roi_hint": "SO/跑动次数",
     "alt": "不要单独为讲政策上门"},
    {"id": "product_talk", "name": "销售产品讲解", "necessary": 0, "order": 4,
     "utility": "低", "benefit": "SO（当日、7日）", "roi_hint": "SO/跑动次数",
     "alt": "微信、推广会"},
    {"id": "product_exp", "name": "销售产品体验", "necessary": 1, "order": 5,
     "utility": "中", "benefit": "SO（当日、7日）", "roi_hint": "SO/跑动；云联活跃/跑动",
     "alt": "推广会、新品试用"},
    {"id": "maintain", "name": "日常维护", "necessary": 1, "order": 6,
     "utility": "中", "benefit": "SO（当日、7日）", "roi_hint": "SO/跑动次数",
     "alt": "电话降低线下频次"},
]

# 生产库 拜访目的 现值为多选：新签/行销/激活/复购
PURPOSE_SEED = [
    ("新签", "connect"),
    ("行销", "product_talk"),
    ("激活", "product_exp"),
    ("复购", "maintain"),
]


def seed_visit_purpose_map():
    """幂等写入种子映射；已有 token 不覆盖（允许运营改表）。"""
    by_id = {x["id"]: x for x in PURPOSE_LABELS}
    with get_conn() as conn:
        for token, lid in PURPOSE_SEED:
            meta = by_id[lid]
            conn.execute(
                """INSERT INTO visit_purpose_map(token, label_id, label_name, necessary, sort_order)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(token) DO NOTHING""",
                (token, lid, meta["name"], meta["necessary"], meta["order"]),
            )


def load_visit_purpose_map() -> Dict[str, Dict[str, Any]]:
    """{token: {label_id, label_name, necessary}}"""
    init_schema()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT token, label_id, label_name, necessary FROM visit_purpose_map"
        ).fetchall()
    return {
        r["token"]: {
            "label_id": r["label_id"],
            "label_name": r["label_name"],
            "necessary": bool(r["necessary"]),
        }
        for r in rows
    }


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
    row = _load_state_row(geo_key, period_key)
    if row and row.get("state_json"):
        return json.loads(row["state_json"])
    return None


def get_state_meta(geo_key: str, period_key: str) -> Optional[Dict[str, Any]]:
    """Return slice metadata (period_type, created_at, updated_at) without the blob."""
    row = _load_state_row(geo_key, period_key)
    if not row:
        return None
    return {k: row[k] for k in ("period_type", "created_at", "updated_at")}


def load_state_bundle(geo_key: str, period_key: str
                      ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """一次查出 state + meta，避免 /state 接口连开两次库。"""
    row = _load_state_row(geo_key, period_key)
    if not row:
        return None, None
    try:
        state = json.loads(row["state_json"]) if row.get("state_json") else None
    except (json.JSONDecodeError, TypeError):
        state = None
    meta = {k: row[k] for k in ("period_type", "created_at", "updated_at")}
    return state, meta


def _load_state_row(geo_key: str, period_key: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT state_json, period_type, created_at, updated_at
               FROM funnel_state WHERE geo_key=? AND period_key=?""",
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


def list_states_for_period(period_key: str) -> Dict[str, Dict[str, Any]]:
    """
    某周期全部切片正文，一次读出。总览页要按地区挂待办/手填目标，
    不能对每个单位再打一遍 /state。
    返回 {geo_key: {"period_type": ..., "state": {...}}}。
    JSON 坏掉的行跳过，不让一张脏切片把整页打挂。
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT geo_key, period_type, state_json FROM funnel_state "
            "WHERE period_key=?",
            (period_key,)
        ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        try:
            blob = json.loads(r["state_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(blob, dict):
            continue
        out[r["geo_key"]] = {"period_type": r["period_type"], "state": blob}
    return out


def list_states_for_period_keys(period_keys: List[str]
                                ) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    多个周期一次读出。月度总览要把当月各周切片卷进来。
    返回 {geo_key: {period_key: state}}。
    """
    keys = [k for k in (period_keys or []) if k]
    if not keys:
        return {}
    qs = ",".join("?" * len(keys))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT geo_key, period_key, state_json FROM funnel_state "
            f"WHERE period_key IN ({qs})",
            keys
        ).fetchall()
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for r in rows:
        try:
            blob = json.loads(r["state_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(blob, dict):
            continue
        out.setdefault(r["geo_key"], {})[r["period_key"]] = blob
    return out


def list_all_states() -> List[Dict[str, Any]]:
    """
    全库切片正文（含 updated_at）。指标库收获各地 custom_factors 用。
    JSON 坏掉的行跳过。
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT geo_key, period_key, period_type, updated_at, state_json "
            "FROM funnel_state"
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            blob = json.loads(r["state_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(blob, dict):
            continue
        out.append({
            "geo_key": r["geo_key"],
            "period_key": r["period_key"],
            "period_type": r["period_type"],
            "updated_at": r["updated_at"],
            "state": blob,
        })
    return out


def list_states_for_geo_periods(geo_key: str, period_keys: List[str]
                                ) -> Dict[str, Dict[str, Any]]:
    """某地区多个周期的切片，{period_key: state}。"""
    keys = [k for k in (period_keys or []) if k]
    if not keys:
        return {}
    qs = ",".join("?" * len(keys))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT period_key, state_json FROM funnel_state "
            f"WHERE geo_key=? AND period_key IN ({qs})",
            [geo_key, *keys]
        ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        try:
            blob = json.loads(r["state_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(blob, dict):
            out[r["period_key"]] = blob
    return out


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


def upsert_factor_cache_many(geo_key: str, period_key: str,
                            values: Dict[str, float]):
    """批量写因子缓存，一次事务，避免每个因子开一次库。"""
    if not values:
        return
    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO funnel_factor_value_cache(factor_id,geo_key,period_key,value)
               VALUES(?,?,?,?)
               ON CONFLICT(factor_id,geo_key,period_key)
               DO UPDATE SET value=excluded.value,
                             computed_at=datetime('now','localtime')""",
            [(fid, geo_key, period_key, val) for fid, val in values.items()]
        )


# ── SO 事件缓存（首台 / 过 1 万日）────────────────────────────────────────────
def load_so_event_cache(year: str, stamp: str) -> Optional[Dict[str, Dict[str, Optional[str]]]]:
    """命中则 {code: {first_so, v3_time}}，stamp 对不上返回 None。"""
    with get_conn() as conn:
        meta = conn.execute(
            "SELECT stamp FROM so_event_meta WHERE year=?", (year,)
        ).fetchone()
        if not meta or meta["stamp"] != stamp:
            return None
        rows = conn.execute(
            "SELECT code, first_so, v3_time FROM so_event_cache WHERE year=? AND stamp=?",
            (year, stamp),
        ).fetchall()
    return {r["code"]: {"first_so": r["first_so"], "v3_time": r["v3_time"]} for r in rows}


def save_so_event_cache(year: str, stamp: str, events: Dict[str, Dict[str, Optional[str]]]):
    with get_conn() as conn:
        conn.execute("DELETE FROM so_event_cache WHERE year=?", (year,))
        conn.execute(
            """INSERT INTO so_event_meta(year, stamp, n, built_at)
               VALUES(?,?,?,datetime('now','localtime'))
               ON CONFLICT(year) DO UPDATE SET
                 stamp=excluded.stamp, n=excluded.n,
                 built_at=excluded.built_at""",
            (year, stamp, len(events)),
        )
        conn.executemany(
            "INSERT INTO so_event_cache(year, stamp, code, first_so, v3_time) VALUES(?,?,?,?,?)",
            [(year, stamp, code, ev.get("first_so"), ev.get("v3_time"))
             for code, ev in events.items()],
        )
