"""
关键因素指标库：系统 defs + 各地切片里收获的 custom_factors。

系统指标继续走 SEED_DEFS / FETCHERS。手填指标不另建表，扫 funnel_state
聚合。只投影名称/单位/来源地市/周期/实际/目标，不带待办、问题、思考。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .db import list_all_states, list_factor_defs
from .factors import FETCHERS
from .periods import MONTH_RE, WEEK_RE

# 库里 conv_key 与页面漏斗跳不完全一致：首单礼因子 id 仍是 a2t_fo，
# 页面挂在授权→开单 a2v1。弹窗按页面跳归类，避免选错跳。
FACTOR_UI = {
    "a2t_fo": {"conv_key": "a2v1", "factor_key": "fo"},
    "a2t_meet": {"conv_key": "a2t", "factor_key": "meet"},
    "a2t_visit": {"conv_key": "a2t", "factor_key": "visit"},
    "a2t_visit_dahua": {"conv_key": "a2t", "factor_key": "visitD"},
    "a2t_visit_dealer": {"conv_key": "a2t", "factor_key": "visitA"},
    "a2v1_new_open": {"conv_key": "a2v1", "factor_key": "newOpen"},
}

CONV_NAMES = {
    "p2a": "城市总量 → 授权签约（渗透率）",
    "a2v1": "授权 → 已开单（V1+）",
    "a2t": "已开单 → 已激活（V2+）",
    "t2v": "激活 → 高级服务商（V3+）",
}

_TASKISH = re.compile(r"搞定|本周")


def _ui_for_def(d: Dict[str, Any]) -> Dict[str, str]:
    mapped = FACTOR_UI.get(d["id"])
    if mapped:
        return mapped
    return {"conv_key": d.get("conv_key") or "", "factor_key": d["id"]}


def _geo_label(geo_key: str) -> str:
    parts = (geo_key or "").split("/")
    city = parts[1] if len(parts) > 1 else ""
    district = parts[2] if len(parts) > 2 else ""
    if district:
        return f"{city}·{district}"
    if city:
        return city
    return "全省"


def _period_label(period_key: str) -> str:
    key = (period_key or "").strip()
    m = WEEK_RE.match(key)
    if m:
        return f"W{int(m.group(2)):02d}"
    m = MONTH_RE.match(key)
    if m:
        return f"{int(m.group(1))}年{int(m.group(2))}月"
    return key


def _filled(v: Any) -> bool:
    return v is not None and v != ""


def _conv_from_cf_key(key: str, period_key: str) -> Optional[str]:
    needle = f"/{period_key}/"
    i = (key or "").find(needle)
    if i < 0:
        return None
    rest = key[i + len(needle):]
    if not rest or "/" in rest:
        return None
    return rest


def _is_taskish(name: str) -> bool:
    if len(name) >= 12:
        return True
    return bool(_TASKISH.search(name))


def _system_defs(conv_key: Optional[str]) -> List[Dict[str, Any]]:
    out = []
    for d in list_factor_defs():
        ui = _ui_for_def(d)
        if conv_key and ui["conv_key"] != conv_key:
            continue
        fid = d["id"]
        has_fetcher = fid in FETCHERS
        out.append({
            "id": fid,
            "name": d.get("name") or fid,
            "unit": d.get("unit") or "",
            "source": d.get("source") or "auto",
            "db_conv_key": d.get("conv_key"),
            "conv_key": ui["conv_key"],
            "factor_key": ui["factor_key"],
            "conv_name": CONV_NAMES.get(ui["conv_key"], ui["conv_key"]),
            "has_fetcher": has_fetcher,
            "status": "auto" if has_fetcher else "pending",
            "display_order": d.get("display_order") or 0,
        })
    out.sort(key=lambda x: (x["conv_key"], x["display_order"], x["name"]))
    return out


def _harvest_community(conv_key: Optional[str]) -> List[Dict[str, Any]]:
    groups: Dict[tuple, Dict[str, Any]] = {}
    for row in list_all_states():
        cf_map = (row["state"] or {}).get("custom_factors") or {}
        if not isinstance(cf_map, dict):
            continue
        gk, pk, ts = row["geo_key"], row["period_key"], row["updated_at"] or ""
        for raw_key, items in cf_map.items():
            conv = _conv_from_cf_key(str(raw_key), pk)
            if not conv:
                continue
            if conv_key and conv != conv_key:
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                unit = str(item.get("unit") or "") or "%"
                val, tgt = item.get("val"), item.get("target")
                key = (conv, name)
                occ = {
                    "geo_key": gk,
                    "geo_label": _geo_label(gk),
                    "period_key": pk,
                    "period_label": _period_label(pk),
                    "val": val if _filled(val) else None,
                    "target": tgt if _filled(tgt) else None,
                    "unit": unit,
                    "updated_at": ts,
                }
                g = groups.get(key)
                if not g:
                    groups[key] = {
                        "name": name,
                        "unit": unit,
                        "conv_key": conv,
                        "conv_name": CONV_NAMES.get(conv, conv),
                        "taskish": _is_taskish(name),
                        "occurrences": [occ],
                    }
                else:
                    g["occurrences"].append(occ)
                    if unit and not g.get("unit"):
                        g["unit"] = unit

    out = []
    for g in groups.values():
        occs = sorted(g["occurrences"], key=lambda o: o["updated_at"] or "",
                      reverse=True)
        first = min(occs, key=lambda o: o["updated_at"] or "9999")
        latest = occs[0]
        geos = {o["geo_key"] for o in occs}
        periods = {o["period_key"] for o in occs}
        cities = {(_geo_label(o["geo_key"]).split("·")[0]) for o in occs}
        out.append({
            "name": g["name"],
            "unit": g["unit"],
            "conv_key": g["conv_key"],
            "conv_name": g["conv_name"],
            "taskish": g["taskish"],
            "origin": {
                "geo_key": first["geo_key"],
                "geo_label": first["geo_label"],
                "period_key": first["period_key"],
                "period_label": first["period_label"],
            },
            "latest": {
                "geo_key": latest["geo_key"],
                "geo_label": latest["geo_label"],
                "period_key": latest["period_key"],
                "period_label": latest["period_label"],
                "val": latest["val"],
                "target": latest["target"],
                "unit": latest["unit"],
            },
            "geo_count": len(geos),
            "city_count": len(cities),
            "period_count": len(periods),
            "use_count": len(occs),
            "occurrences": occs,
        })
    # 用得多的在前；次数相同再按覆盖地市、最近更新
    out.sort(key=lambda x: (
        x["use_count"],
        x["city_count"],
        x["occurrences"][0].get("updated_at") or "",
    ), reverse=True)
    return out


def build_factor_library(conv_key: Optional[str] = None) -> Dict[str, Any]:
    return {
        "conv_key": conv_key,
        "system": _system_defs(conv_key),
        "community": _harvest_community(conv_key),
    }
