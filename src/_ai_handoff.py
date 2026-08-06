"""V2 → V3 上下文衔接工具

用户从 V2 标准报表 page 看到结果，想就当前筛选/数据追问 → 用这个组件传递上下文给 page 09
（V3 AI 代码助手），AI 能直接基于现有筛选条件继续分析。

用法（在 V2 page 里）：

    from _ai_handoff import render_ai_followup_button

    # 用户筛选好后，在表格/图表底部：
    render_ai_followup_button(
        source_page='02·🧭 服务商智能分类',
        context_summary=(
            '当前 RFM 分群结果：🏆 核心活跃 50 家 / 🌱 培育客户 120 家 ...\\n'
            '筛选条件：地市=杭州市，时间窗=12 个月\\n'
        ),
        data_snapshot=df_top10.to_markdown(index=False) if not df_top10.empty else None,
        suggested_followups=[
            '🏆 核心活跃客户里宁波占多少？',
            '画一张核心活跃客户的上线频次月度趋势图',
            '对比核心活跃和培育客户的产品系列偏好',
        ],
    )

page 09 会自动检测 st.session_state['ca_handoff_context']，作为 prompt 前缀传给 Code Agent。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import streamlit as st


HANDOFF_KEY = 'ca_handoff_context'


def render_ai_followup_button(
    *,
    source_page: str,
    context_summary: str,
    data_snapshot: Optional[str] = None,
    suggested_followups: Optional[list[str]] = None,
    button_label: str = '🛠️ 在 AI 代码助手里追问',
    help_text: str = '把当前筛选条件和数据带过去，让 AI 基于这些做个性化分析',
    key_suffix: str = '',
):
    """在 V2 page 渲染一个「跳转到 V3 + 携带当前上下文」按钮

    Args:
        source_page: 来源 page 名（用于 AI 知道用户从哪来）
        context_summary: 必填 — 一段说明当前筛选条件和已知结果的 markdown
        data_snapshot: 可选 — 数据快照（如 top10 表格的 markdown），让 AI 不用重查
        suggested_followups: 可选 — 推荐的后续问题列表，给用户选
        button_label: 按钮文字
        help_text: 鼠标悬停提示
        key_suffix: 同 page 多个按钮时区分用
    """
    btn_key = f'_ai_handoff_{source_page}_{key_suffix}'

    if st.button(button_label, help=help_text, key=btn_key, use_container_width=False):
        ctx = {
            'source_page': source_page,
            'context_summary': context_summary,
            'data_snapshot': data_snapshot,
            'suggested_followups': suggested_followups or [],
            'created_at': datetime.now().isoformat(timespec='seconds'),
        }
        st.session_state[HANDOFF_KEY] = ctx
        # 同时清掉 page 09 上一次会话状态，让用户进入一个新 thread
        st.session_state.pop('ca_question', None)
        st.session_state.pop('ca_question_input', None)
        st.session_state.pop('ca_claude_session_id', None)
        st.session_state['ca_history'] = []
        # 跳转到 page 09
        try:
            st.switch_page('pages/09·🛠️ AI 代码助手.py')
        except Exception:
            # 老版 streamlit 不支持 switch_page，给提示
            st.success(
                '上下文已保存。请左侧栏切到 "09·🛠️ AI 代码助手"，'
                '会自动看到这次的上下文。'
            )


def consume_handoff_context() -> Optional[dict]:
    """page 09 启动时调：取出（不消费）当前的 handoff context。

    Returns:
        dict 或 None。dict 含 source_page / context_summary / data_snapshot /
        suggested_followups / created_at。
    """
    return st.session_state.get(HANDOFF_KEY)


def clear_handoff_context():
    """显式清掉 context（用户点了"忘掉这次的上下文"按钮时）"""
    st.session_state.pop(HANDOFF_KEY, None)


def build_prompt_prefix(ctx: dict) -> str:
    """把 handoff context 编成 markdown 前缀，拼到用户输入前一起传给 AI"""
    if not ctx:
        return ''

    parts = [
        '【上下文：来自 V2 标准报表】',
        f"用户来自 page：{ctx['source_page']}",
        '',
        '## 当前筛选条件 / 已知结果',
        ctx.get('context_summary', '').rstrip(),
    ]

    snap = ctx.get('data_snapshot')
    if snap:
        parts.extend([
            '',
            '## 数据快照（V2 报表已经算出来了，无需重查）',
            snap.rstrip(),
        ])

    parts.extend([
        '',
        '请基于以上上下文回答下面的追问。如果数字跟你查 DB 算的对不上，先信 V2 的（那是公司标准口径）。',
        '',
        '## 用户的追问',
        '',
    ])

    return '\n'.join(parts)


def render_handoff_banner_in_page09():
    """page 09 顶部渲染上下文 banner（如果有）"""
    ctx = consume_handoff_context()
    if not ctx:
        return

    with st.container(border=True):
        cols = st.columns([10, 1])
        with cols[0]:
            st.markdown(f"### 📨 来自 **{ctx['source_page']}** 的上下文")
            st.caption(f"创建于 {ctx['created_at']}")
            with st.expander("查看完整上下文", expanded=False):
                st.markdown(ctx.get('context_summary', ''))
                if ctx.get('data_snapshot'):
                    st.markdown('**数据快照：**')
                    st.markdown(ctx['data_snapshot'])
        with cols[1]:
            if st.button('✕', help='忘掉这次的上下文', key='_clear_handoff'):
                clear_handoff_context()
                st.rerun()

        # 推荐问题（点了直接填到输入框）
        followups = ctx.get('suggested_followups') or []
        if followups:
            st.markdown('**💡 你可能想问：**')
            for i, q in enumerate(followups):
                if st.button(q, key=f'_handoff_fu_{i}', use_container_width=True):
                    st.session_state['ca_question'] = q
                    st.rerun()
