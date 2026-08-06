"""LLM 统一调用接口

支持的提供商（都兼容 OpenAI 协议）：
- Kimi（Moonshot AI）
- 通义千问（阿里 DashScope）
- 豆包（字节 ARK）

配置持久化到 ~/.so_data_analytics/llm_config.json
"""

import json
import os
from pathlib import Path
from typing import Iterator, Optional, Tuple, Union

import streamlit as st

# ─── 配置文件路径 ───────────────────────────
CONFIG_DIR = Path.home() / '.so_data_analytics'
CONFIG_PATH = CONFIG_DIR / 'llm_config.json'


# ─── 提供商定义 ─────────────────────────────
PROVIDERS = {
    'kimi': {
        'label': '🌙 Kimi（Moonshot）',
        'base_url': 'https://api.moonshot.cn/v1',
        'models': [
            'kimi-k2-0905-preview',
            'moonshot-v1-128k',
            'moonshot-v1-32k',
            'moonshot-v1-8k',
        ],
        'default_model': 'moonshot-v1-32k',
        'apply_url': 'https://platform.moonshot.cn/console/api-keys',
        'note': '默认 1 元 = 5 万 tokens（moonshot-v1-32k）',
    },
    'qwen': {
        'label': '☁️ 通义千问（阿里 DashScope）',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'models': [
            'qwen-max',
            'qwen-plus',
            'qwen-turbo',
            'qwen-long',
        ],
        'default_model': 'qwen-plus',
        'apply_url': 'https://dashscope.console.aliyun.com/apiKey',
        'note': 'qwen-plus 兼顾成本和效果',
    },
    'doubao': {
        'label': '🐳 豆包（字节 ARK）',
        'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
        'models': [
            # 这些是 endpoint id 占位，实际需要去火山方舟控制台创建接入点
            'doubao-1.5-pro-32k-250115',
            'doubao-1.5-pro-256k-250115',
            'doubao-pro-32k-241215',
        ],
        'default_model': 'doubao-1.5-pro-32k-250115',
        'apply_url': 'https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey',
        'note': '需先在火山方舟创建接入点，模型 ID 填接入点 endpoint',
    },
}


# ─── 配置读写 ───────────────────────────────
def load_config() -> dict:
    """加载用户 LLM 配置。返回 {active_provider, providers: {provider_id: {api_key, model}}}"""
    if not CONFIG_PATH.exists():
        return {
            'active_provider': 'kimi',
            'providers': {},
        }
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {'active_provider': 'kimi', 'providers': {}}


def save_config(config: dict):
    """保存配置"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    # 设置文件权限：只有用户自己可读（保护 API Key）
    os.chmod(CONFIG_PATH, 0o600)


def get_active_config() -> Optional[dict]:
    """返回当前激活的提供商配置 dict（含 base_url, api_key, model）。
    若未配置或缺 API Key，返回 None。
    """
    cfg = load_config()
    pid = cfg.get('active_provider', 'kimi')
    p_cfg = cfg.get('providers', {}).get(pid, {})
    if not p_cfg.get('api_key'):
        return None
    provider_def = PROVIDERS.get(pid)
    if not provider_def:
        return None
    return {
        'provider_id': pid,
        'provider_label': provider_def['label'],
        'base_url': provider_def['base_url'],
        'api_key': p_cfg['api_key'],
        'model': p_cfg.get('model') or provider_def['default_model'],
    }


def is_configured() -> bool:
    """是否已配置可用的 LLM"""
    return get_active_config() is not None


# ─── 调用接口 ───────────────────────────────
def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: Optional[float] = None,
    max_tokens: int = 2000,
    stream: bool = False,
) -> Union[str, Iterator[str]]:
    """同步调用 LLM。

    Args:
        system_prompt: 系统角色提示词
        user_prompt: 用户输入（含数据上下文）
        temperature: None = 用模型 API 默认值（推荐，避免 K2 等模型只允许特定值）
                     传具体数字会强制覆盖
        max_tokens: 最大输出 tokens
        stream: 是否流式

    Returns:
        非 stream → 完整字符串
        stream → 字符串迭代器（每次 yield 增量字符）

    Raises:
        RuntimeError: 未配置或调用失败
    """
    cfg = get_active_config()
    if not cfg:
        raise RuntimeError(
            "尚未配置 LLM。请前往侧栏 '🤖 AI 配置' 页面填写 API Key。"
        )

    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai SDK 未安装，请运行 pip install openai")

    client = OpenAI(api_key=cfg['api_key'], base_url=cfg['base_url'])

    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ]

    # 不传 temperature 让 API 用默认（K2 等模型只允许 1，传 0.3 会 400）
    base_kwargs = {
        'model': cfg['model'],
        'messages': messages,
        'max_tokens': max_tokens,
    }
    if temperature is not None:
        base_kwargs['temperature'] = temperature

    if stream:
        def _gen():
            try:
                resp = client.chat.completions.create(stream=True, **base_kwargs)
                for chunk in resp:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
            except Exception as e:
                yield f"\n\n❌ 调用失败：{e}"
        return _gen()

    try:
        resp = client.chat.completions.create(**base_kwargs)
        return resp.choices[0].message.content or ''
    except Exception as e:
        raise RuntimeError(f"LLM 调用失败：{e}")


def test_connection(provider_id: str, api_key: str, model: str) -> Tuple[bool, str]:
    """测试 API Key 是否有效。返回 (是否成功, 错误信息或响应预览)
    不传 temperature/max_tokens 之外的可选参数，让 API 用默认（兼容 K2 等限制特定 temperature 的模型）
    """
    provider_def = PROVIDERS.get(provider_id)
    if not provider_def:
        return False, f"未知提供商：{provider_id}"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=provider_def['base_url'])
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': '你是一个测试助手。'},
                {'role': 'user', 'content': '请用一句话确认你能正常响应。'},
            ],
            max_tokens=80,
        )
        return True, (resp.choices[0].message.content or '响应为空').strip()
    except Exception as e:
        return False, str(e)


# ─── Agent 模式（Function Calling 循环）────────────────

AGENT_SYSTEM_PROMPT = """你是「SO 数据分析平台」的 AI 数据助手，专精浙江省服务商管理数据分析。

【你的能力】
你可以调用一组工具查 SQLite 数据库，包括：
  - 查服务商信息 / 上线记录 / 拜访 / 红包
  - Top N 排行（服务商/代理商/区县/产品/业务员）
  - 时间段对比
  - 假商识别
  - 兜底的 SQL 查询

【关键业务概念】
- 「服务商等级」按累计上线金额（产品现有分销价之和）：≥¥1k=已激活, ¥1k-1w=v2, ¥1w-3w=v3, ≥¥3w=v4
- 「中奖金额」是抽奖中奖（自愿），不影响等级
- 「上线」= 服务商激活设备时扫码，每台货值累加
- 「假商」分 4 类：🎭马甲(已确认) / ☂️伞形 / ❌假签约(签约60+天但上线≤2台) / 🔄套上线
- 「打卡方」: 🏢 大华业务员 / 🏪 代理商业务员（用 _打卡方 字段区分）
- 数据范围：仅浙江省

【工作流程】
1. 用户问问题 → 你拆解为需要哪些数据
2. **优先用专用工具**（lookup_provider / get_provider_profile / query_*）
3. 拿到数据后整合分析，给中文答案
4. **必须引用具体数字**，不要"加强管理"这种空话
5. 复杂查询不到现成工具时，先用 describe_schema 看表结构，再用 generic_sql

【输出风格】
- 简洁直接，结论先行
- 中文，避免英文术语
- 用 markdown 格式（合适时用表格）
- 数据驱动 — 每个结论都引用具体数字
- 如果信息不足，明确告诉用户需要补充什么

【禁止事项】
- 不要瞎编数据 — 只说工具返回的数字
- 不要执行写操作 SQL（会被拒绝）
- 不要泄露 API Key 等敏感信息"""


def call_agent(
    user_question: str,
    history: list = None,
    *,
    max_rounds: int = 5,
    on_tool_call=None,
    on_assistant_chunk=None,
):
    """运行 Agent 循环。

    Args:
        user_question: 用户当前问题
        history: 历史对话 [{role, content}]，用于多轮对话
        max_rounds: 最多工具调用轮数（防死循环）
        on_tool_call: 回调 callable(name, args, result)，在 streamlit 实时渲染
        on_assistant_chunk: 回调 callable(text)，流式输出最终答案

    Returns:
        {
            'answer': str,           # 最终答案
            'tool_calls': list,      # [{name, args, result}, ...]
            'rounds': int,
            'error': str | None,
        }
    """
    from _llm_tools import get_tool_schemas, execute_tool

    cfg = get_active_config()
    if not cfg:
        return {
            'answer': '尚未配置 LLM。请到「06·🤖 AI 配置」填 API Key。',
            'tool_calls': [], 'rounds': 0, 'error': 'not_configured',
        }

    try:
        from openai import OpenAI
    except ImportError:
        return {'answer': 'openai SDK 未安装', 'tool_calls': [], 'rounds': 0,
                'error': 'sdk_missing'}

    client = OpenAI(api_key=cfg['api_key'], base_url=cfg['base_url'])

    # 构造消息历史
    messages = [{'role': 'system', 'content': AGENT_SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({'role': 'user', 'content': user_question})

    tools = get_tool_schemas()
    tool_calls_log = []

    for round_idx in range(max_rounds):
        try:
            resp = client.chat.completions.create(
                model=cfg['model'],
                messages=messages,
                tools=tools,
                tool_choice='auto',
            )
        except Exception as e:
            return {
                'answer': f'LLM 调用失败：{e}',
                'tool_calls': tool_calls_log,
                'rounds': round_idx,
                'error': str(e),
            }

        msg = resp.choices[0].message

        # 没有工具调用 = AI 直接给答案
        if not msg.tool_calls:
            answer = msg.content or '（AI 没有给出回答）'
            if on_assistant_chunk:
                on_assistant_chunk(answer)
            return {
                'answer': answer,
                'tool_calls': tool_calls_log,
                'rounds': round_idx + 1,
                'error': None,
            }

        # 有工具调用 — 把 assistant message 加入历史（含 tool_calls）
        asst_msg = {
            'role': 'assistant',
            'content': msg.content,
            'tool_calls': [
                {
                    'id': tc.id,
                    'type': 'function',
                    'function': {
                        'name': tc.function.name,
                        'arguments': tc.function.arguments,
                    },
                } for tc in msg.tool_calls
            ],
        }
        # Kimi K2 等带 thinking 的模型要求 assistant message 保留 reasoning_content
        # 否则下一轮调用时会报 'thinking is enabled but reasoning_content is missing'
        if hasattr(msg, 'reasoning_content') and msg.reasoning_content:
            asst_msg['reasoning_content'] = msg.reasoning_content
        # 兼容某些 SDK 用 model_extra 存自定义字段
        if hasattr(msg, 'model_extra') and msg.model_extra:
            rc = msg.model_extra.get('reasoning_content')
            if rc and 'reasoning_content' not in asst_msg:
                asst_msg['reasoning_content'] = rc
        messages.append(asst_msg)

        # 执行每个工具调用
        for tc in msg.tool_calls:
            import json as _json
            try:
                args = _json.loads(tc.function.arguments) if tc.function.arguments else {}
            except Exception:
                args = {}
            result = execute_tool(tc.function.name, args)

            log_entry = {'name': tc.function.name, 'args': args, 'result': result}
            tool_calls_log.append(log_entry)
            if on_tool_call:
                on_tool_call(tc.function.name, args, result)

            messages.append({
                'role': 'tool',
                'tool_call_id': tc.id,
                'content': _json.dumps(result, ensure_ascii=False, default=str),
            })

    # 超过 max_rounds — 强制让 AI 用现有信息答
    messages.append({
        'role': 'user',
        'content': f'已经达到工具调用上限 {max_rounds} 次，请用已有信息直接给出答案。',
    })
    try:
        resp = client.chat.completions.create(
            model=cfg['model'],
            messages=messages,
        )
        final = resp.choices[0].message.content or '（达到工具调用上限，无法给出答案）'
    except Exception as e:
        final = f'达到上限且最终调用失败：{e}'

    if on_assistant_chunk:
        on_assistant_chunk(final)
    return {
        'answer': final,
        'tool_calls': tool_calls_log,
        'rounds': max_rounds,
        'error': 'max_rounds_reached',
    }
