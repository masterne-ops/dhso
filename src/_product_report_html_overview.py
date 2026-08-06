#!/usr/bin/env python3
"""产品维度经营报告 · 概览版 — 问题清单 HTML

输入:v4 runner 生成的全量 data.json
输出:精简 HTML,聚焦"哪里有问题",每个对象 1-3 条问题点,带跳转到 V4 详细版的链接

对象层级:
  - Top 10 子系列
  - 11 地市
  - Top 30 区县
  - Top 20 代理商
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from html import escape
from collections import defaultdict
import pandas as pd

OUT_DIR = Path(__file__).parent

# 问题阈值
TH_SEVERE = -0.20      # 严重下滑
TH_MILD = -0.05        # 轻度下滑
TH_HALF = -0.50        # 腰斩
TH_PRICE_DROP = -0.10  # 价跌
TH_UP = 0.30           # 上位
TH_NET_OUT = -20       # 净流出阈值
TH_CLOSED_RATIO = 0.10 # 已关闭货值占比


def fmt_pct(v, sign=True):
    if v is None or pd.isna(v): return '—'
    s = '+' if sign and v >= 0 else ''
    return f'{s}{v*100:.1f}%'


def fmt_money(v, d=2):
    if v is None or pd.isna(v): return '—'
    try: return f'{float(v):,.{d}f}'
    except Exception: return '—'


def fmt_int(v):
    if v is None or pd.isna(v): return '—'
    try: return f'{int(v):,}'
    except Exception: return '—'


def safe_pct(v):
    """同比 None 时返回 None,否则原值"""
    return None if (v is None or pd.isna(v)) else v


# ─── 问题判定 ───

def analyze_subseries(sub, data):
    """分析一个子系列的问题"""
    issues = []
    yoy = safe_pct(sub.get('同比'))
    avg_yoy = safe_pct(sub.get('均价同比'))

    # 1. 同比下滑
    if yoy is not None:
        if yoy <= TH_HALF:
            issues.append(('🚨', f'**腰斩级下滑**:同比 {fmt_pct(yoy)},基本失守'))
        elif yoy <= TH_SEVERE:
            issues.append(('❌', f'**严重下滑**:同比 {fmt_pct(yoy)}'))
        elif yoy <= TH_MILD:
            issues.append(('⚠️', f'轻度下滑:同比 {fmt_pct(yoy)}'))

    # 2. 价跌
    if avg_yoy is not None and avg_yoy <= TH_PRICE_DROP:
        issues.append(('💰', f'**价格下降** {fmt_pct(avg_yoy)} — 排查是否被竞品压价 / 产品换代'))

    # 3. 价涨
    if avg_yoy is not None and avg_yoy >= TH_UP:
        issues.append(('💲', f'均价大幅上涨 {fmt_pct(avg_yoy)} — 关注客户接受度'))

    # 4. 子系列在多少地市下滑(从产品×区县推断)
    px_qx = [r for r in data.get('全量_产品×区县', []) if r.get('子系列') == sub.get('子系列')]
    # 按地市归并(需要从 v4 数据 推) — 实际 全量_产品×区县 维度是区县,不带地市
    # 简化:直接看 子系列×地市透视
    mat = data.get('全量_子系列×地市', {})
    yoy_matrix = {r['子系列']: r for r in mat.get('同比矩阵', [])}
    if sub.get('子系列') in yoy_matrix:
        row = yoy_matrix[sub['子系列']]
        cities = mat.get('地市列表', [])
        n_decl_cities = sum(1 for ct in cities if row.get(ct) is not None and not pd.isna(row.get(ct)) and row.get(ct) <= TH_SEVERE)
        n_grow_cities = sum(1 for ct in cities if row.get(ct) is not None and not pd.isna(row.get(ct)) and row.get(ct) >= TH_UP)
        if n_decl_cities >= 3:
            decl_list = [f"{ct}({fmt_pct(row[ct])})" for ct in cities
                        if row.get(ct) is not None and not pd.isna(row.get(ct)) and row.get(ct) <= TH_SEVERE]
            decl_list.sort(key=lambda s: float(s.split('(')[1].rstrip('%)').replace('+','')))
            issues.append(('🗺️', f'**{n_decl_cities} 个地市下滑严重**:{", ".join(decl_list[:5])}'))
        elif n_grow_cities >= 5 and (yoy is None or yoy >= 0):
            issues.append(('📈', f'高增地市 {n_grow_cities} 个 — 复制经验'))

    return issues


def analyze_city(city, data):
    """分析一个地市的问题"""
    issues = []
    # 找该地市在 11 地市矩阵里的数据
    mat = data.get('全量_子系列×地市', {})
    yoy_mat = {r['子系列']: r for r in mat.get('同比矩阵', [])}
    amt_mat = {r['子系列']: r for r in mat.get('货值矩阵', [])}

    top10 = data.get('Top10子系列', [])
    decl_subs, grow_subs = [], []
    city_so_cur, city_so_yoy_est = 0, 0

    for sub in top10:
        if sub not in yoy_mat: continue
        v = yoy_mat[sub].get(city)
        amt = amt_mat.get(sub, {}).get(city, 0) or 0
        city_so_cur += amt
        if v is not None and not pd.isna(v):
            if v <= TH_SEVERE:
                decl_subs.append((sub, v, amt))
            elif v >= TH_UP:
                grow_subs.append((sub, v, amt))
            # 估算同期(用同比反推)
            if v > -1:
                city_so_yoy_est += amt / (1 + v) if (1+v) > 0 else 0

    # 1. Top 10 子系列下滑数量
    if len(decl_subs) >= 3:
        names = ', '.join(f"{s[0]}({fmt_pct(s[1])})" for s in sorted(decl_subs, key=lambda x: x[1])[:5])
        issues.append(('❌', f'**{len(decl_subs)} 个 Top 10 子系列严重下滑**:{names}'))
    elif len(decl_subs) >= 1:
        names = ', '.join(f"{s[0]}({fmt_pct(s[1])})" for s in decl_subs)
        issues.append(('⚠️', f'{len(decl_subs)} 个 Top 10 子系列下滑:{names}'))

    # 2. 渠道健康度
    ch4_city_rows = data.get('全量_渠道健康度', {}).get('11地市矩阵', [])
    city_health = next((r for r in ch4_city_rows if r['地市'] == city), None)
    if city_health:
        if city_health['同期净流入'] <= TH_NET_OUT:
            issues.append(('📉', f'**渠道净流出** {city_health["同期净流入"]:+}(新增 {city_health["同期新增"]} / 流失 {city_health["同期流失"]})'))
        elif city_health['同期净流入'] <= 0:
            issues.append(('🟡', f'渠道微缩 {city_health["同期净流入"]:+}'))

    # 3. 高增产品(亮点)
    if grow_subs:
        names = ', '.join(f"{s[0]}({fmt_pct(s[1])})" for s in sorted(grow_subs, key=lambda x: -x[1])[:3])
        issues.append(('📈', f'高增产品:{names}'))

    return issues, {
        'top10_decl': len(decl_subs),
        'top10_grow': len(grow_subs),
        'net_in': city_health['同期净流入'] if city_health else None,
        '当期货值_万': round(city_so_cur, 2),
    }


def analyze_district(district_rows):
    """分析一个区县(多产品行)的问题 — 输入是该区县所有子系列的行"""
    issues = []
    decl = [r for r in district_rows if r.get('同比') is not None and not pd.isna(r['同比']) and r['同比'] <= TH_SEVERE]
    grow = [r for r in district_rows if r.get('同比') is not None and not pd.isna(r['同比']) and r['同比'] >= TH_UP]
    total = sum(r.get('货值_万', 0) or 0 for r in district_rows)
    total_yoy = sum(r.get('同期_万', 0) or 0 for r in district_rows)

    overall_yoy = (total - total_yoy) / total_yoy if total_yoy > 0 else None

    if overall_yoy is not None:
        if overall_yoy <= TH_HALF:
            issues.append(('🚨', f'**整体腰斩** 同比 {fmt_pct(overall_yoy)}({fmt_money(total_yoy)} → {fmt_money(total)} 万)'))
        elif overall_yoy <= TH_SEVERE:
            issues.append(('❌', f'**整体下滑** 同比 {fmt_pct(overall_yoy)}'))

    if decl:
        names = ', '.join(f"{r['子系列']}({fmt_pct(r['同比'])})" for r in sorted(decl, key=lambda x: x['同比'])[:3])
        if not issues:  # 没有整体问题但子系列有
            issues.append(('⚠️', f'{len(decl)} 个子系列下滑:{names}'))

    if grow:
        names = ', '.join(f"{r['子系列']}({fmt_pct(r['同比'])})" for r in sorted(grow, key=lambda x: -x['同比'])[:3])
        issues.append(('📈', f'增长亮点:{names}'))

    return issues, {'overall_yoy': overall_yoy, 'total_wan': total, 'decl_n': len(decl), 'grow_n': len(grow)}


CSS = """
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Arial,sans-serif;
    margin:0;background:#f7f9fc;color:#1a3c5e;line-height:1.6;font-size:14px}
.page{max-width:1100px;margin:0 auto;background:white;padding:28px 36px;
    box-shadow:0 0 20px rgba(0,0,0,.05)}
h1.title{text-align:center;margin:0 0 8px;font-size:28px}
.meta{text-align:center;color:#607d8b;font-size:12px;margin-bottom:24px;
    padding-bottom:12px;border-bottom:2px solid #e3edf5}
.meta p{margin:3px 0}
.callout{background:linear-gradient(90deg,#dbeafe 0%,#bfdbfe 100%);color:#1e40af;
    padding:14px 20px;border-radius:8px;margin:16px 0;font-size:13px}
.callout a{color:#1e40af;font-weight:600;text-decoration:underline}
h1.section{border-bottom:3px solid #1a3c5e;padding-bottom:6px;margin-top:36px;font-size:21px}
.card{border:1px solid #e3edf5;border-radius:8px;padding:14px 18px;margin:10px 0;
    background:white;transition:box-shadow .15s}
.card:hover{box-shadow:0 2px 8px rgba(0,0,0,.06)}
.card.severe{border-left:5px solid #ef4444}
.card.warning{border-left:5px solid #f59e0b}
.card.healthy{border-left:5px solid #10b981}
.card.neutral{border-left:5px solid #94a3b8}
.card-head{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}
.card-name{font-size:16px;font-weight:700;color:#1a3c5e}
.card-name .tag{font-size:11px;font-weight:normal;color:#94a3b8;margin-left:8px}
.card-stats{font-size:12px;color:#607d8b}
.card-stats .num{font-weight:600;color:#1a3c5e;font-size:13px;margin:0 2px}
.card-stats .up{color:#065f46}
.card-stats .down{color:#991b1b}
.card-issues{margin-top:8px}
.card-issues .issue{padding:4px 0;font-size:12px}
.card-issues .issue .icon{margin-right:6px;font-size:14px}
.card-issues strong{color:#1a3c5e}
.no-issues{color:#10b981;font-size:12px;font-style:italic}
.summary-bar{display:flex;gap:12px;margin:16px 0;flex-wrap:wrap}
.summary-pill{padding:6px 14px;background:#f0f4f8;border-radius:16px;font-size:12px}
.summary-pill.red{background:#fee2e2;color:#991b1b}
.summary-pill.yellow{background:#fef3c7;color:#92400e}
.summary-pill.green{background:#d1fae5;color:#065f46}
.toc{background:#f0f4f8;padding:14px 20px;border-radius:6px;margin-bottom:24px;
    border-left:4px solid #3b82f6;font-size:13px}
.toc a{color:#1a3c5e;text-decoration:none;margin-right:18px}
.toc a:hover{color:#3b82f6}
.detail-link{display:inline-block;padding:2px 8px;background:#3b82f6;color:white;
    border-radius:3px;font-size:11px;text-decoration:none;margin-left:8px}
.detail-link:hover{background:#2563eb}
@media print{body{background:white}.page{box-shadow:none}.card{break-inside:avoid}}
</style>
"""


def render(data, v4_filename):
    meta = data['_meta']
    blocks = [CSS]
    blocks.append('<div class="page">')

    blocks.append('<h1 class="title">产品维度经营报告 · 问题清单(概览)</h1>')
    blocks.append('<div class="meta">')
    blocks.append(f'<p>地域:{escape(meta["city"])}  |  评估期:<b>{meta["period_start"]} ~ {meta["period_end"]}</b>({meta["n_months"]} 个月)</p>')
    blocks.append(f'<p>同期:{meta["yoy_start"]} ~ {meta["yoy_end"]}  |  环期:{meta["mom_start"]} ~ {meta["mom_end"]}  |  生成:{meta["generated_at"]}</p>')
    blocks.append('</div>')

    blocks.append(f'<div class="callout">📋 <b>用法</b>:本页是问题清单概览,用于<b>简短汇报</b>。需要详细数据 / 全量筛选 / 跨维度交叉 → 打开 <a href="{escape(v4_filename)}" target="_blank">📊 V4 全量交互版</a></div>')

    blocks.append('<div class="toc">')
    blocks.append('<a href="#s1">① Top 10 子系列</a>')
    blocks.append('<a href="#s2">② 11 地市</a>')
    blocks.append('<a href="#s3">③ Top 30 区县</a>')
    blocks.append('<a href="#s4">④ Top 20 代理商</a>')
    blocks.append('</div>')

    # ─── 一、Top 10 子系列 ───
    blocks.append('<h1 class="section" id="s1">一、Top 10 子系列 — 问题清单</h1>')
    top10_subs = data.get('Top10子系列', [])
    all_subs = data.get('全量子系列', [])
    sub_map = {r['子系列']: r for r in all_subs}

    # 状态统计
    n_severe = 0; n_warn = 0; n_healthy = 0
    cards = []
    for sn in top10_subs:
        if sn not in sub_map: continue
        sub = sub_map[sn]
        issues = analyze_subseries(sub, data)

        # 卡片状态
        has_severe = any(i[0] in ('🚨','❌','📉','🚨') for i in issues)
        has_warn = any(i[0] in ('⚠️','💰','💲','🗺️','🟡') for i in issues)
        if has_severe: cls = 'severe'; n_severe += 1
        elif has_warn: cls = 'warning'; n_warn += 1
        else: cls = 'healthy'; n_healthy += 1

        yoy_cls = 'up' if (sub['同比'] and sub['同比'] >= 0) else 'down'
        cards.append(f"""<div class="card {cls}">
<div class="card-head">
  <div class="card-name">{escape(sub['子系列'])}<span class="tag">({escape(sub['三级'] or '—')})</span></div>
  <div class="card-stats">
    评估期 <span class="num">{fmt_money(sub['货值_万'])}</span> 万 ·
    同期 <span class="num">{fmt_money(sub['同期_万'])}</span> 万 ·
    同比 <span class="num {yoy_cls}">{fmt_pct(sub['同比'])}</span> ·
    均价同比 <span class="num">{fmt_pct(sub.get('均价同比'))}</span>
  </div>
</div>""")
        if issues:
            cards.append('<div class="card-issues">')
            for icon, text in issues[:4]:
                cards.append(f'<div class="issue"><span class="icon">{icon}</span>{text}</div>')
            cards.append('</div>')
        else:
            cards.append('<div class="no-issues">✓ 表现良好,无需特别关注</div>')
        cards.append('</div>')

    blocks.append(f'<div class="summary-bar"><span class="summary-pill red">严重 {n_severe}</span><span class="summary-pill yellow">告警 {n_warn}</span><span class="summary-pill green">健康 {n_healthy}</span></div>')
    blocks.extend(cards)

    # ─── 二、11 地市 ───
    blocks.append('<h1 class="section" id="s2">二、11 地市 — 问题清单</h1>')
    mat = data.get('全量_子系列×地市', {})
    cities = mat.get('地市列表', [])
    ch4_city_rows = {r['地市']: r for r in data.get('全量_渠道健康度', {}).get('11地市矩阵', [])}

    # 地市按当期 SO 货值降序(取自 11地市矩阵 评估期活跃排序,或者算总货值)
    city_amounts = {}
    for r in mat.get('货值矩阵', []):
        for ct in cities:
            v = r.get(ct, 0) or 0
            city_amounts[ct] = city_amounts.get(ct, 0) + v
    cities_sorted = sorted(cities, key=lambda c: -city_amounts.get(c, 0))

    n_severe = 0; n_warn = 0; n_healthy = 0
    cards = []
    for ct in cities_sorted:
        issues, stats = analyze_city(ct, data)
        has_severe = any(i[0] in ('🚨','❌','📉') for i in issues)
        has_warn = any(i[0] in ('⚠️','💰','💲','🗺️','🟡') for i in issues)
        if has_severe: cls = 'severe'; n_severe += 1
        elif has_warn: cls = 'warning'; n_warn += 1
        else: cls = 'healthy'; n_healthy += 1

        net = stats.get('net_in')
        net_str = f"{net:+}" if net is not None else '—'
        cards.append(f"""<div class="card {cls}">
<div class="card-head">
  <div class="card-name">{escape(ct)}</div>
  <div class="card-stats">
    Top10 子系列:<span class="num down">{stats['top10_decl']}</span> 下滑 / <span class="num up">{stats['top10_grow']}</span> 增长 ·
    渠道同期净流入 <span class="num">{net_str}</span> ·
    当期 Top10 货值 <span class="num">{fmt_money(stats['当期货值_万'])}</span> 万
  </div>
</div>""")
        if issues:
            cards.append('<div class="card-issues">')
            for icon, text in issues[:4]:
                cards.append(f'<div class="issue"><span class="icon">{icon}</span>{text}</div>')
            cards.append('</div>')
        else:
            cards.append('<div class="no-issues">✓ 表现良好</div>')
        cards.append('</div>')

    blocks.append(f'<div class="summary-bar"><span class="summary-pill red">严重 {n_severe}</span><span class="summary-pill yellow">告警 {n_warn}</span><span class="summary-pill green">健康 {n_healthy}</span></div>')
    blocks.extend(cards)

    # ─── 三、Top 30 区县 ───
    blocks.append('<h1 class="section" id="s3">三、Top 30 区县 — 问题清单(按总货值降序)</h1>')
    # 从 全量_产品×区县 按区县聚合,只取 Top 10 子系列范围内
    top10_set = set(top10_subs)
    qx_data = defaultdict(list)
    for r in data.get('全量_产品×区县', []):
        if r['子系列'] in top10_set:
            qx_data[r['维度']].append(r)

    qx_list = []
    for qx, rows in qx_data.items():
        total = sum(r.get('货值_万', 0) for r in rows)
        qx_list.append((qx, total, rows))
    qx_list.sort(key=lambda x: -x[1])
    qx_list = qx_list[:30]

    n_severe = 0; n_warn = 0; n_healthy = 0
    cards = []
    for qx, total, rows in qx_list:
        issues, stats = analyze_district(rows)
        has_severe = any(i[0] in ('🚨','❌','📉') for i in issues)
        has_warn = any(i[0] in ('⚠️','💰','💲','🗺️','🟡') for i in issues)
        if has_severe: cls = 'severe'; n_severe += 1
        elif has_warn: cls = 'warning'; n_warn += 1
        else: cls = 'healthy'; n_healthy += 1

        overall_str = fmt_pct(stats['overall_yoy']) if stats['overall_yoy'] is not None else '—'
        cards.append(f"""<div class="card {cls}">
<div class="card-head">
  <div class="card-name">{escape(qx)}</div>
  <div class="card-stats">
    Top10 货值 <span class="num">{fmt_money(total)}</span> 万 ·
    总体同比 <span class="num">{overall_str}</span> ·
    下滑/增长子系列 <span class="num">{stats['decl_n']}/{stats['grow_n']}</span>
  </div>
</div>""")
        if issues:
            cards.append('<div class="card-issues">')
            for icon, text in issues[:3]:
                cards.append(f'<div class="issue"><span class="icon">{icon}</span>{text}</div>')
            cards.append('</div>')
        else:
            cards.append('<div class="no-issues">✓ Top 10 子系列表现稳定</div>')
        cards.append('</div>')

    blocks.append(f'<div class="summary-bar"><span class="summary-pill red">严重 {n_severe}</span><span class="summary-pill yellow">告警 {n_warn}</span><span class="summary-pill green">健康 {n_healthy}</span></div>')
    blocks.extend(cards)

    # ─── 四、Top 20 代理商 ───
    blocks.append('<h1 class="section" id="s4">四、Top 20 代理商 — 问题清单(按总货值降序)</h1>')
    dl_data = defaultdict(list)
    for r in data.get('全量_产品×代理商', []):
        if r['子系列'] in top10_set:
            dl_data[r['维度']].append(r)

    dl_list = []
    for dl, rows in dl_data.items():
        total = sum(r.get('货值_万', 0) for r in rows)
        dl_list.append((dl, total, rows))
    dl_list.sort(key=lambda x: -x[1])
    dl_list = dl_list[:20]

    n_severe = 0; n_warn = 0; n_healthy = 0
    cards = []
    for dl, total, rows in dl_list:
        issues, stats = analyze_district(rows)
        has_severe = any(i[0] in ('🚨','❌','📉') for i in issues)
        has_warn = any(i[0] in ('⚠️','💰','💲','🗺️','🟡') for i in issues)
        if has_severe: cls = 'severe'; n_severe += 1
        elif has_warn: cls = 'warning'; n_warn += 1
        else: cls = 'healthy'; n_healthy += 1

        overall_str = fmt_pct(stats['overall_yoy']) if stats['overall_yoy'] is not None else '—'
        cards.append(f"""<div class="card {cls}">
<div class="card-head">
  <div class="card-name">{escape(dl[:40])}</div>
  <div class="card-stats">
    Top10 货值 <span class="num">{fmt_money(total)}</span> 万 ·
    总体同比 <span class="num">{overall_str}</span> ·
    下滑/增长子系列 <span class="num">{stats['decl_n']}/{stats['grow_n']}</span>
  </div>
</div>""")
        if issues:
            cards.append('<div class="card-issues">')
            for icon, text in issues[:3]:
                cards.append(f'<div class="issue"><span class="icon">{icon}</span>{text}</div>')
            cards.append('</div>')
        else:
            cards.append('<div class="no-issues">✓ Top 10 子系列表现稳定</div>')
        cards.append('</div>')

    blocks.append(f'<div class="summary-bar"><span class="summary-pill red">严重 {n_severe}</span><span class="summary-pill yellow">告警 {n_warn}</span><span class="summary-pill green">健康 {n_healthy}</span></div>')
    blocks.extend(cards)

    blocks.append('<div style="margin-top:40px;padding:16px;background:#f0f4f8;border-radius:6px;color:#607d8b;font-size:12px;text-align:center">')
    blocks.append(f'本概览只列 Top 对象的关键问题。完整数据 / 全量筛选 / 服务商级穿透 → <a href="{escape(v4_filename)}" target="_blank" style="color:#3b82f6">打开 V4 全量交互版</a>')
    blocks.append('</div>')

    blocks.append('</div>')  # close .page
    return '\n'.join(blocks)


