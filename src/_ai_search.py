"""AI 智能搜索 — 把用户的模糊需求映射到具体功能 page

策略：
  ① 关键词初筛（永远跑，零延迟兜底）
  ② LLM 重排（若已配置 LLM，对前 N 条做语义重排）

输出：top-3 推荐 page 列表，含 reason 解释为什么推。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from _page_registry import PAGES, filter_for_role


# ══════════════════════════════════════════════
# SKILL（系统提示）
# ══════════════════════════════════════════════

SEARCH_SKILL = """你是 SO 数据分析平台的「智能导航助手」。

任务：用户用自然语言描述一个数据需求 / 工作场景，你从下面给出的 page 列表里挑出**最贴近**的 **3 个 page**，给出推荐顺序。

输出严格 JSON 格式，**仅含一个数组**，不要其他解释：

```json
[
  {"url": "rfm",        "reason": "12 字以内为什么这个 page 最匹配"},
  {"url": "dispatch",   "reason": "..."},
  {"url": "visit-eval", "reason": "..."}
]
```

规则：
- 只能从下面 `pages` 提供的 url 里选，**不要发明新 url**
- 推荐 **3 个**（最贴近的在第一位）
- reason 用中文，简短到能在按钮上一行显示
- 如果用户问的明显不在范围（比如「天气怎么样」），返回空数组 `[]`
- 不要解释、不要 markdown、不要多余文字，**只输出 JSON 数组**
"""


# ══════════════════════════════════════════════
# 关键词初筛
# ══════════════════════════════════════════════

def _tokenize(text: str) -> list[str]:
    """中文按字 + 词 双切：'代理商排名' → ['代', '理', '商', '排', '名', '代理', '理商', '商排', '排名', ...]
    再加上正则提取的"词块"
    """
    text = (text or '').lower().strip()
    out = set()
    # 词块（连续中文 / 字母数字）
    for w in re.findall(r'[一-鿿]+|[a-z0-9]+', text):
        if w:
            out.add(w)
    # 中文 2-gram（提升模糊匹配召回）
    for w in re.findall(r'[一-鿿]+', text):
        for i in range(len(w) - 1):
            out.add(w[i:i+2])
    return list(out)


def _keyword_score(query: str, page: dict) -> float:
    """关键词/标题/描述匹配分数 — 中文按字 + 2-gram"""
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0
    score = 0.0
    title = (page.get('title') or '').lower()
    desc = (page.get('description') or '').lower()
    kws_blob = ' '.join(str(k).lower() for k in page.get('keywords', []))
    haystack_blob = (title + ' ' + desc + ' ' + kws_blob).lower()

    # 完整 query 在 title/desc 命中
    q_full = query.lower().strip()
    if q_full and q_full in title:
        score += 8.0
    elif q_full and q_full in desc:
        score += 4.0

    for tok in q_tokens:
        if len(tok) < 1:
            continue
        if tok in title:
            score += 3.0 * len(tok)            # 长 token 命中加权
        elif tok in desc:
            score += 1.5 * len(tok)
        elif tok in kws_blob:
            score += 2.0 * len(tok)
        # 字符级模糊匹配（每个字符独立加 0.2，避免完全无 hit）
        if len(tok) == 1 and tok in haystack_blob:
            score += 0.3
    return score


def keyword_rank(query: str, pages: list, top_k: int = 10) -> list:
    """关键词初筛：永远返回 top_k（即使分低也排序展示）"""
    scored = [
        {**p, '_score': _keyword_score(query, p)}
        for p in pages
    ]
    scored.sort(key=lambda x: -x['_score'])
    return scored[:top_k]


# ══════════════════════════════════════════════
# LLM 重排（可选）
# ══════════════════════════════════════════════

def _build_pages_for_prompt(pages: list[dict]) -> str:
    """把 page 列表序列化到 prompt 里"""
    lines = []
    for p in pages:
        lines.append(
            f"- url={p['url']}: {p['title']} — {p['description']} "
            f"[关键词: {', '.join(p.get('keywords', [])[:6])}]"
        )
    return '\n'.join(lines)


def llm_rerank(query: str, candidates: list[dict]) -> Optional[list[dict]]:
    """让 LLM 在 candidates 里挑前 3。失败时返回 None（调用方退回 keyword 排序）"""
    if not candidates:
        return []
    try:
        from _llm import call_llm, is_configured
        if not is_configured():
            return None
    except Exception:
        return None

    sys_prompt = SEARCH_SKILL + "\n\npages:\n" + _build_pages_for_prompt(candidates)
    user_prompt = f"用户输入：{query}"

    try:
        from _llm import call_llm
        resp = call_llm(sys_prompt, user_prompt, max_tokens=512, temperature=None)
    except Exception:
        return None

    # 解析 JSON
    m = re.search(r'\[\s*(\{.*?\}\s*,?\s*)*\]', resp, re.DOTALL)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(arr, list):
        return None

    # 把 LLM 选出来的 url 映射回完整 page 信息
    by_url = {p['url']: p for p in candidates}
    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        url = item.get('url')
        page = by_url.get(url)
        if not page:
            continue
        out.append({**page, '_reason': item.get('reason', '')})
    return out[:3]


# ══════════════════════════════════════════════
# 总入口
# ══════════════════════════════════════════════

def search(query: str, role: str = '') -> list[dict]:
    """返回 top-3 推荐 pages。带 _reason 字段（LLM 给的理由 / 关键词命中）"""
    pages = filter_for_role(role)
    # 排除「智能搜索」自己
    pages = [p for p in pages if p['url'] != 'search']

    candidates = keyword_rank(query, pages, top_k=10)
    if not candidates:
        return []

    # 全 0 分（完全没命中关键词），认为是无关查询
    if max(c['_score'] for c in candidates) == 0:
        return []

    reranked = llm_rerank(query, candidates)
    if reranked:
        return reranked

    # LLM 没启用 → 用关键词排序前 3
    top = [c for c in candidates if c['_score'] > 0][:3]
    if not top:
        return []
    max_score = max(c['_score'] for c in top)
    return [
        {**p, '_reason': _reason_text(p, query, max_score)}
        for p in top
    ]


def _reason_text(page: dict, query: str, max_score: float) -> str:
    """生成一个对用户更友好的理由文字"""
    s = page['_score']
    pct = (s / max_score * 100) if max_score > 0 else 0
    if pct >= 80:
        return "🎯 高度匹配"
    if pct >= 50:
        return "✅ 较为匹配"
    return "💡 可能相关"
