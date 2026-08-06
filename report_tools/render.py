#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全省月报渲染 tool（本地跑）——把【数据 + LLM 写的分析】渲染成 HTML + docx。

输入 JSON（--in）结构：
{
  "本月": "2026-05",
  "趋势月份": ["2026-03","2026-04","2026-05"],
  "trend":  <fetch trend 的原样输出>,
  "drills": [ <fetch drill 的输出>, ... ],     # LLM 决定下钻了哪几个地市
  "focus":  <fetch focus 的原样输出>,
  "分析": {                                     # ← 这部分由 LLM(我) 填写
     "总览": "一句话/一段总体判断",
     "地市": {"金华市": "金华的问题诊断段落", ...},
     "专项": "三专项的诊断段落",
     "下阶段计划": ["行动1", "行动2", ...]
  }
}

数字全部来自 trend/drills/focus（tool 算的，可复现）；
"分析" 全部来自 LLM（叙述/判断/建议）。两者在渲染层合并。

用法：
    python3 render.py --in combined.json --outdir 分析报告 [--basename 全省月报-2026-05]
输出：{outdir}/{basename}.html + {basename}.docx
"""
import argparse
import html
import json
from pathlib import Path


# ────────────────────────── 公共格式化 ──────────────────────────
def pct(x):
    return f'{x * 100:+.1f}%' if x is not None else 'N/A'


def cls(x):
    if x is None:
        return ''
    return 'g' if x > 0 else ('r' if x < 0 else '')


def h(s):
    return html.escape(str(s)) if s is not None else ''


# 等级显示映射:底层 tier_of 用「已激活/v0」,报告统一显示为 V1/V0(用户口径)
_TIER_DISPLAY = {'已激活': 'V1', 'v0': 'V0'}


def tier_label(lv):
    return _TIER_DISPLAY.get(lv, lv)


import re as _re


def _blocks(value):
    """把分析字段规整成块列表。每块是 ('p', 文本) 段落 或 ('ol', [项,...]) 有序列表。

    输入可为:
      - str：按换行/句末切成多段（一事一段）
      - list[str]：每个元素一段；以 '1.'/'1、'/'1)' 开头的连续元素归为有序列表
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    items = [str(x).strip() for x in items if str(x).strip()]
    blocks = []
    ol_buf = []

    def flush():
        if ol_buf:
            blocks.append(('ol', ol_buf[:]))
            ol_buf.clear()

    for it in items:
        m = _re.match(r'^\s*\d+\s*[.、)）]\s*(.+)$', it, _re.S)
        if m:
            ol_buf.append(m.group(1).strip())
        else:
            flush()
            blocks.append(('p', it))
    flush()
    return blocks


def verdict_pill(v):
    if v.startswith('🔴'):
        return '<span class="pill p-red">差</span>'
    if v.startswith('🟢'):
        return '<span class="pill p-green">好</span>'
    if v.startswith('🟡'):
        return '<span class="pill p-amber">转弱</span>'
    return '<span class="pill p-grey">平稳</span>'


CSS = """
:root{--pri:#1f5fbf;--pri-d:#14478f;--bg:#f4f6fa;--card:#fff;--line:#e1e6ef;--txt:#1c2430;
--mute:#6b7686;--green:#1f9d55;--red:#d64545;--amber:#d98a00;
--shadow:0 1px 3px rgba(20,40,80,.08),0 4px 16px rgba(20,40,80,.06);}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font-size:13.5px;line-height:1.6;
font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Segoe UI,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:0 20px 70px}
header{background:linear-gradient(120deg,var(--pri),var(--pri-d));color:#fff;padding:26px 20px;margin-bottom:22px}
header .inner{max-width:980px;margin:0 auto}
h1{margin:0;font-size:23px;letter-spacing:.5px}
.sub{opacity:.88;font-size:12.5px;margin-top:6px}
h2{font-size:17px;margin:30px 0 12px;padding-left:11px;border-left:4px solid var(--pri)}
h3{font-size:14.5px;margin:16px 0 7px}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;box-shadow:var(--shadow);padding:18px 20px;margin-bottom:16px}
p{margin:7px 0}.lead{font-size:14.5px}
b.hl{color:var(--pri-d)}.r{color:var(--red);font-weight:700}.g{color:var(--green);font-weight:700}.a{color:var(--amber);font-weight:700}
table{border-collapse:collapse;width:100%;font-size:12.8px;margin:6px 0}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:right}
th{background:#f3f6fb;color:#34425a;font-weight:600}
td.l,th.l{text-align:left}
.mut{color:var(--mute);font-size:12px}
.pill{display:inline-block;font-size:11px;font-weight:700;color:#fff;border-radius:11px;padding:2px 9px}
.p-red{background:var(--red)}.p-green{background:var(--green)}.p-amber{background:var(--amber)}.p-grey{background:#9aa6b8}
.callout{background:#eef4fc;border:1px solid #c5d8f2;border-radius:9px;padding:12px 15px;font-size:12.8px;margin:10px 0}
.analysis{background:#f7faf7;border-left:3px solid var(--green);padding:10px 14px;margin:10px 0;font-size:13px}
.num{font-variant-numeric:tabular-nums}
ul{margin:7px 0;padding-left:20px}li{margin:4px 0}
footer{color:var(--mute);font-size:11.5px;text-align:center;margin-top:30px;border-top:1px solid var(--line);padding-top:14px}
@media print{body{background:#fff}.card{box-shadow:none}header{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def analysis_html(value, css_class='analysis', prefix='🔍 '):
    """把分析字段渲染成 HTML：多段 <p>，有序列表 <ol>。"""
    blocks = _blocks(value)
    if not blocks:
        return ''
    out = [f'<div class="{css_class}">']
    first = True
    for kind, payload in blocks:
        if kind == 'ol':
            out.append('<ol>' + ''.join(f'<li>{h(x)}</li>' for x in payload) + '</ol>')
        else:
            pfx = prefix if (first and prefix) else ''
            out.append(f'<p>{pfx}{h(payload)}</p>')
        first = False
    out.append('</div>')
    return ''.join(out)


# ────────────────────────── HTML 渲染 ──────────────────────────
def render_html(J):
    MS = J['趋势月份']
    CUR = J['本月']
    m1 = J['trend']
    drills = J.get('drills', [])
    m3 = J['focus']
    A = J.get('分析', {})
    prov = m1['全省']
    P = []
    P.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">')
    P.append('<meta name="viewport" content="width=device-width,initial-scale=1.0">')
    P.append(f'<title>浙江全省月度经营报告 · {CUR}</title><style>{CSS}</style></head><body>')
    P.append('<header><div class="inner"><h1>浙江全省月度经营报告（诊断版）</h1>')
    P.append(f'<div class="sub">本月 {CUR} · 趋势窗口 {MS[0]}~{MS[-1]} · 口径：全量感知 SO（最新分销价/万），全省=浙江11地市之和</div>')
    P.append('</div></header><div class="wrap">')

    # 总览分析（LLM 写）
    if A.get('总览'):
        ov = analysis_html(A['总览'], css_class='card', prefix='')
        # 首段前插「核心结论：」标签
        ov = ov.replace('<p>', '<p><b class="hl">核心结论：</b>', 1)
        P.append(ov)

    # ① 趋势
    P.append('<h2>① 全省 &amp; 11 地市 SO 趋势</h2><div class="card">')
    P.append(f"<p>全省（11 市之和）：{MS[0]} <b class='num'>{prov['分月'][MS[0]]}</b> → "
             f"{MS[1]} <b class='num'>{prov['分月'][MS[1]]}</b> → {MS[2]} <b class='num'>{prov['分月'][MS[2]]}</b> 万　"
             f"本月同比 <span class='{cls(prov['本月同比'])}'>{pct(prov['本月同比'])}</span>　"
             f"环比 <span class='{cls(prov['本月环比'])}'>{pct(prov['本月环比'])}</span></p>")
    P.append('<table><thead><tr><th class="l">地市</th>' +
             ''.join(f'<th>{m}</th>' for m in MS) +
             '<th>去年同月</th><th>同比</th><th>环比</th><th>判定</th></tr></thead><tbody>')
    for r in m1['地市']:
        cells = ''.join(f"<td class='num'>{r['分月SO'].get(m, '·')}</td>" for m in MS)
        P.append(f"<tr><td class='l'>{h(r['地市'])}</td>{cells}"
                 f"<td class='num'>{r['去年同月SO']}</td>"
                 f"<td class='num {cls(r['本月同比'])}'>{pct(r['本月同比'])}</td>"
                 f"<td class='num {cls(r['本月环比'])}'>{pct(r['本月环比'])}</td>"
                 f"<td>{verdict_pill(r['判定'])}</td></tr>")
    P.append('</tbody></table>')
    rb = '；'.join(f"{x['地市']}（{pct(x['本月同比'])}）" for x in m1['红榜'])
    hb = '；'.join(f"{x['地市']}（同比{pct(x['本月同比'])}/环比{pct(x['本月环比'])}）" for x in m1['黑榜'])
    P.append(f"<p>🏆 <b>红榜</b>：{rb}</p><p>⚠️ <b>黑榜</b>：{hb}</p></div>")

    # ② 下钻
    if drills:
        P.append('<h2>② 差地市下钻（逐个）</h2>')
        for d in drills:
            city = d['地市']
            cityrow = next((r for r in m1['地市'] if r['地市'] == city), {})
            P.append(f'<div class="card"><h3>🔴 {h(city)}　<span class="mut">本月 {cityrow.get("本月SO")}万，'
                     f'同比 {pct(cityrow.get("本月同比"))}，环比 {pct(cityrow.get("本月环比"))}</span></h3>')
            # 该地市分析（LLM 写）
            if A.get('地市', {}).get(city):
                P.append(analysis_html(A['地市'][city]))
            # a 代理商
            da = d['代理商']
            P.append(f"<h3>a. 问题代理商 <span class='mut'>（出货口径，门槛≥{da.get('_体量门槛_万')}万，"
                     f"{da.get('_代理商总数')}家中{da.get('_达门槛数')}家达标）</span></h3>")
            P.append('<table><thead><tr><th class="l">代理商</th>' +
                     ''.join(f'<th>{m}出货万</th>' for m in MS) +
                     '<th>出货环比</th><th>本月交易服务商</th><th>服务商数环比</th></tr></thead><tbody>')
            for x in da['问题代理商']:
                ship = ''.join(f"<td class='num'>{x['出货万_分月'].get(m, 0)}</td>" for m in MS)
                sn = x['服务商数环比']
                P.append(f"<tr><td class='l'>{h(x['代理商'])}</td>{ship}"
                         f"<td class='num {cls(x['出货环比'])}'>{pct(x['出货环比'])}</td>"
                         f"<td class='num'>{x['本月交易服务商数']}</td>"
                         f"<td class='num {cls(sn)}'>{sn:+d}</td></tr>")
            P.append('</tbody></table>')
            # b 等级结构
            P.append('<h3>b. 服务商等级结构 <span class="mut">（货值口径·红包SO万）</span></h3>')
            struct = d['等级结构']['分月等级结构']
            P.append('<table><thead><tr><th class="l">等级</th>' +
                     ''.join(f'<th>{m}</th>' for m in MS) + '<th>环比</th><th>本月服务商数</th></tr></thead><tbody>')
            for row in d['等级结构']['等级环比']:
                lv = row['等级']
                cells = ''.join(f"<td class='num'>{struct[m][lv]['SO万']}</td>" for m in MS)
                P.append(f"<tr><td class='l'>{tier_label(lv)}</td>{cells}"
                         f"<td class='num {cls(row['环比'])}'>{pct(row['环比'])}</td>"
                         f"<td class='num'>{row['本月服务商数']}</td></tr>")
            P.append('</tbody></table>')
            for lv, reps in d['等级结构']['代表服务商'].items():
                if reps:
                    names = '；'.join(f"{h(r['客户名称'])}（{h(r['区县'])}，掉{r['掉幅万']}万）" for r in reps[:5])
                    P.append(f"<p class='mut'><b>{tier_label(lv)} 档下滑代表：</b>{names}</p>")
            # c 跑动推广会
            vp = d['跑动推广会']
            P.append('<h3>c. 跑动 &amp; 推广会</h3><table><thead><tr><th class="l">指标</th>' +
                     ''.join(f'<th>{m}</th>' for m in MS) + '</tr></thead><tbody>')
            P.append('<tr><td class="l">有效打卡</td>' +
                     ''.join(f"<td class='num'>{vp['跑动_分月'][m]['有效打卡']}</td>" for m in MS) + '</tr>')
            P.append('<tr><td class="l">活跃业务员</td>' +
                     ''.join(f"<td class='num'>{vp['跑动_分月'][m]['活跃业务员']}</td>" for m in MS) + '</tr>')
            P.append('<tr><td class="l">推广会参会人次</td>' +
                     ''.join(f"<td class='num'>{vp['推广会_分月'][m]['参会人次']}</td>" for m in MS) + '</tr>')
            P.append('</tbody></table>')
            vc = vp['跑动_有效打卡环比']
            if vp.get('推广会_本月有数据'):
                pn = f"推广会参会环比 <span class='{cls(vp['推广会_参会环比'])}'>{pct(vp['推广会_参会环比'])}</span>"
            else:
                pn = f"<span class='a'>推广会本月无数据（截至 {vp.get('推广会_数据末月')}）</span>"
            P.append(f"<p class='mut'>跑动有效打卡环比 <span class='{cls(vc)}'>{pct(vc)}</span>；{pn}</p></div>")

    # ③ 专项
    P.append('<h2>③ 三大产品专项趋势</h2><div class="card">')
    P.append('<table><thead><tr><th class="l">专项</th>' +
             ''.join(f'<th>{m}出货万</th>' for m in MS) + '<th>本月环比</th><th>状态</th></tr></thead><tbody>')
    for x in m3['专项']:
        cells = ''.join(f"<td class='num'>{x['分月出货万'].get(m, 0)}</td>" for m in MS)
        st = '<span class="pill p-red">下降</span>' if x['是否下降'] else '<span class="pill p-green">增长</span>'
        P.append(f"<tr><td class='l'>{x['专项']}</td>{cells}"
                 f"<td class='num {cls(x['本月环比'])}'>{pct(x['本月环比'])}</td><td>{st}</td></tr>")
    P.append('</tbody></table>')
    if A.get('专项'):
        P.append(analysis_html(A['专项']))
    for x in m3['专项']:
        if x.get('下钻'):
            dd = x['下钻']
            P.append(f"<h3>🔴 {x['专项']} 下降下钻</h3>")
            P.append('<p class="mut"><b>跌最多地市：</b>' + '；'.join(
                f"{h(r['地市'])}({r['环比_万']:+}万)" for r in dd['地市下降'][:5] if r['环比_万'] < 0) + '</p>')
            P.append('<p class="mut"><b>跌最多代理商：</b>' + '；'.join(
                f"{h(r['代理商'])}({r['环比_万']:+}万)" for r in dd['代理商下降'][:5]) + '</p>')
            P.append('<p class="mut"><b>明显下降服务商：</b>' + '；'.join(
                f"{h(r['客户名称'])}({h(r['地市'])},掉{r['掉幅万']}万)" for r in dd['服务商下降'][:6]) + '</p>')
    P.append('</div>')

    # 下阶段计划（LLM 写）—— 复用 analysis_html，1./2. 自动成有序列表
    plans = A.get('下阶段计划', [])
    if plans:
        P.append('<h2>④ 下阶段计划</h2>')
        P.append(analysis_html(plans, css_class='card', prefix=''))

    P.append('<footer>浙江全省月度经营报告（诊断版）· 数据源：生产库 product_flow_v / install_redpack_v / '
             'visit_record_v / promotion_meeting / product_focus · 数字由 report_tools 计算，分析由 AI 撰写</footer>')
    P.append('</div></body></html>')
    return '\n'.join(P)


# ────────────────────────── docx 渲染 ──────────────────────────
def render_docx(J, out_path):
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    MS = J['趋势月份']
    CUR = J['本月']
    m1 = J['trend']
    drills = J.get('drills', [])
    m3 = J['focus']
    A = J.get('分析', {})
    prov = m1['全省']

    doc = Document()
    # 中文字体（宋体）
    style = doc.styles['Normal']
    style.font.name = '宋体'
    style.font.size = Pt(10.5)
    try:
        from docx.oxml.ns import qn
        style.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
    except Exception:
        pass

    def heading(txt, level=1):
        p = doc.add_paragraph()
        run = p.add_run(txt)
        run.bold = True
        run.font.size = Pt(15 - level * 1.5)
        run.font.color.rgb = RGBColor(0x14, 0x47, 0x8f)
        try:
            run.font.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        except Exception:
            pass
        return p

    def para(txt, size=10.5, color=None, bold=False):
        p = doc.add_paragraph()
        run = p.add_run(txt)
        run.font.size = Pt(size)
        run.bold = bold
        if color:
            run.font.color.rgb = RGBColor(*color)
        try:
            run.font.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        except Exception:
            pass
        return p

    def analysis(value, color=(0x1f, 0x9d, 0x55), prefix='🔍 '):
        """渲染分析字段：多段各成一段；有序列表逐项 '1. xxx'（缩进）。"""
        blocks = _blocks(value)
        first = True
        for kind, payload in blocks:
            if kind == 'ol':
                for i, x in enumerate(payload, 1):
                    p = para(f'{i}. {x}', color=color)
                    p.paragraph_format.left_indent = Pt(12)
            else:
                para((prefix if (first and prefix) else '') + payload, color=color)
            first = False

    def table(headers, rows):
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = 'Light Grid Accent 1'
        for i, hd in enumerate(headers):
            c = t.rows[0].cells[i]
            c.text = str(hd)
            for r in c.paragraphs[0].runs:
                r.bold = True
                r.font.size = Pt(9)
        for row in rows:
            cells = t.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = '' if v is None else str(v)
                for r in cells[i].paragraphs[0].runs:
                    r.font.size = Pt(9)
        return t

    from docx.oxml.ns import qn

    # 标题
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tr = title.add_run(f'浙江全省月度经营报告（诊断版）· {CUR}')
    tr.bold = True
    tr.font.size = Pt(18)
    tr.font.color.rgb = RGBColor(0x14, 0x47, 0x8f)
    try:
        tr.font.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
    except Exception:
        pass
    para(f'趋势窗口 {MS[0]}~{MS[-1]} · 口径：全量感知 SO（万），全省=浙江11地市之和',
         size=9, color=(0x6b, 0x76, 0x86))

    if A.get('总览'):
        heading('核心结论', 2)
        analysis(A['总览'], color=None, prefix='')

    # ① 趋势
    heading('① 全省 & 11 地市 SO 趋势', 1)
    para(f"全省（11 市之和）：{MS[0]} {prov['分月'][MS[0]]} → {MS[1]} {prov['分月'][MS[1]]} → "
         f"{MS[2]} {prov['分月'][MS[2]]} 万　本月同比 {pct(prov['本月同比'])}　环比 {pct(prov['本月环比'])}",
         bold=True)
    headers = ['地市'] + MS + ['去年同月', '同比', '环比', '判定']
    rows = []
    for r in m1['地市']:
        rows.append([r['地市']] + [r['分月SO'].get(m, '·') for m in MS] +
                    [r['去年同月SO'], pct(r['本月同比']), pct(r['本月环比']),
                     r['判定'].split()[-1] if r['判定'] else ''])
    table(headers, rows)
    para('🏆 红榜：' + '；'.join(f"{x['地市']}（{pct(x['本月同比'])}）" for x in m1['红榜']))
    para('⚠️ 黑榜：' + '；'.join(
        f"{x['地市']}（同比{pct(x['本月同比'])}/环比{pct(x['本月环比'])}）" for x in m1['黑榜']))

    # ② 下钻
    if drills:
        heading('② 差地市下钻（逐个）', 1)
        for d in drills:
            city = d['地市']
            cityrow = next((r for r in m1['地市'] if r['地市'] == city), {})
            heading(f"🔴 {city}（本月 {cityrow.get('本月SO')}万，同比 {pct(cityrow.get('本月同比'))}，"
                    f"环比 {pct(cityrow.get('本月环比'))}）", 2)
            if A.get('地市', {}).get(city):
                analysis(A['地市'][city])
            da = d['代理商']
            para(f"a. 问题代理商（门槛≥{da.get('_体量门槛_万')}万，"
                 f"{da.get('_代理商总数')}家中{da.get('_达门槛数')}家达标）", bold=True)
            hdr = ['代理商'] + [f'{m}出货万' for m in MS] + ['出货环比', '本月交易服务商', '服务商数环比']
            rows = []
            for x in da['问题代理商']:
                rows.append([x['代理商']] + [x['出货万_分月'].get(m, 0) for m in MS] +
                            [pct(x['出货环比']), x['本月交易服务商数'], f"{x['服务商数环比']:+d}"])
            table(hdr, rows)
            para('b. 服务商等级结构（货值口径·红包SO万）', bold=True)
            struct = d['等级结构']['分月等级结构']
            hdr = ['等级'] + MS + ['环比', '本月服务商数']
            rows = []
            for row in d['等级结构']['等级环比']:
                lv = row['等级']
                rows.append([tier_label(lv)] + [struct[m][lv]['SO万'] for m in MS] +
                            [pct(row['环比']), row['本月服务商数']])
            table(hdr, rows)
            for lv, reps in d['等级结构']['代表服务商'].items():
                if reps:
                    para(f"  {tier_label(lv)} 档下滑代表：" + '；'.join(
                        f"{r['客户名称']}（{r['区县']}，掉{r['掉幅万']}万）" for r in reps[:5]),
                        size=9, color=(0x6b, 0x76, 0x86))
            vp = d['跑动推广会']
            para('c. 跑动 & 推广会', bold=True)
            hdr = ['指标'] + MS
            rows = [
                ['有效打卡'] + [vp['跑动_分月'][m]['有效打卡'] for m in MS],
                ['活跃业务员'] + [vp['跑动_分月'][m]['活跃业务员'] for m in MS],
                ['推广会参会人次'] + [vp['推广会_分月'][m]['参会人次'] for m in MS],
            ]
            table(hdr, rows)
            vc = vp['跑动_有效打卡环比']
            note = (f"推广会参会环比 {pct(vp['推广会_参会环比'])}" if vp.get('推广会_本月有数据')
                    else f"推广会本月无数据（截至 {vp.get('推广会_数据末月')}）")
            para(f"跑动有效打卡环比 {pct(vc)}；{note}", size=9, color=(0x6b, 0x76, 0x86))

    # ③ 专项
    heading('③ 三大产品专项趋势', 1)
    hdr = ['专项'] + [f'{m}出货万' for m in MS] + ['本月环比', '状态']
    rows = []
    for x in m3['专项']:
        rows.append([x['专项']] + [x['分月出货万'].get(m, 0) for m in MS] +
                    [pct(x['本月环比']), '下降' if x['是否下降'] else '增长'])
    table(hdr, rows)
    if A.get('专项'):
        analysis(A['专项'])
    for x in m3['专项']:
        if x.get('下钻'):
            dd = x['下钻']
            heading(f"🔴 {x['专项']} 下降下钻", 2)
            para('跌最多地市：' + '；'.join(
                f"{r['地市']}({r['环比_万']:+}万)" for r in dd['地市下降'][:5] if r['环比_万'] < 0), size=9)
            para('跌最多代理商：' + '；'.join(
                f"{r['代理商']}({r['环比_万']:+}万)" for r in dd['代理商下降'][:5]), size=9)
            para('明显下降服务商：' + '；'.join(
                f"{r['客户名称']}({r['地市']},掉{r['掉幅万']}万)" for r in dd['服务商下降'][:6]), size=9)

    # ④ 下阶段计划 —— 复用 analysis，1./2. 自动成有序列表
    plans = A.get('下阶段计划', [])
    if plans:
        heading('④ 下阶段计划', 1)
        analysis(plans, color=None, prefix='')

    doc.save(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='inp', required=True, help='合并 JSON（数据+分析）')
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--basename', default=None)
    ap.add_argument('--no-docx', action='store_true')
    args = ap.parse_args()

    J = json.load(open(args.inp, encoding='utf-8'))
    base = args.basename or f"全省月度经营报告-{J['本月']}"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    html_path = outdir / f"{base}.html"
    html_path.write_text(render_html(J), encoding='utf-8')
    print(f"✓ HTML: {html_path}")

    if not args.no_docx:
        docx_path = outdir / f"{base}.docx"
        render_docx(J, str(docx_path))
        print(f"✓ docx: {docx_path}")


if __name__ == '__main__':
    main()
