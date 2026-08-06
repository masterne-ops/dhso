#!/usr/bin/env python3
"""爆款型号穿透分析 — HTML 渲染

输入:_hotsku_report_runner 出的 data dict
输出:single HTML body(外层 <html><body> 在 page 里加)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ──────────────────────────────────────────
# CSS / 通用样式
# ──────────────────────────────────────────

CSS = """
<style>
  body { font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', Arial, sans-serif;
         max-width: 1280px; margin: 20px auto; padding: 0 24px; color: #222;
         line-height: 1.55; }
  h1 { font-size: 24px; margin: 8px 0 14px; border-bottom: 2px solid #e1e4e8; padding-bottom: 8px; }
  h2 { font-size: 18px; margin: 32px 0 12px; padding: 6px 12px;
       background: linear-gradient(90deg, #1f6feb 0%, #58a6ff 100%);
       color: white; border-radius: 4px; }
  h3 { font-size: 15px; color: #333; margin: 18px 0 8px; }
  .meta { background: #f6f8fa; border: 1px solid #d1d5db; border-radius: 6px;
          padding: 12px 18px; margin-bottom: 18px; font-size: 13px; }
  .meta b { color: #1f6feb; }
  .meta-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px 18px; }
  .meta-item { display: flex; flex-direction: column; font-size: 12px; color: #666; }
  .meta-item span { font-size: 14px; color: #222; font-weight: 600; margin-top: 2px; }

  .rating { display: inline-block; padding: 4px 10px; border-radius: 999px;
            font-weight: 600; font-size: 14px; margin-right: 6px; }
  .rating.r-bull { background: #ffe8d6; color: #d63403; }
  .rating.r-good { background: #d4edda; color: #155724; }
  .rating.r-flat { background: #fff3cd; color: #856404; }
  .rating.r-new  { background: #d1ecf1; color: #0c5460; }
  .rating.r-bad  { background: #f8d7da; color: #721c24; }

  .finding-list { list-style: none; padding: 0; margin: 8px 0; }
  .finding { padding: 8px 12px; margin: 4px 0; border-left: 4px solid #58a6ff;
             background: #f6f8fa; border-radius: 0 4px 4px 0; }
  .finding.bull { border-color: #fd7e14; }
  .finding.good { border-color: #28a745; }
  .finding.warn { border-color: #ffc107; }
  .finding.bad  { border-color: #dc3545; }
  .finding.info { border-color: #17a2b8; }

  table { border-collapse: collapse; width: 100%; font-size: 13px; margin-bottom: 12px; }
  th { background: #f1f3f5; padding: 8px 10px; text-align: left;
       border-bottom: 2px solid #dee2e6; cursor: pointer; user-select: none; }
  th:hover { background: #e7ebef; }
  th.sort-asc::after { content: ' ▲'; color: #1f6feb; }
  th.sort-desc::after { content: ' ▼'; color: #1f6feb; }
  td { padding: 6px 10px; border-bottom: 1px solid #ecf0f4; }
  tr:nth-child(even) td { background: #fafbfc; }
  tr:hover td { background: #f0f6ff; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.pos { color: #28a745; font-weight: 600; }
  td.neg { color: #dc3545; font-weight: 600; }

  .badge { display: inline-block; padding: 1px 6px; border-radius: 4px;
           font-size: 11px; margin-right: 4px; }
  .badge.closed  { background: #f8d7da; color: #721c24; }
  .badge.signed  { background: #d4edda; color: #155724; }
  .badge.crosssell { background: #fff3cd; color: #856404; }
  .badge.unsigned { background: #e9ecef; color: #495057; }

  .chart-container { background: #fff; border: 1px solid #e1e4e8;
                     border-radius: 6px; padding: 16px; margin: 8px 0; }
  .chart { width: 100%; height: 260px; }
  .chart-legend { font-size: 12px; color: #555; text-align: right; margin-top: 4px; }
  .chart-legend span { display: inline-block; margin-left: 14px; }
  .chart-legend i { display: inline-block; width: 10px; height: 10px;
                    margin-right: 4px; vertical-align: middle; border-radius: 2px; }

  .total-row td { font-weight: 700; background: #fff8e6 !important;
                  border-top: 2px solid #ffc107; }
  .footer { font-size: 11px; color: #888; text-align: center; margin: 30px 0 14px;
            border-top: 1px solid #e1e4e8; padding-top: 14px; }

  .kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
              margin: 12px 0 18px; }
  .kpi { background: #fff; border: 1px solid #e1e4e8; border-radius: 6px;
         padding: 12px 14px; }
  .kpi .label { font-size: 11px; color: #888; }
  .kpi .value { font-size: 20px; font-weight: 700; color: #1f6feb;
                margin: 4px 0; line-height: 1.2; }
  .kpi .delta { font-size: 12px; color: #555; }
  .kpi .delta.pos { color: #28a745; }
  .kpi .delta.neg { color: #dc3545; }

  .toc { background: #f6f8fa; border: 1px solid #d1d5db; border-radius: 6px;
         padding: 10px 18px; margin-bottom: 18px; font-size: 13px; }
  .toc a { color: #1f6feb; text-decoration: none; margin-right: 16px; }
  .toc a:hover { text-decoration: underline; }

  .stat-card { display: grid; grid-template-columns: repeat(8, 1fr); gap: 8px;
               margin: 6px 0 12px; }
  .stat-cell { background: #fafbfc; border: 1px solid #ecf0f4;
               padding: 6px 8px; border-radius: 4px; text-align: center; }
  .stat-cell .l { font-size: 10px; color: #888; }
  .stat-cell .v { font-size: 14px; font-weight: 600; color: #222; }
</style>
"""


# ──────────────────────────────────────────
# 工具
# ──────────────────────────────────────────


def fmt_num(x, default='—'):
    if x is None or (isinstance(x, float) and x != x):
        return default
    if abs(x) >= 100000:
        return f'{x:,.0f}'
    return f'{x:,.1f}' if isinstance(x, float) and x != int(x) else f'{int(x):,}'


def fmt_pct(x, default='—'):
    if x is None:
        return default
    sign = '+' if x > 0 else ''
    return f'{sign}{x:.1f}%'


def fmt_money_wan(x, default='—'):
    if x is None:
        return default
    return f'¥{x/10000:.1f}万' if x >= 1 else f'¥{x:.0f}'


def fmt_yuan(x, default='—'):
    if x is None:
        return default
    return f'¥{x:,.0f}'


def pct_cls(x):
    if x is None or x == 0:
        return ''
    return 'pos' if x > 0 else 'neg'


# ──────────────────────────────────────────
# Section 渲染
# ──────────────────────────────────────────


def render_top(data):
    m = data['_meta']
    diag = data.get('diagnose', {})
    total = data.get('sku_total', {})

    rating_map = {
        '🔥': ('r-bull', '爆款上扬'),
        '📈': ('r-good', '稳健增长'),
        '🟡': ('r-flat', '持平'),
        '🟢': ('r-new', '新品爬坡'),
        '⚠️': ('r-bad',  '衰退预警'),
    }
    cls, label = rating_map.get(diag.get('rating'), ('r-flat', '—'))

    cur_wan = (total.get('货值') or 0) / 10000
    yoy_wan = (total.get('同期货值') or 0) / 10000
    mom_wan = (total.get('环期货值') or 0) / 10000

    findings_html = ''
    for f in diag.get('findings', []):
        findings_html += f'<li class="finding {f["tone"]}">{f["msg"]}</li>'

    return f"""
<h1>🔍 爆款穿透分析 — 「{m['keyword']}」</h1>

<div class="meta">
  <div class="meta-grid">
    <div class="meta-item">关键字 <span>{m['keyword']}</span></div>
    <div class="meta-item">匹配字段 <span>{m['match_field']}</span></div>
    <div class="meta-item">命中 SKU <span>{len(data.get('sku_list', []))} 个</span></div>
    <div class="meta-item">地域 <span>{m['city']}</span></div>
    <div class="meta-item">评估期 <span>{m['period_start']} ~ {m['period_end']}({m['n_months']} 月)</span></div>
    <div class="meta-item">同期 <span>{m['yoy_start']} ~ {m['yoy_end']}</span></div>
    <div class="meta-item">环期 <span>{m['mom_start']} ~ {m['mom_end']}</span></div>
    <div class="meta-item">生成时间 <span>{m['generated_at']}</span></div>
  </div>
</div>

<div class="kpi-grid">
  <div class="kpi"><div class="label">当期货值</div><div class="value">¥{cur_wan:.1f}万</div>
    <div class="delta">{total.get('台数', 0):,} 台</div></div>
  <div class="kpi"><div class="label">同比</div>
    <div class="value">{fmt_pct(total.get('同比'))}</div>
    <div class="delta">同期 ¥{yoy_wan:.1f}万</div></div>
  <div class="kpi"><div class="label">环比</div>
    <div class="value">{fmt_pct(total.get('环比'))}</div>
    <div class="delta">环期 ¥{mom_wan:.1f}万</div></div>
  <div class="kpi"><div class="label">综合评级</div>
    <div class="value"><span class="rating {cls}">{diag.get('rating', '')} {label}</span></div>
    <div class="delta">均价 {fmt_yuan(total.get('均价'))}</div></div>
</div>

<h3>🤖 自动诊断</h3>
<ul class="finding-list">
  {findings_html or '<li class="finding info">数据不足以触发诊断规则</li>'}
</ul>

<div class="toc">
  目录:
  <a href="#sku">① SKU 列表</a>
  <a href="#trend">② 月度走势</a>
  <a href="#price">③ 价格分布</a>
  <a href="#city">④ 11 地市</a>
  <a href="#district">⑤ Top 区县</a>
  <a href="#dealer">⑥ 代理商</a>
  <a href="#provider">⑦ 服务商生态</a>
  <a href="#geo">⑧ 跨地域流向</a>
  <a href="#action">⑨ 行动建议</a>
</div>
"""


def render_sku_list(data):
    skus = data.get('sku_list', [])
    total = data.get('sku_total', {})

    rows = ''
    for s in skus:
        rows += f"""
        <tr>
          <td>{s['内部型号'] or ''}</td>
          <td>{s['外部型号'] or ''}</td>
          <td>{(s.get('子系列') or '')}</td>
          <td>{(s.get('三级') or '')}</td>
          <td class="num">{s['台数']:,}</td>
          <td class="num">¥{s['货值']/10000:.1f}万</td>
          <td class="num">{fmt_yuan(s['均价'])}</td>
          <td class="num {pct_cls(s.get('同比'))}">{fmt_pct(s.get('同比'))}</td>
          <td class="num {pct_cls(s.get('环比'))}">{fmt_pct(s.get('环比'))}</td>
          <td class="num {pct_cls(s.get('均价同比'))}">{fmt_pct(s.get('均价同比'))}</td>
        </tr>
        """

    # 合计行
    total_row = f"""
    <tr class="total-row">
      <td colspan="4">合计({len(skus)} 个 SKU)</td>
      <td class="num">{total.get('台数', 0):,}</td>
      <td class="num">¥{(total.get('货值') or 0)/10000:.1f}万</td>
      <td class="num">{fmt_yuan(total.get('均价'))}</td>
      <td class="num {pct_cls(total.get('同比'))}">{fmt_pct(total.get('同比'))}</td>
      <td class="num {pct_cls(total.get('环比'))}">{fmt_pct(total.get('环比'))}</td>
      <td class="num {pct_cls(total.get('均价同比'))}">{fmt_pct(total.get('均价同比'))}</td>
    </tr>
    """

    return f"""
<h2 id="sku">① 命中的 SKU 列表</h2>
<p style="font-size:12px;color:#666;">点表头排序。子系列/三级 按 mode 归类。</p>
<table data-sortable>
  <thead>
    <tr>
      <th>内部型号</th>
      <th>外部型号</th>
      <th>子系列</th>
      <th>三级</th>
      <th class="num">台数</th>
      <th class="num">货值</th>
      <th class="num">均价</th>
      <th class="num">同比</th>
      <th class="num">环比</th>
      <th class="num">均价同比</th>
    </tr>
  </thead>
  <tbody>
    {rows}
  </tbody>
  <tfoot>{total_row}</tfoot>
</table>
"""


def render_monthly_trend(data):
    trend = data.get('monthly_trend', [])
    if not trend:
        return '<h2 id="trend">② 月度走势</h2><p>无数据</p>'

    # 评估期高亮
    m = data['_meta']
    period_start, period_end = m['period_start'], m['period_end']

    # SVG inline 折线 + 柱状
    w, h = 1080, 240
    pad_left, pad_right, pad_top, pad_bottom = 60, 60, 20, 40

    months = [t['月份'] for t in trend]
    vals = [t['货值_万'] or 0 for t in trend]
    qtys = [t['台数'] or 0 for t in trend]
    avgs = [t['均价'] or 0 for t in trend]

    if not vals:
        return '<h2 id="trend">② 月度走势</h2><p>无数据</p>'

    max_v = max(vals) * 1.15 or 1
    max_avg = max(avgs) * 1.15 or 1

    plot_w = w - pad_left - pad_right
    plot_h = h - pad_top - pad_bottom

    def x_of(i):
        if len(months) <= 1:
            return pad_left + plot_w / 2
        return pad_left + i / (len(months) - 1) * plot_w

    def y_value(v):
        return pad_top + plot_h - (v / max_v) * plot_h

    def y_avg(v):
        return pad_top + plot_h - (v / max_avg) * plot_h

    # 柱:货值万
    bar_w = max(20, plot_w / len(months) * 0.55)
    bars = []
    for i, t in enumerate(trend):
        x = x_of(i) - bar_w / 2
        y = y_value(vals[i])
        in_period = period_start <= t['月份'] <= period_end
        fill = '#1f6feb' if in_period else '#9ec5ff'
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
            f'height="{plot_h - (y - pad_top):.1f}" fill="{fill}" rx="2"/>'
            f'<text x="{x_of(i):.1f}" y="{y - 3:.1f}" text-anchor="middle" '
            f'font-size="10" fill="#444">{vals[i]:.1f}</text>'
        )

    # 折线:均价
    pts = ' '.join(f'{x_of(i):.1f},{y_avg(avgs[i]):.1f}' for i in range(len(trend)))
    avg_pts = ''.join(
        f'<circle cx="{x_of(i):.1f}" cy="{y_avg(avgs[i]):.1f}" r="3" fill="#fd7e14"/>'
        for i in range(len(trend))
    )

    # X 轴标签
    x_labels = ''.join(
        f'<text x="{x_of(i):.1f}" y="{h - 8}" text-anchor="middle" '
        f'font-size="10" fill="#555">{months[i][-5:]}</text>'
        for i in range(len(months))
    )

    # 数据明细表
    tr_rows = ''
    for t in trend:
        in_period = period_start <= t['月份'] <= period_end
        cls = 'style="background:#fff8e6"' if in_period else ''
        tr_rows += f"""
        <tr {cls}>
          <td>{t['月份']}</td>
          <td class="num">{t['台数']:,}</td>
          <td class="num">{t['货值_万']:.1f}万</td>
          <td class="num">¥{t['均价']:.0f}</td>
        </tr>
        """

    return f"""
<h2 id="trend">② 月度走势(近 {len(months)} 个月)</h2>
<p style="font-size:12px;color:#666;">柱状 = 月度货值(深蓝 = 评估期),折线 = 均价。</p>
<div class="chart-container">
<svg viewBox="0 0 {w} {h}" class="chart" preserveAspectRatio="xMidYMid meet">
  <g>
    {''.join(bars)}
    <polyline points="{pts}" fill="none" stroke="#fd7e14" stroke-width="2"/>
    {avg_pts}
    {x_labels}
    <text x="{pad_left - 8}" y="{pad_top}" text-anchor="end" font-size="11" fill="#666">{max_v:.0f}万</text>
    <text x="{w - pad_right + 8}" y="{pad_top}" text-anchor="start" font-size="11" fill="#fd7e14">¥{max_avg:.0f}</text>
    <line x1="{pad_left}" y1="{pad_top + plot_h}" x2="{w - pad_right}" y2="{pad_top + plot_h}" stroke="#ccc" stroke-width="1"/>
  </g>
</svg>
<div class="chart-legend">
  <span><i style="background:#1f6feb"></i>货值(评估期)</span>
  <span><i style="background:#9ec5ff"></i>货值(对照)</span>
  <span><i style="background:#fd7e14"></i>均价(右轴)</span>
</div>
</div>

<table>
  <thead><tr><th>月份</th><th class="num">台数</th><th class="num">货值(万)</th><th class="num">均价</th></tr></thead>
  <tbody>{tr_rows}</tbody>
</table>
"""


def render_price_dist(data):
    pd_ = data.get('price_dist', {})
    cur = pd_.get('当期')
    yoy = pd_.get('同期')

    if not cur:
        return '<h2 id="price">③ 价格分布</h2><p>无数据</p>'

    cells = ['n', 'min', 'p25', 'median', 'mean', 'p75', 'max', 'std']
    labels = {'n': '样本', 'min': '最低', 'p25': 'P25', 'median': '中位',
              'mean': '均价', 'p75': 'P75', 'max': '最高', 'std': '标准差'}

    cur_cells = ''.join(
        f'<div class="stat-cell"><div class="l">{labels[c]}</div>'
        f'<div class="v">{cur[c]}</div></div>'
        for c in cells
    )
    yoy_cells = ''
    if yoy:
        yoy_cells = ''.join(
            f'<div class="stat-cell"><div class="l">{labels[c]}</div>'
            f'<div class="v">{yoy[c]}</div></div>'
            for c in cells
        )

    diff = None
    if yoy and cur['mean'] and yoy['mean']:
        diff = (cur['mean'] - yoy['mean']) / yoy['mean'] * 100

    diff_html = ''
    if diff is not None:
        cls = 'pos' if diff > 0 else 'neg'
        diff_html = f'<p style="margin-top:8px"><b>均价同比变化:</b><span class="{cls}" style="font-weight:600">{diff:+.1f}%</span></p>'

    return f"""
<h2 id="price">③ 价格分布</h2>
<h3>评估期价格分布(¥)</h3>
<div class="stat-card">{cur_cells}</div>
{'<h3>同期价格分布(¥)</h3><div class="stat-card">' + yoy_cells + '</div>' if yoy_cells else '<p style="color:#888;font-size:12px">同期无数据(新品)</p>'}
{diff_html}
"""


def render_city(data):
    cities = data.get('city_breakdown', [])
    if not cities:
        return '<h2 id="city">④ 11 地市</h2><p>无数据</p>'

    rows = ''
    total_v = sum(c['货值_万'] for c in cities) or 1
    for c in cities:
        share = c['货值_万'] / total_v * 100
        rows += f"""
        <tr>
          <td>{c['地市']}</td>
          <td class="num">{c['台数']:,}</td>
          <td class="num">¥{c['货值_万']:.1f}万</td>
          <td class="num">{share:.1f}%</td>
          <td class="num">¥{c['同期_万']:.1f}万</td>
          <td class="num {pct_cls(c.get('同比'))}">{fmt_pct(c.get('同比'))}</td>
          <td class="num {pct_cls(c.get('环比'))}">{fmt_pct(c.get('环比'))}</td>
        </tr>
        """

    return f"""
<h2 id="city">④ 11 地市分布</h2>
<table data-sortable>
  <thead><tr>
    <th>地市</th><th class="num">台数</th><th class="num">当期货值</th>
    <th class="num">份额</th><th class="num">同期货值</th>
    <th class="num">同比</th><th class="num">环比</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
"""


def render_district(data):
    rows_data = data.get('district_breakdown', [])
    if not rows_data:
        return '<h2 id="district">⑤ Top 30 区县</h2><p>无数据</p>'

    rows = ''
    for r in rows_data:
        rows += f"""
        <tr>
          <td>{r['区县']}</td>
          <td>{r['地市']}</td>
          <td class="num">{r['台数']:,}</td>
          <td class="num">¥{r['货值_万']:.1f}万</td>
          <td class="num">¥{r['同期_万']:.1f}万</td>
          <td class="num {pct_cls(r.get('同比'))}">{fmt_pct(r.get('同比'))}</td>
        </tr>
        """

    return f"""
<h2 id="district">⑤ Top 30 区县(当期货值降序)</h2>
<table data-sortable>
  <thead><tr>
    <th>区县</th><th>地市</th><th class="num">台数</th>
    <th class="num">当期</th><th class="num">同期</th><th class="num">同比</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
"""


def render_dealer(data):
    rows_data = data.get('dealer_breakdown', [])
    if not rows_data:
        return '<h2 id="dealer">⑥ 代理商分销</h2><p>无数据</p>'

    rows = ''
    for r in rows_data:
        rows += f"""
        <tr>
          <td>{r['代理商']}</td>
          <td class="num">{r['台数']:,}</td>
          <td class="num">¥{r['货值_万']:.1f}万</td>
          <td class="num">{r['份额']:.1f}%</td>
          <td class="num">¥{r['同期_万']:.1f}万</td>
          <td class="num {pct_cls(r.get('同比'))}">{fmt_pct(r.get('同比'))}</td>
        </tr>
        """

    return f"""
<h2 id="dealer">⑥ 代理商分销份额</h2>
<p style="font-size:12px;color:#666;">Top 15。「(无签约代理商)」代表 product_flow 里没有签约代理商记录的部分。</p>
<table data-sortable>
  <thead><tr>
    <th>代理商</th><th class="num">台数</th><th class="num">当期</th>
    <th class="num">份额</th><th class="num">同期</th><th class="num">同比</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
"""


def render_provider(data):
    pd_ = data.get('provider_data', {})
    top = pd_.get('top_providers', [])
    kinds = pd_.get('kind_dist', [])
    signs = pd_.get('sign_dist', [])
    health = pd_.get('health', {})

    # Top 服务商表
    top_rows = ''
    for p in top:
        badges = ''
        if p.get('已关闭'):
            badges += '<span class="badge closed">🚫 已关闭</span>'
        if p.get('签约状态') == '签约采购':
            badges += '<span class="badge signed">签约</span>'
        elif p.get('签约状态') == '跨渠道采购':
            badges += '<span class="badge crosssell">跨渠道</span>'
        elif p.get('签约状态') == '无签约':
            badges += '<span class="badge unsigned">无签约</span>'
        top_rows += f"""
        <tr>
          <td>{p['服务商']} {badges}</td>
          <td>{p['地市'] or ''}</td>
          <td>{p['客户类型'] or '未签约'}</td>
          <td>{p['签约代理商'] or ''}</td>
          <td class="num">{p['台数']:,}</td>
          <td class="num">¥{p['货值_万']:.1f}万</td>
        </tr>
        """

    # 客户类型分布
    kind_rows = ''
    for k in kinds:
        kind_rows += f"""
        <tr>
          <td>{k['类型']}</td>
          <td class="num">{k['服务商数']:,}</td>
          <td class="num">{k['台数']:,}</td>
          <td class="num">¥{k['货值_万']:.1f}万</td>
          <td class="num">{k['货值占比']:.1f}%</td>
        </tr>
        """

    # 签约状态分布
    sign_rows = ''
    for s in signs:
        sign_rows += f"""
        <tr>
          <td>{s['状态']}</td>
          <td class="num">{s['服务商数']:,}</td>
          <td class="num">{s['台数']:,}</td>
          <td class="num">¥{s['货值_万']:.1f}万</td>
          <td class="num">{s['货值占比']:.1f}%</td>
        </tr>
        """

    health_html = ''
    if health:
        h = health
        new_rate = h.get('new', 0) / h.get('cur_set', 1) * 100 if h.get('cur_set') else 0
        health_html = f"""
        <h3>渠道健康度</h3>
        <p style="font-size:12px;color:#666;">服务商集合 = 当期内有该产品上线的(去马甲)。</p>
        <div class="stat-card" style="grid-template-columns:repeat(4,1fr)">
          <div class="stat-cell"><div class="l">当期服务商</div><div class="v">{h.get('cur_set', 0)}</div></div>
          <div class="stat-cell"><div class="l">同期服务商</div><div class="v">{h.get('yoy_set', 0)}</div></div>
          <div class="stat-cell"><div class="l">同比新增</div><div class="v" style="color:#28a745">+{h.get('new', 0)}({new_rate:.0f}%)</div></div>
          <div class="stat-cell"><div class="l">同比流失</div><div class="v" style="color:#dc3545">-{h.get('churn', 0)}</div></div>
        </div>
        <div class="stat-card" style="grid-template-columns:repeat(4,1fr)">
          <div class="stat-cell"><div class="l">环期服务商</div><div class="v">{h.get('mom_set', 0)}</div></div>
          <div class="stat-cell"><div class="l">环比新增</div><div class="v" style="color:#28a745">+{h.get('mom_new', 0)}</div></div>
          <div class="stat-cell"><div class="l">环比流失</div><div class="v" style="color:#dc3545">-{h.get('mom_churn', 0)}</div></div>
          <div class="stat-cell"><div class="l">扩散速率</div><div class="v">{new_rate:.0f}%</div></div>
        </div>
        """

    return f"""
<h2 id="provider">⑦ 服务商生态(🎯 红包扫码口径,已剔除马甲)</h2>
{health_html}

<h3>客户类型分布</h3>
<table>
  <thead><tr><th>类型</th><th class="num">服务商数</th><th class="num">台数</th>
    <th class="num">货值</th><th class="num">占比</th></tr></thead>
  <tbody>{kind_rows or '<tr><td colspan="5">无数据</td></tr>'}</tbody>
</table>

<h3>签约状态分布</h3>
<p style="font-size:12px;color:#666;">「签约采购」= 服务商出货代理商正好是签约代理商;「跨渠道采购」= 不一致(price-cross);「无签约」= 服务商无签约代理商。</p>
<table>
  <thead><tr><th>状态</th><th class="num">服务商数</th><th class="num">台数</th>
    <th class="num">货值</th><th class="num">占比</th></tr></thead>
  <tbody>{sign_rows or '<tr><td colspan="5">无数据</td></tr>'}</tbody>
</table>

<h3>Top 30 服务商(当期货值降序)</h3>
<table data-sortable>
  <thead><tr><th>服务商</th><th>地市</th><th>客户类型</th><th>签约代理商</th>
    <th class="num">台数</th><th class="num">货值</th></tr></thead>
  <tbody>{top_rows or '<tr><td colspan="6">无数据</td></tr>'}</tbody>
</table>
"""


def render_geo(data):
    g = data.get('cross_geo', {})
    if not g or g.get('total_n', 0) == 0:
        return '<h2 id="geo">⑧ 跨地域流向</h2><p>无数据</p>'

    paths_rows = ''
    for p in g.get('top_paths', []):
        paths_rows += f"""
        <tr>
          <td>{p['出货'] or '(空)'}</td>
          <td>{p['上线'] or '(空)'}</td>
          <td class="num">{p['台数']:,}</td>
        </tr>
        """

    return f"""
<h2 id="geo">⑧ 跨地域流向</h2>
<div class="stat-card" style="grid-template-columns:repeat(3,1fr)">
  <div class="stat-cell"><div class="l">总台数</div><div class="v">{g['total_n']:,}</div></div>
  <div class="stat-cell"><div class="l">异城率</div><div class="v">{g['rate_diff_city']:.1f}%</div></div>
  <div class="stat-cell"><div class="l">异省率</div><div class="v">{g['rate_diff_prov']:.1f}%</div></div>
</div>
<h3>Top 5 异城货流路径</h3>
<table>
  <thead><tr><th>出货城市</th><th>上线城市</th><th class="num">台数</th></tr></thead>
  <tbody>{paths_rows or '<tr><td colspan="3">无异城</td></tr>'}</tbody>
</table>
"""


def render_action(data):
    diag = data.get('diagnose', {})
    findings = diag.get('findings', [])

    if not findings:
        body = '<p>暂无自动诊断触发的行动建议。</p>'
    else:
        items = ''.join(f'<li class="finding {f["tone"]}">{f["msg"]}</li>'
                        for f in findings)
        body = f'<ul class="finding-list">{items}</ul>'

    return f"""
<h2 id="action">⑨ 行动建议</h2>
{body}

<p style="font-size:12px;color:#888;margin-top:14px;">
基于规则的自动诊断(同比 / 环比 / 价格 / 地市集中度 / 渠道扩散 / 跨渠道占比)。
不包含外部市场容量 / 竞品 / 供应链等数据。
</p>
"""


# ──────────────────────────────────────────
# JS:排序
# ──────────────────────────────────────────

SORT_JS = """
<script>
(function() {
  function parseNum(s) {
    if (!s) return NaN;
    s = s.replace(/[¥,万%\\s]/g, '');
    var f = parseFloat(s);
    return isNaN(f) ? -Infinity : f;
  }
  document.querySelectorAll('table[data-sortable]').forEach(function(tbl) {
    var heads = tbl.querySelectorAll('thead th');
    heads.forEach(function(th, idx) {
      th.addEventListener('click', function() {
        var asc = !th.classList.contains('sort-asc');
        heads.forEach(function(h) { h.classList.remove('sort-asc', 'sort-desc'); });
        th.classList.add(asc ? 'sort-asc' : 'sort-desc');
        var tbody = tbl.querySelector('tbody');
        var rows = Array.from(tbody.querySelectorAll('tr'));
        var isNum = th.classList.contains('num');
        rows.sort(function(a, b) {
          var ax = a.cells[idx].textContent.trim();
          var bx = b.cells[idx].textContent.trim();
          if (isNum) {
            ax = parseNum(ax); bx = parseNum(bx);
          }
          if (ax < bx) return asc ? -1 : 1;
          if (ax > bx) return asc ? 1 : -1;
          return 0;
        });
        rows.forEach(function(r) { tbody.appendChild(r); });
      });
    });
  });
})();
</script>
"""


# ──────────────────────────────────────────
# 入口
# ──────────────────────────────────────────


def render_report(data: dict) -> str:
    if 'error' in data:
        return f'<h1>❌ 错误</h1><p>{data["error"]}</p>'

    parts = [
        CSS,
        render_top(data),
        render_sku_list(data),
        render_monthly_trend(data),
        render_price_dist(data),
        render_city(data),
        render_district(data),
        render_dealer(data),
        render_provider(data),
        render_geo(data),
        render_action(data),
        f'<div class="footer">爆款穿透分析 · {data["_meta"]["generated_at"]} · 浙江省 SO 数据分析平台</div>',
        SORT_JS,
    ]
    return '\n'.join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True, help='runner 出的 JSON')
    ap.add_argument('--out', required=True, help='输出 HTML')
    args = ap.parse_args()

    data = json.loads(Path(args.data).read_text(encoding='utf-8'))
    body = render_report(data)
    label = f'{data["_meta"]["keyword"]}_{data["_meta"]["period_start"]}_to_{data["_meta"]["period_end"]}'
    html = (
        f"<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>爆款穿透 · {label}</title></head><body>\n"
        f"{body}\n</body></html>"
    )
    Path(args.out).write_text(html, encoding='utf-8')
    print(f'✅ 输出 {args.out}({len(html)/1024:.0f} KB)', file=sys.stderr)


if __name__ == '__main__':
    main()
