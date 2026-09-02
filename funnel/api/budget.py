"""
数据汇总 / 预算矩阵。

行：管辖范围内的地市 + 其下区县。
列：当前周期四档本期目标（手填 ?? 节奏预设）+ 各档手填预算（元）。
预算与目标数量独立；列合计只加地市行，避免市+区县双计。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .db import get_conn
from .periods import period_label, period_meta
from .prod_db import geo_tree as prod_geo_tree, targets as prod_targets
from .rollup import LEVELS, _stored_targets
from .users import User, scoped_geo_tree

LEVEL_LABELS = {
    "authorized": "授权签约",
    "activated_v1": "已开单 V1+",
    "activated": "已激活 V2+",
    "senior": "高级 V3+",
}


def _split(geo_key: str) -> Tuple[str, Optional[str], Optional[str]]:
    parts = (geo_key or "").split("/")
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1] or None, parts[2] or None


def _load_budgets(period_key: str,
                  geo_keys: List[str]) -> Dict[str, Dict[str, float]]:
    """{geo_key: {level: amount}}"""
    if not geo_keys:
        return {}
    out: Dict[str, Dict[str, float]] = {}
    qs = ",".join("?" * len(geo_keys))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT geo_key, level, amount FROM funnel_budget "
            f"WHERE period_key = ? AND geo_key IN ({qs})",
            [period_key, *geo_keys]).fetchall()
    for r in rows:
        out.setdefault(r["geo_key"], {})[r["level"]] = r["amount"]
    return out


def _effective_period_targets(geo_key: str, period_key: str, meta: Dict[str, Any],
                              stored: Dict[str, Dict[str, Dict[str, Any]]]
                              ) -> Dict[str, Optional[float]]:
    """本期四档：用户手填 ?? 节奏预设。"""
    _, city, district = _split(geo_key)
    preset = prod_targets(city=city, district=district, period_key=period_key,
                          year=meta["year"], month=meta["month"],
                          week_share=meta["week_share"]) or {}
    period_store = (stored.get(geo_key, {}) or {}).get("period") or {}
    out: Dict[str, Optional[float]] = {}
    for lv in LEVELS:
        sv = period_store.get(lv)
        pv = (preset.get("period") or {}).get(lv)
        if sv is not None:
            out[lv] = sv
        elif pv is not None:
            out[lv] = pv
        else:
            out[lv] = None
    return out


def build_matrix(user: Optional[User], period_key: str) -> Dict[str, Any]:
    meta = period_meta(period_key)
    tree = scoped_geo_tree(user, prod_geo_tree())
    if not tree:
        return {
            "period_key": period_key,
            "period_label": period_label(period_key),
            "levels": [{"key": k, "label": LEVEL_LABELS[k]} for k in LEVELS],
            "cities": [],
            "notes": ["生产库不可用或无地区树，无法铺矩阵。"],
            "available": False,
        }

    province = tree.get("province") or "浙江"
    cities_out: List[Dict[str, Any]] = []
    all_keys: List[str] = []

    for c in tree.get("cities") or []:
        city = c["city"]
        city_key = f"{province}/{city}/"
        all_keys.append(city_key)
        dist_keys = []
        for d in c.get("districts") or []:
            dk = f"{province}/{city}/{d}"
            dist_keys.append(dk)
            all_keys.append(dk)
        cities_out.append({
            "name": city,
            "geo_key": city_key,
            "kind": "city",
            "districts": [{"name": d, "geo_key": f"{province}/{city}/{d}",
                           "kind": "district"} for d in (c.get("districts") or [])],
        })

    stored = _stored_targets(all_keys, period_key)
    budgets = _load_budgets(period_key, all_keys)

    def pack_row(name: str, geo_key: str, kind: str) -> Dict[str, Any]:
        tg = _effective_period_targets(geo_key, period_key, meta, stored)
        bd = budgets.get(geo_key) or {}
        return {
            "name": name,
            "geo_key": geo_key,
            "kind": kind,
            "targets": {lv: tg.get(lv) for lv in LEVELS},
            "budgets": {lv: bd.get(lv) for lv in LEVELS},
        }

    rows = []
    for c in cities_out:
        city_row = pack_row(c["name"], c["geo_key"], "city")
        city_row["districts"] = [
            pack_row(d["name"], d["geo_key"], "district") for d in c["districts"]
        ]
        rows.append(city_row)

    return {
        "period_key": period_key,
        "period_label": period_label(period_key),
        "period": meta,
        "levels": [{"key": k, "label": LEVEL_LABELS[k]} for k in LEVELS],
        "cities": rows,
        "available": True,
        "notes": [
            "目标 = 系统本期已填值 ?? 节奏预设；只读。",
            "预算为各格手填总额（元），与目标数量独立。",
            "列合计按地市行加总；区县预算不计入列合计，避免与地市双计。",
        ],
    }


def set_budget(geo_key: str, period_key: str, level: str,
               amount: Optional[float]) -> Dict[str, Any]:
    if level not in LEVELS:
        raise ValueError(f"无效档位：{level}")
    period_meta(period_key)  # validate
    with get_conn() as conn:
        if amount is None:
            conn.execute(
                "DELETE FROM funnel_budget WHERE geo_key=? AND period_key=? AND level=?",
                (geo_key, period_key, level))
        else:
            conn.execute(
                """INSERT INTO funnel_budget(geo_key, period_key, level, amount, updated_at)
                   VALUES(?,?,?,?,datetime('now','localtime'))
                   ON CONFLICT(geo_key, period_key, level) DO UPDATE SET
                     amount=excluded.amount,
                     updated_at=excluded.updated_at""",
                (geo_key, period_key, level, float(amount)))
        conn.commit()
    return {"geo_key": geo_key, "period_key": period_key,
            "level": level, "amount": amount}
