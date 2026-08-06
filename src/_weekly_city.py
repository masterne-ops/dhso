"""地市周报 — 单市数据汇总（按 7 天窗口）

对标地市月报维度,但时间粒度=周。复用省周报(_weekly_prov)的窗口/下钻/推广会函数。
模块:
  ① 该市周趋势(最近n周 SO)+本周环比 + 当月完成率(进度条) + YTD累计同比
  ② 区县周动态(各区县本周SO+周环比,增长/下滑top)
  ③ 代理商周动态(各代理商本周出货SO列表,按本周降序,环比涨绿跌红)+ 周跑动(打卡环比,不剔除异常)  —— 复用 query_city_week_drill
  ④ 三专项该市周趋势(台数)+本周环比 + 推广会(该市,按参会客户城市归属)

口径:SO=全量感知;完成率=当月累计÷(年目标×当月节奏);专项用台数;
***=外省代理商在本省出货(复用省周报改写)。
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent))
from _monthly_province_report import _pct  # noqa: E402
from _weekly_prov import (_week_windows, query_city_week_drill, _cities_ph, query_np_transfer,  # noqa: E402
                          query_provider_kpi, query_activation_v2, query_visit_split,
                          query_provider_by_dealer)

DB_PATH_DEFAULT = Path(__file__).parent.parent / 'db' / 'product_flow.db'


# ── ① 该市周趋势 + 完成率 + YTD同比 ──
def query_city_trend(conn, city: str, week_end: str, n: int = 6, year: int = 2026) -> dict:
    wins = _week_windows(week_end, n)
    labels, vals = [], []
    for s, e in wins:
        df = pd.read_sql("""
            SELECT ROUND(SUM(最新分销价)/10000,1) so FROM product_flow_v
             WHERE 上线城市=? AND date(上线时间) BETWEEN ? AND ?
        """, conn, params=(city, s, e))
        vals.append(float(df.iloc[0]['so'] or 0))
        labels.append(f"{s[5:]}~{e[5:]}")
    cur, prev = vals[-1], (vals[-2] if len(vals) >= 2 else None)

    # 全省位次（本周）
    ph = _cities_ph()
    from _monthly_province_report import ZHEJIANG_CITIES
    cur_s = wins[-1][0]
    rankdf = pd.read_sql(f"""
        SELECT 上线城市 市, SUM(最新分销价) so FROM product_flow_v
         WHERE 上线城市 IN ({ph}) AND date(上线时间) BETWEEN ? AND ?
         GROUP BY 上线城市 ORDER BY so DESC
    """, conn, params=(*ZHEJIANG_CITIES, cur_s, week_end))
    rank = [r['市'] for _, r in rankdf.iterrows()]
    pos = rank.index(city) + 1 if city in rank else None

    # 当月完成率
    cur_month = week_end[:7]
    month_num = int(cur_month[5:7])
    mon = pd.read_sql("""
        SELECT ROUND(SUM(最新分销价)/10000,1) so FROM product_flow_v
         WHERE 上线城市=? AND 上线年月=?
    """, conn, params=(city, cur_month)).iloc[0]['so']
    mon = float(mon or 0)
    tgt = pd.read_sql("SELECT SUM(SO目标_万) t FROM kpi_targets WHERE 年度=? AND 城市=?",
                      conn, params=(year, city)).iloc[0]['t']
    tgt = float(tgt or 0)
    rhy = pd.read_sql("SELECT 占比 FROM kpi_rhythm WHERE 指标 LIKE '省区SO进度条%' AND 年度=? AND 月份=?",
                      conn, params=(year, month_num))
    pace = float(rhy.iloc[0]['占比']) if not rhy.empty else None
    should = round(tgt * pace, 1) if pace else None
    rate = round(mon / should, 3) if (should and should > 0) else None
    first = pd.Timestamp(cur_month + '-01')
    last_day = (first + pd.offsets.MonthEnd(0)).day
    time_progress = round(((pd.Timestamp(week_end) - first).days + 1) / last_day, 3)

    # YTD 同比
    cur_y_s = f'{year}-01-01'
    prev_y_s = f'{year-1}-01-01'
    prev_y_e = (pd.Timestamp(week_end) - pd.DateOffset(years=1)).strftime('%Y-%m-%d')
    ytd_cur = float(pd.read_sql("""
        SELECT ROUND(SUM(最新分销价)/10000,1) so FROM product_flow_v
         WHERE 上线城市=? AND date(上线时间) BETWEEN ? AND ?
    """, conn, params=(city, cur_y_s, week_end)).iloc[0]['so'] or 0)
    ytd_prev = float(pd.read_sql("""
        SELECT ROUND(SUM(最新分销价)/10000,1) so FROM product_flow_v
         WHERE 上线城市=? AND date(上线时间) BETWEEN ? AND ?
    """, conn, params=(city, prev_y_s, prev_y_e)).iloc[0]['so'] or 0)

    # 当月同比（本月1号~week_end 当天累计 vs 去年同月同期）
    mtd_cur_s = cur_month + '-01'
    mtd_prev_s = (pd.Timestamp(mtd_cur_s) - pd.DateOffset(years=1)).strftime('%Y-%m-%d')
    mtd_prev_e = (pd.Timestamp(week_end) - pd.DateOffset(years=1)).strftime('%Y-%m-%d')
    mtd_cur = float(pd.read_sql("""
        SELECT ROUND(SUM(最新分销价)/10000,1) so FROM product_flow_v
         WHERE 上线城市=? AND date(上线时间) BETWEEN ? AND ?
    """, conn, params=(city, mtd_cur_s, week_end)).iloc[0]['so'] or 0)
    mtd_prev = float(pd.read_sql("""
        SELECT ROUND(SUM(最新分销价)/10000,1) so FROM product_flow_v
         WHERE 上线城市=? AND date(上线时间) BETWEEN ? AND ?
    """, conn, params=(city, mtd_prev_s, mtd_prev_e)).iloc[0]['so'] or 0)

    return {
        '地市': city, '标签': labels, '分周SO': vals,
        '本周区间': f"{wins[-1][0]} ~ {wins[-1][1]}",
        '本周SO': cur, '上周SO': prev, '本周环比': _pct(cur, prev),
        '全省位次': pos, '全省地市数': len(rank),
        '当月': cur_month, '当月累计': mon, '当月应达成': should, '当月完成率': rate,
        '当月时间进度': time_progress, '月节奏占比': pace,
        '当月今年': mtd_cur, '当月去年': mtd_prev, '当月同比': _pct(mtd_cur, mtd_prev),
        '当月今年区间': f"{mtd_cur_s} ~ {week_end}", '当月去年区间': f"{mtd_prev_s} ~ {mtd_prev_e}",
        'YTD今年': ytd_cur, 'YTD去年': ytd_prev, 'YTD累计同比': _pct(ytd_cur, ytd_prev),
        'YTD今年区间': f"{cur_y_s} ~ {week_end}", 'YTD去年区间': f"{prev_y_s} ~ {prev_y_e}",
    }


# ── ② 区县周动态 ──
def query_district_week(conn, city: str, week_end: str) -> dict:
    cur_s = (pd.Timestamp(week_end) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    prev_s = (pd.Timestamp(week_end) - pd.Timedelta(days=13)).strftime('%Y-%m-%d')
    prev_e = (pd.Timestamp(week_end) - pd.Timedelta(days=7)).strftime('%Y-%m-%d')

    def by_dist(s, e):
        df = pd.read_sql("""
            SELECT 上线区县 区县, ROUND(SUM(最新分销价)/10000,1) so FROM product_flow_v
             WHERE 上线城市=? AND date(上线时间) BETWEEN ? AND ?
               AND 上线区县 IS NOT NULL AND 上线区县!='' GROUP BY 上线区县
        """, conn, params=(city, s, e))
        return {r['区县']: float(r['so'] or 0) for _, r in df.iterrows()}
    cur, prev = by_dist(cur_s, week_end), by_dist(prev_s, prev_e)
    rows = []
    for d in set(cur) | set(prev):
        cv, pv = cur.get(d, 0), prev.get(d, 0)
        rows.append({'区县': d, '本周SO': cv, '上周SO': pv, '本周环比': _pct(cv, pv)})
    rows.sort(key=lambda x: x['本周SO'], reverse=True)
    ranked = sorted([r for r in rows if r['本周环比'] is not None], key=lambda x: x['本周环比'])
    return {'区县明细': rows, '区县数': len(rows),
            '增长_top': [{'区县': r['区县'], '本周SO': r['本周SO'], '本周环比': r['本周环比']} for r in ranked[::-1][:3]],
            '下滑_top': [{'区县': r['区县'], '本周SO': r['本周SO'], '本周环比': r['本周环比']} for r in ranked[:3]]}


# ── ④ 三专项该市周趋势(台数) + 推广会(该市) ──
def query_city_focus_week(conn, city: str, week_end: str, n: int = 6) -> dict:
    wins = _week_windows(week_end, n)
    out = {'标签': [f"{s[5:]}~{e[5:]}" for s, e in wins], '专项': []}
    for focus in ['夜视王', '无线', '场景化']:
        vals = []
        for s, e in wins:
            df = pd.read_sql("""
                SELECT COUNT(*) n FROM product_flow_v pf JOIN product_focus fc ON fc.物料号=pf.物料号
                 WHERE fc.专项=? AND pf.上线城市=? AND date(pf.上线时间) BETWEEN ? AND ?
            """, conn, params=(focus, city, s, e))
            vals.append(int(df.iloc[0]['n'] or 0))
        cur, prev = vals[-1], (vals[-2] if len(vals) >= 2 else None)
        out['专项'].append({'专项': focus, '分周台数': vals, '本周台数': cur, '上周台数': prev,
                            '本周环比': _pct(cur, prev), '是否下降': (_pct(cur, prev) or 0) < 0})
    return out


def query_city_promo_week(conn, city: str, week_end: str) -> dict:
    """该市推广会:参会客户编码 join provider_contract.客户城市 归属。"""
    cur_s = (pd.Timestamp(week_end) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    prev_s = (pd.Timestamp(week_end) - pd.Timedelta(days=13)).strftime('%Y-%m-%d')
    prev_e = (pd.Timestamp(week_end) - pd.Timedelta(days=7)).strftime('%Y-%m-%d')

    def pm(s, e):
        df = pd.read_sql("""
            SELECT COUNT(*) 参会人次, COUNT(DISTINCT pm.云商会议ID) 场次,
                   COUNT(DISTINCT pm.参会客户编码) 参会服务商
              FROM promotion_meeting pm JOIN provider_contract pc ON pc.客户编码=pm.参会客户编码
             WHERE pc.客户城市=? AND date(pm.活动开始时间) BETWEEN ? AND ?
        """, conn, params=(city, s, e))
        r = df.iloc[0]
        return {'参会人次': int(r['参会人次'] or 0), '场次': int(r['场次'] or 0),
                '参会服务商': int(r['参会服务商'] or 0)}
    pm_max = pd.read_sql("SELECT MAX(substr(活动开始时间,1,7)) m FROM promotion_meeting", conn).iloc[0]['m']
    return {'本周': pm(cur_s, week_end), '上周': pm(prev_s, prev_e), '数据末月': pm_max}


def gather_city_weekly(city: str, week_end: str = '2026-06-06', n_trend: int = 6,
                       db_path: Path = None) -> dict:
    db_path = db_path or DB_PATH_DEFAULT
    conn = sqlite3.connect(str(db_path))
    try:
        trend = query_city_trend(conn, city, week_end, n_trend)
        dist = query_district_week(conn, city, week_end)
        drill = query_city_week_drill(conn, city, week_end)   # 复用省周报(含***改写+周跑动)
        provkpi = query_provider_kpi(conn, week_end, city)
        prov_by_dealer = query_provider_by_dealer(conn, week_end, city)
        act = query_activation_v2(conn, week_end, city, n_trend)
        visit = query_visit_split(conn, week_end, city)
        focus = query_city_focus_week(conn, city, week_end, n_trend)
        promo = query_city_promo_week(conn, city, week_end)
        np_t = query_np_transfer(conn, city)
        return {'地市': city, '本周截止': week_end, '本周区间': trend['本周区间'],
                '口径说明': 'SO=全量感知；一周=7天；完成率=当月累计÷(年目标×当月节奏)；'
                            'YTD累计同比=年初至本周末 vs 去年同期；专项用台数；'
                            '本周签约=签约日期落在本周；激活=服务商管理表激活时间落本周(是否激活=Y,官方字段)；'
                            '跑动分大华/代理商业务员；任务达成=当前实际vs年度目标；'
                            '***=外省代理商在本省出货。',
                '①周趋势': trend, '②区县': dist, '③代理商跑动': drill,
                '服务商KPI': provkpi, '服务商按代理商': prov_by_dealer, '激活V2': act, '跑动分组': visit,
                '④专项': focus, '推广会': promo, 'NP转入': np_t}
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument('--city', required=True)
    ap.add_argument('--week-end', default='2026-06-06')
    ap.add_argument('--n', type=int, default=6)
    ap.add_argument('--db', default=None)
    a = ap.parse_args()
    rep = gather_city_weekly(a.city, a.week_end, a.n, db_path=Path(a.db) if a.db else None)
    print(json.dumps(rep, ensure_ascii=False, default=str))
