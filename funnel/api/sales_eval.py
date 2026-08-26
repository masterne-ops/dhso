"""
业务员 / 代理商能力评估。

对齐「渠道生意关注指标」：
  1) 必要性跑动占比
  2) 客户维护规定动作合规率
  3) 跑动目的矩阵 + ROI
  4) 分档规定动作清单

只读生产库；拜访目的 → 6 类标准标签走 funnel.db 的 visit_purpose_map。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import calendar
from typing import Any, Dict, List, Optional, Set, Tuple

from .db import PURPOSE_LABELS, load_visit_purpose_map
from .factors import (
    IS_DAHUA, IS_DEALER, SO_LINE_R, VISIT_TIME, visit_source,
    _auth_cert_from, _churn_pred_sql, _day_add, _year_bounds,
)
from .periods import period_label, period_meta
from .prod_db import (
    NOT_PLAQUE, SO_LINE_FILTER, TIER_RANK, _conn, _geo_filter, available,
)
from .report import CITIES, parse_geo_key
from .special import visible_special_pins
from . import sales_eval_store as eval_store

# 系统类打卡异常（不算真异常）
_SYS_ANOMALY = (
    "系统客户地址信息维护错误",
    "客户多地址办公",
)


def _parse_day(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    t = str(s).strip()[:10]
    try:
        return date.fromisoformat(t)
    except ValueError:
        return None


def _split_tokens(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    # 多选可能是逗号/顿号/斜杠分隔
    text = str(raw).replace("、", ",").replace("/", ",").replace("，", ",")
    return [p.strip() for p in text.split(",") if p.strip()]


def _map_tokens(tokens: List[str], pmap: Dict[str, Dict[str, Any]]
                ) -> List[Dict[str, Any]]:
    out = []
    for tok in tokens:
        hit = pmap.get(tok)
        if hit:
            out.append({"token": tok, **hit})
    return out


def _primary_necessary(mapped: List[Dict[str, Any]]) -> Optional[bool]:
    if not mapped:
        return None
    return any(m["necessary"] for m in mapped)


# 虚拟组织：归属=全部 / 归属=代理 时的「汇总」项
ORG_ALL = "all"
ORG_ALL_DEALERS = "all_dealers"


def build_sales_eval(
    geo_key: str,
    period_key: str,
    mode: str = "person",
    subject: Optional[str] = None,
    side: str = "all",
    org: Optional[str] = None,
    person: Optional[str] = None,
) -> Dict[str, Any]:
    """
    三层筛选：
      1) geo_key 地市 + side（全部/大华/代理）
      2) org = all | all_dealers | dahua | 代理商公司名
      3) person = None → 整组织；否则个人成效
    """
    if not available():
        raise ValueError("生产库不可用")
    if side not in ("all", "dahua", "dealer"):
        raise ValueError("side 必须是 all / dahua / dealer")

    # ── 旧参数兼容 ────────────────────────────────────────────────────────
    if person is None and subject:
        sub = str(subject)
        if sub.startswith("dahua:"):
            org = org or "dahua"
            person = sub[6:]
        elif mode == "dealer":
            org = org or sub
            person = None
        else:
            person = sub
    if person in ("", "all"):
        person = None

    province, city, district = parse_geo_key(geo_key)
    meta = period_meta(period_key)
    start, end = meta["start"], meta["end"]
    pmap = load_visit_purpose_map()
    label_meta = {x["id"]: x for x in PURPOSE_LABELS}

    city_opts = [{"key": f"{province}//", "name": "全部"}]
    for c in CITIES:
        city_opts.append({"key": f"{province}/{c}/", "name": c})

    with _conn() as conn:
        visits = _load_visits(conn, city, district, start, end)
        signed = _load_signed(conn, city, district)
        tiers = _load_tiers(conn, city, district)
        so_by_code = _load_so(conn, city, district, start, end)
        dist_days = _load_dist_days(conn, city, district, start, end)

        dahua_n = sum(1 for v in visits if v["is_dahua"])
        dealer_n = len(visits) - dahua_n

        orgs = _build_orgs(visits, side)
        org_keys = {o["key"] for o in orgs}
        if org and org not in org_keys:
            org = None
        # 仅有人名、未指定组织：落到具体公司/大华（非汇总项）
        if person and not org:
            for o in orgs:
                if o["key"] in (ORG_ALL, ORG_ALL_DEALERS):
                    continue
                if any(
                    v["person"] == person and _org_of(v) == o["key"]
                    for v in visits
                ):
                    org = o["key"]
                    break
        if not org and orgs:
            org = orgs[0]["key"]  # 汇总项已排在第一

        org_visits = _filter_org_visits(visits, org)
        ranking = _build_ranking(
            org_visits, "person", pmap, signed, so_by_code, start, end)
        people = [{
            "key": r["key"],
            "name": r["name"],
            "visits": r["visits"],
            "side": r["side"],
            "kind": "person",
            "company": r.get("company") or "",
            "people": 1,
            "customers": r.get("customers"),
            "necessary_rate": r.get("necessary_rate"),
            "unlabeled_rate": r.get("unlabeled_rate"),
            "sign_linked": r.get("sign_linked"),
            "so_providers": r.get("so_providers"),
            "so_amount": r.get("so_amount"),
        } for r in ranking]
        people_keys = {p["key"] for p in people}
        if person and person not in people_keys:
            person = None

        prov_visits = _load_visits(conn, None, None, start, end)
        if side == "dahua":
            prov_visits = [v for v in prov_visits if v["is_dahua"]]
        elif side == "dealer":
            prov_visits = [v for v in prov_visits if not v["is_dahua"]]
        prov_signed = _load_signed(conn, None, None)
        connect_baseline = _connect_roi_parts(
            prov_visits, prov_signed, start, end)["roi"]

        special_pins = visible_special_pins(geo_key, period_key)

        detail = None
        if org and org_visits:
            if person:
                subset = [v for v in org_visits if v["person"] == person]
                eval_mode = "person"
                subject_key = person
            else:
                subset = org_visits
                eval_mode = "org"
                subject_key = org
            sop_vs_start = (date.fromisoformat(start) - timedelta(days=7)).isoformat()
            sop_vs_end = (date.fromisoformat(end) + timedelta(days=37)).isoformat()
            q0, q1 = _quarter_bounds(date.fromisoformat(start))
            sop_all = _load_visits(
                conn, city, district,
                min(sop_vs_start, q0.isoformat()),
                max(sop_vs_end, q1.isoformat()),
            )
            sop_pool = _filter_org_visits(sop_all, org)
            sop_subset = (
                [v for v in sop_pool if v["person"] == person]
                if person else sop_pool
            )
            meeting_codes = _load_meeting_codes(
                conn, q0.isoformat(), q1.isoformat())
            specials = _special_actuals(
                conn, special_pins, org, person, city, district, start, end)
            presets = eval_store.load_presets(
                geo_key, period_key, side, org, person)
            detail = _build_detail(
                subset, eval_mode, subject_key, pmap, label_meta,
                signed, tiers, so_by_code, dist_days, start, end, meta,
                specials=specials,
                connect_baseline=connect_baseline,
                sop_visits=sop_subset,
                meeting_codes=meeting_codes,
                quarter=(q0.isoformat(), q1.isoformat()),
                presets=presets,
            )

    org_label = _org_label(org, orgs)
    saved_row = eval_store.load_slice(geo_key, period_key, side, org, person)
    if detail:
        try:
            saved_row = eval_store.upsert_slice(
                geo_key, period_key, side, org, person,
                eval_store.compact_slice(detail, org, person, org_label),
                note=None,
            )
        except Exception:
            pass
    saved = {
        "presets": {
            k: v["value"] for k, v in
            (eval_store.load_presets(geo_key, period_key, side, org, person)
             if detail else {}).items()
        },
        "note": (saved_row or {}).get("note") or "",
        "updated_at": (saved_row or {}).get("updated_at"),
    }

    notes = [
        "筛选：①地市+归属 → ②组织（含「全部/全部代理商」汇总）→ ③默认整组织，可选个人。",
        "必要性占比分母不含未标注拜访；当前拜访目的约七成空，请看未标注率。",
        "建联拓客：打卡「渠道客户类型=意向服务商」计入跑动；"
        "收益仅计这些意向打卡所触达、且本周期新签的客户；"
        "ROI=意向跑动/意向归因签约，括号内为全省同侧基准。",
        "铺货：跑动当日该客户有 distribution_info 提交铺货则计入；ROI 暂空。",
        "其余标签仍按拜访目的映射（行销/激活/复购）；合作政策讲解库无单独值。",
        "分档规定动作十条均可取系统值；手调阈值按「个人 > 组织 > 群体」继承，写入评估库。",
        "「无样本」=本周期没有可检客户，不计入合规率。",
        "云联活跃未接入，体验类 ROI 活跃列显示「—」。",
    ]
    if dahua_n == 0 and side != "dealer":
        notes.append("本库本周期无大华跑动（visit_record_v 空公司记录为 0）。")

    return {
        "geo_key": geo_key,
        "province": province,
        "city": city,
        "district": district,
        "cities": city_opts,
        "period_key": period_key,
        "period_label": period_label(period_key),
        "period_type": meta["type"],
        "start": start,
        "end": end,
        "side": side,
        "org": org,
        "org_label": org_label,
        "orgs": orgs,
        "person": person,
        "people": people,
        "mode": "person" if person else "org",
        "subject": person,
        "subjects": people,
        "ranking": ranking,
        "detail": detail,
        "special_pins": special_pins,
        "side_split": {"dahua": dahua_n, "dealer": dealer_n},
        "purpose_map": [
            {"token": t, **m} for t, m in sorted(pmap.items())
        ],
        "notes": notes,
        "saved": saved,
        "generated": date.today().isoformat(),
    }


def patch_sales_eval(
    geo_key: str,
    period_key: str,
    side: str = "all",
    org: Optional[str] = None,
    person: Optional[str] = None,
    item_id: Optional[str] = None,
    preset_value: Optional[float] = None,
    note: Optional[str] = None,
    rebuild: bool = True,
) -> Dict[str, Any]:
    """写入本对象的规定动作阈值和/或分析小结。阈值改完默认重算评估。"""
    if item_id:
        eval_store.save_preset(
            geo_key, period_key, side, org, person, item_id, preset_value)
    if note is not None:
        eval_store.save_note(geo_key, period_key, side, org, person, note)
    if rebuild:
        return build_sales_eval(
            geo_key, period_key, side=side, org=org, person=person)
    row = eval_store.load_slice(geo_key, period_key, side, org, person)
    return {
        "ok": True,
        "note": (row or {}).get("note") or "",
        "updated_at": (row or {}).get("updated_at"),
    }


def _org_of(v: Dict[str, Any]) -> str:
    """实体组织 key：大华=dahua；代理=公司名。"""
    return "dahua" if v["is_dahua"] else (v["company"] or "(未填公司)")


def _org_label(org: Optional[str], orgs: List[Dict[str, Any]]) -> str:
    if not org:
        return ""
    for o in orgs:
        if o["key"] == org:
            return o["name"]
    if org == ORG_ALL:
        return "全部"
    if org == ORG_ALL_DEALERS:
        return "全部代理商"
    if org == "dahua":
        return "大华"
    return org


def _filter_org_visits(visits: List[Dict[str, Any]], org: Optional[str]
                       ) -> List[Dict[str, Any]]:
    if not org:
        return []
    if org == ORG_ALL:
        return list(visits)
    if org == ORG_ALL_DEALERS:
        return [v for v in visits if not v["is_dahua"]]
    return [v for v in visits if _org_of(v) == org]


def _build_orgs(visits: List[Dict[str, Any]], side: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in visits:
        if side == "dahua" and not v["is_dahua"]:
            continue
        if side == "dealer" and v["is_dahua"]:
            continue
        buckets[_org_of(v)].append(v)
    out = []
    for key, vs in buckets.items():
        out.append({
            "key": key,
            "name": "大华" if key == "dahua" else key,
            "side": "dahua" if key == "dahua" else "dealer",
            "visits": len(vs),
            "people": len({v["person"] for v in vs}),
            "rollup": False,
        })
    out.sort(key=lambda o: (0 if o["key"] == "dahua" else 1, -o["visits"], o["name"]))

    # 汇总项置顶
    if side == "all" and (out or visits):
        pool = visits
        out.insert(0, {
            "key": ORG_ALL,
            "name": "全部",
            "side": "all",
            "visits": len(pool),
            "people": len({v["person"] for v in pool}),
            "rollup": True,
        })
    elif side == "dealer":
        pool = [v for v in visits if not v["is_dahua"]]
        if pool or out:
            out.insert(0, {
                "key": ORG_ALL_DEALERS,
                "name": "全部代理商",
                "side": "dealer",
                "visits": len(pool),
                "people": len({v["person"] for v in pool}),
                "rollup": True,
            })
    return out


def _org_key(v: Dict[str, Any]) -> str:
    """旧组织模式 key（排行兼容）：大华按人带前缀，代理按公司。"""
    if v["is_dahua"]:
        return f"dahua:{v['person']}"
    return v["company"] or "(未填公司)"


def _person_who_sql(org: str, person: Optional[str], alias: str = "v"
                    ) -> Tuple[str, List[Any]]:
    """拜访表限制：汇总组织 / 实体组织 / 可选到人。"""
    p = f"{alias}." if alias else ""
    if org == ORG_ALL:
        if person:
            return f" AND {p}打卡人姓名 = ?", [person]
        return "", []
    if org == ORG_ALL_DEALERS:
        base = f" AND COALESCE({p}打卡人所属公司,'') <> ''"
        if person:
            return base + f" AND {p}打卡人姓名 = ?", [person]
        return base, []
    if org == "dahua":
        base = f" AND COALESCE({p}打卡人所属公司,'') = ''"
        if person:
            return base + f" AND {p}打卡人姓名 = ?", [person]
        return base, []
    if person:
        return (f" AND {p}打卡人所属公司 = ?"
                f" AND {p}打卡人姓名 = ?", [org, person])
    return f" AND {p}打卡人所属公司 = ?", [org]


def _special_actuals(
    conn, pins: List[Dict[str, Any]], org: str, person: Optional[str],
    city, district, start: str, end: str,
) -> List[Dict[str, Any]]:
    """
    对本评估对象计算专项绝对值（不要求有个人目标）。
    person=None 时按整组织归因。
    """
    if not pins:
        return []
    who_sql, who_params = _person_who_sql(org, person, "v")
    out = []
    for pin in pins:
        fid = pin["factor_id"]
        actual, note, extra = _factor_actual_for_who(
            conn, fid, city, district, start, end, who_sql, who_params)
        out.append({
            "factor_id": fid,
            "name": pin["name"],
            "unit": pin["unit"],
            "source_label": pin["source_label"],
            "actual": actual,
            "note": note,
            **extra,
        })
    return out


def _factor_actual_for_who(
    conn, factor_id: str, city, district, start, end,
    who_sql: str, who_params: List[Any],
) -> Tuple[Optional[float], str, Dict[str, Any]]:
    """返回 (actual, note, extra)。"""
    vgf, vgp = _geo_filter(city, district, "v.拜访客户城市", "v.拜访客户区县")

    src = visit_source(conn)

    if factor_id in ("a2t_visit", "a2t_visit_dahua", "a2t_visit_dealer"):
        side = "1=1"
        if factor_id == "a2t_visit_dahua":
            side = IS_DAHUA
        elif factor_id == "a2t_visit_dealer":
            side = IS_DEALER
        row = conn.execute(
            f"""SELECT COUNT(*) n, COUNT(DISTINCT 客户编码) customers
                FROM {src} v
                WHERE {side}{vgf}{who_sql}
                  AND {VISIT_TIME} BETWEEN ? AND ?""",
            vgp + who_params + [start, end],
        ).fetchone()
        n = int(row["n"] or 0)
        return n, "", {"customers": int(row["customers"] or 0)}

    if factor_id == "a2t_churn_visit":
        _, y0, _, prev0 = _year_bounds(end)
        auth_sql, gp = _auth_cert_from(city, district)
        pred = _churn_pred_sql("v.vday", y0, prev0)
        row = conn.execute(
            f"""SELECT COUNT(*) n, COUNT(DISTINCT v.code) customers
                FROM (
                  SELECT 客户编码 AS code, {VISIT_TIME} AS vday
                  FROM {src} v
                  WHERE 1=1{vgf}{who_sql} AND {VISIT_TIME} BETWEEN ? AND ?
                ) v
                JOIN ({auth_sql}) a ON a.code = v.code
                WHERE {pred}""",
            vgp + who_params + [start, end] + gp,
        ).fetchone()
        n = int(row["n"] or 0)
        return n, "本对象拜访流失客户的打卡次数", {
            "customers": int(row["customers"] or 0)}

    if factor_id == "a2t_churn_visit_recover":
        _, y0, _, prev0 = _year_bounds(end)
        auth_sql, gp = _auth_cert_from(city, district)
        pred = _churn_pred_sql("v.vday", y0, prev0)
        row = conn.execute(
            f"""SELECT COUNT(DISTINCT v.code) n
                FROM (
                  SELECT 客户编码 AS code, {VISIT_TIME} AS vday
                  FROM {src} v
                  WHERE 1=1{vgf}{who_sql} AND {VISIT_TIME} BETWEEN ? AND ?
                ) v
                JOIN ({auth_sql}) a ON a.code = v.code
                WHERE {pred}
                  AND EXISTS (
                    SELECT 1 FROM install_redpack r
                    WHERE r.上线客户编码 = v.code AND {SO_LINE_R}
                      AND date(r.上线时间) >= v.vday
                      AND date(r.上线时间) <= ?
                  )""",
            vgp + who_params + [start, end] + gp + [end],
        ).fetchone()
        n = int(row["n"] or 0)
        return n, "本对象跑动当日流失且之后有 SO 的家数", {"customers": n}

    if factor_id == "a2t_churn_recover":
        # 归因：期前流失 + 本期有 SO，且本对象在本期拜访过该客户
        _, y0, _, prev0 = _year_bounds(end)
        as_of = _day_add(start, -1)
        auth_sql, gp = _auth_cert_from(city, district)
        pred = _churn_pred_sql(f"'{as_of}'", y0, prev0)
        row = conn.execute(
            f"""SELECT COUNT(DISTINCT a.code) n
                FROM ({auth_sql}) a
                WHERE {pred}
                  AND EXISTS (
                    SELECT 1 FROM install_redpack r
                    WHERE r.上线客户编码 = a.code AND {SO_LINE_R}
                      AND date(r.上线时间) BETWEEN ? AND ?
                  )
                  AND EXISTS (
                    SELECT 1 FROM {src} v
                    WHERE v.客户编码 = a.code{vgf}{who_sql}
                      AND {VISIT_TIME} BETWEEN ? AND ?
                  )""",
            gp + [start, end] + vgp + who_params + [start, end],
        ).fetchone()
        n = int(row["n"] or 0)
        return n, "期前流失、本期有 SO，且本对象本期拜访过", {"customers": n}

    if factor_id == "a2v1_new_open":
        # 本期新开单客户中，本对象拜访过的家数
        gf, gp = _geo_filter(city, district, "p.客户城市", "p.客户区县")
        row = conn.execute(
            f"""SELECT COUNT(*) n FROM (
                  SELECT r.上线客户编码 AS code
                  FROM install_redpack r
                  JOIN provider_contract p ON p.客户编码 = r.上线客户编码
                  WHERE COALESCE(r.上线客户编码,'') <> ''
                    AND COALESCE(p.管理标签,'') <> '授牌服务商'{gf}
                    AND date(r.上线时间) <= ?
                  GROUP BY r.上线客户编码
                  HAVING MIN(date(r.上线时间)) >= ?
                ) x
                WHERE EXISTS (
                  SELECT 1 FROM {src} v
                  WHERE v.客户编码 = x.code{vgf}{who_sql}
                    AND {VISIT_TIME} BETWEEN ? AND ?
                )""",
            gp + [end, start] + vgp + who_params + [start, end],
        ).fetchone()
        n = int(row["n"] or 0)
        return n, "本期新开单且本对象拜访过", {"customers": n}

    # 比率 / 无拜访归因
    return None, "该指标无法按业务员/代理商拆分绝对值", {}


def _load_visits(conn, city, district, start, end) -> List[Dict[str, Any]]:
    src = visit_source(conn)
    gf, gp = _geo_filter(city, district, "拜访客户城市", "拜访客户区县")
    # 拜访类型列：旧合成库可能没有，探测后回落空串
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({src})")}
    type_sel = ("COALESCE(拜访类型,'') AS visit_type"
                if "拜访类型" in cols else "'' AS visit_type")
    rows = conn.execute(
        f"""SELECT 客户编码 AS code,
                   打卡人姓名 AS person,
                   COALESCE(打卡人所属公司,'') AS company,
                   拜访目的 AS purpose,
                   COALESCE(渠道客户类型,'') AS channel_type,
                   {type_sel},
                   {VISIT_TIME} AS day,
                   COALESCE(距离偏离_米, 0) AS dist_m,
                   COALESCE(打卡异常类型,'') AS anomaly
            FROM {src}
            WHERE 1=1{gf} AND {VISIT_TIME} BETWEEN ? AND ?""",
        gp + [start, end],
    ).fetchall()
    out = []
    for r in rows:
        company = r["company"] or ""
        channel = r["channel_type"] or ""
        vtype = (r["visit_type"] or "").strip()
        out.append({
            "code": r["code"] or "",
            "person": r["person"] or "(无名)",
            "company": company,
            "is_dahua": company == "",
            "purpose": r["purpose"],
            "channel_type": channel,
            "is_intent": channel == "意向服务商",
            "visit_type": vtype,
            "is_screen": vtype in ("电话沟通", "微信沟通"),
            "is_face": vtype in ("现场沟通", "现场陪同") or vtype == "",
            # 无类型时按现场计（历史行多为现场）；微信/电话必须显式类型
            "is_wechat": vtype == "微信沟通",
            "is_phone": vtype == "电话沟通",
            "day": r["day"],
            "dist_m": float(r["dist_m"] or 0),
            "anomaly": r["anomaly"] or "",
        })
    return out


def _load_signed(conn, city, district) -> Dict[str, str]:
    gf, gp = _geo_filter(city, district, "客户城市", "客户区县")
    rows = conn.execute(
        f"""SELECT 客户编码 AS code, date(签约日期) AS signed
            FROM provider_contract
            WHERE {NOT_PLAQUE}{gf} AND 签约日期 IS NOT NULL""",
        gp,
    ).fetchall()
    return {r["code"]: r["signed"] for r in rows if r["code"] and r["signed"]}


def _load_tiers(conn, city, district) -> Dict[str, Optional[int]]:
    gf, gp = _geo_filter(city, district, "客户城市", "客户区县")
    rows = conn.execute(
        f"""SELECT 客户编码 AS code, {TIER_RANK} AS rank
            FROM provider_contract WHERE {NOT_PLAQUE}{gf}""",
        gp,
    ).fetchall()
    return {r["code"]: r["rank"] for r in rows if r["code"]}


def _load_so(conn, city, district, start: str, end: str
             ) -> Dict[str, List[Dict[str, Any]]]:
    """code -> [{day, amount}, ...] in [start, end+37d]（SOP 回访窗用）。"""
    end_ext = (date.fromisoformat(end) + timedelta(days=37)).isoformat()
    gf, gp = _geo_filter(city, district, "p.客户城市", "p.客户区县")
    so_f = (SO_LINE_FILTER
            .replace("国内产品线二级", "r.国内产品线二级")
            .replace("产品名称", "r.产品名称"))
    rows = conn.execute(
        f"""SELECT r.上线客户编码 AS code,
                   date(r.上线时间) AS day,
                   COALESCE(r.产品现有分销价, 0) AS amt
            FROM install_redpack r
            JOIN provider_contract p ON p.客户编码 = r.上线客户编码
            WHERE COALESCE(r.上线客户编码,'') <> ''
              AND {NOT_PLAQUE.replace('管理标签', 'p.管理标签')}
              AND date(r.上线时间) BETWEEN ? AND ?
              AND {so_f}{gf}""",
        [start, end_ext] + gp,
    ).fetchall()
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[r["code"]].append({"day": r["day"], "amount": float(r["amt"] or 0)})
    return out


def _load_meeting_codes(conn, start: str, end: str) -> Set[str]:
    """推广会参会客户编码（活动开始日落在窗内）。"""
    try:
        rows = conn.execute(
            """SELECT DISTINCT 参会客户编码 AS code
               FROM promotion_meeting
               WHERE COALESCE(参会客户编码,'') <> ''
                 AND date(活动开始时间) BETWEEN ? AND ?""",
            [start, end],
        ).fetchall()
        return {r["code"] for r in rows if r["code"]}
    except Exception:
        return set()


def _quarter_bounds(day: date) -> Tuple[date, date]:
    q = (day.month - 1) // 3
    m0 = q * 3 + 1
    m1 = m0 + 2
    start = date(day.year, m0, 1)
    last = calendar.monthrange(day.year, m1)[1]
    end = date(day.year, m1, last)
    return start, end


def _load_dist_days(conn, city, district, start: str, end: str
                    ) -> Dict[str, Set[str]]:
    """下级客户编码 → 本周期有铺货的日期集合（用于「跑动当日铺货」）。"""
    try:
        gf, gp = _geo_filter(city, district, "客户所在城市_下级", "客户所在区县_下级")
        rows = conn.execute(
            f"""SELECT 客户编码_下级 AS code, date(提交铺货时间) AS day
                FROM distribution_info
                WHERE COALESCE(客户编码_下级,'') <> ''
                  AND date(提交铺货时间) BETWEEN ? AND ?{gf}""",
            [start, end] + gp,
        ).fetchall()
        out: Dict[str, Set[str]] = defaultdict(set)
        for r in rows:
            if r["code"] and r["day"]:
                out[r["code"]].add(r["day"])
        return out
    except Exception:
        return {}


def _build_ranking(
    visits: List[Dict[str, Any]], mode: str, pmap,
    signed: Dict[str, str], so_by_code, start: str, end: str,
) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in visits:
        key = v["person"] if mode == "person" else _org_key(v)
        buckets[key].append(v)

    rows = []
    for key, vs in buckets.items():
        kpis = _kpis(vs, pmap)
        linked = _linked_outcomes(vs, signed, so_by_code, start, end)
        is_dahua = vs[0]["is_dahua"]
        if mode == "person":
            kind = "person"
            name = key
            company = "大华" if is_dahua else (vs[0]["company"] or "")
            people = 1
        elif is_dahua:
            # 组织模式：大华无「所属公司」，按业务员个人列出
            kind = "person"
            name = vs[0]["person"]
            company = "大华"
            people = 1
        else:
            kind = "company"
            name = key
            company = key
            people = len({v["person"] for v in vs})
        rows.append({
            "key": key,
            "name": name,
            "company": company,
            "side": "dahua" if is_dahua else "dealer",
            "kind": kind,
            "people": people,
            "visits": kpis["visits"],
            "customers": kpis["customers"],
            "labeled": kpis["labeled"],
            "unlabeled": kpis["unlabeled"],
            "unlabeled_rate": kpis["unlabeled_rate"],
            "necessary_rate": kpis["necessary_rate"],
            "true_anomaly_rate": kpis["true_anomaly_rate"],
            "sign_linked": linked["sign_count"],
            "so_providers": linked["so_providers"],
            "so_amount": linked["so_amount"],
        })
    rows.sort(key=lambda r: (-r["visits"], r["key"]))
    return rows


def _kpis(vs: List[Dict[str, Any]], pmap) -> Dict[str, Any]:
    visits = len(vs)
    customers = len({v["code"] for v in vs if v["code"]})
    labeled = unlabeled = necessary = 0
    anomaly = 0
    for v in vs:
        mapped = _map_tokens(_split_tokens(v["purpose"]), pmap)
        if not mapped:
            unlabeled += 1
        else:
            labeled += 1
            if _primary_necessary(mapped):
                necessary += 1
        if v["dist_m"] > 1000 and not any(s in v["anomaly"] for s in _SYS_ANOMALY):
            anomaly += 1
    return {
        "visits": visits,
        "customers": customers,
        "labeled": labeled,
        "unlabeled": unlabeled,
        "unlabeled_rate": round(unlabeled / visits * 100, 1) if visits else None,
        "necessary_rate": round(necessary / labeled * 100, 1) if labeled else None,
        "necessary_n": necessary,
        "true_anomaly_rate": round(anomaly / visits * 100, 1) if visits else None,
        "true_anomaly_n": anomaly,
    }


def _linked_outcomes(vs, signed, so_by_code, start, end) -> Dict[str, Any]:
    """拜访客户在当日新签 / 当日或 7 日内 SO。"""
    start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)
    sign_codes: Set[str] = set()
    so_codes: Set[str] = set()
    so_amt = 0.0
    for v in vs:
        code, day = v["code"], _parse_day(v["day"])
        if not code or not day:
            continue
        s = signed.get(code)
        if s:
            sd = _parse_day(s)
            if sd and sd == day and start_d <= sd <= end_d:
                sign_codes.add(code)
        for so in so_by_code.get(code, []):
            sod = _parse_day(so["day"])
            if not sod:
                continue
            if day <= sod <= day + timedelta(days=7):
                so_codes.add(code)
                so_amt += so["amount"]
    return {
        "sign_count": len(sign_codes),
        "so_providers": len(so_codes),
        "so_amount": round(so_amt, 0),
    }


def _build_detail(
    vs, mode, subject, pmap, label_meta,
    signed, tiers, so_by_code, dist_days, start, end, meta,
    specials: Optional[List[Dict[str, Any]]] = None,
    connect_baseline: Optional[float] = None,
    sop_visits: Optional[List[Dict[str, Any]]] = None,
    meeting_codes: Optional[Set[str]] = None,
    quarter: Optional[Tuple[str, str]] = None,
    presets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    kpis = _kpis(vs, pmap)
    linked = _linked_outcomes(vs, signed, so_by_code, start, end)
    matrix = _matrix(vs, pmap, label_meta, signed, so_by_code, dist_days,
                     start, end, connect_baseline=connect_baseline)
    sop = _sop_checks(
        sop_visits if sop_visits is not None else vs,
        signed, tiers, so_by_code, start, end, meta,
        meeting_codes=meeting_codes or set(),
        quarter=quarter,
        presets=presets or {},
    )
    is_dahua = bool(vs) and vs[0]["is_dahua"]
    people = sorted({v["person"] for v in vs}) if mode == "org" else []
    if mode == "org":
        if subject == ORG_ALL:
            name = "全部"
            company = ""
            is_dahua = False
        elif subject == ORG_ALL_DEALERS:
            name = "全部代理商"
            company = ""
            is_dahua = False
        elif subject == "dahua" or (vs and vs[0]["is_dahua"]):
            name = "大华"
            company = "大华"
            is_dahua = True
        else:
            name = vs[0]["company"] if vs else subject
            company = name
            is_dahua = False
    elif mode == "person" and vs:
        name = subject
        company = "大华" if is_dahua else (vs[0]["company"] or "")
    else:
        name = subject
        company = ""
    return {
        "key": subject,
        "name": name,
        "mode": mode,
        "side": (
            "all" if subject == ORG_ALL
            else ("dahua" if is_dahua else "dealer")
        ),
        "company": company,
        "people": people,
        "kpis": {
            **kpis,
            **linked,
            "sop_rate": sop["rate"],
            "sop_ok": sop["ok"],
            "sop_auto": sop["auto"],
            "sop_pending": sop["pending"],
        },
        "matrix": matrix,
        "sop": sop,
        "specials": specials or [],
        "connect_baseline": connect_baseline,
    }


def _fmt_num(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.1f}"


def _fmt_roi_pair(roi: Optional[float], baseline: Optional[float]) -> Optional[str]:
    """本对象 ROI /（全省基准），如 5/(3.4)。两侧都缺则 None。"""
    if roi is None and baseline is None:
        return None
    return f"{_fmt_num(roi)}/({_fmt_num(baseline)})"


def _connect_roi_parts(vs, signed, start, end) -> Dict[str, Any]:
    """
    建联拓客（跑动与收益同一分母集合）：
      visits = 意向服务商打卡次数
      signs  = 上述意向打卡所触达客户中，本周期新签且拜访日≤签约日的家数
      roi    = visits / signs（跑多少次意向能签 1 家）
    """
    start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)
    intent_vs = [v for v in vs if v.get("is_intent")]
    visits_n = len(intent_vs)
    sign_codes: Set[str] = set()
    for v in intent_vs:
        code, day = v["code"], _parse_day(v["day"])
        if not code or not day:
            continue
        s = _parse_day(signed.get(code))
        if s and start_d <= s <= end_d and day <= s:
            sign_codes.add(code)
    signs_n = len(sign_codes)
    roi = round(visits_n / signs_n, 1) if signs_n else None
    return {"visits": visits_n, "signs": signs_n, "roi": roi}


def _matrix(vs, pmap, label_meta, signed, so_by_code, dist_days, start, end,
            connect_baseline: Optional[float] = None):
    counts = {lid: 0 for lid in label_meta}
    for v in vs:
        if v.get("is_intent"):
            counts["connect"] = counts.get("connect", 0) + 1
        mapped = _map_tokens(_split_tokens(v["purpose"]), pmap)
        for m in mapped:
            lid = m["label_id"]
            if lid == "connect":
                # 建联只认意向服务商，不再用「新签」目的叠算
                continue
            counts[lid] = counts.get(lid, 0) + 1
        code, day = v["code"], v.get("day")
        if code and day and day in dist_days.get(code, ()):
            counts["distribute"] = counts.get("distribute", 0) + 1

    connect_parts = _connect_roi_parts(vs, signed, start, end)

    rows = []
    for meta in sorted(label_meta.values(), key=lambda x: x["order"]):
        lid = meta["id"]
        n = counts.get(lid, 0)
        benefit = _label_benefit(
            lid, vs, pmap, signed, so_by_code, dist_days, start, end,
            connect_parts=connect_parts,
        )
        roi = None
        roi_text = None
        if lid == "connect":
            roi = connect_parts["roi"]
            roi_text = _fmt_roi_pair(roi, connect_baseline)
        elif lid == "distribute":
            roi = None
            roi_text = None
        elif n and benefit["value"] is not None:
            # 其余行保持旧口径：收益 / 跑动
            roi = round(benefit["value"] / n, 2)
            roi_text = str(roi)
        rows.append({
            "label_id": lid,
            "label_name": meta["name"],
            "utility": meta["utility"],
            "necessary": bool(meta["necessary"]),
            "necessary_flag": "Y" if meta["necessary"] else "N",
            "benefit_desc": meta["benefit"],
            "roi_hint": meta["roi_hint"],
            "alt": meta["alt"],
            "visits": n,
            "benefit_value": benefit["value"],
            "benefit_unit": benefit["unit"],
            "roi": roi,
            "roi_baseline": connect_baseline if lid == "connect" else None,
            "roi_text": roi_text,
            "cloud_active": None,  # 未接入
        })
    return rows


def _label_benefit(lid, vs, pmap, signed, so_by_code, dist_days, start, end,
                   connect_parts: Optional[Dict[str, Any]] = None):
    """按标签类型汇总收益量。"""
    if lid == "connect":
        parts = connect_parts or _connect_roi_parts(vs, signed, start, end)
        return {"value": parts["signs"], "unit": "家签约"}
    if lid == "distribute":
        n = 0
        for v in vs:
            code, day = v["code"], v.get("day")
            if code and day and day in dist_days.get(code, ()):
                n += 1
        # 收益列：当日铺货的跑动次数对应的铺货条数不好一一对应，先用当日命中次数
        return {"value": n, "unit": "次当日铺货"}
    # SO 类：policy / product_talk / product_exp / maintain
    if lid in ("policy", "product_talk", "product_exp", "maintain"):
        amt = 0.0
        providers = set()
        for v in vs:
            mapped = _map_tokens(_split_tokens(v["purpose"]), pmap)
            if lid != "policy" and not any(m["label_id"] == lid for m in mapped):
                continue
            # policy 无库值：不计（visits 多为 0）
            if lid == "policy":
                continue
            code, day = v["code"], _parse_day(v["day"])
            if not code or not day:
                continue
            for so in so_by_code.get(code, []):
                sod = _parse_day(so["day"])
                if sod and day <= sod <= day + timedelta(days=7):
                    providers.add(code)
                    amt += so["amount"]
        return {"value": round(amt, 0), "unit": "元SO", "providers": len(providers)}
    return {"value": None, "unit": ""}


def _preset_num(presets: Optional[Dict[str, Dict[str, Any]]],
                item_id: str, default: float) -> float:
    hit = (presets or {}).get(item_id)
    if not hit:
        return default
    v = hit.get("value") if isinstance(hit, dict) else hit
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _sop_checks(
    vs, signed, tiers, so_by_code, start, end, meta,
    meeting_codes: Optional[Set[str]] = None,
    quarter: Optional[Tuple[str, str]] = None,
    presets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    分档规定动作（对齐渠道生意关注指标图）：
      潜客×2 / V0×2 / V1×2 / V2×2 / V3+×2
    均可取系统值；「3次不成交」为监测家数（越少越好），不进合规分母。
    手调阈值经 presets 覆盖（个人 > 组织 > 群体）。
    """
    presets = presets or {}
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    meeting_codes = meeting_codes or set()
    if quarter:
        q0, q1 = date.fromisoformat(quarter[0]), date.fromisoformat(quarter[1])
    else:
        q0, q1 = _quarter_bounds(start_d)

    p_screen = int(_preset_num(presets, "prospect_screen", 1))
    p_visit7 = int(_preset_num(presets, "prospect_visit7", 7))
    p_dense = int(_preset_num(presets, "v0_dense", 3))
    p_stuck = int(_preset_num(presets, "v0_stuck", 0))
    p_v1_3d = int(_preset_num(presets, "v1_revisit_3d", 3))
    p_v1_30 = int(_preset_num(presets, "v1_revisit_30", 30))
    p_v2_face = int(_preset_num(presets, "v2_face_q", 1))
    p_v2_wx = int(_preset_num(presets, "v2_wechat_m", 1))
    p_v3_meet = int(_preset_num(presets, "v3_meeting_q", 1))
    p_v3_wx = int(_preset_num(presets, "v3_wechat_w", 1))

    # code -> visit events
    by_code: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in vs:
        d = _parse_day(v["day"])
        if v["code"] and d:
            by_code[v["code"]].append({**v, "_d": d})

    def days_of(code, pred=None):
        out = []
        for e in by_code.get(code, []):
            if pred is None or pred(e):
                out.append(e["_d"])
        return sorted(out)

    items: List[Dict[str, Any]] = []

    # ── 潜客 ──────────────────────────────────────────────────────────────
    prospects = sorted(
        c for c in by_code
        if (any(e.get("is_intent") for e in by_code[c])
            or c not in signed)
        and any(start_d <= e["_d"] <= end_d for e in by_code[c])
    )
    if prospects:
        screen_ok = 0
        for c in prospects:
            n_sc = sum(1 for e in by_code[c]
                       if e.get("is_screen") and start_d <= e["_d"] <= end_d)
            if n_sc >= p_screen:
                screen_ok += 1
        items.append(_sop_item(
            "prospect_screen", "潜客", "电话/微信筛选",
            "微信或电话打卡",
            "auto", screen_ok == len(prospects),
            f"{screen_ok}/{len(prospects)} 家有电话/微信打卡",
            preset_value=p_screen, preset_unit="次",
        ))
        follow_ok = follow_n = 0
        for c in prospects:
            screens = [e["_d"] for e in by_code[c] if e.get("is_screen")]
            if not screens:
                continue
            follow_n += 1
            first_s = min(screens)
            if any(e.get("is_face") and first_s < e["_d"] <= first_s + timedelta(days=p_visit7)
                   for e in by_code[c]):
                follow_ok += 1
        if follow_n:
            items.append(_sop_item(
                "prospect_visit7", "潜客", "上门拜访",
                f"电话/微信筛选后 {p_visit7} 日内完成现场打卡",
                "auto", follow_ok == follow_n,
                f"{follow_ok}/{follow_n} 家在筛选后{p_visit7}日内上门",
                preset_value=p_visit7, preset_unit="天",
            ))
        else:
            items.append(_sop_item(
                "prospect_visit7", "潜客", "上门拜访",
                f"电话/微信筛选后 {p_visit7} 日内完成现场打卡",
                "skip", None, "无已筛选潜客",
                preset_value=p_visit7, preset_unit="天",
            ))
    else:
        items.append(_sop_item(
            "prospect_screen", "潜客", "电话/微信筛选",
            "微信或电话打卡", "skip", None, "本周期无潜客触达",
            preset_value=p_screen, preset_unit="次",
        ))
        items.append(_sop_item(
            "prospect_visit7", "潜客", "上门拜访",
            f"电话/微信筛选后 {p_visit7} 日内完成现场打卡",
            "skip", None, "本周期无潜客触达",
            preset_value=p_visit7, preset_unit="天",
        ))

    # ── V0 新签 ───────────────────────────────────────────────────────────
    # 本周期新签且本对象有拜访（缓冲窗内）
    v0_codes = sorted({
        c for c, s in signed.items()
        if start_d <= (_parse_day(s) or date.min) <= end_d and c in by_code
    })

    v0_ok = 0
    for c in v0_codes:
        sd = _parse_day(signed[c])
        if not sd:
            continue
        n = sum(1 for d in days_of(c) if sd <= d <= sd + timedelta(days=30))
        if n >= p_dense:
            v0_ok += 1
    if v0_codes:
        items.append(_sop_item(
            "v0_dense", "V0 新签", f"首月密联 {p_dense} 次",
            f"签约后 30 天内拜访 ≥ {p_dense} 次",
            "auto", v0_ok == len(v0_codes),
            f"{v0_ok}/{len(v0_codes)} 家达标",
            preset_value=p_dense, preset_unit="次",
        ))
    else:
        items.append(_sop_item(
            "v0_dense", "V0 新签", f"首月密联 {p_dense} 次",
            f"签约后 30 天内拜访 ≥ {p_dense} 次",
            "skip", None, "本周期无相关新签",
            preset_value=p_dense, preset_unit="次",
        ))

    # 3次不成交：签约后已≥3访且无筛后SO → 滞留家数（监测，不进分母）
    stuck = 0
    for c in by_code:
        sd = _parse_day(signed.get(c))
        if not sd:
            continue
        n_vis = sum(1 for d in days_of(c) if d >= sd)
        if n_vis < max(p_dense, 3):
            continue
        has_so = any(
            (_parse_day(so["day"]) or date.min) >= sd
            for so in so_by_code.get(c, [])
        )
        if not has_so:
            stuck += 1
    items.append(_sop_item(
        "v0_stuck", "V0 新签", "拜访3次后不成交",
        "跑动≥3次仍无筛后SO的家数（越少越好）",
        "metric", None,
        f"滞留 {stuck} 家" + (f"（阈值 {p_stuck} 家）" if p_stuck else ""),
        preset_value=p_stuck, preset_unit="家",
        value=stuck,
        in_rate=False,
    ))

    # ── V1 开单 ───────────────────────────────────────────────────────────
    first_so: Dict[str, date] = {}
    for code, sos in so_by_code.items():
        for so in sos:
            d = _parse_day(so["day"])
            if d and start_d <= d <= end_d:
                if code not in first_so or d < first_so[code]:
                    first_so[code] = d
    # 只检本对象拜访过的
    first_so = {c: d for c, d in first_so.items() if c in by_code}

    v1a_ok = v1a_n = 0
    v1b_ok = v1b_n = 0
    for code, sod in first_so.items():
        v1a_n += 1
        visits = days_of(code)
        first_rev = None
        for d in visits:
            if sod <= d <= sod + timedelta(days=p_v1_3d):
                first_rev = d
                break
        if first_rev:
            v1a_ok += 1
            v1b_n += 1
            if any(first_rev < d <= first_rev + timedelta(days=p_v1_30) for d in visits):
                v1b_ok += 1
        # 无3日内回访则不计第二项分母
    if v1a_n:
        items.append(_sop_item(
            "v1_revisit_3d", "V1 开单", f"SO 上线后 {p_v1_3d} 日内回访",
            f"SO 上线后 {p_v1_3d} 日内回访",
            "auto", v1a_ok == v1a_n,
            f"{v1a_ok}/{v1a_n} 家达标",
            preset_value=p_v1_3d, preset_unit="天",
        ))
    else:
        items.append(_sop_item(
            "v1_revisit_3d", "V1 开单", f"SO 上线后 {p_v1_3d} 日内回访",
            f"SO 上线后 {p_v1_3d} 日内回访",
            "skip", None, "本周期无相关 SO 上线",
            preset_value=p_v1_3d, preset_unit="天",
        ))
    if v1b_n:
        items.append(_sop_item(
            "v1_revisit_30", "V1 开单", "第一次回访后再访",
            f"第一次回访后 {p_v1_30} 日内再次拜访",
            "auto", v1b_ok == v1b_n,
            f"{v1b_ok}/{v1b_n} 家达标",
            preset_value=p_v1_30, preset_unit="天",
        ))
    else:
        items.append(_sop_item(
            "v1_revisit_30", "V1 开单", "第一次回访后再访",
            f"第一次回访后 {p_v1_30} 日内再次拜访",
            "skip", None,
            "无已完成3日内回访的样本" if v1a_n else "本周期无相关 SO 上线",
            preset_value=p_v1_30, preset_unit="天",
        ))

    # ── V2 激活 ───────────────────────────────────────────────────────────
    v2_codes = sorted(
        c for c, r in tiers.items()
        if r == 2 and c in by_code
        and any(q0 <= e["_d"] <= q1 for e in by_code[c])
    )
    if v2_codes:
        face_ok = sum(
            1 for c in v2_codes
            if sum(1 for e in by_code[c]
                   if e.get("is_face") and q0 <= e["_d"] <= q1) >= p_v2_face
        )
        items.append(_sop_item(
            "v2_face_q", "V2 激活", f"每季度 {p_v2_face} 次当面拜访",
            f"本季（{q0}~{q1}）现场拜访 ≥ {p_v2_face} 次",
            "auto", face_ok == len(v2_codes),
            f"{face_ok}/{len(v2_codes)} 家本季有当面拜访",
            preset_value=p_v2_face, preset_unit="次/季",
        ))
        # 月微信：评估期为月则看该月；为周则看该周所在月
        m0 = date(start_d.year, start_d.month, 1)
        m1 = date(start_d.year, start_d.month,
                  calendar.monthrange(start_d.year, start_d.month)[1])
        wx_ok = sum(
            1 for c in v2_codes
            if sum(1 for e in by_code[c]
                   if e.get("is_wechat") and m0 <= e["_d"] <= m1) >= p_v2_wx
        )
        items.append(_sop_item(
            "v2_wechat_m", "V2 激活", f"每月至少 {p_v2_wx} 次微信",
            "当月有微信沟通打卡",
            "auto", wx_ok == len(v2_codes),
            f"{wx_ok}/{len(v2_codes)} 家当月有微信",
            preset_value=p_v2_wx, preset_unit="次/月",
        ))
    else:
        items.append(_sop_item(
            "v2_face_q", "V2 激活", f"每季度 {p_v2_face} 次当面拜访",
            "本季有现场拜访", "skip", None, "无相关 V2 客户",
            preset_value=p_v2_face, preset_unit="次/季",
        ))
        items.append(_sop_item(
            "v2_wechat_m", "V2 激活", f"每月至少 {p_v2_wx} 次微信",
            "当月有微信沟通打卡", "skip", None, "无相关 V2 客户",
            preset_value=p_v2_wx, preset_unit="次/月",
        ))

    # ── V3+ 稳定 ──────────────────────────────────────────────────────────
    v3_codes = sorted(
        c for c, r in tiers.items()
        if r is not None and r >= 3 and c in by_code
        and any(q0 <= e["_d"] <= q1 for e in by_code[c])
    )
    if v3_codes:
        meet_ok = sum(1 for c in v3_codes if c in meeting_codes) if p_v3_meet <= 1 else sum(
            1 for c in v3_codes if c in meeting_codes)
        items.append(_sop_item(
            "v3_meeting_q", "V3+ 稳定", f"每季度 {p_v3_meet} 次推广会",
            f"本季（{q0}~{q1}）参加推广会",
            "auto", meet_ok == len(v3_codes),
            f"{meet_ok}/{len(v3_codes)} 家本季参会",
            preset_value=p_v3_meet, preset_unit="次/季",
        ))
        # 周微信：评估期内每个 ISO 周都要有微信
        weeks = []
        d = start_d
        while d <= end_d:
            weeks.append(d.isocalendar()[:2])
            d += timedelta(days=1)
        weeks = sorted(set(weeks))
        wx_ok = 0
        for c in v3_codes:
            ok = True
            for y, w in weeks:
                w0 = date.fromisocalendar(y, w, 1)
                w1 = date.fromisocalendar(y, w, 7)
                n_wx = sum(1 for e in by_code[c]
                           if e.get("is_wechat") and w0 <= e["_d"] <= w1)
                if n_wx < p_v3_wx:
                    ok = False
                    break
            if ok:
                wx_ok += 1
        items.append(_sop_item(
            "v3_wechat_w", "V3+ 稳定", f"每周至少 {p_v3_wx} 次微信打卡",
            "评估期内每周有微信沟通",
            "auto", wx_ok == len(v3_codes),
            f"{wx_ok}/{len(v3_codes)} 家每周有微信",
            preset_value=p_v3_wx, preset_unit="次/周",
        ))
    else:
        items.append(_sop_item(
            "v3_meeting_q", "V3+ 稳定", f"每季度 {p_v3_meet} 次推广会",
            "本季参加推广会", "skip", None, "无相关 V3+ 客户",
            preset_value=p_v3_meet, preset_unit="次/季",
        ))
        items.append(_sop_item(
            "v3_wechat_w", "V3+ 稳定", f"每周至少 {p_v3_wx} 次微信打卡",
            "评估期内每周有微信沟通", "skip", None, "无相关 V3+ 客户",
            preset_value=p_v3_wx, preset_unit="次/周",
        ))

    for it in items:
        hit = presets.get(it["id"])
        it["preset_source"] = hit["source"] if hit else "default"

    auto = [i for i in items if i["status"] == "auto" and i.get("in_rate", True)]
    ok = sum(1 for i in auto if i["pass"] is True)
    pending = sum(1 for i in items if i["status"] == "pending")
    rate = round(ok / len(auto) * 100, 1) if auto else None
    return {
        "items": items,
        "ok": ok,
        "auto": len(auto),
        "pending": pending,
        "rate": rate,
        "budget_note": "「无样本」=本周期没有可检客户，不计入合规率；「拜访3次后不成交」为滞留家数监测（越少越好），不计入合规率。手调阈值写入评估库，按个人 > 组织 > 群体继承。",
    }


def _sop_item(
    id_, tier, name, rule, status, passed, detail,
    preset_value: Any = None,
    preset_unit: str = "",
    value: Any = None,
    in_rate: bool = True,
) -> Dict[str, Any]:
    return {
        "id": id_,
        "tier": tier,
        "name": name,
        "rule": rule,
        "status": status,  # auto | skip | metric | pending
        "pass": passed,
        "detail": detail,
        "preset_value": preset_value,
        "preset_unit": preset_unit,
        "value": value,
        "in_rate": in_rate,
    }
