"""
关键因素（因子）取数引擎。

设计见 docs/factor-library-design.md。与那份设计的一处偏差：**auto 因子的取数
不走库里存的 fetch_query 字符串**，而是每个因子在本文件注册一个 Python 取数函数。
理由：fetch_query 靠 `.format()` 拼 geo/period 是字符串插值，等于把 SQL 注入面
摆在数据库里；而且首单礼这类指标需要 join + 两个计数（分子/分母），单条标量
SQL 表达不了「分子为 0 也要区分是没发券还是发了没兑」。库表 funnel_factor_defs
仍然是因子清单的来源（名称/单位/目标/排序），只是 fetch_query 列留空。

每个因子函数签名统一：
    fn(conn, city, district, start, end) -> dict | None
返回 None = 该地区该周期无样本（分母为 0），前端显示「—」而不是 0%。
返回 dict 至少含 {"value": float|None, "num": int, "den": int}，多余键作明细。

因子值一律**按周期算**（不是存量）：券是有发放时间的行为数据，全年累计使用率
对「本周该催谁」没有指导意义。
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Optional

from .prod_db import (
    SO_LINE_FILTER, TIER_RANK,
    _conn, _geo_filter, available,
)

# 业务红线：授牌服务商是无价值客户，不进任何名单/口径（docs/数据库说明.md:267）。
# prod_db.NOT_PLAQUE 是不带表别名的版本；这里 join 了两张表，列名必须限定到 p.
NOT_PLAQUE_P = "COALESCE(p.管理标签,'') <> '授牌服务商'"

# ── 券类因子 ───────────────────────────────────────────────────────────────────
# 载体是 dahua_redpack_grant（大华业务员转化红包发放明细，一行一张券）：业务员
# 发抵用券给服务商，服务商激活够台数 → 券解锁（已兑换）。业务链条正好是
# 「授权签约 → 激活」，所以这些因子都挂在 a2t 这一跳。
#
# ⚠️ 2026-08-01 实测（GET /api/funnel/redpack-values）：全库 447 张券，卡券名称
# 只有四种取值 —— 「50/30/20/10 元红包券（激活 N 台设备解锁）」，**没有任何一张
# 叫「首单礼」**。所以 FIRST_ORDER_LIKE 当前命中 0 行，a2t_fo 恒返回 None。
# 保留该因子是为了让口径显式可见（前端显示「—」并给出原因），而不是悄悄拿别的
# 券种冒充首单礼。首单礼若是另一个活动，其数据尚未入库。
FIRST_ORDER_LIKE = "COALESCE(卡券名称,'') LIKE '%首单%'"

# 转化红包（全部券种）—— 当前唯一有真实数据的券类因子。
# 不筛券种：四种面额券是同一个活动的不同档位（50元=激活5台/10元=激活1台），
# 合起来才是「业务员发的券兑掉几成」这一个问题。
ANY_COUPON = "1=1"

# 「使用」= 卡券状态='已兑换'。该状态的业务含义是服务商激活够台数、红包已解锁领取
# （见 docs/数据库说明.md:1584）。待解锁/待兑换 = 券还在有效期内没用完，已失效 =
# 没激活够、过期作废。分母含全部四种状态：使用率要回答「发出去的券兑掉几成」，
# 把待解锁剔出分母会让刚发券的周期虚高到 100%。
USED_STATUS = "COALESCE(卡券状态,'') = '已兑换'"

# 地区：grant 表只有省份，没有市/区县，靠 发放客户编码 join provider_contract
# 取地区（该表 客户城市/客户区县 与 district_base 100% 对齐，见 prod_db 模块头）。
# join 用 INNER：join 不上的券说明发给了非签约户或已退出的客户，没有地区归属，
# 省级汇总时也不该计入 —— 否则全省 ≠ 各市之和。
_GRANT_JOIN = """
FROM dahua_redpack_grant g
JOIN provider_contract p ON p.客户编码 = g.发放客户编码
"""


def _redpack_rate(conn: sqlite3.Connection, city, district, start, end,
                  coupon_like: str, label: str) -> Optional[Dict[str, Any]]:
    """
    某类红包券在 [start,end] 发放的使用（兑换）率。
    分母 = 该地区该周期发出的该类券数；分子 = 其中 卡券状态='已兑换' 的张数。
    """
    gf, gp = _geo_filter(city, district, "p.客户城市", "p.客户区县")
    where = (f"WHERE {coupon_like} AND {NOT_PLAQUE_P}"
             f"{gf} AND date(g.发放时间) BETWEEN ? AND ?")
    row = conn.execute(
        f"""SELECT COUNT(*) den,
                   SUM(CASE WHEN {USED_STATUS} THEN 1 ELSE 0 END) num,
                   SUM(CASE WHEN COALESCE(卡券状态,'') IN ('待解锁','待兑换')
                            THEN 1 ELSE 0 END) pending,
                   SUM(CASE WHEN COALESCE(卡券状态,'') = '已失效'
                            THEN 1 ELSE 0 END) expired
            {_GRANT_JOIN} {where}""",
        gp + [start, end]).fetchone()

    den = row["den"] or 0
    if den == 0:
        # 没发券 ≠ 使用率 0%。分母为 0 就是无样本，返回 None 让前端显示「—」。
        return None
    num = row["num"] or 0
    return {
        "value": round(num / den * 100, 1),
        "num": num, "den": den,
        "pending": row["pending"] or 0,
        "expired": row["expired"] or 0,
        "label": label,
    }


def first_order_gift_rate(conn, city, district, start, end):
    """首单礼红包券使用率（a2t_fo）。当前库里无此券种 → 恒 None，见上方注释。"""
    return _redpack_rate(conn, city, district, start, end,
                         FIRST_ORDER_LIKE, "首单礼红包券使用率")


def salesman_coupon_rate(conn, city, district, start, end):
    """业务员转化红包券使用率（a2t_meet）—— 发出的券里解锁（已兑换）的比例。"""
    return _redpack_rate(conn, city, district, start, end,
                         ANY_COUPON, "业务员红包券使用率")


# ── 跑动次数 ───────────────────────────────────────────────────────────────────
# 载体优先 visit_record_v（UNION 代理商 visit_record + 大华 dahua_visit_record，
# 大华行映射后「打卡人所属公司」为空）。旧库/合成库没有视图时回落 visit_record。
#
# 两类打卡人，数据源不同、口径必须分开（见 docs/数据库说明.md:188-190）：
#   🏢 大华分销经理    打卡人所属公司 为空   源「SMB客户拜访活动明细」→ dahua_visit_record
#   🏪 代理商业务员    打卡人所属公司 非空   源「拜访活动明细表」→ visit_record
# 判定沿用 src/_views.py 的既有口径，全库一致。
IS_DAHUA = "COALESCE(打卡人所属公司,'') = ''"
IS_DEALER = "COALESCE(打卡人所属公司,'') <> ''"

# ⚠️ 时间列必须用 COALESCE(活动创建时间, 拜访时间)：拜访时间 只到日、带
# 08:00:00 占位，直接用会把周界上的记录算到隔壁周去（docs/数据库说明.md:195）。
VISIT_TIME = "date(COALESCE(活动创建时间, 拜访时间))"


def visit_source(conn) -> str:
    """生产库用 visit_record_v；合成/旧库无视图时用 visit_record。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') "
        "AND name = 'visit_record_v' LIMIT 1"
    ).fetchone()
    return "visit_record_v" if row else "visit_record"


def _visit_count(conn, city, district, start, end,
                 who: str, label: str) -> Optional[Dict[str, Any]]:
    """
    跑动次数（打卡条数，不去重客户）。who 是打卡人筛选 SQL 片段。

    跑动是"次数"型因子而非"率"型：没有天然分母。value 直接是次数，
    单位「次」，目标由用户设。
    """
    src = visit_source(conn)
    gf, gp = _geo_filter(city, district, "拜访客户城市", "拜访客户区县")
    row = conn.execute(
        f"""SELECT COUNT(*) n, COUNT(DISTINCT 客户编码) customers,
                   COUNT(DISTINCT 打卡人姓名) people
            FROM {src}
            WHERE {who}{gf} AND {VISIT_TIME} BETWEEN ? AND ?""",
        gp + [start, end]).fetchone()
    n = row["n"] or 0
    if n == 0:
        # 一次跑动都没有：0 次是真实且有意义的数（不是"无样本"）—— 跑动与
        # 券不同，没发券可能是没到活动期，没跑动就是真没跑。所以返回 0 而非 None。
        return {"value": 0, "num": 0, "den": None, "customers": 0, "people": 0,
                "label": label}
    return {
        "value": n, "num": n, "den": None,
        "customers": row["customers"] or 0,
        "people": row["people"] or 0,
        "label": label,
    }


def visit_all(conn, city, district, start, end):
    """跑动次数·合计（大华 + 代理商）。"""
    return _visit_count(conn, city, district, start, end, "1=1", "跑动次数（合计）")


def visit_dahua(conn, city, district, start, end):
    """跑动次数·大华分销经理。"""
    return _visit_count(conn, city, district, start, end, IS_DAHUA, "跑动次数（大华）")


def visit_dealer(conn, city, district, start, end):
    """跑动次数·代理商业务员。"""
    return _visit_count(conn, city, district, start, end, IS_DEALER, "跑动次数（代理商）")


# ── 新增开单服务商 ─────────────────────────────────────────────────────────────
# 业务定义：当前周期内服务商从签约 V0 → 已开单 V1。库里没有等级变更流水
# （provider_contract 是快照），用安装红包「终身第一次上线」作代理：
#   本期有 install_redpack 上线，且期前从未上线。
# 不等价于「当前 服务商等级 = v1」——同期 V0→V2 也会错过；首次上线即破冰。
# 口径窄于 product_flow SO（用户指定走红包扫码）。
# 地区取签约表 客户城市/客户区县，全省 = 各市之和。
NEW_OPEN_LABEL = "新增开单服务商"


def new_open_providers(conn, city, district, start, end):
    """
    新增开单服务商（a2v1_new_open）：本期首次安装红包上线的签约服务商家数。
    计数型，0 家是真值（不是无样本）。
    """
    gf, gp = _geo_filter(city, district, "p.客户城市", "p.客户区县")
    row = conn.execute(
        f"""SELECT COUNT(*) n FROM (
              SELECT r.上线客户编码
              FROM install_redpack r
              JOIN provider_contract p ON p.客户编码 = r.上线客户编码
              WHERE COALESCE(r.上线客户编码,'') <> ''
                AND {NOT_PLAQUE_P}{gf}
                AND date(r.上线时间) <= ?
              GROUP BY r.上线客户编码
              HAVING MIN(date(r.上线时间)) >= ?
            )""",
        gp + [end, start]).fetchone()
    n = int(row["n"] or 0)
    return {
        "value": n, "num": n, "den": None,
        "customers": n,
        "kind": "new_open",
        "tip": f"本期首次安装红包上线 {n} 家（期前无上线）",
        "label": NEW_OPEN_LABEL,
    }


# ── 流失客户（授权 + 认证）────────────────────────────────────────────────────
# 流失定义（两类取并集，as_of 日评估）：
#   A. 上年有筛后 SO，当年截至 as_of 无筛后 SO
#   B. 当年达到 V2（激活时间落在当年且 ≤ as_of，档位 ≥ v2），
#      且截至 as_of 最近一次筛后 SO 早于 as_of−90 天（或从未 SO）
# 采购 = install_redpack 筛后 SO（与漏斗 V1/V3 同源）。
# 主体 = 授权（有签约日）∩ 认证（协议类型 LIKE %认证%）∩ 非授牌。

from datetime import date as _date, timedelta as _timedelta

SO_LINE_R = (SO_LINE_FILTER
             .replace("国内产品线二级", "r.国内产品线二级")
             .replace("产品名称", "r.产品名称"))
CERT_YES_P = "COALESCE(p.协议类型,'') LIKE '%认证%'"
TIER_RANK_P = TIER_RANK.replace("服务商等级", "p.服务商等级")


def _year_bounds(end: str) -> tuple:
    """按期末年份切「当年 / 上年」。"""
    y = int(str(end)[:4])
    return str(y), f"{y}-01-01", f"{y + 1}-01-01", f"{y - 1}-01-01"


def _day_add(day: str, delta: int) -> str:
    return (_date.fromisoformat(str(day)[:10]) + _timedelta(days=delta)).isoformat()


def _churn_pred_sql(as_of_expr: str, y0: str, prev0: str) -> str:
    """
    相对 as_of_expr（SQL 日期表达式或 'YYYY-MM-DD' 字面量）判定是否流失。
    外层需提供 auth 别名 a（code / act_day / tier）。
    """
    type_a = f"""(
      EXISTS (
        SELECT 1 FROM install_redpack r
        WHERE r.上线客户编码 = a.code AND {SO_LINE_R}
          AND date(r.上线时间) >= '{prev0}' AND date(r.上线时间) < '{y0}'
      )
      AND NOT EXISTS (
        SELECT 1 FROM install_redpack r
        WHERE r.上线客户编码 = a.code AND {SO_LINE_R}
          AND date(r.上线时间) >= '{y0}' AND date(r.上线时间) <= {as_of_expr}
      )
    )"""
    type_b = f"""(
      a.act_day IS NOT NULL
      AND a.act_day >= '{y0}' AND a.act_day <= {as_of_expr}
      AND COALESCE(a.tier, -1) >= 2
      AND NOT EXISTS (
        SELECT 1 FROM install_redpack r
        WHERE r.上线客户编码 = a.code AND {SO_LINE_R}
          AND date(r.上线时间) > date({as_of_expr}, '-90 days')
          AND date(r.上线时间) <= {as_of_expr}
      )
    )"""
    return f"({type_a} OR {type_b})"


def _auth_cert_from(city, district):
    gf, gp = _geo_filter(city, district, "p.客户城市", "p.客户区县")
    sql = f"""
      SELECT p.客户编码 AS code,
             date(p.激活时间) AS act_day,
             {TIER_RANK_P} AS tier
      FROM provider_contract p
      WHERE {NOT_PLAQUE_P} AND {CERT_YES_P}
        AND p.签约日期 IS NOT NULL{gf}
    """
    return sql, gp


def churn_visit_count(conn, city, district, start, end):
    """
    流失客户跑动数量：周期内对「拜访当日已流失」的授权认证服务商的打卡次数
    （大华 + 代理商）。
    """
    _, y0, _, prev0 = _year_bounds(end)
    auth_sql, gp = _auth_cert_from(city, district)
    src = visit_source(conn)
    vgf, vgp = _geo_filter(city, district, "v.拜访客户城市", "v.拜访客户区县")
    pred = _churn_pred_sql("v.vday", y0, prev0)
    row = conn.execute(
        f"""SELECT COUNT(*) n,
                   COUNT(DISTINCT v.code) customers
            FROM (
              SELECT 客户编码 AS code, {VISIT_TIME} AS vday
              FROM {src} v
              WHERE 1=1{vgf} AND {VISIT_TIME} BETWEEN ? AND ?
            ) v
            JOIN ({auth_sql}) a ON a.code = v.code
            WHERE {pred}""",
        vgp + [start, end] + gp,
    ).fetchone()
    n = int(row["n"] or 0)
    return {
        "value": n, "num": n, "den": None,
        "customers": int(row["customers"] or 0),
        "label": "流失客户跑动数量",
        "tip": f"拜访当日已流失的授权认证服务商 · 打卡 {n} 次",
        "kind": "churn_visit",
    }


def churn_recover_count(conn, city, district, start, end):
    """
    流失客户挽回数量：周期首日 D 之前已流失的客户，在本期内有筛后 SO 上线。
    as_of = D−1。
    """
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
              )""",
        gp + [start, end],
    ).fetchone()
    n = int(row["n"] or 0)
    return {
        "value": n, "num": n, "den": None,
        "customers": n,
        "label": "流失客户挽回数量",
        "tip": f"期前已流失、本期有筛后 SO · {n} 家（as_of={as_of}）",
        "kind": "churn_recover",
        "as_of": as_of,
    }


def churn_visit_recover_count(conn, city, district, start, end):
    """
    流失客户跑动挽回：跑动当日 D 该客户已流失，且 D 日及之后（至期末）有筛后 SO。
    按服务商去重。
    """
    _, y0, _, prev0 = _year_bounds(end)
    auth_sql, gp = _auth_cert_from(city, district)
    src = visit_source(conn)
    vgf, vgp = _geo_filter(city, district, "v.拜访客户城市", "v.拜访客户区县")
    pred = _churn_pred_sql("v.vday", y0, prev0)
    row = conn.execute(
        f"""SELECT COUNT(DISTINCT v.code) n
            FROM (
              SELECT 客户编码 AS code, {VISIT_TIME} AS vday
              FROM {src} v
              WHERE 1=1{vgf} AND {VISIT_TIME} BETWEEN ? AND ?
            ) v
            JOIN ({auth_sql}) a ON a.code = v.code
            WHERE {pred}
              AND EXISTS (
                SELECT 1 FROM install_redpack r
                WHERE r.上线客户编码 = v.code AND {SO_LINE_R}
                  AND date(r.上线时间) >= v.vday
                  AND date(r.上线时间) <= ?
              )""",
        vgp + [start, end] + gp + [end],
    ).fetchone()
    n = int(row["n"] or 0)
    return {
        "value": n, "num": n, "den": None,
        "customers": n,
        "label": "流失客户跑动挽回",
        "tip": f"跑动当日已流失且当日及之后有 SO · {n} 家",
        "kind": "churn_visit_recover",
    }


# ── 因子注册表 ─────────────────────────────────────────────────────────────────
# id 与 funnel_factor_defs.id 对应；只有在此注册了取数函数的 auto 因子才算得出值。
FETCHERS: Dict[str, Callable] = {
    "a2t_fo": first_order_gift_rate,
    "a2t_meet": salesman_coupon_rate,
    # 跑动三档：合计 = 大华 + 代理商，三个都独立成因子，各自可设目标、可单独停用。
    # 合计不是前端把两档相加得来 —— 单独查一次，好让「合计 == 大华 + 代理商」
    # 成为一条可验证的断言（自测里就在验它）。
    "a2t_visit": visit_all,
    "a2t_visit_dahua": visit_dahua,
    "a2t_visit_dealer": visit_dealer,
    "a2v1_new_open": new_open_providers,
    "a2t_churn_visit": churn_visit_count,
    "a2t_churn_recover": churn_recover_count,
    "a2t_churn_visit_recover": churn_visit_recover_count,
}

# 因子定义（写入 funnel_factor_defs 的种子数据）。
# fetch_query 列留空 —— 取数走 FETCHERS 里的 Python 函数，见模块 docstring。
#
# ⚠️ **所有因子的 default_target 一律留空**（金总 2026-08-01 定）：因子目标没有
# 库内下发值，各地区基数差一个量级，任何预设都是拍脑袋的数。留空让页面显示空输入
# 框、由用户按地区手填，比给一个看起来权威的假目标好。
SEED_DEFS: List[Dict[str, Any]] = [
    {"id": "a2t_fo", "conv_key": "a2t", "name": "首单礼红包券使用率",
     "unit": "%", "source": "auto", "display_order": 10},
    {"id": "a2t_meet", "conv_key": "a2t", "name": "业务员红包券使用率",
     "unit": "%", "source": "auto", "display_order": 20},
    {"id": "a2t_visit", "conv_key": "a2t", "name": "跑动次数（合计）",
     "unit": "次", "source": "auto", "display_order": 1},
    {"id": "a2t_visit_dahua", "conv_key": "a2t", "name": "跑动次数（大华）",
     "unit": "次", "source": "auto", "display_order": 2},
    {"id": "a2t_visit_dealer", "conv_key": "a2t", "name": "跑动次数（代理商）",
     "unit": "次", "source": "auto", "display_order": 3},
    {"id": "a2t_churn_visit", "conv_key": "a2t", "name": "流失客户跑动数量",
     "unit": "次", "source": "auto", "display_order": 4},
    {"id": "a2t_churn_recover", "conv_key": "a2t", "name": "流失客户挽回数量",
     "unit": "家", "source": "auto", "display_order": 5},
    {"id": "a2t_churn_visit_recover", "conv_key": "a2t",
     "name": "流失客户跑动挽回",
     "unit": "家", "source": "auto", "display_order": 6},
    {"id": "a2v1_new_open", "conv_key": "a2v1", "name": "新增开单服务商",
     "unit": "家", "source": "auto", "display_order": 5},
]


def compute(factor_id: str, city: Optional[str], district: Optional[str],
            start: str, end: str) -> Optional[Dict[str, Any]]:
    """算单个因子。生产库不可用或因子未注册取数函数时返回 None。"""
    fn = FETCHERS.get(factor_id)
    if fn is None or not available():
        return None
    with _conn() as conn:
        return fn(conn, city, district, start, end)


def compute_all(city: Optional[str], district: Optional[str],
                start: str, end: str) -> Dict[str, Any]:
    """算全部已注册 auto 因子，返回 {factor_id: detail|None}。"""
    if not available():
        return {}
    out: Dict[str, Any] = {}
    with _conn() as conn:
        for fid, fn in FETCHERS.items():
            try:
                out[fid] = fn(conn, city, district, start, end)
            except sqlite3.Error as e:
                out[fid] = {"error": str(e)}
    return out


# ── 诊断：券种取值分布 ─────────────────────────────────────────────────────────

def redpack_values() -> Optional[Dict[str, Any]]:
    """
    dahua_redpack_grant 的 卡券名称 / 卡券状态 取值分布，用来核对
    FIRST_ORDER_LIKE 与 USED_STATUS 这两个判定条件选得对不对。纯诊断用途。
    """
    if not available():
        return None
    out: Dict[str, Any] = {
        "coupon_like": FIRST_ORDER_LIKE, "used_status": USED_STATUS,
    }
    with _conn() as conn:
        try:
            out["卡券名称"] = [dict(r) for r in conn.execute(
                "SELECT COALESCE(卡券名称,'<NULL>') v, COUNT(*) n "
                "FROM dahua_redpack_grant GROUP BY 1 ORDER BY n DESC").fetchall()]
            out["卡券状态"] = [dict(r) for r in conn.execute(
                "SELECT COALESCE(卡券状态,'<NULL>') v, COUNT(*) n "
                "FROM dahua_redpack_grant GROUP BY 1 ORDER BY n DESC").fetchall()]
            out["发放时间范围"] = dict(conn.execute(
                "SELECT MIN(date(发放时间)) min, MAX(date(发放时间)) max, "
                "COUNT(*) total FROM dahua_redpack_grant").fetchone())
            # join 命中率：join 不上的券没有地区归属、不进任何地区口径
            out["join命中"] = dict(conn.execute(
                f"""SELECT COUNT(*) total,
                    SUM(CASE WHEN p.客户编码 IS NOT NULL THEN 1 ELSE 0 END) matched
                    FROM dahua_redpack_grant g
                    LEFT JOIN provider_contract p ON p.客户编码 = g.发放客户编码"""
            ).fetchone())
            # 首单礼那一类券单独看一眼：有多少张、什么状态、跨哪些月
            out["首单礼"] = dict(conn.execute(
                f"""SELECT COUNT(*) n, MIN(date(发放时间)) min, MAX(date(发放时间)) max
                    FROM dahua_redpack_grant WHERE {FIRST_ORDER_LIKE}""").fetchone())
        except sqlite3.Error as e:
            out["error"] = str(e)
    return out
