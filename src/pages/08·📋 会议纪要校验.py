#!/usr/bin/env python3
"""📋 会议纪要校验

工作流：
1. 上传 docx/xlsx 或 直接粘贴
2. AI 抽取「可验证的数据声明」清单
3. 用户勾选要校验的条目
4. AI 对每条声明调工具查 DB → 对比 → 判定状态
5. 显示对比报告 + 导出
6. 用户基于纪要继续追问（复用 AI 助手）
"""

import io
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _anthropic_client import is_configured, get_config, call_agent_with_tools  # noqa: E402
from _llm_tools import get_tool_schemas, execute_tool  # noqa: E402
from _meeting_parser import parse_uploaded  # noqa: E402
from _meeting_audit import (  # noqa: E402
    extract_claims, audit_claim, normalize_status,
    AUDIT_SYSTEM_PROMPT,
)
from _ai_log import log_audit, read_audits  # noqa: E402

require_auth()

# AI 功能仅 admin 可用
from _auth import is_admin, current_user, get_current_role  # noqa: E402
if not is_admin():
    st.error(
        f"⛔ AI 功能（会议纪要校验）仅 admin 可用。"
        f"当前用户: `{current_user()}`，角色: `{get_current_role()}`"
    )
    st.stop()

st.markdown("### 📋 会议纪要校验")
st.caption(
    "上传/粘贴会议纪要 → AI 抽取核心数据声明 → 调工具核对 DB → 标记一致/偏差/严重不符。"
    "**支持 .docx 和 .xlsx 格式。**"
)

# ── LLM 配置检查（复用 AI 代码助手的 Anthropic 配置）──
if not is_configured():
    st.warning("⚠️ 尚未配置 LLM。请到侧栏 **「AI 代码助手」** 填 Base URL / Token / Model。")
    st.stop()

cfg = get_config()
st.success(f"✅ 当前 LLM（复用 AI 代码助手）：{cfg['base_url']} · 模型 `{cfg['model']}`")


# ── Session 状态 ──
def _reset():
    for k in ['mt_text', 'mt_filename', 'mt_meeting_date',
              'mt_claims', 'mt_audit_results', 'mt_audit_id',
              'mt_chat_history']:
        st.session_state.pop(k, None)


if 'mt_audit_id' not in st.session_state:
    st.session_state['mt_audit_id'] = uuid.uuid4().hex[:12]


st.divider()

# ══════════════════════════════════════════════════
# Step 1：上传纪要
# ══════════════════════════════════════════════════
st.markdown("#### Step 1：上传会议纪要")

src_col1, src_col2 = st.columns([1, 1])

with src_col1:
    upl = st.file_uploader(
        "上传文件（.docx / .xlsx）",
        type=['docx', 'xlsx', 'xlsm'],
        key='mt_uploader',
    )
    if upl is not None:
        try:
            text = parse_uploaded(upl)
            st.session_state['mt_text'] = text
            st.session_state['mt_filename'] = upl.name
            st.success(f"✅ 已读取 {upl.name}（{len(text):,} 字符）")
        except Exception as e:
            st.error(f"解析失败：{e}")

with src_col2:
    pasted = st.text_area(
        "或者直接粘贴文本",
        height=160,
        key='mt_paste',
        placeholder="把会议纪要内容粘贴到这里...",
    )
    if st.button("使用粘贴内容", disabled=not pasted.strip()):
        st.session_state['mt_text'] = pasted.strip()
        st.session_state['mt_filename'] = '直接粘贴'
        st.success(f"✅ 已采纳粘贴内容（{len(pasted):,} 字符）")

# 有文本则显示
if 'mt_text' in st.session_state:
    text = st.session_state['mt_text']

    # 会议日期（用于解析"本月"等相对时间）
    md_col1, md_col2 = st.columns([1, 3])
    with md_col1:
        meeting_date = st.text_input(
            "会议日期 YYYY-MM-DD",
            value=st.session_state.get('mt_meeting_date',
                                          datetime.now().strftime('%Y-%m-%d')),
            key='mt_meeting_date_input',
            help='AI 用此日期解析"本月"/"上月"等相对时间',
        )
        st.session_state['mt_meeting_date'] = meeting_date

    with st.expander(f"📄 纪要原文预览（{len(text):,} 字符，可展开）", expanded=False):
        st.text(text[:5000] + ('\n... (已截断)' if len(text) > 5000 else ''))

    if st.button("🗑️ 清空 重新开始", key="mt_reset"):
        _reset()
        st.rerun()


# ══════════════════════════════════════════════════
# Step 2：AI 抽取数据声明
# ══════════════════════════════════════════════════
if 'mt_text' in st.session_state:
    st.divider()
    st.markdown("#### Step 2：AI 抽取核心数据声明")

    if 'mt_claims' not in st.session_state:
        if st.button("🔍 开始抽取（约 10-30 秒）", type='primary', key='mt_extract_btn'):
            with st.spinner("AI 正在阅读会议纪要并抽取数据声明..."):
                try:
                    t0 = time.time()
                    claims = extract_claims(
                        st.session_state['mt_text'],
                        meeting_date=st.session_state.get('mt_meeting_date'),
                    )
                    elapsed = time.time() - t0
                    if not claims:
                        st.warning("AI 没找到可验证的数据声明。请检查纪要内容或换更详细的纪要。")
                    else:
                        st.session_state['mt_claims'] = claims
                        st.success(f"✅ 抽取完成 · {len(claims)} 条数据声明 · 耗时 {elapsed:.1f}s")
                        st.rerun()
                except Exception as e:
                    st.error(f"抽取失败：{e}")
    else:
        claims = st.session_state['mt_claims']
        st.caption(f"已识别 **{len(claims)}** 条数据声明。请勾选要校验的条目（默认全选）。")

        # 默认全选 / 上次勾选
        sel_key = 'mt_claim_selection'
        if sel_key not in st.session_state:
            st.session_state[sel_key] = {c['id']: True for c in claims}

        # 表格 with 复选框
        for c in claims:
            cid = c['id']
            ck_col, txt_col = st.columns([0.5, 11])
            with ck_col:
                sel = st.checkbox(
                    '', key=f'mt_sel_{cid}',
                    value=st.session_state[sel_key].get(cid, True),
                    label_visibility='collapsed',
                )
                st.session_state[sel_key][cid] = sel
            with txt_col:
                ev = c.get('expected_value', '?')
                eu = c.get('expected_unit', '')
                metric = c.get('metric_type', '')
                ver = '✅可验证' if c.get('verifiable') else '❌不可验证'
                st.markdown(
                    f"**#{cid}** · {ver} · {metric} · 预期值: **{ev} {eu}**\n\n"
                    f"📌 {c.get('claim', '')}\n\n"
                    f"_验证策略：{c.get('verify_strategy', '—')}_"
                )

        # 按钮：全选 / 全不选 / 反选 / 开始校验
        bc = st.columns([1, 1, 1, 3])
        if bc[0].button("✅ 全选"):
            for c in claims:
                st.session_state[sel_key][c['id']] = True
            st.rerun()
        if bc[1].button("⬜ 全不选"):
            for c in claims:
                st.session_state[sel_key][c['id']] = False
            st.rerun()
        if bc[2].button("🔄 反选"):
            for c in claims:
                st.session_state[sel_key][c['id']] = not st.session_state[sel_key].get(c['id'], False)
            st.rerun()

        n_selected = sum(1 for v in st.session_state[sel_key].values() if v)
        # 偏差阈值（可调）
        th_cols = st.columns([1, 1, 4])
        with th_cols[0]:
            th_consistent = st.number_input(
                "一致阈值 ≤", value=5.0, min_value=0.0, max_value=100.0,
                key='mt_th_consistent',
                help="相对误差 ≤ 此值算 ✅ 一致",
            )
        with th_cols[1]:
            th_deviation = st.number_input(
                "偏差阈值 ≤", value=20.0, min_value=0.0, max_value=100.0,
                key='mt_th_deviation',
                help="相对误差 ≤ 此值算 ⚠️ 偏差，超过算 🚨 严重不符",
            )

        if st.button(
            f"🚀 校验选中的 {n_selected} 条声明",
            type='primary',
            disabled=(n_selected == 0),
            key='mt_audit_btn',
        ):
            sel_claims = [c for c in claims if st.session_state[sel_key].get(c['id'], False)]
            results = []
            t0 = time.time()
            progress = st.progress(0, text=f"开始校验 {len(sel_claims)} 条声明...")
            for i, c in enumerate(sel_claims, 1):
                progress.progress(
                    (i - 1) / len(sel_claims),
                    text=f"[{i}/{len(sel_claims)}] 校验：{c.get('claim', '')[:50]}",
                )
                try:
                    r = audit_claim(c)
                    # 应用用户阈值重新判定（如果 LLM 给的状态合理就保留）
                    if r.get('diff_pct') is not None:
                        abs_pct = abs(float(r['diff_pct']))
                        if abs_pct <= th_consistent:
                            r['status'] = '✅ 一致'
                        elif abs_pct <= th_deviation:
                            r['status'] = '⚠️ 偏差'
                        else:
                            r['status'] = '🚨 严重不符'
                    else:
                        r['status'] = normalize_status(r.get('status', ''))
                    results.append(r)
                except Exception as e:
                    results.append({
                        'claim_id': c['id'],
                        'claim_text': c.get('claim'),
                        'expected_value': c.get('expected_value'),
                        'actual_value': None,
                        'status': '❓ 无法验证',
                        'reason': f'校验异常：{e}',
                        'tool_calls': [],
                    })
            progress.progress(1.0, text=f"完成 · {len(results)} 条")
            duration_ms = int((time.time() - t0) * 1000)

            st.session_state['mt_audit_results'] = results

            # 写日志
            status_counts = {}
            for r in results:
                s = r.get('status', '')
                key = s.split(' ', 1)[1] if ' ' in s else s
                status_counts[key] = status_counts.get(key, 0) + 1

            try:
                log_audit(
                    audit_id=st.session_state['mt_audit_id'],
                    file_name=st.session_state.get('mt_filename', ''),
                    meeting_date=st.session_state.get('mt_meeting_date', ''),
                    raw_text=st.session_state['mt_text'],
                    n_claims_extracted=len(claims),
                    n_claims_audited=len(results),
                    status_counts=status_counts,
                    audit_results=results,
                    duration_ms=duration_ms,
                )
            except Exception as e:
                st.warning(f"日志写入失败：{e}")

            st.rerun()


# ══════════════════════════════════════════════════
# Step 3：对比报告
# ══════════════════════════════════════════════════
if 'mt_audit_results' in st.session_state:
    st.divider()
    st.markdown("#### Step 3：校验结果")

    results = st.session_state['mt_audit_results']

    # 状态统计
    n_consistent = sum(1 for r in results if '一致' in r.get('status', ''))
    n_deviation = sum(1 for r in results if '偏差' in r.get('status', ''))
    n_severe = sum(1 for r in results if '严重' in r.get('status', ''))
    n_unverif = sum(1 for r in results if '无法' in r.get('status', ''))
    n_caliber = sum(1 for r in results if '口径' in r.get('status', ''))

    sc = st.columns(5)
    sc[0].metric("✅ 一致", n_consistent)
    sc[1].metric("⚠️ 偏差", n_deviation)
    sc[2].metric("🚨 严重不符", n_severe, help="相对误差 > 20%")
    sc[3].metric("❓ 无法验证", n_unverif)
    sc[4].metric("📅 口径差异", n_caliber)

    if n_severe > 0:
        st.error(
            f"🚨 发现 **{n_severe}** 条严重不符的数据声明，建议立即核查（看下方红色行）"
        )

    # 报告表格
    rows = []
    for r in results:
        rows.append({
            '#': r.get('claim_id'),
            '声明': str(r.get('claim_text') or '')[:100],
            '预期值': f"{r.get('expected_value', '')} {r.get('expected_unit', '')}".strip(),
            '实际值': f"{r.get('actual_value', '—')} {r.get('actual_unit', '')}".strip(),
            '偏差%': f"{r['diff_pct']:+.1f}%" if r.get('diff_pct') is not None else '—',
            '状态': r.get('status', '❓'),
            '原因/解释': r.get('reason', ''),
            '调用工具': ', '.join(r.get('tools_used') or []),
        })
    rep_df = pd.DataFrame(rows)
    st.dataframe(rep_df, use_container_width=True, hide_index=True)

    # 导出
    exp_col1, exp_col2 = st.columns([1, 1])

    with exp_col1:
        # Excel 导出（含详细 tool calls）
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as w:
            rep_df.to_excel(w, sheet_name='校验报告', index=False)
            # 工具调用明细
            tc_rows = []
            for r in results:
                for tc in r.get('tool_calls', []):
                    tc_rows.append({
                        '#': r.get('claim_id'),
                        '声明': str(r.get('claim_text') or '')[:50],
                        '工具': tc.get('name'),
                        '参数': json.dumps(tc.get('args') or {}, ensure_ascii=False),
                        '返回': str(tc.get('result'))[:500],
                    })
            if tc_rows:
                pd.DataFrame(tc_rows).to_excel(w, sheet_name='工具调用明细', index=False)
        stamp = datetime.now().strftime('%Y%m%d_%H%M')
        st.download_button(
            "📥 导出 Excel（含工具调用明细）",
            data=buf.getvalue(),
            file_name=f'会议纪要校验_{stamp}.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            key='mt_dl_xlsx',
        )

    with exp_col2:
        # Word 导出（校验意见）
        try:
            from docx import Document
            doc = Document()
            doc.add_heading('会议纪要校验报告', level=0)
            doc.add_paragraph(
                f"会议日期：{st.session_state.get('mt_meeting_date', '—')} | "
                f"文件：{st.session_state.get('mt_filename', '—')} | "
                f"校验时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            doc.add_paragraph(
                f"汇总：✅ 一致 {n_consistent} · ⚠️ 偏差 {n_deviation} · "
                f"🚨 严重不符 {n_severe} · ❓ 无法验证 {n_unverif} · "
                f"📅 口径差异 {n_caliber}"
            )
            doc.add_heading('详细校验结果', level=1)

            t = doc.add_table(rows=1, cols=5)
            t.style = 'Light Grid Accent 1'
            hdr = t.rows[0].cells
            hdr[0].text = '#'
            hdr[1].text = '声明'
            hdr[2].text = '预期 → 实际'
            hdr[3].text = '状态'
            hdr[4].text = '原因'

            for r in results:
                row = t.add_row().cells
                row[0].text = str(r.get('claim_id', ''))
                row[1].text = str(r.get('claim_text') or '')[:200]
                row[2].text = (
                    f"{r.get('expected_value', '')} → {r.get('actual_value', '—')}"
                )
                row[3].text = r.get('status', '')
                row[4].text = str(r.get('reason') or '')[:300]

            buf2 = io.BytesIO()
            doc.save(buf2)
            st.download_button(
                "📥 导出 Word（校验意见书）",
                data=buf2.getvalue(),
                file_name=f'会议纪要校验意见_{stamp}.docx',
                mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                key='mt_dl_docx',
            )
        except Exception as e:
            st.caption(f"Word 导出未就绪：{e}")

    # 详细查看（每条展开看 LLM 的工具调用过程）
    with st.expander("🔍 查看 AI 校验过程（工具调用 + 原始返回）", expanded=False):
        for r in results:
            st.markdown(f"**#{r.get('claim_id')} · {r.get('status', '')}**")
            st.caption(r.get('claim_text', ''))
            st.caption(f"💬 AI 解释：{r.get('reason', '')}")
            tcs = r.get('tool_calls', [])
            if tcs:
                for i, tc in enumerate(tcs, 1):
                    st.code(
                        f"#{i} {tc.get('name')}({json.dumps(tc.get('args') or {}, ensure_ascii=False)})",
                        language='python',
                    )
            st.divider()


# ══════════════════════════════════════════════════
# Step 4：基于纪要追问
# ══════════════════════════════════════════════════
if 'mt_audit_results' in st.session_state:
    st.divider()
    st.markdown("#### Step 4：基于纪要追问")
    st.caption("可以追问任何关于上面校验结果的问题。AI 会带着纪要上下文回答。")

    if 'mt_chat_history' not in st.session_state:
        st.session_state['mt_chat_history'] = []

    # 历史
    for m in st.session_state['mt_chat_history']:
        role = '👤' if m['role'] == 'user' else '🤖'
        with st.chat_message(m['role'], avatar=role):
            st.markdown(m['content'])

    user_q = st.chat_input("基于纪要追问（如：为什么 #3 偏差这么大？）")

    # 推荐快捷追问
    rc = st.columns(3)
    quick_qs = [
        "把所有「严重不符」的声明详细分析一遍",
        "偏差最大的是哪条？为什么？",
        "有没有可能是数据时点对不齐？",
    ]
    quick_picked = None
    for i, q in enumerate(quick_qs):
        if rc[i].button(q, key=f'mt_qq_{i}', use_container_width=True):
            quick_picked = q
    if quick_picked:
        user_q = quick_picked

    if user_q:
        # 把校验结果作为上下文喂给 AI
        ctx_summary_lines = []
        for r in st.session_state['mt_audit_results'][:30]:
            ctx_summary_lines.append(
                f"#{r.get('claim_id')} · {r.get('status', '')} · "
                f"声明：{str(r.get('claim_text') or '')[:80]} · "
                f"预期：{r.get('expected_value', '')} → 实际：{r.get('actual_value', '—')} · "
                f"原因：{str(r.get('reason') or '')[:60]}"
            )
        context_msg = (
            f"以下是已校验的会议纪要数据声明：\n\n"
            + '\n'.join(ctx_summary_lines)
            + f"\n\n用户问：{user_q}"
        )

        st.session_state['mt_chat_history'].append({
            'role': 'user', 'content': user_q,
        })

        with st.chat_message('user', avatar='👤'):
            st.markdown(user_q)

        with st.chat_message('assistant', avatar='🤖'):
            with st.spinner("AI 正在分析..."):
                history = []
                # 加几轮历史（最多 4 轮）
                for m in st.session_state['mt_chat_history'][:-1][-4:]:
                    history.append(m)

                result = call_agent_with_tools(
                    system=AUDIT_SYSTEM_PROMPT,
                    user_question=context_msg,
                    history=history,
                    openai_tools=get_tool_schemas(),
                    execute_tool_fn=execute_tool,
                    max_rounds=4,
                )

            answer = result.get('answer', '')
            st.markdown(answer)
            n_tools = len(result.get('tool_calls', []))
            if n_tools > 0:
                st.caption(f"调用了 {n_tools} 次工具")

            st.session_state['mt_chat_history'].append({
                'role': 'assistant', 'content': answer,
            })

        st.rerun()


# ══════════════════════════════════════════════════
# 历史校验记录
# ══════════════════════════════════════════════════
st.divider()
with st.expander("📚 历史校验记录", expanded=False):
    audits = read_audits(limit=30)
    if audits.empty:
        st.info("暂无历史记录")
    else:
        show = audits[[
            'timestamp', 'file_name', 'meeting_date',
            'n_claims_extracted', 'n_claims_audited',
            'n_consistent', 'n_deviation', 'n_severe', 'n_inconclusive',
            'duration_ms',
        ]].copy()
        show['duration_ms'] = (show['duration_ms'] / 1000).round(1).astype(str) + 's'
        show.columns = ['时间', '文件', '会议日期', '抽取数', '校验数',
                         '✅', '⚠️', '🚨', '❓', '耗时']
        st.dataframe(show, use_container_width=True, hide_index=True)
