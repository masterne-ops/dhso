"""SMB 主管周报 — 数据汇总(基于 360 健康度月报快照 + 实时数据)

数据源映射:
  - city_snapshot_p3     → 11 地市 KPI(SI/SO/服务商)月报快照(数据时点)
  - dealer_snapshot_p4   → 49 代理商 KPI(SI/SO/下沉/进销存/应收)月报快照
  - provider_target      → 服务商签约/激活目标(年度,地市级)
  - install_redpack      → 服务商激活实时(过滤期间)
  - product_flow         → 出货实时(过滤期间)
  - focus_target/rhythm  → 三大专项目标
  - kpi_targets/rhythm   → 省区 SO 目标 + 节奏
  - dahua_dealer_owner   → 业务员归属
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd


def to_pct(v, plus_sign=False, decimals=1):
    if v is None or pd.isna(v):
        return '—'
    sign = '+' if v >= 0 and plus_sign else ''
    return f"{sign}{v * 100:.{decimals}f}%"


def fmt_num(v, suffix='', decimals=1):
    if v is None or pd.isna(v):
        return '—'
    return f"{v:,.{decimals}f}{suffix}"


def gather_weekly_data(
    conn: sqlite3.Connection,
    week_start: str,    # 'YYYY-MM-DD'
    week_end: str,      # 'YYYY-MM-DD'
    period_snapshot: str = '2026-04',   # 360 月报数据时点
) -> dict:
    """汇总周报所需的所有数据,返回 dict 供 UI 渲染"""
    year = int(week_start.split('-')[0])
    week_month = week_start[:7]      # 'YYYY-MM' 本周所在月份
    week_end_dt = pd.to_datetime(week_end)
    last_week_start = (pd.to_datetime(week_start) - timedelta(days=7)).strftime('%Y-%m-%d')
    last_week_end = (pd.to_datetime(week_end) - timedelta(days=7)).strftime('%Y-%m-%d')

    out = {
        '_meta': {
            'week_start': week_start, 'week_end': week_end,
            'last_week_start': last_week_start, 'last_week_end': last_week_end,
            'snapshot_period': period_snapshot,
            'year': year,
            'week_month': week_month,
            'generated_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
        },
    }

    # ═══ 一、分销商布局 ═══════════════════════════════
    # 1.1 分销商签约 — 用 dealer_si_snapshot 实时快照(取最新数据时点)
    si_period_row = pd.read_sql(
        "SELECT MAX(数据时点) AS 时点 FROM dealer_si_snapshot", conn).iloc[0]
    si_period = si_period_row['时点'] or period_snapshot

    si_overall = pd.read_sql(f"""
        SELECT COUNT(*) AS 总数,
               SUM("签约金额") AS 全年签约总额_万,
               SUM("累计任务") AS 累计任务_万,
               SUM("累计业绩达成（计任务）") AS 累计达成_万,
               SUM(CASE WHEN "累计业绩达成（计任务）" >= "累计任务" THEN 1 ELSE 0 END) AS 累计达标数,
               SUM(CASE WHEN "本月业绩达成（计任务）" >= "本月任务" THEN 1 ELSE 0 END) AS 本月达标数,
               SUM(CASE WHEN "是否新签" = 'Y' THEN 1 ELSE 0 END) AS 新签家数
          FROM dealer_si_snapshot WHERE 数据时点 = ?
    """, conn, params=(si_period,)).iloc[0]

    # 按城市汇总(dealer_si_snapshot 实时口径)
    city_si = pd.read_sql(f"""
        SELECT "客户所在城市" AS 城市,
               COUNT(*) AS 代理商数,
               SUM("签约金额") AS 签约金额_万,
               SUM("累计任务") AS 累计任务_万,
               SUM("累计业绩达成（计任务）") AS 累计达成_万,
               SUM(CASE WHEN "累计业绩达成（计任务）" >= "累计任务" THEN 1 ELSE 0 END) AS 达标家数
          FROM dealer_si_snapshot WHERE 数据时点 = ?
         GROUP BY 1
    """, conn, params=(si_period,))
    city_si['累计完成率'] = (city_si['累计达成_万'] / city_si['累计任务_万']).round(4)         # vs 阶段任务
    city_si['年度完成率'] = (city_si['累计达成_万'] / city_si['签约金额_万']).round(4)         # vs 签约金额(年度)
    city_si['签约是否达标_城市'] = (city_si['累计完成率'] >= 1.0).map({True: 'Y', False: 'N'})

    accum_task = float(si_overall['累计任务_万'] or 0)
    accum_done = float(si_overall['累计达成_万'] or 0)
    sign_total = float(si_overall['全年签约总额_万'] or 0)   # 年度任务 = 签约金额

    out['ch1_si_overall'] = {
        '_数据时点': si_period,
        '代理商总数': int(si_overall['总数']),
        '新签家数': int(si_overall['新签家数']),
        '全年签约总额_万': round(sign_total, 1),
        '年度完成率': accum_done / sign_total if sign_total > 0 else None,   # 达成÷签约金额(年度口径)
        '累计任务_万': round(accum_task, 1),
        '累计达成_万': round(accum_done, 1),
        '累计完成率': accum_done / accum_task if accum_task > 0 else None,   # 达成÷累计任务(阶段达标率)
        '累计达标数': int(si_overall['累计达标数']),
        '本月达标数': int(si_overall['本月达标数']),
        '未达标城市': city_si[city_si['签约是否达标_城市'] == 'N']['城市'].tolist(),
    }
    out['ch1_city_si'] = city_si.to_dict('records')

    # 1.2 省区 SO — 用 kpi_targets(主键 城市+区县,值在 SO目标_万) + product_flow
    so_target_row = pd.read_sql(f"""
        SELECT SUM(SO目标_万) AS 全省目标
          FROM kpi_targets WHERE 年度 = ?
    """, conn, params=(year,)).iloc[0]
    so_year_target_w = float(so_target_row['全省目标'] or 0)

    so_ytd = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数, ROUND(SUM(最新分销价)/10000, 2) AS 万
          FROM product_flow_v
         WHERE 上线年月 BETWEEN ? AND ?
    """, conn, params=(f'{year}-01', week_month)).iloc[0]

    so_yoy = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数, ROUND(SUM(最新分销价)/10000, 2) AS 万
          FROM product_flow_v
         WHERE 上线年月 BETWEEN ? AND ?
    """, conn, params=(f'{year-1}-01', f'{year-1}-{week_month[5:]}')).iloc[0]

    # 本月已达成
    so_mtd = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数, ROUND(SUM(最新分销价)/10000, 2) AS 万
          FROM product_flow_v WHERE 上线年月 = ?
    """, conn, params=(week_month,)).iloc[0]

    so_mtd_yoy = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数, ROUND(SUM(最新分销价)/10000, 2) AS 万
          FROM product_flow_v WHERE 上线年月 = ?
    """, conn, params=(f'{year-1}-{week_month[5:]}',)).iloc[0]

    # 本周新增 SO(week_start ~ week_end 之间的上线)
    so_week = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数, ROUND(SUM(最新分销价)/10000, 2) AS 万
          FROM product_flow_v
         WHERE substr(上线时间, 1, 10) BETWEEN ? AND ?
    """, conn, params=(week_start, week_end)).iloc[0]
    # 上周新增 SO (用于"较上周变化"计算)
    so_last_week = pd.read_sql(f"""
        SELECT COUNT(*) AS 台数, ROUND(SUM(最新分销价)/10000, 2) AS 万
          FROM product_flow_v
         WHERE substr(上线时间, 1, 10) BETWEEN ? AND ?
    """, conn, params=(last_week_start, last_week_end)).iloc[0]

    ytd_amt = float(so_ytd['万'] or 0)
    yoy_amt = float(so_yoy['万'] or 0)
    mtd_amt = float(so_mtd['万'] or 0)
    mtd_yoy_amt = float(so_mtd_yoy['万'] or 0)
    week_amt = float(so_week['万'] or 0)
    last_week_amt = float(so_last_week['万'] or 0)

    out['ch1_so_province'] = {
        '年度目标_万': so_year_target_w,
        'YTD达成_万': ytd_amt,
        'YTD完成率': ytd_amt / so_year_target_w if so_year_target_w > 0 else None,
        'YTD同比': (ytd_amt - yoy_amt) / yoy_amt if yoy_amt > 0 else None,
        '本月达成_万': mtd_amt,
        '本月同比': (mtd_amt - mtd_yoy_amt) / mtd_yoy_amt if mtd_yoy_amt > 0 else None,
        '本周新增_万': week_amt,
        '上周新增_万': last_week_amt,
        '周环比': (week_amt - last_week_amt) / last_week_amt if last_week_amt > 0 else None,
    }

    # 1.2 城市级 SO 表现(用 city_snapshot_p3)
    city_so = pd.read_sql(f"""
        SELECT "城市", "SO金额目标", "累计SO金额", "SO完成率", "SO是否达标",
               "同期SO金额", "SO同比", "是否连续3个月SO下降"
          FROM city_snapshot_p3 WHERE 数据时点 = ?
    """, conn, params=(period_snapshot,))
    city_so['SO完成率_num'] = pd.to_numeric(city_so['SO完成率'], errors='coerce')
    city_so['SO同比_num'] = pd.to_numeric(city_so['SO同比'], errors='coerce')
    out['ch1_city_so'] = city_so.to_dict('records')
    out['ch1_so_not_达标_cities'] = city_so[city_so['SO是否达标'] == 'N']['城市'].tolist()
    out['ch1_so_yoy_negative_cities'] = city_so[city_so['SO同比_num'] < 0]['城市'].tolist()

    # 1.3 客户(代理商)SO 排查 — 未达标 + 同比负增长
    dealer_so = pd.read_sql(f"""
        SELECT "客户名称（未合并）" AS 客户, "所在城市", "客户所有者" AS 业务员,
               CAST("累月SO完成率" AS REAL) AS SO完成率,
               CAST("SO同比" AS REAL) AS SO同比,
               "是否新签", "分销商认证",
               "是否连续3个月SO同比下滑"
          FROM dealer_snapshot_p4 WHERE 数据时点 = ?
    """, conn, params=(period_snapshot,))
    out['ch1_dealer_so_未达100'] = dealer_so[dealer_so['SO完成率'] < 1].sort_values('SO完成率').to_dict('records')
    out['ch1_dealer_so_yoy_neg'] = dealer_so[dealer_so['SO同比'] < 0].sort_values('SO同比').to_dict('records')

    # ═══ 二、服务商管理 ═══════════════════════════════
    # 2.1 服务商签约/激活目标
    pt = pd.read_sql(f"""
        SELECT 服务商签约数_含个人 AS 签约目标,
               安装红包V2_家数 AS V2目标, 安装红包V3_家数 AS V3目标, 安装红包V4及以上_家数 AS V4目标,
               新签目标
          FROM provider_target WHERE 年度 = ? AND 地市 = '浙江合计'
    """, conn, params=(year,)).iloc[0]

    # 签约实绩 — provider_contract 全量(快照)+ 新签数(按「是否新签」字段)
    signed_total = pd.read_sql(
        "SELECT COUNT(*) AS n FROM provider_contract", conn).iloc[0]['n']
    # 新签 = 是否新签 = 'Y'(系统判定的真·新客户)
    new_signed_ytd = pd.read_sql(
        "SELECT COUNT(*) AS n FROM provider_contract WHERE 是否新签 = 'Y'",
        conn).iloc[0]['n']
    new_signed_month = pd.read_sql(f"""
        SELECT COUNT(*) AS n FROM provider_contract
         WHERE 是否新签 = 'Y' AND substr(签约日期, 1, 7) = ?
    """, conn, params=(week_month,)).iloc[0]['n']
    new_signed_week = pd.read_sql(f"""
        SELECT COUNT(*) AS n FROM provider_contract
         WHERE 是否新签 = 'Y' AND substr(签约日期, 1, 10) BETWEEN ? AND ?
    """, conn, params=(week_start, week_end)).iloc[0]['n']
    # 兼容字段(老 UI 用)
    signed_ytd = {'签约数': signed_total}

    # V2/V3/V4 等级达成(install_redpack 累计金额分级)
    activated = pd.read_sql(f"""
        WITH provider_amt AS (
          SELECT 上线客户编码, SUM(产品现有分销价) AS 金额
            FROM install_redpack_v WHERE 上线年月 BETWEEN ? AND ?
           GROUP BY 上线客户编码
        )
        SELECT
          SUM(CASE WHEN 金额 >= 1000 AND 金额 < 10000 THEN 1 ELSE 0 END) AS V2,
          SUM(CASE WHEN 金额 >= 10000 AND 金额 < 30000 THEN 1 ELSE 0 END) AS V3,
          SUM(CASE WHEN 金额 >= 30000 THEN 1 ELSE 0 END) AS V4
        FROM provider_amt
    """, conn, params=(f'{year}-01', week_month)).iloc[0]

    # ─── 激活口径:provider_contract.是否激活 = 'Y' ───
    # 存量激活 = 是否新签=N AND 是否激活=Y
    # 新增激活 = 是否新签=Y AND 是否激活=Y(本年新签且本年激活)
    activation = pd.read_sql(f"""
        SELECT
          SUM(CASE WHEN 是否激活='Y' THEN 1 ELSE 0 END) AS 累计激活,
          SUM(CASE WHEN 是否激活='Y' AND 是否新签='N' THEN 1 ELSE 0 END) AS 存量激活,
          SUM(CASE WHEN 是否激活='Y' AND 是否新签='Y' THEN 1 ELSE 0 END) AS 新增激活
          FROM provider_contract
    """, conn).iloc[0]

    # 本月/本周存量激活(本期间内激活的存量服务商)
    month_stock_act = pd.read_sql(f"""
        SELECT COUNT(*) AS n FROM provider_contract
         WHERE 是否激活='Y' AND 是否新签='N'
           AND substr(激活时间, 1, 7) = ?
    """, conn, params=(week_month,)).iloc[0]['n']
    week_stock_act = pd.read_sql(f"""
        SELECT COUNT(*) AS n FROM provider_contract
         WHERE 是否激活='Y' AND 是否新签='N'
           AND substr(激活时间, 1, 10) BETWEEN ? AND ?
    """, conn, params=(week_start, week_end)).iloc[0]['n']
    # 本月/本周新增激活(本期间内激活的新签服务商)
    month_new_act = pd.read_sql(f"""
        SELECT COUNT(*) AS n FROM provider_contract
         WHERE 是否激活='Y' AND 是否新签='Y'
           AND substr(激活时间, 1, 7) = ?
    """, conn, params=(week_month,)).iloc[0]['n']
    week_new_act = pd.read_sql(f"""
        SELECT COUNT(*) AS n FROM provider_contract
         WHERE 是否激活='Y' AND 是否新签='Y'
           AND substr(激活时间, 1, 10) BETWEEN ? AND ?
    """, conn, params=(week_start, week_end)).iloc[0]['n']

    out['ch2_provider'] = {
        # 签约口径(provider_contract 全量)
        '签约目标': int(pt['签约目标'] or 0),
        '签约达成': int(signed_total),
        '本年新签': int(new_signed_ytd),
        '本月新签': int(new_signed_month),
        '本周新签': int(new_signed_week),
        # 激活口径(provider_contract.是否激活 = 'Y')
        '激活目标': int((pt['V2目标'] or 0) + (pt['V3目标'] or 0) + (pt['V4目标'] or 0)),
        '累计激活': int(activation['累计激活']),
        '存量激活': int(activation['存量激活']),
        '新增激活': int(activation['新增激活']),
        '本月存量激活': int(month_stock_act),
        '本周存量激活': int(week_stock_act),
        '本月新增激活': int(month_new_act),
        '本周新增激活': int(week_new_act),
        # V2/V3/V4 等级(install_redpack 累计金额分级)
        'V2目标': int(pt['V2目标'] or 0), 'V2达成': int(activated['V2'] or 0),
        'V3目标': int(pt['V3目标'] or 0), 'V3达成': int(activated['V3'] or 0),
        'V4目标': int(pt['V4目标'] or 0), 'V4达成': int(activated['V4'] or 0),
        '新签目标': int(pt['新签目标'] or 0),
        # 兼容旧字段
        '已激活': int(activation['累计激活']),
    }

    # 2.2 竞品 top 服务商进展(总览 + 转化情况)
    try:
        comp_overview = pd.read_sql("""
            SELECT COUNT(*) AS 总数,
                   ROUND(SUM(竞品体量_万), 1) AS 竞品体量_万,
                   SUM(CASE WHEN 在售大华 = 1 THEN 1 ELSE 0 END) AS 已开发,
                   COUNT(DISTINCT 城市) AS 覆盖城市数
              FROM competitor_top_provider
        """, conn).iloc[0]

        # 本月转化(本月有上线)
        comp_month = pd.read_sql("""
            WITH top_codes AS (SELECT DISTINCT 客户编码 AS 编码 FROM competitor_top_provider)
            SELECT COUNT(DISTINCT ir.上线客户编码) AS 家数,
                   COUNT(*) AS 单数,
                   ROUND(SUM(ir.产品现有分销价)/10000, 2) AS 金额_万
              FROM top_codes t
              JOIN install_redpack_v ir ON ir.上线客户编码 = t.编码
             WHERE ir.上线年月 = ?
        """, conn, params=(week_month,)).iloc[0]

        # 本周转化
        comp_week = pd.read_sql("""
            WITH top_codes AS (SELECT DISTINCT 客户编码 AS 编码 FROM competitor_top_provider)
            SELECT COUNT(DISTINCT ir.上线客户编码) AS 家数,
                   COUNT(*) AS 单数,
                   ROUND(SUM(ir.产品现有分销价)/10000, 2) AS 金额_万
              FROM top_codes t
              JOIN install_redpack ir ON ir.上线客户编码 = t.编码
             WHERE substr(ir.上线时间, 1, 10) BETWEEN ? AND ?
        """, conn, params=(week_start, week_end)).iloc[0]

        # 上周转化
        comp_lw = pd.read_sql("""
            WITH top_codes AS (SELECT DISTINCT 客户编码 AS 编码 FROM competitor_top_provider)
            SELECT COUNT(DISTINCT ir.上线客户编码) AS 家数,
                   ROUND(SUM(ir.产品现有分销价)/10000, 2) AS 金额_万
              FROM top_codes t
              JOIN install_redpack ir ON ir.上线客户编码 = t.编码
             WHERE substr(ir.上线时间, 1, 10) BETWEEN ? AND ?
        """, conn, params=(last_week_start, last_week_end)).iloc[0]

        total = int(comp_overview['总数'] or 0)
        opened = int(comp_overview['已开发'] or 0)
        out['ch2_competitor'] = {
            '总数': total,
            '竞品体量_万': float(comp_overview['竞品体量_万'] or 0),
            '覆盖城市数': int(comp_overview['覆盖城市数'] or 0),
            '已开发家数': opened,
            '已开发率': opened / total if total else None,
            '待开发家数': total - opened,
            '本月转化家数': int(comp_month['家数'] or 0),
            '本月转化单数': int(comp_month['单数'] or 0),
            '本月转化金额_万': float(comp_month['金额_万'] or 0),
            '本周转化家数': int(comp_week['家数'] or 0),
            '本周转化金额_万': float(comp_week['金额_万'] or 0),
            '上周转化家数': int(comp_lw['家数'] or 0),
            '上周转化金额_万': float(comp_lw['金额_万'] or 0),
            '较上周': int(comp_week['家数'] or 0) - int(comp_lw['家数'] or 0),
        }
        out['ch2_competitor_top_count'] = total
    except Exception:
        out['ch2_competitor'] = None
        out['ch2_competitor_top_count'] = None

    # 2.3 NP 流转(np_customer_pool 最新快照)
    try:
        np_period_row = pd.read_sql(
            "SELECT MAX(数据时点) AS 时点 FROM np_customer_pool", conn).iloc[0]
        np_period = np_period_row['时点']
        if np_period:
            np_stats = pd.read_sql(f"""
                SELECT COUNT(*) AS 流转,
                       SUM(CASE WHEN 签约服务商名称 IS NOT NULL AND 签约服务商名称 != '' THEN 1 ELSE 0 END) AS 已签约
                  FROM np_customer_pool WHERE 数据时点 = ?
            """, conn, params=(np_period,)).iloc[0]
            # 激活 = 已签约名 INNER JOIN install_redpack_v 累计金额 ≥ 1000
            np_activated = pd.read_sql(f"""
                WITH signed AS (
                  SELECT 签约服务商名称 AS 名 FROM np_customer_pool
                   WHERE 数据时点 = ? AND 签约服务商名称 IS NOT NULL AND 签约服务商名称 != ''
                ),
                amt AS (
                  SELECT 上线客户名称 AS 名, SUM(产品现有分销价) AS 金额
                    FROM install_redpack_v GROUP BY 1
                )
                SELECT COUNT(*) AS 已激活 FROM signed s JOIN amt a USING(名)
                 WHERE a.金额 >= 1000
            """, conn, params=(np_period,)).iloc[0]
            out['ch2_np'] = {
                '数据时点': np_period,
                '识别有效流转': int(np_stats['流转']),
                '已签约': int(np_stats['已签约']),
                '已激活': int(np_activated['已激活']),
            }
        else:
            out['ch2_np'] = None
    except Exception:
        out['ch2_np'] = None

    # 2.4 高德潜客(gaode_potential_customer 最新快照)
    try:
        gd_period_row = pd.read_sql(
            "SELECT MAX(数据时点) AS 时点 FROM gaode_potential_customer", conn).iloc[0]
        gd_period = gd_period_row['时点']
        if gd_period:
            gd_stats = pd.read_sql(f"""
                SELECT COUNT(*) AS 下发,
                       SUM(CASE WHEN 与我司业务相关 = 'Y' THEN 1 ELSE 0 END) AS 识别有效,
                       SUM(CASE WHEN 下一步计划 = '签约服务商' THEN 1 ELSE 0 END) AS 计划签约,
                       SUM(CASE WHEN 客户名称 IS NOT NULL AND 客户名称 != '' THEN 1 ELSE 0 END) AS 已签约
                  FROM gaode_potential_customer WHERE 数据时点 = ?
            """, conn, params=(gd_period,)).iloc[0]
            # 激活 = 客户名称 命中 install_redpack 累计 ≥ 1000
            gd_activated = pd.read_sql(f"""
                WITH signed AS (
                  SELECT 客户名称 AS 名 FROM gaode_potential_customer
                   WHERE 数据时点 = ? AND 客户名称 IS NOT NULL AND 客户名称 != ''
                ),
                amt AS (
                  SELECT 上线客户名称 AS 名, SUM(产品现有分销价) AS 金额
                    FROM install_redpack_v GROUP BY 1
                )
                SELECT COUNT(*) AS 已激活 FROM signed s JOIN amt a USING(名)
                 WHERE a.金额 >= 1000
            """, conn, params=(gd_period,)).iloc[0]
            out['ch2_gaode'] = {
                '数据时点': gd_period,
                '累计下发': int(gd_stats['下发']),
                '识别有效': int(gd_stats['识别有效']),
                '计划签约': int(gd_stats['计划签约']),
                '已签约': int(gd_stats['已签约']),
                '已激活': int(gd_activated['已激活']),
            }
        else:
            out['ch2_gaode'] = None
    except Exception:
        out['ch2_gaode'] = None

    # ═══ 三、技术行销 — 三大专项 SO 达成 ═══════════════════════════════
    focus_data = []
    for focus in ('夜视王', '无线', '场景化'):
        target_row = pd.read_sql("""
            SELECT 目标台数 FROM focus_target
             WHERE 专项 = ? AND 年度 = ? AND 地市 = '浙江合计'
        """, conn, params=(focus, year))
        target_n = int(target_row['目标台数'].iloc[0]) if not target_row.empty else 0

        ytd_row = pd.read_sql("""
            SELECT COUNT(*) AS 台数
              FROM product_flow_v pf
              JOIN product_focus fc ON fc.物料号 = pf.物料号
             WHERE fc.专项 = ? AND 上线年月 BETWEEN ? AND ?
        """, conn, params=(focus, f'{year}-01', week_month)).iloc[0]

        mtd_row = pd.read_sql("""
            SELECT COUNT(*) AS 台数
              FROM product_flow_v pf
              JOIN product_focus fc ON fc.物料号 = pf.物料号
             WHERE fc.专项 = ? AND 上线年月 = ?
        """, conn, params=(focus, week_month)).iloc[0]

        # 本周新增(week_start ~ week_end)
        week_row = pd.read_sql("""
            SELECT COUNT(*) AS 台数
              FROM product_flow_v pf
              JOIN product_focus fc ON fc.物料号 = pf.物料号
             WHERE fc.专项 = ? AND substr(pf.上线时间, 1, 10) BETWEEN ? AND ?
        """, conn, params=(focus, week_start, week_end)).iloc[0]
        # 上周新增
        lw_row = pd.read_sql("""
            SELECT COUNT(*) AS 台数
              FROM product_flow_v pf
              JOIN product_focus fc ON fc.物料号 = pf.物料号
             WHERE fc.专项 = ? AND substr(pf.上线时间, 1, 10) BETWEEN ? AND ?
        """, conn, params=(focus, last_week_start, last_week_end)).iloc[0]

        focus_data.append({
            '专项': focus, '目标': target_n,
            'YTD达成': int(ytd_row['台数']),
            'YTD完成率': int(ytd_row['台数']) / target_n if target_n > 0 else None,
            '本月达成': int(mtd_row['台数']),
            '本周新增': int(week_row['台数']),
            '上周新增': int(lw_row['台数']),
            '较上周': int(week_row['台数']) - int(lw_row['台数']),
        })
    out['ch3_focus'] = focus_data

    # 3.1 无线铺货管理(distribution_info)
    try:
        # 总览(全部时段)
        dist_overview = pd.read_sql("""
            SELECT
              COUNT(DISTINCT di.序列号) AS 累计铺货数,
              COUNT(DISTINCT di.客户名称_下级) AS 累计铺货服务商,
              SUM(CASE WHEN ir.产品序列号 IS NOT NULL THEN 1 ELSE 0 END) AS 已上线数,
              COUNT(DISTINCT di.序列号) AS 全部
              FROM distribution_info di
              LEFT JOIN install_redpack ir ON ir.产品序列号 = di.序列号
              JOIN product_focus fc ON fc.物料号 = di.物料号
             WHERE fc.专项 = '无线'
        """, conn).iloc[0]

        # 本周新提交铺货(按提交铺货时间)
        wireless_week = pd.read_sql("""
            SELECT COUNT(DISTINCT di.序列号) AS 铺货台数,
                   COUNT(DISTINCT di.客户名称_下级) AS 铺货家数
              FROM distribution_info di
              JOIN product_focus fc ON fc.物料号 = di.物料号
             WHERE fc.专项 = '无线'
               AND substr(di.提交铺货时间, 1, 10) BETWEEN ? AND ?
        """, conn, params=(week_start, week_end)).iloc[0]

        wireless_lw = pd.read_sql("""
            SELECT COUNT(DISTINCT di.序列号) AS 铺货台数,
                   COUNT(DISTINCT di.客户名称_下级) AS 铺货家数
              FROM distribution_info di
              JOIN product_focus fc ON fc.物料号 = di.物料号
             WHERE fc.专项 = '无线'
               AND substr(di.提交铺货时间, 1, 10) BETWEEN ? AND ?
        """, conn, params=(last_week_start, last_week_end)).iloc[0]

        # 本周铺货里已上线的比例
        wireless_week_online = pd.read_sql("""
            SELECT COUNT(DISTINCT di.序列号) AS 已上线
              FROM distribution_info di
              JOIN product_focus fc ON fc.物料号 = di.物料号
              JOIN install_redpack ir ON ir.产品序列号 = di.序列号
             WHERE fc.专项 = '无线'
               AND substr(di.提交铺货时间, 1, 10) BETWEEN ? AND ?
        """, conn, params=(week_start, week_end)).iloc[0]

        # 本周铺货客户分类拆分(夫妻门店 / 批发类 / 其他)
        wireless_week_class = pd.read_sql("""
            SELECT
              SUM(CASE WHEN pc.客户分类_规范 = '夫妻门店' THEN 1 ELSE 0 END) AS 夫妻店,
              SUM(CASE WHEN pc.客户分类_规范 = '批发门店' THEN 1 ELSE 0 END) AS 批发类
              FROM (
                SELECT DISTINCT di.客户名称_下级
                  FROM distribution_info di
                  JOIN product_focus fc ON fc.物料号 = di.物料号
                 WHERE fc.专项 = '无线'
                   AND substr(di.提交铺货时间, 1, 10) BETWEEN ? AND ?
              ) sub
              LEFT JOIN provider_contract_v pc ON pc.客户名称 = sub.客户名称_下级
        """, conn, params=(week_start, week_end)).iloc[0]

        wireless_lw_class = pd.read_sql("""
            SELECT
              SUM(CASE WHEN pc.客户分类_规范 = '夫妻门店' THEN 1 ELSE 0 END) AS 夫妻店,
              SUM(CASE WHEN pc.客户分类_规范 = '批发门店' THEN 1 ELSE 0 END) AS 批发类
              FROM (
                SELECT DISTINCT di.客户名称_下级
                  FROM distribution_info di
                  JOIN product_focus fc ON fc.物料号 = di.物料号
                 WHERE fc.专项 = '无线'
                   AND substr(di.提交铺货时间, 1, 10) BETWEEN ? AND ?
              ) sub
              LEFT JOIN provider_contract_v pc ON pc.客户名称 = sub.客户名称_下级
        """, conn, params=(last_week_start, last_week_end)).iloc[0]

        cum_total = int(dist_overview['累计铺货数'] or 0)
        cum_online = int(dist_overview['已上线数'] or 0)
        wk_houses = int(wireless_week['铺货家数'] or 0)
        lw_houses = int(wireless_lw['铺货家数'] or 0)
        wk_couple = int(wireless_week_class['夫妻店'] or 0)
        wk_wholesale = int(wireless_week_class['批发类'] or 0)
        lw_couple = int(wireless_lw_class['夫妻店'] or 0)
        lw_wholesale = int(wireless_lw_class['批发类'] or 0)
        out['ch3_wireless_distribution'] = {
            '累计铺货台数': cum_total,
            '累计铺货家数': int(dist_overview['累计铺货服务商'] or 0),
            '累计上线率': cum_online / cum_total if cum_total else None,
            '本周铺货台数': int(wireless_week['铺货台数'] or 0),
            '本周铺货家数': wk_houses,
            '上周铺货台数': int(wireless_lw['铺货台数'] or 0),
            '上周铺货家数': lw_houses,
            '较上周': wk_houses - lw_houses,
            '本周上线数': int(wireless_week_online['已上线'] or 0),
            '本周上线率': (int(wireless_week_online['已上线'] or 0) / int(wireless_week['铺货台数'])
                          if wireless_week['铺货台数'] else None),
            # 客户分类拆分
            '本周夫妻店': wk_couple,
            '本周夫妻店占比': wk_couple / wk_houses if wk_houses else None,
            '本周批发类': wk_wholesale,
            '本周批发类占比': wk_wholesale / wk_houses if wk_houses else None,
            '夫妻店较上周': wk_couple - lw_couple,
            '批发类较上周': wk_wholesale - lw_wholesale,
        }
    except Exception:
        out['ch3_wireless_distribution'] = None

    # 3.2 呆滞品(从 P4 取)
    stock = pd.read_sql(f"""
        SELECT SUM(CAST("分销商呆滞库存" AS REAL)) AS 呆滞,
               SUM(CAST("呆滞库存上线台数" AS REAL)) AS 已消耗,
               ROUND(AVG(CAST("呆滞库存消化率" AS REAL))*100, 2) AS 消化率pct
          FROM dealer_snapshot_p4 WHERE 数据时点 = ?
    """, conn, params=(period_snapshot,)).iloc[0]
    out['ch3_stock'] = {
        '呆滞总台数': int(stock['呆滞'] or 0),
        '已消耗': int(stock['已消耗'] or 0),
        '消化率pct': float(stock['消化率pct'] or 0),
    }

    return out
