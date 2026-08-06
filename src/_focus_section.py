"""产品专项数据穿透 — 给定专项,跨 5 个数据源查所有维度

数据源:
  - product_flow_v   (出货,全量感知)
  - install_redpack_v (上线 + 红包扫码)
  - visit_record_v   (跑动)
  - promotion_meeting(推广会)
  - product_focus    (专项映射,物料号 → 专项)

主键关联:物料号(install_redpack/product_flow 都有,跟 product_focus 一致)
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Optional

import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from _metrics_rfm import tier_of  # noqa: E402


def shift_period(start: str, end: str, months: int) -> tuple:
    s = pd.Period(start, freq='M') + months
    e = pd.Period(end, freq='M') + months
    return str(s), str(e)


def n_months(start: str, end: str) -> int:
    return (pd.Period(end, freq='M') - pd.Period(start, freq='M')).n + 1


def calc_yoy(cur, base):
    if base and base > 0:
        return (cur - base) / base
    return None


def gather_focus_data(
    conn: sqlite3.Connection,
    focus: str,                  # '夜视王' / '无线' / '场景化'
    period_start: str,
    period_end: str,
    city: str = None,
    dealer: str = None,
) -> dict:
    """专项多源数据穿透"""
    yoy_start, yoy_end = shift_period(period_start, period_end, -12)
    n = n_months(period_start, period_end)
    mom_start, mom_end = shift_period(period_start, period_end, -n)

    # 实体过滤参数(地市 / 代理商)
    pf_w, pf_p = [], []
    rp_w, rp_p = [], []
    v_w, v_p = [], []
    if city:
        pf_w.append('上线城市 = ?'); pf_p.append(city)
        rp_w.append('上线客户地市 = ?'); rp_p.append(city)
        v_w.append('拜访客户城市 = ?'); v_p.append(city)
    if dealer:
        pf_w.append('出库客户名称 = ?'); pf_p.append(dealer)
        rp_w.append('所属一级客户 = ?'); rp_p.append(dealer)

    pf_where = ' AND '.join(pf_w) if pf_w else '1=1'
    rp_where = ' AND '.join(rp_w) if rp_w else '1=1'
    v_where = ' AND '.join(v_w) if v_w else '1=1'

    # 专项 SKU 集合
    sku_df = pd.read_sql(
        "SELECT 物料号, 规格型号, 产品类别, 产品状态 FROM product_focus WHERE 专项 = ?",
        conn, params=(focus,),
    )
    n_sku = len(sku_df)
    n_active_sku = int((sku_df['产品状态'] == '正常销售').sum())

    out = {
        '_meta': {
            'focus': focus,
            'period_start': period_start, 'period_end': period_end,
            'yoy_start': yoy_start, 'yoy_end': yoy_end,
            'mom_start': mom_start, 'mom_end': mom_end,
            'n_months': n,
            'city': city or '全省',
            'dealer': dealer or '全部',
            'sku_总数': n_sku,
            'sku_在售': n_active_sku,
            'generated_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
        },
    }

    # ─── 1. 总览 KPI ─────────
    # 出货(product_flow,全量感知)
    pf_cur = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数,
               COALESCE(SUM(最新分销价), 0) AS 货值
          FROM product_flow_v pf
          JOIN product_focus fc ON fc.物料号 = pf.物料号
         WHERE fc.专项 = ?
           AND 上线年月 BETWEEN ? AND ?
           AND {pf_where}
    """, conn, params=(focus, period_start, period_end, *pf_p)).iloc[0]

    pf_yoy = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数, COALESCE(SUM(最新分销价), 0) AS 货值
          FROM product_flow_v pf
          JOIN product_focus fc ON fc.物料号 = pf.物料号
         WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ? AND {pf_where}
    """, conn, params=(focus, yoy_start, yoy_end, *pf_p)).iloc[0]

    pf_mom = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数, COALESCE(SUM(最新分销价), 0) AS 货值
          FROM product_flow_v pf
          JOIN product_focus fc ON fc.物料号 = pf.物料号
         WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ? AND {pf_where}
    """, conn, params=(focus, mom_start, mom_end, *pf_p)).iloc[0]

    # 上线(install_redpack,红包扫码)
    rp_cur = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数,
               COALESCE(SUM(产品现有分销价), 0) AS 货值,
               COALESCE(SUM(中奖金额), 0) AS 红包额,
               COUNT(DISTINCT 上线客户编码) AS 服务商数
          FROM install_redpack_v ir
          JOIN product_focus fc ON fc.物料号 = ir.物料号
         WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ? AND {rp_where}
    """, conn, params=(focus, period_start, period_end, *rp_p)).iloc[0]

    rp_yoy = pd.read_sql(f"""
        SELECT COALESCE(SUM(产品现有分销价), 0) AS 货值
          FROM install_redpack_v ir
          JOIN product_focus fc ON fc.物料号 = ir.物料号
         WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ? AND {rp_where}
    """, conn, params=(focus, yoy_start, yoy_end, *rp_p)).iloc[0]

    pf_amt = float(pf_cur['货值'])
    rp_amt = float(rp_cur['货值'])

    # 年度目标(focus_target 表)— 按 城市 或 浙江合计
    year = int(period_start.split('-')[0])
    target_city = city if city else '浙江合计'
    target_row = pd.read_sql(f"""
        SELECT 目标台数, 目标货值_万
          FROM focus_target
         WHERE 专项 = ? AND 年度 = ? AND 地市 = ?
        LIMIT 1
    """, conn, params=(focus, year, target_city))
    if not target_row.empty:
        t = target_row.iloc[0]
        target_n = int(t['目标台数']) if pd.notna(t['目标台数']) else None
        target_amt = float(t['目标货值_万']) if pd.notna(t['目标货值_万']) else None
    else:
        target_n = target_amt = None

    # YTD(年初至评估期末)出货 — 用于完成率
    ytd_pf = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数,
               COALESCE(SUM(最新分销价)/10000, 0) AS 货值_万
          FROM product_flow_v pf
          JOIN product_focus fc ON fc.物料号 = pf.物料号
         WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ? AND {pf_where}
    """, conn, params=(focus, f'{year}-01', period_end, *pf_p)).iloc[0]
    ytd_n = int(ytd_pf['台数'])
    ytd_amt = float(ytd_pf['货值_万'])

    out['kpi_overview'] = {
        '出货_台数': int(pf_cur['台数']),
        '出货_万': round(pf_amt / 10000, 2),
        '出货_同比': calc_yoy(pf_amt, float(pf_yoy['货值'])),
        '出货_环比': calc_yoy(pf_amt, float(pf_mom['货值'])),
        '上线_台数': int(rp_cur['台数']),
        '上线_万': round(rp_amt / 10000, 2),
        '上线_同比': calc_yoy(rp_amt, float(rp_yoy['货值'])),
        '绑定率': round(rp_amt / pf_amt, 4) if pf_amt > 0 else None,
        '红包额_元': round(float(rp_cur['红包额']), 2),
        '红包ROI': round(float(rp_cur['红包额']) / rp_amt, 4) if rp_amt > 0 else None,
        '活跃服务商数': int(rp_cur['服务商数']),
        '_target_城市': target_city,
        '_target_年度': year,
        '目标_台数': target_n,
        '目标_货值_万': target_amt,
        'YTD_台数': ytd_n,
        'YTD_货值_万': ytd_amt,
        '完成率_台数': round(ytd_n / target_n, 4) if target_n and target_n > 0 else None,
        '完成率_货值': round(ytd_amt / target_amt, 4) if target_amt and target_amt > 0 else None,
    }

    # ─── 2. 月度走势 ─────────
    trend = pd.read_sql(f"""
        SELECT 上线年月 AS 月份,
               COUNT(*) AS 出货台数,
               ROUND(SUM(最新分销价)/10000, 2) AS 出货货值_万
          FROM product_flow_v pf
          JOIN product_focus fc ON fc.物料号 = pf.物料号
         WHERE fc.专项 = ? AND 上线年月 IS NOT NULL AND {pf_where}
         GROUP BY 上线年月
         ORDER BY 上线年月 DESC LIMIT 12
    """, conn, params=(focus, *pf_p))
    trend = trend.sort_values('月份')

    rp_trend = pd.read_sql(f"""
        SELECT 上线年月 AS 月份,
               COUNT(*) AS 上线台数,
               ROUND(SUM(产品现有分销价)/10000, 2) AS 上线货值_万
          FROM install_redpack_v ir
          JOIN product_focus fc ON fc.物料号 = ir.物料号
         WHERE fc.专项 = ? AND 上线年月 IS NOT NULL AND {rp_where}
         GROUP BY 上线年月
         ORDER BY 上线年月 DESC LIMIT 12
    """, conn, params=(focus, *rp_p))
    rp_trend = rp_trend.sort_values('月份')

    merged_trend = trend.merge(rp_trend, on='月份', how='outer').fillna(0).sort_values('月份')
    out['monthly_trend'] = merged_trend.to_dict('records')

    # ─── 3. 11 地市分布(只在 全省 视图下显示) ─────────
    city_dist = []
    if not city:
        city_df = pd.read_sql(f"""
            SELECT pf.上线城市 AS 地市,
                   COUNT(*) AS 出货台数,
                   ROUND(SUM(pf.最新分销价)/10000, 2) AS 出货货值_万
              FROM product_flow_v pf
              JOIN product_focus fc ON fc.物料号 = pf.物料号
             WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ?
               AND pf.上线城市 IS NOT NULL AND pf.上线城市 != ''
             GROUP BY pf.上线城市
             ORDER BY 出货货值_万 DESC
        """, conn, params=(focus, period_start, period_end))

        rp_city = pd.read_sql(f"""
            SELECT ir.上线客户地市 AS 地市,
                   COUNT(*) AS 上线台数,
                   ROUND(SUM(ir.产品现有分销价)/10000, 2) AS 上线货值_万,
                   COUNT(DISTINCT ir.上线客户编码) AS 服务商数
              FROM install_redpack_v ir
              JOIN product_focus fc ON fc.物料号 = ir.物料号
             WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ?
               AND ir.上线客户地市 IS NOT NULL AND ir.上线客户地市 != ''
             GROUP BY ir.上线客户地市
        """, conn, params=(focus, period_start, period_end))

        city_merged = city_df.merge(rp_city, on='地市', how='outer').fillna(0)
        city_merged['绑定率'] = city_merged.apply(
            lambda r: round(r['上线货值_万'] / r['出货货值_万'], 4)
            if r['出货货值_万'] > 0 else None, axis=1,
        )

        # 加 11 地市 YTD + 目标 + 完成率
        city_ytd = pd.read_sql(f"""
            SELECT pf.上线城市 AS 地市,
                   COUNT(*) AS YTD台数,
                   ROUND(SUM(pf.最新分销价)/10000, 2) AS YTD货值_万
              FROM product_flow_v pf
              JOIN product_focus fc ON fc.物料号 = pf.物料号
             WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ?
               AND pf.上线城市 IS NOT NULL AND pf.上线城市 != ''
             GROUP BY pf.上线城市
        """, conn, params=(focus, f'{year}-01', period_end))

        city_target = pd.read_sql(f"""
            SELECT 地市, 目标台数, 目标货值_万
              FROM focus_target
             WHERE 专项 = ? AND 年度 = ? AND 地市 != '浙江合计'
        """, conn, params=(focus, year))

        city_merged = city_merged.merge(city_ytd, on='地市', how='outer').fillna(0)
        city_merged = city_merged.merge(city_target, on='地市', how='left')
        city_merged['完成率_台数'] = city_merged.apply(
            lambda r: round(r['YTD台数'] / r['目标台数'], 4)
            if pd.notna(r.get('目标台数')) and r.get('目标台数', 0) > 0 else None,
            axis=1,
        )
        city_merged['完成率_货值'] = city_merged.apply(
            lambda r: round(r['YTD货值_万'] / r['目标货值_万'], 4)
            if pd.notna(r.get('目标货值_万')) and r.get('目标货值_万', 0) > 0 else None,
            axis=1,
        )
        city_merged['目标台数'] = city_merged['目标台数'].fillna(0).astype(int)
        city_merged['目标货值_万'] = city_merged['目标货值_万'].fillna(0)
        city_dist = city_merged.sort_values('出货货值_万', ascending=False).to_dict('records')
    out['city_breakdown'] = city_dist

    # ─── 4. Top 代理商分布 ─────────
    dealer_df = pd.read_sql(f"""
        SELECT pf.出库客户名称 AS 代理商,
               COUNT(*) AS 出货台数,
               ROUND(SUM(pf.最新分销价)/10000, 2) AS 出货货值_万
          FROM product_flow_v pf
          JOIN product_focus fc ON fc.物料号 = pf.物料号
         WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ? AND {pf_where}
           AND pf.出库客户名称 IS NOT NULL AND pf.出库客户名称 != ''
         GROUP BY pf.出库客户名称
         ORDER BY 出货货值_万 DESC
         LIMIT 20
    """, conn, params=(focus, period_start, period_end, *pf_p))
    out['dealer_top'] = dealer_df.to_dict('records')

    # ─── 5. Top 服务商(install_redpack 上线) ─────────
    provider_df = pd.read_sql(f"""
        SELECT ir.上线客户编码 AS 客户编码,
               MAX(ir.上线客户名称) AS 客户名称,
               MAX(ir.上线客户区县) AS 区县,
               MAX(ir.所属一级客户) AS 签约代理商,
               MAX(ir.上线客户渠道客户类型) AS 客户类型,
               COUNT(*) AS 上线台数,
               ROUND(SUM(ir.产品现有分销价)/10000, 2) AS 上线货值_万,
               ROUND(SUM(ir.中奖金额), 2) AS 红包额_元
          FROM install_redpack_v ir
          JOIN product_focus fc ON fc.物料号 = ir.物料号
         WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ? AND {rp_where}
         GROUP BY ir.上线客户编码
         ORDER BY 上线货值_万 DESC
         LIMIT 100
    """, conn, params=(focus, period_start, period_end, *rp_p))
    out['provider_top100'] = provider_df.to_dict('records')

    # ─── 6. 产品 SKU 排行(看哪个型号卖得多) ─────────
    sku_so = pd.read_sql(f"""
        SELECT fc.规格型号 AS 内部型号,
               fc.物料号,
               fc.产品类别,
               fc.产品状态,
               COUNT(*) AS 上线台数,
               ROUND(SUM(ir.产品现有分销价)/10000, 2) AS 上线货值_万,
               ROUND(AVG(ir.产品现有分销价), 0) AS 均价
          FROM install_redpack_v ir
          JOIN product_focus fc ON fc.物料号 = ir.物料号
         WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ? AND {rp_where}
         GROUP BY fc.物料号
         ORDER BY 上线货值_万 DESC
         LIMIT 50
    """, conn, params=(focus, period_start, period_end, *rp_p))
    out['sku_top50'] = sku_so.to_dict('records')

    # 全部 SKU 状态分布
    out['sku_status'] = sku_df['产品状态'].value_counts().to_dict() if not sku_df.empty else {}

    # ─── 7. 跑动覆盖(卖该专项的服务商中,有多少被业务员拜访) ─────────
    if not provider_df.empty:
        codes = provider_df['客户编码'].astype(str).tolist()
        codes_in = "','".join(codes)
        visit_df = pd.read_sql(f"""
            SELECT v.客户编码,
                   v._打卡方,
                   COUNT(*) AS 拜访次数,
                   COUNT(DISTINCT v.打卡人姓名) AS 业务员数
              FROM visit_record_v v
             WHERE v.客户编码 IN ('{codes_in}')
               AND v._打卡异常无效 = 0 AND v._真异常打卡 = 0
               AND v.拜访年月 BETWEEN ? AND ?
             GROUP BY v.客户编码, v._打卡方
        """, conn, params=(period_start, period_end))

        if not visit_df.empty:
            dahua_visited = set(visit_df[visit_df['_打卡方'].astype(str).str.contains('大华')]['客户编码'].astype(str))
            agent_visited = set(visit_df[~visit_df['_打卡方'].astype(str).str.contains('大华', na=False)]['客户编码'].astype(str))
        else:
            dahua_visited = set()
            agent_visited = set()

        total = len(provider_df)
        n_dahua = sum(1 for c in codes if c in dahua_visited)
        n_agent = sum(1 for c in codes if c in agent_visited)
        n_any = sum(1 for c in codes if c in (dahua_visited | agent_visited))
        out['visit_coverage'] = {
            '总服务商数': total,
            '大华业务员到访数': n_dahua,
            '大华业务员到访率': round(n_dahua / total, 4) if total else None,
            '代理商业务员到访数': n_agent,
            '代理商业务员到访率': round(n_agent / total, 4) if total else None,
            '任一到访数': n_any,
            '任一到访率': round(n_any / total, 4) if total else None,
            '未拜访数': total - n_any,
        }
    else:
        out['visit_coverage'] = None

    # ─── 8. 推广会覆盖 ─────────
    if not provider_df.empty:
        codes = provider_df['客户编码'].astype(str).tolist()
        codes_in = "','".join(codes)
        pm_df = pd.read_sql(f"""
            SELECT 参会客户编码 AS 客户编码, COUNT(*) AS 参会次数
              FROM promotion_meeting
             WHERE 参会客户编码 IN ('{codes_in}')
             GROUP BY 参会客户编码
        """, conn)
        n_pm = len(pm_df)
        total = len(provider_df)
        out['promotion_coverage'] = {
            '总服务商数': total,
            '参加过推广会数': n_pm,
            '覆盖率': round(n_pm / total, 4) if total else None,
        }
    else:
        out['promotion_coverage'] = None

    # ─── 9. 服务商等级分布(卖该专项的) ─────────
    if not provider_df.empty:
        prov_codes = provider_df['客户编码'].astype(str).tolist()
        codes_in2 = "','".join(prov_codes)
        cum = pd.read_sql(f"""
            SELECT 上线客户编码 AS 客户编码,
                   COALESCE(SUM(产品现有分销价), 0) AS 累计货值
              FROM install_redpack_v
             WHERE 上线客户编码 IN ('{codes_in2}')
             GROUP BY 上线客户编码
        """, conn)
        if not cum.empty:
            cum['等级'] = cum['累计货值'].fillna(0).apply(tier_of)
            tier_dist = cum['等级'].value_counts().to_dict()
        else:
            tier_dist = {}
        out['provider_tier'] = tier_dist
    else:
        out['provider_tier'] = {}

    # ─── 9.5. 月度节奏 vs 实绩(基于 focus_rhythm + focus_target) ─────────
    # 地市目标 = focus_target.目标台数(全省=浙江合计 / 地市=对应行)
    # 月度目标 = 地市目标 × 节奏%(focus_rhythm.占比_pct)
    # 月度实绩 = product_flow_v 该年该月该专项实际出货(自动应用地市过滤)
    try:
        rhythm = pd.read_sql("""
            SELECT 月份, 占比_pct, 全省目标
              FROM focus_rhythm
             WHERE 专项 = ? AND 年度 = ?
             ORDER BY 月份
        """, conn, params=(focus, year))

        if not rhythm.empty and target_n and target_n > 0:
            m_actual = pd.read_sql(f"""
                SELECT CAST(SUBSTR(pf.上线年月, 6, 2) AS INTEGER) AS 月份,
                       COUNT(*) AS 实绩台数
                  FROM product_flow_v pf
                  JOIN product_focus fc ON fc.物料号 = pf.物料号
                 WHERE fc.专项 = ?
                   AND pf.上线年月 BETWEEN ? AND ?
                   AND {pf_where}
                 GROUP BY 1
            """, conn, params=(focus, f'{year}-01', f'{year}-12', *pf_p))

            rhythm['月度目标'] = (target_n * rhythm['占比_pct'] / 100).round().astype(int)
            rhythm = rhythm.merge(m_actual, on='月份', how='left')
            rhythm['实绩台数'] = rhythm['实绩台数'].fillna(0).astype(int)
            rhythm['月度完成率'] = rhythm.apply(
                lambda r: round(r['实绩台数'] / r['月度目标'], 4) if r['月度目标'] > 0 else None,
                axis=1,
            )
            rhythm['累计目标'] = rhythm['月度目标'].cumsum().astype(int)
            rhythm['累计实绩'] = rhythm['实绩台数'].cumsum().astype(int)
            rhythm['累计完成率'] = rhythm.apply(
                lambda r: round(r['累计实绩'] / r['累计目标'], 4) if r['累计目标'] > 0 else None,
                axis=1,
            )
            out['monthly_rhythm'] = rhythm.to_dict('records')
        else:
            out['monthly_rhythm'] = []
    except Exception:
        # focus_rhythm 表可能尚未建
        out['monthly_rhythm'] = []

    # ─── 10. 业务员维度(地市级目标分解,基于 focus_target_salesperson) ─────────
    # 实绩口径:估算值 = 地市 YTD 总台数 × 占比%(总负责口径 = 全城市)
    #   - 是否地市总负责='Y'  → YTD台数_估算 = 地市 YTD 总台数
    #   - 是否地市总负责='N' + 有占比 → YTD台数_估算 = 地市 YTD × 占比/100
    #   - 精确口径需「大华业务员 ↔ 一级代理商」映射表,后续补充
    sp_sql = """
        WITH ytd AS (
            SELECT pf.上线城市 AS 地市, COUNT(*) AS YTD台数
              FROM product_flow_v pf
              JOIN product_focus fc ON fc.物料号 = pf.物料号
             WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ?
               AND pf.上线城市 IS NOT NULL AND pf.上线城市 != ''
             GROUP BY 1
        ),
        per AS (
            SELECT pf.上线城市 AS 地市, COUNT(*) AS 评估期台数
              FROM product_flow_v pf
              JOIN product_focus fc ON fc.物料号 = pf.物料号
             WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ?
               AND pf.上线城市 IS NOT NULL AND pf.上线城市 != ''
             GROUP BY 1
        )
        SELECT sp.地市, sp.业务员, sp.是否地市总负责, sp.占比_pct, sp.目标台数,
               COALESCE(ytd.YTD台数, 0)   AS 地市YTD,
               COALESCE(per.评估期台数, 0) AS 地市评估期,
               CASE
                   WHEN sp.是否地市总负责 = 'Y' THEN COALESCE(ytd.YTD台数, 0)
                   WHEN sp.占比_pct IS NOT NULL
                       THEN CAST(ROUND(COALESCE(ytd.YTD台数, 0) * sp.占比_pct / 100.0) AS INTEGER)
                   ELSE 0
               END AS YTD台数_估算
          FROM focus_target_salesperson sp
          LEFT JOIN ytd ON ytd.地市 = sp.地市
          LEFT JOIN per ON per.地市 = sp.地市
         WHERE sp.专项 = ? AND sp.年度 = ?
    """
    sp_params = [focus, f'{year}-01', period_end,
                 focus, period_start, period_end,
                 focus, year]
    if city:
        sp_sql += " AND sp.地市 = ?"
        sp_params.append(city)
    sp_sql += " ORDER BY sp.地市, CASE WHEN sp.是否地市总负责='Y' THEN 0 ELSE 1 END, sp.业务员"

    try:
        sp_df = pd.read_sql(sp_sql, conn, params=sp_params)
        if not sp_df.empty:
            sp_df['完成率_估算'] = sp_df.apply(
                lambda r: round(r['YTD台数_估算'] / r['目标台数'], 4)
                if r['目标台数'] and r['目标台数'] > 0 else None,
                axis=1,
            )
            out['salesperson_breakdown'] = sp_df.to_dict('records')
        else:
            out['salesperson_breakdown'] = []
    except Exception:
        # focus_target_salesperson 表可能还未建
        out['salesperson_breakdown'] = []

    return out
