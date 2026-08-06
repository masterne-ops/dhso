#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把衢州周报校验点评渲染成 HTML(含手机版+导出长图)。复用周报渲染助手。"""
import sys
sys.path.insert(0, '/Users/cory/claude/projects/ai-consulting/scenarios/so数据分析/report_tools')
import json
from render import CSS, h  # noqa: E402

TITLE = '衢州 SMB 周报校验'
SUB = '6月第1周（本周 5/31–6/6 · 对照上周 5/17–5/23）· 数据经生产库核实'
GRADE = '合格偏下'
SUMMARY = ('数据真实可信，区县和攻坚客户的判断也站得住。但口径问题连续两周重复，'
           '上周待办基本未兑现，且未提资源需求，未形成闭环。')

# (标题, 类型, 内容)  类型: ol=有序列表[(badge,文本)] / p=段落 / table
SECTIONS = [
    ('一、上周待办兑现核对（本次重点）', 'lead',
     '上周周报承诺的事项，对照本周真实数据：'),
    ('', 'ol', [
        ('❌', '<b>攻坚4家服务商未兑现。</b>上周点名亿米、俊峰、常山华科、龙游迅维，承诺"下周重点跑动/铺货"；本周这4家出货均为0。本周周报又将同样4家原样重列"下周重点跑动"，待办在空转。'),
        ('❌', '<b>常山华科铺货承诺未兑现。</b>上周写"下周铺货2台、目标提货500元"，本周华科0出货，无结果交代。'),
        ('❌', '<b>龙游迅维铺货未见效。</b>上周写"铺货20台"，本周迅维0出货。'),
        ('⚠️', '<b>龙游推广会效果微弱。</b>上周写"下周跟进服务商上线"，本周龙游县SO仅0.16万、9台，为全市最低。'),
        ('⚠️', '<b>衢江"已提货未上线"未见改善。</b>连续两周列为重点，衢江本周仍仅0.41万，无改善。'),
        ('⚠️', '<b>金智"盘库排查异常"无结论。</b>上周承诺，本周未交代结果，仅称"本月SI 3.1w"。'),
    ]),
    ('', 'note', '上周6项待办无一明确兑现，且多项在本周原样重提，缺少"上周做没做、为什么没做"的交代。'),

    ('二、数据真实性', 'lead', '核心经营数字真实，主管未编数字：'),
    ('', 'ol', [
        ('✅', '本周累计SO 172.2万，真实172.8万；累计同比53%，真实+53.6%——吻合。'),
        ('✅', '6月实际达成5.1万，与真实一致。'),
        ('✅', '区县（衢江、江山进度偏慢）、攻坚4家本周无出货——经核实均属实，判断准确。'),
    ]),

    ('三、口径问题（连续两周重复）', 'ol', [
        ('1', '<b>本周/本月混用。</b>本周写"国勇本月5.1万"，实为本周（5/31–6/6）数；其中5/31占1.14万计入5月，国勇6月当月实际仅3.91万，距月度目标差距比周报体现的更大。上周同样将5月当月数标为"4月SO目标"，月份口径连续两周标错。'),
        ('2', '<b>目标与完成率口径需核对。</b>按系统节奏测算（年目标520万 × 6月节奏9.5%），6月应达成约49.4万；周报写39.6万，差9.8万，请说明来源或按系统口径修正。完成率5.1÷49.4=10.3%（与周报一致），但5.1÷39.6应为12.9%，目标与完成率用了两套分母，需统一。'),
    ]),

    ('四、漏项', 'ol', [
        ('📈', '<b>本周跑动是亮点，未提及。</b>有效打卡由上周31次增至本周65次，增长110%，是本周最明显的正向变化。'),
        ('📉', '<b>三大产品专项偏弱，未纳入。</b>本周全市夜视王上线2台、无线36台、场景化8台，量级偏低，属结构性短板。'),
    ]),

    ('五、闭环缺失', 'ol', [
        ('1', '<b>未提资源需求。</b>周报列了下周动作，但未提出需要省区支持的事项（政策、费用、人力、协调）。建议写明本周卡点和所需支持，使计划可落地、可跟进。'),
        ('2', '<b>待办无闭环。</b>上周待办在本周缺少兑现交代，建议每周固定增设"上周待办完成情况"一栏。'),
    ]),

    ('六、给主管的建议', 'ol', [
        ('1', '周报固定增设"上周待办完成情况"，逐条写明做没做、未做原因。'),
        ('2', '统一时间口径，本周与本月分清，月份标题与正文一致。'),
        ('3', '目标与完成率用同一套口径，并说明目标来源。'),
        ('4', '补充本周亮点（跑动）与短板（专项），以及需要省区支持的资源需求。'),
        ('5', '攻坚客户连续两周无出货的，应分析原因或调整名单，而非重复罗列。'),
    ]),
]


def render():
    P = []
    P.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">')
    P.append('<meta name="viewport" content="width=device-width,initial-scale=1.0">')
    P.append(f'<title>{TITLE}</title><style>{CSS}')
    P.append('.toolbar{position:fixed;top:14px;right:14px;z-index:999;display:flex;gap:8px}'
             '.toolbar button{background:#fff;color:var(--pri-d);border:1px solid var(--pri);border-radius:8px;'
             'padding:7px 12px;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 2px 8px rgba(20,40,80,.15)}'
             '.toolbar button:hover{background:var(--pri);color:#fff}.toolbar button:disabled{opacity:.6}'
             '.exporting .toolbar{display:none!important}@media print{.toolbar{display:none!important}}')
    P.append('body.mobile .wrap{max-width:440px}body.mobile header .inner{max-width:440px}')
    P.append('@media(max-width:520px){.wrap{max-width:100%;padding:0 12px 60px}h1{font-size:20px}}')
    # 自定义
    P.append('.grade{display:inline-block;background:#fff3e0;color:#d98a00;border:1px solid #f0d9a8;'
             'border-radius:20px;padding:3px 14px;font-weight:700;font-size:13px;margin-left:8px}')
    P.append('.clist{list-style:none;padding:0;margin:8px 0}'
             '.clist li{display:flex;gap:9px;padding:9px 0;border-top:1px solid var(--line);align-items:flex-start}'
             '.clist li:first-child{border-top:none}'
             '.badge{flex:none;min-width:22px;height:22px;border-radius:6px;font-size:12px;font-weight:700;'
             'display:flex;align-items:center;justify-content:center;color:#fff;margin-top:1px}'
             '.b-no{background:var(--red)}.b-warn{background:var(--amber)}.b-ok{background:var(--green)}'
             '.b-num{background:var(--pri)}.b-up{background:var(--green)}.b-down{background:var(--red)}'
             '.clist .txt{flex:1;font-size:13.2px;line-height:1.6}')
    P.append('.note{background:#fff8ec;border:1px solid #f0d9a8;border-radius:9px;padding:11px 14px;'
             'font-size:12.8px;color:#8a6d3b;margin:10px 0}')
    P.append('</style></head><body>')
    P.append('<div class="toolbar"><button id="viewBtn" onclick="toggleMobile()">📱 手机版</button>'
             '<button id="exportBtn" onclick="exp()">📷 导出长图</button></div>')
    P.append(f'<header><div class="inner"><h1>{TITLE}<span class="grade">评级：{GRADE}</span></h1>')
    P.append(f'<div class="sub">{SUB}</div></div></header><div class="wrap">')
    # 总评
    P.append(f'<div class="card"><p class="lead"><b class="hl">总评：</b>{SUMMARY}</p></div>')

    def badge_cls(b):
        return {'❌': 'b-no', '⚠️': 'b-warn', '✅': 'b-ok', '📈': 'b-up', '📉': 'b-down'}.get(b, 'b-num')

    cur_h2 = None
    open_card = False
    for title, typ, content in SECTIONS:
        if title:
            if open_card:
                P.append('</div>')
            P.append(f'<h2>{h(title)}</h2><div class="card">')
            open_card = True
        if typ == 'lead':
            P.append(f'<p>{content}</p>')
        elif typ == 'note':
            P.append(f'<div class="note">{content}</div>')
        elif typ == 'ol':
            P.append('<ul class="clist">')
            for badge, txt in content:
                P.append(f'<li><span class="badge {badge_cls(badge)}">{badge}</span>'
                         f'<span class="txt">{txt}</span></li>')
            P.append('</ul>')
    if open_card:
        P.append('</div>')

    P.append('<footer>衢州 SMB 周报校验 · 数字经生产库逐项核实 · 仅供内部参考</footer></div>')
    fname = json.dumps('衢州SMB周报校验-6月第1周', ensure_ascii=False)
    P.append('<script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>')
    P.append('<script>function toggleMobile(){var m=document.body.classList.toggle("mobile");'
             'document.getElementById("viewBtn").textContent=m?"🖥 桌面版":"📱 手机版";}'
             'async function exp(){var b=document.getElementById("exportBtn");'
             'if(typeof html2canvas==="undefined"){alert("组件未加载，请联网刷新");return;}'
             'var m=document.body.classList.contains("mobile");'
             'b.disabled=true;b.textContent="生成中…";document.body.classList.add("exporting");'
             'await new Promise(function(r){setTimeout(r,60);});'
             'try{var c=await html2canvas(document.body,{scale:2,useCORS:true,backgroundColor:"#f4f6fa",'
             'width:document.body.clientWidth,windowWidth:document.body.clientWidth});'
             'var a=document.createElement("a");a.download=' + fname + '+(m?"-手机版":"")+".png";'
             'a.href=c.toDataURL("image/png");a.click();}catch(e){alert("导出失败："+e.message);}'
             'finally{document.body.classList.remove("exporting");b.disabled=false;b.textContent="📷 导出长图";}}')
    P.append('</script></body></html>')
    return '\n'.join(P)


out = '/Users/cory/claude/projects/ai-consulting/scenarios/so数据分析/分析报告/周报校验-衢州SMB-6月第1周.html'
open(out, 'w', encoding='utf-8').write(render())
print('✓', out)
