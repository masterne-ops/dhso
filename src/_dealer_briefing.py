# -*- coding: utf-8 -*-
"""代理商经营简报 · 服务器端编排
固定取数(build_briefing_data.py) + LLM写本周小结(套报告写作规范) + 固定渲染(build_briefing_html.py)。
供 Streamlit 页面「📋 代理商经营简报」调用。取数/渲染复用 代理商群运营/ 下的固定脚本，
小结走 _llm.call_llm —— 与本机 skill「代理商简报」同一套口径与写法。"""
import json, subprocess, sys, tempfile, os, re
from pathlib import Path

BRIEF_DIR = Path(__file__).resolve().parent.parent / '代理商群运营'
DATA_PY = str(BRIEF_DIR / 'build_briefing_data.py')
HTML_PY = str(BRIEF_DIR / 'build_briefing_html.py')
PROD_DB = '/opt/so-data-analytics/db/product_flow.db'

# 与本机 skill / 报告写作规范 一致的小结系统提示
SUMMARY_SYS = (
    "你是大华浙江省区渠道运营分析师，为代理商经营简报写「本周小结」。小结就放在已列出全部数字的卡片下方，"
    "因此【不要复述数字】，要给判断和观点。严格遵循写作规范：\n"
    "- 书面平实、去AI味，像分析师书面汇报；不口语化、不炫技、不喊口号、不写激励语。\n"
    "- 数量变化用经济术语(环比/同比、增长/下降)，不用「多了/少了/涨了/跌了」；禁 AI 空词(赋能/抓手/动能换挡/整体来看/值得注意的是…)与夸张比喻(断崖/塌方/熄火…)。\n"
    "- 判断必须有数据依据(依据来自给定数据)，不编造数字；个别要点的关键数字可少量引用，但不整段罗列。\n"
    "- 每家 2~3 句，按此结构给观点：①定性其体量与位次(头部/中游/偏小、是否本市领先)；"
    "②点一个结构亮点或隐患(如 SO 靠少数大单、红包与SO口径背离、服务商分级偏低/V3以上偏少、新激活或跑动不足)；"
    "③一句关注或改进方向。"
)

def _digest_city(city, cd, start, end, days):
    lines = [f"城市：{city}（{start}~{end}，{days}天）。为下列每家代理商各写一条本周小结。"
             "只输出一个 JSON 对象：键=代理商全名、值=小结文本，不要任何其他文字。\n"]
    for d in cd['dealers']:
        so = d['SO']; t = d['分级']
        lines.append(
            f"[{d['名称']}] 第{d['排名']}名 本市SO{so['本市台']}台/{so['本市万']}万 "
            f"本省SO{so['本省台']}台/{so['本省万']}万 红包{d['红包台']}台/{d['红包万']}万 "
            f"交易服务商{d['交易服务商']} 新注册{d['新注册']}/新激活{d['新激活']} "
            f"跑动{d['跑动次数']}次/{d['跑动家数']}家 分级V2+={t['v2']+t['v3']+t['v4']+t['v5']}")
    return "\n".join(lines)

def _parse_json(txt):
    s = (txt or '').strip()
    if s.startswith('```'):
        s = s.split('```')[1]
        s = s[4:] if s.startswith('json') else s
    try:
        return json.loads(s)
    except Exception:
        pass
    i, j = s.find('{'), s.rfind('}')
    if 0 <= i < j:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            pass
    # 兜底：JSON 被截断也能抢救出所有完整的 "键":"值" 对（防大市小结因 max_tokens 截断全丢）
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"', s)
    return {k: v for k, v in pairs} if pairs else {}

def llm_ready():
    """简报小结的 LLM 是否就绪（走服务器已配置的 Anthropic 路径）。"""
    try:
        import _anthropic_client as ac
        return ac.is_configured()
    except Exception:
        return False

def gen_summaries(data):
    """每城市一次 LLM 调用，返回 {城市:{代理商全名:小结}}。
    走服务器已配置的 Anthropic 路径（与「AI 代码助手」「经营周报」同一套 anthropic_config，
    而非 _llm 的 Kimi/通义/豆包 —— 后者服务器从未配置，曾导致小结全空）。"""
    import _anthropic_client as ac
    m = data['meta']; notes = {}
    for city, cd in data['cities'].items():
        if not cd['dealers']:
            continue
        try:
            resp = ac.call_messages(
                system=SUMMARY_SYS,
                messages=[{'role': 'user', 'content': _digest_city(city, cd, m['起'], m['止'], m['天数'])}],
                # 模型带推理、占 token；按家数放大，防大市(杭州10家)JSON 被截断
                max_tokens=min(8000, max(4000, 700 * len(cd['dealers']))),
            )
            notes[city] = _parse_json(ac.extract_text(resp))
        except Exception:
            notes[city] = {}
    return notes

def build(start, end, db=PROD_DB, progress=None):
    """跑全流程，返回 (html_str, data_dict)。progress(msg) 为可选回调。"""
    def p(msg):
        if progress:
            progress(msg)
    with tempfile.TemporaryDirectory() as td:
        data_json = os.path.join(td, 'data.json')
        notes_json = os.path.join(td, 'notes.json')
        out_html = os.path.join(td, 'out.html')
        p('① 取数（生产库）…')
        subprocess.run([sys.executable, DATA_PY, start, end, db, data_json], check=True)
        data = json.load(open(data_json, encoding='utf-8'))
        p('② LLM 写本周小结…')
        notes = gen_summaries(data)
        json.dump(notes, open(notes_json, 'w', encoding='utf-8'), ensure_ascii=False)
        p('③ 渲染 HTML…')
        subprocess.run([sys.executable, HTML_PY, data_json, out_html, notes_json], check=True)
        return open(out_html, encoding='utf-8').read(), data
