#!/usr/bin/env python3
"""🛠️ AI 代码助手 (V3 beta) — 沙箱里跑 Claude Code CLI

V3 beta 新增（vs alpha）：
  - 多轮对话（claude --resume）
  - 完整日志写 code_agent_log 表
  - 实时 stderr（不再死锁）
  - 用户反馈（👍/👎）
  - 历史会话可恢复
"""

import json
import sys
import time
import uuid
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import DB_PATH  # noqa: E402
from _code_agent import (  # noqa: E402
    DEFAULT_IMAGE,
    DEFAULT_TIMEOUT,
    docker_available,
    extract_result,
    extract_tool_result,
    extract_tool_use,
    image_exists,
    is_configured,
    load_config,
    run_agent,
    save_config,
)
from _ai_log import (  # noqa: E402
    code_agent_stats,
    read_code_agent_logs,
    update_code_agent_feedback,
)
from _ai_handoff import (  # noqa: E402
    build_prompt_prefix,
    consume_handoff_context,
    render_handoff_banner_in_page09,
)


require_auth()

# AI 功能仅 admin 可用
from _auth import is_admin, current_user, get_current_role  # noqa: E402
if not is_admin():
    st.error(
        f"⛔ AI 功能（AI 代码助手）仅 admin 可用。"
        f"当前用户: `{current_user()}`，角色: `{get_current_role()}`"
    )
    st.stop()

st.markdown("### 🛠️ AI 代码助手 (V3 beta)")
st.caption(
    "Docker 沙箱里跑 Claude Code CLI，AI 自己写 Python / SQL / 画图解你的问题。"
    "**支持多轮对话**，每个 ID 可继续追问。"
)

# ────────────────────────────────────────────────
# 路径常量
# ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent
SANDBOX_OUTPUT_ROOT = PROJECT_ROOT / 'v3' / 'sandbox-output'
SANDBOX_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────
# session_state 初始化
# ────────────────────────────────────────────────
if 'ca_ui_session_id' not in st.session_state:
    st.session_state['ca_ui_session_id'] = uuid.uuid4().hex[:12]
if 'ca_claude_session_id' not in st.session_state:
    st.session_state['ca_claude_session_id'] = None
if 'ca_history' not in st.session_state:
    # [{role, content, ...}, ...]，仅 UI 展示用
    st.session_state['ca_history'] = []


# ────────────────────────────────────────────────
# Sidebar：配置 + 环境检查
# ────────────────────────────────────────────────
with st.sidebar:
    st.markdown("#### ⚙️ Anthropic 端点")
    cfg = load_config()
    base_url = st.text_input("Base URL", value=cfg.get('base_url', ''),
                             placeholder="https://your-anthropic-compat.com")
    auth_token = st.text_input("Auth Token", value=cfg.get('auth_token', ''),
                               type='password', placeholder="sk-...")
    model = st.text_input("Model", value=cfg.get('model', ''),
                          placeholder="claude-sonnet-4 / 厂商映射模型名")
    small_model = st.text_input("Small Model（可选）", value=cfg.get('small_model', ''),
                                placeholder="留空 = 用上面同一个")

    if st.button("💾 保存配置", use_container_width=True):
        save_config({
            'base_url': base_url.strip(),
            'auth_token': auth_token.strip(),
            'model': model.strip(),
            'small_model': small_model.strip() or model.strip(),
        })
        st.success("已保存")
        st.rerun()

    st.markdown("---")
    st.markdown("#### 🩺 环境检查")

    docker_ok, docker_msg = docker_available()
    if docker_ok:
        st.success("✅ Docker daemon 就绪")
    else:
        st.error(f"❌ {docker_msg}")

    if docker_ok:
        if image_exists(DEFAULT_IMAGE):
            st.success(f"✅ 镜像 `{DEFAULT_IMAGE}` 已构建")
        else:
            st.warning(f"⚠️ 镜像 `{DEFAULT_IMAGE}` 未构建")
            st.code("cd v3 && ./build.sh", language="bash")

    if is_configured():
        st.success("✅ Anthropic 端点已配置")
    else:
        st.warning("⚠️ Anthropic 端点未配置")

    st.markdown("---")
    st.markdown("#### 💬 当前会话")
    cur_ui_sid = st.session_state['ca_ui_session_id']
    cur_claude_sid = st.session_state['ca_claude_session_id']
    n_turns = len(st.session_state['ca_history']) // 2  # 每轮一个 user + 一个 assistant
    st.caption(f"UI ID: `{cur_ui_sid}`")
    st.caption(f"Claude ID: `{cur_claude_sid or '（首轮未生成）'}`")
    st.caption(f"已对话: {n_turns} 轮")
    if st.button("🆕 新建会话（清空历史）", use_container_width=True):
        st.session_state['ca_ui_session_id'] = uuid.uuid4().hex[:12]
        st.session_state['ca_claude_session_id'] = None
        st.session_state['ca_history'] = []
        st.session_state.pop('ca_question', None)
        st.session_state.pop('ca_question_input', None)
        st.rerun()

    st.markdown("---")
    st.markdown("#### 📦 高级")
    timeout = st.slider("超时（秒）", min_value=60, max_value=1800,
                        value=DEFAULT_TIMEOUT, step=60)
    max_turns_cap = st.slider("最大轮数", min_value=5, max_value=80,
                              value=30, step=5)
    memory = st.selectbox("内存", ['1g', '2g', '4g', '8g'], index=1)
    cpus = st.selectbox("CPU", ['1', '2', '4'], index=1)

    st.markdown("---")
    st.markdown("#### 📊 累计统计")
    try:
        stats = code_agent_stats()
        scols = st.columns(2)
        scols[0].metric("总轮次", stats['total_turns'])
        scols[1].metric("会话数", stats['distinct_sessions'])
        scols[0].metric("成功率", f"{stats['success_rate'] * 100:.0f}%")
        scols[1].metric("总成本", f"${stats['total_cost_usd']:.2f}")
    except Exception as e:
        st.caption(f"统计读取失败：{e}")


# ────────────────────────────────────────────────
# 入口检查
# ────────────────────────────────────────────────

if not docker_ok:
    st.error("Docker 未就绪，无法继续。装好后刷新页面。")
    st.stop()

if not image_exists(DEFAULT_IMAGE):
    st.error(f"沙箱镜像 `{DEFAULT_IMAGE}` 还没构建。")
    st.code(f"cd {PROJECT_ROOT}/v3 && ./build.sh", language="bash")
    st.stop()

if not is_configured():
    st.warning("⚠️ 请先在左侧侧栏填 Anthropic 端点配置。")
    st.stop()


# ────────────────────────────────────────────────
# 来自 V2 报表的 handoff 上下文（如果有）
# ────────────────────────────────────────────────
render_handoff_banner_in_page09()


# ────────────────────────────────────────────────
# 例子
# ────────────────────────────────────────────────

EXAMPLES = [
    "查 2026-04 杭州市新签了多少家服务商，按累计上线货值排序前 10，画一张柱状图",
    "找出过去 30 天内累计上线货值跳升 ≥ ¥50,000 的服务商（疑似套上线），列名单 + 上线明细",
    "对比 2026-Q1 vs 2025-Q1，浙江省 V4 服务商净增数和总货值变化",
    "黄明君业务员过去 3 个月跑了多少次合法打卡？按周列出活跃度趋势图",
    "宁波鄞州区 5 月所有打卡距离 >500 米的红包记录拉出来，看是不是套上线",
]

with st.expander("💡 试试这些例子（一键填充）"):
    for ex in EXAMPLES:
        if st.button(ex, key=f"ex_{hash(ex)}", use_container_width=True):
            st.session_state['ca_question'] = ex
            st.rerun()


# ────────────────────────────────────────────────
# 历史对话回顾（多轮上下文）
# ────────────────────────────────────────────────
if st.session_state['ca_history']:
    st.markdown("#### 💭 对话历史")
    for i, h in enumerate(st.session_state['ca_history']):
        role = h['role']
        if role == 'user':
            with st.chat_message('user'):
                st.markdown(h['content'])
        else:  # assistant
            with st.chat_message('assistant'):
                st.markdown(h.get('content') or '_（无文本回复）_')
                # 反馈按钮（只对最近的 assistant 显示）
                log_id = h.get('log_id')
                if log_id and i == len(st.session_state['ca_history']) - 1:
                    fb_cols = st.columns([1, 1, 8])
                    with fb_cols[0]:
                        if st.button("👍", key=f"fb_up_{log_id}"):
                            update_code_agent_feedback(log_id, 1)
                            st.toast("已记录 👍")
                    with fb_cols[1]:
                        if st.button("👎", key=f"fb_down_{log_id}"):
                            update_code_agent_feedback(log_id, -1)
                            st.toast("已记录 👎")


# ────────────────────────────────────────────────
# 提问区
# ────────────────────────────────────────────────
st.markdown("---")
is_first_turn = st.session_state['ca_claude_session_id'] is None
prompt_label = "💬 第一个问题" if is_first_turn else "💬 继续追问（同一会话）"
question = st.text_area(
    prompt_label,
    value=st.session_state.get('ca_question', ''),
    height=120,
    key='ca_question_input',
    placeholder="自然语言描述你想要的分析。AI 会自己写代码 / 查 DB / 画图 / 出报告。",
)

run_col, clear_col, _ = st.columns([1, 1, 5])
with run_col:
    run_btn = st.button("🚀 跑！", type='primary', use_container_width=True,
                        disabled=not question.strip())
with clear_col:
    if st.button("🗑️ 清输入", use_container_width=True):
        st.session_state['ca_question'] = ''
        st.session_state.pop('ca_question_input', None)
        st.rerun()


# ────────────────────────────────────────────────
# 执行
# ────────────────────────────────────────────────

if run_btn and question.strip():
    st.markdown("---")

    # 顶部状态
    status_box = st.empty()
    metric_cols = st.columns(4)
    duration_metric = metric_cols[0].empty()
    turns_metric = metric_cols[1].empty()
    cost_metric = metric_cols[2].empty()
    files_metric = metric_cols[3].empty()

    # 主体三栏
    main_left, main_right = st.columns([3, 2])
    with main_left:
        st.markdown("#### 🤖 AI 思考过程")
        thinking_box = st.container()
    with main_right:
        st.markdown("#### 🔧 工具调用")
        tools_box = st.container()

    # stderr 实时区
    stderr_holder = st.empty()
    stderr_lines: list[str] = []

    # 文件区
    st.markdown("---")
    st.markdown("#### 📂 生成的文件")
    files_box = st.empty()

    # 调试区（折叠）
    with st.expander("🛠️ Debug — 原始事件流", expanded=False):
        debug_box = st.empty()

    # 状态收集
    n_turns = 0
    last_text_chunks: list[str] = []
    tool_calls = []
    tool_results_by_id = {}
    final_result = None
    debug_lines = []
    files_so_far = []
    started = time.time()
    captured_claude_sid = None
    captured_log_id = None

    status_box.info(
        "🚀 沙箱启动中..." if is_first_turn
        else f"🔁 恢复会话 `{st.session_state['ca_claude_session_id']}` 中..."
    )

    # 如果有 V2 → V3 的 handoff context，且这是第一轮，把上下文拼到 prompt 前
    handoff_ctx = consume_handoff_context()
    if handoff_ctx and is_first_turn:
        full_question = build_prompt_prefix(handoff_ctx) + question.strip()
    else:
        full_question = question.strip()

    try:
        for event in run_agent(
            full_question,
            db_path=DB_PATH,
            output_root=SANDBOX_OUTPUT_ROOT,
            ui_session_id=st.session_state['ca_ui_session_id'],
            resume_claude_session_id=st.session_state['ca_claude_session_id'],
            timeout=timeout,
            max_turns=max_turns_cap,
            memory=memory,
            cpus=cpus,
        ):
            etype = event.get('type', '?')
            debug_lines.append(json.dumps(event, ensure_ascii=False, default=str)[:800])

            # ── meta ──
            if etype == 'meta':
                tag = "（resume）" if event.get('resumed') else "（新会话）"
                status_box.info(
                    f"🚀 UI `{event['ui_session_id']}` {tag} · "
                    f"DB={Path(event['db_path']).name} · "
                    f"output=`{Path(event['output_dir']).name}/`"
                )

            # ── system: 含 claude_session_id ──
            elif etype == 'system':
                sid = event.get('session_id')
                if sid:
                    captured_claude_sid = sid

            # ── assistant ──
            elif etype == 'assistant':
                msg = event.get('message') or {}
                content = msg.get('content') or []
                if isinstance(content, list):
                    for blk in content:
                        if not isinstance(blk, dict):
                            continue
                        if blk.get('type') == 'text':
                            t = blk.get('text', '')
                            if t:
                                last_text_chunks.append(t)
                                with thinking_box:
                                    st.markdown(t)
                        elif blk.get('type') == 'tool_use':
                            tu = {
                                'id': blk.get('id'),
                                'name': blk.get('name'),
                                'input': blk.get('input', {}),
                            }
                            tool_calls.append(tu)
                            n_turns += 1
                            with tools_box:
                                with st.expander(
                                    f"🔧 [{n_turns}] {tu['name']}",
                                    expanded=False,
                                ):
                                    inp = tu.get('input', {}) or {}
                                    if tu['name'] == 'Bash':
                                        st.code(inp.get('command', ''), language='bash')
                                        if inp.get('description'):
                                            st.caption(inp['description'])
                                    elif tu['name'] in ('Read', 'Edit', 'Write'):
                                        st.code(inp.get('file_path', ''), language=None)
                                        if inp.get('content'):
                                            st.code(inp['content'][:1000], language='python')
                                    else:
                                        st.json(inp)

            # ── user: tool_result ──
            elif etype == 'user':
                tr = extract_tool_result(event)
                if tr:
                    tool_results_by_id[tr['tool_use_id']] = tr
                    with tools_box:
                        content = tr.get('content', '')
                        if isinstance(content, list):
                            content = '\n'.join(
                                b.get('text', '') for b in content if isinstance(b, dict)
                            )
                        prefix = '❌ ' if tr.get('is_error') else '↳ '
                        st.caption(f"{prefix}{str(content)[:300]}")

            # ── result ──
            elif etype == 'result':
                final_result = extract_result(event)

            # ── stderr 实时渲染 ──
            elif etype == 'stderr':
                stderr_lines.append(event.get('text', ''))
                with stderr_holder.container():
                    with st.expander(f"⚠️ 容器 stderr（{len(stderr_lines)} 行）", expanded=False):
                        st.code('\n'.join(stderr_lines[-100:]))

            # ── error ──
            elif etype == 'error':
                status_box.error(f"❌ {event.get('error')}")

            # ── done ──
            elif etype == 'done':
                rc = event.get('returncode')
                files_so_far = event.get('files', [])
                captured_claude_sid = event.get('claude_session_id') or captured_claude_sid
                captured_log_id = event.get('log_id')
                if rc == 0:
                    status_box.success(
                        f"✅ 完成 · 用时 {event.get('duration_s')}s · "
                        f"{len(files_so_far)} 个文件 · log_id=`{captured_log_id}`"
                    )
                else:
                    extra = "（已超时）" if event.get('timed_out') else ""
                    status_box.error(f"❌ 进程退出码 {rc}{extra} · 看 stderr 找原因")

            # 实时 metrics
            duration_metric.metric("⏱ 已用", f"{int(time.time() - started)}s")
            turns_metric.metric("🔁 工具调用", str(n_turns))
            if final_result:
                cost_metric.metric(
                    "💰 成本",
                    f"${final_result.get('cost_usd') or 0:.4f}",
                )
            files_metric.metric("📂 文件", str(len(files_so_far)))

            with debug_box:
                st.code('\n'.join(debug_lines[-30:]), language='json')

    except Exception as e:
        status_box.error(f"❌ Streamlit 端异常：{e}")
        import traceback
        with st.expander("Traceback", expanded=True):
            st.code(traceback.format_exc())

    # ── 文件预览 ──
    with files_box.container():
        if not files_so_far:
            st.info("沙箱没生成任何文件 —— AI 可能直接在文本里回答了。")
        else:
            for f in files_so_far:
                fcols = st.columns([5, 2, 2])
                fcols[0].text(f['rel'])
                fcols[1].text(f"{f['size'] / 1024:.1f} KB")

                fp = Path(f['abs'])
                if fp.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                    st.image(str(fp), caption=f['rel'])
                elif fp.suffix.lower() == '.csv':
                    try:
                        import pandas as pd
                        df_preview = pd.read_csv(fp).head(50)
                        with st.expander(f"📊 {f['rel']} 预览（前 50 行）", expanded=False):
                            st.dataframe(df_preview, use_container_width=True)
                    except Exception as ex:
                        st.caption(f"读 CSV 失败：{ex}")
                elif fp.suffix.lower() in ('.md', '.markdown'):
                    with st.expander(f"📄 {f['rel']}", expanded=True):
                        st.markdown(fp.read_text(encoding='utf-8'))

                try:
                    fcols[2].download_button(
                        "⬇️ 下载",
                        data=fp.read_bytes(),
                        file_name=f['name'],
                        key=f"dl_{f['rel']}",
                        use_container_width=True,
                    )
                except Exception:
                    pass

    # ── 把这一轮记入 UI 历史 ──
    answer_text = '\n'.join(last_text_chunks).strip()
    st.session_state['ca_history'].append({'role': 'user', 'content': question.strip()})
    st.session_state['ca_history'].append({
        'role': 'assistant',
        'content': answer_text,
        'log_id': captured_log_id,
        'files': files_so_far,
    })

    # ── 关键：保存 claude_session_id 给下一轮 resume ──
    if captured_claude_sid:
        st.session_state['ca_claude_session_id'] = captured_claude_sid

    # 清输入框，等下一轮
    st.session_state['ca_question'] = ''


# ────────────────────────────────────────────────
# 历史会话浏览
# ────────────────────────────────────────────────
st.markdown("---")


def _list_session_files(sandbox_root: Path, ui_session_id: str) -> list[dict]:
    """扫某个 ui_session_id 目录下的所有产物文件（不含 _claude_state 等内部目录）"""
    if not ui_session_id:
        return []
    sess_dir = sandbox_root / ui_session_id
    if not sess_dir.exists():
        return []
    files = []
    for p in sess_dir.rglob('*'):
        if not p.is_file():
            continue
        rel = p.relative_to(sess_dir)
        # 跳过内部状态文件
        parts = rel.parts
        if parts and parts[0] in ('_claude_state',):
            continue
        if rel.name in ('_status.json', '_events.jsonl'):
            continue
        try:
            size = p.stat().st_size
        except Exception:
            size = 0
        files.append({
            'name': p.name,
            'rel': str(rel),
            'abs': str(p.resolve()),
            'size': size,
        })
    return sorted(files, key=lambda f: f['rel'])


# 先扫所有 session_id 对应的文件数（决定是否在标题里提示）
try:
    _logs_pre = read_code_agent_logs(limit=50)
    _total_files = sum(
        len(_list_session_files(SANDBOX_OUTPUT_ROOT, str(r['ui_session_id'])))
        for _, r in _logs_pre.iterrows()
    ) if not _logs_pre.empty else 0
except Exception:
    _total_files = 0

with st.expander(
    f"📚 历史会话（最近 50 轮）"
    + (f" · 含 {_total_files} 个产物文件可下载" if _total_files > 0 else ""),
    expanded=(_total_files > 0),
):
    try:
        df_logs = read_code_agent_logs(limit=50)
        if df_logs.empty:
            st.info("还没有历史记录")
        else:
            for _, row in df_logs.iterrows():
                rc_emoji = '✅' if row['returncode'] == 0 else '❌'
                fb_emoji = ('👍' if row['feedback'] == 1
                            else '👎' if row['feedback'] == -1 else '·')

                # 扫该 session 的产物文件
                ui_sid = str(row['ui_session_id']) if row['ui_session_id'] else ''
                sess_files = _list_session_files(SANDBOX_OUTPUT_ROOT, ui_sid)

                st.markdown(
                    f"{rc_emoji} `{ui_sid}` · {row['timestamp']} · "
                    f"{row['duration_ms']/1000:.1f}s · "
                    f"💰${row['cost_usd'] or 0:.4f} · "
                    f"📂 **{len(sess_files)} 个文件** · {fb_emoji}"
                )
                st.caption(f"Q: {row['user_question'][:200]}")

                # 列出文件 + 下载按钮
                if sess_files:
                    for f in sess_files:
                        fcols = st.columns([5, 2, 2])
                        fcols[0].text(f"📄 {f['rel']}")
                        fcols[1].caption(f"{f['size']/1024:.1f} KB")
                        try:
                            fp = Path(f['abs'])
                            fcols[2].download_button(
                                "⬇️ 下载",
                                data=fp.read_bytes(),
                                file_name=f['name'],
                                key=f"hist_dl_{row['id']}_{f['rel']}",
                                use_container_width=True,
                            )
                        except Exception as ex:
                            fcols[2].caption(f"⚠️ {ex}")

                st.markdown("---")
    except Exception as e:
        st.caption(f"读取失败：{e}")
