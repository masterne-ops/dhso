"""Anthropic Messages API 直连客户端

会议纪要校验等功能复用 AI 代码助手的 LLM 配置（`anthropic_config.json`）。
不引入 anthropic SDK，用 requests 直连，避免新增依赖。

支持：
  - 一轮 Messages 调用（用于 extract_claims）
  - 多轮 tool use 循环（用于 audit_claim 的工具调用）
"""
from __future__ import annotations

import json
import os
from typing import Optional

import requests

from _code_agent import load_config as _load_anthropic_cfg


def is_configured() -> bool:
    cfg = _load_anthropic_cfg()
    return bool(cfg.get('base_url') and cfg.get('auth_token') and cfg.get('model'))


def get_config() -> dict:
    cfg = _load_anthropic_cfg()
    return {
        'base_url': cfg.get('base_url', '').rstrip('/'),
        'auth_token': cfg.get('auth_token', ''),
        'model': cfg.get('model', ''),
    }


def _endpoint(base_url: str) -> str:
    """大部分 Anthropic-compat 端点是 {base_url}/v1/messages"""
    base = base_url.rstrip('/')
    if base.endswith('/v1'):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _headers(auth_token: str) -> dict:
    return {
        'Content-Type': 'application/json',
        'x-api-key': auth_token,
        'authorization': f'Bearer {auth_token}',  # 双保险，部分端点读 Authorization
        'anthropic-version': '2023-06-01',
    }


def call_messages(
    *,
    system: str,
    messages: list,
    tools: list = None,
    max_tokens: int = 4096,
    temperature: Optional[float] = None,
    timeout: int = 120,
) -> dict:
    """单次 Messages API 调用，返回原始 response.json()

    messages: [{role: 'user'|'assistant', content: str | list of blocks}]
    tools: Anthropic 格式 [{name, description, input_schema}, ...]
    """
    cfg = get_config()
    if not cfg['base_url']:
        raise RuntimeError("AI 代码助手未配置（anthropic_config.json 缺失），请先到「AI 代码助手」侧栏配 Base URL / Token / Model")

    body: dict = {
        'model': cfg['model'],
        'max_tokens': max_tokens,
        'system': system,
        'messages': messages,
    }
    if tools:
        body['tools'] = tools
    if temperature is not None:
        body['temperature'] = temperature

    url = _endpoint(cfg['base_url'])
    r = requests.post(url, headers=_headers(cfg['auth_token']),
                      data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                      timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Anthropic API {r.status_code}: {r.text[:500]}")
    return r.json()


def extract_text(response: dict) -> str:
    """从 response.content 里抽出所有 text block 拼起来"""
    parts = []
    for block in response.get('content', []):
        if block.get('type') == 'text':
            parts.append(block.get('text', ''))
    return ''.join(parts)


def extract_tool_uses(response: dict) -> list:
    """从 response.content 抽出 tool_use blocks"""
    return [b for b in response.get('content', []) if b.get('type') == 'tool_use']


# ══════════════════════════════════════════════
# 工具协议转换（OpenAI Function → Anthropic Tool）
# ══════════════════════════════════════════════

def openai_tools_to_anthropic(openai_tools: list) -> list:
    """OpenAI 格式工具描述转 Anthropic 格式

    OpenAI:  [{type:'function', function:{name, description, parameters}}]
    Anthropic: [{name, description, input_schema}]
    """
    out = []
    for t in openai_tools or []:
        if t.get('type') == 'function' and 'function' in t:
            f = t['function']
            out.append({
                'name': f['name'],
                'description': f.get('description', ''),
                'input_schema': f.get('parameters', {'type': 'object', 'properties': {}}),
            })
        elif 'name' in t and 'input_schema' in t:
            # 已经是 Anthropic 格式
            out.append(t)
    return out


# ══════════════════════════════════════════════
# 多轮 tool use 循环（替代 _llm.call_agent）
# ══════════════════════════════════════════════

def call_agent_with_tools(
    *,
    system: str,
    user_question: str,
    history: list = None,             # [{role, content}]
    openai_tools: list = None,        # OpenAI 格式工具（自动转 Anthropic）
    execute_tool_fn=None,             # callable(name, args) → result（任意可 JSON 化）
    max_rounds: int = 5,
    max_tokens: int = 4096,
    on_tool_call=None,                # callable(name, args, result)
) -> dict:
    """模仿 _llm.call_agent 接口，底层走 Anthropic 协议。

    Returns:
        {
          'answer': str,
          'tool_calls': [{name, args, result}, ...],
          'rounds': int,
          'error': str|None,
        }
    """
    tools = openai_tools_to_anthropic(openai_tools or [])

    # 拼 messages history（不含 system，system 单独传）
    messages: list = []
    for h in history or []:
        if h.get('role') == 'system':
            continue   # system 已传，跳过
        messages.append({'role': h['role'], 'content': h.get('content', '')})
    messages.append({'role': 'user', 'content': user_question})

    tool_calls_log = []
    final_text = ''

    for round_idx in range(max_rounds):
        try:
            resp = call_messages(
                system=system, messages=messages,
                tools=tools if tools else None,
                max_tokens=max_tokens,
            )
        except Exception as e:
            return {'answer': '', 'tool_calls': tool_calls_log,
                    'rounds': round_idx, 'error': str(e)}

        stop_reason = resp.get('stop_reason', '')
        tool_uses = extract_tool_uses(resp)
        text_part = extract_text(resp)

        # 把 assistant 的回复加进 messages（必须包含 tool_use blocks）
        messages.append({'role': 'assistant', 'content': resp.get('content', [])})

        if not tool_uses or stop_reason != 'tool_use':
            final_text = text_part
            return {'answer': final_text, 'tool_calls': tool_calls_log,
                    'rounds': round_idx + 1, 'error': None}

        # 执行所有 tool_use 并构造 tool_result 回传
        tool_results = []
        for tu in tool_uses:
            name = tu.get('name', '')
            args = tu.get('input', {})
            tid = tu.get('id', '')
            if execute_tool_fn:
                try:
                    result = execute_tool_fn(name, args)
                except Exception as e:
                    result = {'error': str(e)}
            else:
                result = {'error': 'no execute_tool_fn given'}

            tool_calls_log.append({'name': name, 'args': args, 'result': result})
            if on_tool_call:
                try:
                    on_tool_call(name, args, result)
                except Exception:
                    pass

            tool_results.append({
                'type': 'tool_result',
                'tool_use_id': tid,
                'content': json.dumps(result, ensure_ascii=False, default=str),
            })

        messages.append({'role': 'user', 'content': tool_results})

    return {'answer': final_text, 'tool_calls': tool_calls_log,
            'rounds': max_rounds, 'error': 'max_rounds_exhausted'}
