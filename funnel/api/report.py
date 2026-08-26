"""
漏斗报告数据：供 /api/funnel/report-data 与 CLI 生成器共用。

scope 由 geo_key 决定：
  浙江//     → 全省（11 地市明细 + 全省汇总）
  浙江/杭州市/ → 地市（vs 省均 benchmark）
  浙江/杭州市/西湖区 → 区县（vs 本市 benchmark）
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from . import factors as fac
from .overview import JUMPS, WEAK_RATE, _empty_counts, _pct, _lookup_target, _add_counts
from .periods import period_meta, period_label as pk_label, weeks_in_month
from .prod_db import (
    NOT_PLAQUE, SO_LINE_FILTER, TIER_RANK,
    _conn, _geo_filter, _month_rhythm, available,
    so_events, _so_year,
)

CITIES = [
    "杭州市", "宁波市", "温州市", "嘉兴市", "湖州市", "绍兴市",
    "金华市", "衢州市", "舟山市", "台州市", "丽水市",
]


def parse_geo_key(geo_key: str) -> Tuple[str, Optional[str], Optional[str]]:
    parts = (geo_key or "").strip("/").split("/")
    province = parts[0] if parts else "浙江"
    city = parts[1] if len(parts) > 1 and parts[1] else None
    district = parts[2] if len(parts) > 2 and parts[2] else None
    return province, city, district


def scope_kind(city: Optional[str], district: Optional[str]) -> str:
    if district:
        return "district"
    if city:
        return "city"
    return "province"


def _so_period(conn, city: Optional[str], district: Optional[str],
               start: str, end: str) -> Dict[str, Any]:
    gf, gp = _geo_filter(city, district, "p.客户城市", "p.客户区县")
    row = conn.execute(
        f"""SELECT COUNT(*) lines,
                   COUNT(DISTINCT r.上线客户编码) providers,
                   COALESCE(SUM(r.产品现有分销价), 0) amount
            FROM install_redpack r
            JOIN provider_contract p ON p.客户编码 = r.上线客户编码
            WHERE COALESCE(r.上线客户编码,'') <> ''
              AND {NOT_PLAQUE.replace('管理标签', 'p.管理标签')}
              AND date(r.上线时间) BETWEEN ? AND ?
              AND {SO_LINE_FILTER.replace('国内产品线二级', 'r.国内产品线二级').replace('产品名称', 'r.产品名称')}
              {gf}""",
        [start, end] + gp,
    ).fetchone()
    return {
        "so_lines": row["lines"] or 0,
        "so_providers": row["providers"] or 0,
        "so_amount": round(float(row["amount"] or 0), 0),
    }


def _evidence(conn, city: Optional[str], district: Optional[str],
              start: str, end: str) -> Dict[str, Any]:
    v_all = fac.visit_all(conn, city, district, start, end) or {}
    v_dh = fac.visit_dahua(conn, city, district, start, end) or {}
    v_ag = fac.visit_dealer(conn, city, district, start, end) or {}
    new_open = fac.new_open_providers(conn, city, district, start, end) or {}
    return {
        "visit": v_all.get("value") or 0,
        "visit_dahua": v_dh.get("value") or 0,
        "visit_dealer": v_ag.get("value") or 0,
        "visit_customers": v_all.get("customers") or 0,
        "visit_people": v_all.get("people") or 0,
        "new_open": new_open.get("value") or 0,
        **_so_period(conn, city, district, start, end),
    }


def _load_counts(start: str, end: str) -> Dict[str, Any]:
    """区县级存量 + 增量；顺带城市/全省汇总。"""
    with _conn() as conn:
        pool_rows = conn.execute(
            "SELECT 城市, 区县, COALESCE(SUM(服务商体量),0) pool "
            "FROM district_base "
            "WHERE 城市 IS NOT NULL AND 城市 <> '' "
            "GROUP BY 城市, 区县"
        ).fetchall()

        events = so_events(_so_year(end, start))
        districts: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for r in pool_rows:
            key = (r["城市"], r["区县"] or "")
            districts.setdefault(key, {
                "ytd": _empty_counts(), "period": _empty_counts(),
            })
            districts[key]["ytd"]["pool"] = r["pool"] or 0

        rows = conn.execute(
            f"""SELECT 客户城市 AS city, 客户区县 AS dist, 客户编码 AS code,
                       date(签约日期) AS signed, date(激活时间) AS act,
                       {TIER_RANK} AS rank
                FROM provider_contract WHERE {NOT_PLAQUE}"""
        ).fetchall()

        for r in rows:
            key = (r["city"], r["dist"] or "")
            if key not in districts:
                districts[key] = {"ytd": _empty_counts(), "period": _empty_counts()}
            yslot = districts[key]["ytd"]
            pslot = districts[key]["period"]
            signed, act, rank = r["signed"], r["act"], r["rank"]
            ev = events.get(r["code"] or "") or {}
            first_so, v3_time = ev.get("first_so"), ev.get("v3_time")

            if signed is not None and signed <= end:
                yslot["authorized"] += 1
                if rank is not None:
                    if rank >= 1 and first_so and first_so <= end:
                        yslot["activated_v1"] += 1
                    if rank >= 2 and act and act <= end:
                        yslot["activated"] += 1
                    if (rank >= 3 and act and act <= end
                            and v3_time and v3_time <= end):
                        yslot["senior"] += 1
            if signed and start <= signed <= end:
                pslot["authorized"] += 1
            if rank is not None:
                if rank >= 1 and first_so and start <= first_so <= end:
                    pslot["activated_v1"] += 1
                if rank >= 2 and act and start <= act <= end:
                    pslot["activated"] += 1
                if rank >= 3 and v3_time and start <= v3_time <= end:
                    pslot["senior"] += 1

        year = int(start[:4])
        month = int(start[5:7])
        tgt_rows = conn.execute(
            "SELECT * FROM provider_target WHERE 年度 = ?", (year,)
        ).fetchall()
        targets = {r["地市"]: dict(r) for r in tgt_rows}
        rhythm = _month_rhythm(conn, year, month)

    return {"districts": districts, "targets": targets, "rhythm": rhythm,
            "year": year, "month": month}


def _sum_districts(districts: Dict[Tuple[str, str], Dict[str, Any]],
                   city: Optional[str] = None,
                   dist_filter: Optional[str] = None) -> Tuple[Dict[str, int], Dict[str, int]]:
    ytd, period = _empty_counts(), _empty_counts()
    for (ct, dist), slot in districts.items():
        if city and ct != city:
            continue
        if dist_filter is not None and dist != dist_filter:
            continue
        _add_counts(ytd, slot["ytd"])
        _add_counts(period, slot["period"])
    return ytd, period


def _rates(ytd: Dict[str, int]) -> Dict[str, Optional[float]]:
    return {k: _pct(ytd.get(nk), ytd.get(dk)) for k, _, nk, dk in JUMPS}


def _weak_keys(rates: Dict[str, Optional[float]]) -> List[str]:
    return [k for k, v in rates.items() if v is not None and v < WEAK_RATE]


def _period_auth_target(targets: Dict[str, Any], city: Optional[str],
                        rhythm: Optional[float], meta: Dict[str, Any]) -> Optional[float]:
    row = _lookup_target(targets, city)
    if not row:
        return None
    annual = row.get("服务商签约数_含个人")
    if annual is None:
        return None
    r = rhythm if rhythm is not None else 1.0 / 12
    if meta["type"] == "week":
        share = meta.get("week_share") or (1.0 / weeks_in_month(meta["year"], meta["month"]))
        return round(annual * r * share)
    return round(annual * r)


def _unit(name: str, key: str, ytd: Dict[str, int], period: Dict[str, int],
          bench: Dict[str, Optional[float]], evidence: Dict[str, Any],
          period_target: Optional[float]) -> Dict[str, Any]:
    rates = _rates(ytd)
    return {
        "key": key,
        "name": name,
        "ytd": ytd,
        "period": period,
        "rates": rates,
        "benchmark_rates": bench,
        "weak": _weak_keys(rates),
        "period_target": period_target,
        "evidence": evidence,
    }


def build_report_data(geo_key: str, period_key: str) -> Dict[str, Any]:
    if not available():
        raise ValueError("生产库不可用")
    province, city, district = parse_geo_key(geo_key)
    meta = period_meta(period_key)
    start, end = meta["start"], meta["end"]
    bundle = _load_counts(start, end)
    districts = bundle["districts"]
    targets = bundle["targets"]
    rhythm = bundle["rhythm"]
    kind = scope_kind(city, district)

    with _conn() as conn:
        if kind == "province":
            prov_ytd, prov_period = _sum_districts(districts)
            prov_ev = _evidence(conn, None, None, start, end)
            bench = _rates(prov_ytd)
            period_lbl = "本周" if meta["type"] == "week" else "本月"
            units: List[Dict[str, Any]] = [{
                **_unit("全省", "province", prov_ytd, prov_period, bench, prov_ev,
                       _period_auth_target(targets, None, rhythm, meta)),
            }]
            overview: List[Dict[str, Any]] = []
            for ct in CITIES:
                cy, cp = _sum_districts(districts, city=ct)
                ev = _evidence(conn, ct, None, start, end)
                u = _unit(ct.rstrip("市"), ct, cy, cp, bench, ev,
                          _period_auth_target(targets, ct, rhythm, meta))
                units.append(u)
                overview.append(u)
            scope_label = "全省"
        elif kind == "city":
            prov_ytd, _ = _sum_districts(districts)
            bench = _rates(prov_ytd)
            cy, cp = _sum_districts(districts, city=city)
            ev = _evidence(conn, city, None, start, end)
            u = _unit(city.rstrip("市"), city or "", cy, cp, bench, ev,
                      _period_auth_target(targets, city, rhythm, meta))
            units = [u]
            overview = [u]
            scope_label = city.rstrip("市") if city else "全省"
        else:
            city_ytd, _ = _sum_districts(districts, city=city)
            bench = _rates(city_ytd)
            dy, dp = _sum_districts(districts, city=city, dist_filter=district or "")
            ev = _evidence(conn, city, district, start, end)
            u = _unit(district or "", f"{city}/{district}", dy, dp, bench, ev, None)
            units = [u]
            overview = [u]
            scope_label = district or ""

    rhythm_pct = round(rhythm * 100, 1) if rhythm is not None else None
    if meta["type"] == "week":
        wks = meta.get("weeks_in_month") or weeks_in_month(meta["year"], meta["month"])
        tgt_note = (f"签约目标 = 全年 × {rhythm_pct}%（{meta['month']}月节奏）÷ {wks} 周"
                    if rhythm_pct is not None else "签约目标按节奏分解")
    else:
        tgt_note = (f"签约目标 = 全年 × {rhythm_pct}%（{meta['month']}月节奏）"
                    if rhythm_pct is not None else "签约目标按节奏分解")

    ptype = meta["type"]
    report_title = "周报" if ptype == "week" else "月报"

    return {
        "scope": kind,
        "scope_label": scope_label,
        "province": province,
        "city": city,
        "district": district,
        "period_key": period_key,
        "period_type": ptype,
        "period_label": pk_label(period_key),
        "period_range": f"{start} ~ {end}",
        "start": start,
        "end": end,
        "report_title": report_title,
        "period_word": "本周" if ptype == "week" else "本月",
        "benchmark_label": "省均" if kind != "district" else "本市",
        "rhythm_pct": rhythm_pct,
        "target_note": tgt_note,
        "weak_threshold": WEAK_RATE,
        "generated": date.today().isoformat(),
        "headline": units[0],
        "units": units,
        "overview": overview if kind == "province" else None,
        "jumps": [{"key": k, "label": lbl} for k, lbl, _, _ in JUMPS],
    }
