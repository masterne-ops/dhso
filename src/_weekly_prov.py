"""全省周报 — 数据汇总模块（按 7 天窗口）

对标全省月报，但时间粒度=周，主对比=周环比上周。
本周 = week_end 当天往前 7 天（含 end）；上周 = 再往前 7 天；趋势 = 最近 n 个 7 天窗口。
默认 week_end='2026-06-06' → 本周 5/31–6/6、上周 5/24–5/30。

模块：
  ① 全省周趋势（最近 n 个 7 天窗口 SO + 本周环比上周）
  ② 11 地市周对比（本周 SO + 周环比 + 红黑榜 + 当月完成率进度条）
  ③ 异动地市下钻（本周环比下滑地市 → 代理商本周异动 + 周跑动环比）
  ④ 三大专项周趋势（全省，本周环比）+ 推广会本周

口径：SO=全量感知(product_flow_v.最新分销价)，全省=浙江11地市；
完成率=当月累计实际 ÷ (年目标 × 当月节奏占比)，复用月报/panorama 口径。
保留周跑动、纳入推广会(数据已更新至2026-06)；砍等级结构(累计货值周级无意义)。
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent))
from _monthly_province_report import ZHEJIANG_CITIES, _pct  # noqa: E402

DB_PATH_DEFAULT = Path(__file__).parent.parent / 'db' / 'product_flow.db'


def _week_windows(week_end: str, n: int = 6) -> list[tuple]:
    """以 week_end(本周最后一天)为基准,往前推 n 个 7 天窗口。升序,末个=本周。"""
    end = pd.Timestamp(week_end)
    wins = []
    for i in range(n - 1, -1, -1):
        e = end - pd.Timedelta(days=7 * i)
        s = e - pd.Timedelta(days=6)
        wins.append((s.strftime('%Y-%m-%d'), e.strftime('%Y-%m-%d')))
    return wins


def _cities_ph():
    return ','.join('?' * len(ZHEJIANG_CITIES))


# ── ① 全省周趋势 ──
def query_prov_week_trend(conn, week_end: str, n: int = 6) -> dict:
    wins = _week_windows(week_end, n)
    ph = _cities_ph()
    labels, vals = [], []
    for s, e in wins:
        df = pd.read_sql(f"""
            SELECT ROUND(SUM(最新分销价)/10000,1) AS so
              FROM product_flow_v
             WHERE 上线城市 IN ({ph}) AND date(上线时间) BETWEEN ? AND ?
        """, conn, params=(*ZHEJIANG_CITIES, s, e))
        vals.append(float(df.iloc[0]['so'] or 0))
        labels.append(f"{s[5:]}~{e[5:]}")
    cur, prev = vals[-1], (vals[-2] if len(vals) >= 2 else None)
    return {'窗口': wins, '标签': labels, '分周SO': vals,
            '本周区间': f"{wins[-1][0]} ~ {wins[-1][1]}",
            '本周SO': cur, '上周SO': prev, '本周环比': _pct(cur, prev)}


# ── ② 11地市周对比 + 当月完成率进度条 ──
def query_city_week_compare(conn, week_end: str, year: int = 2026) -> dict:
    cur_s = (pd.Timestamp(week_end) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    cur_e = week_end
    prev_s = (pd.Timestamp(week_end) - pd.Timedelta(days=13)).strftime('%Y-%m-%d')
    prev_e = (pd.Timestamp(week_end) - pd.Timedelta(days=7)).strftime('%Y-%m-%d')
    cur_month = week_end[:7]
    month_num = int(cur_month[5:7])
    ph = _cities_ph()

    def week_so(s, e):
        df = pd.read_sql(f"""
            SELECT 上线城市 市, ROUND(SUM(最新分销价)/10000,1) so
              FROM product_flow_v WHERE 上线城市 IN ({ph}) AND date(上线时间) BETWEEN ? AND ?
             GROUP BY 上线城市
        """, conn, params=(*ZHEJIANG_CITIES, s, e))
        return {r['市']: float(r['so'] or 0) for _, r in df.iterrows()}
    cur, prev = week_so(cur_s, cur_e), week_so(prev_s, prev_e)

    mon_act = pd.read_sql(f"""
        SELECT 上线城市 市, ROUND(SUM(最新分销价)/10000,1) so
          FROM product_flow_v WHERE 上线城市 IN ({ph}) AND 上线年月=?
         GROUP BY 上线城市
    """, conn, params=(*ZHEJIANG_CITIES, cur_month))
    mon_map = {r['市']: float(r['so'] or 0) for _, r in mon_act.iterrows()}
    tgt = pd.read_sql("SELECT 城市 市, SUM(SO目标_万) 年目标 FROM kpi_targets WHERE 年度=? GROUP BY 城市",
                      conn, params=(year,))
    tgt_map = {r['市']: float(r['年目标'] or 0) for _, r in tgt.iterrows()}
    rhy = pd.read_sql("SELECT 占比 FROM kpi_rhythm WHERE 指标 LIKE '省区SO进度条%' AND 年度=? AND 月份=?",
                      conn, params=(year, month_num))
    pace = float(rhy.iloc[0]['占比']) if not rhy.empty else None

    first = pd.Timestamp(cur_month + '-01')
    last_day = (first + pd.offsets.MonthEnd(0)).day
    time_progress = round(((pd.Timestamp(week_end) - first).days + 1) / last_day, 3)

    rows = []
    for c in ZHEJIANG_CITIES:
        sc, sp = cur.get(c, 0), prev.get(c, 0)
        mon = mon_map.get(c, 0)
        should = round(tgt_map.get(c, 0) * pace, 1) if pace else None
        rate = round(mon / should, 3) if (should and should > 0) else None
        rows.append({'市': c, '本周SO': sc, '上周SO': sp, '本周环比': _pct(sc, sp),
                     '当月累计': mon, '当月应达成': should, '当月完成率': rate,
                     '年目标': tgt_map.get(c, 0)})
    rows.sort(key=lambda x: x['本周SO'], reverse=True)
    ranked = sorted([r for r in rows if r['本周环比'] is not None], key=lambda x: x['本周环比'])
    return {'本周区间': f"{cur_s} ~ {cur_e}", '上周区间': f"{prev_s} ~ {prev_e}",
            '当月': cur_month, '当月时间进度': time_progress, '月节奏占比': pace,
            '地市': rows,
            '红榜': [{'市': r['市'], '本周SO': r['本周SO'], '本周环比': r['本周环比']} for r in ranked[::-1][:3]],
            '黑榜': [{'市': r['市'], '本周SO': r['本周SO'], '本周环比': r['本周环比']} for r in ranked[:3]],
            '异动下滑': [r['市'] for r in ranked if r['本周环比'] is not None and r['本周环比'] < -0.15]}


# ── ②.5 YTD 累计同比（年初至本周末 vs 去年同期）──
def query_ytd_yoy(conn, week_end: str) -> dict:
    """全省 + 各地市 两种同比：
      - 当月同比：本月 1 号 ~ week_end vs 去年同月同期（"当月当天累计"，看本月势头）
      - YTD累计同比：今年 1/1 ~ week_end vs 去年同期（看全年趋势）
    """
    ph = _cities_ph()
    y = int(week_end[:4])
    we = pd.Timestamp(week_end)
    # YTD 区间
    ytd_cur_s, ytd_cur_e = f'{y}-01-01', week_end
    ytd_prev_s = f'{y-1}-01-01'
    ytd_prev_e = (we - pd.DateOffset(years=1)).strftime('%Y-%m-%d')
    # 当月当天累计 区间（本月 1 号 ~ week_end；去年同月同期）
    mtd_cur_s = week_end[:7] + '-01'
    mtd_cur_e = week_end
    mtd_prev_s = (pd.Timestamp(mtd_cur_s) - pd.DateOffset(years=1)).strftime('%Y-%m-%d')
    mtd_prev_e = (we - pd.DateOffset(years=1)).strftime('%Y-%m-%d')

    def by_city(s, e):
        df = pd.read_sql(f"""
            SELECT 上线城市 市, ROUND(SUM(最新分销价)/10000,1) so
              FROM product_flow_v WHERE 上线城市 IN ({ph}) AND date(上线时间) BETWEEN ? AND ?
             GROUP BY 上线城市
        """, conn, params=(*ZHEJIANG_CITIES, s, e))
        return {r['市']: float(r['so'] or 0) for _, r in df.iterrows()}
    ytd_cur, ytd_prev = by_city(ytd_cur_s, ytd_cur_e), by_city(ytd_prev_s, ytd_prev_e)
    mtd_cur, mtd_prev = by_city(mtd_cur_s, mtd_cur_e), by_city(mtd_prev_s, mtd_prev_e)

    rows = []
    for c in ZHEJIANG_CITIES:
        yc, yp = ytd_cur.get(c, 0), ytd_prev.get(c, 0)
        mc, mp = mtd_cur.get(c, 0), mtd_prev.get(c, 0)
        rows.append({'市': c,
                     '当月今年': mc, '当月去年': mp, '当月同比': _pct(mc, mp),
                     'YTD今年': yc, 'YTD去年': yp, '累计同比': _pct(yc, yp)})
    rows.sort(key=lambda x: x['YTD今年'], reverse=True)
    return {
        '当月区间': f"{mtd_cur_s} ~ {mtd_cur_e}", '当月去年区间': f"{mtd_prev_s} ~ {mtd_prev_e}",
        '今年区间': f"{ytd_cur_s} ~ {ytd_cur_e}", '去年区间': f"{ytd_prev_s} ~ {ytd_prev_e}",
        '全省当月今年': round(sum(mtd_cur.values()), 1), '全省当月去年': round(sum(mtd_prev.values()), 1),
        '全省当月同比': _pct(round(sum(mtd_cur.values()), 1), round(sum(mtd_prev.values()), 1)),
        '全省今年': round(sum(ytd_cur.values()), 1), '全省去年': round(sum(ytd_prev.values()), 1),
        '全省累计同比': _pct(round(sum(ytd_cur.values()), 1), round(sum(ytd_prev.values()), 1)),
        '地市': rows,
    }


# ── 服务商激活(达V2)：本周达V2数 + 近N周趋势 ──
def query_activation_v2(conn, week_end: str, city: str = None, n: int = 6) -> dict:
    """激活口径=provider_contract 激活时间落本周 且 是否激活=Y(官方激活字段,不依赖install_redpack)。给最近 n 周趋势。"""
    cities = ZHEJIANG_CITIES if city is None else [city]
    ph = ','.join('?' * len(cities))
    wins = _week_windows(week_end, n)

    def actv(win_s, win_e):
        df = pd.read_sql(f"""
            SELECT COUNT(*) n FROM provider_contract
             WHERE 客户城市 IN ({ph}) AND 是否激活='Y' AND date(激活时间) BETWEEN ? AND ?
        """, conn, params=(*cities, win_s, win_e))
        return int(df.iloc[0]['n'] or 0)

    labels, vals = [], []
    for s, e in wins:
        labels.append(f"{s[5:]}~{e[5:]}")
        vals.append(actv(s, e))
    cur, prev = vals[-1], (vals[-2] if len(vals) >= 2 else None)
    return {'标签': labels, '分周达V2': vals, '本周达V2': cur, '上周达V2': prev,
            '环比': _pct(cur, prev)}


# ── 跑动:大华 vs 代理商业务员分组 ──
def query_visit_split(conn, week_end: str, city: str = None) -> dict:
    """本周跑动按 _打卡方(🏢大华/🏪代理商)拆组,各自打卡数/人数 + 环比上周。"""
    cities = ZHEJIANG_CITIES if city is None else [city]
    ph = ','.join('?' * len(cities))
    cur_s = (pd.Timestamp(week_end) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    prev_s = (pd.Timestamp(week_end) - pd.Timedelta(days=13)).strftime('%Y-%m-%d')
    prev_e = (pd.Timestamp(week_end) - pd.Timedelta(days=7)).strftime('%Y-%m-%d')

    def by_side(s, e):
        df = pd.read_sql(f"""
            SELECT _打卡方 方, COUNT(*) 打卡, COUNT(DISTINCT 打卡人姓名) 人数
              FROM visit_record_v
             WHERE 拜访客户城市 IN ({ph})
               AND date(COALESCE(活动创建时间,拜访时间)) BETWEEN ? AND ?
             GROUP BY _打卡方
        """, conn, params=(*cities, s, e))
        out = {}
        for _, r in df.iterrows():
            side = '大华' if '大华' in str(r['方']) else '代理商'
            out[side] = {'打卡': int(r['打卡']), '人数': int(r['人数'])}
        return out
    cur, prev = by_side(cur_s, week_end), by_side(prev_s, prev_e)

    def grp(side):
        c = cur.get(side, {'打卡': 0, '人数': 0})
        p = prev.get(side, {'打卡': 0, '人数': 0})
        return {'本周打卡': c['打卡'], '本周人数': c['人数'],
                '上周打卡': p['打卡'], '打卡环比': _pct(c['打卡'], p['打卡'])}
    return {'大华': grp('大华'), '代理商': grp('代理商')}


# ── 服务商按 11 地市对比(全省周报用) ──
def query_provider_by_city(conn, week_end: str, year: int = 2026) -> dict:
    """每个地市:本周签约 + 签约达成率 + 本周激活(达V2) + 激活环比 + 激活达成率。
    签约达成率分母=年度签约目标;激活达成率分母=V2年度目标
    (分子=V2及以上存量,provider_contract 官方原始等级,与全名册/目标同口径)。
    """
    cur_s = (pd.Timestamp(week_end) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    prev_end = (pd.Timestamp(week_end) - pd.Timedelta(days=7)).strftime('%Y-%m-%d')
    prev_s = (pd.Timestamp(week_end) - pd.Timedelta(days=13)).strftime('%Y-%m-%d')
    ph = _cities_ph()

    # 本周签约(按城市)
    sign = pd.read_sql(f"""
        SELECT 客户城市 市, COUNT(*) n FROM provider_contract
         WHERE 客户城市 IN ({ph}) AND date(签约日期) BETWEEN ? AND ?
         GROUP BY 客户城市
    """, conn, params=(*ZHEJIANG_CITIES, cur_s, week_end))
    sign_map = {r['市']: int(r['n']) for _, r in sign.iterrows()}

    # 本周/上周 激活(按城市) — provider_contract 激活时间落该周,按客户城市分组
    def reached_v2_by_city(win_end):
        win_s = (pd.Timestamp(win_end) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
        df = pd.read_sql(f"""
            SELECT 客户城市 市, COUNT(*) n FROM provider_contract
             WHERE 客户城市 IN ({ph}) AND 是否激活='Y' AND date(激活时间) BETWEEN ? AND ?
             GROUP BY 客户城市
        """, conn, params=(*ZHEJIANG_CITIES, win_s, win_end))
        return {r['市']: int(r['n']) for _, r in df.iterrows()}
    act_cur = reached_v2_by_city(week_end)
    act_prev = reached_v2_by_city(prev_end)

    # 目标(按城市) + 当前累计签约/V2存量(按城市)
    tgt = pd.read_sql(f"""
        SELECT 地市 市, SUM(服务商签约数_含个人) 签约目标, SUM(安装红包V2_家数) V2目标
          FROM provider_target WHERE 年度=? AND 地市 IN ({ph}) GROUP BY 地市
    """, conn, params=(year, *ZHEJIANG_CITIES))
    tgt_map = {r['市']: (int(r['签约目标'] or 0), int(r['V2目标'] or 0)) for _, r in tgt.iterrows()}
    cur_sign = pd.read_sql(f"""
        SELECT 客户城市 市, COUNT(*) n FROM provider_contract WHERE 客户城市 IN ({ph}) GROUP BY 客户城市
    """, conn, params=tuple(ZHEJIANG_CITIES))
    cursign_map = {r['市']: int(r['n']) for _, r in cur_sign.iterrows()}
    # V2及以上存量 = provider_contract 官方原始等级(与全名册/provider_target 同口径)
    cur_v2 = pd.read_sql(f"""
        SELECT 客户城市 市, COUNT(*) n FROM provider_contract
         WHERE 客户城市 IN ({ph})
           AND 服务商等级 IN ('v2服务商','v3服务商','v4服务商','v5服务商')
         GROUP BY 客户城市
    """, conn, params=tuple(ZHEJIANG_CITIES))
    curv2_map = {r['市']: int(r['n']) for _, r in cur_v2.iterrows()}

    def r(a, t):
        return round(a / t, 3) if t else None
    rows = []
    for c in ZHEJIANG_CITIES:
        st, v2t = tgt_map.get(c, (0, 0))
        ac, ap = act_cur.get(c, 0), act_prev.get(c, 0)
        rows.append({'市': c,
                     '本周签约': sign_map.get(c, 0),
                     '签约达成率': r(cursign_map.get(c, 0), st),
                     '本周激活': ac, '激活环比': _pct(ac, ap),
                     '激活达成率': r(curv2_map.get(c, 0), v2t)})
    rows.sort(key=lambda x: x['本周签约'], reverse=True)
    return {'地市': rows}


# ── 服务商按代理商对比(地市周报用) ──
def query_provider_by_dealer(conn, week_end: str, city: str, top_k: int = 12) -> dict:
    """该市每个代理商:本周新签约服务商数 + 本周新达V2服务商数。
    代理商级只给本周签约/本周激活实际值(无年度目标,不设达成率)。
    代理商口径=provider_contract.上级分销商名称 / install_redpack.所属一级客户。
    """
    cur_s = (pd.Timestamp(week_end) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    prev_end = (pd.Timestamp(week_end) - pd.Timedelta(days=7)).strftime('%Y-%m-%d')

    # 本周新签约(按上级分销商)
    sign = pd.read_sql("""
        SELECT 上级分销商名称 代理商, COUNT(*) n FROM provider_contract
         WHERE 客户城市=? AND date(签约日期) BETWEEN ? AND ?
           AND 上级分销商名称 IS NOT NULL AND 上级分销商名称!=''
         GROUP BY 上级分销商名称
    """, conn, params=(city, cur_s, week_end))
    sign_map = {r['代理商']: int(r['n']) for _, r in sign.iterrows()}

    # 本周激活(按代理商) — provider_contract 激活时间落本周,按上级分销商分组
    actv2 = pd.read_sql("""
        SELECT 上级分销商名称 代理商, COUNT(*) n FROM provider_contract
         WHERE 客户城市=? AND 是否激活='Y' AND date(激活时间) BETWEEN ? AND ?
           AND 上级分销商名称 IS NOT NULL AND 上级分销商名称!=''
         GROUP BY 上级分销商名称
    """, conn, params=(city, cur_s, week_end))
    act_map = {r['代理商']: int(r['n']) for _, r in actv2.iterrows()}

    dealers = sorted(set(sign_map) | set(act_map),
                     key=lambda d: (sign_map.get(d, 0) + act_map.get(d, 0)), reverse=True)
    rows = [{'代理商': '外省代理商在本省出货' if str(d).strip() == '***' else d,
             '本周签约': sign_map.get(d, 0), '本周激活': act_map.get(d, 0)}
            for d in dealers if (sign_map.get(d, 0) + act_map.get(d, 0)) > 0][:top_k]
    return {'代理商': rows}


# ── 服务商:本周签约/激活 + 任务达成(年度目标) ──
def query_provider_kpi(conn, week_end: str, city: str = None, year: int = 2026) -> dict:
    """服务商三指标:
      - 本周签约 = provider_contract.签约日期 落在本周的服务商数
      - 本周激活 = 本年累计上线下单价首破 V2(≥1000):本周末累计≥1000 且 上周末<1000(对齐官方 激活时间)
      - 任务达成 = 当前实际 vs provider_target 年度目标:签约数 + V2/V3/V4各档家数 + 新签
        (各档家数=provider_contract 官方原始等级,与全名册/目标同口径)
    city=None 时为全省(11市汇总);否则单市。
    """
    cur_s = (pd.Timestamp(week_end) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    cur_e = week_end
    cities = ZHEJIANG_CITIES if city is None else [city]
    ph = ','.join('?' * len(cities))

    # 本周签约(按 客户城市)
    sign = pd.read_sql(f"""
        SELECT COUNT(*) n FROM provider_contract
         WHERE 客户城市 IN ({ph}) AND date(签约日期) BETWEEN ? AND ?
    """, conn, params=(*cities, cur_s, cur_e)).iloc[0]['n']

    # 本周激活 = provider_contract 激活时间落本周 且 是否激活=Y(官方激活字段,不依赖install_redpack)
    act = pd.read_sql(f"""
        SELECT COUNT(*) n FROM provider_contract
         WHERE 客户城市 IN ({ph}) AND 是否激活='Y'
           AND date(激活时间) BETWEEN ? AND ?
    """, conn, params=(*cities, cur_s, cur_e)).iloc[0]['n']

    # 任务达成:目标(provider_target 年度)
    tgt = pd.read_sql(f"""
        SELECT COALESCE(SUM(服务商签约数_含个人),0) 签约目标,
               COALESCE(SUM(安装红包V2_家数),0) V2目标,
               COALESCE(SUM(安装红包V3_家数),0) V3目标,
               COALESCE(SUM("安装红包V4及以上_家数"),0) V4目标,
               COALESCE(SUM(新签目标),0) 新签目标
          FROM provider_target WHERE 年度=? AND 地市 IN ({ph})
    """, conn, params=(year, *cities)).iloc[0]

    # 实际:当前签约数(provider_contract) + 新签数(是否新签=Y)
    actual_sign = pd.read_sql(f"""
        SELECT COUNT(*) 签约数, SUM(CASE WHEN 是否新签='Y' THEN 1 ELSE 0 END) 新签数
          FROM provider_contract WHERE 客户城市 IN ({ph})
    """, conn, params=tuple(cities)).iloc[0]

    # 实际:各档家数(provider_contract 官方原始等级,与全名册/provider_target 同口径)
    tier = pd.read_sql(f"""
        SELECT 服务商等级 等级, COUNT(*) n
          FROM provider_contract
         WHERE 客户城市 IN ({ph})
         GROUP BY 服务商等级
    """, conn, params=tuple(cities))
    tier_map = {r['等级']: int(r['n']) for _, r in tier.iterrows()}
    v2 = tier_map.get('v2服务商', 0)
    v3 = tier_map.get('v3服务商', 0)
    # 目标列为「安装红包V4及以上_家数」→ V4实际含 v5(同 page22 全名册口径)
    v4 = tier_map.get('v4服务商', 0) + tier_map.get('v5服务商', 0)

    def rate(a, t):
        return round(a / t, 3) if t else None

    return {
        '本周签约': int(sign or 0),
        '本周激活': int(act or 0),
        '任务达成': {
            '签约数': {'实际': int(actual_sign['签约数'] or 0), '目标': int(tgt['签约目标'] or 0),
                       '达成率': rate(int(actual_sign['签约数'] or 0), int(tgt['签约目标'] or 0))},
            '新签': {'实际': int(actual_sign['新签数'] or 0), '目标': int(tgt['新签目标'] or 0),
                     '达成率': rate(int(actual_sign['新签数'] or 0), int(tgt['新签目标'] or 0))},
            'V2': {'实际': v2, '目标': int(tgt['V2目标'] or 0), '达成率': rate(v2, int(tgt['V2目标'] or 0))},
            'V3': {'实际': v3, '目标': int(tgt['V3目标'] or 0), '达成率': rate(v3, int(tgt['V3目标'] or 0))},
            'V4': {'实际': v4, '目标': int(tgt['V4目标'] or 0), '达成率': rate(v4, int(tgt['V4目标'] or 0))},
        },
    }


# ── ③ 异动地市下钻 ──
def query_city_week_drill(conn, city: str, week_end: str) -> dict:
    cur_s = (pd.Timestamp(week_end) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    cur_e = week_end
    prev_s = (pd.Timestamp(week_end) - pd.Timedelta(days=13)).strftime('%Y-%m-%d')
    prev_e = (pd.Timestamp(week_end) - pd.Timedelta(days=7)).strftime('%Y-%m-%d')

    def dealer_so(s, e):
        df = pd.read_sql("""
            SELECT 出库客户名称 代理商, ROUND(SUM(最新分销价)/10000,1) so
              FROM product_flow_v WHERE 上线城市=? AND date(上线时间) BETWEEN ? AND ?
               AND 出库客户名称 IS NOT NULL AND 出库客户名称!='' GROUP BY 出库客户名称
        """, conn, params=(city, s, e))
        return {r['代理商']: float(r['so'] or 0) for _, r in df.iterrows()}
    dc, dp = dealer_so(cur_s, cur_e), dealer_so(prev_s, prev_e)
    dealers = []
    for k in set(dc) | set(dp):
        cv, pv = dc.get(k, 0), dp.get(k, 0)
        if max(cv, pv) >= 3:
            # *** = 外省代理商的货在浙江上线，改写为可读名称
            name = '外省代理商在本省出货' if str(k).strip() == '***' else k
            dealers.append({'代理商': name, '本周': cv, '上周': pv,
                            '环比_万': round(cv - pv, 1), '环比': _pct(cv, pv)})
    dealers.sort(key=lambda x: x['环比_万'])

    def visit(s, e):
        df = pd.read_sql("""
            SELECT COUNT(*) 打卡,
                   COUNT(DISTINCT 打卡人姓名) 业务员
              FROM visit_record_v WHERE 拜访客户城市=?
               AND date(COALESCE(活动创建时间,拜访时间)) BETWEEN ? AND ?
        """, conn, params=(city, s, e))
        return int(df.iloc[0]['打卡'] or 0), int(df.iloc[0]['业务员'] or 0)
    vc, va = visit(cur_s, cur_e)
    vp, _ = visit(prev_s, prev_e)
    return {'地市': city,
            '代理商SO': sorted(dealers, key=lambda x: x['本周'], reverse=True),
            '问题代理商': [d for d in dealers if d['环比_万'] < 0][:8],
            '跑动': {'本周打卡': vc, '上周打卡': vp, '环比': _pct(vc, vp), '本周活跃业务员': va}}


# ── NP 转入客户进展(漏斗,当前状态口径,复用 page39) ──
def query_np_transfer(conn, city: str = None) -> dict:
    """NP 转入客户转化漏斗。表 np_transfer_customer(快照表,PK=数据时点+外部客户名称,多期累积)。
    口径同 page39(当前口径,非按周):每个来源只取其最新数据时点的快照,再按外部客户名称去重
    (跨来源同名保留字段更全的新批行);转入→已报备(客户状态=已报备)→相关(与我司业务相关=Y)
    →已签约(客户名称命中 provider_contract)→已激活(该签约名 install_redpack 累计上线≥1000)。
    注:旧批无 客户状态/与我司业务相关 字段,报备/相关只反映新批。
    city=None 返回全省11地市对比+合计;否则单市漏斗。表不存在返回 None。
    """
    t = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='np_transfer_customer'").fetchone()
    if not t:
        return None
    df = pd.read_sql(
        "SELECT 外部客户名称, 客户来源, 市, 客户名称, 客户状态, 与我司业务相关, 数据时点"
        " FROM np_transfer_customer", conn)
    # 当前口径:每个来源取最新数据时点快照 + 按外部客户名称去重(保留字段更全的新批行)
    df = df[df['数据时点'] == df.groupby('客户来源')['数据时点'].transform('max')]
    df = df.assign(_填充=df.notna().sum(axis=1)).sort_values(
        ['数据时点', '_填充'], ascending=False).drop_duplicates('外部客户名称').drop(columns='_填充')
    pc = set(r[0] for r in conn.execute("SELECT 客户名称 FROM provider_contract"))
    amt = {r[0]: (r[1] or 0) for r in conn.execute(
        "SELECT 上线客户名称, SUM(产品现有分销价) FROM install_redpack GROUP BY 1")}
    df['_签约'] = df['客户名称'].apply(lambda x: bool(x) and x in pc)
    df['_激活'] = df['客户名称'].apply(lambda x: bool(x) and amt.get(x, 0) >= 1000)

    def funnel(sub):
        n = len(sub)
        ns, na = int(sub['_签约'].sum()), int(sub['_激活'].sum())
        return {'转入': n, '已报备': int((sub['客户状态'] == '已报备').sum()),
                '相关': int((sub['与我司业务相关'] == 'Y').sum()),
                '已签约': ns, '已激活': na,
                '签约率': round(ns / n, 3) if n else None,
                '激活率': round(na / n, 3) if n else None}
    snap = '/'.join(sorted(df['数据时点'].dropna().astype(str).unique().tolist()))
    if city:
        r = funnel(df[df['市'] == city])
        r['数据时点'] = snap
        return r
    rows = []
    for c in ZHEJIANG_CITIES:
        r = funnel(df[df['市'] == c])
        r['市'] = c
        rows.append(r)
    rows.sort(key=lambda x: x['转入'], reverse=True)
    return {'地市': rows, '合计': funnel(df), '数据时点': snap}


# ── ④ 三专项周趋势 + 推广会 ──
def query_focus_week(conn, week_end: str, n: int = 6) -> dict:
    wins = _week_windows(week_end, n)
    ph = _cities_ph()
    out = {'标签': [f"{s[5:]}~{e[5:]}" for s, e in wins], '专项': []}
    for focus in ['夜视王', '无线', '场景化']:
        vals = []
        for s, e in wins:
            df = pd.read_sql(f"""
                SELECT COUNT(*) n
                  FROM product_flow_v pf JOIN product_focus fc ON fc.物料号=pf.物料号
                 WHERE fc.专项=? AND pf.上线城市 IN ({ph}) AND date(pf.上线时间) BETWEEN ? AND ?
            """, conn, params=(focus, *ZHEJIANG_CITIES, s, e))
            vals.append(int(df.iloc[0]['n'] or 0))
        cur, prev = vals[-1], (vals[-2] if len(vals) >= 2 else None)
        out['专项'].append({'专项': focus, '分周台数': vals, '本周台数': cur, '上周台数': prev,
                            '本周环比': _pct(cur, prev), '是否下降': (_pct(cur, prev) or 0) < 0})
    return out


def query_promo_week(conn, week_end: str) -> dict:
    cur_s = (pd.Timestamp(week_end) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    prev_s = (pd.Timestamp(week_end) - pd.Timedelta(days=13)).strftime('%Y-%m-%d')
    prev_e = (pd.Timestamp(week_end) - pd.Timedelta(days=7)).strftime('%Y-%m-%d')

    def pm(s, e):
        df = pd.read_sql("""
            SELECT COUNT(*) 参会人次, COUNT(DISTINCT 云商会议ID) 场次,
                   COUNT(DISTINCT 参会客户编码) 参会服务商
              FROM promotion_meeting WHERE date(活动开始时间) BETWEEN ? AND ?
        """, conn, params=(s, e))
        r = df.iloc[0]
        return {'参会人次': int(r['参会人次'] or 0), '场次': int(r['场次'] or 0),
                '参会服务商': int(r['参会服务商'] or 0)}
    pm_max = pd.read_sql("SELECT MAX(substr(活动开始时间,1,7)) m FROM promotion_meeting", conn).iloc[0]['m']
    return {'本周': pm(cur_s, week_end), '上周': pm(prev_s, prev_e), '数据末月': pm_max}


def gather_prov_weekly(week_end: str = '2026-06-06', n_trend: int = 6,
                       db_path: Path = None, max_drill: int = 3) -> dict:
    db_path = db_path or DB_PATH_DEFAULT
    conn = sqlite3.connect(str(db_path))
    try:
        trend = query_prov_week_trend(conn, week_end, n_trend)
        compare = query_city_week_compare(conn, week_end)
        ytd = query_ytd_yoy(conn, week_end)
        provkpi = query_provider_kpi(conn, week_end, None)
        prov_by_city = query_provider_by_city(conn, week_end)
        act = query_activation_v2(conn, week_end, None, n_trend)
        visit = query_visit_split(conn, week_end, None)
        targets = compare['异动下滑'][:max_drill]
        drills = [query_city_week_drill(conn, c, week_end) for c in targets]
        focus = query_focus_week(conn, week_end, n_trend)
        promo = query_promo_week(conn, week_end)
        np_t = query_np_transfer(conn, None)
        return {'范围': '浙江全省（11地市）', '本周截止': week_end, '本周区间': trend['本周区间'],
                '口径说明': 'SO=全量感知；一周=7天；完成率=当月累计÷(年目标×当月节奏)；'
                            'YTD累计同比=年初至本周末 vs 去年同期；专项用台数；'
                            '本周签约=签约日期落在本周；激活=服务商管理表激活时间落本周(是否激活=Y,官方字段)；'
                            '跑动分大华/代理商业务员；任务达成=当前实际vs年度目标。',
                '①周趋势': trend, '②地市对比': compare, '②YTD累计同比': ytd,
                '服务商KPI': provkpi, '服务商按地市': prov_by_city, '激活V2': act, '跑动分组': visit,
                '③异动下钻': {'下钻地市': targets, '明细': drills},
                '④专项': focus, '推广会': promo, 'NP转入': np_t}
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument('--week-end', default='2026-06-06')
    ap.add_argument('--n', type=int, default=6)
    ap.add_argument('--db', default=None)
    a = ap.parse_args()
    rep = gather_prov_weekly(a.week_end, a.n, db_path=Path(a.db) if a.db else None)
    print(json.dumps(rep, ensure_ascii=False, default=str))
