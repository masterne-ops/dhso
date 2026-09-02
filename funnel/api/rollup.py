"""
下级目标卷积（省 ← 11 地市，市 ← 其下区县）。

为什么要单独一个模块：卷积要同时读两个库。
  - 用户改过的目标 → funnel.db 的 funnel_state.state_json.targets（本地库，可写）
  - 没改过的预设值 → product_flow.db 的 provider_target × kpi_rhythm（生产库，只读）

**关键点：预设值不落库。** funnel_state 里只存用户手动改过的目标，一个从没被人
打开过的地市在本地库里连行都没有。所以「把已存切片加一遍」会把这些地市算成 0，
卷积值凭空缩水。正确口径是逐个下级取**生效目标**：

    生效目标 = 用户改过的值 ?? 该地区的节奏预设值

两者都没有才算缺口，并且**明确报出来是哪几个地区缺**，而不是当 0 加进去 ——
把缺口当 0 会让卷积值看起来"已经加全了"，那比不给数更危险。

口径差异（有意保留，前端要显示出来）：
  省级卷积地市 —— 11 个地市在 provider_target 里都有下发值，所以即使没人填过，
                  预设也是齐的，卷积值天然完整。
  市级卷积区县 —— provider_target **只到地市级**，区县没有下发目标也就没有预设。
                  没人手填过的区县就是真缺口，此时卷积值可能是 null 或只覆盖了
                  一部分区县。这不是 bug，是下发粒度决定的。

卷积值**只做参照，不写回、不覆盖任何下发值**。省级目标是年初独立下达的
（「浙江合计」是 provider_target 里自己一行），通常留了缓冲、不等于 11 个地市
之和；用地市之和覆盖省级目标等于把下发值改掉。所以这里只回报"下级加起来是多少"
和"和本级目标差多少"，改不改由人判断。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .db import get_conn
from .periods import period_meta
from .prod_db import available as prod_available, geo_tree, targets as prod_targets

# 可被目标管理的四阶，与前端 renderSideTable 的 LEVELS 一致
LEVELS = ("authorized", "activated_v1", "activated", "senior")


def _split(geo_key: str) -> Tuple[str, Optional[str], Optional[str]]:
    """geo_key → (省, 市|None, 区县|None)。形如 '浙江//' / '浙江/杭州市/'。"""
    parts = (geo_key or "").split("/")
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1] or None, parts[2] or None


def _child_keys(geo_key: str) -> Tuple[str, List[str], str]:
    """
    本级的**全部**直接下级 geo_key（不管有没有存过切片）。

    下级清单取自生产库的 district_base 地区树，而不是"库里存过切片的地区" ——
    后者会漏掉从没被人打开过的地区，而那些恰恰是最需要暴露的缺口。
    返回 (下级类型, [下级 geo_key], 说明)。
    """
    province, city, district = _split(geo_key)
    if district:
        return "none", [], "区县是最细粒度，没有下级可卷积。"
    tree = geo_tree()
    if not tree:
        return "none", [], "生产库不可用，取不到下级地区清单。"
    if city:
        for c in tree["cities"]:
            if c["city"] == city:
                return "district", [f"{province}/{city}/{d}"
                                    for d in c["districts"]], ""
        return "district", [], f"地区树里没有「{city}」。"
    return "city", [f"{province}/{c['city']}/" for c in tree["cities"]], ""


def _stored_targets(child_keys: List[str],
                    period_key: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    下级地区里**用户改过**的目标。返回 {geo_key: {"ytd": {...}, "period": {...}}}。

    ytd 与 period 的取法不同，因为 state_json.targets 存的是该地区**整个** targets
    桶（含 ytd 和所有周期），而 funnel_state 一行只对应一个周期：
      - period → 只认 period_key 那一行
      - ytd    → 该地区任意一行都带着同一份 ytd，取 updated_at 最新的那行
    """
    if not child_keys:
        return {}
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    qs = ",".join("?" * len(child_keys))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT geo_key, period_key, state_json FROM funnel_state "
            f"WHERE geo_key IN ({qs}) ORDER BY updated_at",
            child_keys).fetchall()
    for r in rows:
        try:
            blob = json.loads(r["state_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        tg = (blob or {}).get("targets") if isinstance(blob, dict) else None
        # 切片是前端写进来的自由 JSON，targets 不一定是字典（历史数据/手工改库/
        # 客户端 bug 都可能写进别的类型）。类型不对就当"没填过"，回落预设值 ——
        # 卷积不该因为一个地区的脏数据整个 500。
        if not isinstance(tg, dict):
            continue
        slot = out.setdefault(r["geo_key"], {"ytd": {}, "period": {}})
        # 按 updated_at 升序遍历，后写的覆盖前面的 → 最终留下最新的 ytd
        if isinstance(tg.get("ytd"), dict):
            slot["ytd"] = tg["ytd"]
        if r["period_key"] == period_key and isinstance(tg.get(period_key), dict):
            slot["period"] = tg[period_key]
    return out


def rollup(geo_key: str, period_key: str) -> Dict[str, Any]:
    """
    把直接下级的目标加起来。

    每个下级每一档取「用户改过的值 ?? 节奏预设值」，两者皆无记为缺口。
    返回值里同时给出 sum、各档的来源计数、缺口地区名单，以及与本级目标的差额。
    """
    meta = period_meta(period_key)          # 非法周期键在路由层已挡，这里会抛 ValueError
    kind, children, why = _child_keys(geo_key)
    base = {"geo_key": geo_key, "period_key": period_key,
            "children_kind": kind, "children_total": len(children)}

    if kind == "none" or not children:
        return {**base, "available": False, "ytd": None, "period": None,
                "detail": [], "notes": [why or "没有可卷积的下级地区。"]}

    stored = _stored_targets(children, period_key)

    detail: List[Dict[str, Any]] = []
    # 每档分别累计：值、来源计数、缺口地区
    agg = {scope: {lv: {"sum": 0, "stored": 0, "preset": 0, "missing": []}
                   for lv in LEVELS} for scope in ("ytd", "period")}

    for ck in children:
        _, c_city, c_district = _split(ck)
        preset = prod_targets(city=c_city, district=c_district,
                              period_key=period_key, year=meta["year"],
                              month=meta["month"],
                              week_share=meta["week_share"]) or {}
        row: Dict[str, Any] = {"geo_key": ck,
                               "name": c_district or c_city or ck,
                               "ytd": {}, "period": {}, "src": {}}
        for scope in ("ytd", "period"):
            for lv in LEVELS:
                sv = (stored.get(ck, {}).get(scope) or {}).get(lv)
                pv = (preset.get(scope) or {}).get(lv)
                if sv is not None:
                    val, src = sv, "stored"
                elif pv is not None:
                    val, src = pv, "preset"
                else:
                    val, src = None, None
                row[scope][lv] = val
                row["src"][f"{scope}.{lv}"] = src
                a = agg[scope][lv]
                if src is None:
                    a["missing"].append(row["name"])
                else:
                    a["sum"] += val
                    a[src] += 1
        detail.append(row)

    # 本级自己的目标，用来算差额（省级/市级都有下发值；区县进不到这里）
    _, s_city, s_district = _split(geo_key)
    own = prod_targets(city=s_city, district=s_district, period_key=period_key,
                       year=meta["year"], month=meta["month"],
                       week_share=meta["week_share"]) or {}

    def pack(scope: str) -> Dict[str, Any]:
        out = {}
        for lv in LEVELS:
            a = agg[scope][lv]
            got = a["stored"] + a["preset"]
            own_v = (own.get(scope) or {}).get(lv)
            out[lv] = {
                # 一个下级都没有值时给 null，不给 0 —— 0 会被读成"下级目标是 0"
                "sum": a["sum"] if got else None,
                "n_stored": a["stored"], "n_preset": a["preset"],
                "n_missing": len(a["missing"]),
                # 名单截断，避免 97 个区县全塞进响应
                "missing": a["missing"][:12],
                "own": own_v,
                "diff": (a["sum"] - own_v) if (got and own_v is not None) else None,
            }
        return out

    notes: List[str] = []
    if kind == "city":
        notes.append("卷积口径：11 个地市逐个取「用户改过的值 ?? 该地市全年/节奏预设」"
                     "后求和。2026 年预设来自省区填写任务表（签约/V2+/V3+）。")
    else:
        notes.append("卷积口径：本市各区县逐个取「用户改过的值 ?? 预设值」后求和。"
                     "⚠️ 目标只下发到地市级，区县没有预设值 —— "
                     "没人手填过的区县是真缺口，不按体量摊派（那是编数）。")
    notes.append("卷积值仅作参照，不写回、不覆盖本级下发目标：本级目标是独立"
                 "下达的全年任务，通常留有缓冲，与下级之和不相等是正常的。")
    if any(pack("period")[lv]["n_missing"] for lv in LEVELS):
        notes.append("有下级缺目标值，缺口按「未纳入」处理、不按 0 计入 —— "
                     "否则卷积值看起来是加全了的。")

    return {**base, "available": True,
            "ytd": pack("ytd"), "period": pack("period"),
            "detail": detail, "prod_available": prod_available(),
            "notes": notes}
