#!/usr/bin/env python3
"""产品维度经营报告 v4 — HTML + 内嵌 JS 客户端筛选

特点:
- 数据全量嵌入(~4MB JSON)
- 客户端 JS 提供:多选筛选 / 关键字搜索 / 表头排序 / 标识过滤
- 单文件,可离线打开
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from html import escape
import pandas as pd

OUT_DIR = Path(__file__).parent

TH_UP = 0.30
TH_DOWN = -0.20


# ─── 格式化辅助 ───

def fmt_pct(v, sign=True):
    if v is None or pd.isna(v): return '—'
    s = '+' if sign and v >= 0 else ''
    return f'{s}{v*100:.1f}%'


def fmt_pct_plain(v):
    if v is None or pd.isna(v): return '—'
    return f'{v*100:.1f}%'


def fmt_money(v, decimals=2):
    if v is None or pd.isna(v): return '—'
    try: return f'{float(v):,.{decimals}f}'
    except Exception: return '—'


def fmt_int(v):
    if v is None or pd.isna(v): return '—'
    try: return f'{int(v):,}'
    except Exception: return '—'


def pct_class(v):
    if v is None or pd.isna(v): return ''
    if v >= TH_UP: return 'up'
    if v <= TH_DOWN: return 'down'
    return ''


CSS = """
<style>
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Arial,sans-serif;
    margin:0;background:#f7f9fc;color:#1a3c5e;line-height:1.6;font-size:13px}
.page{max-width:1400px;margin:0 auto;background:white;padding:24px 32px;box-shadow:0 0 20px rgba(0,0,0,.05)}
h1.title{text-align:center;margin:0 0 8px;font-size:26px}
.meta{text-align:center;color:#607d8b;font-size:12px;margin-bottom:24px;
    padding-bottom:12px;border-bottom:2px solid #e3edf5}
.meta p{margin:3px 0}
.toc{background:#f0f4f8;padding:14px 20px;border-radius:6px;margin-bottom:24px;
    border-left:4px solid #3b82f6;position:sticky;top:0;z-index:100}
.toc h3{margin:0 0 6px;font-size:14px}
.toc ul{margin:0;padding-left:18px;columns:3;column-gap:30px;font-size:12px}
.toc li{padding:2px 0;list-style:none;break-inside:avoid}
.toc li::before{content:'▸ ';color:#3b82f6}
.toc a{color:#1a3c5e;text-decoration:none}
.toc a:hover{color:#3b82f6;text-decoration:underline}
h1,h2,h3,h4{color:#1a3c5e}
h1{border-bottom:3px solid #1a3c5e;padding-bottom:6px;margin-top:32px;font-size:20px}
h2{color:#2563eb;margin-top:20px;font-size:16px;border-left:4px solid #2563eb;padding-left:10px}
h3{font-size:14px;margin-top:14px}
p.note{color:#607d8b;font-size:11px;background:#f0f4f8;padding:6px 10px;border-radius:4px;margin:8px 0}
p.warn{color:#92400e;background:#fef3c7;padding:6px 10px;border-radius:4px;margin:8px 0}
table.data{border-collapse:collapse;width:100%;margin:10px 0 20px;font-size:11px}
table.data th{background:#1a3c5e;color:white;padding:6px 8px;text-align:left;font-weight:600;
    cursor:pointer;user-select:none;position:sticky;top:0}
table.data th:hover{background:#2563eb}
table.data th.sortable::after{content:' ⇅';opacity:.4;font-size:9px}
table.data th.sort-asc::after{content:' ↑';opacity:1}
table.data th.sort-desc::after{content:' ↓';opacity:1}
table.data td{padding:4px 8px;border-bottom:1px solid #e3edf5;vertical-align:top}
table.data tbody tr:hover{background:#f7f9fc}
table.data td.up{background:#d1fae5;color:#065f46}
table.data td.down{background:#fee2e2;color:#991b1b}
table.data td.muted{color:#94a3b8}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:8px;background:#f0f4f8;
    border-radius:4px;margin-bottom:8px;font-size:12px}
.toolbar label{font-weight:600;margin-right:4px;color:#475569}
.toolbar select,.toolbar input{padding:4px 8px;font-size:12px;border:1px solid #cbd5e1;border-radius:3px}
.toolbar input[type="text"]{min-width:160px}
.toolbar .stat{color:#607d8b;font-size:11px;margin-left:auto}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}
.kpi{background:#f0f4f8;padding:10px 14px;border-radius:6px;border-left:3px solid #3b82f6}
.kpi .label{color:#607d8b;font-size:11px}
.kpi .val{font-size:18px;font-weight:700;color:#1a3c5e;margin-top:2px}
.kpi .val.up{color:#065f46}
.kpi .val.down{color:#991b1b}
.focus-banner{background:linear-gradient(90deg,#fef3c7 0%,#fbbf24 100%);color:#78350f;
    padding:10px 16px;border-radius:6px;margin:12px 0;font-weight:600}
.action-card{border:1px solid #e3edf5;border-radius:6px;padding:10px 14px;margin:6px 0;background:#fafbfc}
.action-card .title{font-weight:700}
details{margin:8px 0}
summary{cursor:pointer;padding:6px 10px;background:#e3edf5;border-radius:4px;font-weight:600}
summary:hover{background:#cbd5e1}
.checkbox-group{display:flex;flex-wrap:wrap;gap:4px 8px}
.checkbox-group label{font-weight:normal;padding:2px 6px;background:white;border:1px solid #cbd5e1;
    border-radius:3px;font-size:11px;cursor:pointer}
.checkbox-group label:has(input:checked){background:#3b82f6;color:white;border-color:#3b82f6}
.checkbox-group input{margin-right:3px}
@media print{body{background:white}.page{box-shadow:none;padding:0;max-width:100%}.toc{position:static}}
</style>
"""

JS = """
<script>
// 通用表格控制器
class TableCtrl {
  constructor(tableEl, opts={}){
    this.t = tableEl;
    this.tbody = tableEl.querySelector('tbody');
    this.allRows = Array.from(this.tbody.querySelectorAll('tr'));
    this.opts = opts;
    this.bindSort();
    this.bindToolbar();
    this.update();
  }
  bindSort(){
    this.t.querySelectorAll('thead th').forEach((th, idx) => {
      if (th.dataset.sortable === 'false') return;
      th.classList.add('sortable');
      th.dataset.col = idx;
      th.addEventListener('click', () => this.sortBy(idx, th));
    });
  }
  sortBy(idx, th){
    const cur = th.classList.contains('sort-asc') ? 'asc' :
                th.classList.contains('sort-desc') ? 'desc' : null;
    const next = cur === 'asc' ? 'desc' : 'asc';
    this.t.querySelectorAll('thead th').forEach(x => x.classList.remove('sort-asc','sort-desc'));
    th.classList.add('sort-' + next);
    const rows = this.allRows.slice();
    rows.sort((a, b) => {
      const av = (a.children[idx]?.dataset.val ?? a.children[idx]?.textContent ?? '').toString();
      const bv = (b.children[idx]?.dataset.val ?? b.children[idx]?.textContent ?? '').toString();
      const an = parseFloat(av.replace(/[,%+元¥]/g,''));
      const bn = parseFloat(bv.replace(/[,%+元¥]/g,''));
      if (!isNaN(an) && !isNaN(bn)) {
        return next === 'asc' ? an - bn : bn - an;
      }
      return next === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    this.tbody.innerHTML = '';
    rows.forEach(r => this.tbody.appendChild(r));
    this.allRows = rows;
    this.update(false);
  }
  bindToolbar(){
    const tb = this.opts.toolbar;
    if (!tb) return;
    tb.querySelectorAll('input,select').forEach(el => {
      el.addEventListener('input', () => this.update());
      el.addEventListener('change', () => this.update());
    });
  }
  update(refresh=true){
    const tb = this.opts.toolbar;
    let visible = 0;
    this.allRows.forEach(r => {
      let show = true;
      if (tb) {
        // 文本搜索
        const searches = tb.querySelectorAll('input[data-search]');
        searches.forEach(inp => {
          const q = inp.value.trim().toLowerCase();
          if (q) {
            const cols = inp.dataset.search.split(',').map(c => parseInt(c));
            const match = cols.some(c => (r.children[c]?.textContent || '').toLowerCase().includes(q));
            if (!match) show = false;
          }
        });
        // 下拉筛选(单列)
        const selects = tb.querySelectorAll('select[data-col]');
        selects.forEach(sel => {
          const v = sel.value;
          if (v) {
            const col = parseInt(sel.dataset.col);
            const cellVal = (r.children[col]?.textContent || '').trim();
            if (cellVal !== v) show = false;
          }
        });
        // 多选 checkbox(子系列等)
        const cbgrps = tb.querySelectorAll('.checkbox-group[data-col]');
        cbgrps.forEach(grp => {
          const col = parseInt(grp.dataset.col);
          const checked = Array.from(grp.querySelectorAll('input:checked')).map(i => i.value);
          if (checked.length > 0) {
            const cellVal = (r.children[col]?.textContent || '').trim();
            if (!checked.includes(cellVal)) show = false;
          }
        });
      }
      r.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    if (tb) {
      const stat = tb.querySelector('.stat');
      if (stat) stat.textContent = `显示 ${visible} / ${this.allRows.length} 行`;
    }
  }
}

// 初始化所有带 data-table 的表
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-tablectrl]').forEach(container => {
    const table = container.querySelector('table');
    const toolbar = container.querySelector('.toolbar');
    new TableCtrl(table, {toolbar});
  });
});
</script>
"""


# ─── 渲染辅助 ───

def td_cell(val, cls=''):
    cls_str = f' class="{cls}"' if cls else ''
    return f'<td{cls_str}>{escape(str(val))}</td>'


def td_num(val, raw, cls=''):
    """带 data-val 用于排序"""
    cls_str = f' class="{cls}"' if cls else ''
    return f'<td{cls_str} data-val="{raw if raw is not None else ""}">{escape(str(val))}</td>'


def render_simple_table(rows, headers_sortable=None):
    """简单表(无 JS 交互)"""
    if not rows: return '<p>(无数据)</p>'
    h = '<thead><tr>' + ''.join(f'<th data-sortable="false">{escape(str(c))}</th>' for c in rows[0]) + '</tr></thead>'
    body = '<tbody>'
    for r in rows[1:]:
        cells = []
        for v in r:
            if isinstance(v, tuple):
                val, cls = v
                cells.append(td_cell(val, cls))
            else:
                cells.append(td_cell(v))
        body += '<tr>' + ''.join(cells) + '</tr>'
    body += '</tbody>'
    return f'<table class="data">{h}{body}</table>'


def render_report(data):
    meta = data['_meta']
    blocks = [CSS]
    blocks.append('<div class="page">')

    # 标题
    blocks.append('<h1 class="title">产品维度经营报告 (v4 · 交互版)</h1>')
    blocks.append('<div class="meta">')
    blocks.append(f'<p>地域:{escape(meta["city"])}  |  评估期:<b>{meta["period_start"]} ~ {meta["period_end"]}</b>({meta["n_months"]} 个月)</p>')
    blocks.append(f'<p>同期:{meta["yoy_start"]} ~ {meta["yoy_end"]}  |  环期:{meta["mom_start"]} ~ {meta["mom_end"]}  |  生成时间:{meta["generated_at"]}</p>')
    blocks.append('<p style="color:#3b82f6;font-weight:600">💡 报告所有表格都支持:点表头排序 · 多维筛选 · 关键字搜索</p>')
    blocks.append('</div>')

    # 目录
    blocks.append('<div class="toc"><h3>📑 目录(可点跳转)</h3><ul>')
    for sid, label in [
        ('s1','一、全省总览'), ('s2','二、产品 × 区县'), ('s3','三、产品 × 代理商'),
        ('s4','四、服务商汇总'), ('s5','五、子系列 × 地市透视'), ('s6','六、渠道健康度'),
        ('s7','七、夜视王2.0 专题'), ('s8','八、4G 专题'),
        ('s9','九、行动计划'), ('s10','十、分析能力声明'),
    ]:
        blocks.append(f'<li><a href="#{sid}">{label}</a></li>')
    blocks.append('</ul></div>')

    # ──────── 章 1 全省总览 ────────
    blocks.append('<h1 id="s1">一、全省总览</h1>')

    blocks.append('<h2>1.1 SO 总量(双口径 同环比)</h2>')
    rows = [['指标', '当期', '同期', '环期', '同比', '环比']]
    for r in data['章1_1_SO总量']:
        ratio = r.get('_是比率', False)
        def fv(v):
            if v is None or pd.isna(v): return '—'
            if ratio: return fmt_pct_plain(v)
            if '台数' in r['指标'] or '服务商' in r['指标']: return fmt_money(v, 0)
            return fmt_money(v, 2)
        rows.append([
            r['指标'], fv(r['当期']), fv(r['同期']), fv(r['环期']),
            (fmt_pct(r['同比']), pct_class(r['同比'])) if r['同比'] is not None else '—',
            (fmt_pct(r['环比']), pct_class(r['环比'])) if r['环比'] is not None else '—',
        ])
    blocks.append(render_simple_table(rows))

    # 1.2 全量子系列(交互)
    blocks.append('<h2>1.2 全量子系列总览(可筛选三级 / 按字段排序)</h2>')
    blocks.append('<p class="note">点击表头排序;在三级下拉里筛选大类;同比绿/红高亮。Top 10 子系列用 ★ 标识。</p>')

    # 三级 unique 列表
    tris = sorted(set(r['三级'] for r in data['全量子系列'] if r['三级']))
    top10 = set(data.get('Top10子系列', []))

    blocks.append('<div data-tablectrl>')
    blocks.append('<div class="toolbar">')
    blocks.append('<label>三级:</label><select data-col="0"><option value="">全部</option>')
    for t in tris:
        blocks.append(f'<option value="{escape(t)}">{escape(t)}</option>')
    blocks.append('</select>')
    blocks.append('<input type="text" placeholder="搜索子系列..." data-search="1">')
    blocks.append('<span class="stat"></span></div>')

    head = ['国内产品线三级', '子系列', '货值(万)', '同期(万)', '同比', '当期均价(元)', '同期均价(元)', '均价同比', '台数']
    tbl = ['<table class="data"><thead><tr>']
    for c in head: tbl.append(f'<th>{escape(c)}</th>')
    tbl.append('</tr></thead><tbody>')
    for r in data['全量子系列']:
        star = ' ★' if r['子系列'] in top10 else ''
        tr = ['<tr>']
        tr.append(td_cell(r['三级'] or '—'))
        tr.append(td_cell((r['子系列'] or '—') + star))
        tr.append(td_num(fmt_money(r['货值_万']), r['货值_万']))
        tr.append(td_num(fmt_money(r['同期_万']), r['同期_万']))
        tr.append(td_num(fmt_pct(r['同比']), r['同比'], pct_class(r['同比'])))
        tr.append(td_num(fmt_int(r['当期均价']), r['当期均价']))
        tr.append(td_num(fmt_int(r['同期均价']), r['同期均价']))
        tr.append(td_num(fmt_pct(r['均价同比']), r['均价同比'], pct_class(r['均价同比'])))
        tr.append(td_num(fmt_int(r['台数']), r['台数']))
        tr.append('</tr>')
        tbl.append(''.join(tr))
    tbl.append('</tbody></table></div>')
    blocks.append(''.join(tbl))

    # ──────── 章 2 全量产品 × 区县 ────────
    blocks.append('<h1 id="s2">二、全量产品 × 区县</h1>')
    blocks.append('<p class="note">货值口径:全量感知(product_flow_v.最新分销价)。已过滤 *** 脏数据,只保留 ≥ 0.1 万的组合。绿底 > +30% / 红底 < -20%。</p>')
    blocks.append('<div data-tablectrl>')
    blocks.append('<div class="toolbar">')
    blocks.append('<input type="text" placeholder="搜索子系列/区县..." data-search="0,1">')
    blocks.append('<span class="stat"></span></div>')
    head = ['子系列', '区县', '货值(万)', '同期(万)', '同比', '台数']
    tbl = ['<table class="data"><thead><tr>']
    for c in head: tbl.append(f'<th>{escape(c)}</th>')
    tbl.append('</tr></thead><tbody>')
    for r in data['全量_产品×区县']:
        tr = ['<tr>']
        tr.append(td_cell(r['子系列']))
        tr.append(td_cell(r['维度']))
        tr.append(td_num(fmt_money(r['货值_万']), r['货值_万']))
        tr.append(td_num(fmt_money(r['同期_万']), r['同期_万']))
        tr.append(td_num(fmt_pct(r['同比']), r['同比'], pct_class(r['同比'])))
        tr.append(td_num(fmt_int(r['台数']), r['台数']))
        tr.append('</tr>')
        tbl.append(''.join(tr))
    tbl.append('</tbody></table></div>')
    blocks.append(''.join(tbl))

    # ──────── 章 3 产品 × 代理商 ────────
    blocks.append('<h1 id="s3">三、全量产品 × 代理商</h1>')
    blocks.append('<p class="note">货值口径:全量感知。已过滤脏数据。</p>')
    blocks.append('<div data-tablectrl>')
    blocks.append('<div class="toolbar">')
    blocks.append('<input type="text" placeholder="搜索子系列/代理商..." data-search="0,1">')
    blocks.append('<span class="stat"></span></div>')
    head = ['子系列', '代理商', '货值(万)', '同期(万)', '同比', '台数']
    tbl = ['<table class="data"><thead><tr>']
    for c in head: tbl.append(f'<th>{escape(c)}</th>')
    tbl.append('</tr></thead><tbody>')
    for r in data['全量_产品×代理商']:
        tr = ['<tr>']
        tr.append(td_cell(r['子系列']))
        tr.append(td_cell(r['维度']))
        tr.append(td_num(fmt_money(r['货值_万']), r['货值_万']))
        tr.append(td_num(fmt_money(r['同期_万']), r['同期_万']))
        tr.append(td_num(fmt_pct(r['同比']), r['同比'], pct_class(r['同比'])))
        tr.append(td_num(fmt_int(r['台数']), r['台数']))
        tr.append('</tr>')
        tbl.append(''.join(tr))
    tbl.append('</tbody></table></div>')
    blocks.append(''.join(tbl))

    # ──────── 章 4 服务商汇总 ────────
    blocks.append('<h1 id="s4">四、全量服务商汇总(去马甲)</h1>')
    blocks.append('<p class="note">货值口径:红包扫码(install_redpack_v)。已排除马甲(vest_account)。🚫 = closed_provider(明确无意向)。</p>')

    sp_total = data.get('全量_服务商汇总', [])
    sp_cities = sorted({r['地市'] for r in sp_total if r.get('地市')})
    blocks.append('<div data-tablectrl>')
    blocks.append('<div class="toolbar">')
    blocks.append('<label>地市:</label><select data-col="3"><option value="">全部</option>')
    for c in sp_cities:
        blocks.append(f'<option value="{escape(c)}">{escape(c)}</option>')
    blocks.append('</select>')
    blocks.append('<label>标识:</label><select data-col="1"><option value="">全部</option><option value="—">正常</option><option value="🚫">🚫 已关闭</option></select>')
    blocks.append('<input type="text" placeholder="搜索服务商/区县/代理商..." data-search="2,4,5">')
    blocks.append('<span class="stat"></span></div>')
    head = ['#', '标识', '服务商', '地市', '区县', '代理商', '当期货值(万)', '同期(万)', '同比', '台数', '涉及子系列', '主要子系列']
    tbl = ['<table class="data"><thead><tr>']
    for c in head: tbl.append(f'<th>{escape(c)}</th>')
    tbl.append('</tr></thead><tbody>')
    for i, r in enumerate(sp_total, 1):
        tr = ['<tr>']
        tr.append(td_num(i, i))
        tr.append(td_cell(r['标识'] or '—'))
        tr.append(td_cell((r['服务商'] or '')[:30]))
        tr.append(td_cell(r['地市'] or '—'))
        tr.append(td_cell(r['区县'] or '—'))
        tr.append(td_cell((r['代理商'] or '—')[:20]))
        tr.append(td_num(fmt_money(r['货值_万']), r['货值_万']))
        tr.append(td_num(fmt_money(r.get('同期_万', 0)), r.get('同期_万', 0)))
        tr.append(td_num(fmt_pct(r.get('同比')), r.get('同比'), pct_class(r.get('同比'))))
        tr.append(td_num(fmt_int(r['台数']), r['台数']))
        tr.append(td_num(fmt_int(r['涉及子系列数']), r['涉及子系列数']))
        tr.append(td_cell((r.get('主要子系列') or '—')[:30]))
        tr.append('</tr>')
        tbl.append(''.join(tr))
    tbl.append('</tbody></table></div>')
    blocks.append(''.join(tbl))

    # ──────── 章 5 子系列 × 地市透视 ────────
    blocks.append('<h1 id="s5">五、子系列 × 地市 透视矩阵</h1>')
    blocks.append('<p class="note">货值口径:全量感知。矩阵默认展示 Top 10 子系列;勾选下方复选框可显示更多子系列。绿底 > +30% / 红底 < -20%。</p>')

    mat = data['全量_子系列×地市']
    cities = mat['地市列表']

    # 子系列多选
    all_subs_list = [r['子系列'] for r in data['全量子系列']]
    blocks.append('<div data-tablectrl id="matrix-ctrl">')
    blocks.append('<div class="toolbar">')
    blocks.append('<label>子系列:</label><div class="checkbox-group" data-col="0" style="max-height:80px;overflow-y:auto">')
    top10 = set(data.get('Top10子系列', []))
    for s in all_subs_list:
        checked = 'checked' if s in top10 else ''
        blocks.append(f'<label><input type="checkbox" value="{escape(s)}" {checked}>{escape(s)}</label>')
    blocks.append('</div></div>')

    blocks.append('<h3>5.1 货值矩阵(万元)</h3>')
    head = ['子系列'] + cities
    tbl = ['<table class="data"><thead><tr>']
    for c in head: tbl.append(f'<th>{escape(c)}</th>')
    tbl.append('</tr></thead><tbody>')
    for r in mat['货值矩阵']:
        tr = ['<tr>']
        tr.append(td_cell(r['子系列']))
        for ct in cities:
            v = r.get(ct, 0) or 0
            tr.append(td_num(fmt_money(v) if v > 0 else '', v if v > 0 else None))
        tr.append('</tr>')
        tbl.append(''.join(tr))
    tbl.append('</tbody></table>')
    blocks.append(''.join(tbl))

    blocks.append('<h3>5.2 同比矩阵</h3>')
    tbl = ['<table class="data"><thead><tr>']
    for c in head: tbl.append(f'<th>{escape(c)}</th>')
    tbl.append('</tr></thead><tbody>')
    for r in mat['同比矩阵']:
        tr = ['<tr>']
        tr.append(td_cell(r['子系列']))
        for ct in cities:
            v = r.get(ct)
            if v is None or pd.isna(v):
                tr.append(td_cell('—'))
            else:
                tr.append(td_num(fmt_pct(v), v, pct_class(v)))
        tr.append('</tr>')
        tbl.append(''.join(tr))
    tbl.append('</tbody></table></div>')  # close matrix-ctrl
    blocks.append(''.join(tbl))

    # ──────── 章 6 渠道健康度 ────────
    blocks.append('<h1 id="s6">六、渠道健康度(同环比对称)</h1>')
    blocks.append(f'<p class="note">数据源:install_redpack_v。同期 {meta["yoy_start"]} ~ {meta["yoy_end"]},环期 {meta["mom_start"]} ~ {meta["mom_end"]}。</p>')

    ch4 = data['全量_渠道健康度']

    blocks.append('<h2>6.1 全省集合数</h2>')
    rows = [['口径', '评估期活跃', '对照期活跃', '持续', '新增', '流失', '净流入']]
    for label, key in [('同期对比', '同期对比'), ('环期对比', '环期对比')]:
        d = ch4[key]
        rows.append([label, fmt_int(d['评估期活跃']), fmt_int(d['对照期活跃']),
                    fmt_int(d['持续']), fmt_int(d['新增']), fmt_int(d['流失']),
                    f"{d['净流入']:+}"])
    blocks.append(render_simple_table(rows))
    blocks.append(f'<p class="warn">📝 流失中已标「🚫 明确无意向」:同期 {ch4.get("同期流失中已关闭",0)} 家 / 环期 {ch4.get("环期流失中已关闭",0)} 家 — 已知死户,不计入需归因范围。</p>')

    blocks.append('<h2>6.2 11 地市矩阵</h2>')
    rows = [['地市', '评估期活跃', '同期新增', '同期流失', '同期净流入', '环期新增', '环期流失', '环期净流入']]
    for r in ch4.get('11地市矩阵', []):
        rows.append([r['地市'], fmt_int(r['评估期活跃']),
                    fmt_int(r['同期新增']), fmt_int(r['同期流失']), f"{r['同期净流入']:+}",
                    fmt_int(r['环期新增']), fmt_int(r['环期流失']), f"{r['环期净流入']:+}"])
    blocks.append(render_simple_table(rows))

    # 6.3 同期新增/流失全量交互
    for k, label, val_col in [('同期对比', '同期', '评估期_万'), ('环期对比', '环期', '环期_万')]:
        for kind in ['新增名单', '流失名单']:
            data_list = ch4[k][kind]
            blocks.append(f'<details><summary>6.3 {label}{kind}全量(共 {len(data_list)} 家)</summary>')
            blocks.append('<div data-tablectrl>')
            blocks.append('<div class="toolbar">')
            blocks.append('<input type="text" placeholder="搜索服务商/地市/区县..." data-search="1,2,3">')
            blocks.append('<label>标识:</label><select data-col="0"><option value="">全部</option><option value="—">正常</option><option value="🎭">🎭 马甲</option><option value="🚫">🚫 已关闭</option></select>')
            blocks.append('<span class="stat"></span></div>')
            head = ['标识', '服务商', '地市', '区县', '评估期(万)', '同期(万)', '环期(万)']
            tbl = ['<table class="data"><thead><tr>']
            for c in head: tbl.append(f'<th>{escape(c)}</th>')
            tbl.append('</tr></thead><tbody>')
            for r in data_list:
                tr = ['<tr>']
                tr.append(td_cell(r['标识'] or '—'))
                tr.append(td_cell((r['服务商'] or '')[:30]))
                tr.append(td_cell(r['地市'] or '—'))
                tr.append(td_cell(r['区县'] or '—'))
                tr.append(td_num(fmt_money(r.get('评估期_万', 0)), r.get('评估期_万', 0)))
                tr.append(td_num(fmt_money(r.get('同期_万', 0)), r.get('同期_万', 0)))
                tr.append(td_num(fmt_money(r.get('环期_万', 0)), r.get('环期_万', 0)))
                tr.append('</tr>')
                tbl.append(''.join(tr))
            tbl.append('</tbody></table></div></details>')
            blocks.append(''.join(tbl))

    # ──────── 章 7/8 焦点子系列 ────────
    focus = data.get('焦点子系列', {})

    def render_focus(sub_name, sid, label):
        b = [f'<h1 id="{sid}">{label}、重点子系列:{sub_name}</h1>']
        if sub_name not in focus:
            return '\n'.join(b + ['<p>(无数据)</p>'])
        f = focus[sub_name]
        ov = f['概况']
        b.append('<div class="focus-banner">⭐ 战略产品 — 单独跟进</div>')

        b.append('<h2>概况</h2>')
        b.append('<div class="kpi-grid">')
        b.append(f'<div class="kpi"><div class="label">评估期货值(万)</div><div class="val">{fmt_money(ov["货值_当期_万"])}</div></div>')
        b.append(f'<div class="kpi"><div class="label">同比</div><div class="val {pct_class(ov["货值同比"])}">{fmt_pct(ov["货值同比"])}</div></div>')
        b.append(f'<div class="kpi"><div class="label">环比</div><div class="val {pct_class(ov["货值环比"])}">{fmt_pct(ov["货值环比"])}</div></div>')
        b.append(f'<div class="kpi"><div class="label">活跃服务商(红包)</div><div class="val">{fmt_int(ov["活跃服务商_红包"])}</div></div>')
        b.append('</div>')

        b.append('<div class="kpi-grid">')
        b.append(f'<div class="kpi"><div class="label">当期均价(元)</div><div class="val">{fmt_int(ov.get("均价_当期"))}</div></div>')
        b.append(f'<div class="kpi"><div class="label">同期均价</div><div class="val">{fmt_int(ov.get("均价_同期"))}</div></div>')
        b.append(f'<div class="kpi"><div class="label">均价同比</div><div class="val {pct_class(ov.get("均价同比"))}">{fmt_pct(ov.get("均价同比"))}</div></div>')
        b.append(f'<div class="kpi"><div class="label">当期台数</div><div class="val">{fmt_int(ov.get("台数_当期"))}</div></div>')
        b.append('</div>')

        # 地市分布(简单表)
        b.append('<h2>11 地市分布</h2>')
        rows = [['地市', '货值(万)', '台数', '同期(万)', '同比']]
        for r in f['地市分布']:
            rows.append([r['地市'], fmt_money(r['货值_万']), fmt_int(r['台数']),
                        fmt_money(r['同期_万']),
                        (fmt_pct(r['同比']), pct_class(r['同比'])) if r['同比'] is not None else '—'])
        b.append(render_simple_table(rows))

        # 区县全量(交互)
        b.append(f'<details><summary>区县全量(共 {len(f["区县全量"])} 个)</summary>')
        b.append('<div data-tablectrl>')
        b.append('<div class="toolbar">')
        cities_f = sorted({r['地市'] for r in f['区县全量'] if r.get('地市')})
        b.append('<label>地市:</label><select data-col="1"><option value="">全部</option>')
        for c in cities_f:
            b.append(f'<option value="{escape(c)}">{escape(c)}</option>')
        b.append('</select>')
        b.append('<input type="text" placeholder="搜索区县..." data-search="0">')
        b.append('<span class="stat"></span></div>')
        head = ['区县', '地市', '货值(万)', '同期(万)', '同比', '台数']
        tbl = ['<table class="data"><thead><tr>']
        for c in head: tbl.append(f'<th>{escape(c)}</th>')
        tbl.append('</tr></thead><tbody>')
        for r in f['区县全量']:
            tr = ['<tr>']
            tr.append(td_cell(r['维度']))
            tr.append(td_cell(r.get('地市', '—') or '—'))
            tr.append(td_num(fmt_money(r['货值_万']), r['货值_万']))
            tr.append(td_num(fmt_money(r['同期_万']), r['同期_万']))
            tr.append(td_num(fmt_pct(r['同比']), r['同比'], pct_class(r['同比'])))
            tr.append(td_num(fmt_int(r['台数']), r['台数']))
            tr.append('</tr>')
            tbl.append(''.join(tr))
        tbl.append('</tbody></table></div></details>')
        b.append(''.join(tbl))

        # 代理商全量
        b.append(f'<details><summary>代理商全量(共 {len(f["代理商全量"])} 个)</summary>')
        b.append('<div data-tablectrl>')
        b.append('<div class="toolbar"><input type="text" placeholder="搜索代理商..." data-search="0"><span class="stat"></span></div>')
        head = ['代理商', '地市', '货值(万)', '同期(万)', '同比', '台数']
        tbl = ['<table class="data"><thead><tr>']
        for c in head: tbl.append(f'<th>{escape(c)}</th>')
        tbl.append('</tr></thead><tbody>')
        for r in f['代理商全量']:
            tr = ['<tr>']
            tr.append(td_cell(r['维度'][:30]))
            tr.append(td_cell(r.get('地市', '—') or '—'))
            tr.append(td_num(fmt_money(r['货值_万']), r['货值_万']))
            tr.append(td_num(fmt_money(r['同期_万']), r['同期_万']))
            tr.append(td_num(fmt_pct(r['同比']), r['同比'], pct_class(r['同比'])))
            tr.append(td_num(fmt_int(r['台数']), r['台数']))
            tr.append('</tr>')
            tbl.append(''.join(tr))
        tbl.append('</tbody></table></div></details>')
        b.append(''.join(tbl))

        # 服务商全量(去马甲)
        b.append(f'<details open><summary>服务商全量(去马甲,共 {len(f["服务商全量"])} 家)</summary>')
        b.append('<div data-tablectrl>')
        b.append('<div class="toolbar">')
        cities_sp = sorted({r['地市'] for r in f['服务商全量'] if r.get('地市')})
        b.append('<label>地市:</label><select data-col="2"><option value="">全部</option>')
        for c in cities_sp:
            b.append(f'<option value="{escape(c)}">{escape(c)}</option>')
        b.append('</select>')
        b.append('<label>标识:</label><select data-col="0"><option value="">全部</option><option value="—">正常</option><option value="🚫">🚫 已关闭</option></select>')
        b.append('<input type="text" placeholder="搜索服务商/区县/代理商..." data-search="1,3,4">')
        b.append('<span class="stat"></span></div>')
        head = ['标识', '服务商', '地市', '区县', '代理商', '货值(万)', '台数']
        tbl = ['<table class="data"><thead><tr>']
        for c in head: tbl.append(f'<th>{escape(c)}</th>')
        tbl.append('</tr></thead><tbody>')
        for r in f['服务商全量']:
            tr = ['<tr>']
            tr.append(td_cell(r['标识'] or '—'))
            tr.append(td_cell((r['服务商'] or '')[:25]))
            tr.append(td_cell(r['地市'] or '—'))
            tr.append(td_cell(r['区县'] or '—'))
            tr.append(td_cell((r.get('代理商') or '—')[:20]))
            tr.append(td_num(fmt_money(r['货值_万']), r['货值_万']))
            tr.append(td_num(fmt_int(r['台数']), r['台数']))
            tr.append('</tr>')
            tbl.append(''.join(tr))
        tbl.append('</tbody></table></div></details>')
        b.append(''.join(tbl))

        return '\n'.join(b)

    blocks.append(render_focus('夜视王2.0', 's7', '七'))
    blocks.append(render_focus('4G', 's8', '八'))

    # ──────── 章 9 行动计划 ────────
    blocks.append('<h1 id="s9">九、下阶段行动计划</h1>')
    for sub_name, num in [('夜视王2.0', '9.1'), ('4G', '9.2')]:
        if sub_name not in focus: continue
        f = focus[sub_name]
        ov = f['概况']
        blocks.append(f'<h2>{num} 全省级专项:{sub_name}</h2>')
        situation = [
            f"同比 {fmt_pct(ov['货值同比'])}" if ov.get('货值同比') is not None else "同比 —",
            f"环比 {fmt_pct(ov['货值环比'])}" if ov.get('货值环比') is not None else "环比 —",
            f"评估期 {fmt_money(ov['货值_当期_万'])} 万 / {ov['活跃服务商_红包']} 家服务商",
        ]
        blocks.append('<div class="action-card">')
        blocks.append(f'<div class="title">{escape(sub_name)} · 战略产品</div>')
        blocks.append(f'<p style="margin:6px 0;color:#475569">情况:{escape(" / ".join(situation))}</p>')

        # 找该子系列下降的 Top 5 区县
        decl_qx = [r for r in f['区县全量'] if r.get('同比') is not None and r['同比'] <= TH_DOWN]
        decl_qx.sort(key=lambda x: x['同比'])
        grow_qx = [r for r in f['区县全量'] if r.get('同比') is not None and r['同比'] >= TH_UP and r['同期_万'] >= 0.5]
        grow_qx.sort(key=lambda x: -x['同比'])

        blocks.append('<ul>')
        if sub_name == '夜视王2.0':
            yoy_v = ov.get('货值同比', 0) or 0
            mom_v = ov.get('货值环比', 0) or 0
            if yoy_v >= 0.3:
                blocks.append('<li>✅ <b>同比高速增长</b>,主推 — 维持当前营销节奏,加大 Top 服务商激励</li>')
            if mom_v < 0:
                blocks.append(f'<li>⚠️ <b>环比转负</b>({fmt_pct(mom_v)}) — 排查环期后是否有竞品冲击或内部调整</li>')
            if decl_qx:
                names = ', '.join(f"{r['维度']}({fmt_pct(r['同比'])})" for r in decl_qx[:5])
                blocks.append(f'<li>🎯 <b>下降区县</b>:{escape(names)} — 区域经理一对一会该区县责任服务商</li>')
            if grow_qx:
                names = ', '.join(r['维度'] for r in grow_qx[:5])
                blocks.append(f'<li>📈 <b>高增区县(复制经验)</b>:{escape(names)}</li>')
        elif sub_name == '4G':
            blocks.append('<li>🔍 <b>4G 物联新品</b>,重点看新晋区县和服务商 — 渗透扩张期</li>')
            if grow_qx:
                names = ', '.join(r['维度'] for r in grow_qx[:5])
                blocks.append(f'<li>📈 <b>高增区县</b>:{escape(names)} — 派员深度跟进 + 给运营激励</li>')
            if decl_qx:
                names = ', '.join(f"{r['维度']}({fmt_pct(r['同比'])})" for r in decl_qx[:5])
                blocks.append(f'<li>⚠️ <b>下降区县</b>:{escape(names)} — 排查竞品替代</li>')
            top_sp = [r for r in f['服务商全量'][:10] if r['标识'] != '🚫']
            if top_sp:
                names = ', '.join((r['服务商'] or '')[:15] for r in top_sp[:5])
                blocks.append(f'<li>🌟 <b>头部服务商</b>:{escape(names)} — 单独维系 + 升级专项政策</li>')
        blocks.append('</ul></div>')

    # 9.3 各地市行动(全量服务商 by 地市)
    blocks.append('<h2>9.3 各地市行动:全量服务商(去马甲)</h2>')
    blocks.append('<p class="note">⬇️ 选地市看 Top 30 服务商,带🚫的不派任务,带「主要子系列」给推荐动作。</p>')

    sp_total = data.get('全量_服务商汇总', [])
    cities_action = sorted({r['地市'] for r in sp_total if r.get('地市')})

    for ct in cities_action:
        sub_sp = [r for r in sp_total if r.get('地市') == ct]
        sub_sp = sorted(sub_sp, key=lambda x: -(x.get('货值_万') or 0))[:30]
        n_closed = sum(1 for p in sub_sp if p.get('标识') == '🚫')

        blocks.append(f'<details><summary>{escape(ct)} — Top {len(sub_sp)}(可派 {len(sub_sp)-n_closed} 家 / 🚫 {n_closed} 家)</summary>')
        rows = [['#', '标识', '服务商', '区县', '货值(万)', '主要子系列', '推荐动作']]
        for i, p in enumerate(sub_sp, 1):
            sub_list = p.get('主要子系列') or ''
            is_closed = p.get('标识') == '🚫'
            if is_closed:
                action = '⊘ 不派任务(已确认无采购意向)'
            else:
                actions = []
                if '夜视王2.0' in sub_list: actions.append('已激活夜视王2.0:追加单 + 升级套餐')
                elif '夜视王' in sub_list: actions.append('已激活夜视王:推 2.0 升级')
                else: actions.append('推夜视王2.0 试用')
                if '4G' in sub_list: actions.append('已激活4G:推 4G 低功耗 / 电商-4G')
                else: actions.append('推 4G 试用')
                action = ' / '.join(actions)
            rows.append([
                i, p.get('标识') or '—',
                (p.get('服务商') or '')[:25],
                p.get('区县') or '—',
                fmt_money(p.get('货值_万', 0)),
                sub_list, action,
            ])
        blocks.append(render_simple_table(rows))
        blocks.append('</details>')

    # ──────── 章 10 分析能力声明 ────────
    blocks.append('<h1 id="s10">十、本报告的分析能力声明</h1>')
    rows = [['影响因素', '本报告能反应', '用什么数据', '边界']]
    rows.append(['市场需求', '❌', '—', '报表层无外部市场数据'])
    rows.append(['自身产品涨价/降价', '⚠️ 部分', '单台均价同比(1.2)', '仅自身定价,不能对标竞品'])
    rows.append(['竞品定价', '❌', '—', '报表层无竞品价格'])
    rows.append(['产品质量', '❌', '—', '暂无售后数据'])
    rows.append(['客户跑动拜访', '❌', '—', '本版未分析跑动'])
    rows.append(['激励动作(红包)', '❌', '—', '本版未分析红包'])
    blocks.append(render_simple_table(rows))

    blocks.append('<h3>使用本报告建议</h3>')
    blocks.append('<ol>')
    blocks.append('<li>行动计划是「优先调查方向」,非「确认根因」</li>')
    blocks.append('<li>重大决策前结合一线访谈 / 客户调研做交叉验证</li>')
    blocks.append('<li>关注未覆盖因素(市场 / 竞品 / 质量 / 跑动 / 红包)</li>')
    blocks.append('</ol>')

    blocks.append('</div>')  # close .page
    blocks.append(JS)
    return '\n'.join(blocks)


