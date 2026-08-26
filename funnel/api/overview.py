"""
管辖总览：一次查出下级单位的漏斗转化率 + 待办/问题/思考，给总览页用。

漏斗工作页继续只编辑「一个地区 × 一个周期」。总览是只读横比 + 催办清单，
不在这里改目标或关键因素。勾选待办/问题仍写回该地区切片；思考/目标只读。

取数约束：
- 不能对每个单位串行打 /data + /state + /factor-values（11 市 × 90 区县会打挂）。
- 生产库按区县 GROUP BY 两三次扫完；切片按 period_key 一次读出。
- 短板用期末存量转化率 < 70%（与漏斗页 renderFocus 同口径），不做因子库热力图。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from .db import (
    list_states_for_period_keys, load_latest_state, get_state_meta, save_state,
)
from .periods import period_label, period_meta, weeks_of_month
from .prod_db import (
    NOT_PLAQUE, TIER_RANK, TARGET_CITY_ALL,
    so_events, _so_year,
    available as prod_available, geo_tree, _conn,
)
from .users import User, authorize_geo, scoped_geo_tree

JUMPS = (
    ("p2a", "渗透率", "authorized", "pool"),
    ("a2v1", "授权→开单", "activated_v1", "authorized"),
    ("a2t", "开单→激活", "activated", "activated_v1"),
    ("t2v", "激活→高级", "senior", "activated"),
)
WEAK_RATE = 70.0
TARGET_GAP = 0.9
COUNT_KEYS = ("pool", "authorized", "activated_v1", "activated", "senior")


def _geo_key(province: str, city: Optional[str] = None,
             district: Optional[str] = None) -> str:
    return f"{province}/{city or ''}/{district or ''}"


def _pct(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or not den:
        return None
    return round(num / den * 100, 1)


def _empty_counts() -> Dict[str, int]:
    return {k: 0 for k in COUNT_KEYS}


def _add_counts(dst: Dict[str, int], src: Dict[str, Any]) -> None:
    for k in COUNT_KEYS:
        dst[k] = (dst.get(k) or 0) + (src.get(k) or 0)


def _list_items(items: Any, *, default_done: bool = False,
                period_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """全部非空条目（含已完成），保留原始下标，总览勾选才能写回同一条。
    空行不算；删掉的不在切片里，自然不会出现。月度视图会带上来源周。"""
    out: List[Dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for i, raw in enumerate(items):
        if isinstance(raw, str):
            text, done = raw, default_done
        elif isinstance(raw, dict):
            text = str(raw.get("text") or "")
            done = bool(raw["done"]) if "done" in raw else default_done
        else:
            continue
        if not text.strip():
            continue
        rec = {"index": i, "text": text, "done": bool(done)}
        if period_key:
            rec["period_key"] = period_key
            rec["period_label"] = period_label(period_key)
        out.append(rec)
    return out


def _notes_from_periods(state_map: Dict[str, Dict[str, Any]],
                        prefer_key: str) -> List[Dict[str, Any]]:
    """月切片 + 当月各周切片里的思考/目标。空串不要。月度在前，再按周序。"""
    out: List[Dict[str, Any]] = []
    keys = []
    if prefer_key in state_map:
        keys.append(prefer_key)
    keys.extend(sorted(k for k in state_map if k != prefer_key))
    for pk in keys:
        raw = (state_map.get(pk) or {}).get("notes")
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue
        out.append({
            "text": text,
            "period_key": pk,
            "period_label": period_label(pk),
        })
    return out


def _items_from_periods(state_map: Dict[str, Dict[str, Any]],
                        prefer_key: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把月切片 + 当月各周切片的待办/问题拼在一起。月度条目在前，再按周序。"""
    todos: List[Dict[str, Any]] = []
    questions: List[Dict[str, Any]] = []
    keys = []
    if prefer_key in state_map:
        keys.append(prefer_key)
    keys.extend(sorted(k for k in state_map if k != prefer_key))
    for pk in keys:
        st = state_map.get(pk) or {}
        todos.extend(_list_items(st.get("todos"), period_key=pk))
        questions.extend(_list_items(st.get("questions"), period_key=pk))
    return todos, questions


def _state_map_for(geo_key: str,
                   by_geo: Dict[str, Dict[str, Dict[str, Any]]]
                   ) -> Dict[str, Dict[str, Any]]:
    hit = by_geo.get(geo_key)
    if hit:
        return hit
    alt = geo_key.rstrip("/") if geo_key.endswith("/") else geo_key + "/"
    return by_geo.get(alt) or {}


def _lookup_target(by_city: Dict[str, Any], city: Optional[str]):
    """provider_target 地市名可能带/不带「市」，与 prod_db._target_row 同口径。"""
    key = city or TARGET_CITY_ALL
    if key in by_city:
        return by_city[key]
    if city:
        stripped = city.rstrip("市")
        if stripped in by_city:
            return by_city[stripped]
        with_shi = city if city.endswith("市") else city + "市"
        if with_shi in by_city:
            return by_city[with_shi]
    return None


def _load_prod_bundle(start: str, end: str, year: int, month: int
                      ) -> Optional[Dict[str, Any]]:
    """三次生产库扫描：体量、期末存量 + 本期增量、全年目标 + 当月节奏。"""
    if not prod_available():
        return None
    with _conn() as conn:
        pool_rows = conn.execute(
            "SELECT 城市, 区县, COALESCE(SUM(服务商体量),0) pool "
            "FROM district_base "
            "WHERE 城市 IS NOT NULL AND 城市 <> '' "
            "GROUP BY 城市, 区县"
        ).fetchall()
        events = so_events(_so_year(end, start))
        contract_rows = conn.execute(
            f"""SELECT 客户城市, 客户区县, 客户编码 AS code,
                       date(签约日期) AS signed, date(激活时间) AS act,
                       {TIER_RANK} AS rank
                FROM provider_contract WHERE {NOT_PLAQUE}"""
        ).fetchall()
        ytd_by: Dict[Tuple[str, str], Dict[str, int]] = {}
        period_by: Dict[Tuple[str, str], Dict[str, int]] = {}
        for r in contract_rows:
            city, dist = r["客户城市"], r["客户区县"] or ""
            key = (city, dist)
            yslot = ytd_by.setdefault(key, _empty_counts())
            pslot = period_by.setdefault(key, _empty_counts())
            signed, act, rank = r["signed"], r["act"], r["rank"]
            ev = events.get(r["code"] or "") or {}
            first_so, v3_time = ev.get("first_so"), ev.get("v3_time")
            if signed is not None and signed <= end:
                yslot["authorized"] += 1
                if rank is not None:
                    if rank >= 1 and first_so is not None and first_so <= end:
                        yslot["activated_v1"] += 1
                    if rank >= 2 and act is not None and act <= end:
                        yslot["activated"] += 1
                    if (rank >= 3 and act is not None and act <= end
                            and v3_time is not None and v3_time <= end):
                        yslot["senior"] += 1
            if signed is not None and start <= signed <= end:
                pslot["authorized"] += 1
            if rank is not None:
                if rank >= 1 and first_so is not None and start <= first_so <= end:
                    pslot["activated_v1"] += 1
                if rank >= 2 and act is not None and start <= act <= end:
                    pslot["activated"] += 1
                if rank >= 3 and v3_time is not None and start <= v3_time <= end:
                    pslot["senior"] += 1
        ytd_rows = [
            {"客户城市": c, "客户区县": d, **vals} for (c, d), vals in ytd_by.items()
        ]
        period_rows = [
            {"客户城市": c, "客户区县": d, **vals} for (c, d), vals in period_by.items()
        ]
        tgt_rows = conn.execute(
            "SELECT * FROM provider_target WHERE 年度 = ?", (year,)
        ).fetchall()
        rhythm_row = conn.execute(
            "SELECT 占比 FROM kpi_rhythm WHERE 指标 = 'SMB服务商' "
            "AND 适用范围 = '服务商签约' AND 年度 = ? AND 月份 = ?",
            (year, month)
        ).fetchone()

    districts: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in pool_rows:
        city, dist = r["城市"], r["区县"] or ""
        slot = districts.setdefault((city, dist), {
            "ytd": _empty_counts(), "period": _empty_counts()
        })
        slot["ytd"]["pool"] = r["pool"] or 0
    for r in ytd_rows:
        city, dist = r["客户城市"], r["客户区县"] or ""
        slot = districts.setdefault((city, dist), {
            "ytd": _empty_counts(), "period": _empty_counts()
        })
        for k in ("authorized", "activated_v1", "activated", "senior"):
            slot["ytd"][k] = r[k] or 0
    for r in period_rows:
        city, dist = r["客户城市"], r["客户区县"] or ""
        slot = districts.setdefault((city, dist), {
            "ytd": _empty_counts(), "period": _empty_counts()
        })
        for k in ("authorized", "activated_v1", "activated", "senior"):
            slot["period"][k] = r[k] or 0

    targets = {}
    for r in tgt_rows:
        targets[r["地市"]] = dict(r)
    rhythm = rhythm_row["占比"] if rhythm_row and rhythm_row["占比"] is not None else None
    return {"districts": districts, "targets": targets, "rhythm": rhythm}


def _counts_for_city(city: str, districts: List[str],
                     dist_counts: Dict[Tuple[str, str], Dict[str, Any]]
                     ) -> Tuple[Dict[str, int], Dict[str, int]]:
    ytd, period = _empty_counts(), _empty_counts()
    known = set(districts or [])
    for dist in known:
        slot = dist_counts.get((city, dist))
        if slot:
            _add_counts(ytd, slot["ytd"])
            _add_counts(period, slot["period"])
    for (ct, dist), slot in dist_counts.items():
        if ct == city and dist not in known:
            _add_counts(ytd, slot["ytd"])
            _add_counts(period, slot["period"])
    return ytd, period


def _counts_for_cities(cities: List[Dict[str, Any]],
                       dist_counts: Dict[Tuple[str, str], Dict[str, Any]]
                       ) -> Tuple[Dict[str, int], Dict[str, int]]:
    ytd, period = _empty_counts(), _empty_counts()
    for c in cities:
        cy, cp = _counts_for_city(c["city"], c.get("districts") or [], dist_counts)
        _add_counts(ytd, cy)
        _add_counts(period, cp)
    return ytd, period


def _preset_auth(tgt_by_city: Dict[str, Any], city: Optional[str]) -> Optional[float]:
    row = _lookup_target(tgt_by_city, city) if tgt_by_city else None
    return row.get("服务商签约数_含个人") if row else None


def _is_admin(user: Optional[User]) -> bool:
    return user is None or user.is_admin


def _unit_metrics(ytd: Dict[str, Any], period: Dict[str, Any],
                  state: Dict[str, Any], preset_auth: Optional[float],
                  geo_key: str, name: str, city: Optional[str],
                  district: Optional[str], role: str,
                  state_map: Optional[Dict[str, Dict[str, Any]]] = None,
                  prefer_key: Optional[str] = None) -> Dict[str, Any]:
    rates = {}
    weak: List[Dict[str, Any]] = []
    worst = None
    for key, label, num_k, den_k in JUMPS:
        rate = _pct(ytd.get(num_k), ytd.get(den_k))
        rates[key] = rate
        jump = {"key": key, "label": label, "rate": rate}
        if rate is not None and rate < WEAK_RATE:
            weak.append(jump)
        if rate is not None and (worst is None or rate < worst["rate"]):
            worst = jump

    stored_ytd = (state.get("targets") or {}).get("ytd") if isinstance(
        state.get("targets"), dict) else None
    stored_auth = stored_ytd.get("authorized") if isinstance(stored_ytd, dict) else None
    if stored_auth is not None:
        target_auth, target_src = stored_auth, "stored"
    elif preset_auth is not None:
        target_auth, target_src = preset_auth, "preset"
    else:
        target_auth, target_src = None, None

    actual_auth = ytd.get("authorized")
    target_gap = (
        target_auth is not None and actual_auth is not None
        and actual_auth < target_auth * TARGET_GAP
    )

    if state_map:
        todos, questions = _items_from_periods(state_map, prefer_key or "")
        notes = _notes_from_periods(state_map, prefer_key or "")
    else:
        todos = _list_items(state.get("todos"), default_done=False)
        questions = _list_items(state.get("questions"), default_done=False)
        raw_note = state.get("notes")
        notes = []
        if isinstance(raw_note, str) and raw_note.strip():
            notes = [{
                "text": raw_note.strip(),
                "period_key": prefer_key or "",
                "period_label": period_label(prefer_key or ""),
            }]
    todo_open = sum(1 for t in todos if not t["done"])
    question_open = sum(1 for t in questions if not t["done"])

    return {
        "geo_key": geo_key,
        "name": name,
        "city": city,
        "district": district,
        "role": role,
        "ytd": ytd,
        "period": period,
        "rates": rates,
        "weak_count": len(weak),
        "weak_jumps": [w["key"] for w in weak],
        "worst": worst,
        "target": {"authorized": target_auth, "src": target_src},
        "target_gap": bool(target_gap),
        "todo_open": todo_open,
        "question_open": question_open,
        "todo_done": len(todos) - todo_open,
        "question_done": len(questions) - question_open,
        "todos": todos,
        "questions": questions,
        "notes": notes,
        "note_count": len(notes),
    }


def build_overview(user: Optional[User], period_key: str, level: str,
                   city_filter: Optional[str] = None) -> Dict[str, Any]:
    meta = period_meta(period_key)
    tree = scoped_geo_tree(user, geo_tree())
    notes = [
        f"转化率取所选周期期末存量（与漏斗箭头一致），短板阈值 {int(WEAK_RATE)}%。",
        "目标缺口：全年授权实际 < 90% 生效目标（手填 ?? 下发）。区县无下发目标，"
        "没手填过的不计入缺口。",
        "关键因素热力图本版不做，短板只看四跳转化率。",
        "管理员默认编辑位是全省、地市账号是全市；总览把这一级也列出来，"
        "否则漏斗页刚记下的待办/问题/思考会在总览里消失。",
    ]

    if city_filter:
        probe = _geo_key((tree or {}).get("province") or "浙江", city_filter)
        if not authorize_geo(user, probe):
            raise HTTPException(
                status_code=403,
                detail=f"无权查看「{city_filter}」。当前账号范围是 "
                       f"{user.scope if user else '*'}。")

    cities = list((tree or {}).get("cities") or [])
    if city_filter:
        cities = [c for c in cities if c["city"] == city_filter]

    base = {
        "period_key": period_key,
        "period": meta,
        "level": level,
        "city": city_filter,
        "user": user.as_public() if user else None,
        "source": "unavailable",
        "kpi": {"open_todos": 0, "open_questions": 0, "with_notes": 0,
                "weak_units": 0, "target_gap_units": 0},
        "units": [],
        "notes": notes,
    }
    if not tree or not cities:
        if not tree:
            notes.append("生产库不可用，总览没有地区清单。")
        return base

    province = tree["province"]
    bundle = _load_prod_bundle(meta["start"], meta["end"],
                               meta["year"], meta["month"])
    if bundle is None:
        notes.append("生产库不可用，总览没有漏斗数字。")
        return base

    period_keys = [period_key]
    if meta["type"] == "month":
        period_keys.extend(weeks_of_month(meta["year"], meta["month"]))
        notes.append("月度总览含当月各周待办/问题/思考（周四归属），条目带来源周，勾选写回该周。")
    by_geo = list_states_for_period_keys(period_keys)
    dist_counts = bundle["districts"]
    tgt_by_city = bundle["targets"]

    units: List[Dict[str, Any]] = []

    def add_unit(gk, name, city, district, role, ytd, period, preset):
        smap = _state_map_for(gk, by_geo)
        units.append(_unit_metrics(
            ytd, period, smap.get(period_key) or {}, preset,
            gk, name, city, district, role,
            state_map=smap, prefer_key=period_key))

    # 当前账号的默认编辑位（全省 / 全市）必须进总览：漏斗页打开就是这一级，
    # 待办、问题和思考写在对应切片上。只列下级的话，刚录入的内容会像「丢了」。
    if level == "city" and _is_admin(user):
        ytd, period = _counts_for_cities(cities, dist_counts)
        add_unit(_geo_key(province), "全省", None, None, "province",
                 ytd, period, _preset_auth(tgt_by_city, None))
    elif level == "district" and len(cities) == 1:
        c0 = cities[0]
        ytd, period = _counts_for_city(
            c0["city"], c0.get("districts") or [], dist_counts)
        add_unit(_geo_key(province, c0["city"]), "全市", c0["city"], None, "city",
                 ytd, period, _preset_auth(tgt_by_city, c0["city"]))
    elif level == "district" and _is_admin(user) and not city_filter:
        ytd, period = _counts_for_cities(cities, dist_counts)
        add_unit(_geo_key(province), "全省", None, None, "province",
                 ytd, period, _preset_auth(tgt_by_city, None))

    if level == "city":
        for c in cities:
            city = c["city"]
            ytd, period = _counts_for_city(
                city, c.get("districts") or [], dist_counts)
            gk = _geo_key(province, city)
            add_unit(gk, city, city, None, "city",
                     ytd, period, _preset_auth(tgt_by_city, city))
    else:
        for c in cities:
            city = c["city"]
            for dist in c.get("districts") or []:
                slot = dist_counts.get((city, dist)) or {
                    "ytd": _empty_counts(), "period": _empty_counts()
                }
                gk = _geo_key(province, city, dist)
                add_unit(gk, dist, city, dist, "district",
                         slot["ytd"], slot["period"], None)

    kpi = {
        "open_todos": sum(u["todo_open"] for u in units),
        "open_questions": sum(u["question_open"] for u in units),
        "with_notes": sum(1 for u in units if u["note_count"]),
        "weak_units": sum(1 for u in units if u["weak_count"] > 0),
        "target_gap_units": sum(1 for u in units if u["target_gap"]),
    }
    return {
        **base,
        "source": "prod",
        "kpi": kpi,
        "units": units,
        "city_count": len(cities),
        "unit_count": len(units),
    }


def patch_item(geo_key: str, period_key: str, kind: str, index: int,
               done: bool, text: Optional[str] = None) -> Dict[str, Any]:
    """勾选一条待办/问题，只改这一项，不覆盖整份切片。"""
    if kind not in ("todo", "question"):
        raise HTTPException(status_code=422, detail="kind 必须是 todo 或 question")
    key = "todos" if kind == "todo" else "questions"
    state = load_latest_state(geo_key, period_key)
    if not state:
        raise HTTPException(status_code=404, detail="该地区本周期还没有切片")
    items = list(state.get(key) or [])
    if not items:
        raise HTTPException(status_code=404, detail="没有可勾选的条目")

    def as_dict(raw):
        if isinstance(raw, dict):
            return raw
        return {"text": str(raw)}

    pick = index
    if text is not None:
        want = str(text)
        cur = as_dict(items[index]).get("text") if 0 <= index < len(items) else None
        if cur != want:
            pick = next((i for i, it in enumerate(items)
                         if as_dict(it).get("text") == want), None)
            if pick is None:
                raise HTTPException(status_code=409,
                                    detail="条目已变化，请刷新后再勾选")
    if pick is None or pick < 0 or pick >= len(items):
        raise HTTPException(status_code=404, detail="条目下标无效")

    item = as_dict(items[pick])
    item["done"] = bool(done)
    items[pick] = item
    state[key] = items

    meta = get_state_meta(geo_key, period_key)
    period_type = (meta or {}).get("period_type") or (
        "week" if "W" in period_key else "month")
    updated_at = save_state(geo_key, period_type, period_key, state)
    return {"ok": True, "updated_at": updated_at, "geo_key": geo_key,
            "kind": kind, "index": pick, "done": bool(done)}
