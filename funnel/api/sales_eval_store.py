"""
业务员评估的本地持久化（funnel.db 独立表，不挂 funnel_state）。

  sales_eval_preset  规定动作手调阈值（群体 / 组织 / 个人各一行）
  sales_eval_slice   该对象该周期的状态切片 + 分析小结
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .db import get_conn, init_schema


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _person_key(person: Optional[str]) -> str:
    if not person or person == "all":
        return ""
    return str(person)


def _org_key(org: Optional[str]) -> str:
    return org or ""


def _side_key(side: Optional[str]) -> str:
    return side or "all"


def subject_tuple(
    geo_key: str, period_key: str,
    side: Optional[str], org: Optional[str], person: Optional[str],
) -> Tuple[str, str, str, str, str]:
    return (
        geo_key, period_key,
        _side_key(side), _org_key(org), _person_key(person),
    )


def _inherit_chain(side: str, org: str, person: str
                   ) -> List[Tuple[str, str, str]]:
    """由近到远：个人 → 本组织 → 群体汇总。"""
    chain: List[Tuple[str, str, str]] = []
    seen = set()

    def add(s, o, p):
        key = (s, o, p)
        if key in seen:
            return
        seen.add(key)
        chain.append(key)

    if person:
        add(side, org, person)
    if org:
        add(side, org, "")
    if side == "dealer" and org != "all_dealers":
        add("dealer", "all_dealers", "")
    if side == "dahua" and org != "dahua":
        add("dahua", "dahua", "")
    if side == "all" and org != "all":
        add("all", "all", "")
    return chain


def load_presets(
    geo_key: str, period_key: str,
    side: Optional[str], org: Optional[str], person: Optional[str],
) -> Dict[str, Dict[str, Any]]:
    """
    返回 {item_id: {value, source}}。
    source = person | org | group，未手调的项不出现（调用方用系统默认）。
    """
    init_schema()
    side_k, org_k, person_k = _side_key(side), _org_key(org), _person_key(person)
    chain = _inherit_chain(side_k, org_k, person_k)
    if not chain:
        return {}

    placeholders = ",".join(["(?,?,?)"] * len(chain))
    params: List[Any] = [geo_key, period_key]
    for s, o, p in chain:
        params.extend([s, o, p])

    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT side, org, person, item_id, preset_value
                FROM sales_eval_preset
                WHERE geo_key=? AND period_key=?
                  AND (side, org, person) IN ({placeholders})""",
            params,
        ).fetchall()

    by_level: Dict[Tuple[str, str, str], Dict[str, float]] = {}
    for r in rows:
        by_level.setdefault((r["side"], r["org"], r["person"]), {})[
            r["item_id"]] = r["preset_value"]

    out: Dict[str, Dict[str, Any]] = {}
    # 由远到近覆盖，近的赢
    for s, o, p in reversed(chain):
        bucket = by_level.get((s, o, p), {})
        if p:
            src = "person"
        elif o in ("all", "all_dealers"):
            src = "group"
        else:
            src = "org"
        for item_id, val in bucket.items():
            out[item_id] = {"value": val, "source": src}
    return out


def save_preset(
    geo_key: str, period_key: str,
    side: Optional[str], org: Optional[str], person: Optional[str],
    item_id: str, value: Optional[float],
) -> Dict[str, Any]:
    """value=None 删除本对象覆盖，回落继承。"""
    init_schema()
    sub = subject_tuple(geo_key, period_key, side, org, person)
    with get_conn() as conn:
        if value is None:
            conn.execute(
                """DELETE FROM sales_eval_preset
                   WHERE geo_key=? AND period_key=? AND side=? AND org=?
                     AND person=? AND item_id=?""",
                sub + (item_id,),
            )
        else:
            conn.execute(
                """INSERT INTO sales_eval_preset
                     (geo_key, period_key, side, org, person, item_id,
                      preset_value, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(geo_key, period_key, side, org, person, item_id)
                   DO UPDATE SET preset_value=excluded.preset_value,
                                 updated_at=excluded.updated_at""",
                sub + (item_id, float(value), _now()),
            )
    return load_presets(geo_key, period_key, side, org, person)


def load_slice(
    geo_key: str, period_key: str,
    side: Optional[str], org: Optional[str], person: Optional[str],
) -> Optional[Dict[str, Any]]:
    init_schema()
    sub = subject_tuple(geo_key, period_key, side, org, person)
    with get_conn() as conn:
        row = conn.execute(
            """SELECT slice_json, note, created_at, updated_at
               FROM sales_eval_slice
               WHERE geo_key=? AND period_key=? AND side=? AND org=? AND person=?""",
            sub,
        ).fetchone()
    if not row:
        return None
    try:
        body = json.loads(row["slice_json"] or "{}")
    except json.JSONDecodeError:
        body = {}
    return {
        "slice": body,
        "note": row["note"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_slice(
    geo_key: str, period_key: str,
    side: Optional[str], org: Optional[str], person: Optional[str],
    slice_body: Dict[str, Any],
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """刷新状态切片；note=None 时保留已有小结。"""
    init_schema()
    sub = subject_tuple(geo_key, period_key, side, org, person)
    now = _now()
    existing = load_slice(geo_key, period_key, side, org, person)
    keep_note = existing["note"] if existing else ""
    if note is None:
        note = keep_note
    payload = json.dumps(slice_body, ensure_ascii=False)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sales_eval_slice
                 (geo_key, period_key, side, org, person,
                  slice_json, note, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(geo_key, period_key, side, org, person)
               DO UPDATE SET slice_json=excluded.slice_json,
                             note=excluded.note,
                             updated_at=excluded.updated_at""",
            sub + (payload, note, now, now),
        )
    return {
        "slice": slice_body,
        "note": note or "",
        "updated_at": now,
    }


def save_note(
    geo_key: str, period_key: str,
    side: Optional[str], org: Optional[str], person: Optional[str],
    note: str,
) -> Dict[str, Any]:
    """只改小结，不动切片正文。无切片时先建空行。"""
    init_schema()
    existing = load_slice(geo_key, period_key, side, org, person)
    body = existing["slice"] if existing else {}
    return upsert_slice(
        geo_key, period_key, side, org, person, body, note=note)


def compact_slice(detail: Optional[Dict[str, Any]],
                  org: Optional[str], person: Optional[str],
                  org_label: str) -> Dict[str, Any]:
    """GET 时落库的精简切片：KPI + 规定动作状态，不含排行全文。"""
    if not detail:
        return {
            "org": org, "person": person, "org_label": org_label,
            "kpis": {}, "sop": {"items": [], "rate": None},
        }
    sop = detail.get("sop") or {}
    items = []
    for it in sop.get("items") or []:
        items.append({
            "id": it.get("id"),
            "name": it.get("name"),
            "status": it.get("status"),
            "pass": it.get("pass"),
            "detail": it.get("detail"),
            "value": it.get("value"),
            "preset_value": it.get("preset_value"),
            "preset_unit": it.get("preset_unit"),
            "preset_source": it.get("preset_source"),
        })
    kpis = detail.get("kpis") or {}
    return {
        "org": org,
        "person": person,
        "org_label": org_label,
        "name": detail.get("name"),
        "kpis": {
            "visits": kpis.get("visits"),
            "customers": kpis.get("customers"),
            "necessary_rate": kpis.get("necessary_rate"),
            "sop_rate": kpis.get("sop_rate"),
            "unlabeled_rate": kpis.get("unlabeled_rate"),
            "so_amount": kpis.get("so_amount"),
            "sign_count": kpis.get("sign_count"),
        },
        "sop": {
            "items": items,
            "rate": sop.get("rate"),
            "ok": sop.get("ok"),
            "auto": sop.get("auto"),
        },
    }
