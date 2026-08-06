#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地市月度SO作战方案 — 数据生成脚本(只读)
生成 so_plan_data.html 所需的 so_plan_data.json。

用法:
  # 在生产服务器上(数据库在 /opt/...):
  ssh root@121.196.152.24 'python3 -' < build_so_plan_data.py
  ssh root@121.196.152.24 'cat /tmp/so_plan_data.json' > so_plan_data.json
  # 或本地有同步好的 DB:
  python3 build_so_plan_data.py /path/to/product_flow.db ./so_plan_data.json

口径全部对齐 src/_panorama.py:
  SO完成 = SUM(product_flow_v.最新分销价)/10000,归口 上线城市/上线区县,日期取 上线年月
  6月节奏 = kpi_rhythm「省区SO进度条」6月占比;YTD应达成 = 年度SO目标 × Σ(1..当前月 节奏)
  代理商6月目标 = dealer_si_snapshot.签约金额 × 0.7 × 6月节奏
  RFM = 安装红包 当年1-5月,M=上线台数,F=活跃天数,R=最近上线距今;波士顿2×2(价值M × 活跃R)中位数切分
"""
import sqlite3, json, datetime, statistics, sys, os
from collections import defaultdict

# ─────────── 下月更新只改这 4 行 ───────────
YEAR        = '2026'
REF_DATE    = datetime.date(2026, 5, 30)   # 今天(算 recency)
DATA_CUTOFF = '2026-05-29'                  # 数据截止日(标注用)
CUR_MONTH   = 5                            # 数据截止到第几月(YTD/RFM 用)
# ──────────────────────────────────────────

DB  = sys.argv[1] if len(sys.argv) > 1 else '/opt/so-data-analytics/db/product_flow.db'
OUT = sys.argv[2] if len(sys.argv) > 2 else '/tmp/so_plan_data.json'

conn = sqlite3.connect(DB)
cur = conn.cursor()

def f(x, d=0.0):
    try: return float(x)
    except (TypeError, ValueError): return d

june_rhythm = f(cur.execute(
    "SELECT 占比 FROM kpi_rhythm WHERE 指标 LIKE '省区SO进度条%' AND 年度=? AND CAST(月份 AS INT)=6",
    (YEAR,)).fetchone()[0])
ytd_rhythm = sum(f(r[0]) for r in cur.execute(
    "SELECT 占比 FROM kpi_rhythm WHERE 指标 LIKE '省区SO进度条%' AND 年度=? AND CAST(月份 AS INT) BETWEEN 1 AND ?",
    (YEAR, CUR_MONTH)))

cities = [r[0] for r in cur.execute(
    "SELECT DISTINCT 城市 FROM kpi_targets WHERE 年度=? ORDER BY 城市", (YEAR,))]

def so_sum(city, ym_s, ym_e, district=None):
    if district:
        return cur.execute(
            "SELECT COALESCE(SUM(最新分销价)/10000,0) FROM product_flow_v "
            "WHERE 上线城市=? AND 上线区县=? AND 上线年月 BETWEEN ? AND ?",
            (city, district, ym_s, ym_e)).fetchone()[0]
    return cur.execute(
        "SELECT COALESCE(SUM(最新分销价)/10000,0) FROM product_flow_v "
        "WHERE 上线城市=? AND 上线年月 BETWEEN ? AND ?",
        (city, ym_s, ym_e)).fetchone()[0]

data = {'meta': {
    '生成日期': str(REF_DATE), '数据截止': DATA_CUTOFF, '年度': YEAR,
    '6月节奏占比': june_rhythm, 'YTD节奏占比_1to5': round(ytd_rhythm, 4),
    '代理商拆分系数': 0.7,
    '口径说明': 'SO=product_flow上线城市口径最新分销价/万;价值=安装红包当年1-5月上线金额(产品现有分销价);波士顿2×2=价值(金额贡献头部累计80%)×成长趋势(近2月月均vs前3月月均):头部+上升=明星,头部+平降=金牛,尾部+上升=问号,尾部+平降=瘦狗',
}, 'cities': {}}

省合计 = {'年度目标_万': 0, 'ytd完成_万': 0, 'y25_1_6_万': 0, 'y25_6_万': 0}

for city in cities:
    annual = sum(f(r[0]) for r in cur.execute(
        "SELECT SO目标_万 FROM kpi_targets WHERE 城市=? AND 年度=?", (city, YEAR)))
    ytd    = so_sum(city, f'{YEAR}-01', f'{YEAR}-{CUR_MONTH:02d}')
    y25_16 = so_sum(city, '2025-01', '2025-06')
    y25_6  = so_sum(city, '2025-06', '2025-06')
    ytd_should = annual * ytd_rhythm
    overview = {
        '年度目标_万': round(annual, 1), '6月目标_万': round(annual * june_rhythm, 1),
        'ytd完成_万': round(ytd, 1), 'ytd应达成_万': round(ytd_should, 1),
        '进度条达成率': round(ytd / ytd_should, 4) if ytd_should else None,
        '年完成率': round(ytd / annual, 4) if annual else None,
        'y25_1_6_万': round(y25_16, 1), 'y25_6_万': round(y25_6, 1),
        '同比_ytd_vs_25_1_6': round(ytd / y25_16 - 1, 4) if y25_16 else None,
    }
    省合计['年度目标_万'] += annual; 省合计['ytd完成_万'] += ytd
    省合计['y25_1_6_万'] += y25_16; 省合计['y25_6_万'] += y25_6

    districts = []
    for r in cur.execute(
        "SELECT 区县, SO目标_万 FROM kpi_targets WHERE 城市=? AND 年度=? "
        "ORDER BY CAST(SO目标_万 AS REAL) DESC", (city, YEAR)).fetchall():
        d, dt = r[0], f(r[1])
        owners = [x[0] for x in cur.execute(
            "SELECT DISTINCT 业务员 FROM dahua_dealer_owner WHERE 所在城市=? AND 所在区县=? AND 业务员 IS NOT NULL",
            (city, d))]
        districts.append({
            '区县': d, '年度目标_万': round(dt, 1), '6月目标_万': round(dt * june_rhythm, 2),
            '分销经理候选': owners, '默认分销经理': owners[0] if owners else '',
            'ytd完成_万': round(so_sum(city, f'{YEAR}-01', f'{YEAR}-{CUR_MONTH:02d}', d), 1),
        })

    dealers = []
    for r in cur.execute(
        "SELECT \"客户名称（无@办事处）\", 客户编码, 签约金额, 客户所有者, 渠道客户类型 "
        "FROM dealer_si_snapshot WHERE 客户所在城市=? ORDER BY CAST(签约金额 AS REAL) DESC", (city,)).fetchall():
        sv = f(r[2])
        dealers.append({
            '代理商': r[0], '客户编码': r[1], '签约值_万': round(sv, 1),
            '6月目标_万': round(sv * 0.7 * june_rhythm, 2),
            '分销经理': r[3] or '', '渠道类型': r[4] or '',
        })

    owner_set = set(d['分销经理'] for d in dealers if d['分销经理'])
    for o in cur.execute("SELECT DISTINCT 业务员 FROM dahua_dealer_owner WHERE 所在城市=? AND 业务员 IS NOT NULL", (city,)):
        owner_set.add(o[0])

    # 红包窗口扩到「过去6个月」(基线用),内含 1-CUR_MONTH 月(分类/趋势用)
    bym = []; _y, _m = int(YEAR), CUR_MONTH
    for _ in range(6):
        bym.append(f'{_y}-{_m:02d}'); _m -= 1
        if _m == 0: _m = 12; _y -= 1
    bstart = bym[-1]                                        # 最早月,如 '2025-12'
    ym15 = [f'{YEAR}-{mm:02d}' for mm in range(1, CUR_MONTH + 1)]   # 当年 1-CUR_MONTH 月
    rows = cur.execute(
        "SELECT 上线客户编码, 上线客户名称, 上线时间, 所属一级客户, \"所属一级客户业务员（固化）\", \"产品现有分销价\" "
        "FROM install_redpack WHERE 上线客户地市=? "
        f"AND 上线时间>='{bstart}-01' AND 上线时间<'{YEAR}-{CUR_MONTH+1:02d}-01' "
        "AND 上线客户编码 IS NOT NULL AND 上线客户编码 NOT IN ('***','')", (city,)).fetchall()
    agg = defaultdict(lambda: {'last': None, 'name': None, 'dealer': None, 'owner': None,
                               'm': defaultdict(float), 'mc': defaultdict(int)})
    for code, name, t, dealer, owner, price in rows:
        a = agg[code]; ym = str(t)[:7]
        a['m'][ym] += f(price); a['mc'][ym] += 1
        day = str(t)[:10]
        if a['last'] is None or day > a['last']: a['last'] = day
        if name and name != '***': a['name'] = name
        if dealer and dealer != '***': a['dealer'] = dealer
        if owner and owner != '***': a['owner'] = owner
    recent_m = [CUR_MONTH - 1, CUR_MONTH]              # 近2月
    early_m  = list(range(1, CUR_MONTH - 1))           # 前面的月
    sps = []
    for code, a in agg.items():
        cnt15 = sum(a['mc'].get(ym, 0) for ym in ym15)
        if cnt15 == 0: continue                        # 仅过去6月有量、当年1-N月无量 → 不纳入分类
        amt15 = sum(a['m'].get(ym, 0) for ym in ym15)
        base  = sum(a['m'].get(ym, 0) for ym in bym) / 6.0          # 过去6个月月均金额(元)
        recent = sum(a['m'].get(f'{YEAR}-{mm:02d}', 0) for mm in recent_m)
        early  = sum(a['m'].get(f'{YEAR}-{mm:02d}', 0) for mm in early_m)
        ravg = recent / len(recent_m); eavg = early / max(1, len(early_m))
        rising = ravg > eavg
        trendpct = round(ravg / eavg - 1, 3) if eavg > 0 else None  # None=纯新增(前期无量)
        sps.append({'code': code, 'name': a['name'] or code,
                    'amt': round(amt15 / 10000, 2), 'cnt': cnt15,
                    'baseline': round(base / 10000, 2),               # 6月基线(万/月,过去半年月均)
                    'rising': rising, 'trendpct': trendpct,
                    'last': a['last'], 'dealer': a['dealer'] or '', 'owner': a['owner'] or ''})
    # ── 价值轴:金额(1-N月)贡献头部累计 ~80% 为高价值 ──
    sps.sort(key=lambda s: -s['amt'])
    total_amt = sum(s['amt'] for s in sps)
    cum = 0.0; head_cut = 0.0
    for s in sps:
        s['high_value'] = cum < 0.8 * total_amt
        cum += s['amt']
        if s['high_value']: head_cut = s['amt']        # 头部最小金额=高价值门槛
    counts = {'明星': 0, '金牛': 0, '问号': 0, '瘦狗': 0}
    for s in sps:
        hv, up = s['high_value'], s['rising']
        s['象限'] = '明星' if (hv and up) else '金牛' if (hv and not up) else '问号' if (not hv and up) else '瘦狗'
        counts[s['象限']] += 1
        del s['high_value']
        if s['owner']: owner_set.add(s['owner'])
    # 输出「全部服务商」(按金额降序),board4 客户端按象限筛选

    data['cities'][city] = {
        'overview': overview, 'districts': districts, 'dealers': dealers,
        'owners_datalist': sorted(owner_set),
        'rfm': {'counts': counts, 'total_sp': len(sps), 'total_amt_万': round(total_amt, 1),
                'head_count': counts['明星'] + counts['金牛'], '高价值门槛_万': round(head_cut, 2),
                'baseline_months': bym, 'services': sps},
    }

省合计['进度条达成率'] = round(省合计['ytd完成_万'] / (省合计['年度目标_万'] * ytd_rhythm), 4) if 省合计['年度目标_万'] else None
省合计['6月目标_万'] = round(省合计['年度目标_万'] * june_rhythm, 1)
for k in ['年度目标_万', 'ytd完成_万', 'y25_1_6_万', 'y25_6_万']:
    省合计[k] = round(省合计[k], 1)
data['meta']['省合计'] = 省合计

with open(OUT, 'w', encoding='utf-8') as fp:
    json.dump(data, fp, ensure_ascii=False, indent=1)

print(f'✅ 完成 → {OUT} ({os.path.getsize(OUT)/1024:.0f} KB)')
print(f'   {len(cities)} 城市 · 省年度目标 {省合计["年度目标_万"]}万 · 省6月目标 {省合计["6月目标_万"]}万 · 进度条达成率 {省合计["进度条达成率"]}')
conn.close()
