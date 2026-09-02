"""
Funnel REST API endpoints.

geo_key and period_key are passed as QUERY parameters, not path segments:
geo_key contains '/' separators ('浙江省/杭州/西湖区') and percent-encoded
slashes get decoded before routing, which breaks path-param matching.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from ..db import (
    save_state, load_latest_state, get_state_meta, list_slices,
    list_factor_defs, get_cached_factor_values, upsert_factor_cache,
)
from ..factors import (
    FETCHERS, compute_all, redpack_values as prod_redpack_values,
)
from ..periods import period_meta, period_range
from ..prod_db import (
    funnel_data, geo_tree as prod_geo_tree, cert_values as prod_cert_values,
    targets as prod_targets, target_values as prod_target_values,
)
from ..rollup import rollup as prod_rollup
from ..auth import current_user
from ..users import authorize_geo, scoped_geo_tree
from ..schemas import (
    StatePayload, OverviewItemPatch, SpecialPinPayload, SpecialTargetPayload,
    SalesEvalPatch, BudgetPayload,
)
from ..overview import build_overview, patch_item
from ..week_rollup import build_week_rollup
from ..factor_library import build_factor_library
from ..report import build_report_data
from ..sales_eval import build_sales_eval, patch_sales_eval
from ..budget import build_matrix as budget_matrix, set_budget as budget_set
from ..special import (
    add_pin as special_add_pin,
    build_special,
    remove_pin as special_remove_pin,
    set_target as special_set_target,
)

router = APIRouter(prefix="/api/funnel", tags=["funnel"])

GeoKey = Query(..., description="地区键，如 '浙江省/杭州/西湖区'")
PeriodKey = Query(..., description="固定周期键，如 '2026-W31' 或 '2026-07'")
CertMode = Query("all", pattern="^(all|cert|nocert)$",
                 description="认证维度：all 全部 / cert 认证 / nocert 非认证")


def require_geo(request, geo_key: str) -> None:
    """
    地区级授权。地市账号只能碰本市及其区县，越权一律 403。

    **每个带 geo_key 的接口都必须调它**，读接口也要 —— 读也是越权：省级视图会
    暴露全省和其它 10 个地市的数字。漏一个接口就等于开一个后门，所以这里不做
    "写接口才校验"的优化。
    """
    user = current_user(request)
    if not authorize_geo(user, geo_key):
        raise HTTPException(
            status_code=403,
            detail=f"无权访问该地区。当前账号「{user.label}」的范围是 "
                   f"{user.scope}（含其下属区县），不含省级汇总。")


@router.get("/overview")
def get_overview(request: Request,
                 period_key: str = PeriodKey,
                 level: Optional[str] = Query(
                     None, pattern="^(city|district)$",
                     description="city=下一级地市；district=区县。默认：管理员地市、地市账号区县"),
                 city: Optional[str] = Query(
                     None, description="管理员看区县时可选，只列该市")):
    """
    管辖总览：一次返回下级单位的期末存量转化率、短板、待办/问题。

    不串行打 /data+/state。地市账号只看到本市（scoped_geo_tree），
    传别人的 city= 会 403。
    """
    user = current_user(request)
    if city:
        tree = scoped_geo_tree(user, prod_geo_tree())
        province = (tree or {}).get("province") or "浙江"
        require_geo(request, f"{province}/{city}/")
    if not level:
        level = "district" if (user and not user.is_admin) else "city"
    try:
        return build_overview(user, period_key, level, city)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/overview/item")
def post_overview_item(request: Request, payload: OverviewItemPatch,
                       geo_key: str = GeoKey, period_key: str = PeriodKey):
    """勾选总览右侧一条待办/问题，写回该地区切片，不改目标和漏斗数。"""
    require_geo(request, geo_key)
    return patch_item(geo_key, period_key, payload.kind, payload.index,
                      payload.done, payload.text)


@router.get("/week-rollup")
def get_week_rollup(request: Request,
                    geo_key: str = GeoKey, period_key: str = PeriodKey):
    """
    月度 ← 当月各周切片。周视图返回空 weeks。
    手填关键因素 / 待办 / 问题按周列出，不平均成月度值。
    """
    require_geo(request, geo_key)
    try:
        return build_week_rollup(geo_key, period_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/me")
def get_me(request: Request):
    """
    当前登录账号及其地区范围。前端用它决定标题、锁死地区切换。
    无鉴权（本地开发）时 user 为 null，视为不受限。
    """
    user = current_user(request)
    return {"user": user.as_public() if user else None}


# ── State slices ───────────────────────────────────────────────────────────────

@router.get("/state")
def get_state(request: Request,
              geo_key: str = GeoKey, period_key: str = PeriodKey):
    """Return the stored slice for this geo+period."""
    require_geo(request, geo_key)
    state = load_latest_state(geo_key, period_key)
    if state is None:
        return {"found": False, "state": {}, "meta": None}
    return {"found": True, "state": state,
            "meta": get_state_meta(geo_key, period_key)}


@router.post("/state")
def post_state(request: Request, payload: StatePayload,
               geo_key: str = GeoKey, period_key: str = PeriodKey):
    """
    Save the slice for this geo+period, overwriting any previous one.
    One row per (geo_key, period_key) — always the latest.
    """
    require_geo(request, geo_key)
    if payload.period_type not in ("week", "month"):
        raise HTTPException(status_code=422,
                            detail="period_type must be 'week' or 'month'")
    updated_at = save_state(geo_key, payload.period_type, period_key, payload.state)
    return {"ok": True, "updated_at": updated_at}


@router.get("/slices")
def get_slices(request: Request, geo_key: Optional[str] = None,
               limit: int = Query(200, ge=1, le=1000)):
    """
    List stored slices (metadata only), most recently updated first.

    ⚠️ geo_key 在这个接口是**可选**的，不传就是列全库 —— 对地市账号必须收紧成
    「只列本市」，否则它是一个绕过 require_geo 的旁路：不传 geo_key 就能看到
    其它地市存了哪些周期的切片。
    """
    user = current_user(request)
    if user is not None and not user.is_admin:
        if geo_key:
            require_geo(request, geo_key)
            rows = list_slices(geo_key, limit)
        else:
            # 不传 geo_key：列全库后逐行按同一套授权规则过滤。复用
            # authorize_geo 而不是自己拼前缀 —— 两处判定规则必须是同一份代码，
            # 否则哪天改了 scope 语义就会漏。
            rows = [r for r in list_slices(None, limit)
                    if authorize_geo(user, r["geo_key"])]
        return {"slices": rows, "scope": user.scope}
    return {"slices": list_slices(geo_key, limit)}


# ── Factor library ─────────────────────────────────────────────────────────────

@router.get("/factor-defs")
def get_factor_defs(conv_key: Optional[str] = None):
    """Factor definitions. Empty until the factor library is populated."""
    return {"defs": list_factor_defs(conv_key)}


@router.get("/factor-library")
def get_factor_library(conv_key: Optional[str] = None):
    """
    指标库：系统可取数 defs + 各地切片收获的手填指标。

    登录即可读（知识共享）。不返回待办/问题/思考。写仍走 POST /state，
    地市账号只能改自己管辖的切片。
    conv_key 有则只返回该漏斗跳（按页面跳，首单礼算 a2v1）。
    """
    return build_factor_library(conv_key)


@router.get("/factor-values")
def get_factor_values(request: Request,
                      geo_key: str = GeoKey, period_key: str = PeriodKey,
                      refresh: bool = Query(False, description="跳过缓存强制重算")):
    """
    auto 因子的计算值（本地区+本周期）。

    先查缓存（1 小时内有效），未命中就现算并写缓存。缓存只存标量 value；
    详情（分子/分母/待解锁/已失效）不入缓存，命中缓存时 detail 为 null——
    这些数字随卡券状态变，存下来会给出过期的明细，而 value 本身 1 小时的
    陈旧度是可接受的。要看明细就带 refresh=true。

    值为 null 的因子 = 该地区该周期没有样本（如一张券都没发），不是 0%。
    """
    require_geo(request, geo_key)
    try:
        start, end = period_range(period_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    parts = (geo_key or "").split("/")
    city = parts[1] if len(parts) > 1 and parts[1] else None
    district = parts[2] if len(parts) > 2 and parts[2] else None

    cached = {} if refresh else get_cached_factor_values(geo_key, period_key)
    missing = [f for f in FETCHERS if f not in cached]

    values, details = dict(cached), {}
    if missing:
        fresh = compute_all(city, district, start, end)
        for fid in missing:
            d = fresh.get(fid)
            values[fid] = d.get("value") if isinstance(d, dict) else None
            details[fid] = d
            # 只缓存算得出的值；None（无样本）不写缓存，下次照样现算 —— 期中
            # 才发的券否则会被 1 小时前的「无样本」盖住。
            if values[fid] is not None:
                upsert_factor_cache(geo_key, period_key, fid, values[fid])

    return {"geo_key": geo_key, "period_key": period_key,
            "range": {"start": start, "end": end},
            "values": values, "details": details,
            "cached": sorted(set(values) - set(details)),
            "source": "prod" if missing else "cache"}


@router.get("/targets")
def get_targets(request: Request,
                geo_key: str = GeoKey, period_key: str = PeriodKey):
    """
    下发目标：全年（库内权威值，只读）+ 本期（按月节奏分解出的预设值，可改）。

    本期目标 = 全年目标 × 该月节奏占比（kpi_rhythm）；周再 ÷ 该月周数。
    区县级库里没有下发目标 → 全部 null，页面留空由用户手填。
    生产库不可用时 targets=null，前端不显示预设值（不编数）。
    """
    require_geo(request, geo_key)
    try:
        meta = period_meta(period_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    parts = (geo_key or "").split("/")
    city = parts[1] if len(parts) > 1 and parts[1] else None
    district = parts[2] if len(parts) > 2 and parts[2] else None

    data = prod_targets(city=city, district=district, period_key=period_key,
                        year=meta["year"], month=meta["month"],
                        week_share=meta["week_share"])
    return {"geo_key": geo_key, "period_key": period_key, "period": meta,
            "source": "prod" if data else "unavailable", "targets": data}


@router.get("/target-rollup")
def get_target_rollup(request: Request,
                     geo_key: str = GeoKey, period_key: str = PeriodKey):
    """
    下级目标卷积：省看 11 个地市之和，市看其下区县之和，区县无下级。

    每个下级每档取「用户改过的值 ?? 节奏预设值」；两者皆无算缺口、**不按 0 计入**。
    同时返回与本级下发目标的差额。卷积值只作参照，不写回任何下发值。
    """
    require_geo(request, geo_key)
    try:
        return prod_rollup(geo_key, period_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/target-values")
def get_target_values():
    """
    provider_target / kpi_rhythm 的实际内容。诊断用，核对地市命名与节奏占比。
    """
    vals = prod_target_values()
    if vals is None:
        return {"available": False, "values": None}
    return {"available": True, "values": vals}


@router.get("/redpack-values")
def get_redpack_values():
    """
    转化红包发放明细里 卡券名称 / 卡券状态 的取值分布。诊断用，核对
    「首单礼」这类券种的识别条件（LIKE '%首单%'）与「已使用」的判定选得对不对。
    """
    vals = prod_redpack_values()
    if vals is None:
        return {"available": False, "values": None}
    return {"available": True, "values": vals}


# ── Production funnel data ─────────────────────────────────────────────────────

@router.get("/geo")
def get_geo(request: Request):
    """
    地区维度（11 市 / 97 区县），取自生产库 district_base。
    生产库不可用时返回 available=false，前端回落内置演示地区。

    地市账号只拿到本市那一支 —— 在**服务端**裁剪，不是让前端自己藏：
    前端藏起来的东西改改 JS 就能点出来。同时回带 `user`，前端据此隐藏
    省级入口、把标题改成本市。
    """
    user = current_user(request)
    tree = scoped_geo_tree(user, prod_geo_tree())
    return {"available": tree is not None, "geo": tree,
            "user": user.as_public() if user else None}


@router.get("/cert-values")
def get_cert_values():
    """
    认证相关三列的实际取值分布。诊断用，核对认证判定列选得对不对。
    """
    vals = prod_cert_values()
    if vals is None:
        return {"available": False, "values": None}
    return {"available": True, "values": vals}


@router.get("/data")
def get_funnel_data(request: Request,
                    geo_key: str = GeoKey, period_key: str = PeriodKey,
                    cert: str = CertMode):
    """
    漏斗五阶绝对值，取自生产库。

    geo_key 形如 '浙江/杭州市/西湖区'（省级则后两段为空）；
    period_key 是 '2026-W31' 或 '2026-07'，在这里解析成日期区间：
    存量截至期末，增量落在区间内；
    cert 是认证维度筛选 all/cert/nocert，只作用于签约表那三阶，城市总量不变。
    生产库不可用时返回 data=null，前端继续用内置演示数据，页面不至于白屏。
    """
    require_geo(request, geo_key)
    parts = (geo_key or "").split("/")
    city = parts[1] if len(parts) > 1 and parts[1] else None
    district = parts[2] if len(parts) > 2 and parts[2] else None

    try:
        start, end = period_range(period_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    data = funnel_data(city=city, district=district, start=start, end=end,
                       cert=cert)
    if data is None:
        return {"geo_key": geo_key, "period_key": period_key,
                "range": {"start": start, "end": end}, "cert": cert,
                "source": "unavailable", "data": None}
    return {"geo_key": geo_key, "period_key": period_key,
            "range": {"start": start, "end": end}, "cert": cert,
            "source": "prod", "data": data}


@router.get("/report-data")
def get_report_data(request: Request,
                    geo_key: str = GeoKey,
                    period_key: str = PeriodKey):
    """
    漏斗报告 JSON：期末存量转化率 + 周期增量 + benchmark + 跑动/SO 佐证。
    省级 geo_key 出 11 地市明细；地市级出本市；区县级出本区县。
    """
    require_geo(request, geo_key)
    try:
        return build_report_data(geo_key, period_key)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/sales-eval")
def get_sales_eval(
    request: Request,
    geo_key: str = GeoKey,
    period_key: str = PeriodKey,
    side: str = Query("all", pattern="^(all|dahua|dealer)$",
                      description="第一层：全部 / 只看大华 / 只看代理商"),
    org: Optional[str] = Query(
        None, description="第二层：dahua 或代理商公司名；空则取列表第一"),
    person: Optional[str] = Query(
        None, description="第三层：业务员姓名；空则取组织内第一人"),
    # 旧参数兼容
    mode: str = Query("person", pattern="^(person|dealer)$"),
    subject: Optional[str] = Query(None),
):
    """
    业务员能力评估（三层筛选）：地市+归属 → 大华/代理商公司 → 个人成效。
    """
    require_geo(request, geo_key)
    try:
        return build_sales_eval(
            geo_key, period_key,
            side=side, org=org, person=person,
            mode=mode, subject=subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.put("/sales-eval")
def put_sales_eval(
    request: Request,
    payload: SalesEvalPatch,
    geo_key: str = GeoKey,
    period_key: str = PeriodKey,
):
    """
    写入本评估对象的规定动作阈值和/或分析小结。
    独立表 sales_eval_preset / sales_eval_slice，不挂漏斗切片。
    """
    require_geo(request, geo_key)
    if not payload.item_id and payload.note is None:
        raise HTTPException(status_code=422, detail="需要 item_id 或 note")
    try:
        return patch_sales_eval(
            geo_key, period_key,
            side=payload.side, org=payload.org, person=payload.person,
            item_id=payload.item_id, preset_value=payload.preset_value,
            note=payload.note,
            rebuild=bool(payload.item_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/special-targets")
def get_special_targets(request: Request,
                        geo_key: str = GeoKey, period_key: str = PeriodKey):
    """
    专项目标：本级可见的因子钉（上级设置自动继承）+ 本期目标/实际/完成率/下级卷积。
    """
    require_geo(request, geo_key)
    try:
        return build_special(geo_key, period_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/special-targets")
def post_special_pin(request: Request, payload: SpecialPinPayload,
                     geo_key: str = GeoKey, period_key: str = PeriodKey):
    """本级设置专项目标（钉住一个因子）；省设置后全市/区县自动可见。"""
    require_geo(request, geo_key)
    try:
        return special_add_pin(
            geo_key, period_key, payload.factor_id, payload.target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.put("/special-targets")
def put_special_target(request: Request, payload: SpecialTargetPayload,
                       geo_key: str = GeoKey, period_key: str = PeriodKey):
    """本级填写/清空某专项的本期目标。"""
    require_geo(request, geo_key)
    try:
        return special_set_target(
            geo_key, period_key, payload.factor_id, payload.target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/special-targets")
def delete_special_pin(request: Request,
                       geo_key: str = GeoKey, period_key: str = PeriodKey,
                       factor_id: str = Query(..., description="因子 id")):
    """取消本级设置的专项（不能取消上级设置的）。"""
    require_geo(request, geo_key)
    try:
        return special_remove_pin(geo_key, period_key, factor_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ── 数据汇总 / 预算 ───────────────────────────────────────────────────────────

@router.get("/budget-matrix")
def get_budget_matrix(request: Request, period_key: str = PeriodKey):
    """
    管辖范围内地市 + 区县的本期目标与手填预算矩阵。
    目标只读；预算按格存 funnel_budget。
    """
    user = current_user(request)
    try:
        return budget_matrix(user, period_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.put("/budget")
def put_budget(request: Request, payload: BudgetPayload):
    """手填/清空某一格预算（元）。"""
    require_geo(request, payload.geo_key)
    try:
        return budget_set(payload.geo_key, payload.period_key,
                          payload.level, payload.amount)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
