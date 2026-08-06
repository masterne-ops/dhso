"""地市 / 区县 / 代理商 全景图 — 共用数据层

四个维度统一返回：
  - so_growth         SO 增长
  - provider_quality  服务商质量
  - redpack           红包投放
  - sales_visits      销售跑动

调用：
    gather_panorama(conn, city, district=None, dealer=None, period_start, period_end)
  返回 dict 结构 = {so_growth: {...}, provider_quality: {...}, redpack: {...}, sales_visits: {...}}
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Optional

import pandas as pd

from _provider_coverage import authorized_provider_by_district, authorized_provider_metrics


# ══════════════════════════════════════════════
# 过滤维度抽象：city / district / dealer
# ══════════════════════════════════════════════

def _redpack_filter(city: str, district: str = None, dealer: str = None) -> tuple:
    """install_redpack_v 的 WHERE 子句 + 参数"""
    # 代理商场景下，地市只用于筛选代理商名单；选中后看该代理商全省数据。
    if dealer:
        return "所属一级客户 = ?", [dealer]
    parts = ["上线客户地市 = ?"]
    params = [city]
    if district:
        parts.append("上线客户区县 = ?")
        params.append(district)
    return ' AND '.join(parts), params


def _pf_filter(city: str, district: str = None, dealer: str = None) -> tuple:
    """product_flow_v 的 WHERE 子句 + 参数

    注:product_flow.所属一级客户 填充率仅 1.4%(几乎全空),
        改用「出库客户名称」过滤代理商 — 100% 填充,代表代理商出货客户。
    """
    if dealer:
        return "出库客户名称 = ?", [dealer]
    parts = ["上线城市 = ?"]
    params = [city]
    if district:
        parts.append("上线区县 = ?")
        params.append(district)
    return ' AND '.join(parts), params


def _visit_filter(city: str, district: str = None, dealer: str = None) -> tuple:
    """visit_record_v 的 WHERE 子句 + 参数（visit 表没有"代理商"维度，跳过）"""
    parts = ["_打卡异常无效 = 0", "_真异常打卡 = 0"]
    params = []
    if not dealer:
        parts.insert(0, "拜访客户城市 = ?")
        params.append(city)
    if district:
        parts.append("拜访客户区县 = ?")
        params.append(district)
    return ' AND '.join(parts), params


def _entity_label(city: str, district: str = None, dealer: str = None) -> str:
    parts = [city]
    if district:
        parts.append(district)
    if dealer:
        parts.append(dealer)
    return ' / '.join(parts)


# ══════════════════════════════════════════════
# Section 1：SO 增长
# ══════════════════════════════════════════════

def _section_so_growth(
    conn, city, district, dealer, period_start, period_end,
) -> dict:
    pf_w, pf_p = _pf_filter(city, district, dealer)
    rp_w, rp_p = _redpack_filter(city, district, dealer)

    # 注:product_flow 代理商口径用「出库客户名称」(100% 填充);
    #    install_redpack 代理商口径用「所属一级客户」(也是 100% 填充);
    #    两个口径都可用。dealer 模式下两个都查,各自展示。
    is_dealer = bool(dealer)

    # 当期实际
    cur_pf = pd.read_sql(f"""
        SELECT
            COALESCE(SUM(最新分销价)/10000, 0) AS pf_万,
            COUNT(*) AS pf_台数
          FROM product_flow_v
         WHERE {pf_w}
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(*pf_p, period_start, period_end)).iloc[0]

    rp = pd.read_sql(f"""
        SELECT
            COALESCE(SUM(产品现有分销价)/10000, 0) AS rp_万,
            COUNT(*) AS rp_台数
          FROM install_redpack_v
         WHERE {rp_w}
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(*rp_p, period_start, period_end)).iloc[0]

    # 当期 SO 主口径:统一用全量感知(代理商现在也能查 product_flow)
    cur_main_amt = float(cur_pf['pf_万'])
    cur_main_n = int(cur_pf['pf_台数'])

    # 同期 & 环期
    yoy_start = str(pd.Period(period_start, freq='M') - 12)
    yoy_end = str(pd.Period(period_end, freq='M') - 12)
    yoy = pd.read_sql(f"""
        SELECT COALESCE(SUM(最新分销价)/10000, 0) AS 万
          FROM product_flow_v WHERE {pf_w}
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(*pf_p, yoy_start, yoy_end)).iloc[0]['万']

    # 环期
    n_months = (pd.Period(period_end, freq='M') - pd.Period(period_start, freq='M')).n + 1
    mom_end = str(pd.Period(period_start, freq='M') - 1)
    mom_start = str(pd.Period(period_start, freq='M') - n_months)
    mom = pd.read_sql(f"""
        SELECT COALESCE(SUM(最新分销价)/10000, 0) AS 万
          FROM product_flow_v WHERE {pf_w}
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(*pf_p, mom_start, mom_end)).iloc[0]['万']

    # 应达成（只算城市级 / 区县级，代理商没有目标）
    target_amt = None
    annual_target = None            # 全年 SO 目标(万)
    ytd_should_amt = None           # YTD 应达成(年初到 period_end 节奏累计)
    ytd_actual_amt = None           # YTD 累计实际(年初到 period_end)
    if not dealer:
        year = int(period_start.split('-')[0])
        end_month_num = int(period_end.split('-')[1])
        month_nums = [int(m.split('-')[1])
                      for m in [str(p) for p in pd.period_range(period_start, period_end, freq='M')]]
        placeholders = ','.join('?' * len(month_nums))
        ytd_month_nums = list(range(1, end_month_num + 1))
        ytd_placeholders = ','.join('?' * len(ytd_month_nums))

        if district:
            # 评估期应达成
            tgt = pd.read_sql(f"""
                SELECT COALESCE(SUM(t.SO目标_万 * r.占比), 0) AS 应达成_万,
                       MAX(t.SO目标_万) AS 全年目标_万
                  FROM kpi_targets t
                  JOIN kpi_rhythm r ON r.年度 = t.年度
                 WHERE t.城市 = ? AND t.区县 = ? AND t.年度 = ?
                   AND r.指标 LIKE '省区SO进度条%'
                   AND r.月份 IN ({placeholders})
            """, conn, params=(city, district, year, *month_nums))
            # YTD 应达成 (年初到 period_end)
            ytd_tgt = pd.read_sql(f"""
                SELECT COALESCE(SUM(t.SO目标_万 * r.占比), 0) AS YTD应达成_万
                  FROM kpi_targets t
                  JOIN kpi_rhythm r ON r.年度 = t.年度
                 WHERE t.城市 = ? AND t.区县 = ? AND t.年度 = ?
                   AND r.指标 LIKE '省区SO进度条%'
                   AND r.月份 IN ({ytd_placeholders})
            """, conn, params=(city, district, year, *ytd_month_nums))
            # YTD 累计实际
            ytd_act = pd.read_sql(f"""
                SELECT COALESCE(SUM(最新分销价)/10000, 0) AS YTD实际_万
                  FROM product_flow_v
                 WHERE 上线城市 = ? AND 上线区县 = ?
                   AND 上线年月 BETWEEN ? AND ?
            """, conn, params=(city, district, f'{year}-01', period_end))
        else:
            tgt = pd.read_sql(f"""
                SELECT COALESCE(SUM(t.SO目标_万 * r.占比), 0) AS 应达成_万,
                       (SELECT SUM(SO目标_万) FROM kpi_targets WHERE 城市 = ? AND 年度 = ?) AS 全年目标_万
                  FROM kpi_targets t
                  JOIN kpi_rhythm r ON r.年度 = t.年度
                 WHERE t.城市 = ? AND t.年度 = ?
                   AND r.指标 LIKE '省区SO进度条%'
                   AND r.月份 IN ({placeholders})
            """, conn, params=(city, year, city, year, *month_nums))
            ytd_tgt = pd.read_sql(f"""
                SELECT COALESCE(SUM(t.SO目标_万 * r.占比), 0) AS YTD应达成_万
                  FROM kpi_targets t
                  JOIN kpi_rhythm r ON r.年度 = t.年度
                 WHERE t.城市 = ? AND t.年度 = ?
                   AND r.指标 LIKE '省区SO进度条%'
                   AND r.月份 IN ({ytd_placeholders})
            """, conn, params=(city, year, *ytd_month_nums))
            ytd_act = pd.read_sql(f"""
                SELECT COALESCE(SUM(最新分销价)/10000, 0) AS YTD实际_万
                  FROM product_flow_v
                 WHERE 上线城市 = ?
                   AND 上线年月 BETWEEN ? AND ?
            """, conn, params=(city, f'{year}-01', period_end))

        target_amt = float(tgt.iloc[0]['应达成_万']) if not tgt.empty else None
        annual_target = float(tgt.iloc[0]['全年目标_万']) if not tgt.empty and tgt.iloc[0]['全年目标_万'] else None
        ytd_should_amt = float(ytd_tgt.iloc[0]['YTD应达成_万']) if not ytd_tgt.empty else None
        ytd_actual_amt = float(ytd_act.iloc[0]['YTD实际_万']) if not ytd_act.empty else None

    # 月度走势(近 12 月)— 全口径用 product_flow
    last_m = pd.Period(period_end, freq='M')
    trend_start = str(last_m - 11)
    trend = pd.read_sql(f"""
        SELECT 上线年月 AS 月份,
               COALESCE(SUM(最新分销价)/10000, 0) AS 金额_万,
               COUNT(*) AS 台数
          FROM product_flow_v
         WHERE {pf_w}
           AND 上线年月 BETWEEN ? AND ?
         GROUP BY 上线年月
         ORDER BY 上线年月
    """, conn, params=(*pf_p, trend_start, str(last_m)))

    # 城市级：区县完成率分布
    district_breakdown = None
    if not district and not dealer:
        db = pd.read_sql(f"""
            SELECT 上线区县 AS 区县,
                   COALESCE(SUM(最新分销价)/10000, 0) AS 金额_万,
                   COUNT(*) AS 台数
              FROM product_flow_v
             WHERE 上线城市 = ?
               AND 上线年月 BETWEEN ? AND ?
             GROUP BY 上线区县
             ORDER BY 金额_万 DESC
        """, conn, params=(city, period_start, period_end))
        district_breakdown = db.to_dict('records')

    return {
        '_主口径': '全量感知',
        '当期_主口径_万': round(cur_main_amt, 2),
        '当期_主口径_台数': cur_main_n,
        '当期_全量感知_万': round(float(cur_pf['pf_万']), 2),
        '当期_全量感知_台数': int(cur_pf['pf_台数']),
        '当期_红包扫码_万': round(float(rp['rp_万']), 2),
        '当期_红包扫码_台数': int(rp['rp_台数']),
        '绑定率': round(float(rp['rp_万']) / float(cur_pf['pf_万']), 4) if float(cur_pf['pf_万']) > 0 else None,
        '同期_万': round(float(yoy), 2),
        '同比': round((cur_main_amt - float(yoy)) / float(yoy), 4) if float(yoy) > 0 else None,
        '环期_万': round(float(mom), 2),
        '环比': round((cur_main_amt - float(mom)) / float(mom), 4) if float(mom) > 0 else None,
        '应达成_万': round(target_amt, 2) if target_amt else None,
        '完成率': round(cur_main_amt / target_amt, 4) if target_amt and target_amt > 0 else None,
        # ─── 新增:年度全量 + YTD 累计 ───
        '年度目标_万': round(annual_target, 2) if annual_target else None,
        'YTD应达成_万': round(ytd_should_amt, 2) if ytd_should_amt else None,
        'YTD累计_万': round(ytd_actual_amt, 2) if ytd_actual_amt else None,
        'YTD完成率': round(ytd_actual_amt / ytd_should_amt, 4)
                      if ytd_should_amt and ytd_should_amt > 0 and ytd_actual_amt is not None else None,
        '年完成率': round(ytd_actual_amt / annual_target, 4)
                     if annual_target and annual_target > 0 and ytd_actual_amt is not None else None,
        '月度趋势': trend.to_dict('records'),
        '区县分布': district_breakdown,   # 城市级才有
    }


# ══════════════════════════════════════════════
# Section 2：服务商质量
# ══════════════════════════════════════════════

def _section_provider_quality(
    conn, city, district, dealer, period_start, period_end,
) -> dict:
    rp_w, rp_p = _redpack_filter(city, district, dealer)

    period_end_dt = pd.Timestamp(period_end + '-01') + pd.offsets.MonthEnd(0)
    period_start_dt = pd.Timestamp(period_start + '-01')

    # 当期活跃服务商 + 等级分布
    cur_codes = pd.read_sql(f"""
        SELECT DISTINCT 上线客户编码 AS 客户编码,
               MAX(上线客户名称) AS 客户名称,
               MAX(上线客户区县) AS 区县
          FROM install_redpack_v
         WHERE {rp_w}
           AND 上线年月 BETWEEN ? AND ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
         GROUP BY 上线客户编码
    """, conn, params=(*rp_p, period_start, period_end))

    # 取这些客户的累计货值（用全历史，不仅当期）算等级
    if cur_codes.empty:
        return _empty_quality()
    codes_in = "','".join(cur_codes['客户编码'].astype(str))
    cum = pd.read_sql(f"""
        SELECT 上线客户编码 AS 客户编码,
               SUM(产品现有分销价) AS 累计货值,
               COUNT(*) AS 累计台数
          FROM install_redpack_v
         WHERE 上线客户编码 IN ('{codes_in}')
         GROUP BY 上线客户编码
    """, conn)
    cur_codes = cur_codes.merge(cum, on='客户编码', how='left')

    # 9 宫格 — 从 _metrics_rfm 来
    from _metrics_rfm import r_bucket, f_bucket, calc_rfm
    # 等级用 provider_contract 官方原始等级(不再按货值 tier_of 重算)；活跃但未签约的归「未签约」
    _TIER_LABEL = {f'v{i}服务商': f'V{i}' for i in range(6)}
    _codes_t = "','".join(cur_codes['客户编码'].astype(str))
    _tier = pd.read_sql(f"""
        SELECT 客户编码, 服务商等级 FROM provider_contract
         WHERE 客户编码 IN ('{_codes_t}') AND 服务商等级 IS NOT NULL AND 服务商等级 != ''
    """, conn)
    _tmap = dict(zip(_tier['客户编码'].astype(str), _tier['服务商等级'].astype(str)))
    cur_codes['等级'] = (cur_codes['客户编码'].astype(str).map(_tmap)
                         .map(_TIER_LABEL).fillna('未签约'))
    tier_dist = cur_codes['等级'].value_counts().to_dict()

    # RFM 9 宫格(仅活跃客户)
    n_months = (pd.Period(period_end, freq='M') - pd.Period(period_start, freq='M')).n + 1
    rfm_codes = cur_codes['客户编码'].astype(str).tolist()
    codes_in2 = "','".join(rfm_codes)
    acts = pd.read_sql(f"""
        SELECT 上线客户编码 AS 客户编码, 上线时间, 产品现有分销价
          FROM install_redpack_v
         WHERE 上线客户编码 IN ('{codes_in2}')
    """, conn)
    acts['上线时间'] = pd.to_datetime(acts['上线时间'])
    grouped = {c: g for c, g in acts.groupby('客户编码')}

    rfm_rows = []
    for code in rfm_codes:
        r = calc_rfm(grouped.get(code), period_end_dt, with_growth=False)
        if r is None:
            continue
        rfm_rows.append({'客户编码': code, 'R': r['R'], 'F': r['F'], 'M': r['M_cur']})
    rfm_df = pd.DataFrame(rfm_rows)

    if rfm_df.empty:
        grid = []
    else:
        rfm_df['R档'] = rfm_df['R'].apply(r_bucket)
        rfm_df['F档'] = rfm_df['F'].apply(f_bucket)
        grid_df = (rfm_df.groupby(['R档', 'F档'])
                          .agg(客户数=('客户编码', 'count'),
                               累计货值_万=('M', lambda x: round(x.sum()/10000, 2)))
                          .reset_index())
        grid = grid_df.to_dict('records')

    # 流失/沉睡（vs 上一段）
    mom_end = str(pd.Period(period_start, freq='M') - 1)
    mom_start = str(pd.Period(period_start, freq='M') - n_months)
    mom_codes = set(pd.read_sql(f"""
        SELECT DISTINCT 上线客户编码 FROM install_redpack_v
         WHERE {rp_w}
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(*rp_p, mom_start, mom_end))['上线客户编码'].astype(str))
    cur_set = set(cur_codes['客户编码'].astype(str))
    持续活跃 = cur_set & mom_codes
    沉睡 = mom_codes - cur_set     # 环期有，当期没
    新增 = cur_set - mom_codes     # 当期有，环期没

    # 异常服务商（只算当前 entity 范围）
    abnormal = _query_abnormal(conn, city, district, dealer)

    # ─── 服务商个体明细(代理商场景下展开,city/区县也提供)─────
    detail = cur_codes[['客户编码', '客户名称', '累计货值', '累计台数', '等级']].copy()
    if not rfm_df.empty:
        rfm_min = rfm_df[['客户编码', 'R', 'F', 'M', 'R档', 'F档']].copy()
        detail = detail.merge(rfm_min, on='客户编码', how='left')
    else:
        detail['R'] = None; detail['F'] = None; detail['M'] = None
        detail['R档'] = None; detail['F档'] = None

    # 当期 SO + 上次交易时间
    if rfm_codes:
        codes_in3 = "','".join(rfm_codes)
        cur_period_so = pd.read_sql(f"""
            SELECT 上线客户编码 AS 客户编码,
                   上线客户名称 AS 名称_最新,
                   上线客户地市 AS 地市,
                   上线客户区县 AS 区县,
                   上线客户渠道客户类型 AS 客户类型,
                   COUNT(*) AS 当期台数,
                   SUM(产品现有分销价) AS 当期货值
              FROM install_redpack_v
             WHERE 上线客户编码 IN ('{codes_in3}')
               AND 上线年月 BETWEEN ? AND ?
             GROUP BY 上线客户编码
        """, conn, params=(period_start, period_end))
        detail = detail.merge(cur_period_so, on='客户编码', how='left')
        detail['当期台数'] = detail['当期台数'].fillna(0).astype(int)
        detail['当期货值'] = detail['当期货值'].fillna(0)

    # 标记马甲 + 关闭
    detail['标签'] = ''

    # 状态:当期活跃/新增/沉睡
    new_set = 新增; sleep_set = 沉睡
    def _stat(c):
        if c in new_set: return '🆕 新增'
        return '🟢 持续活跃'
    detail['状态'] = detail['客户编码'].astype(str).apply(_stat)

    # 格式化输出
    detail_show = detail.copy()
    detail_show['累计货值_万'] = (detail_show['累计货值'].fillna(0)/10000).round(2)
    detail_show['当期货值_万'] = (detail_show['当期货值'].fillna(0)/10000).round(2)
    if 'M' in detail_show.columns:
        detail_show['M_近12月_万'] = (detail_show['M'].fillna(0)/10000).round(2)
    else:
        detail_show['M_近12月_万'] = 0
    detail_show = detail_show.sort_values('累计货值_万', ascending=False)
    out_cols = ['客户编码', '客户名称', '地市', '区县', '客户类型', '等级',
                'R', 'F', 'M_近12月_万', 'R档', 'F档', '当期台数', '当期货值_万',
                '累计货值_万', '累计台数', '状态']
    out_cols = [c for c in out_cols if c in detail_show.columns]
    provider_detail = detail_show[out_cols].to_dict('records')

    # 加入沉睡服务商明细(环期有 / 当期无)— 单独段
    sleep_codes = list(沉睡)
    sleep_detail = []
    if sleep_codes and len(sleep_codes) <= 500:
        codes_in_sleep = "','".join(sleep_codes)
        sleep_df = pd.read_sql(f"""
            SELECT 上线客户编码 AS 客户编码,
                   MAX(上线客户名称) AS 客户名称,
                   MAX(上线客户地市) AS 地市,
                   MAX(上线客户区县) AS 区县,
                   MAX(上线客户渠道客户类型) AS 客户类型,
                   COUNT(*) AS 累计台数,
                   ROUND(SUM(产品现有分销价)/10000, 2) AS 累计货值_万,
                   MAX(上线时间) AS 最近上线
              FROM install_redpack_v
             WHERE 上线客户编码 IN ('{codes_in_sleep}')
             GROUP BY 上线客户编码
             ORDER BY 累计货值_万 DESC
        """, conn)
        sleep_detail = sleep_df.to_dict('records')

    return {
        '总数': int(len(cur_codes)),
        '等级分布': {k: int(v) for k, v in tier_dist.items()},
        'RFM_9宫格': grid,
        '持续活跃': len(持续活跃),
        '沉睡': len(沉睡),
        '新增': len(新增),
        '异常服务商': abnormal,
        '服务商明细': provider_detail,
        '沉睡明细': sleep_detail,
        **_worthless_in_scope(conn, city, district, dealer, cur_set),
    }


def _worthless_in_scope(conn, city, district, dealer, active_codes: set) -> dict:
    """授牌服务商(无价值客户,管理标签口径)在当前实体范围内的数量,单独筛出展示。"""
    w, p = (['上级分销商名称 = ?'], [dealer]) if dealer else (['客户城市 = ?'], [city])
    if district:
        w.append('客户区县 = ?'); p.append(district)
    try:
        row = conn.execute(
            f"SELECT COUNT(*), SUM(CASE WHEN 管理标签='授牌服务商' THEN 1 ELSE 0 END) "
            f"FROM provider_contract WHERE {' AND '.join(w)}", p).fetchone()
        total, worth = int(row[0] or 0), int(row[1] or 0)
        wc = set(str(r[0]) for r in conn.execute(
            f"SELECT 客户编码 FROM provider_contract WHERE {' AND '.join(w)} "
            f"AND 管理标签='授牌服务商'", p))
        return {'签约服务商总数': total, '授牌服务商数': worth,
                '活跃中授牌数': len(active_codes & wc)}
    except Exception:
        return {'签约服务商总数': 0, '授牌服务商数': 0, '活跃中授牌数': 0}


def _empty_quality() -> dict:
    return {
        '总数': 0, '等级分布': {}, 'RFM_9宫格': [],
        '持续活跃': 0, '沉睡': 0, '新增': 0,
        '异常服务商': {'马甲': 0, '伞形_组数': 0, '伞形_户数': 0, '低效服务商': 0},
        '签约服务商总数': 0, '授牌服务商数': 0, '活跃中授牌数': 0,
    }


def _query_abnormal(conn, city, district=None, dealer=None) -> dict:
    """异常服务商计数（实体范围内）"""
    rp_w, rp_p = _redpack_filter(city, district, dealer)

    # 马甲
    if dealer:
        vest_n = pd.read_sql(
            "SELECT COUNT(*) AS n FROM vest_account WHERE 对应一级 = ?",
            conn, params=(dealer,),
        ).iloc[0]['n']
    else:
        # 城市/区县：vest_account 不含区县字段，只能按城市 + 客户编码反查
        if district:
            # 取该区县的客户编码集合 ∩ vest_account
            codes = pd.read_sql(f"""
                SELECT DISTINCT 上线客户编码 FROM install_redpack_v
                 WHERE {rp_w}
            """, conn, params=rp_p)['上线客户编码'].astype(str).tolist()
            if codes:
                codes_in = "','".join(codes)
                vest_n = pd.read_sql(f"""
                    SELECT COUNT(*) AS n FROM vest_account
                     WHERE 城市 = ? AND 服务商客户编码 IN ('{codes_in}')
                """, conn, params=(city,)).iloc[0]['n']
            else:
                vest_n = 0
        else:
            vest_n = pd.read_sql(
                "SELECT COUNT(*) AS n FROM vest_account WHERE 城市 = ?",
                conn, params=(city,),
            ).iloc[0]['n']

    # 伞形（老板姓名 + 电话 ≥ 2 家）
    if dealer:
        # 该代理商旗下的服务商客户编码集合
        codes = pd.read_sql(f"""
            SELECT DISTINCT 上线客户编码 FROM install_redpack_v
             WHERE 所属一级客户 = ?
        """, conn, params=(dealer,))['上线客户编码'].astype(str).tolist()
        if codes:
            codes_in = "','".join(codes)
            umb = pd.read_sql(f"""
                WITH grp AS (
                    SELECT 老板姓名, 老板电话, COUNT(*) AS n
                      FROM provider_profile
                     WHERE 客户编码 IN ('{codes_in}')
                       AND 老板姓名 IS NOT NULL AND 老板姓名 != ''
                       AND 老板电话 IS NOT NULL AND 老板电话 != ''
                     GROUP BY 老板姓名, 老板电话
                    HAVING COUNT(*) >= 2
                )
                SELECT COUNT(*) AS 组, COALESCE(SUM(n), 0) AS 户 FROM grp
            """, conn).iloc[0]
        else:
            umb = {'组': 0, '户': 0}
    else:
        params = [city]
        sql = """
            SELECT 老板姓名, 老板电话, COUNT(*) AS n
              FROM provider_profile
             WHERE 地市 = ?
               AND 老板姓名 IS NOT NULL AND 老板姓名 != ''
               AND 老板电话 IS NOT NULL AND 老板电话 != ''
        """
        if district:
            sql += " AND 区县 = ?"
            params.append(district)
        sql += " GROUP BY 老板姓名, 老板电话 HAVING COUNT(*) >= 2"
        u = pd.read_sql(sql, conn, params=params)
        umb = {'组': int(len(u)), '户': int(u['n'].sum()) if not u.empty else 0}

    # 假签约
    db_max = pd.read_sql("SELECT MAX(上线时间) AS d FROM install_redpack_v", conn).iloc[0]['d']
    pc_sql = """
        WITH cust_amt AS (
            SELECT 上线客户编码 AS 客户编码, COUNT(*) AS n
              FROM install_redpack_v GROUP BY 上线客户编码
        )
        SELECT COUNT(*) AS n FROM provider_contract pc
        LEFT JOIN cust_amt ca ON ca.客户编码 = pc.客户编码
        WHERE pc.签约日期 IS NOT NULL
          AND date(pc.签约日期) <= date(?, '-60 day')
          AND COALESCE(ca.n, 0) <= 2
    """
    pc_params = [db_max]
    if not dealer:
        pc_sql += " AND pc.客户城市 = ?"
        pc_params.append(city)
    if district and not dealer:
        pc_sql += " AND pc.客户区县 = ?"
        pc_params.append(district)
    if dealer:
        pc_sql += " AND pc.上级客户名称 = ?"
        pc_params.append(dealer)
    fake_n = pd.read_sql(pc_sql, conn, params=pc_params).iloc[0]['n']

    return {
        '马甲': int(vest_n),
        '伞形_组数': int(umb['组']) if isinstance(umb, dict) else int(umb.get('组', 0)),
        '伞形_户数': int(umb['户']) if isinstance(umb, dict) else int(umb.get('户', 0)),
        '低效服务商': int(fake_n),
    }


# ══════════════════════════════════════════════
# Section 3：红包投放
# ══════════════════════════════════════════════

def _section_redpack(
    conn, city, district, dealer, period_start, period_end,
) -> dict:
    rp_w, rp_p = _redpack_filter(city, district, dealer)

    # 全省 ROI 基线（不带过滤，用于对比）
    prov = pd.read_sql("""
        SELECT COALESCE(SUM(中奖金额), 0) AS 红包,
               COALESCE(SUM(产品现有分销价), 0) AS 上线
          FROM install_redpack_v
         WHERE 上线年月 BETWEEN ? AND ?
    """, conn, params=(period_start, period_end)).iloc[0]
    prov_roi = float(prov['红包']) / float(prov['上线']) if float(prov['上线']) > 0 else None

    # 当前实体 ROI
    ent = pd.read_sql(f"""
        SELECT COALESCE(SUM(中奖金额), 0) AS 红包,
               COALESCE(SUM(产品现有分销价), 0) AS 上线,
               COUNT(*) AS 笔数
          FROM install_redpack_v
         WHERE {rp_w}
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(*rp_p, period_start, period_end)).iloc[0]
    ent_roi = float(ent['红包']) / float(ent['上线']) if float(ent['上线']) > 0 else None

    # 高/低偏离服务商
    sp = pd.read_sql(f"""
        SELECT 上线客户编码 AS 客户编码,
               MAX(上线客户名称) AS 客户名称,
               MAX(上线客户区县) AS 区县,
               SUM(中奖金额) AS 红包额,
               SUM(产品现有分销价) AS 上线额,
               COUNT(*) AS 笔数
          FROM install_redpack_v
         WHERE {rp_w}
           AND 上线年月 BETWEEN ? AND ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
         GROUP BY 上线客户编码
        HAVING SUM(产品现有分销价) >= 1000
    """, conn, params=(*rp_p, period_start, period_end))

    high = low = []
    if not sp.empty and prov_roi:
        sp['服务商ROI'] = sp['红包额'] / sp['上线额']
        sp['偏离度'] = sp['服务商ROI'] / prov_roi
        sp['红包额_元'] = sp['红包额'].round(2)
        sp['上线额_万'] = (sp['上线额'] / 10000).round(2)

        # 正偏离(红包多 vs 上线少,ROI 偏高)— Top 50 按偏离度降序
        h = sp[sp['偏离度'] > 1.5].sort_values('偏离度', ascending=False).head(50)
        high = h[['客户编码', '客户名称', '区县', '红包额_元', '上线额_万', '偏离度']].round({'偏离度': 2}).to_dict('records')

        # 负偏离(红包少但上线高,ROI 偏低)— Top 50 按上线额降序
        l = sp[(sp['偏离度'] < 0.5) & (sp['上线额'] >= 10000)].sort_values('上线额', ascending=False).head(50)
        low = l[['客户编码', '客户名称', '区县', '红包额_元', '上线额_万', '偏离度']].round({'偏离度': 2}).to_dict('records')

    # 666 大额红包审核
    audit_666 = _audit_666_visits(conn, city, district, dealer, period_start, period_end)

    return {
        '当期红包额_元': round(float(ent['红包']), 2),
        '当期上线额_万': round(float(ent['上线'])/10000, 2),
        '本实体ROI': round(ent_roi, 4) if ent_roi else None,
        '全省ROI基线': round(prov_roi, 4) if prov_roi else None,
        '偏离度': round(ent_roi / prov_roi, 2) if (ent_roi and prov_roi) else None,
        '正偏离名单_top50': high,
        '负偏离名单_top50': low,
        '审核666': audit_666,
    }


def _audit_666_visits(conn, city, district, dealer, period_start, period_end) -> dict:
    """中过 666 的服务商,2 周 / 4 周后业务员到访情况审核

    评估期内中 666 的服务商,按"最近一次 666 时间"作为基准:
    - 2 周内(0-14 天)被 大华业务员 / 代理商业务员 拜访的数量
    - 4 周内(0-28 天)同上

    各算"是否被拜访过(至少一次)"。
    """
    rp_w, rp_p = _redpack_filter(city, district, dealer)

    # 1. 评估期内中过 666 的服务商
    s666 = pd.read_sql(f"""
        SELECT 上线客户编码 AS 客户编码,
               MAX(上线客户名称) AS 客户名称,
               MAX(上线客户区县) AS 区县,
               MAX(所属一级客户) AS 签约代理商,
               MAX(中奖时间) AS 最近666时间,
               COUNT(*) AS 中奖次数
          FROM install_redpack_v
         WHERE {rp_w}
           AND 中奖金额 = 666
           AND 中奖时间 IS NOT NULL
           AND 上线年月 BETWEEN ? AND ?
         GROUP BY 上线客户编码
    """, conn, params=(*rp_p, period_start, period_end))

    if s666.empty:
        return {'总数': 0, '_empty': True}

    s666['最近666时间'] = pd.to_datetime(s666['最近666时间'])

    # 2. 数据库最末日 — 用来判断"4 周窗口是否过完"
    db_max = pd.read_sql(
        "SELECT MAX(date(拜访时间)) AS d FROM visit_record_v", conn,
    ).iloc[0]['d']
    db_max_dt = pd.to_datetime(db_max) if db_max else pd.Timestamp.now()

    # 3. 拿这批服务商的全部拜访
    codes = s666['客户编码'].astype(str).tolist()
    codes_in = "','".join(codes)
    visits = pd.read_sql(f"""
        SELECT 客户编码,
               拜访时间,
               _打卡方,
               打卡人姓名
          FROM visit_record_v
         WHERE 客户编码 IN ('{codes_in}')
           AND _打卡异常无效 = 0 AND _真异常打卡 = 0
    """, conn)
    if not visits.empty:
        visits['拜访时间'] = pd.to_datetime(visits['拜访时间'])
        visits['_是大华'] = visits['_打卡方'].astype(str).str.contains('大华', na=False)

    # 4. 逐服务商算 2 周 / 4 周窗口
    rows = []
    for _, sp in s666.iterrows():
        code = str(sp['客户编码'])
        t0 = sp['最近666时间']
        t_2w = t0 + pd.Timedelta(days=14)
        t_4w = t0 + pd.Timedelta(days=28)
        win_2w_complete = t_2w <= db_max_dt
        win_4w_complete = t_4w <= db_max_dt

        sp_v = visits[visits['客户编码'] == code] if not visits.empty else pd.DataFrame()

        v_2w = sp_v[(sp_v['拜访时间'] >= t0) & (sp_v['拜访时间'] <= t_2w)] if not sp_v.empty else pd.DataFrame()
        v_4w = sp_v[(sp_v['拜访时间'] >= t0) & (sp_v['拜访时间'] <= t_4w)] if not sp_v.empty else pd.DataFrame()

        v_2w_dh = v_2w[v_2w['_是大华']] if not v_2w.empty else pd.DataFrame()
        v_2w_ag = v_2w[~v_2w['_是大华']] if not v_2w.empty else pd.DataFrame()
        v_4w_dh = v_4w[v_4w['_是大华']] if not v_4w.empty else pd.DataFrame()
        v_4w_ag = v_4w[~v_4w['_是大华']] if not v_4w.empty else pd.DataFrame()

        rows.append({
            '客户编码': code,
            '客户名称': sp['客户名称'],
            '区县': sp['区县'],
            '签约代理商': sp['签约代理商'] or '',
            '最近666时间': t0.strftime('%Y-%m-%d'),
            '中奖次数': int(sp['中奖次数']),
            '2周大华访问': len(v_2w_dh),
            '2周大华_业务员数': v_2w_dh['打卡人姓名'].nunique() if not v_2w_dh.empty else 0,
            '2周代理商访问': len(v_2w_ag),
            '2周代理商_业务员数': v_2w_ag['打卡人姓名'].nunique() if not v_2w_ag.empty else 0,
            '4周大华访问': len(v_4w_dh),
            '4周大华_业务员数': v_4w_dh['打卡人姓名'].nunique() if not v_4w_dh.empty else 0,
            '4周代理商访问': len(v_4w_ag),
            '4周代理商_业务员数': v_4w_ag['打卡人姓名'].nunique() if not v_4w_ag.empty else 0,
            '2周窗口完整': win_2w_complete,
            '4周窗口完整': win_4w_complete,
        })

    df = pd.DataFrame(rows).sort_values('最近666时间', ascending=False).reset_index(drop=True)
    n_total = len(df)

    # 仅看窗口已完整的(否则覆盖率不公平)
    df_2w = df[df['2周窗口完整']]
    df_4w = df[df['4周窗口完整']]

    n_2w_total = len(df_2w)
    n_4w_total = len(df_4w)
    n_2w_dh = int((df_2w['2周大华访问'] > 0).sum())
    n_2w_ag = int((df_2w['2周代理商访问'] > 0).sum())
    n_2w_any = int(((df_2w['2周大华访问'] + df_2w['2周代理商访问']) > 0).sum())
    n_4w_dh = int((df_4w['4周大华访问'] > 0).sum())
    n_4w_ag = int((df_4w['4周代理商访问'] > 0).sum())
    n_4w_any = int(((df_4w['4周大华访问'] + df_4w['4周代理商访问']) > 0).sum())

    return {
        '总数': n_total,
        '2周窗口已完整数': n_2w_total,
        '4周窗口已完整数': n_4w_total,
        '2周_大华': n_2w_dh,
        '2周_大华_pct': round(n_2w_dh / n_2w_total * 100, 1) if n_2w_total else None,
        '2周_代理商': n_2w_ag,
        '2周_代理商_pct': round(n_2w_ag / n_2w_total * 100, 1) if n_2w_total else None,
        '2周_任一': n_2w_any,
        '2周_任一_pct': round(n_2w_any / n_2w_total * 100, 1) if n_2w_total else None,
        '4周_大华': n_4w_dh,
        '4周_大华_pct': round(n_4w_dh / n_4w_total * 100, 1) if n_4w_total else None,
        '4周_代理商': n_4w_ag,
        '4周_代理商_pct': round(n_4w_ag / n_4w_total * 100, 1) if n_4w_total else None,
        '4周_任一': n_4w_any,
        '4周_任一_pct': round(n_4w_any / n_4w_total * 100, 1) if n_4w_total else None,
        '明细': df.to_dict('records'),
        'DB末日': db_max_dt.strftime('%Y-%m-%d') if pd.notna(db_max_dt) else None,
    }


# ══════════════════════════════════════════════
# Section 4：销售跑动
# ══════════════════════════════════════════════

def _section_sales_visits(
    conn, city, district, dealer, period_start, period_end,
) -> dict:
    v_w, v_p = _visit_filter(city, district, dealer)

    # 业务员清单 + 拜访量
    # 代理商维度：通过 visits.客户编码 → install_redpack.所属一级客户 反查
    if dealer:
        sp_df = pd.read_sql(f"""
            SELECT 打卡人姓名 AS 业务员,
                   MAX(_打卡方) AS 所属,
                   COUNT(DISTINCT v.客户编码) AS 拜访客户数,
                   COUNT(*) AS 总打卡次数
              FROM visit_record_v v
              JOIN (
                  SELECT DISTINCT 上线客户编码 AS 客户编码
                    FROM install_redpack_v
                   WHERE 所属一级客户 = ?
              ) c ON c.客户编码 = v.客户编码
             WHERE {v_w}
               AND 拜访年月 BETWEEN ? AND ?
               AND v.打卡人姓名 IS NOT NULL AND v.打卡人姓名 != ''
             GROUP BY 打卡人姓名
             ORDER BY 总打卡次数 DESC
        """, conn, params=(dealer, *v_p, period_start, period_end))
    else:
        sp_df = pd.read_sql(f"""
            SELECT 打卡人姓名 AS 业务员,
                   MAX(_打卡方) AS 所属,
                   COUNT(DISTINCT 客户编码) AS 拜访客户数,
                   COUNT(*) AS 总打卡次数
              FROM visit_record_v
             WHERE {v_w}
               AND 拜访年月 BETWEEN ? AND ?
               AND 打卡人姓名 IS NOT NULL AND 打卡人姓名 != ''
             GROUP BY 打卡人姓名
             ORDER BY 总打卡次数 DESC
        """, conn, params=(*v_p, period_start, period_end))

    n_dahua = int(sp_df['所属'].str.contains('大华', na=False).sum()) if not sp_df.empty else 0
    n_agent = int(len(sp_df) - n_dahua)
    total_visits = int(sp_df['总打卡次数'].sum()) if not sp_df.empty else 0
    total_clients = int(sp_df['拜访客户数'].sum()) if not sp_df.empty else 0

    # 大华业务员跑动评估（city 级，复用 evaluate_salesperson）
    # 区县/代理商不复用（逻辑要重写），先给空
    sp_eval = []
    if not district and not dealer:
        try:
            import sys
            from pathlib import Path as _P
            sys.path.insert(0, str(_P(__file__).parent))
            from _visit_rfm_eval import overview_all_salespeople
            df = overview_all_salespeople(conn, city, period_start, period_end)
            if not df.empty:
                df['_有效产出'] = df['✅ 救援成功'] + df['🟢 正常']
                df = df.sort_values('_有效产出', ascending=False).head(15)
                sp_eval = df.drop(columns=['_有效产出']).to_dict('records')
        except Exception:
            pass

    # 漏跑名单（仅 city 级，复用 evaluate 已经算好的；区县/代理商手动算）
    missed_top = []
    if not district and not dealer:
        # 从 city 级 evaluate 汇总
        from _visit_rfm_eval import overview_all_salespeople  # 已 import 过
        # 累计漏跑名单从评估结果聚合
        # 这里先简单查 V3+ 严重漏跑 — 通过 rfm 直接算

    # 分两组:大华 vs 代理商业务员 明细
    if sp_df.empty:
        dahua_list = []
        agent_sp = pd.DataFrame()
    else:
        sp_df['_是大华'] = sp_df['所属'].astype(str).str.contains('大华', na=False)
        dahua_list = sp_df[sp_df['_是大华']].drop(columns=['_是大华']).head(50).to_dict('records')
        agent_sp = sp_df[~sp_df['_是大华']].drop(columns=['_是大华', '所属']).copy()
        # 重命名拜访字段,准备 join 贡献
        agent_sp = agent_sp.rename(columns={
            '拜访客户数': '拜访客户数',
            '总打卡次数': '总拜访次数',
        })

    # 代理商场景:代理商业务员上线贡献 + 拜访明细 合并成综合视图
    dealer_clerk_combined = []
    if dealer:
        contrib_df = pd.read_sql(f"""
            SELECT
              "所属一级客户业务员（固化）" AS 业务员,
              COUNT(DISTINCT 上线客户编码) AS 名下服务商数,
              COUNT(*) AS 当期上线台数,
              ROUND(SUM(产品现有分销价)/10000, 2) AS 当期上线货值_万,
              MAX(上线时间) AS 最近上线
            FROM install_redpack_v
           WHERE 所属一级客户 = ?
             AND 上线年月 BETWEEN ? AND ?
             AND "所属一级客户业务员（固化）" IS NOT NULL
             AND "所属一级客户业务员（固化）" != ''
           GROUP BY 1
        """, conn, params=(dealer, period_start, period_end))

        # 加历史货值(全周期累计)用于背景参考
        if not contrib_df.empty:
            hist_df = pd.read_sql(f"""
                SELECT "所属一级客户业务员（固化）" AS 业务员,
                       COUNT(*) AS 历史上线台数,
                       ROUND(SUM(产品现有分销价)/10000, 2) AS 历史上线货值_万
                  FROM install_redpack_v
                 WHERE 所属一级客户 = ?
                   AND "所属一级客户业务员（固化）" IS NOT NULL
                   AND "所属一级客户业务员（固化）" != ''
                 GROUP BY 1
            """, conn, params=(dealer,))
            contrib_df = contrib_df.merge(hist_df, on='业务员', how='left')

            # 总当期货值,算贡献占比
            total_cur = float(contrib_df['当期上线货值_万'].sum())
            if total_cur > 0:
                contrib_df['当期贡献占比_pct'] = (
                    contrib_df['当期上线货值_万'] / total_cur * 100
                ).round(1)
            else:
                contrib_df['当期贡献占比_pct'] = 0

        # ── 把拜访 + 贡献 OUTER JOIN 成综合视图 ──
        combined = agent_sp.merge(contrib_df, on='业务员', how='outer') \
            if not agent_sp.empty else contrib_df.copy()

        if not combined.empty:
            # 填空 NaN → 0
            for col in ['拜访客户数', '总拜访次数', '名下服务商数', '当期上线台数',
                        '当期上线货值_万', '历史上线台数', '历史上线货值_万',
                        '当期贡献占比_pct']:
                if col not in combined.columns:
                    combined[col] = 0
                combined[col] = combined[col].fillna(0)
                if col in ('拜访客户数', '总拜访次数', '名下服务商数',
                           '当期上线台数', '历史上线台数'):
                    combined[col] = combined[col].astype(int)

            # 派生:产出/拜访比(万元/次)、覆盖差距(名下 vs 拜访)
            combined['产出_拜访比_万'] = combined.apply(
                lambda r: round(r['当期上线货值_万'] / r['总拜访次数'], 2)
                if r['总拜访次数'] > 0 else None,
                axis=1,
            )
            combined['覆盖差'] = combined['名下服务商数'] - combined['拜访客户数']

            # 状态标签
            def _status(r):
                v_cur = r['当期上线货值_万']
                v_visits = r['总拜访次数']
                v_hist = r.get('历史上线货值_万', 0) or 0
                if v_cur == 0 and v_visits > 0:
                    return '⚠️ 拜访无产出'
                if v_cur > 0 and v_visits == 0:
                    return '🤔 有产出无拜访'
                if v_hist > 50 and v_cur < v_hist * 0.3:
                    return '🚨 产出暴跌(vs 历史)'
                if v_cur > 30:
                    return '🌟 主力'
                if v_cur > 0:
                    return '🟢 正常'
                return '💤 静默'
            combined['状态'] = combined.apply(_status, axis=1)

            # 排序:当期上线货值 降序
            combined = combined.sort_values('当期上线货值_万', ascending=False).reset_index(drop=True)

            # 列顺序
            col_order = ['业务员', '状态',
                         '名下服务商数', '拜访客户数', '覆盖差', '总拜访次数',
                         '当期上线台数', '当期上线货值_万', '当期贡献占比_pct',
                         '产出_拜访比_万',
                         '历史上线台数', '历史上线货值_万',
                         '最近上线']
            col_order = [c for c in col_order if c in combined.columns]
            combined = combined[col_order]
            dealer_clerk_combined = combined.to_dict('records')

    # 代理商场景:服务商 × 业务员 跑动覆盖矩阵
    sp_coverage = []
    if dealer:
        cov_df = pd.read_sql(f"""
            SELECT v.客户编码,
                   MAX(v.拜访客户) AS 客户名称,
                   MAX(v.拜访客户区县) AS 区县,
                   SUM(CASE WHEN v._打卡方 LIKE '%大华%' THEN 1 ELSE 0 END) AS 大华拜访次数,
                   COUNT(DISTINCT CASE WHEN v._打卡方 LIKE '%大华%' THEN v.打卡人姓名 END) AS 大华业务员数,
                   SUM(CASE WHEN v._打卡方 LIKE '%大华%' THEN 0 ELSE 1 END) AS 代理商拜访次数,
                   COUNT(DISTINCT CASE WHEN v._打卡方 LIKE '%大华%' THEN NULL ELSE v.打卡人姓名 END) AS 代理商业务员数,
                   MAX(v.拜访时间) AS 最近拜访
              FROM visit_record_v v
              JOIN (
                  SELECT DISTINCT 上线客户编码 AS 客户编码
                    FROM install_redpack_v
                   WHERE 所属一级客户 = ?
              ) c ON c.客户编码 = v.客户编码
             WHERE v._打卡异常无效 = 0 AND v._真异常打卡 = 0
               AND v.拜访年月 BETWEEN ? AND ?
             GROUP BY v.客户编码
             ORDER BY (大华拜访次数 + 代理商拜访次数) DESC
             LIMIT 200
        """, conn, params=(dealer, period_start, period_end))
        sp_coverage = cov_df.to_dict('records')

    return {
        '业务员_大华数': n_dahua,
        '业务员_代理商数': n_agent,
        '总拜访次数': total_visits,
        '总拜访客户数': total_clients,
        '业务员清单_top10': sp_df.head(10).to_dict('records'),
        '大华业务员明细': dahua_list,
        '代理商业务员综合': dealer_clerk_combined,  # 拜访 + 贡献 合并
        '服务商跑动覆盖': sp_coverage,   # 仅代理商级别
        '大华跑动评估_top15': sp_eval,    # 仅城市级
    }


# ══════════════════════════════════════════════
# Section 5：⚔️ 竞品开拓机会
# ══════════════════════════════════════════════

def _section_competitor_top(
    conn, city, district, dealer,
) -> dict:
    """竞品 Top 服务商分布（只看 city/district 维度；代理商维度暂不分）"""
    if dealer:
        return {
            '总数': 0, '总竞品体量_万': 0, '已混卖大华_数': 0, '纯竞品_数': 0,
            '名单_top10_体量': [], '按城市分布': [], '不适用': True,
        }
    sql = "SELECT * FROM competitor_top_provider WHERE 1=1"
    params = []
    if city:
        sql += " AND 城市 = ?"; params.append(city)
    if district:
        sql += " AND 区县 = ?"; params.append(district)
    # 代理商维度：competitor_top 表没有所属一级客户，跳过
    df = pd.read_sql(sql, conn, params=params)

    if df.empty:
        return {
            '总数': 0, '总竞品体量_万': 0, '已混卖大华_数': 0, '纯竞品_数': 0,
            '名单_top10_体量': [], '按城市分布': [], '不适用': bool(dealer),
        }

    total = int(len(df))
    total_vol = float(df['竞品体量_万'].fillna(0).sum())
    n_mixed = int((df['在售大华'] == 1).sum())
    n_pure = total - n_mixed

    top_rows = df.sort_values('竞品体量_万', ascending=False, na_position='last').head(10)
    top_list = top_rows[[
        '客户名称', '城市', '区县', '客户经营品牌', '在售大华',
        '竞品体量_万', '责任人姓名', '责任人角色',
    ]].fillna('').to_dict('records')

    # 仅 city 级才出"按城市分布"
    by_city = []
    if not district and not dealer:
        bc = (df.groupby('城市')
                .agg(客户数=('客户编码', 'count'),
                     竞品体量_万=('竞品体量_万', 'sum'),
                     已混卖大华=('在售大华', 'sum'))
                .reset_index()
                .sort_values('竞品体量_万', ascending=False))
        bc['未合作'] = bc['客户数'] - bc['已混卖大华']
        by_city = bc.to_dict('records')

    return {
        '总数': total,
        '总竞品体量_万': round(total_vol, 1),
        '已混卖大华_数': n_mixed,
        '纯竞品_数': n_pure,
        '名单_top10_体量': top_list,
        '按城市分布': by_city,
        '不适用': bool(dealer),
    }


# ══════════════════════════════════════════════
# Section 6：🆕 渠道健康度（管道进/出水 + 大商动向 + 归因）
#
# 视角：自 2025-01-01 起,看哪些服务商是「初次入场」、哪些「流失」、
#       大商两期对比。回答金先生的 5 个问题:
#         ① 评估期活跃服务商数变化
#         ② 25-01-01 至评估期前未采购、评估期内首次激活的服务商数
#         ③ 流失的服务商名单(基线期活跃但评估期 0)
#         ④ 新增 - 流失 = 净增 / 管道扩张率
#         ⑤ 大商动向(基线期+评估期 合并货值 Top 10,带 状态 + 变化率)
#
# 数据源:install_redpack_v(KPI 口径,服务商唯一识别用「上线客户编码」)
# ══════════════════════════════════════════════

# 基线期起点(用户口径:看自 25 年初以来从未激活过的服务商作为「新增」)
_BASELINE_START = '2025-01'


def _list_provider_period(conn, codes_set, period_start, period_end, limit=None):
    """对一组服务商,取指定期间内的台数+货值+首/末次激活,按货值降序

    limit=None → 全量返回,不截断
    """
    if not codes_set:
        return []
    codes_in = "','".join(str(c) for c in codes_set)
    sql = f"""
        SELECT 上线客户编码 AS 客户编码,
               MAX(上线客户名称) AS 客户名称,
               MAX(上线客户区县) AS 区县,
               MAX(所属一级客户) AS 签约代理商,
               COUNT(*) AS 台数,
               SUM(产品现有分销价) AS 货值,
               MIN(上线时间) AS 首次激活,
               MAX(上线时间) AS 最近激活
          FROM install_redpack_v
         WHERE 上线客户编码 IN ('{codes_in}')
           AND 上线年月 BETWEEN ? AND ?
         GROUP BY 上线客户编码
         ORDER BY 货值 DESC
    """
    params = [period_start, period_end]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    df = pd.read_sql(sql, conn, params=params)
    if df.empty:
        return []
    df['货值_万'] = (df['货值'].fillna(0) / 10000).round(2)
    df['首次激活'] = pd.to_datetime(df['首次激活'], errors='coerce').dt.strftime('%Y-%m-%d')
    df['最近激活'] = pd.to_datetime(df['最近激活'], errors='coerce').dt.strftime('%Y-%m-%d')
    return df.drop(columns=['货值']).fillna('').to_dict('records')


def _big_mover_compare(
    conn, all_codes, eval_start, eval_end, baseline_start, baseline_end,
    top_n=10,
):
    """大商两期对比 Top N(基线 + 评估 合并货值排序),带状态标记 + 变化率"""
    if not all_codes:
        return []
    codes_in = "','".join(str(c) for c in all_codes)
    df = pd.read_sql(f"""
        SELECT 上线客户编码 AS 客户编码,
               MAX(上线客户名称) AS 客户名称,
               MAX(上线客户区县) AS 区县,
               MAX(所属一级客户) AS 签约代理商,
               SUM(CASE WHEN 上线年月 BETWEEN ? AND ?
                        THEN 产品现有分销价 ELSE 0 END) AS 评估期货值,
               SUM(CASE WHEN 上线年月 BETWEEN ? AND ?
                        THEN 产品现有分销价 ELSE 0 END) AS 基线期货值
          FROM install_redpack_v
         WHERE 上线客户编码 IN ('{codes_in}')
         GROUP BY 上线客户编码
    """, conn, params=(eval_start, eval_end, baseline_start, baseline_end))
    if df.empty:
        return []
    df['评估期货值'] = df['评估期货值'].fillna(0)
    df['基线期货值'] = df['基线期货值'].fillna(0)
    df['合并货值'] = df['评估期货值'] + df['基线期货值']
    df = df.sort_values('合并货值', ascending=False).head(top_n).copy()

    def _status(row):
        ev = row['评估期货值']
        bl = row['基线期货值']
        if bl == 0 and ev > 0:
            return '🆕 新晋'
        if ev == 0 and bl > 0:
            return '💀 退场'
        if bl > 0 and ev > bl * 1.5:
            return '⬆️ 上位'
        if bl > 0 and ev < bl * 0.8:
            return '⬇️ 掉队'
        return '➡️ 稳定'

    df['状态'] = df.apply(_status, axis=1)
    df['评估期货值_万'] = (df['评估期货值'] / 10000).round(2)
    df['基线期货值_万'] = (df['基线期货值'] / 10000).round(2)
    df['变化_万'] = ((df['评估期货值'] - df['基线期货值']) / 10000).round(2)

    def _change_rate(row):
        bl = row['基线期货值']
        if bl <= 0:
            return None
        return (row['评估期货值'] - bl) / bl

    df['变化率'] = df.apply(_change_rate, axis=1)
    return df[[
        '状态', '客户名称', '客户编码', '区县', '签约代理商',
        '基线期货值_万', '评估期货值_万', '变化_万', '变化率',
    ]].fillna('').to_dict('records')


def _section_channel_health(
    conn, city, district, dealer, period_start, period_end, cert_only=False,
) -> dict:
    """渠道健康度 — 进/出水 + 大商动向 + 归因穿透

    基线期 = 2025-01 至 评估期起前一月
    评估期 = period_start ~ period_end
    """
    rp_w, rp_p = _redpack_filter(city, district, dealer)

    baseline_end = str(pd.Period(period_start, freq='M') - 1)
    # 如果评估期起点 ≤ 基线期起点,无意义
    if baseline_end < _BASELINE_START:
        return {
            '_disabled': True,
            '_reason': f"评估期起点 {period_start} 太早,基线期({_BASELINE_START} 至评估期前一月)为空",
        }

    # 评估期 / 基线期 活跃服务商集合
    eval_df = pd.read_sql(f"""
        SELECT DISTINCT 上线客户编码
          FROM install_redpack_v
         WHERE {rp_w}
           AND 上线年月 BETWEEN ? AND ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
    """, conn, params=(*rp_p, period_start, period_end))
    eval_set = set(eval_df['上线客户编码'].astype(str))

    baseline_df = pd.read_sql(f"""
        SELECT DISTINCT 上线客户编码
          FROM install_redpack_v
         WHERE {rp_w}
           AND 上线年月 BETWEEN ? AND ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
    """, conn, params=(*rp_p, _BASELINE_START, baseline_end))
    baseline_set = set(baseline_df['上线客户编码'].astype(str))

    # 只看认证 SMB 服务商(渠道客户类型='认证SMB服务商')
    if cert_only:
        cert_set = set(pd.read_sql(
            "SELECT DISTINCT 客户编码 FROM provider_contract "
            "WHERE 客户编码 IS NOT NULL AND 渠道客户类型='认证SMB服务商'",
            conn)['客户编码'].astype(str))
        eval_set &= cert_set
        baseline_set &= cert_set

    new_set = eval_set - baseline_set    # 新增 = 25-01 以来从未激活、评估期首次激活
    lost_set = baseline_set - eval_set   # 流失 = 基线期活跃、评估期 0
    kept_set = eval_set & baseline_set   # 持续活跃

    # 流失名单全量(用户要求,核心关注)+ 新增名单全量
    new_list = _list_provider_period(conn, new_set, period_start, period_end, limit=None)
    lost_list = _list_provider_period(conn, lost_set, _BASELINE_START, baseline_end, limit=None)

    # 流失归因:这些「在本实体下流失」的服务商,在评估期内是否还在「全局」激活?
    #   全局还激活 = 转向其他地区/代理商(管道转移,人没丢)
    #   全局也没激活 = 真停采
    n_truly_lost = 0
    n_drifted = 0
    drifted_list = []
    if lost_set:
        codes_in = "','".join(lost_set)
        global_active = pd.read_sql(f"""
            SELECT DISTINCT 上线客户编码
              FROM install_redpack_v
             WHERE 上线客户编码 IN ('{codes_in}')
               AND 上线年月 BETWEEN ? AND ?
        """, conn, params=(period_start, period_end))
        drifted_set = set(global_active['上线客户编码'].astype(str))
        n_drifted = len(drifted_set)
        n_truly_lost = len(lost_set) - n_drifted

        if drifted_set:
            codes_in2 = "','".join(drifted_set)
            df = pd.read_sql(f"""
                SELECT 上线客户编码 AS 客户编码,
                       MAX(上线客户名称) AS 客户名称,
                       MAX(上线客户地市) AS 现去向地市,
                       MAX(上线客户区县) AS 现去向区县,
                       MAX(所属一级客户) AS 现签约代理商,
                       SUM(产品现有分销价) AS 评估期货值
                  FROM install_redpack_v
                 WHERE 上线客户编码 IN ('{codes_in2}')
                   AND 上线年月 BETWEEN ? AND ?
                 GROUP BY 上线客户编码
                 ORDER BY 评估期货值 DESC
                 LIMIT 30
            """, conn, params=(period_start, period_end))
            if not df.empty:
                df['评估期货值_万'] = (df['评估期货值'].fillna(0) / 10000).round(2)
                df = df.drop(columns=['评估期货值'])
                drifted_list = df.fillna('').to_dict('records')

    # 大商动向(基线 + 评估 合并货值 Top 10)
    big_movers = _big_mover_compare(
        conn, eval_set | baseline_set,
        period_start, period_end,
        _BASELINE_START, baseline_end,
        top_n=10,
    )

    return {
        '_disabled': False,
        '基线期': f"{_BASELINE_START} ~ {baseline_end}",
        '评估期': f"{period_start} ~ {period_end}",
        '评估期活跃数': len(eval_set),
        '基线期活跃数': len(baseline_set),
        '活跃净变化': len(eval_set) - len(baseline_set),
        '新增数': len(new_set),
        '流失数': len(lost_set),
        '持续活跃': len(kept_set),
        '净增': len(new_set) - len(lost_set),
        '管道扩张率': len(new_set) / len(baseline_set) if baseline_set else None,
        '流失_真停采': n_truly_lost,
        '流失_转走': n_drifted,
        '新增名单': new_list,
        '流失名单': lost_list,
        '转走去向_top30': drifted_list,
        '大商动向_top10': big_movers,
    }


# ══════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════

def _section_battlefield(
    conn: sqlite3.Connection,
    city: str,
    dealer: str,
    period_start: str,
    period_end: str,
) -> dict:
    """代理商阵地沙盘 — 仅 dealer 级别。

    数据组装(完整工作流):
    1. **基础列**:代理商旗下所有服务商(install_redpack 历史 + 评估期活跃)
    2. **战略列**:LEFT JOIN dealer_battlefield(每个服务商最多一条战略档案)
    3. **派生统计**:9 宫格 / 攻坚名单 / 流失预警 — 只在已建档的行上算

    所以未建档的服务商也会出现在"待录入名单"里(战略字段为 NULL)。
    """
    if not dealer:
        return {}

    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dealer_battlefield'")
    if cur.fetchone() is None:
        return {'_no_table': True}

    # === 1. 代理商旗下所有服务商 + 战略档案 LEFT JOIN ===
    # 候选服务商 = 历史在该代理商下有过 install_redpack 的所有服务商
    all_providers = pd.read_sql("""
        WITH latest_dealer AS (
            SELECT 上线客户编码 AS code,
                   所属一级客户 AS 所属代理商,
                   上线客户地市 AS 地市_rp,
                   上线客户区县 AS 区县_rp,
                   上线客户名称 AS 名称_rp,
                   上线客户渠道客户类型 AS 客户类型,
                   ROW_NUMBER() OVER (PARTITION BY 上线客户编码 ORDER BY 上线时间 DESC) AS rn
              FROM install_redpack_v
        ),
        agg_so AS (
            SELECT 上线客户编码 AS code,
                   COUNT(*) AS 历史台数,
                   ROUND(SUM(产品现有分销价)/10000, 2) AS 历史货值_万,
                   MAX(上线时间) AS 最近交易
              FROM install_redpack_v
             GROUP BY 上线客户编码
        ),
        cur_so AS (
            SELECT 上线客户编码 AS code,
                   COUNT(*) AS 当期台数,
                   ROUND(SUM(产品现有分销价)/10000, 2) AS 当期货值_万
              FROM install_redpack_v
             WHERE 上线年月 BETWEEN ? AND ?
             GROUP BY 上线客户编码
        ),
        scope AS (
            SELECT DISTINCT 上线客户编码 AS code
              FROM install_redpack_v
             WHERE 所属一级客户 = ?
        )
        SELECT sc.code AS 服务商编码,
               COALESCE(pp.公司名称, ld.名称_rp) AS 服务商名称,
               COALESCE(pp.地市, ld.地市_rp) AS 地市,
               COALESCE(pp.区县, ld.区县_rp) AS 区县,
               ld.所属代理商,
               ld.客户类型,
               COALESCE(ag.历史台数, 0) AS 历史台数,
               COALESCE(ag.历史货值_万, 0) AS 历史货值_万,
               COALESCE(cs.当期台数, 0) AS 当期台数,
               COALESCE(cs.当期货值_万, 0) AS 当期货值_万,
               ag.最近交易,
               bf.类型, bf.归属, bf.价值, bf.趋势,
               bf.主力采购品牌, bf.次要品牌, bf.全年销量_万,
               bf.大华占比_pct, bf.争取目标_万, bf.争取策略, bf.流失风险,
               bf.备注, bf.更新人, bf.更新时间
          FROM scope sc
          LEFT JOIN provider_profile pp ON pp.客户编码 = sc.code
          LEFT JOIN latest_dealer ld ON ld.code = sc.code AND ld.rn = 1
          LEFT JOIN agg_so ag ON ag.code = sc.code
          LEFT JOIN cur_so cs ON cs.code = sc.code
          LEFT JOIN dealer_battlefield bf ON bf.服务商编码 = sc.code
         ORDER BY 历史货值_万 DESC
    """, conn, params=(period_start, period_end, dealer))

    if all_providers.empty:
        return {'_empty': True, '提示': f'代理商「{dealer}」全省暂无任何服务商'}

    # 待录入名单(战略字段为 NULL)
    pending = all_providers[all_providers['类型'].isna()].copy()
    # 已建档名单(有任何战略字段)
    bf = all_providers[all_providers['类型'].notna()].copy()

    # 待录入名单(全部输出,供 data_editor 用)
    editor_cols = ['服务商编码', '服务商名称', '地市', '区县', '客户类型',
                   '历史台数', '历史货值_万', '当期台数', '当期货值_万', '最近交易',
                   '类型', '归属', '价值', '趋势',
                   '主力采购品牌', '次要品牌', '全年销量_万',
                   '大华占比_pct', '争取目标_万', '争取策略', '流失风险',
                   '备注', '更新人', '更新时间']
    editor_cols = [c for c in editor_cols if c in all_providers.columns]
    editor_records = all_providers[editor_cols].to_dict('records')

    if bf.empty:
        return {
            '已建档数': 0,
            '待录入数': len(pending),
            '总服务商数': len(all_providers),
            '_empty_archive': True,
            '编辑表': editor_records,
        }

    # 2. 战场态势统计
    total_n = len(bf)
    n_position = int((bf['类型'] == '阵地').sum())
    n_channel = int((bf['类型'] == '渠道').sum())

    own_dist = bf['归属'].value_counts().to_dict()  # {'我方': n, '敌方': n, '摇摆': n}
    val_dist = bf['价值'].value_counts().to_dict()  # {'A':n, 'B':n, 'C':n}

    total_market = float(bf['全年销量_万'].fillna(0).sum())
    total_target = float(bf['争取目标_万'].fillna(0).sum())
    # 钱包份额加权(按全年销量加权)
    if total_market > 0:
        bf['_share_weight'] = bf['全年销量_万'].fillna(0)
        weighted_share = (bf['大华占比_pct'].fillna(0) * bf['_share_weight']).sum() / total_market
    else:
        weighted_share = None

    # 3. 归属 × 价值 9 宫格
    grid_df = bf.groupby(['归属', '价值'], dropna=False).agg(
        家数=('服务商编码', 'count'),
        全年销量_万=('全年销量_万', 'sum'),
        争取目标_万=('争取目标_万', 'sum'),
    ).reset_index()
    grid = grid_df.to_dict('records')

    # 4. 攻坚优先级名单(摇摆 A → 敌方 A → 摇摆 B)
    bf['_priority'] = bf.apply(
        lambda r: (
            1 if (r['归属'] == '摇摆' and r['价值'] == 'A') else
            2 if (r['归属'] == '敌方' and r['价值'] == 'A') else
            3 if (r['归属'] == '摇摆' and r['价值'] == 'B') else
            4 if (r['归属'] == '敌方' and r['价值'] == 'B') else
            5 if (r['归属'] == '摇摆' and r['价值'] == 'C') else
            6 if (r['归属'] == '敌方' and r['价值'] == 'C') else
            99
        ),
        axis=1,
    )
    attack_df = bf[bf['归属'].isin(['摇摆', '敌方'])].sort_values(
        ['_priority', '争取目标_万'], ascending=[True, False],
    )
    attack_cols = ['服务商编码', '服务商名称', '区县', '类型', '归属', '价值',
                   '主力采购品牌', '全年销量_万', '大华占比_pct', '争取目标_万',
                   '争取策略', '流失风险']
    attack_cols = [c for c in attack_cols if c in attack_df.columns]
    attack_list = attack_df[attack_cols].head(100).to_dict('records')

    # 5. 流失风险预警(我方 + 高风险 或 趋势萎缩)
    risk_df = bf[
        (bf['归属'] == '我方')
        & (bf['流失风险'].isin(['高', '中']) | (bf['趋势'] == '萎缩'))
    ].sort_values('全年销量_万', ascending=False, na_position='last')
    risk_cols = ['服务商编码', '服务商名称', '区县', '类型', '价值', '趋势',
                 '主力采购品牌', '全年销量_万', '大华占比_pct', '流失风险', '备注']
    risk_cols = [c for c in risk_cols if c in risk_df.columns]
    risk_list = risk_df[risk_cols].head(50).to_dict('records')

    # 6. 全档案明细(供完整列表查看)
    detail_cols = ['服务商编码', '服务商名称', '地市', '区县', '类型', '归属', '价值', '趋势',
                   '主力采购品牌', '次要品牌',
                   '全年销量_万', '大华占比_pct', '争取目标_万', '争取策略', '流失风险',
                   '备注', '更新人', '更新时间']
    detail_cols = [c for c in detail_cols if c in bf.columns]
    full_detail = bf[detail_cols].to_dict('records')

    return {
        '已建档数': total_n,
        '待录入数': len(pending),
        '总服务商数': len(all_providers),
        '阵地数': n_position,
        '渠道数': n_channel,
        '归属分布': {k: int(v) for k, v in own_dist.items()},
        '价值分布': {k: int(v) for k, v in val_dist.items()},
        '总全年销量_万': round(total_market, 2),
        '总争取目标_万': round(total_target, 2),
        '钱包份额加权_pct': round(weighted_share, 2) if weighted_share is not None else None,
        '九宫格': grid,
        '攻坚名单_top100': attack_list,
        '流失风险名单': risk_list,
        '档案明细': full_detail,
        '编辑表': editor_records,
    }


def _section_promotion_coverage(
    conn: sqlite3.Connection,
    city: str,
    dealer: str,
    period_start: str,
    period_end: str,
) -> dict:
    """代理商旗下服务商推广会覆盖情况 — 仅 dealer 级别。"""
    if not dealer:
        return {}

    # 1. 旗下活跃服务商(评估期内有红包扫码)
    active = pd.read_sql("""
        SELECT DISTINCT 上线客户编码 AS 编码, 上线客户名称 AS 名称
          FROM install_redpack_v
         WHERE 所属一级客户 = ?
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(dealer, period_start, period_end))
    active_codes = set(active['编码'].astype(str))

    # 2. 旗下所有曾合作过的服务商(全历史)— 作为覆盖率分母候选
    all_codes = pd.read_sql("""
        SELECT DISTINCT 上线客户编码 AS 编码, 上线客户名称 AS 名称
          FROM install_redpack_v
         WHERE 所属一级客户 = ?
    """, conn, params=(dealer,))
    all_codes_set = set(all_codes['编码'].astype(str))

    if not all_codes_set:
        return {'_no_data': True}

    # 3. 推广会:这些服务商参加过的(全历史)+ 评估期内的
    placeholders = ','.join('?' * len(all_codes_set))
    codes_list = list(all_codes_set)
    pm_all = pd.read_sql(f"""
        SELECT 参会客户编码 AS 编码,
               参会客户名称,
               活动名称,
               活动开始时间,
               主办方代理商,
               签到时间 IS NOT NULL AS 是否签到
          FROM promotion_meeting
         WHERE 参会客户编码 IN ({placeholders})
         ORDER BY 活动开始时间 DESC
    """, conn, params=codes_list)

    if pm_all.empty:
        return {
            '旗下活跃服务商数': len(active_codes),
            '旗下全历史服务商数': len(all_codes_set),
            '参与推广会_服务商数': 0,
            '活跃服务商覆盖率': 0,
            '全历史覆盖率': 0,
            '总参会次数': 0,
            '场次列表': [],
            '服务商参会明细': [],
            '未覆盖服务商Top20': [],
        }

    # 4. 参与统计(全历史)
    participated_codes = set(pm_all['编码'].astype(str))
    active_attended = active_codes & participated_codes
    cover_rate_active = len(active_attended) / max(len(active_codes), 1)
    cover_rate_all = len(participated_codes & all_codes_set) / max(len(all_codes_set), 1)

    # 5. 每场会的参与人数
    event_summary = pm_all.groupby(['活动名称', '活动开始时间', '主办方代理商']).agg(
        参会人次=('编码', 'count'),
        签到人数=('是否签到', 'sum'),
    ).reset_index().sort_values('活动开始时间', ascending=False)
    event_summary['活动开始时间'] = pd.to_datetime(
        event_summary['活动开始时间']
    ).dt.strftime('%Y-%m-%d')
    event_summary['签到人数'] = event_summary['签到人数'].astype(int)

    # 6. 服务商参会次数明细
    sp_attend = pm_all.groupby(['编码', '参会客户名称']).agg(
        参会场次=('活动名称', 'count'),
        签到场次=('是否签到', 'sum'),
        最近参会=('活动开始时间', 'max'),
    ).reset_index().sort_values('参会场次', ascending=False)
    sp_attend['签到场次'] = sp_attend['签到场次'].astype(int)
    sp_attend['最近参会'] = pd.to_datetime(sp_attend['最近参会']).dt.strftime('%Y-%m-%d')

    # 7. 未覆盖的活跃服务商(Top 20 按累计货值)
    uncovered_codes = active_codes - participated_codes
    uncovered_top = []
    if uncovered_codes:
        codes_uncov = "','".join(uncovered_codes)
        uncov_df = pd.read_sql(f"""
            SELECT 上线客户编码 AS 编码,
                   MAX(上线客户名称) AS 名称,
                   ROUND(SUM(产品现有分销价)/10000, 2) AS 累计货值_万,
                   COUNT(*) AS 累计台数
              FROM install_redpack_v
             WHERE 上线客户编码 IN ('{codes_uncov}')
             GROUP BY 上线客户编码
             ORDER BY 累计货值_万 DESC
             LIMIT 20
        """, conn)
        uncovered_top = uncov_df.to_dict('records')

    return {
        '旗下活跃服务商数': len(active_codes),
        '旗下全历史服务商数': len(all_codes_set),
        '参与推广会_服务商数': len(active_attended),
        '活跃服务商覆盖率': round(cover_rate_active, 4),
        '全历史覆盖率': round(cover_rate_all, 4),
        '总参会次数': int(pm_all['活动名称'].count()),
        '场次数': int(event_summary['活动名称'].nunique()),
        '场次列表': event_summary.to_dict('records'),
        '服务商参会明细': sp_attend.to_dict('records'),
        '未覆盖服务商Top20': uncovered_top,
    }


def _section_dealer_benchmark(
    conn: sqlite3.Connection,
    city: str,
    dealer: str,
    period_end: str,
) -> dict:
    """代理商业务对标 — 仅在代理商级别(传 dealer)时生效。
    返回:
      - 代理商画像(dealer_sandbox 全字段)
      - 签约目标 vs SI 进度(dealer_si_snapshot 正式口径)
      - 当月 MTD 表现(本月/环比/同比)
      - 历史标杆 vs 当前
      - 同地市横向榜单 + 排名
    """
    if not dealer:
        return {}

    # 1. 代理商画像
    profile = pd.read_sql(
        "SELECT * FROM dealer_sandbox WHERE 客户名称 = ? AND 地市 = ? LIMIT 1",
        conn, params=(dealer, city),
    )
    if profile.empty:
        # 用客户名称模糊找一下
        profile = pd.read_sql(
            "SELECT * FROM dealer_sandbox WHERE 客户名称 = ? LIMIT 1",
            conn, params=(dealer,),
        )
    if profile.empty:
        return {'_no_sandbox': True, '提示': '该代理商不在沙盘表中(dealer_sandbox 共 50 家)'}

    profile_dict = profile.iloc[0].to_dict()
    dealer_code = profile_dict.get('客户编码')

    # 2. SI 实绩：正式代理商 SI 快照。地市不参与数据过滤。
    cur_period = pd.Period(period_end, freq='M')
    year_now = cur_period.year
    year_prev = year_now - 1

    si = pd.read_sql("""
        SELECT * FROM dealer_si_snapshot
         WHERE 客户编码 = ?
           AND date(数据时点) <= date(? || '-01', '+1 month', '-1 day')
         ORDER BY date(数据时点) DESC LIMIT 1
    """, conn, params=(dealer_code, period_end))
    if si.empty:
        si = pd.read_sql("""
            SELECT * FROM dealer_si_snapshot
             WHERE 客户名称 = ?
               AND date(数据时点) <= date(? || '-01', '+1 month', '-1 day')
             ORDER BY date(数据时点) DESC LIMIT 1
        """, conn, params=(dealer, period_end))
    si_row = si.iloc[0].to_dict() if not si.empty else {}
    ytd_si_wan = float(si_row.get('累计业绩达成（返利前）') or 0)
    last_ytd_si_wan = float(si_row.get('同期业绩达成') or 0)
    ytd_providers = int(pd.read_sql("""
        SELECT COUNT(DISTINCT 上线客户编码) AS n FROM install_redpack_v
         WHERE 所属一级客户 = ? AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(dealer, f'{year_now}-01', period_end)).iloc[0]['n'] or 0)

    # 月度 SO：代理商全省出货，不再受筛选地市限制。
    def _pf_so(month_str):
        return float(pd.read_sql("""
            SELECT COALESCE(SUM(最新分销价), 0) AS v FROM product_flow_v
             WHERE 出库客户名称 = ? AND 上线年月 = ?
        """, conn, params=(dealer, month_str)).iloc[0]['v'] or 0)

    cur_month_str = str(cur_period)
    last_month_str = str(cur_period - 1)
    yoy_month_str = str(cur_period - 12)
    cur_month_so_v = _pf_so(cur_month_str)
    last_month_so_v = _pf_so(last_month_str)
    yoy_month_so_v = _pf_so(yoy_month_str)

    # 3. 进度条算法(简化:用全省 SO 节奏代替 SI 节奏,待真 SI 数据接入)
    rhythm_df = pd.read_sql("""
        SELECT 月份, 占比 FROM kpi_rhythm
        WHERE 指标 = '客户SI进度条(返利前)'
          AND 适用范围 = '分销商协议'
          AND 类型 = '续签客户'
          AND 年度 = ?
        ORDER BY 月份
    """, conn, params=(year_now,))
    if not rhythm_df.empty:
        cur_month_n = cur_period.month
        # 进度条节奏 = 截止上月底应达成的占比累计
        completed_pct = rhythm_df[rhythm_df['月份'] <= (cur_month_n - 1)]['占比'].sum()
    else:
        completed_pct = 0

    si_target_wan = float(si_row.get('签约金额') or profile_dict.get('分销签约金额') or 0)
    should_achieve_wan = float(si_row.get('累计任务') or 0) or (
        si_target_wan * completed_pct if si_target_wan > 0 else None)
    progress_rate = (ytd_si_wan / should_achieve_wan) if should_achieve_wan else None
    completion_rate = (ytd_si_wan / si_target_wan) if si_target_wan > 0 else None

    # 4. 同地市横向榜单
    peers = pd.read_sql("""
        SELECT 客户名称, 客户编码, 分销签约金额, 去年安防分销销售规模
          FROM dealer_sandbox
         WHERE 地市 = ?
         ORDER BY 分销签约金额 DESC
    """, conn, params=(city,))

    # 名单仍按注册地市横向比较，但每家实绩均取各自最新正式 SI 快照。
    peer_si = pd.read_sql("""
        WITH ranked AS (
            SELECT 客户编码, "累计业绩达成（返利前）" AS ytd_si,
                   ROW_NUMBER() OVER (PARTITION BY 客户编码 ORDER BY date(数据时点) DESC) AS rn
              FROM dealer_si_snapshot
             WHERE date(数据时点) <= date(? || '-01', '+1 month', '-1 day')
        )
        SELECT 客户编码, ytd_si FROM ranked WHERE rn = 1
    """, conn, params=(period_end,))
    peer_so_map = dict(zip(peer_si['客户编码'].astype(str), peer_si['ytd_si']))

    peers['YTD_SO_万'] = peers['客户编码'].astype(str).map(peer_so_map).fillna(0).round(1)
    peers['完成率'] = (peers['YTD_SO_万'] / peers['分销签约金额']).round(3)
    peers['完成率'] = peers['完成率'].fillna(0)
    peers = peers.sort_values('YTD_SO_万', ascending=False).reset_index(drop=True)
    peers['排名'] = peers.index + 1

    # 当前代理商在 peers 里的位置
    my_rank = None
    if dealer in peers['客户名称'].values:
        my_rank = int(peers[peers['客户名称'] == dealer].iloc[0]['排名'])

    peers_list = peers[['排名', '客户名称', '分销签约金额', 'YTD_SO_万', '完成率']].copy()
    peers_list['当前'] = peers_list['客户名称'].apply(lambda x: '⭐' if x == dealer else '')
    peers_list_records = peers_list.to_dict('records')

    return {
        '画像': profile_dict,
        '签约目标_万': si_target_wan,
        '去年安防分销_万': float(profile_dict.get('去年安防分销销售规模') or 0),
        '去年红包上线_万': float(profile_dict.get('去年安装红包上线金额') or 0),
        'YTD_SO_万': ytd_si_wan,
        '去年同期SO_万': last_ytd_si_wan,
        '累计同比': (ytd_si_wan - last_ytd_si_wan) / last_ytd_si_wan if last_ytd_si_wan > 0 else None,
        '本月_MTD_SO_万': cur_month_so_v / 10000,
        '上月SO_万': last_month_so_v / 10000,
        '本月环比': (cur_month_so_v - last_month_so_v) / last_month_so_v if last_month_so_v > 0 else None,
        '去年同月SO_万': yoy_month_so_v / 10000,
        '本月同比': (cur_month_so_v - yoy_month_so_v) / yoy_month_so_v if yoy_month_so_v > 0 else None,
        'YTD服务商数': ytd_providers,
        '完成率': completion_rate,
        '截止上月应达成占比': completed_pct,
        '应达成_万': should_achieve_wan,
        '进度达成率': progress_rate,
        'SI数据时点': si_row.get('数据时点'),
        '本月': cur_month_str,
        '同地市榜单': peers_list_records,
        '同地市排名': my_rank,
        '同地市总数': len(peers),
    }


def gather_panorama(
    conn: sqlite3.Connection,
    city: str,
    *,
    district: str = None,
    dealer: str = None,
    period_start: str = '2026-01',
    period_end: str = '2026-04',
) -> dict:
    """统一全景查询入口。3 种场景：
       - city 级：只传 city
       - 区县级：city + district
       - 代理商级：city + dealer
    """
    result = {
        '_meta': {
            '实体': _entity_label(city, district, dealer),
            'city': city,
            'district': district,
            'dealer': dealer,
            'period_start': period_start,
            'period_end': period_end,
        },
        'so_growth': _section_so_growth(conn, city, district, dealer, period_start, period_end),
        'channel_health': _section_channel_health(conn, city, district, dealer, period_start, period_end),
        'channel_health_cert': _section_channel_health(conn, city, district, dealer, period_start, period_end, cert_only=True),
        'provider_quality': _section_provider_quality(conn, city, district, dealer, period_start, period_end),
        'authorized_provider': authorized_provider_metrics(
            conn, city=city, district=district, dealer=dealer,
        ),
        'redpack': _section_redpack(conn, city, district, dealer, period_start, period_end),
        'sales_visits': _section_sales_visits(conn, city, district, dealer, period_start, period_end),
        'competitor_top': _section_competitor_top(conn, city, district, dealer),
    }
    if not district and not dealer:
        result['authorized_provider_breakdown'] = {
            '层级': '区县',
            '明细': authorized_provider_by_district(conn, city),
        }
    # 代理商级别才有 业务对标 + 推广会覆盖 + 阵地沙盘
    if dealer:
        result['dealer_benchmark'] = _section_dealer_benchmark(conn, city, dealer, period_end)
        result['promotion_coverage'] = _section_promotion_coverage(
            conn, city, dealer, period_start, period_end,
        )
        result['battlefield'] = _section_battlefield(
            conn, city, dealer, period_start, period_end,
        )
    return result


# ══════════════════════════════════════════════
# 工具：列出可选 实体
# ══════════════════════════════════════════════

def list_districts(conn, city: str) -> list[str]:
    df = pd.read_sql("""
        SELECT DISTINCT 上线区县 FROM product_flow_v
         WHERE 上线城市 = ? AND 上线区县 IS NOT NULL AND 上线区县 != ''
         ORDER BY 上线区县
    """, conn, params=(city,))
    return df['上线区县'].tolist()


def list_dealers(conn, city: str) -> list[str]:
    df = pd.read_sql("""
        SELECT 客户名称
          FROM dealer_sandbox
         WHERE 地市 = ? AND 客户名称 IS NOT NULL AND 客户名称 != ''
         ORDER BY COALESCE(分销签约金额, 0) DESC, 客户名称
    """, conn, params=(city,))
    return df['客户名称'].tolist()
