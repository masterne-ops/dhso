"""周报校验 — 后端逻辑（网页 page 用）

输入上周+本周两份 SMB 主管周报文本,调已配置的 LLM(call_agent_with_tools),
让它用 generic_sql 等工具查生产库逐项核实,输出结构化校验结果(JSON)。
page 拿结果渲染成 HTML 点评卡片。

复用:
  _anthropic_client.call_agent_with_tools  (已配置的 LLM)
  _llm_tools.get_tool_schemas / execute_tool  (含 generic_sql 只读核实)
"""
from __future__ import annotations
import json
import re

from _anthropic_client import call_agent_with_tools
from _llm_tools import get_tool_schemas, execute_tool


# 校验口径基准（教 LLM 怎么核实 + 标准答案口径）
SYSTEM = """你是浙江省区 SMB 渠道的周报质检员。任务：校验片区主管的周报是否合格，输出结构化结论。

你能调用工具查生产库（SQLite，只读）。核心表 product_flow_v（上线明细，字段：上线城市、上线区县、
出库客户名称=代理商、上线自客户名称=服务商、最新分销价=SO金额、上线时间、上线年月）、
visit_record_v（跑动，拜访客户城市、_打卡异常无效、_真异常打卡、打卡人姓名、活动创建时间）、
kpi_targets（年目标，城市/区县/SO目标_万）、kpi_rhythm（月节奏，指标 LIKE '省区SO进度条%'）、
product_focus（专项映射，物料号→专项）、promotion_meeting（推广会）。用 describe_schema 可查表结构。

【口径基准——这是判主管对错的标准答案】
1. SO = product_flow_v.最新分销价，按 date(上线时间) 切日期。
2. 本周 ≠ 本月：本周是7天窗口（如 5/31~6/6），本月是 substr(上线年月,1,7)。主管最常把本周当本月，重点查。
3. 月目标 = 年目标 × 当月节奏占比。年目标 SUM(kpi_targets.SO目标_万) WHERE 城市=X；
   节奏 kpi_rhythm WHERE 指标 LIKE '省区SO进度条%' AND 月份=N 的占比。默认按此算（除非用户说本月目标有变动）。
4. 完成率 = 当月累计 ÷ (年目标×当月节奏)。注意完成率分母要和目标一致。
5. YTD累计同比 = 今年1/1~本周末 vs 去年同期。
6. *** 代表外省代理商在本省出货，点评里这样写，不要写 *** 。
7. 跑动 = visit_record_v 有效打卡（_打卡异常无效=0 AND _真异常打卡=0），按拜访客户城市。
8. 专项用台数（COUNT(*) join product_focus），不用金额。
9. SI（签约）数据不在 SO 库，无法核实就如实说"SI不在SO库，建议附来源"，不硬判对错。

【校验流程】
A. 从两份周报抽取主管声称的数字和事项（累计SO、同比、完成率、月目标、各代理商SO、区县、攻坚服务商、推广会）。
B. 逐项查生产库核实真假。代理商SO要同时查"本周(7天)"和"本月(上线年月)"两个口径，看主管标对没标对。
C. 【重点】上周待办兑现：从上周周报"下周工作安排/攻坚进展"提取每条承诺，查本周真实数据，
   看上周说要跑/铺货的服务商本周出货了吗、推广会区县本周起量了吗；再看本周周报有没有交代结果，还是原样重列。
D. 漏项：查本周跑动环比、三专项台数——主管没提但数据上明显的（尤其亮点和短板）。
E. 闭环：主管有没有提需要省区支持的资源需求（政策/费用/人力/协调）。没提就指出未闭环。

【效率要求——重要】
- 不要无限核验。每个数字查1-2次确认即可，不要反复换写法查同一个数。
- 优先用 generic_sql 一次查全（如一条 SQL 同时出本周/本月/上周多个口径），少用单点查询。
- 大约查库 20-30 次内必须收敛。够用就停，立即输出 JSON，不要追求穷尽。
- 不需要 describe_schema 反复看表结构（schema 上面已给）。

【写作风格】书面平实、对事不对人、不用情绪词（糊弄/水分/硬伤等）。一事一段。

【输出】最后必须输出一个 JSON（用 ```json 包裹），结构如下，不要有多余文字：
{
  "片区": "衢州",
  "周次": "6月第1周",
  "本周区间": "5/31–6/6",
  "上周区间": "5/17–5/23",
  "评级": "合格/合格偏下/不合格",
  "总评": "一句话总评",
  "待办兑现": [{"badge":"❌/⚠️/✅","text":"<b>标题。</b>正文，含真实数字"}],
  "数据真实性": [{"badge":"✅/❌/⚠️","text":"..."}],
  "口径问题": [{"badge":"1/2","text":"..."}],
  "漏项": [{"badge":"📈/📉","text":"..."}],
  "闭环": [{"badge":"1/2","text":"..."}],
  "建议": [{"badge":"1/2/3","text":"..."}]
}
没有内容的节给空数组。badge 用上面的符号。text 里关键数字加 <b></b>。"""


def _extract_json(text: str) -> dict | None:
    """从 LLM 回复里抽出 ```json ... ``` 块。"""
    m = re.search(r'```json\s*(\{.*?\})\s*```', text, re.S)
    if not m:
        m = re.search(r'(\{[\s\S]*\})', text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def run_check(last_week_text: str, this_week_text: str,
              this_week_end: str = None, note: str = '',
              on_tool_call=None) -> dict:
    """执行周报校验。

    Args:
      last_week_text: 上周周报原文（可空，空则跳过待办兑现）
      this_week_text: 本周周报原文（必填）
      this_week_end:  本周最后一天 YYYY-MM-DD（帮 LLM 锚定窗口）
      note:           用户补充（如"本月目标有调整为X"）
      on_tool_call:   callable(name,args,result) 进度回调
    Returns:
      {'result': dict|None, 'raw': str, 'tool_calls': [...], 'error': str|None}
    """
    parts = []
    if last_week_text and last_week_text.strip():
        parts.append("【上周周报】\n" + last_week_text.strip())
    parts.append("【本周周报】\n" + this_week_text.strip())
    if this_week_end:
        parts.append(f"【本周截止日】{this_week_end}（本周=该日往前7天）")
    if note and note.strip():
        parts.append(f"【用户补充，优先级最高】{note.strip()}")
    user = '\n\n'.join(parts) + '\n\n请校验，最后输出 JSON。'

    resp = call_agent_with_tools(
        system=SYSTEM,
        user_question=user,
        openai_tools=get_tool_schemas(),
        execute_tool_fn=execute_tool,
        max_rounds=28,         # 周报校验要多轮查数(但 prompt 要求 20-30 内收敛)
        max_tokens=4096,
        on_tool_call=on_tool_call,
    )
    raw = resp.get('answer', '')
    result = _extract_json(raw)

    # 兜底：撞轮数上限/没出 JSON → 用已查到的数据强制追问一次，要求立即只输出 JSON
    if result is None:
        tcalls = resp.get('tool_calls', [])
        evidence = '\n'.join(
            f"- {t.get('name')}({json.dumps(t.get('args', {}), ensure_ascii=False)[:120]}) → "
            f"{json.dumps(t.get('result', {}), ensure_ascii=False)[:300]}"
            for t in tcalls[-30:]
        )
        resp2 = call_agent_with_tools(
            system=SYSTEM,
            user_question=(user + "\n\n【已查到的数据，不要再查库，立即基于这些输出最终 JSON】\n"
                           + evidence + "\n\n现在只输出 ```json ... ``` 结果，不要任何其他文字。"),
            openai_tools=None,          # 禁止再调工具,逼它直接出结果
            execute_tool_fn=None,
            max_rounds=1,
            max_tokens=4096,
        )
        raw2 = resp2.get('answer', '')
        result = _extract_json(raw2)
        if result is not None:
            raw = raw2

    return {
        'result': result,
        'raw': raw,
        'tool_calls': resp.get('tool_calls', []),
        'rounds': resp.get('rounds', 0),
        'error': resp.get('error'),
    }


# ══════════════════════════════════════════════
# 渲染：校验结果 JSON → HTML 点评卡片（评级徽章+彩色待办+导出长图）
# ══════════════════════════════════════════════
import html as _html  # noqa: E402

_CSS = """
:root{--pri:#1f5fbf;--pri-d:#14478f;--bg:#f4f6fa;--card:#fff;--line:#e1e6ef;--txt:#1c2430;
--mute:#6b7686;--green:#1f9d55;--red:#d64545;--amber:#d98a00;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font-size:13.5px;line-height:1.6;
font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:840px;margin:0 auto;padding:0 18px 50px}
header{background:linear-gradient(120deg,var(--pri),var(--pri-d));color:#fff;padding:22px 18px;margin-bottom:18px}
header .inner{max-width:840px;margin:0 auto}
h1{margin:0;font-size:20px}.sub{opacity:.88;font-size:12px;margin-top:5px}
.grade{display:inline-block;background:#fff3e0;color:#d98a00;border:1px solid #f0d9a8;
border-radius:20px;padding:2px 13px;font-weight:700;font-size:12.5px;margin-left:8px}
.grade.ok{background:#e9f7ef;color:#1f9d55;border-color:#b6e2c6}
.grade.bad{background:#fdecec;color:#d64545;border-color:#f2c5c5}
h2{font-size:16px;margin:24px 0 10px;padding-left:10px;border-left:4px solid var(--pri)}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;
box-shadow:0 1px 3px rgba(20,40,80,.08);padding:15px 18px;margin-bottom:14px}
.lead{font-size:14px}b.hl{color:var(--pri-d)}
.clist{list-style:none;padding:0;margin:4px 0}
.clist li{display:flex;gap:9px;padding:9px 0;border-top:1px solid var(--line);align-items:flex-start}
.clist li:first-child{border-top:none}
.badge{flex:none;min-width:22px;height:22px;border-radius:6px;font-size:12px;font-weight:700;
display:flex;align-items:center;justify-content:center;color:#fff;margin-top:1px}
.b-no{background:var(--red)}.b-warn{background:var(--amber)}.b-ok{background:var(--green)}
.b-num{background:var(--pri)}.b-up{background:var(--green)}.b-down{background:var(--red)}
.clist .txt{flex:1;font-size:13px;line-height:1.6}
footer{color:var(--mute);font-size:11px;text-align:center;margin-top:24px;border-top:1px solid var(--line);padding-top:12px}
"""


def _badge_cls(b):
    return {'❌': 'b-no', '⚠️': 'b-warn', '✅': 'b-ok',
            '📈': 'b-up', '📉': 'b-down'}.get(str(b).strip(), 'b-num')


def render_check_html(R: dict, standalone: bool = True) -> str:
    """校验结果 dict → HTML。standalone=完整页(含导出长图按钮);False=只返回内容片段。"""
    def esc(s):
        return _html.escape(str(s)) if s else ''

    grade = R.get('评级', '')
    gcls = 'ok' if grade.startswith('合格') and '偏下' not in grade else ('bad' if '不合格' in grade else '')
    片区 = esc(R.get('片区', ''))
    P = []
    P.append(f'<header><div class="inner"><h1>{片区} SMB 周报校验'
             f'<span class="grade {gcls}">评级：{esc(grade)}</span></h1>')
    P.append(f'<div class="sub">{esc(R.get("周次",""))} · 本周 {esc(R.get("本周区间",""))} '
             f'· 对照上周 {esc(R.get("上周区间",""))} · 数据经生产库核实</div></div></header>')
    P.append('<div class="wrap">')
    if R.get('总评'):
        P.append(f'<div class="card"><p class="lead"><b class="hl">总评：</b>{esc(R["总评"])}</p></div>')

    SECTIONS = [
        ('一、上周待办兑现核对（重点）', '待办兑现'),
        ('二、数据真实性', '数据真实性'),
        ('三、口径问题', '口径问题'),
        ('四、漏项', '漏项'),
        ('五、闭环', '闭环'),
        ('六、给主管的建议', '建议'),
    ]
    for title, key in SECTIONS:
        items = R.get(key) or []
        if not items:
            continue
        P.append(f'<h2>{title}</h2><div class="card"><ul class="clist">')
        for it in items:
            badge = esc(it.get('badge', ''))
            txt = it.get('text', '')  # text 允许含 <b>，不转义
            P.append(f'<li><span class="badge {_badge_cls(it.get("badge",""))}">{badge}</span>'
                     f'<span class="txt">{txt}</span></li>')
        P.append('</ul></div>')
    P.append('<footer>周报校验 · 数字经生产库逐项核实 · 仅供内部参考</footer></div>')
    body = '\n'.join(P)

    if not standalone:
        return f'<style>{_CSS}</style>' + body

    fname = json.dumps(f"{R.get('片区','')}周报校验-{R.get('周次','')}", ensure_ascii=False)
    script = ('<script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>'
              '<button id="eb" onclick="ex()" style="position:fixed;top:14px;right:14px;z-index:9;'
              'background:#fff;color:#14478f;border:1px solid #1f5fbf;border-radius:8px;padding:7px 12px;'
              'font-size:13px;font-weight:600;cursor:pointer">📷 导出长图</button>'
              '<script>async function ex(){var b=document.getElementById("eb");'
              'if(typeof html2canvas==="undefined"){alert("组件未加载，请联网刷新");return;}'
              'b.disabled=true;b.textContent="生成中…";b.style.display="none";'
              'await new Promise(function(r){setTimeout(r,50);});'
              'try{var c=await html2canvas(document.body,{scale:2,useCORS:true,backgroundColor:"#f4f6fa",'
              'width:document.body.clientWidth,windowWidth:document.body.clientWidth});'
              'var a=document.createElement("a");a.download=' + fname + '+".png";'
              'a.href=c.toDataURL("image/png");a.click();}catch(e){alert("导出失败："+e.message);}'
              'finally{b.style.display="";b.disabled=false;b.textContent="📷 导出长图";}}</script>')
    return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1.0">'
            f'<style>{_CSS}</style></head><body>{script}{body}</body></html>')
