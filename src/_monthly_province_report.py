"""月度全省经营报告 — 数据汇总模块（聚焦诊断版）

对标地市月报（_monthly_city_report.py），但定位不同：**全省视角 + 11 地市横向诊断**。
独立模块，不改地市版，零回归。

三大模块（对应用户需求）：
  ① 城市 SO 趋势 — 11 地市 3/4/5 月 SO 同比 + 环比三月趋势，分出好 / 差地市
  ② 差地市逐个下钻 —
       a. 代理商问题：出货量 + 交易服务商数 变化
       b. 服务商等级结构（官方原始等级 V0-V5，provider_contract）哪档下降 + 代表服务商
       c. 跑动 + 推广会 环比变化
  ③ 三大产品专项 — 环比趋势，本月下降的专项打开：哪些地市 / 代理商 / 服务商下降

口径要点（见 docs/数据库说明.md）：
  - 全省 = 浙江 11 地市（product_flow_v.上线城市 IN ZHEJIANG_CITIES）。
    ⚠️ product_flow_v.上线城市 是全国口径（含异地上线），必须限定 11 市，
       否则 2025（含异地）vs 2026 同比不同口径。全省 = 11 地市之和（自洽）。
  - SO 金额：全量感知口径 = product_flow_v.最新分销价（与目标 / 周报同口径）。
  - 服务商等级：官方原始等级 provider_contract.服务商等级（26 年官方评定，V0-V5 六档）。
    ⚠️ 区别于「货值档」（全历史累计上线货值分层 V4/V3/V2/已激活/v0），后者用于派单/激活等业务。
  - 字段差异：product_flow 用「上线城市」「出库客户名称」；
    install_redpack 用「上线客户地市」「所属一级客户」「上线客户编码」；
    visit 用「拜访客户城市」。

不让 AI 写 SQL —— 数字全部在此算准，喂 JSON 给 V3 沙箱拼 docx。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent))

DB_PATH_DEFAULT = Path(__file__).parent.parent / 'db' / 'product_flow.db'

# 浙江 11 地市（全省口径 = 这 11 个之和）
ZHEJIANG_CITIES = [
    '杭州市', '宁波市', '温州市', '绍兴市', '湖州市', '嘉兴市',
    '金华市', '衢州市', '台州市', '丽水市', '舟山市',
]

FOCUS_LIST = ['夜视王', '无线', '场景化']


# ══════════════════════════════════════════════
# 月份 / 数值工具
# ══════════════════════════════════════════════

def _m(s: str, delta: int = 0) -> str:
    """月份偏移：_m('2026-05', -1) -> '2026-04'"""
    return str(pd.Period(s, freq='M') + delta)


def _last_n_months(end_month: str, n: int) -> list[str]:
    """end_month 往前数 n 个月（含 end），升序。"""
    return [_m(end_month, -(n - 1 - i)) for i in range(n)]


def _pct(curr, base) -> Optional[float]:
    """变化率（小数 0.066 = +6.6%）；base 缺失/0 → None。"""
    if curr is None or base is None or base == 0:
        return None
    return (curr - base) / base


def _fmt_pct(x) -> str:
    return f'{x * 100:+.1f}%' if x is not None else 'N/A'


def _cities_in_clause(prefix: str = '') -> tuple[str, list]:
    """构造 `col IN (?,?,…)` 片段 + 参数。"""
    ph = ','.join('?' * len(ZHEJIANG_CITIES))
    return f'{prefix} IN ({ph})', list(ZHEJIANG_CITIES)


# ══════════════════════════════════════════════
# 模块 ① 城市 SO 趋势（11 地市 3/4/5 月 同比 + 环比）
# ══════════════════════════════════════════════

def query_city_so_trend(conn, end_month: str = '2026-05', n_trend: int = 3) -> dict:
    """11 地市分月 SO（全量感知），同比（去年同月）+ 环比（上月），分好 / 差。

    返回:
      {
        '趋势月份': ['2026-03','2026-04','2026-05'],
        '同比月份': ['2025-03','2025-04','2025-05'],
        '全省': {'分月': {...}, '本月同比': , '本月环比': },
        '地市': [ {地市, 分月SO{月:值}, 同比{月:值}, 本月SO, 上月SO, 去年同月SO,
                   本月同比, 本月环比, 趋势下行, 判定}, … ],   # 按本月同比升序（差在前）
        '红榜': [...], '黑榜': [...],
        '差地市': ['金华市','杭州市',…],
      }
    """
    months = _last_n_months(end_month, n_trend)
    yoy_months = [_m(x, -12) for x in months]
    all_m = months + yoy_months

    in_clause, city_params = _cities_in_clause('上线城市')
    mph = ','.join('?' * len(all_m))
    df = pd.read_sql(f"""
        SELECT 上线城市 AS 地市, 上线年月 AS 月,
               ROUND(SUM(最新分销价) / 10000, 1) AS SO万
          FROM product_flow_v
         WHERE {in_clause}
           AND 上线年月 IN ({mph})
         GROUP BY 上线城市, 上线年月
    """, conn, params=(*city_params, *all_m))

    pivot = {c: {} for c in ZHEJIANG_CITIES}
    for _, r in df.iterrows():
        pivot[r['地市']][r['月']] = float(r['SO万'])

    cur_m, prev_m = months[-1], months[-2] if len(months) >= 2 else months[-1]
    yoy_cur_m = _m(cur_m, -12)

    rows = []
    for city in ZHEJIANG_CITIES:
        series = pivot[city]
        so_cur = series.get(cur_m)
        so_prev = series.get(prev_m)
        so_yoy = series.get(yoy_cur_m)
        yoy = _pct(so_cur, so_yoy)
        mom = _pct(so_cur, so_prev)
        # 趋势下行：当期是窗口内最低，或连续两月下降
        vals = [series.get(m) for m in months if series.get(m) is not None]
        trend_down = bool(vals) and so_cur is not None and so_cur <= min(vals) + 1e-9
        consecutive = (
            len(months) >= 3
            and all(series.get(m) is not None for m in months)
            and series[months[-1]] < series[months[-2]] < series[months[-3]]
        )
        trend_down = trend_down or consecutive

        # 判定（同比为硬指标——比去年同期差才算"差"；环比/趋势下行表达"动能转弱"）
        #   🔴 差   : 同比 < 0（比去年同期还低）
        #   🟢 好   : 同比 > +10% 且 环比未明显回落（≥ -5%）
        #   🟡 转弱 : 同比为正但动能转弱（环比 < -10% 或趋势下行）
        #   ⚪ 平稳 : 其余
        if yoy is not None and yoy < 0:
            verdict = '🔴 差'
        elif yoy is not None and yoy > 0.10 and (mom is None or mom >= -0.05):
            verdict = '🟢 好'
        elif (mom is not None and mom < -0.10) or trend_down:
            verdict = '🟡 转弱'
        else:
            verdict = '⚪ 平稳'

        rows.append({
            '地市': city,
            '分月SO': {m: series.get(m) for m in months},
            '同比月SO': {ym: series.get(ym) for ym in yoy_months},
            '本月SO': so_cur, '上月SO': so_prev, '去年同月SO': so_yoy,
            '本月同比': yoy, '本月环比': mom,
            '趋势下行': trend_down, '判定': verdict,
        })

    rows.sort(key=lambda x: (x['本月同比'] if x['本月同比'] is not None else 999))

    # 全省（11 市之和）
    prov = {}
    for m in all_m:
        prov[m] = round(sum(pivot[c].get(m, 0) for c in ZHEJIANG_CITIES), 1)

    bad = [r['地市'] for r in rows if r['判定'].startswith('🔴')]
    rank_yoy = sorted(rows, key=lambda x: (x['本月同比'] if x['本月同比'] is not None else 999))

    return {
        '趋势月份': months, '同比月份': yoy_months,
        '当期月': cur_m, '上月': prev_m,
        '全省': {
            '分月': {m: prov[m] for m in months},
            '同比分月': {ym: prov[ym] for ym in yoy_months},
            '本月SO': prov[cur_m], '上月SO': prov[prev_m], '去年同月SO': prov[yoy_cur_m],
            '本月同比': _pct(prov[cur_m], prov[yoy_cur_m]),
            '本月环比': _pct(prov[cur_m], prov[prev_m]),
        },
        '地市': rows,
        '红榜': [{'地市': r['地市'], '本月SO': r['本月SO'], '本月同比': r['本月同比']}
                 for r in rank_yoy[::-1][:3]],
        '黑榜': [{'地市': r['地市'], '本月SO': r['本月SO'], '本月同比': r['本月同比'],
                  '本月环比': r['本月环比'], '判定': r['判定']}
                 for r in rank_yoy[:3]],
        '差地市': bad,
    }


# ══════════════════════════════════════════════
# 模块 ②a 代理商下钻（出货量 + 交易服务商数 变化）
# ══════════════════════════════════════════════

def query_dealer_drilldown(conn, city: str, months: list[str], top_k: int = 10,
                           min_wan: float = 5.0) -> dict:
    """某地市各代理商：出货（product_flow 出库客户名称）+ 交易服务商数
    （install_redpack 所属一级客户的 distinct 上线客户编码），分月 + 环比。

    min_wan: 体量门槛（万）——本月或上月出货均 < min_wan 的代理商视为
             "在该地市几乎无业务"的噪音，剔除（如外地代理商在本市偶发 0.1 万）。
    """
    mph = ','.join('?' * len(months))
    # 出货：product_flow 按出库客户名称
    pf = pd.read_sql(f"""
        SELECT 出库客户名称 AS 代理商, 上线年月 AS 月,
               COUNT(*) AS 出货台数, ROUND(SUM(最新分销价) / 10000, 1) AS 出货万
          FROM product_flow_v
         WHERE 上线城市 = ? AND 上线年月 IN ({mph})
           AND 出库客户名称 IS NOT NULL AND 出库客户名称 != ''
         GROUP BY 出库客户名称, 上线年月
    """, conn, params=(city, *months))
    # 交易服务商数：install_redpack 按所属一级客户
    rp = pd.read_sql(f"""
        SELECT 所属一级客户 AS 代理商, 上线年月 AS 月,
               COUNT(DISTINCT 上线客户编码) AS 交易服务商数,
               ROUND(SUM(产品现有分销价) / 10000, 1) AS 上线万
          FROM install_redpack_v
         WHERE 上线客户地市 = ? AND 上线年月 IN ({mph})
           AND 所属一级客户 IS NOT NULL AND 所属一级客户 != ''
         GROUP BY 所属一级客户, 上线年月
    """, conn, params=(city, *months))

    cur_m, prev_m = months[-1], months[-2] if len(months) >= 2 else months[-1]

    dealers = sorted(set(pf['代理商']) | set(rp['代理商']))
    pf_map = {(r['代理商'], r['月']): (int(r['出货台数']), float(r['出货万']))
              for _, r in pf.iterrows()}
    rp_map = {(r['代理商'], r['月']): (int(r['交易服务商数']), float(r['上线万']))
              for _, r in rp.iterrows()}

    out = []
    for d in dealers:
        ship = {m: pf_map.get((d, m), (0, 0.0))[1] for m in months}   # 出货万
        ship_n = {m: pf_map.get((d, m), (0, 0.0))[0] for m in months}
        prov_n = {m: rp_map.get((d, m), (0, 0.0))[0] for m in months}  # 交易服务商数
        cur_ship, prev_ship = ship[cur_m], ship[prev_m]
        cur_pn, prev_pn = prov_n[cur_m], prov_n[prev_m]
        out.append({
            '代理商': d,
            '出货万_分月': ship, '出货台_分月': ship_n, '交易服务商数_分月': prov_n,
            '本月出货万': cur_ship, '上月出货万': prev_ship,
            '出货环比': _pct(cur_ship, prev_ship),
            '出货环比_万': round(cur_ship - prev_ship, 1),
            '本月交易服务商数': cur_pn, '上月交易服务商数': prev_pn,
            '服务商数环比': cur_pn - prev_pn,
        })

    # 体量门槛：本月或上月出货至少一个月 >= min_wan，才算该地市的实质代理商
    sized = [d for d in out if max(d['本月出货万'], d['上月出货万']) >= min_wan]
    # 按本月出货万降序给全量；问题代理商 = 出货环比下降额最大
    sized.sort(key=lambda x: x['本月出货万'], reverse=True)
    decliners = sorted(
        [d for d in sized if d['出货环比_万'] < 0 or d['服务商数环比'] < 0],
        key=lambda x: x['出货环比_万'],
    )[:top_k]
    return {
        '_体量门槛_万': min_wan,
        '_代理商总数': len(out),
        '_达门槛数': len(sized),
        '代理商明细': sized[:top_k * 2],
        '问题代理商': decliners,
    }


# ══════════════════════════════════════════════
# 模块 ②b 服务商等级结构（provider_contract 官方原始等级 V0-V5）哪档下降 + 代表服务商
# ══════════════════════════════════════════════

def query_tier_structure(conn, city: str, months: list[str]) -> dict:
    """各月活跃服务商按官方原始等级（provider_contract.服务商等级，V0-V5 六档）分档，
    看哪档当月 SO 下降，并给出下降档的代表服务商（本月 vs 上月 SO 掉得最多的）。
    口径：服务商等级=provider_contract 26 年官方评定（v0服务商~v5服务商→V0-V5）；
    活跃但未签约的归「未签约」。这是展示性等级结构，非货值分层。
    """
    mph = ','.join('?' * len(months))
    # 当月每服务商 SO（本地市）
    m_df = pd.read_sql(f"""
        SELECT 上线客户编码 AS 编码, MAX(上线客户名称) AS 名称,
               MAX(上线客户区县) AS 区县, MAX(上线客户渠道客户类型) AS 客户类型,
               上线年月 AS 月,
               SUM(产品现有分销价) AS mso, COUNT(*) AS mn
          FROM install_redpack_v
         WHERE 上线客户地市 = ? AND 上线年月 IN ({mph})
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
         GROUP BY 上线客户编码, 上线年月
    """, conn, params=(city, *months))
    if m_df.empty:
        return {'分月等级结构': {}, '下降档': [], '代表服务商': {}}

    codes = m_df['编码'].astype(str).unique().tolist()
    codes_in = "','".join(codes)
    # 等级 = provider_contract 官方原始等级（v0服务商~v5服务商 → V0-V5）；未签约归「未签约」
    _GRADE_LABEL = {f'v{i}服务商': f'V{i}' for i in range(6)}
    _gr = pd.read_sql(f"""
        SELECT 客户编码, 服务商等级 FROM provider_contract
         WHERE 客户编码 IN ('{codes_in}') AND 服务商等级 IS NOT NULL AND 服务商等级 != ''
    """, conn)
    grade_map = dict(zip(_gr['客户编码'].astype(str), _gr['服务商等级'].astype(str)))
    m_df['等级'] = (m_df['编码'].astype(str).map(grade_map)
                    .map(_GRADE_LABEL).fillna('未签约'))

    levels = ['V5', 'V4', 'V3', 'V2', 'V1', 'V0', '未签约']
    # 分月 × 等级：服务商数 + SO万
    struct = {}
    for m in months:
        sub = m_df[m_df['月'] == m]
        struct[m] = {}
        for lv in levels:
            s2 = sub[sub['等级'] == lv]
            struct[m][lv] = {
                '服务商数': int(s2['编码'].nunique()),
                'SO万': round(float(s2['mso'].sum()) / 10000, 1),
            }

    cur_m, prev_m = months[-1], months[-2] if len(months) >= 2 else months[-1]
    declines = []
    for lv in levels:
        cur_so = struct[cur_m][lv]['SO万']
        prev_so = struct[prev_m][lv]['SO万']
        declines.append({
            '等级': lv,
            '本月SO万': cur_so, '上月SO万': prev_so,
            '环比': _pct(cur_so, prev_so), '环比_万': round(cur_so - prev_so, 1),
            '本月服务商数': struct[cur_m][lv]['服务商数'],
            '上月服务商数': struct[prev_m][lv]['服务商数'],
            '服务商数环比': struct[cur_m][lv]['服务商数'] - struct[prev_m][lv]['服务商数'],
        })
    down = sorted([d for d in declines if d['环比_万'] < 0], key=lambda x: x['环比_万'])

    # 代表服务商：在下降档里，本月 vs 上月 SO 掉得最多的个体（含掉到 0 的）
    # 名称/区县/类型从全月份取（掉到 0 的服务商当月无记录，必须从历史取名，否则 None）
    m_df['编码'] = m_df['编码'].astype(str)
    name_meta = (m_df.groupby('编码')
                 .agg(名称=('名称', 'max'), 区县=('区县', 'max'), 客户类型=('客户类型', 'max')))
    rep = {}
    cur_per = m_df[m_df['月'] == cur_m].groupby('编码')['mso'].sum()
    prev_per = m_df[m_df['月'] == prev_m].groupby('编码')['mso'].sum()
    for d in down:
        lv = d['等级']
        lv_codes = set(m_df[m_df['等级'] == lv]['编码'])
        recs = []
        for code in lv_codes:
            cs = float(cur_per.get(code, 0))
            ps = float(prev_per.get(code, 0))
            if ps - cs > 0:  # 掉了
                nm = name_meta['名称'].get(code)
                qx = name_meta['区县'].get(code)
                ct = name_meta['客户类型'].get(code)
                recs.append({
                    '客户编码': code, '客户名称': nm, '区县': qx, '客户类型': ct,
                    '上月SO万': round(ps / 10000, 2), '本月SO万': round(cs / 10000, 2),
                    '掉幅万': round((ps - cs) / 10000, 2),
                    '状态': '🔴 归零' if cs == 0 else '🟡 下滑',
                })
        recs.sort(key=lambda x: x['掉幅万'], reverse=True)
        rep[lv] = recs[:8]

    return {'分月等级结构': struct, '等级环比': declines, '下降档': down, '代表服务商': rep}


# ══════════════════════════════════════════════
# 模块 ②c 跑动 + 推广会 环比
# ══════════════════════════════════════════════

def query_visit_promotion(conn, city: str, months: list[str]) -> dict:
    """某地市跑动（visit_record 拜访客户城市）+ 推广会（参会客户 join 客户城市）分月 + 环比。"""
    mph = ','.join('?' * len(months))
    visit = pd.read_sql(f"""
        SELECT 拜访年月 AS 月,
               SUM(CASE WHEN _打卡异常无效=0 AND _真异常打卡=0 THEN 1 ELSE 0 END) AS 有效打卡,
               COUNT(DISTINCT CASE WHEN _打卡异常无效=0 AND _真异常打卡=0
                                   THEN 打卡人姓名 END) AS 活跃业务员
          FROM visit_record_v
         WHERE 拜访客户城市 = ? AND 拜访年月 IN ({mph})
         GROUP BY 拜访年月
    """, conn, params=(city, *months))
    v_map = {r['月']: {'有效打卡': int(r['有效打卡'] or 0),
                       '活跃业务员': int(r['活跃业务员'] or 0)}
             for _, r in visit.iterrows()}

    # 推广会数据末月（promotion_meeting 常滞后于上线数据，避免把"无数据"误判为下降）
    pm_max = pd.read_sql(
        'SELECT MAX(substr(活动开始时间, 1, 7)) AS m FROM promotion_meeting', conn,
    ).iloc[0]['m']

    # 推广会：参会客户编码 → provider_contract.客户城市 归属地市
    promo = pd.read_sql(f"""
        SELECT substr(pm.活动开始时间, 1, 7) AS 月,
               COUNT(*) AS 参会人次,
               COUNT(DISTINCT pm.云商会议ID) AS 场次,
               COUNT(DISTINCT pm.参会客户编码) AS 参会服务商
          FROM promotion_meeting pm
          JOIN provider_contract pc ON pc.客户编码 = pm.参会客户编码
         WHERE pc.客户城市 = ? AND substr(pm.活动开始时间, 1, 7) IN ({mph})
         GROUP BY 月
    """, conn, params=(city, *months))
    p_map = {r['月']: {'参会人次': int(r['参会人次'] or 0), '场次': int(r['场次'] or 0),
                       '参会服务商': int(r['参会服务商'] or 0)}
             for _, r in promo.iterrows()}

    cur_m, prev_m = months[-1], months[-2] if len(months) >= 2 else months[-1]

    def _series(mp, key):
        return {m: mp.get(m, {}).get(key, 0) for m in months}

    visit_clk = _series(v_map, '有效打卡')
    promo_att = _series(p_map, '参会人次')
    # 本月是否有推广会数据：cur_m 不晚于数据末月才算
    promo_cur_ok = pm_max is not None and cur_m <= pm_max
    promo_prev_ok = pm_max is not None and prev_m <= pm_max
    return {
        '跑动_分月': {m: v_map.get(m, {'有效打卡': 0, '活跃业务员': 0}) for m in months},
        '推广会_分月': {m: p_map.get(m, {'参会人次': 0, '场次': 0, '参会服务商': 0}) for m in months},
        '跑动_有效打卡环比': _pct(visit_clk[cur_m], visit_clk[prev_m]),
        '推广会_数据末月': pm_max,
        '推广会_本月有数据': bool(promo_cur_ok),
        '推广会_参会环比': _pct(promo_att[cur_m], promo_att[prev_m])
                          if (promo_cur_ok and promo_prev_ok) else None,
    }


def drilldown_city(conn, city: str, months: list[str], min_dealer_wan: float = 5.0) -> dict:
    """单个差地市的完整下钻（②a + ②b + ②c）。"""
    return {
        '地市': city,
        '代理商': query_dealer_drilldown(conn, city, months, min_wan=min_dealer_wan),
        '等级结构': query_tier_structure(conn, city, months),
        '跑动推广会': query_visit_promotion(conn, city, months),
    }


# ══════════════════════════════════════════════
# 模块 ③ 三大产品专项 — 环比趋势 + 下降专项下钻
# ══════════════════════════════════════════════

def query_focus_trends(conn, end_month: str = '2026-05', n_trend: int = 3) -> dict:
    """三大专项全省（11 市）月度出货趋势 + 本月环比；下降的专项下钻：
    哪些地市跌、哪些代理商跌、哪些服务商明显下降。
    """
    months = _last_n_months(end_month, n_trend)
    cur_m, prev_m = months[-1], months[-2] if len(months) >= 2 else months[-1]
    in_clause, city_params = _cities_in_clause('pf.上线城市')
    rp_in_clause, rp_city_params = _cities_in_clause('ir.上线客户地市')
    mph = ','.join('?' * len(months))

    out = {'趋势月份': months, '当期月': cur_m, '专项': []}
    for focus in FOCUS_LIST:
        # 全省月度出货
        tr = pd.read_sql(f"""
            SELECT pf.上线年月 AS 月,
                   COUNT(*) AS 出货台数, ROUND(SUM(pf.最新分销价) / 10000, 1) AS 出货万
              FROM product_flow_v pf JOIN product_focus fc ON fc.物料号 = pf.物料号
             WHERE fc.专项 = ? AND {in_clause}
               AND pf.上线年月 IN ({mph})
             GROUP BY pf.上线年月
        """, conn, params=(focus, *city_params, *months))
        series = {r['月']: float(r['出货万']) for _, r in tr.iterrows()}
        cur_so = series.get(cur_m, 0.0)
        prev_so = series.get(prev_m, 0.0)
        mom = _pct(cur_so, prev_so)
        item = {
            '专项': focus,
            '分月出货万': {m: series.get(m, 0.0) for m in months},
            '本月出货万': cur_so, '上月出货万': prev_so, '本月环比': mom,
            '是否下降': mom is not None and mom < 0,
        }
        if item['是否下降']:
            item['下钻'] = _focus_drilldown(conn, focus, cur_m, prev_m,
                                            in_clause, city_params,
                                            rp_in_clause, rp_city_params)
        out['专项'].append(item)

    out['下降专项'] = [x['专项'] for x in out['专项'] if x['是否下降']]
    return out


def _focus_drilldown(conn, focus, cur_m, prev_m, pf_in, pf_cp, rp_in, rp_cp) -> dict:
    """下降专项：地市 / 代理商 / 服务商 本月 vs 上月。"""
    # 地市
    city = pd.read_sql(f"""
        SELECT pf.上线城市 AS 地市, pf.上线年月 AS 月,
               ROUND(SUM(pf.最新分销价) / 10000, 1) AS 出货万
          FROM product_flow_v pf JOIN product_focus fc ON fc.物料号 = pf.物料号
         WHERE fc.专项 = ? AND {pf_in} AND pf.上线年月 IN (?, ?)
         GROUP BY pf.上线城市, pf.上线年月
    """, conn, params=(focus, *pf_cp, cur_m, prev_m))
    city_rows = _pivot_decline(city, '地市', cur_m, prev_m)

    # 代理商
    dealer = pd.read_sql(f"""
        SELECT pf.出库客户名称 AS 代理商, pf.上线年月 AS 月,
               ROUND(SUM(pf.最新分销价) / 10000, 1) AS 出货万
          FROM product_flow_v pf JOIN product_focus fc ON fc.物料号 = pf.物料号
         WHERE fc.专项 = ? AND {pf_in} AND pf.上线年月 IN (?, ?)
           AND pf.出库客户名称 IS NOT NULL AND pf.出库客户名称 != ''
         GROUP BY pf.出库客户名称, pf.上线年月
    """, conn, params=(focus, *pf_cp, cur_m, prev_m))
    dealer_rows = _pivot_decline(dealer, '代理商', cur_m, prev_m)

    # 服务商（红包上线口径）
    prov = pd.read_sql(f"""
        SELECT ir.上线客户编码 AS 客户编码, MAX(ir.上线客户名称) AS 客户名称,
               MAX(ir.上线客户地市) AS 地市, ir.上线年月 AS 月,
               ROUND(SUM(ir.产品现有分销价) / 10000, 2) AS 上线万
          FROM install_redpack_v ir JOIN product_focus fc ON fc.物料号 = ir.物料号
         WHERE fc.专项 = ? AND {rp_in} AND ir.上线年月 IN (?, ?)
           AND ir.上线客户编码 IS NOT NULL AND ir.上线客户编码 != ''
         GROUP BY ir.上线客户编码, ir.上线年月
    """, conn, params=(focus, *rp_cp, cur_m, prev_m))
    prov_rows = _pivot_decline_provider(prov, cur_m, prev_m)

    return {
        '地市下降': city_rows[:11],
        '代理商下降': dealer_rows[:10],
        '服务商下降': prov_rows[:15],
    }


def _pivot_decline(df, key, cur_m, prev_m) -> list:
    cur = {r[key]: float(r['出货万']) for _, r in df[df['月'] == cur_m].iterrows()}
    prev = {r[key]: float(r['出货万']) for _, r in df[df['月'] == prev_m].iterrows()}
    keys = set(cur) | set(prev)
    rows = [{
        key: k, '本月出货万': round(cur.get(k, 0), 1), '上月出货万': round(prev.get(k, 0), 1),
        '环比_万': round(cur.get(k, 0) - prev.get(k, 0), 1),
        '环比': _pct(cur.get(k, 0), prev.get(k, 0)),
    } for k in keys]
    return sorted(rows, key=lambda x: x['环比_万'])


def _pivot_decline_provider(df, cur_m, prev_m) -> list:
    # 名称/地市从全集合取（掉到 0 的服务商当月无记录，否则名字为 None）
    meta = {r['客户编码']: (r['客户名称'], r['地市']) for _, r in df.iterrows()}
    cur = {r['客户编码']: float(r['上线万']) for _, r in df[df['月'] == cur_m].iterrows()}
    prev = {r['客户编码']: float(r['上线万']) for _, r in df[df['月'] == prev_m].iterrows()}
    keys = set(cur) | set(prev)
    rows = []
    for k in keys:
        nm, dist = meta.get(k, (None, None))
        cs = cur.get(k, 0.0)
        ps = prev.get(k, 0.0)
        if ps - cs > 0:
            rows.append({
                '客户编码': k, '客户名称': nm, '地市': dist,
                '上月上线万': round(ps, 2), '本月上线万': round(cs, 2),
                '掉幅万': round(ps - cs, 2),
                '状态': '🔴 归零' if cs == 0 else '🟡 下滑',
            })
    return sorted(rows, key=lambda x: x['掉幅万'], reverse=True)


# ══════════════════════════════════════════════
# 总入口
# ══════════════════════════════════════════════

def gather_province_report(end_month: str = '2026-05', n_trend: int = 3,
                           db_path: Path = None, max_drill: int = None,
                           min_dealer_wan: float = 5.0) -> dict:
    """全省月报数据汇总。

    Args:
      end_month:      本月（最新月），如 '2026-05'
      n_trend:        趋势月数（默认 3 → 3/4/5 月）
      max_drill:      模块②下钻地市数上限；None = 下钻全部🔴差地市（默认）
      min_dealer_wan: 模块②a 代理商体量门槛（万）
    """
    db_path = db_path or DB_PATH_DEFAULT
    conn = sqlite3.connect(str(db_path))
    try:
        months = _last_n_months(end_month, n_trend)
        m1 = query_city_so_trend(conn, end_month, n_trend)

        # 模块②：对所有🔴差地市逐个下钻（若没有🔴，则取黑榜前 2）
        targets = list(m1['差地市'])
        if max_drill is not None:
            targets = targets[:max_drill]
        if not targets:
            targets = [b['地市'] for b in m1['黑榜'][:2]]
        drills = [drilldown_city(conn, c, months, min_dealer_wan=min_dealer_wan)
                  for c in targets]

        m3 = query_focus_trends(conn, end_month, n_trend)

        return {
            '范围': '浙江全省（11 地市）',
            '本月': end_month, '趋势月份': months,
            '口径说明': 'SO=全量感知(product_flow_v.最新分销价)，全省=11地市之和；'
                        '服务商等级=provider_contract 官方原始等级(26年官方评定，V0-V5)。',
            '模块1_城市SO趋势': m1,
            '模块2_差地市下钻': {'下钻地市': targets, '明细': drills},
            '模块3_专项趋势': m3,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════
# 渲染：把汇总 dict 转成可读 markdown 案例（给用户审阅 + SKILL 参考）
# ══════════════════════════════════════════════

def render_markdown(rep: dict) -> str:
    L = []
    m1 = rep['模块1_城市SO趋势']
    months = rep['趋势月份']
    L.append(f"# 浙江全省月度经营报告（诊断版）· 本月 {rep['本月']}")
    L.append(f"\n> 范围：{rep['范围']}　|　口径：{rep['口径说明']}\n")

    # 模块①
    prov = m1['全省']
    L.append('## ① 全省 & 11 地市 SO 趋势')
    L.append(f"\n**全省**（11 市之和，万元）：" +
             '　'.join(f"{m} {prov['分月'][m]}" for m in months) +
             f"　|　本月同比 **{_fmt_pct(prov['本月同比'])}**　环比 **{_fmt_pct(prov['本月环比'])}**\n")
    hdr = '| 地市 | ' + ' | '.join(months) + ' | 本月同比 | 本月环比 | 判定 |'
    sep = '|---|' + '---|' * (len(months)) + '---|---|---|'
    L.append(hdr); L.append(sep)
    for r in m1['地市']:
        cells = ' | '.join(str(r['分月SO'].get(m, '·')) for m in months)
        L.append(f"| {r['地市']} | {cells} | {_fmt_pct(r['本月同比'])} | "
                 f"{_fmt_pct(r['本月环比'])} | {r['判定']} |")
    L.append('')
    L.append('**🏆 红榜**：' + '；'.join(
        f"{x['地市']}（同比{_fmt_pct(x['本月同比'])}）" for x in m1['红榜']))
    L.append('\n**⚠️ 黑榜**：' + '；'.join(
        f"{x['地市']}（同比{_fmt_pct(x['本月同比'])} 环比{_fmt_pct(x['本月环比'])}）"
        for x in m1['黑榜']))

    # 模块②
    L.append('\n## ② 差地市下钻')
    for d in rep['模块2_差地市下钻']['明细']:
        city = d['地市']
        L.append(f"\n### 🔴 {city}")
        # a 代理商
        thr = d['代理商'].get('_体量门槛_万', 0)
        nd = d['代理商'].get('_达门槛数', 0)
        nt = d['代理商'].get('_代理商总数', 0)
        L.append(f'\n**a. 问题代理商（出货量 + 交易服务商数 环比；'
                 f'体量门槛 ≥{thr}万，{nt} 家中 {nd} 家达标）**\n')
        L.append('| 代理商 | ' + ' | '.join(f'{m}出货万' for m in months) +
                 ' | 出货环比 | 本月交易服务商 | 服务商数环比 |')
        L.append('|---|' + '---|' * len(months) + '---|---|---|')
        for x in d['代理商']['问题代理商'][:6]:
            ship = ' | '.join(str(x['出货万_分月'].get(m, 0)) for m in months)
            L.append(f"| {x['代理商'][:16]} | {ship} | {_fmt_pct(x['出货环比'])} | "
                     f"{x['本月交易服务商数']} | {x['服务商数环比']:+d} |")
        # b 等级结构
        L.append('\n**b. 服务商等级结构（官方原始等级 V0-V5，当月 SO 万）**\n')
        struct = d['等级结构']['分月等级结构']
        L.append('| 等级 | ' + ' | '.join(months) + ' | 本月环比 |')
        L.append('|---|' + '---|' * len(months) + '---|')
        for row in d['等级结构']['等级环比']:
            lv = row['等级']
            cells = ' | '.join(str(struct[m][lv]['SO万']) for m in months)
            L.append(f"| {lv} | {cells} | {_fmt_pct(row['环比'])} |")
        for lv, reps in d['等级结构']['代表服务商'].items():
            if reps:
                names = '；'.join(f"{r['客户名称']}({r['区县']}, 掉{r['掉幅万']}万{r['状态'][:2]})"
                                  for r in reps[:5])
                L.append(f"\n　- **{lv} 档代表下滑服务商**：{names}")
        # c 跑动推广会
        vp = d['跑动推广会']
        L.append('\n**c. 跑动 & 推广会（环比）**\n')
        L.append('| 月份 | ' + ' | '.join(months) + ' |')
        L.append('|---|' + '---|' * len(months) + '')
        L.append('| 有效打卡 | ' + ' | '.join(
            str(vp['跑动_分月'][m]['有效打卡']) for m in months) + ' |')
        L.append('| 活跃业务员 | ' + ' | '.join(
            str(vp['跑动_分月'][m]['活跃业务员']) for m in months) + ' |')
        L.append('| 推广会参会人次 | ' + ' | '.join(
            str(vp['推广会_分月'][m]['参会人次']) for m in months) + ' |')
        if vp.get('推广会_本月有数据'):
            promo_note = f"推广会参会环比 **{_fmt_pct(vp['推广会_参会环比'])}**"
        else:
            promo_note = f"推广会本月无数据（数据截至 {vp.get('推广会_数据末月')}）"
        L.append(f"\n　跑动有效打卡环比 **{_fmt_pct(vp['跑动_有效打卡环比'])}**，{promo_note}")

    # 模块③
    L.append('\n## ③ 三大产品专项趋势')
    m3 = rep['模块3_专项趋势']
    L.append('\n| 专项 | ' + ' | '.join(f'{m}出货万' for m in months) + ' | 本月环比 | 状态 |')
    L.append('|---|' + '---|' * len(months) + '---|---|')
    for x in m3['专项']:
        cells = ' | '.join(str(x['分月出货万'].get(m, 0)) for m in months)
        st = '🔴 下降' if x['是否下降'] else '🟢 增长'
        L.append(f"| {x['专项']} | {cells} | {_fmt_pct(x['本月环比'])} | {st} |")
    for x in m3['专项']:
        if x.get('下钻'):
            dd = x['下钻']
            L.append(f"\n### 🔴 {x['专项']} 下降下钻")
            L.append('- **跌得最多的地市**：' + '；'.join(
                f"{r['地市']}({r['环比_万']:+}万)" for r in dd['地市下降'][:5] if r['环比_万'] < 0))
            L.append('- **跌得最多的代理商**：' + '；'.join(
                f"{r['代理商'][:14]}({r['环比_万']:+}万)" for r in dd['代理商下降'][:5]))
            L.append('- **明显下降的服务商**：' + '；'.join(
                f"{r['客户名称']}({r['地市']},掉{r['掉幅万']}万{r['状态'][:2]})"
                for r in dd['服务商下降'][:6]))
    return '\n'.join(L)


if __name__ == '__main__':
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument('--end', default='2026-05')
    ap.add_argument('--n', type=int, default=3)
    ap.add_argument('--drill', type=int, default=None,
                    help='下钻地市数上限；默认 None = 全部🔴差地市')
    ap.add_argument('--min-wan', type=float, default=5.0, help='代理商体量门槛（万）')
    ap.add_argument('--db', default=None, help='DB 路径（生产验证时指向生产 DB）')
    ap.add_argument('--md', action='store_true', help='输出 markdown 案例')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    args = ap.parse_args()
    rep = gather_province_report(args.end, args.n, max_drill=args.drill,
                                 min_dealer_wan=args.min_wan,
                                 db_path=Path(args.db) if args.db else None)
    if args.md:
        print(render_markdown(rep))
    if args.json or not args.md:
        print(json.dumps(rep, ensure_ascii=False, default=str, indent=2))
