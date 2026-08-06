"""会议纪要数据校验 — 抽取声明 + 调工具核对

工作流：
  1. extract_claims(text) → 用 LLM 抽取「可被 DB 验证的数据声明」清单
  2. audit_claim(claim) → 对一条声明运行 mini-Agent，调工具查 DB 拿实际值
  3. 状态分级（一致/偏差/严重不符/无法验证/口径差异）
"""

import json
import re
from typing import List, Dict, Any

from _anthropic_client import (
    call_messages, extract_text, call_agent_with_tools, is_configured as _anth_configured,
)
from _llm_tools import get_tool_schemas, execute_tool


# ══════════════════════════════════════════════
# 抽取阶段
# ══════════════════════════════════════════════

EXTRACT_SYSTEM_PROMPT = """你是数据审计专家。从会议纪要中提取所有「可被 SQLite 数据库验证的数值型数据声明」。

【数据库覆盖】（仅以下范围内的声明算"可验证"）
- 浙江省服务商相关数据（地市/区县级别）
- 数据维度：服务商签约、上线（设备激活扫码）、红包中奖、业务员跑动、服务商等级
- 时间范围：2025-01 至 2026-04

【可验证 ≠ 不可验证】
✅ 可验证：杭州市本月新签 50 家服务商 / V4 服务商累计 631 家 / 黄明君跑了 30 次
❌ 不可验证：业务做得好 / 客户满意度高 / 战略要落地（无具体数值）
❌ 不可验证：浙江以外的数据（如江苏数据）/ 非服务商数据（如 HR/财务/市场）

【关键业务概念】
- 「服务商等级」按累计上线金额：≥¥1k=已激活, ¥1k-1w=v2, ¥1w-3w=v3, ≥¥3w=v4
- 「上线」= 服务商激活设备扫码，每台货值累加
- 「中奖」= 抽奖中奖金额，跟等级无关
- 「假商」= 马甲(已确认) / 伞形 / 假签约 / 套上线

【输出格式】严格 JSON 数组，**不要任何其他文字**：

[
  {
    "id": 1,
    "claim": "原文引用 — 完整一句话",
    "metric_type": "家数 | 台数 | 金额(元/万元) | 百分比 | 排名 | 比率 | 次数",
    "filters": {
      "地市": "杭州市",
      "区县": null,
      "时段": "2026-04",
      "对象": "新签服务商",
      "其他": null
    },
    "expected_value": 50,
    "expected_unit": "家",
    "verifiable": true,
    "verify_strategy": "查 provider_contract 中签约日期在 2026-04 的杭州市记录数量"
  }
]

【提取规则】
1. 数值要标准化：50 家 → expected_value=50, expected_unit="家"；5000万 → expected_value=50000000, expected_unit="元"
2. 时段要绝对化："本月" → 推断会议日期对应的月份（如纪要标注 2026-05-09，则"本月"= 2026-05，"上月"= 2026-04）
3. "新签/已激活/V3/V4" 这些标准化术语保留
4. 一句话含多个数据声明 → 拆成多条
5. 重复声明 → 去重保留一条
6. 主观描述 → 跳过，不要硬塞
7. 最多输出 30 条，按重要性排序"""


def build_extract_user_prompt(meeting_text: str, meeting_date: str = None) -> str:
    """构造抽取用户 prompt"""
    if meeting_date:
        date_hint = f"\n【会议日期】 {meeting_date}（用于解析「本月」「上月」等相对时间）\n"
    else:
        date_hint = ""
    return f"""请从下面的会议纪要中提取所有可验证的数值型数据声明，以严格 JSON 数组返回。
{date_hint}
=== 会议纪要原文 ===
{meeting_text}
=== 原文结束 ===

请只输出 JSON 数组，不要其他文字。"""


def extract_claims(meeting_text: str, meeting_date: str = None) -> List[Dict[str, Any]]:
    """调 LLM 抽取数据声明清单（不带 tools）

    复用 AI 代码助手的 Anthropic 配置（~/.so_data_analytics/anthropic_config.json）

    Returns:
        list of claim dict（已 parse 成 Python list）
    """
    if not _anth_configured():
        raise RuntimeError("LLM 未配置。请先到「AI 代码助手」侧栏配 Base URL / Token / Model")

    user_prompt = build_extract_user_prompt(meeting_text, meeting_date)

    resp = call_messages(
        system=EXTRACT_SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': user_prompt}],
        max_tokens=4000,
    )
    raw = extract_text(resp) or '[]'
    return _parse_claims_json(raw)


def _parse_claims_json(raw: str) -> List[Dict[str, Any]]:
    """从 LLM 返回的文本中提取 JSON 数组（容错：可能被 ```json``` 包裹）"""
    # 去掉 markdown 代码块
    raw = raw.strip()
    if raw.startswith('```'):
        # 截掉首行 ```json 和末行 ```
        m = re.search(r'```(?:json)?\s*(.+?)```', raw, flags=re.DOTALL)
        if m:
            raw = m.group(1).strip()

    # 找到第一个 [ 和最后一个 ]
    start = raw.find('[')
    end = raw.rfind(']')
    if start < 0 or end < 0:
        return []

    json_str = raw[start:end + 1]
    try:
        arr = json.loads(json_str)
        if isinstance(arr, list):
            return arr
    except json.JSONDecodeError:
        pass

    # 退一步：尝试逐行修复
    return []


# ══════════════════════════════════════════════
# 校验阶段
# ══════════════════════════════════════════════

AUDIT_SYSTEM_PROMPT = """你是数据审计 AI，专精浙江省服务商数据。

任务：对一条会议纪要数据声明，调用工具查 DB 实际值，与预期值对比，输出审计结论。

【你拥有的工具】（同 AI 数据助手）
lookup_provider, get_provider_profile, query_online_records, query_visit_records,
query_redpack_records, top_n, compare_periods, is_fake_provider, generic_sql, ...

【关键业务概念】
- 「服务商等级」按累计上线金额：≥¥1k=已激活, ¥1k-1w=v2, ¥1w-3w=v3, ≥¥3w=v4
- 「上线」= 设备激活扫码（产品现有分销价累加）
- 「假商」分 4 类：马甲/伞形/假签约/套上线

【工作流程】
1. 理解声明的 metric / filters / expected_value
2. 选合适的工具查 DB（必要时多次调用）
3. 拿到实际值后，用以下严格 JSON 格式返回结论：

{
  "actual_value": 数字(实际查到的值),
  "actual_unit": "家 / 台 / 元 / 次",
  "diff_pct": 数字(百分比，正=实际更高，负=实际更低),
  "status": "一致 | 偏差 | 严重不符 | 无法验证 | 口径差异",
  "reason": "一句话解释（如有偏差，说明可能原因）",
  "tools_used": ["工具1", "工具2"]
}

【状态判定规则】
- ✅ 一致：实际值 vs 预期值，相对误差 ≤ 5%
- ⚠️ 偏差：相对误差 5%-20%
- 🚨 严重不符：相对误差 > 20%
- ❓ 无法验证：工具查不到 / 需要用户提供更多信息
- 📅 口径差异：值差异是因为时点/统计口径不同（如纪要用"全月"DB 截止月中）

【禁止】
- 不要编造数字
- 不要给出 JSON 之外的内容
- 不要超过 5 轮工具调用"""


def build_audit_user_prompt(claim: Dict[str, Any]) -> str:
    """构造校验用户 prompt"""
    filters_str = json.dumps(claim.get('filters') or {}, ensure_ascii=False)
    return f"""请验证下面这条数据声明：

【声明】 {claim.get('claim', '')}
【指标类型】 {claim.get('metric_type', '')}
【过滤条件】 {filters_str}
【预期值】 {claim.get('expected_value', '')} {claim.get('expected_unit', '')}
【建议验证策略】 {claim.get('verify_strategy', '')}

请调工具查 DB 实际值，并按要求 JSON 格式返回结论。"""


def audit_claim(claim: Dict[str, Any]) -> Dict[str, Any]:
    """对单条声明运行 mini-Agent 验证。返回审计结论 dict

    复用 AI 代码助手的 Anthropic 配置（不再走 OpenAI 兼容协议）
    """
    user_prompt = build_audit_user_prompt(claim)

    result = call_agent_with_tools(
        system=AUDIT_SYSTEM_PROMPT,
        user_question=user_prompt,
        openai_tools=get_tool_schemas(),
        execute_tool_fn=execute_tool,
        max_rounds=5,
    )

    # 解析 AI 返回的 JSON
    answer = result.get('answer', '')
    parsed = _parse_audit_json(answer)

    return {
        'claim_id': claim.get('id'),
        'claim_text': claim.get('claim'),
        'expected_value': claim.get('expected_value'),
        'expected_unit': claim.get('expected_unit'),
        'actual_value': parsed.get('actual_value'),
        'actual_unit': parsed.get('actual_unit'),
        'diff_pct': parsed.get('diff_pct'),
        'status': parsed.get('status', '❓ 无法验证'),
        'reason': parsed.get('reason', ''),
        'tools_used': parsed.get('tools_used', [c['name'] for c in result.get('tool_calls', [])]),
        'tool_calls': result.get('tool_calls', []),
        'raw_answer': answer,
        'rounds': result.get('rounds', 0),
        'error': result.get('error'),
    }


def _parse_audit_json(raw: str) -> Dict[str, Any]:
    """从 LLM 返回的文本中提取审计 JSON 对象"""
    raw = raw.strip()
    if raw.startswith('```'):
        m = re.search(r'```(?:json)?\s*(.+?)```', raw, flags=re.DOTALL)
        if m:
            raw = m.group(1).strip()

    start = raw.find('{')
    end = raw.rfind('}')
    if start < 0 or end < 0:
        return {}

    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return {}


def normalize_status(status: str) -> str:
    """规范化 LLM 返回的 status 字符串（增加 emoji 前缀）"""
    if not status:
        return '❓ 无法验证'
    s = status.strip()
    # 已经带 emoji
    if any(s.startswith(e) for e in ['✅', '⚠️', '🚨', '❓', '📅']):
        return s
    # 文字 → emoji
    mapping = {
        '一致': '✅ 一致',
        '偏差': '⚠️ 偏差',
        '严重不符': '🚨 严重不符',
        '严重': '🚨 严重不符',
        '无法验证': '❓ 无法验证',
        '无法': '❓ 无法验证',
        '口径差异': '📅 口径差异',
        '口径': '📅 口径差异',
    }
    for k, v in mapping.items():
        if k in s:
            return v
    return f'❓ {s}'
