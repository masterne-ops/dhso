#!/usr/bin/env python3
"""产品维度经营报告 v4 — 全量数据(不限 Top N),供前端 JS 筛选

产出:全量产品 × 维度 + 全量服务商 + 全量 11 地市矩阵,在 HTML 里嵌入完整 JSON,
浏览器 JS 做筛选/排序/搜索。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH as DB

FOCUS_SUBSERIES = ['夜视王2.0', '4G']


def shift_period(start, end, months):
    s = pd.Period(start, freq='M') + months
    e = pd.Period(end, freq='M') + months
    return str(s), str(e)


def n_months(start, end):
    return (pd.Period(end, freq='M') - pd.Period(start, freq='M')).n + 1


def calc_yoy(cur, base):
    if base and base > 0:
        return (cur - base) / base
    return None


def safe(d, k, default=0): return float(d.get(k) or default)


def run(period_start, period_end, city):
    conn = sqlite3.connect(str(DB))

    yoy_start, yoy_end = shift_period(period_start, period_end, -12)
    n = n_months(period_start, period_end)
    mom_start, mom_end = shift_period(period_start, period_end, -n)

    out = {
        '_meta': {
            'city': city or '全省',
            'period_start': period_start, 'period_end': period_end,
            'yoy_start': yoy_start, 'yoy_end': yoy_end,
            'mom_start': mom_start, 'mom_end': mom_end,
            'n_months': n,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        }
    }

    pf_city, pf_p = ("", [])
    if city:
        pf_city, pf_p = " AND 上线城市 = ?", [city]
    rp_city, rp_p = ("", [])
    if city:
        rp_city, rp_p = " AND 上线客户地市 = ?", [city]

    # ━━━━━━━━━━━ 全量子系列清单 + 三级归类 ━━━━━━━━━━━
    all_subs = pd.read_sql(f"""
        SELECT [产品子系列-新] AS 子系列,
               ROUND(SUM(最新分销价)/10000, 2) AS 货值_万,
               COUNT(*) AS 台数
          FROM product_flow_v
         WHERE 上线年月 BETWEEN ? AND ?
           AND [产品子系列-新] IS NOT NULL AND [产品子系列-新] != ''
           {pf_city}
         GROUP BY 1
         ORDER BY 2 DESC
    """, conn, params=[period_start, period_end, *pf_p])
    sub_list = all_subs['子系列'].tolist()
    top10_subs = sub_list[:10]

    if sub_list:
        ph = ','.join('?' * len(sub_list))
        tri = pd.read_sql(f"""
            WITH counted AS (
              SELECT [产品子系列-新] AS 子系列, 国内产品线三级 AS 三级, COUNT(*) AS cnt
                FROM product_flow_v
               WHERE [产品子系列-新] IN ({ph})
                 AND 国内产品线三级 IS NOT NULL AND 国内产品线三级 != ''
               GROUP BY 1, 2
            ),
            ranked AS (
              SELECT *, ROW_NUMBER() OVER (PARTITION BY 子系列 ORDER BY cnt DESC) AS rn FROM counted
            )
            SELECT 子系列, 三级 FROM ranked WHERE rn = 1
        """, conn, params=sub_list)
        tri_map = dict(zip(tri['子系列'], tri['三级']))
    else:
        tri_map = {}

    # 加同期 / 均价
    yoy_sub = pd.read_sql(f"""
        SELECT [产品子系列-新] AS 子系列,
               ROUND(SUM(最新分销价)/10000, 2) AS 同期_万,
               ROUND(SUM(最新分销价)/NULLIF(COUNT(*),0), 0) AS 同期均价
          FROM product_flow_v
         WHERE 上线年月 BETWEEN ? AND ?
           AND [产品子系列-新] IN ({ph}) {pf_city}
         GROUP BY 1
    """, conn, params=[yoy_start, yoy_end, *sub_list, *pf_p])
    cur_avg = pd.read_sql(f"""
        SELECT [产品子系列-新] AS 子系列,
               ROUND(SUM(最新分销价)/NULLIF(COUNT(*),0), 0) AS 当期均价
          FROM product_flow_v
         WHERE 上线年月 BETWEEN ? AND ?
           AND [产品子系列-新] IN ({ph}) {pf_city}
         GROUP BY 1
    """, conn, params=[period_start, period_end, *sub_list, *pf_p])
    all_subs = all_subs.merge(yoy_sub, on='子系列', how='left').merge(cur_avg, on='子系列', how='left')
    all_subs['同比'] = (all_subs['货值_万'] - all_subs['同期_万'].fillna(0)) / all_subs['同期_万'].replace(0, pd.NA)
    all_subs['均价同比'] = (all_subs['当期均价'] - all_subs['同期均价']) / all_subs['同期均价'].replace(0, pd.NA)
    all_subs['三级'] = all_subs['子系列'].map(tri_map).fillna('(未知)')
    all_subs = all_subs.where(pd.notnull(all_subs), None)
    out['全量子系列'] = all_subs[[
        '三级', '子系列', '货值_万', '同期_万', '同比', '当期均价', '同期均价', '均价同比', '台数'
    ]].to_dict('records')
    out['Top10子系列'] = top10_subs

    # ━━━━━━━━━━━ 章 1.1 SO 总量(双口径 × 5 列) ━━━━━━━━━━━
    def rp_total(p_s, p_e):
        return pd.read_sql(f"""
            SELECT SUM(产品现有分销价)/10000 AS SO_万,
                   COUNT(*) AS 台数,
                   COUNT(DISTINCT 上线客户编码) AS 活跃服务商
              FROM install_redpack_v
             WHERE 上线年月 BETWEEN ? AND ? {rp_city}
        """, conn, params=[p_s, p_e, *rp_p]).iloc[0].to_dict()

    def pf_total(p_s, p_e):
        return pd.read_sql(f"""
            SELECT SUM(最新分销价)/10000 AS SO_万, COUNT(*) AS 台数
              FROM product_flow_v
             WHERE 上线年月 BETWEEN ? AND ? {pf_city}
        """, conn, params=[p_s, p_e, *pf_p]).iloc[0].to_dict()

    rp_cur, rp_yoy, rp_mom = rp_total(period_start, period_end), rp_total(yoy_start, yoy_end), rp_total(mom_start, mom_end)
    pf_cur, pf_yoy, pf_mom = pf_total(period_start, period_end), pf_total(yoy_start, yoy_end), pf_total(mom_start, mom_end)

    rp_so, rp_so_y, rp_so_m = safe(rp_cur, 'SO_万'), safe(rp_yoy, 'SO_万'), safe(rp_mom, 'SO_万')
    pf_so, pf_so_y, pf_so_m = safe(pf_cur, 'SO_万'), safe(pf_yoy, 'SO_万'), safe(pf_mom, 'SO_万')

    out['章1_1_SO总量'] = [
        {'指标': 'SO 货值(红包扫码,万)', '当期': rp_so, '同期': rp_so_y, '环期': rp_so_m,
         '同比': calc_yoy(rp_so, rp_so_y), '环比': calc_yoy(rp_so, rp_so_m)},
        {'指标': '上线台数(红包扫码)', '当期': safe(rp_cur,'台数'), '同期': safe(rp_yoy,'台数'), '环期': safe(rp_mom,'台数'),
         '同比': calc_yoy(safe(rp_cur,'台数'), safe(rp_yoy,'台数')), '环比': calc_yoy(safe(rp_cur,'台数'), safe(rp_mom,'台数'))},
        {'指标': '活跃服务商数', '当期': safe(rp_cur,'活跃服务商'), '同期': safe(rp_yoy,'活跃服务商'),
         '环期': safe(rp_mom,'活跃服务商'),
         '同比': calc_yoy(safe(rp_cur,'活跃服务商'), safe(rp_yoy,'活跃服务商')),
         '环比': calc_yoy(safe(rp_cur,'活跃服务商'), safe(rp_mom,'活跃服务商'))},
        {'指标': 'SO 货值(全量感知,万)', '当期': pf_so, '同期': pf_so_y, '环期': pf_so_m,
         '同比': calc_yoy(pf_so, pf_so_y), '环比': calc_yoy(pf_so, pf_so_m)},
        {'指标': '上线台数(全量感知)', '当期': safe(pf_cur,'台数'), '同期': safe(pf_yoy,'台数'),
         '环期': safe(pf_mom,'台数'),
         '同比': calc_yoy(safe(pf_cur,'台数'), safe(pf_yoy,'台数')),
         '环比': calc_yoy(safe(pf_cur,'台数'), safe(pf_mom,'台数'))},
        {'指标': '绑定率(红包/全量)',
         '当期': rp_so/pf_so if pf_so > 0 else None,
         '同期': rp_so_y/pf_so_y if pf_so_y > 0 else None,
         '环期': rp_so_m/pf_so_m if pf_so_m > 0 else None,
         '同比': None, '环比': None, '_是比率': True},
    ]

    # ━━━━━━━━━━━ 章 2.x 全量产品 × {区县, 代理商} ━━━━━━━━━━━
    def full_product_x_dim_pf(dim_field):
        sql = f"""
            WITH cur AS (
              SELECT [产品子系列-新] AS 子系列, {dim_field} AS 维度,
                     SUM(最新分销价) AS 货值, COUNT(*) AS 台数
                FROM product_flow_v
               WHERE 上线年月 BETWEEN ? AND ?
                 AND [产品子系列-新] IS NOT NULL AND [产品子系列-新] != ''
                 AND {dim_field} IS NOT NULL AND {dim_field} != ''
                 AND {dim_field} NOT LIKE '%***%' {pf_city}
               GROUP BY 1, 2
            ),
            yoy AS (
              SELECT [产品子系列-新] AS 子系列, {dim_field} AS 维度,
                     SUM(最新分销价) AS 货值_同期
                FROM product_flow_v
               WHERE 上线年月 BETWEEN ? AND ?
                 AND [产品子系列-新] IS NOT NULL AND [产品子系列-新] != ''
                 AND {dim_field} IS NOT NULL AND {dim_field} != ''
                 AND {dim_field} NOT LIKE '%***%' {pf_city}
               GROUP BY 1, 2
            )
            SELECT cur.子系列, cur.维度,
                   ROUND(cur.货值/10000, 2) AS 货值_万,
                   cur.台数,
                   ROUND(COALESCE(yoy.货值_同期,0)/10000, 2) AS 同期_万,
                   CASE WHEN yoy.货值_同期 > 0 THEN (cur.货值 - yoy.货值_同期)/yoy.货值_同期 END AS 同比
              FROM cur LEFT JOIN yoy USING(子系列, 维度)
             WHERE cur.货值/10000 >= 0.1
             ORDER BY cur.货值 DESC
        """
        params = [period_start, period_end, *pf_p, yoy_start, yoy_end, *pf_p]
        df = pd.read_sql(sql, conn, params=params)
        df = df.where(pd.notnull(df), None)
        return df.to_dict('records')

    out['全量_产品×区县'] = full_product_x_dim_pf('上线区县')
    out['全量_产品×代理商'] = full_product_x_dim_pf('出库客户名称')

    # ━━━━━━━━━━━ 章 2.3 全量产品 × 服务商(红包扫码,去马甲,标 🚫) ━━━━━━━━━━━
    sql_sp = f"""
        SELECT ir.上线客户编码 AS 编码,
               MAX(ir.上线客户名称) AS 服务商,
               MAX(ir.上线客户地市) AS 地市,
               MAX(ir.上线客户区县) AS 区县,
               MAX(ir.所属一级客户) AS 代理商,
               ir.[产品子系列-新] AS 子系列,
               ROUND(SUM(ir.产品现有分销价)/10000, 2) AS 货值_万,
               COUNT(*) AS 台数,
               CASE WHEN MAX(cp.客户编码) IS NOT NULL THEN '🚫' ELSE '' END AS 标识
          FROM install_redpack_v ir
          LEFT JOIN vest_account va ON va.服务商客户编码 = ir.上线客户编码
          LEFT JOIN closed_provider cp ON cp.客户编码 = ir.上线客户编码
         WHERE ir.上线年月 BETWEEN ? AND ?
           AND ir.[产品子系列-新] IS NOT NULL AND ir.[产品子系列-新] != ''
           AND va.服务商客户编码 IS NULL
           AND ir.上线客户编码 IS NOT NULL
           {rp_city.replace('上线客户地市', 'ir.上线客户地市')}
         GROUP BY ir.上线客户编码, ir.[产品子系列-新]
        HAVING 货值_万 >= 0.01
         ORDER BY 货值_万 DESC
    """
    sp_long = pd.read_sql(sql_sp, conn, params=[period_start, period_end, *rp_p])
    sp_long = sp_long.where(pd.notnull(sp_long), None)
    out['全量_产品×服务商'] = sp_long.to_dict('records')

    # 服务商汇总(每家一行,所有子系列加总)
    sp_total = sp_long.groupby('编码').agg(
        服务商=('服务商', 'first'),
        地市=('地市', 'first'),
        区县=('区县', 'first'),
        代理商=('代理商', 'first'),
        标识=('标识', 'first'),
        货值_万=('货值_万', 'sum'),
        台数=('台数', 'sum'),
        涉及子系列数=('子系列', 'nunique'),
    ).reset_index().sort_values('货值_万', ascending=False)
    # 加同期货值
    if not sp_total.empty:
        codes = sp_total['编码'].tolist()
        ph_c = ','.join('?' * len(codes))
        yoy_sp = pd.read_sql(f"""
            SELECT 上线客户编码 AS 编码, ROUND(SUM(产品现有分销价)/10000, 2) AS 同期_万
              FROM install_redpack_v
             WHERE 上线客户编码 IN ({ph_c})
               AND 上线年月 BETWEEN ? AND ?
             GROUP BY 1
        """, conn, params=[*codes, yoy_start, yoy_end])
        sp_total = sp_total.merge(yoy_sp, on='编码', how='left').fillna({'同期_万': 0})
        sp_total['同比'] = (sp_total['货值_万'] - sp_total['同期_万']) / sp_total['同期_万'].replace(0, pd.NA)

        # 每家的 Top 3 主要子系列
        top_subs_per = sp_long.sort_values(['编码', '货值_万'], ascending=[True, False]).groupby('编码').head(3)
        main_subs = top_subs_per.groupby('编码')['子系列'].apply(lambda x: ', '.join(x)).to_dict()
        sp_total['主要子系列'] = sp_total['编码'].map(main_subs).fillna('—')

        sp_total = sp_total.where(pd.notnull(sp_total), None)
        out['全量_服务商汇总'] = sp_total.to_dict('records')
    else:
        out['全量_服务商汇总'] = []

    # ━━━━━━━━━━━ 章 3 全量 子系列 × 地市 透视 ━━━━━━━━━━━
    if sub_list:
        ph = ','.join('?' * len(sub_list))
        cur_mat = pd.read_sql(f"""
            SELECT [产品子系列-新] AS 子系列, 上线城市 AS 地市,
                   ROUND(SUM(最新分销价)/10000, 2) AS 货值_万
              FROM product_flow_v
             WHERE 上线年月 BETWEEN ? AND ?
               AND [产品子系列-新] IN ({ph})
               AND 上线城市 IS NOT NULL AND 上线城市 NOT LIKE '%***%'
               {pf_city}
             GROUP BY 1, 2
        """, conn, params=[period_start, period_end, *sub_list, *pf_p])
        yoy_mat = pd.read_sql(f"""
            SELECT [产品子系列-新] AS 子系列, 上线城市 AS 地市,
                   SUM(最新分销价) AS 货值_同期
              FROM product_flow_v
             WHERE 上线年月 BETWEEN ? AND ?
               AND [产品子系列-新] IN ({ph})
               AND 上线城市 IS NOT NULL AND 上线城市 NOT LIKE '%***%'
               {pf_city}
             GROUP BY 1, 2
        """, conn, params=[yoy_start, yoy_end, *sub_list, *pf_p])

        # 货值矩阵 + 同比矩阵(行 = 子系列,列 = 地市)
        amt_pivot = cur_mat.pivot_table(index='子系列', columns='地市', values='货值_万', fill_value=0).reindex(sub_list)
        cur_mat['货值'] = cur_mat['货值_万'] * 10000
        m = cur_mat.merge(yoy_mat, on=['子系列', '地市'], how='left').fillna({'货值_同期': 0})
        m['同比'] = (m['货值'] - m['货值_同期']) / m['货值_同期'].replace(0, pd.NA)
        yoy_pivot = m.pivot_table(index='子系列', columns='地市', values='同比').reindex(sub_list)

        out['全量_子系列×地市'] = {
            '地市列表': list(amt_pivot.columns),
            '货值矩阵': amt_pivot.reset_index().fillna(0).to_dict('records'),
            '同比矩阵': yoy_pivot.reset_index().where(pd.notnull(yoy_pivot.reset_index()), None).to_dict('records'),
        }
    else:
        out['全量_子系列×地市'] = {'地市列表': [], '货值矩阵': [], '同比矩阵': []}

    # ━━━━━━━━━━━ 章 4 渠道健康度 全量名单(同环比对称) ━━━━━━━━━━━
    def active_set(p_s, p_e):
        return set(pd.read_sql(f"""
            SELECT DISTINCT 上线客户编码 FROM install_redpack_v
             WHERE 上线年月 BETWEEN ? AND ? {rp_city}
               AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
        """, conn, params=[p_s, p_e, *rp_p])['上线客户编码'].astype(str))

    E, Y, M = active_set(period_start, period_end), active_set(yoy_start, yoy_end), active_set(mom_start, mom_end)

    all_codes = list(E | Y | M)
    if all_codes:
        ph = ','.join('?' * len(all_codes))
        meta = pd.read_sql(f"""
            SELECT ir.上线客户编码 AS 编码,
                   MAX(ir.上线客户名称) AS 服务商,
                   MAX(ir.上线客户地市) AS 地市,
                   MAX(ir.上线客户区县) AS 区县,
                   CASE WHEN MAX(va.服务商客户编码) IS NOT NULL THEN '🎭' ELSE '' END AS 马甲,
                   CASE WHEN MAX(cp.客户编码) IS NOT NULL THEN '🚫' ELSE '' END AS 已关闭
              FROM install_redpack_v ir
              LEFT JOIN vest_account va ON va.服务商客户编码 = ir.上线客户编码
              LEFT JOIN closed_provider cp ON cp.客户编码 = ir.上线客户编码
             WHERE ir.上线客户编码 IN ({ph})
             GROUP BY ir.上线客户编码
        """, conn, params=all_codes)

        amt = pd.read_sql(f"""
            SELECT 上线客户编码 AS 编码,
                   ROUND(SUM(CASE WHEN 上线年月 BETWEEN ? AND ? THEN 产品现有分销价 ELSE 0 END)/10000, 2) AS 评估期_万,
                   ROUND(SUM(CASE WHEN 上线年月 BETWEEN ? AND ? THEN 产品现有分销价 ELSE 0 END)/10000, 2) AS 同期_万,
                   ROUND(SUM(CASE WHEN 上线年月 BETWEEN ? AND ? THEN 产品现有分销价 ELSE 0 END)/10000, 2) AS 环期_万
              FROM install_redpack_v
             WHERE 上线客户编码 IN ({ph})
             GROUP BY 1
        """, conn, params=[period_start, period_end, yoy_start, yoy_end, mom_start, mom_end, *all_codes])
        meta = meta.merge(amt, on='编码', how='left').fillna(0)
        meta['标识'] = (meta['马甲'].astype(str) + meta['已关闭'].astype(str)).replace('', '—')
        meta = meta.where(pd.notnull(meta), None)
        code_to_record = {r['编码']: r for r in meta.to_dict('records')}

        def make_full_list(codes, sort_col):
            rows = []
            for c in codes:
                if c in code_to_record:
                    rows.append(code_to_record[c])
            rows.sort(key=lambda x: -float(x.get(sort_col) or 0))
            return rows

        out['全量_渠道健康度'] = {
            '同期对比': {
                '评估期活跃': len(E), '对照期活跃': len(Y), '持续': len(E & Y),
                '新增': len(E - Y), '流失': len(Y - E),
                '净流入': len(E - Y) - len(Y - E),
                '新增名单': make_full_list(E - Y, '评估期_万'),
                '流失名单': make_full_list(Y - E, '同期_万'),
            },
            '环期对比': {
                '评估期活跃': len(E), '对照期活跃': len(M), '持续': len(E & M),
                '新增': len(E - M), '流失': len(M - E),
                '净流入': len(E - M) - len(M - E),
                '新增名单': make_full_list(E - M, '评估期_万'),
                '流失名单': make_full_list(M - E, '环期_万'),
            },
        }

        # 11 地市矩阵
        meta_by_city = {r['编码']: r['地市'] for r in meta.to_dict('records')}
        cities_set = sorted({c for c in meta_by_city.values() if c and '***' not in str(c)})
        city_rows = []
        for ct in cities_set:
            codes_in_city = {c for c, x in meta_by_city.items() if x == ct}
            E_c, Y_c, M_c = E & codes_in_city, Y & codes_in_city, M & codes_in_city
            city_rows.append({
                '地市': ct,
                '评估期活跃': len(E_c),
                '同期新增': len(E_c - Y_c), '同期流失': len(Y_c - E_c),
                '同期净流入': len(E_c - Y_c) - len(Y_c - E_c),
                '环期新增': len(E_c - M_c), '环期流失': len(M_c - E_c),
                '环期净流入': len(E_c - M_c) - len(M_c - E_c),
            })
        out['全量_渠道健康度']['11地市矩阵'] = sorted(city_rows, key=lambda x: -x['评估期活跃'])

        closed_codes = set(pd.read_sql("SELECT 客户编码 FROM closed_provider", conn)['客户编码'].astype(str))
        out['全量_渠道健康度']['同期流失中已关闭'] = len((Y - E) & closed_codes)
        out['全量_渠道健康度']['环期流失中已关闭'] = len((M - E) & closed_codes)
    else:
        out['全量_渠道健康度'] = {}

    # ━━━━━━━━━━━ 焦点子系列详细分析(夜视王2.0 + 4G) ━━━━━━━━━━━
    def focus_detail(sub_name):
        # 概况(双口径)
        cur_pf = pd.read_sql(f"""
            SELECT SUM(最新分销价)/10000 AS SO_万, COUNT(*) AS 台数,
                   SUM(最新分销价)/NULLIF(COUNT(*),0) AS 均价
              FROM product_flow_v
             WHERE 上线年月 BETWEEN ? AND ? AND [产品子系列-新] = ? {pf_city}
        """, conn, params=[period_start, period_end, sub_name, *pf_p]).iloc[0].to_dict()
        yoy_pf = pd.read_sql(f"""
            SELECT SUM(最新分销价)/10000 AS SO_万, COUNT(*) AS 台数,
                   SUM(最新分销价)/NULLIF(COUNT(*),0) AS 均价
              FROM product_flow_v
             WHERE 上线年月 BETWEEN ? AND ? AND [产品子系列-新] = ? {pf_city}
        """, conn, params=[yoy_start, yoy_end, sub_name, *pf_p]).iloc[0].to_dict()
        mom_pf = pd.read_sql(f"""
            SELECT SUM(最新分销价)/10000 AS SO_万 FROM product_flow_v
             WHERE 上线年月 BETWEEN ? AND ? AND [产品子系列-新] = ? {pf_city}
        """, conn, params=[mom_start, mom_end, sub_name, *pf_p]).iloc[0].to_dict()
        rp_cur_s = pd.read_sql(f"""
            SELECT COUNT(DISTINCT 上线客户编码) AS sp FROM install_redpack_v
             WHERE 上线年月 BETWEEN ? AND ? AND [产品子系列-新] = ? {rp_city}
        """, conn, params=[period_start, period_end, sub_name, *rp_p]).iloc[0].to_dict()

        c_so, y_so, m_so = safe(cur_pf, 'SO_万'), safe(yoy_pf, 'SO_万'), safe(mom_pf, 'SO_万')

        # 地市分布
        city_df = pd.read_sql(f"""
            WITH cur AS (
              SELECT 上线城市 AS 地市, SUM(最新分销价) AS 货值, COUNT(*) AS 台数
                FROM product_flow_v
               WHERE 上线年月 BETWEEN ? AND ? AND [产品子系列-新] = ?
                 AND 上线城市 IS NOT NULL AND 上线城市 NOT LIKE '%***%' {pf_city}
               GROUP BY 1
            ),
            yoy AS (
              SELECT 上线城市 AS 地市, SUM(最新分销价) AS 货值_同期
                FROM product_flow_v
               WHERE 上线年月 BETWEEN ? AND ? AND [产品子系列-新] = ?
                 AND 上线城市 IS NOT NULL AND 上线城市 NOT LIKE '%***%' {pf_city}
               GROUP BY 1
            )
            SELECT cur.地市, ROUND(cur.货值/10000, 2) AS 货值_万, cur.台数,
                   ROUND(COALESCE(yoy.货值_同期,0)/10000, 2) AS 同期_万,
                   CASE WHEN yoy.货值_同期 > 0 THEN (cur.货值 - yoy.货值_同期)/yoy.货值_同期 END AS 同比
              FROM cur LEFT JOIN yoy USING(地市)
             ORDER BY cur.货值 DESC
        """, conn, params=[period_start, period_end, sub_name, *pf_p,
                          yoy_start, yoy_end, sub_name, *pf_p])
        city_df = city_df.where(pd.notnull(city_df), None)

        # 区县全量 + 代理商全量
        def dim_full(dim_field):
            sql = f"""
                WITH cur AS (
                  SELECT {dim_field} AS 维度, SUM(最新分销价) AS 货值, COUNT(*) AS 台数,
                         MAX(上线城市) AS 地市
                    FROM product_flow_v
                   WHERE 上线年月 BETWEEN ? AND ? AND [产品子系列-新] = ?
                     AND {dim_field} IS NOT NULL AND {dim_field} != ''
                     AND {dim_field} NOT LIKE '%***%' {pf_city}
                   GROUP BY 1
                ),
                yoy AS (
                  SELECT {dim_field} AS 维度, SUM(最新分销价) AS 货值_同期
                    FROM product_flow_v
                   WHERE 上线年月 BETWEEN ? AND ? AND [产品子系列-新] = ?
                     AND {dim_field} IS NOT NULL AND {dim_field} != '' {pf_city}
                   GROUP BY 1
                )
                SELECT cur.维度, cur.地市,
                       ROUND(cur.货值/10000, 2) AS 货值_万, cur.台数,
                       ROUND(COALESCE(yoy.货值_同期,0)/10000, 2) AS 同期_万,
                       CASE WHEN yoy.货值_同期 > 0 THEN (cur.货值 - yoy.货值_同期)/yoy.货值_同期 END AS 同比
                  FROM cur LEFT JOIN yoy USING(维度)
                 WHERE cur.货值/10000 >= 0.05
                 ORDER BY cur.货值 DESC
            """
            df = pd.read_sql(sql, conn, params=[period_start, period_end, sub_name, *pf_p,
                                                  yoy_start, yoy_end, sub_name, *pf_p])
            return df.where(pd.notnull(df), None).to_dict('records')

        # 服务商全量(去马甲)
        sp_df = pd.read_sql(f"""
            SELECT ir.上线客户编码 AS 编码,
                   MAX(ir.上线客户名称) AS 服务商,
                   MAX(ir.上线客户地市) AS 地市,
                   MAX(ir.上线客户区县) AS 区县,
                   MAX(ir.所属一级客户) AS 代理商,
                   ROUND(SUM(ir.产品现有分销价)/10000, 2) AS 货值_万,
                   COUNT(*) AS 台数,
                   CASE WHEN MAX(cp.客户编码) IS NOT NULL THEN '🚫' ELSE '' END AS 标识
              FROM install_redpack_v ir
              LEFT JOIN vest_account va ON va.服务商客户编码 = ir.上线客户编码
              LEFT JOIN closed_provider cp ON cp.客户编码 = ir.上线客户编码
             WHERE ir.上线年月 BETWEEN ? AND ?
               AND ir.[产品子系列-新] = ?
               AND va.服务商客户编码 IS NULL
               AND ir.上线客户编码 IS NOT NULL
               {rp_city.replace('上线客户地市', 'ir.上线客户地市')}
             GROUP BY ir.上线客户编码
            HAVING 货值_万 >= 0.01
             ORDER BY 货值_万 DESC
        """, conn, params=[period_start, period_end, sub_name, *rp_p])
        sp_df = sp_df.where(pd.notnull(sp_df), None)

        return {
            '子系列': sub_name,
            '概况': {
                '货值_当期_万': c_so, '货值_同期_万': y_so, '货值_环期_万': m_so,
                '货值同比': calc_yoy(c_so, y_so), '货值环比': calc_yoy(c_so, m_so),
                '台数_当期': int(safe(cur_pf, '台数')),
                '均价_当期': round(safe(cur_pf, '均价')) if safe(cur_pf, '均价') else None,
                '均价_同期': round(safe(yoy_pf, '均价')) if safe(yoy_pf, '均价') else None,
                '均价同比': calc_yoy(safe(cur_pf, '均价'), safe(yoy_pf, '均价')),
                '活跃服务商_红包': int(safe(rp_cur_s, 'sp')),
            },
            '地市分布': city_df.to_dict('records'),
            '区县全量': dim_full('上线区县'),
            '代理商全量': dim_full('出库客户名称'),
            '服务商全量': sp_df.to_dict('records'),
        }

    out['焦点子系列'] = {sub: focus_detail(sub) for sub in FOCUS_SUBSERIES}

    conn.close()
    return out


