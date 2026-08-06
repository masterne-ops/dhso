# -*- coding: utf-8 -*-
"""代理商经营诊断 · 服务器端编排
固定取数(build_diag_data.py)+聚类(cluster_diag.py)+固定渲染(build_diag_html.py)，
诊断小结/里程碑走 _anthropic_client（复用「AI 代码助手」已配置的 LLM，套报告写作规范）—— 与本机 skill「代理商诊断」同一套口径与写法。
供 Streamlit 页面「🩺 代理商经营诊断」调用。标杆对标对象一律匿名为 标杆1/标杆2/标杆3。"""
import json, subprocess, sys, tempfile, os, datetime
from pathlib import Path

DIAG_DIR = Path(__file__).resolve().parent.parent / '代理商诊断'
DATA_PY = str(DIAG_DIR / 'build_diag_data.py')
CLUSTER_PY = str(DIAG_DIR / 'cluster_diag.py')
HTML_PY = str(DIAG_DIR / 'build_diag_html.py')
PROD_DB = '/opt/so-data-analytics/db/product_flow.db'

# 诊断小结/里程碑缓存（按 代理商|对标标杆|数据库时间 去重，省 token；DB 更新即自动失效）
CACHE_PATH = Path.home() / '.so_data_analytics' / 'diag_cache.json'


def db_time(db=PROD_DB):
    """数据库时间 = DB 文件最后修改时间（YYYY-MM-DD HH:MM）。DB 更新则变，用于缓存失效。"""
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(db)).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return '?'


def cache_key(client_name, bench_label, dbt):
    return f"{client_name}|{bench_label}|{dbt}"


def _load_cache():
    try:
        return json.load(open(CACHE_PATH, encoding='utf-8'))
    except Exception:
        return {}


def _save_cache(cache):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        json.dump(cache, open(CACHE_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    except Exception:
        pass

DIAG_SYS = (
    "你是大华浙江省区渠道运营分析师，为代理商做「对标诊断小结」与「追赶里程碑」。严格遵循报告写作规范：\n"
    "- 书面平实、去AI味；数量变化用经济术语(达成/环比/增长/下降)，不用「多了/少了」。\n"
    "- 禁用 AI 空词(赋能/抓手/动能换挡/整体来看/值得注意的是…)与夸张比喻(断崖/塌方/熄火…)。\n"
    "- 简明扼要、只描述事实；每个数字都来自给定数据，不自己算、不编造。\n"
    "- 标杆一律称「标杆1/标杆2/标杆3」，绝不写真名。\n"
    "只输出一个 JSON 对象：{\"小结\":\"...\",\"里程碑\":\"...\"}，不要任何其他文字。\n"
    "- 小结(2~4句)：客户规模档/全省名次 + 对标该标杆综合达成% + 强项(1~2维) + 主要短板(1~2维带达成%) + 核心问题一句。\n"
    "- 里程碑：按短板优先级分 近期/中期/远期，每条=「维度：从X提到Y(达标杆约60%)+具体动作」，用\\n分行，3~4条。"
)

# 与 build_diag_html.py 的 DIMS 一致
DIMS = [
    ('SO规模', lambda x: x['SO']['万'], '万'),
    ('红包产出', lambda x: x['红包']['万'], '万'),
    ('服务商盘', lambda x: x['名下服务商'] or x['服务商总数'], '家'),
    ('服务商质量', lambda x: round(x['派生']['高阶服务商占比'] * 100), '%'),
    ('铺货深度', lambda x: x['铺货']['台'], '台'),
    ('跑动强度', lambda x: x['跑动']['次数'], '次'),
    ('推广运营', lambda x: x['推广']['参与人数'], '人'),
]


def extract(db=PROD_DB, progress=None):
    """取数+聚类，返回全省特征 data dict。"""
    if progress:
        progress('① 取数（生产库）…')
    with tempfile.TemporaryDirectory() as td:
        dj = os.path.join(td, 'diag.json')
        subprocess.run([sys.executable, DATA_PY, db, dj], check=True)
        subprocess.run([sys.executable, CLUSTER_PY, dj], check=True)
        return json.load(open(dj, encoding='utf-8'))


def _parse_json(txt):
    s = (txt or '').strip()
    if s.startswith('```'):
        s = s.split('```')[1]
        s = s[4:] if s.startswith('json') else s
    try:
        return json.loads(s)
    except Exception:
        i, j = s.find('{'), s.rfind('}')
        if 0 <= i < j:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                return {}
        return {}


def _digest(c, b, label):
    rows = []
    for k, g, u in DIMS:
        cv = g(c) or 0
        bv = g(b) or 0
        pct = round(cv / bv * 100) if bv else 0
        rows.append(f"- {k}：客户 {cv}{u} / {label} {bv}{u}（达成 {pct}%）")
    lv = c['服务商分级']
    pic = '、'.join(f"{k}{v}" for k, v in c['画像'].items() if v)
    return (
        f"客户【{c['名称']}】（{c['城市']}·规模档{c['规模档']}），对标【{label}】。\n"
        f"七维对比：\n" + "\n".join(rows) + "\n"
        f"客户画像：签约{c['签约万']}万，名下服务商{c['名下服务商']}、激活率{round(c['激活率']*100)}%，"
        f"服务商分级V0-V5={lv['v0']}/{lv['v1']}/{lv['v2']}/{lv['v3']}/{lv['v4']}/{lv['v5']}，成交画像 {pic}。\n"
        "请据此写诊断小结与追赶里程碑（JSON）。"
    )


def gen_notes(data, client_name, bench_label):
    """调「AI 代码助手」已配置的 LLM(_anthropic_client / anthropic_config.json) 生成 {小结, 里程碑}。"""
    from _anthropic_client import call_messages, extract_text
    by = {x['名称']: x for x in data['dealers']}
    c = by[client_name]
    b = next(x for x in data['dealers'] if x.get('是标杆') == bench_label)
    resp = call_messages(system=DIAG_SYS,
                         messages=[{'role': 'user', 'content': _digest(c, b, bench_label)}],
                         max_tokens=4000)   # 推理模型(thinking)需余量，否则只出thinking无text
    n = _parse_json(extract_text(resp))
    return {'小结': n.get('小结', ''), '里程碑': n.get('里程碑', '')}


def render(data, client_name, bench_label, notes):
    """渲染单文件诊断 HTML（默认选中该客户、默认对标该标杆，烤入 LLM 小结/里程碑）。"""
    with tempfile.TemporaryDirectory() as td:
        dj = os.path.join(td, 'd.json')
        nj = os.path.join(td, 'n.json')
        oh = os.path.join(td, 'o.html')
        json.dump(data, open(dj, 'w', encoding='utf-8'), ensure_ascii=False)
        json.dump({client_name: notes}, open(nj, 'w', encoding='utf-8'), ensure_ascii=False)
        subprocess.run([sys.executable, HTML_PY, dj, oh, nj, client_name, bench_label], check=True)
        return open(oh, encoding='utf-8').read()


def build(client_name, bench_label, data=None, db=PROD_DB, progress=None, force=False):
    """全流程：取数(可传缓存的 data)→[同 代理商|对标标杆|数据库时间 已生成过则复用、不调LLM]→渲染。
    返回 (html, notes, hit, dbt)。hit=True 表示命中缓存(未消耗 token)。force=True 强制重生成。"""
    dbt = db_time(db)
    key = cache_key(client_name, bench_label, dbt)
    cache = _load_cache()
    hit = (not force) and bool(cache.get(key, {}).get('notes', {}).get('小结'))
    if hit:
        notes = cache[key]['notes']
    else:
        if data is None:
            data = extract(db, progress)
        if progress:
            progress('② LLM 写诊断小结/里程碑…')
        notes = gen_notes(data, client_name, bench_label)
        cache[key] = {'代理商': client_name, '对标标杆': bench_label, '数据库时间': dbt,
                      '生成时间': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'notes': notes}
        _save_cache(cache)
    if data is None:
        data = extract(db, progress)
    if progress:
        progress('③ 渲染 HTML…')
    data.setdefault('meta', {})['数据库时间'] = dbt
    return render(data, client_name, bench_label, notes), notes, bool(hit), dbt
