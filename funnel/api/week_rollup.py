"""
月度 ← 各周切片。周是录入粒度，月是复盘粒度。

不把各周关键因素平均成一个「月度值」——百分比跨周平均会骗人。
月度视图列出当月各周（周四归属，与目标节奏同一套），标出哪一周填了什么，
点周号回到该周编辑。自动因子仍按月区间现算，那是当月真实数，不是周的卷积。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .db import list_states_for_geo_periods, load_latest_state
from .periods import period_meta, weeks_of_month


def build_week_rollup(geo_key: str, period_key: str) -> Dict[str, Any]:
    meta = period_meta(period_key)
    if meta["type"] != "month":
        return {
            "mode": "week",
            "period_key": period_key,
            "weeks": [],
            "self": load_latest_state(geo_key, period_key) or {},
        }

    week_keys = weeks_of_month(meta["year"], meta["month"])
    stored = list_states_for_geo_periods(geo_key, week_keys)
    weeks: List[Dict[str, Any]] = []
    for wk in week_keys:
        wm = period_meta(wk)
        st = stored.get(wk) or {}
        weeks.append({
            "period_key": wk,
            "label": f"W{wm['week']:02d}",
            "start": wm["start"],
            "end": wm["end"],
            "state": st,
            "has_data": bool(st),
        })
    return {
        "mode": "month",
        "period_key": period_key,
        "weeks": weeks,
        "self": load_latest_state(geo_key, period_key) or {},
        "notes": [
            "周按周四归属到月，与目标节奏同一口径。",
            "手填关键因素、待办、问题按周列出，不平均成月度值。",
            "自动因子仍按本月日期区间现算。",
        ],
    }
