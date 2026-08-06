"""地市月度经营报告 — 数据汇总模块（本地 Claude Code 版，对标全省月报架构）

定位：单个地市的月度诊断报告。与全省月报（_monthly_province_report.py）同架构、同口径，
仅范围从「11地市」收敛到「单市」。大量复用 province 引擎的下钻函数。

三大模块：
  ① 该市 SO 3 月趋势（同比+环比）+ 区县下钻（哪个区县好/差）
  ② 代理商（出货+交易服务商数）/ 服务商等级结构(官方原始 V0-V5) / 跑动+推广会
     —— 直接复用 _monthly_province_report.drilldown_city(city, months)
  ③ 三大专项在该市的趋势 + 下降专项下钻
     —— 复用 _focus_section.gather_focus_data(conn, focus, ..., city=city)

口径与全省版完全一致：SO=全量感知(product_flow_v.最新分销价)；
服务商等级=provider_contract 官方原始等级(26年官方评定 V0-V5，区别于货值档)；
区县口径 product_flow_v.上线区县 / install_redpack_v.上线客户区县。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent))
from _monthly_province_report import (  # noqa: E402  复用全省引擎
    _m, _last_n_months, _pct, drilldown_city, FOCUS_LIST,
)
from _focus_section import gather_focus_data  # noqa: E402

DB_PATH_DEFAULT = Path(__file__).parent.parent / 'db' / 'product_flow.db'


# ══════════════════════════════════════════════
# 模块 ① 该市 SO 趋势 + 区县下钻
# ══════════════════════════════════════════════

def query_city_trend(conn, city: str, end_month: str = '2026-05', n_trend: int = 3) -> dict:
    """该市 SO 分月趋势（全量感知），同比（去年同月）+ 环比（上月）。"""
    months = _last_n_months(end_month, n_trend)
    yoy_months = [_m(x, -12) for x in months]
    all_m = months + yoy_months
    mph = ','.join('?' * len(all_m))

    df = pd.read_sql(f"""
        SELECT 上线年月 AS 月, ROUND(SUM(最新分销价) / 10000, 1) AS SO万
          FROM product_flow_v
         WHERE 上线城市 = ? AND 上线年月 IN ({mph})
         GROUP BY 上线年月
    """, conn, params=(city, *all_m))
    series = {r['月']: float(r['SO万']) for _, r in df.iterrows()}

    cur_m, prev_m = months[-1], (months[-2] if len(months) >= 2 else months[-1])
    yoy_cur_m = _m(cur_m, -12)
    so_cur, so_prev, so_yoy = series.get(cur_m), series.get(prev_m), series.get(yoy_cur_m)

    return {
        '地市': city,
        '趋势月份': months, '同比月份': yoy_months,
        '当期月': cur_m, '上月': prev_m,
        '分月SO': {m: series.get(m) for m in months},
        '同比分月': {ym: series.get(ym) for ym in yoy_months},
        '本月SO': so_cur, '上月SO': so_prev, '去年同月SO': so_yoy,
        '本月同比': _pct(so_cur, so_yoy),
        '本月环比': _pct(so_cur, so_prev),
    }


def query_district_trend(conn, city: str, end_month: str = '2026-05', n_trend: int = 3) -> dict:
    """该市各区县 SO 3 月趋势（全量感知），分好/差区县。"""
    months = _last_n_months(end_month, n_trend)
    yoy_cur_m = _m(months[-1], -12)
    all_m = months + [yoy_cur_m]
    mph = ','.join('?' * len(all_m))

    df = pd.read_sql(f"""
        SELECT 上线区县 AS 区县, 上线年月 AS 月,
               ROUND(SUM(最新分销价) / 10000, 1) AS SO万
          FROM product_flow_v
         WHERE 上线城市 = ? AND 上线年月 IN ({mph})
           AND 上线区县 IS NOT NULL AND 上线区县 != ''
         GROUP BY 上线区县, 上线年月
    """, conn, params=(city, *all_m))

    pivot = {}
    for _, r in df.iterrows():
        pivot.setdefault(r['区县'], {})[r['月']] = float(r['SO万'])

    cur_m, prev_m = months[-1], (months[-2] if len(months) >= 2 else months[-1])
    rows = []
    for dist, s in pivot.items():
        so_cur = s.get(cur_m, 0.0)
        so_prev = s.get(prev_m)
        so_yoy = s.get(yoy_cur_m)
        rows.append({
            '区县': dist,
            '分月SO': {m: s.get(m, 0.0) for m in months},
            '本月SO': so_cur, '上月SO': so_prev, '去年同月SO': so_yoy,
            '本月同比': _pct(so_cur, so_yoy),
            '本月环比': _pct(so_cur, so_prev),
        })
    rows.sort(key=lambda x: x['本月SO'], reverse=True)
    # 好/差：按本月环比
    ranked = sorted([r for r in rows if r['本月环比'] is not None],
                    key=lambda x: x['本月环比'])
    return {
        '趋势月份': months,
        '区县明细': rows,
        '区县数': len(rows),
        '增长_top': [{'区县': r['区县'], '本月SO': r['本月SO'], '本月环比': r['本月环比']}
                     for r in ranked[::-1][:3]],
        '下滑_top': [{'区县': r['区县'], '本月SO': r['本月SO'], '本月环比': r['本月环比']}
                     for r in ranked[:3]],
    }


# ══════════════════════════════════════════════
# 模块 ③ 三大专项在该市的趋势 + 下降下钻
# ══════════════════════════════════════════════

def query_city_focus(conn, city: str, end_month: str = '2026-05', n_trend: int = 3) -> dict:
    """三专项在该市的月度出货趋势 + 本月环比；下降专项下钻代理商/服务商。"""
    months = _last_n_months(end_month, n_trend)
    cur_m, prev_m = months[-1], (months[-2] if len(months) >= 2 else months[-1])
    out = {'地市': city, '趋势月份': months, '当期月': cur_m, '专项': []}

    for focus in FOCUS_LIST:
        # 该市各月出货（product_flow_v join product_focus，限定城市）
        mph = ','.join('?' * len(months))
        tr = pd.read_sql(f"""
            SELECT pf.上线年月 AS 月,
                   ROUND(SUM(pf.最新分销价) / 10000, 1) AS 出货万
              FROM product_flow_v pf JOIN product_focus fc ON fc.物料号 = pf.物料号
             WHERE fc.专项 = ? AND pf.上线城市 = ? AND pf.上线年月 IN ({mph})
             GROUP BY pf.上线年月
        """, conn, params=(focus, city, *months))
        series = {r['月']: float(r['出货万']) for _, r in tr.iterrows()}
        cur_so, prev_so = series.get(cur_m, 0.0), series.get(prev_m, 0.0)
        mom = _pct(cur_so, prev_so)
        item = {
            '专项': focus,
            '分月出货万': {m: series.get(m, 0.0) for m in months},
            '本月出货万': cur_so, '上月出货万': prev_so, '本月环比': mom,
            '是否下降': mom is not None and mom < 0,
        }
        if item['是否下降']:
            item['下钻'] = _city_focus_drill(conn, focus, city, cur_m, prev_m)
        out['专项'].append(item)

    out['下降专项'] = [x['专项'] for x in out['专项'] if x['是否下降']]
    return out


def _city_focus_drill(conn, focus, city, cur_m, prev_m) -> dict:
    """该市下降专项：代理商 / 服务商 本月 vs 上月。"""
    # 代理商
    dealer = pd.read_sql("""
        SELECT pf.出库客户名称 AS 代理商, pf.上线年月 AS 月,
               ROUND(SUM(pf.最新分销价) / 10000, 1) AS 出货万
          FROM product_flow_v pf JOIN product_focus fc ON fc.物料号 = pf.物料号
         WHERE fc.专项 = ? AND pf.上线城市 = ? AND pf.上线年月 IN (?, ?)
           AND pf.出库客户名称 IS NOT NULL AND pf.出库客户名称 != ''
         GROUP BY pf.出库客户名称, pf.上线年月
    """, conn, params=(focus, city, cur_m, prev_m))
    dcur = {r['代理商']: float(r['出货万']) for _, r in dealer[dealer['月'] == cur_m].iterrows()}
    dprev = {r['代理商']: float(r['出货万']) for _, r in dealer[dealer['月'] == prev_m].iterrows()}
    dealer_rows = sorted(
        [{'代理商': k, '本月出货万': round(dcur.get(k, 0), 1),
          '上月出货万': round(dprev.get(k, 0), 1),
          '环比_万': round(dcur.get(k, 0) - dprev.get(k, 0), 1)}
         for k in set(dcur) | set(dprev)],
        key=lambda x: x['环比_万'])

    # 服务商
    prov = pd.read_sql("""
        SELECT ir.上线客户编码 AS 客户编码, MAX(ir.上线客户名称) AS 客户名称,
               MAX(ir.上线客户区县) AS 区县, ir.上线年月 AS 月,
               ROUND(SUM(ir.产品现有分销价) / 10000, 2) AS 上线万
          FROM install_redpack_v ir JOIN product_focus fc ON fc.物料号 = ir.物料号
         WHERE fc.专项 = ? AND ir.上线客户地市 = ? AND ir.上线年月 IN (?, ?)
           AND ir.上线客户编码 IS NOT NULL AND ir.上线客户编码 != ''
         GROUP BY ir.上线客户编码, ir.上线年月
    """, conn, params=(focus, city, cur_m, prev_m))
    meta = {r['客户编码']: (r['客户名称'], r['区县']) for _, r in prov.iterrows()}
    pcur = {r['客户编码']: float(r['上线万']) for _, r in prov[prov['月'] == cur_m].iterrows()}
    pprev = {r['客户编码']: float(r['上线万']) for _, r in prov[prov['月'] == prev_m].iterrows()}
    prov_rows = []
    for k in set(pcur) | set(pprev):
        nm, dist = meta.get(k, (None, None))
        cs, ps = pcur.get(k, 0.0), pprev.get(k, 0.0)
        if ps - cs > 0:
            prov_rows.append({'客户编码': k, '客户名称': nm, '区县': dist,
                              '上月上线万': round(ps, 2), '本月上线万': round(cs, 2),
                              '掉幅万': round(ps - cs, 2),
                              '状态': '🔴 归零' if cs == 0 else '🟡 下滑'})
    prov_rows.sort(key=lambda x: x['掉幅万'], reverse=True)

    return {'代理商下降': dealer_rows[:10], '服务商下降': prov_rows[:15]}


# ══════════════════════════════════════════════
# 总入口
# ══════════════════════════════════════════════

def gather_city_report(city: str, end_month: str = '2026-05', n_trend: int = 3,
                       db_path: Path = None, min_dealer_wan: float = 5.0) -> dict:
    """地市月报数据汇总。"""
    db_path = db_path or DB_PATH_DEFAULT
    conn = sqlite3.connect(str(db_path))
    try:
        months = _last_n_months(end_month, n_trend)
        trend = query_city_trend(conn, city, end_month, n_trend)
        district = query_district_trend(conn, city, end_month, n_trend)
        drill = drilldown_city(conn, city, months, min_dealer_wan=min_dealer_wan)  # 复用全省引擎
        focus = query_city_focus(conn, city, end_month, n_trend)
        return {
            '地市': city, '本月': end_month, '趋势月份': months,
            '口径说明': 'SO=全量感知(product_flow_v.最新分销价)；'
                        '服务商等级=provider_contract官方原始等级(V0-V5)；'
                        '区县口径=上线区县/上线客户区县。',
            '模块1_趋势': trend,
            '模块1_区县': district,
            '模块2_下钻': drill,
            '模块3_专项': focus,
        }
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument('--city', required=True)
    ap.add_argument('--end', default='2026-05')
    ap.add_argument('--n', type=int, default=3)
    ap.add_argument('--min-wan', type=float, default=5.0)
    ap.add_argument('--db', default=None)
    args = ap.parse_args()
    rep = gather_city_report(args.city, args.end, args.n,
                             db_path=Path(args.db) if args.db else None,
                             min_dealer_wan=args.min_wan)
    print(json.dumps(rep, ensure_ascii=False, default=str))
