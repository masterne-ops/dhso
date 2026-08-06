#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全省周报渲染 → docx（原型，先给用户看）。

输入:gather_prov_weekly 的 JSON。完成率列同时给「时间进度」参照,避免月初低完成率被误读。
"""
import argparse
import json
from pathlib import Path


def pct(x):
    return f'{x*100:+.1f}%' if x is not None else 'N/A'


def rate(x):
    return f'{x*100:.0f}%' if x is not None else 'N/A'


def render_docx(J, out_path):
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn

    t = J['①周趋势']; c = J['②地市对比']; ytd = J['②YTD累计同比']
    pk = J.get('服务商KPI', {}); actv2 = J.get('激活V2', {}); vsplit = J.get('跑动分组', {})
    pbycity = J.get('服务商按地市', {})
    dr = J['③异动下钻']; f = J['④专项']; pm = J['推广会']

    doc = Document()
    st = doc.styles['Normal']
    st.font.name = '宋体'; st.font.size = Pt(10.5)
    try:
        st.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
    except Exception:
        pass

    def para(txt, size=10.5, color=None, bold=False, indent=0):
        p = doc.add_paragraph()
        r = p.add_run(txt)
        r.font.size = Pt(size); r.bold = bold
        if color:
            r.font.color.rgb = RGBColor(*color)
        try:
            r.font.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        except Exception:
            pass
        if indent:
            p.paragraph_format.left_indent = Pt(indent)
        return p

    def heading(txt, level=1):
        p = doc.add_paragraph()
        r = p.add_run(txt); r.bold = True
        r.font.size = Pt(15 - level * 1.5); r.font.color.rgb = RGBColor(0x14, 0x47, 0x8f)
        try:
            r.font.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        except Exception:
            pass

    def table(headers, rows):
        tb = doc.add_table(rows=1, cols=len(headers)); tb.style = 'Light Grid Accent 1'
        for i, hd in enumerate(headers):
            cell = tb.rows[0].cells[i]; cell.text = str(hd)
            for rr in cell.paragraphs[0].runs:
                rr.bold = True; rr.font.size = Pt(9)
        for row in rows:
            cells = tb.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = '' if v is None else str(v)
                for rr in cells[i].paragraphs[0].runs:
                    rr.font.size = Pt(9)
        return tb

    # 标题
    title = doc.add_paragraph(); title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tr = title.add_run(f"浙江全省经营周报 · {J['本周区间']}")
    tr.bold = True; tr.font.size = Pt(18); tr.font.color.rgb = RGBColor(0x14, 0x47, 0x8f)
    try:
        tr.font.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
    except Exception:
        pass
    para(f"范围：{J['范围']} · 口径：{J['口径说明']}", size=9, color=(0x6b, 0x76, 0x86))

    # ① 周趋势
    heading('① 全省 SO 周趋势', 1)
    para(f"本周（{J['本周区间']}）SO {t['本周SO']} 万，环比上周（{t['上周SO']} 万）{pct(t['本周环比'])}。", bold=True)
    table(['周窗口'] + t['标签'], [['SO万'] + t['分周SO']])
    para('注：本周为 7 天窗口（含跨月 6/1–6/6），与上周等长可比。', size=8.5, color=(0x6b, 0x76, 0x86))

    # ② 地市对比 + 完成率进度条
    heading('② 11 地市周对比 + 当月完成率', 1)
    para(f"当月 {c['当月']}：截至本周末时间进度 {rate(c['当月时间进度'])}，"
         f"当月节奏目标占全年 {rate(c['月节奏占比'])}。完成率应对照时间进度看（≈{rate(c['当月时间进度'])} 即达节奏）。",
         size=9, color=(0x6b, 0x76, 0x86))
    table(['地市', '本周SO万', '周环比', '当月累计万', '当月应达成万', '当月完成率'],
          [[r['市'], r['本周SO'], pct(r['本周环比']), r['当月累计'], r['当月应达成'], rate(r['当月完成率'])]
           for r in c['地市']])
    para('🏆 本周红榜：' + '；'.join(f"{x['市']}（{pct(x['本周环比'])}）" for x in c['红榜']))
    para('⚠️ 本周黑榜：' + '；'.join(f"{x['市']}（{pct(x['本周环比'])}）" for x in c['黑榜']))

    # ③ SO 同比（当月同比 + YTD累计同比）
    heading('③ SO 同比（当月当天累计 + 年初累计）', 1)
    para(f"全省当月（{ytd['当月区间']} vs 去年同期）：{ytd['全省当月今年']} 万，"
         f"较去年同期（{ytd['全省当月去年']} 万）{pct(ytd['全省当月同比'])}；"
         f"全省 YTD（{ytd['今年区间']}）：{ytd['全省今年']} 万，较去年同期 {pct(ytd['全省累计同比'])}。", bold=True)
    para('说明：当月同比＝本月 1 号至今 vs 去年同月同期（看本月势头）；YTD＝年初至今 vs 去年同期（看全年趋势）。',
         size=9, color=(0x6b, 0x76, 0x86))
    table(['地市', '当月今年万', '当月去年万', '当月同比', 'YTD今年万', 'YTD去年万', '累计同比'],
          [[r['市'], r['当月今年'], r['当月去年'], pct(r['当月同比']),
            r['YTD今年'], r['YTD去年'], pct(r['累计同比'])] for r in ytd['地市']])

    # ④ 服务商:本周签约/激活(达V2) + 任务达成
    if pk:
        heading('④ 服务商发展（本周签约/激活 + 任务达成）', 1)
        actcur = actv2.get('本周达V2', pk['本周激活'])
        para(f"本周全省新签约 {pk['本周签约']} 家；本周激活（本年累计破 V2）{actcur} 家，环比 {pct(actv2.get('环比'))}。", bold=True)
        if actv2:
            para('激活（达 V2）近 6 周趋势：')
            table(['周窗口'] + actv2['标签'], [['达V2家数'] + actv2['分周达V2']])
        # 11 地市对比表(像 SO 那样逐市)
        if pbycity.get('地市'):
            para('11 地市服务商发展对比：', bold=True)
            table(['地市', '本周签约', '签约达成率', '本周激活(达V2)', '激活环比', '激活达成率'],
                  [[r['市'], r['本周签约'], rate(r['签约达成率']), r['本周激活'],
                    pct(r['激活环比']), rate(r['激活达成率'])] for r in pbycity['地市']])
        td = pk['任务达成']
        para('全省任务达成（当前实际 / 年度目标）：')
        table(['指标', '当前实际', '年度目标', '达成率'],
              [[k, td[k]['实际'], td[k]['目标'], rate(td[k]['达成率'])]
               for k in ['签约数', '新签', 'V2', 'V3', 'V4']])
        if vsplit:
            dh, ag = vsplit.get('大华', {}), vsplit.get('代理商', {})
            para('跑动人员分类（大华 / 代理商业务员）：', bold=True)
            table(['人员', '本周打卡', '本周人数', '上周打卡', '打卡环比'],
                  [['大华业务员', dh.get('本周打卡', 0), dh.get('本周人数', 0), dh.get('上周打卡', 0), pct(dh.get('打卡环比'))],
                   ['代理商业务员', ag.get('本周打卡', 0), ag.get('本周人数', 0), ag.get('上周打卡', 0), pct(ag.get('打卡环比'))]])
        para('说明：本周激活＝服务商管理表激活时间落本周(是否激活=Y)；跑动分大华/代理商业务员；V2/V3/V4 为官方等级家数 vs 年度目标。',
             size=9, color=(0x6b, 0x76, 0x86))

    # ⑤ 异动下钻
    heading('⑤ 异动地市下钻（本周环比下滑）', 1)
    if not dr['下钻地市']:
        para('本周无明显异动下滑地市。')
    for m in dr['明细']:
        v = m['跑动']
        heading(f"🔴 {m['地市']}", 2)
        if m['问题代理商']:
            para('本周出货下滑代理商：' + '；'.join(
                f"{x['代理商']}（{x['本周']}万，{pct(x['环比'])}）" for x in m['问题代理商'][:5]), size=9)
        else:
            para('本周无明显下滑代理商（多为零散波动）。', size=9)
        para(f"周跑动：打卡 {v['上周打卡']}→{v['本周打卡']}（{pct(v['环比'])}），"
             f"本周活跃业务员 {v['本周活跃业务员']} 人。", size=9)

    # ⑤ 专项（台数）
    heading('⑥ 三大产品专项周趋势（台数）', 1)
    table(['专项'] + f['标签'] + ['本周环比'],
          [[x['专项']] + x['分周台数'] + [pct(x['本周环比'])] for x in f['专项']])
    down = [x['专项'] for x in f['专项'] if x['是否下降']]
    para('本周下降专项：' + ('、'.join(down) if down else '无') + '（口径：上线台数）')

    heading('⑦ 推广会（本周）', 1)
    b, p = pm['本周'], pm['上周']
    table(['指标', '本周', '上周'],
          [['场次', b['场次'], p['场次']],
           ['参会人次', b['参会人次'], p['参会人次']],
           ['参会服务商', b['参会服务商'], p['参会服务商']]])
    para(f"推广会数据已更新至 {pm['数据末月']}。", size=9, color=(0x6b, 0x76, 0x86))

    doc.save(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='inp', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    J = json.load(open(a.inp, encoding='utf-8'))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    render_docx(J, a.out)
    print(f"✓ docx: {a.out}")


if __name__ == '__main__':
    main()
