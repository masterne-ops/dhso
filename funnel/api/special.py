"""
专项目标：把关键因素库里的因子钉到「完成情况」下方。

规则：
  - 本级「设置」= 插入 special_pin(owner=本级 geo)
  - 省设置 → 所有地市/区县自动看见该行（不用再勾选）
  - 市设置 → 仅本市及区县看见；省不必遵守
  - 本期目标存 special_target，并同步到切片 factor_targets
  - 可加总单位（次/家）做下级目标卷积；比率（%）只看本级完成率
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .db import get_conn, list_factor_defs, load_latest_state, save_state
from .factors import FETCHERS, compute
from .periods import period_meta
from .prod_db import available as prod_available
from .report import parse_geo_key, scope_kind
from .rollup import _child_keys

# factor_id → 前端 factor_targets 键用的 (conv_key, factorKey)
FACTOR_UI_KEY: Dict[str, Tuple[str, str]] = {
    "a2t_visit": ("a2t", "visit"),
    "a2t_visit_dahua": ("a2t", "visitD"),
    "a2t_visit_dealer": ("a2t", "visitA"),
    "a2t_meet": ("a2t", "meet"),
    "a2t_fo": ("a2v1", "fo"),
    "a2v1_new_open": ("a2v1", "newOpen"),
    "a2t_churn_visit": ("a2t", "churnVisit"),
    "a2t_churn_recover": ("a2t", "churnRecover"),
    "a2t_churn_visit_recover": ("a2t", "churnVisitRecover"),
}

ADDITIVE_UNITS = frozenset({"次", "家"})


def geo_chain(geo_key: str) -> List[str]:
    """本级 + 上级（细→粗）。用于查找对本级生效的 pin。"""
    province, city, district = parse_geo_key(geo_key)
    out: List[str] = []
    if district and city:
        out.append(f"{province}/{city}/{district}")
    if city:
        out.append(f"{province}/{city}/")
    out.append(f"{province}//")
    return out


def _owner_rank(owner_geo: str) -> int:
    """越小越上级：省 0、市 1、区县 2。"""
    _, city, district = parse_geo_key(owner_geo)
    if district:
        return 2
    if city:
        return 1
    return 0


def _owner_label(owner_geo: str) -> str:
    kind = scope_kind(*parse_geo_key(owner_geo)[1:])
    return {"province": "省设置", "city": "本市设置", "district": "本区县设置"}[kind]


def _defs_by_id() -> Dict[str, Dict[str, Any]]:
    return {d["id"]: d for d in list_factor_defs()}


def _factor_target_ls_key(geo_key: str, period_key: str, factor_id: str) -> Optional[str]:
    ui = FACTOR_UI_KEY.get(factor_id)
    if not ui:
        return None
    conv, fkey = ui
    return f"{geo_key}/{period_key}/{conv}/{fkey}"


def _sync_factor_targets_slice(geo_key: str, period_key: str,
                               factor_id: str, target: Optional[float]) -> None:
    """把专项目标同步进 funnel_state.factor_targets，与关键因素区共用。"""
    ls_key = _factor_target_ls_key(geo_key, period_key, factor_id)
    if not ls_key:
        return
    meta = period_meta(period_key)
    state = load_latest_state(geo_key, period_key) or {}
    ft = state.get("factor_targets")
    if not isinstance(ft, dict):
        ft = {}
    else:
        ft = dict(ft)
    if target is None:
        ft.pop(ls_key, None)
    else:
        ft[ls_key] = target
    state["factor_targets"] = ft
    save_state(geo_key, meta["type"], period_key, state)


def list_catalog() -> List[Dict[str, Any]]:
    """可供设置为专项的因子（启用中的 defs）。"""
    out = []
    for d in list_factor_defs():
        out.append({
            "factor_id": d["id"],
            "name": d["name"],
            "unit": d["unit"] or "",
            "conv_key": d["conv_key"],
            "source": d["source"],
            "additive": (d["unit"] or "") in ADDITIVE_UNITS,
            "has_fetcher": d["id"] in FETCHERS,
        })
    return out


def add_pin(geo_key: str, period_key: str, factor_id: str,
            target: Optional[float] = None) -> Dict[str, Any]:
    period_meta(period_key)  # validate
    defs = _defs_by_id()
    if factor_id not in defs:
        raise ValueError(f"未知因子：{factor_id}")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO special_pin(period_key, factor_id, owner_geo_key)
               VALUES(?,?,?)
               ON CONFLICT(period_key, factor_id, owner_geo_key) DO NOTHING""",
            (period_key, factor_id, geo_key),
        )
        if target is not None:
            conn.execute(
                """INSERT INTO special_target(period_key, factor_id, geo_key, target)
                   VALUES(?,?,?,?)
                   ON CONFLICT(period_key, factor_id, geo_key) DO UPDATE SET
                     target=excluded.target,
                     updated_at=datetime('now','localtime')""",
                (period_key, factor_id, geo_key, float(target)),
            )
    if target is not None:
        _sync_factor_targets_slice(geo_key, period_key, factor_id, float(target))
    return build_special(geo_key, period_key)


def remove_pin(geo_key: str, period_key: str, factor_id: str) -> Dict[str, Any]:
    """仅删除本级发起的 pin；下级不能删上级设置的专项。"""
    with get_conn() as conn:
        cur = conn.execute(
            """DELETE FROM special_pin
               WHERE period_key=? AND factor_id=? AND owner_geo_key=?""",
            (period_key, factor_id, geo_key),
        )
        if cur.rowcount == 0:
            raise ValueError("本级未设置该专项，无法删除（上级设置的须由上级取消）")
    return build_special(geo_key, period_key)


def set_target(geo_key: str, period_key: str, factor_id: str,
               target: Optional[float]) -> Dict[str, Any]:
    """本级填写/清空本期专项目标（不要求本级是 pin 发起方）。"""
    period_meta(period_key)
    visible = {r["factor_id"] for r in _visible_pin_rows(geo_key, period_key)}
    if factor_id not in visible:
        raise ValueError("该专项对本级不可见，请先由本级或上级设置")
    with get_conn() as conn:
        if target is None:
            conn.execute(
                """DELETE FROM special_target
                   WHERE period_key=? AND factor_id=? AND geo_key=?""",
                (period_key, factor_id, geo_key),
            )
        else:
            conn.execute(
                """INSERT INTO special_target(period_key, factor_id, geo_key, target)
                   VALUES(?,?,?,?)
                   ON CONFLICT(period_key, factor_id, geo_key) DO UPDATE SET
                     target=excluded.target,
                     updated_at=datetime('now','localtime')""",
                (period_key, factor_id, geo_key, float(target)),
            )
    _sync_factor_targets_slice(geo_key, period_key, factor_id, target)
    return build_special(geo_key, period_key)


def _visible_pin_rows(geo_key: str, period_key: str) -> List[Dict[str, Any]]:
    chain = geo_chain(geo_key)
    if not chain:
        return []
    qs = ",".join("?" * len(chain))
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT id, period_key, factor_id, owner_geo_key, created_at
                FROM special_pin
                WHERE period_key=? AND owner_geo_key IN ({qs})""",
            [period_key, *chain],
        ).fetchall()
    return [dict(r) for r in rows]


def _get_target(period_key: str, factor_id: str, geo_key: str) -> Optional[float]:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT target FROM special_target
               WHERE period_key=? AND factor_id=? AND geo_key=?""",
            (period_key, factor_id, geo_key),
        ).fetchone()
    if row is None or row["target"] is None:
        return None
    return float(row["target"])


def _actual_value(geo_key: str, period_key: str, factor_id: str
                  ) -> Optional[float]:
    if factor_id not in FETCHERS or not prod_available():
        return None
    meta = period_meta(period_key)
    _, city, district = parse_geo_key(geo_key)
    try:
        detail = compute(factor_id, city, district, meta["start"], meta["end"])
    except Exception:
        return None
    if not detail or detail.get("value") is None:
        return None
    return float(detail["value"])


def _rollup_factor(geo_key: str, period_key: str, factor_id: str
                   ) -> Dict[str, Any]:
    kind, children, why = _child_keys(geo_key)
    if kind == "none" or not children:
        return {
            "children_kind": kind, "sum": None, "n_set": 0,
            "n_missing": 0, "missing": [], "note": why or "无下级",
        }
    total = 0.0
    n_set = 0
    missing: List[str] = []
    with get_conn() as conn:
        for ck in children:
            row = conn.execute(
                """SELECT target FROM special_target
                   WHERE period_key=? AND factor_id=? AND geo_key=?""",
                (period_key, factor_id, ck),
            ).fetchone()
            if row is None or row["target"] is None:
                # 短名：市名或区县名
                parts = ck.strip("/").split("/")
                missing.append(parts[-1] if parts else ck)
            else:
                total += float(row["target"])
                n_set += 1
    return {
        "children_kind": kind,
        "sum": round(total, 2) if n_set else None,
        "n_set": n_set,
        "n_missing": len(missing),
        "missing": missing[:8],
        "note": "",
    }


def visible_special_pins(geo_key: str, period_key: str) -> List[Dict[str, Any]]:
    """
    本级可见专项（含上级继承），按 factor_id 去重。
    供业务员评估等只读展示绝对值，不要求本对象有目标。
    """
    defs = _defs_by_id()
    pin_rows = _visible_pin_rows(geo_key, period_key)
    by_fid: Dict[str, List[Dict[str, Any]]] = {}
    for r in pin_rows:
        by_fid.setdefault(r["factor_id"], []).append(r)
    out = []
    for fid, pins in sorted(by_fid.items(), key=lambda x: x[0]):
        d = defs.get(fid) or {"id": fid, "name": fid, "unit": "", "conv_key": ""}
        owners = sorted(pins, key=lambda p: _owner_rank(p["owner_geo_key"]))
        source_owner = owners[0]["owner_geo_key"]
        out.append({
            "factor_id": fid,
            "name": d.get("name") or fid,
            "unit": d.get("unit") or "",
            "conv_key": d.get("conv_key") or "",
            "source_geo": source_owner,
            "source_label": _owner_label(source_owner),
        })
    return out


def build_special(geo_key: str, period_key: str) -> Dict[str, Any]:
    meta = period_meta(period_key)
    defs = _defs_by_id()
    pin_rows = _visible_pin_rows(geo_key, period_key)

    # 按 factor_id 去重；来源取最上级的 owner
    by_fid: Dict[str, List[Dict[str, Any]]] = {}
    for r in pin_rows:
        by_fid.setdefault(r["factor_id"], []).append(r)

    items = []
    for fid, pins in sorted(by_fid.items(), key=lambda x: x[0]):
        d = defs.get(fid) or {"id": fid, "name": fid, "unit": "", "conv_key": ""}
        unit = d.get("unit") or ""
        additive = unit in ADDITIVE_UNITS
        owners = sorted(pins, key=lambda p: _owner_rank(p["owner_geo_key"]))
        source_owner = owners[0]["owner_geo_key"]
        can_remove = any(p["owner_geo_key"] == geo_key for p in pins)
        tgt = _get_target(period_key, fid, geo_key)
        actual = _actual_value(geo_key, period_key, fid)
        rate = None
        if tgt is not None and tgt > 0 and actual is not None:
            rate = round(actual / tgt * 100, 1)
        roll = _rollup_factor(geo_key, period_key, fid) if additive else None
        items.append({
            "factor_id": fid,
            "name": d.get("name") or fid,
            "unit": unit,
            "conv_key": d.get("conv_key") or "",
            "additive": additive,
            "source_geo": source_owner,
            "source_label": _owner_label(source_owner),
            "can_remove": can_remove,
            "target": tgt,
            "actual": actual,
            "rate": rate,
            "rollup": roll,
        })

    kind, _, _ = _child_keys(geo_key)
    return {
        "geo_key": geo_key,
        "period_key": period_key,
        "period_type": meta["type"],
        "start": meta["start"],
        "end": meta["end"],
        "scope": scope_kind(*parse_geo_key(geo_key)[1:]),
        "children_kind": kind,
        "items": items,
        "catalog": list_catalog(),
        "notes": [
            "省设置的专项会自动出现在所有地市、区县；市设置的专项省不必遵守。",
            "本期目标与关键因素区共用；可加总单位（次/家）显示下级目标卷积。",
            "完成率 = 因子本期实际 ÷ 本期专项目标。",
        ],
    }
