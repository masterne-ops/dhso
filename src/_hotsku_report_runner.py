#!/usr/bin/env python3
"""爆款型号穿透分析 — 数据查询 runner

输入:关键字(对内部型号 / 外部型号 / 产品名称 LIKE 匹配)+ 评估期 + 地市
输出:dict,含 9 章数据,供 html.py 渲染
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
from _loaders import DB_PATH as DB  # noqa: E402

# ──────────────────────────────────────────
# 通用工具
# ──────────────────────────────────────────


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


def fmt_pct(x):
    if x is None:
        return None
    return round(x * 100, 1)


# ──────────────────────────────────────────
# 字段匹配:auto fallback
# ──────────────────────────────────────────

MATCH_FIELDS = ['内部型号', '外部型号', '产品名称']
AUTO_FALLBACK = ['内部型号', '外部型号']  # 自动 fallback 只走这两个,产品名称需用户显式指定


def resolve_match_field(conn, keyword: str, explicit: str | None = None) -> tuple[str, int]:
    """决定用哪个字段匹配。返回 (字段名, 命中行数)。

    - 显式指定字段:直接用
    - 否则:内部型号 → 外部型号 自动 fallback(产品名称噪音大,不进自动顺序)
    """
    if explicit in MATCH_FIELDS:
        cnt = conn.execute(
            f'SELECT COUNT(*) FROM product_flow_v WHERE "{explicit}" LIKE ?',
            (f'%{keyword}%',),
        ).fetchone()[0]
        return explicit, cnt

    for f in AUTO_FALLBACK:
        cnt = conn.execute(
            f'SELECT COUNT(*) FROM product_flow_v WHERE "{f}" LIKE ?',
            (f'%{keyword}%',),
        ).fetchone()[0]
        if cnt > 0:
            return f, cnt
    return AUTO_FALLBACK[0], 0


# ──────────────────────────────────────────
# 1. SKU 列表
# ──────────────────────────────────────────


def query_sku_list(conn, match_field, kw, period_start, period_end,
                   yoy_start, yoy_end, mom_start, mom_end, city):
    """命中 SKU 的 当期/同期/环期 货值&台数&均价。"""
    city_sql = ' AND 上线城市 = ?' if city else ''
    city_p = [city] if city else []

    def query_window(s, e):
        df = pd.read_sql(f"""
            SELECT
                内部型号, 外部型号,
                MIN(产品名称) AS 产品名称,
                "产品子系列-新" AS 子系列,
                国内产品线三级 AS 三级,
                COUNT(*) AS 台数,
                SUM(最新分销价) AS 货值,
                AVG(最新分销价) AS 均价
              FROM product_flow_v
             WHERE "{match_field}" LIKE ?
               AND 上线年月 BETWEEN ? AND ?
               {city_sql}
             GROUP BY 1, 2, 4, 5
        """, conn, params=[f'%{kw}%', s, e, *city_p])
        return df

    cur = query_window(period_start, period_end)
    yoy = query_window(yoy_start, yoy_end)
    mom = query_window(mom_start, mom_end)

    key_cols = ['内部型号', '外部型号']

    # join on key
    yoy_idx = yoy.groupby(key_cols).agg(
        同期台数=('台数', 'sum'),
        同期货值=('货值', 'sum'),
        同期均价=('均价', 'mean'),
    )
    mom_idx = mom.groupby(key_cols).agg(
        环期台数=('台数', 'sum'),
        环期货值=('货值', 'sum'),
        环期均价=('均价', 'mean'),
    )

    def _mode_or_first(s):
        s = s.dropna()
        return s.mode().iloc[0] if not s.empty and len(s.mode()) > 0 else None

    out = cur.groupby(key_cols, as_index=True).agg(
        产品名称=('产品名称', _mode_or_first),
        子系列=('子系列', _mode_or_first),
        三级=('三级', _mode_or_first),
        台数=('台数', 'sum'),
        货值=('货值', 'sum'),
        均价=('均价', 'mean'),
    )
    out = out.join(yoy_idx, how='left').join(mom_idx, how='left')

    # 同/环比
    out['同比'] = (out['货值'] - out['同期货值'].fillna(0)) / out['同期货值'].replace(0, pd.NA)
    out['环比'] = (out['货值'] - out['环期货值'].fillna(0)) / out['环期货值'].replace(0, pd.NA)
    out['均价同比'] = (out['均价'] - out['同期均价']) / out['同期均价']

    out = out.reset_index().sort_values('货值', ascending=False)

    # 转为可序列化
    records = []
    for _, r in out.iterrows():
        records.append({
            '内部型号': r['内部型号'],
            '外部型号': r['外部型号'],
            '产品名称': r['产品名称'],
            '子系列': r['子系列'],
            '三级': r['三级'],
            '台数': int(r['台数']),
            '货值': float(r['货值']),
            '均价': float(r['均价']),
            '同期货值': float(r['同期货值'] or 0),
            '环期货值': float(r['环期货值'] or 0),
            '同比': fmt_pct(r['同比']) if pd.notna(r['同比']) else None,
            '环比': fmt_pct(r['环比']) if pd.notna(r['环比']) else None,
            '均价同比': fmt_pct(r['均价同比']) if pd.notna(r['均价同比']) else None,
        })

    # 合计行(只用 cur)
    cur_total = cur.agg({'台数': 'sum', '货值': 'sum', '均价': 'mean'})
    yoy_total = yoy['货值'].sum() if not yoy.empty else 0
    mom_total = mom['货值'].sum() if not mom.empty else 0
    cur_avg = cur['均价'].mean() if not cur.empty else 0
    yoy_avg = yoy['均价'].mean() if not yoy.empty else 0

    total = {
        '台数': int(cur_total['台数']),
        '货值': float(cur_total['货值']),
        '均价': float(cur_avg) if pd.notna(cur_avg) else 0,
        '同期货值': float(yoy_total),
        '环期货值': float(mom_total),
        '同比': fmt_pct(calc_yoy(cur_total['货值'], yoy_total)),
        '环比': fmt_pct(calc_yoy(cur_total['货值'], mom_total)),
        '均价同比': fmt_pct(calc_yoy(cur_avg - yoy_avg if yoy_avg else None, yoy_avg))
                     if yoy_avg else None,
    }

    return records, total


# ──────────────────────────────────────────
# 2. 月度走势(评估期前 12 月起算)
# ──────────────────────────────────────────


def query_monthly_trend(conn, match_field, kw, period_end, city, n_months_back=12):
    """从评估期末往前 n 个月。"""
    start_period = str(pd.Period(period_end, freq='M') - (n_months_back - 1))

    city_sql = ' AND 上线城市 = ?' if city else ''
    city_p = [city] if city else []

    df = pd.read_sql(f"""
        SELECT 上线年月 AS 月份,
               COUNT(*) AS 台数,
               ROUND(SUM(最新分销价)/10000, 2) AS 货值_万,
               ROUND(AVG(最新分销价), 1) AS 均价
          FROM product_flow_v
         WHERE "{match_field}" LIKE ?
           AND 上线年月 BETWEEN ? AND ?
           {city_sql}
         GROUP BY 1
         ORDER BY 1
    """, conn, params=[f'%{kw}%', start_period, period_end, *city_p])

    return df.to_dict(orient='records')


# ──────────────────────────────────────────
# 3. 价格分布
# ──────────────────────────────────────────


def query_price_dist(conn, match_field, kw, period_start, period_end,
                     yoy_start, yoy_end, city):
    """评估期 vs 同期 价格分布。"""
    city_sql = ' AND 上线城市 = ?' if city else ''
    city_p = [city] if city else []

    def window(s, e):
        df = pd.read_sql(f"""
            SELECT 最新分销价
              FROM product_flow_v
             WHERE "{match_field}" LIKE ?
               AND 上线年月 BETWEEN ? AND ?
               AND 最新分销价 IS NOT NULL
               {city_sql}
        """, conn, params=[f'%{kw}%', s, e, *city_p])
        return df['最新分销价']

    cur_px = window(period_start, period_end)
    yoy_px = window(yoy_start, yoy_end)

    def stats(s):
        if s.empty:
            return None
        return {
            'n': int(len(s)),
            'min': round(float(s.min()), 1),
            'p25': round(float(s.quantile(0.25)), 1),
            'median': round(float(s.median()), 1),
            'mean': round(float(s.mean()), 1),
            'p75': round(float(s.quantile(0.75)), 1),
            'max': round(float(s.max()), 1),
            'std': round(float(s.std()), 1) if len(s) > 1 else 0,
        }

    return {'当期': stats(cur_px), '同期': stats(yoy_px)}


# ──────────────────────────────────────────
# 4. 11 地市分布
# ──────────────────────────────────────────


def query_city_breakdown(conn, match_field, kw,
                         period_start, period_end,
                         yoy_start, yoy_end, mom_start, mom_end):
    """11 地市 当/同/环 货值。"""
    def window(s, e):
        df = pd.read_sql(f"""
            SELECT 上线城市 AS 地市,
                   COUNT(*) AS 台数,
                   ROUND(SUM(最新分销价)/10000, 2) AS 货值_万
              FROM product_flow_v
             WHERE "{match_field}" LIKE ?
               AND 上线年月 BETWEEN ? AND ?
               AND 上线城市 IS NOT NULL AND 上线城市 != ''
               AND 上线城市 NOT LIKE '%***%'
             GROUP BY 1
        """, conn, params=[f'%{kw}%', s, e])
        return df

    cur = window(period_start, period_end).set_index('地市')
    yoy = window(yoy_start, yoy_end).set_index('地市')
    mom = window(mom_start, mom_end).set_index('地市')

    cities = sorted(set(cur.index) | set(yoy.index) | set(mom.index))
    rows = []
    for c in cities:
        cv = float(cur.loc[c, '货值_万']) if c in cur.index else 0
        yv = float(yoy.loc[c, '货值_万']) if c in yoy.index else 0
        mv = float(mom.loc[c, '货值_万']) if c in mom.index else 0
        n = int(cur.loc[c, '台数']) if c in cur.index else 0
        rows.append({
            '地市': c, '台数': n, '货值_万': cv,
            '同期_万': yv, '环期_万': mv,
            '同比': fmt_pct(calc_yoy(cv, yv)),
            '环比': fmt_pct(calc_yoy(cv, mv)),
        })
    rows.sort(key=lambda r: -r['货值_万'])
    return rows


# ──────────────────────────────────────────
# 5. Top 30 区县
# ──────────────────────────────────────────


def query_district_breakdown(conn, match_field, kw,
                             period_start, period_end,
                             yoy_start, yoy_end, city, top_n=30):
    city_sql = ' AND 上线城市 = ?' if city else ''
    city_p = [city] if city else []

    def window(s, e):
        df = pd.read_sql(f"""
            SELECT 上线区县_全 AS 区县, 上线城市 AS 地市,
                   COUNT(*) AS 台数,
                   ROUND(SUM(最新分销价)/10000, 2) AS 货值_万
              FROM product_flow_v
             WHERE "{match_field}" LIKE ?
               AND 上线年月 BETWEEN ? AND ?
               AND 上线区县_全 IS NOT NULL
               {city_sql}
             GROUP BY 1, 2
        """, conn, params=[f'%{kw}%', s, e, *city_p])
        return df

    cur = window(period_start, period_end)
    if cur.empty:
        return []
    yoy = window(yoy_start, yoy_end).set_index('区县')

    cur = cur.sort_values('货值_万', ascending=False).head(top_n)
    rows = []
    for _, r in cur.iterrows():
        yv = float(yoy.loc[r['区县'], '货值_万']) if r['区县'] in yoy.index else 0
        rows.append({
            '区县': r['区县'], '地市': r['地市'],
            '台数': int(r['台数']), '货值_万': float(r['货值_万']),
            '同期_万': yv,
            '同比': fmt_pct(calc_yoy(float(r['货值_万']), yv)),
        })
    return rows


# ──────────────────────────────────────────
# 6. Top 代理商
# ──────────────────────────────────────────


def query_dealer_breakdown(conn, match_field, kw,
                           period_start, period_end,
                           yoy_start, yoy_end, city, top_n=15):
    city_sql = ' AND 上线城市 = ?' if city else ''
    city_p = [city] if city else []

    def window(s, e):
        df = pd.read_sql(f"""
            SELECT
              CASE WHEN 所属一级客户 IS NULL OR 所属一级客户 = ''
                   THEN '(无签约代理商)' ELSE 所属一级客户 END AS 代理商,
              COUNT(*) AS 台数,
              ROUND(SUM(最新分销价)/10000, 2) AS 货值_万
            FROM product_flow_v
            WHERE "{match_field}" LIKE ?
              AND 上线年月 BETWEEN ? AND ?
              {city_sql}
            GROUP BY 1
        """, conn, params=[f'%{kw}%', s, e, *city_p])
        return df

    cur = window(period_start, period_end)
    if cur.empty:
        return []
    cur_total = cur['货值_万'].sum() or 1
    yoy = window(yoy_start, yoy_end).set_index('代理商')

    cur = cur.sort_values('货值_万', ascending=False).head(top_n)
    rows = []
    for _, r in cur.iterrows():
        yv = float(yoy.loc[r['代理商'], '货值_万']) if r['代理商'] in yoy.index else 0
        rows.append({
            '代理商': r['代理商'], '台数': int(r['台数']),
            '货值_万': float(r['货值_万']),
            '同期_万': yv,
            '同比': fmt_pct(calc_yoy(float(r['货值_万']), yv)),
            '份额': round(float(r['货值_万']) / cur_total * 100, 1),
        })
    return rows


# ──────────────────────────────────────────
# 7. 服务商生态(install_redpack 口径,去马甲)
# ──────────────────────────────────────────


def get_match_serials(conn, match_field, kw):
    """先从 product_flow 里取出命中的产品序列号集合,作为 install_redpack 过滤条件。"""
    df = pd.read_sql(f"""
        SELECT DISTINCT 产品序列号
          FROM product_flow_v
         WHERE "{match_field}" LIKE ?
    """, conn, params=[f'%{kw}%'])
    return df['产品序列号'].tolist()


def query_provider_breakdown(conn, match_field, kw,
                             period_start, period_end,
                             yoy_start, yoy_end, mom_start, mom_end,
                             city, top_n=30):
    """红包扫码口径的 Top 服务商 + 客户类型 + 渠道健康度。"""
    city_sql = ' AND 上线客户地市 = ?' if city else ''
    city_p = [city] if city else []

    base_sql = f"""
        SELECT rp.上线客户编码 AS 编码,
               rp.上线客户名称 AS 服务商,
               rp.上线客户地市 AS 地市,
               rp.上线客户渠道客户类型 AS 客户类型,
               rp.所属一级客户 AS 签约代理商,
               rp.签约状态,
               COUNT(*) AS 台数,
               ROUND(SUM(rp.产品现有分销价)/10000, 2) AS 货值_万,
               cp.客户编码 IS NOT NULL AS is_closed
          FROM install_redpack_v rp
          LEFT JOIN vest_account va
                 ON va.服务商客户编码 = rp.上线客户编码
          LEFT JOIN closed_provider cp
                 ON cp.客户编码 = rp.上线客户编码
         WHERE rp.[内部型号] LIKE ? -- placeholder, will be replaced
           AND rp.上线年月 BETWEEN ? AND ?
           AND va.服务商客户编码 IS NULL
           {city_sql}
           AND rp.上线客户编码 IS NOT NULL AND rp.上线客户编码 != ''
         GROUP BY 1, 2, 3, 4, 5, 6
    """

    # install_redpack 没有 内部/外部 型号字段;关联用 产品序列号
    serials = get_match_serials(conn, match_field, kw)
    if not serials:
        return {
            'top_providers': [],
            'kind_dist': [],
            'sign_dist': [],
            'health': {'cur_set': 0, 'yoy_set': 0, 'new': 0, 'churn': 0,
                       'mom_set': 0, 'mom_new': 0, 'mom_churn': 0},
        }

    # SQLite IN list 限制(默认 999),分批
    def window(s, e, batch=900):
        out = []
        for i in range(0, len(serials), batch):
            chunk = serials[i:i + batch]
            ph = ','.join('?' * len(chunk))
            sql = f"""
                SELECT rp.上线客户编码 AS 编码,
                       rp.上线客户名称 AS 服务商,
                       rp.上线客户地市 AS 地市,
                       rp.上线客户渠道客户类型 AS 客户类型,
                       rp.所属一级客户 AS 签约代理商,
                       rp.签约状态,
                       COUNT(*) AS 台数,
                       SUM(rp.产品现有分销价) AS 货值,
                       MAX(CASE WHEN cp.客户编码 IS NOT NULL THEN 1 ELSE 0 END) AS is_closed
                  FROM install_redpack_v rp
                  LEFT JOIN vest_account va
                         ON va.服务商客户编码 = rp.上线客户编码
                  LEFT JOIN closed_provider cp
                         ON cp.客户编码 = rp.上线客户编码
                 WHERE rp.产品序列号 IN ({ph})
                   AND rp.上线年月 BETWEEN ? AND ?
                   AND va.服务商客户编码 IS NULL
                   {city_sql}
                   AND rp.上线客户编码 IS NOT NULL AND rp.上线客户编码 != ''
                 GROUP BY 1, 2, 3, 4, 5, 6
            """
            df = pd.read_sql(sql, conn, params=[*chunk, s, e, *city_p])
            out.append(df)
        if not out:
            return pd.DataFrame()
        ret = pd.concat(out, ignore_index=True)
        # 多批 union 后还要再 group
        if len(out) > 1 and not ret.empty:
            ret = ret.groupby(['编码', '服务商', '地市', '客户类型', '签约代理商', '签约状态'],
                              as_index=False).agg(
                台数=('台数', 'sum'),
                货值=('货值', 'sum'),
                is_closed=('is_closed', 'max'),
            )
        return ret

    cur = window(period_start, period_end)
    yoy = window(yoy_start, yoy_end)
    mom = window(mom_start, mom_end)

    # Top 30 服务商
    top_providers = []
    if not cur.empty:
        cur_sorted = cur.sort_values('货值', ascending=False).head(top_n)
        for _, r in cur_sorted.iterrows():
            top_providers.append({
                '服务商': r['服务商'],
                '编码': r['编码'],
                '地市': r['地市'],
                '客户类型': r['客户类型'] or '未签约',
                '签约状态': r['签约状态'] or '无签约',
                '签约代理商': r['签约代理商'] or '',
                '已关闭': bool(r['is_closed']),
                '台数': int(r['台数']),
                '货值_万': round(float(r['货值']) / 10000, 2),
            })

    # 客户类型分布
    kind_dist = []
    if not cur.empty:
        kg = cur.groupby(cur['客户类型'].fillna('未签约')).agg(
            服务商数=('编码', 'nunique'),
            台数=('台数', 'sum'),
            货值=('货值', 'sum'),
        ).reset_index().rename(columns={'客户类型': '类型'})
        total_v = kg['货值'].sum() or 1
        for _, r in kg.sort_values('货值', ascending=False).iterrows():
            kind_dist.append({
                '类型': r['类型'],
                '服务商数': int(r['服务商数']),
                '台数': int(r['台数']),
                '货值_万': round(float(r['货值']) / 10000, 2),
                '货值占比': round(float(r['货值']) / total_v * 100, 1),
            })

    # 签约状态分布
    sign_dist = []
    if not cur.empty:
        sg = cur.groupby(cur['签约状态'].fillna('无签约')).agg(
            服务商数=('编码', 'nunique'),
            台数=('台数', 'sum'),
            货值=('货值', 'sum'),
        ).reset_index().rename(columns={'签约状态': '状态'})
        total_v = sg['货值'].sum() or 1
        for _, r in sg.sort_values('货值', ascending=False).iterrows():
            sign_dist.append({
                '状态': r['状态'],
                '服务商数': int(r['服务商数']),
                '台数': int(r['台数']),
                '货值_万': round(float(r['货值']) / 10000, 2),
                '货值占比': round(float(r['货值']) / total_v * 100, 1),
            })

    # 渠道健康度
    cur_set = set(cur['编码']) if not cur.empty else set()
    yoy_set = set(yoy['编码']) if not yoy.empty else set()
    mom_set = set(mom['编码']) if not mom.empty else set()
    health = {
        'cur_set': len(cur_set),
        'yoy_set': len(yoy_set),
        'mom_set': len(mom_set),
        'new': len(cur_set - yoy_set),     # 同比新增
        'churn': len(yoy_set - cur_set),    # 同比流失
        'mom_new': len(cur_set - mom_set),  # 环比新增
        'mom_churn': len(mom_set - cur_set),
    }

    return {
        'top_providers': top_providers,
        'kind_dist': kind_dist,
        'sign_dist': sign_dist,
        'health': health,
    }


# ──────────────────────────────────────────
# 8. 跨地域流向
# ──────────────────────────────────────────


def query_cross_geo(conn, match_field, kw, period_start, period_end, city):
    city_sql = ' AND 上线城市 = ?' if city else ''
    city_p = [city] if city else []

    df = pd.read_sql(f"""
        SELECT
          SUM(CASE WHEN 是否异城 = 1 THEN 1 ELSE 0 END) AS 异城台,
          SUM(CASE WHEN 是否异省 = 1 THEN 1 ELSE 0 END) AS 异省台,
          COUNT(*) AS 总台
        FROM product_flow_v
        WHERE "{match_field}" LIKE ?
          AND 上线年月 BETWEEN ? AND ?
          {city_sql}
    """, conn, params=[f'%{kw}%', period_start, period_end, *city_p])

    if df.empty or not df.iloc[0]['总台']:
        return {'rate_diff_city': None, 'rate_diff_prov': None, 'top_paths': []}

    r = df.iloc[0]
    total = r['总台']

    # Top 路径
    paths = pd.read_sql(f"""
        SELECT 出货客户城市 AS 出货, 上线城市 AS 上线, COUNT(*) AS 台数
          FROM product_flow_v
         WHERE "{match_field}" LIKE ?
           AND 上线年月 BETWEEN ? AND ?
           AND 是否异城 = 1
           {city_sql}
         GROUP BY 1, 2
         ORDER BY 3 DESC
         LIMIT 5
    """, conn, params=[f'%{kw}%', period_start, period_end, *city_p])

    return {
        'total_n': int(total),
        'diff_city_n': int(r['异城台'] or 0),
        'diff_prov_n': int(r['异省台'] or 0),
        'rate_diff_city': round((r['异城台'] or 0) / total * 100, 1),
        'rate_diff_prov': round((r['异省台'] or 0) / total * 100, 1),
        'top_paths': paths.to_dict(orient='records'),
    }


# ──────────────────────────────────────────
# 9. 自动诊断
# ──────────────────────────────────────────


def auto_diagnose(out):
    """根据各章节数据出 2-5 条诊断 + 1 个状态评级。"""
    meta = out['_meta']
    total = out.get('sku_total', {})
    trend = out.get('monthly_trend', [])
    px = out.get('price_dist', {})
    cities = out.get('city_breakdown', [])
    providers = out.get('provider_data', {})
    cross = out.get('cross_geo', {})

    findings = []
    rating = '🟡'  # 默认持平

    yoy = total.get('同比') if total else None
    px_drop = total.get('均价同比') if total else None

    # 月销翻倍?
    bull_recent = False
    if trend and len(trend) >= 3:
        last = trend[-1]['货值_万']
        prev_avg = (trend[-2]['货值_万'] + trend[-3]['货值_万']) / 2 if len(trend) >= 3 else 0
        if prev_avg > 0 and last / prev_avg >= 1.5:
            bull_recent = True

    # 评级
    if yoy is not None and yoy >= 50 and bull_recent:
        rating = '🔥'
        findings.append({
            'tone': 'bull',
            'msg': f'**爆款上扬**:整体同比 +{yoy:.0f}%,最近月销翻倍。',
        })
    elif yoy is not None and yoy >= 10:
        rating = '📈'
        findings.append({
            'tone': 'good',
            'msg': f'**稳健增长**:同比 +{yoy:.0f}%。',
        })
    elif yoy is not None and yoy <= -20:
        rating = '⚠️'
        findings.append({
            'tone': 'bad',
            'msg': f'**衰退预警**:同比 {yoy:.0f}%。',
        })
    elif total.get('同期货值', 0) == 0 and total.get('货值', 0) > 0:
        rating = '🟢'
        findings.append({
            'tone': 'info',
            'msg': f'**新品爬坡**:同期(去年同月)无销量,本期 ¥{total["货值"]/10000:.0f} 万。',
        })

    # 降价提示
    if px_drop is not None and px_drop <= -10:
        findings.append({
            'tone': 'warn',
            'msg': f'**降价拉动**:均价同比 {px_drop:.0f}%,警惕透支单价。',
        })

    # 集中度
    if cities:
        top_share = cities[0]['货值_万'] / sum(c['货值_万'] for c in cities) if cities else 0
        if top_share > 0.5:
            findings.append({
                'tone': 'warn',
                'msg': f'**区域过度集中**:Top1 地市 {cities[0]["地市"]} 占 {top_share*100:.0f}%,可加大其他地市拓展。',
            })

    # 服务商扩散
    h = providers.get('health', {}) if providers else {}
    if h.get('cur_set') and h.get('new'):
        new_share = h['new'] / h['cur_set']
        if new_share >= 0.3:
            findings.append({
                'tone': 'good',
                'msg': f'**正在扩散**:{h["cur_set"]} 家服务商里 {h["new"]} 家(同比新增,{new_share*100:.0f}%)。',
            })

    # 跨渠道
    sign = providers.get('sign_dist', []) if providers else []
    for s in sign:
        if s['状态'] == '跨渠道采购' and s['货值占比'] >= 30:
            findings.append({
                'tone': 'warn',
                'msg': f'**价格穿透**:{s["货值占比"]:.0f}% 货值来自跨渠道采购,代理商话语权弱。',
            })
            break

    return {'rating': rating, 'findings': findings}


# ──────────────────────────────────────────
# 入口
# ──────────────────────────────────────────


def run(keyword: str, period_start: str, period_end: str,
        city: str | None = None, match_field: str | None = None) -> dict:
    conn = sqlite3.connect(str(DB))

    field, hit_rows = resolve_match_field(conn, keyword, match_field)
    if hit_rows == 0:
        conn.close()
        return {
            '_meta': {
                'keyword': keyword, 'period_start': period_start, 'period_end': period_end,
                'city': city or '全省', 'match_field': field, 'hit_rows': 0,
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            },
            'error': f'关键字 "{keyword}" 在内部型号/外部型号/产品名称 均无命中。',
        }

    yoy_start, yoy_end = shift_period(period_start, period_end, -12)
    n = n_months(period_start, period_end)
    mom_start, mom_end = shift_period(period_start, period_end, -n)

    print(f'[runner] kw={keyword} match_field={field} ({hit_rows} rows)', file=sys.stderr)
    print(f'  评估期: {period_start}~{period_end}({n} 月)', file=sys.stderr)
    print(f'  同期: {yoy_start}~{yoy_end}', file=sys.stderr)
    print(f'  环期: {mom_start}~{mom_end}', file=sys.stderr)

    out = {
        '_meta': {
            'keyword': keyword,
            'match_field': field,
            'hit_rows_all_time': hit_rows,
            'period_start': period_start, 'period_end': period_end,
            'yoy_start': yoy_start, 'yoy_end': yoy_end,
            'mom_start': mom_start, 'mom_end': mom_end,
            'n_months': n,
            'city': city or '全省',
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        }
    }

    sku_list, sku_total = query_sku_list(
        conn, field, keyword, period_start, period_end,
        yoy_start, yoy_end, mom_start, mom_end, city,
    )
    out['sku_list'] = sku_list
    out['sku_total'] = sku_total
    print(f'  SKU: {len(sku_list)} 个,合计 {sku_total["台数"]} 台 / ¥{sku_total["货值"]/10000:.1f}万', file=sys.stderr)

    out['monthly_trend'] = query_monthly_trend(
        conn, field, keyword, period_end, city,
    )
    out['price_dist'] = query_price_dist(
        conn, field, keyword, period_start, period_end, yoy_start, yoy_end, city,
    )
    out['city_breakdown'] = query_city_breakdown(
        conn, field, keyword, period_start, period_end,
        yoy_start, yoy_end, mom_start, mom_end,
    )
    out['district_breakdown'] = query_district_breakdown(
        conn, field, keyword, period_start, period_end,
        yoy_start, yoy_end, city,
    )
    out['dealer_breakdown'] = query_dealer_breakdown(
        conn, field, keyword, period_start, period_end, yoy_start, yoy_end, city,
    )
    out['provider_data'] = query_provider_breakdown(
        conn, field, keyword, period_start, period_end,
        yoy_start, yoy_end, mom_start, mom_end, city,
    )
    out['cross_geo'] = query_cross_geo(
        conn, field, keyword, period_start, period_end, city,
    )

    out['diagnose'] = auto_diagnose(out)

    conn.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kw', required=True, help='匹配关键字')
    ap.add_argument('--period-start', required=True)
    ap.add_argument('--period-end', required=True)
    ap.add_argument('--city', default=None)
    ap.add_argument('--match-field', default=None,
                    choices=['内部型号', '外部型号', '产品名称'])
    ap.add_argument('--out', default='/tmp/hotsku_data.json')
    args = ap.parse_args()

    data = run(args.kw, args.period_start, args.period_end,
               city=args.city, match_field=args.match_field)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2,
                                         default=str), encoding='utf-8')
    print(f'✅ 输出 {args.out}', file=sys.stderr)


if __name__ == '__main__':
    main()
