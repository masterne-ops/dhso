#!/usr/bin/env python3
"""📅 月度地市经营报告 — 选地市 + 月份范围，一键生成 docx 报告

工作流（标准 agent 模式）：
  1. UI: 选地市 / 开始月份 / 结束月份
  2. 后端：算同环比时段 + 用 SQL 查 5 个维度的数据汇总（数字准确性优先，不让 AI 写查询）
  3. 把数据汇总写成 JSON 喂给 V3 沙箱
  4. 沙箱里 AI 跑 SKILL `/sandbox/skills/monthly-city-report/SKILL.md`：
     - 拿 JSON 用 python-docx 拼装数据表
     - 自己写「问题」+「下一阶段工作计划」两章
     - 输出 .docx 到 /sandbox/output/
  5. UI 提供 docx + JSON 下载
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
    extract_tool_result,
    extract_tool_use,
    image_exists,
    is_configured,
    run_agent,
    start_async_run,
    get_async_run_status,
)
import json  # noqa: E402
from _monthly_city_report import (  # noqa: E402
    calculate_periods,
    gather_full_report,
    list_cities,
    list_months,
)
from _ai_log import (  # noqa: E402
    save_monthly_report,
    get_monthly_report,
    list_monthly_reports,
    delete_monthly_report,
)

require_auth()

# AI 功能仅 admin 可用
from _auth import is_admin, current_user, get_current_role  # noqa: E402
if not is_admin():
    st.error(
        f"⛔ AI 功能（月度地市经营报告）仅 admin 可用。"
        f"当前用户: `{current_user()}`，角色: `{get_current_role()}`"
    )
    st.stop()

st.markdown("### 📅 月度地市经营报告")
st.caption(
    "选地市 + 月份范围 → 自动生成 `.docx` 经营月报。"
    "**数据汇总由 SQL 算准（5 维度）**，**问题 + 工作计划由 AI 写**。"
    "同比 = 去年同期；环比 = 紧邻当期前 N 个月（N = 当期月数）。"
)

# ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent
SANDBOX_OUTPUT_ROOT = PROJECT_ROOT / 'v3' / 'sandbox-output'
SANDBOX_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

# ────────────────────────────────────────────────
# 上半部分：UI（选参）
# ────────────────────────────────────────────────

# 城市列表 + 数据覆盖月份
try:
    cities = list_cities()
    min_month, max_month = list_months()
except Exception as e:
    st.error(f"读 DB 失败：{e}")
    st.stop()

col1, col2, col3 = st.columns([2, 2, 2])

with col1:
    city = st.selectbox(
        "📍 地市",
        cities,
        index=cities.index('金华市') if '金华市' in cities else 0,
    )

# 生成月份选项列表（YYYY-MM 字符串排序）
def month_range(start: str, end: str) -> list[str]:
    import pandas as pd
    s = pd.Period(start, freq='M')
    e = pd.Period(end, freq='M')
    return [str(s + i) for i in range((e - s).n + 1)]

all_months = month_range(min_month, max_month)

with col2:
    start_month = st.selectbox(
        "📅 开始月份",
        all_months,
        index=max(0, len(all_months) - 2),  # 默认倒数第 2 个
    )

with col3:
    # 结束月份只显示 >= 开始月份的
    end_options = [m for m in all_months if m >= start_month]
    end_month = st.selectbox(
        "📅 结束月份",
        end_options,
        index=0,  # 默认同开始月份
    )

# 算时段
try:
    periods = calculate_periods(start_month, end_month)
except Exception as e:
    st.error(f"时段算错：{e}")
    st.stop()

st.markdown("---")
st.markdown("##### 🗓️ 时段对照预览")
pcols = st.columns(3)
pcols[0].metric("📍 当期",
                periods['current'].label,
                f"{periods['current'].n_months} 个月")
pcols[1].metric("📊 同期（去年同月）",
                periods['yoy'].label,
                f"{periods['yoy'].n_months} 个月")
pcols[2].metric("📊 环期（紧邻前期）",
                periods['mom'].label,
                f"{periods['mom'].n_months} 个月")

st.markdown("---")

# ────────────────────────────────────────────────
# 已有报告检查（缓存命中）
# ────────────────────────────────────────────────
cached = get_monthly_report(city, start_month, end_month)
force_regen = st.session_state.get('mc_force_regen', False)

if cached and not force_regen:
    docx_exists = cached.get('docx_path') and Path(cached['docx_path']).exists()
    with st.container(border=True):
        cc1, cc2 = st.columns([4, 1])
        with cc1:
            st.markdown(f"#### ✅ 已有报告 · 生成于 `{cached['生成时间']}`")
            if cached.get('核心结论'):
                st.markdown(f"**核心结论**：{cached['核心结论']}")
            if cached.get('cost_usd'):
                st.caption(
                    f"耗时 {cached['duration_ms']/1000:.0f}s · "
                    f"成本 ${cached['cost_usd']:.4f}"
                )
        with cc2:
            if docx_exists:
                try:
                    fp = Path(cached['docx_path'])
                    st.download_button(
                        "⬇️ 下载 docx",
                        data=fp.read_bytes(),
                        file_name=fp.name,
                        mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        key=f"dl_cached_{cached['id']}",
                        use_container_width=True,
                        type='primary',
                    )
                except Exception:
                    st.warning("docx 读取失败")
            else:
                st.caption("⚠️ docx 文件丢失（被清理过）")

        act_cols = st.columns([1, 1, 1, 5])
        if act_cols[0].button("🔄 重新生成", help="数据更新后用，会覆盖此报告",
                               use_container_width=True):
            st.session_state['mc_force_regen'] = True
            st.rerun()
        if act_cols[1].button("🗑 删除归档",
                               help="从数据库 + 磁盘删除这份报告",
                               use_container_width=True):
            delete_monthly_report(cached['id'])
            st.session_state.pop('mc_force_regen', None)
            st.toast(f"已删除 {city} {cached['时段_label']} 报告")
            st.rerun()

# ────────────────────────────────────────────────
# 环境检查（V3 沙箱）
# ────────────────────────────────────────────────
with st.expander("🩺 V3 沙箱环境检查（生成 docx 需要）", expanded=False):
    docker_ok, docker_msg = docker_available()
    if docker_ok:
        st.success("✅ Docker daemon 就绪")
    else:
        st.error(f"❌ {docker_msg}")

    if docker_ok and image_exists(DEFAULT_IMAGE):
        st.success(f"✅ 镜像 `{DEFAULT_IMAGE}` 已构建")
    else:
        st.warning(f"⚠️ 镜像 `{DEFAULT_IMAGE}` 未构建（需 `cd v3 && ./build.sh`）")

    if is_configured():
        st.success("✅ Anthropic 端点已配置")
    else:
        st.warning("⚠️ Anthropic 端点未配置（去 page 09 左栏配）")

# ────────────────────────────────────────────────
# 触发按钮（已有报告且没点重新生成时隐藏「开始生成」）
# ────────────────────────────────────────────────
show_generate_btn = not cached or force_regen
btn_cols = st.columns([1, 1, 4])
go = btn_cols[0].button(
    "🚀 重新生成" if force_regen else "🚀 开始生成",
    type="primary",
    disabled=not (docker_ok and is_configured() and show_generate_btn),
    use_container_width=True,
)
preview = btn_cols[1].button(
    "👁️ 仅预览数据（不跑 AI）",
    use_container_width=True,
)

# ────────────────────────────────────────────────
# 数据预览模式：只跑数据汇总，不调沙箱
# ────────────────────────────────────────────────
if preview:
    st.markdown("---")
    st.markdown("#### 📋 数据汇总预览（json）")
    with st.spinner("查 DB 中..."):
        report = gather_full_report(city, start_month, end_month, db_path=DB_PATH)
    st.json(report, expanded=False)

# ────────────────────────────────────────────────
# 完整生成：数据 → 沙箱 → docx
# ────────────────────────────────────────────────
if go:
    st.markdown("---")
    status_box = st.empty()
    metric_cols = st.columns(4)
    duration_metric = metric_cols[0].empty()
    turns_metric = metric_cols[1].empty()
    cost_metric = metric_cols[2].empty()
    files_metric = metric_cols[3].empty()

    # ── 1. 算数据 ──────────────────────────────
    status_box.info("📊 步骤 1/3：用 SQL 算数据汇总（5 维度 × 3 时段）...")
    try:
        report = gather_full_report(city, start_month, end_month, db_path=DB_PATH)
    except Exception as e:
        status_box.error(f"❌ 数据汇总失败：{e}")
        st.stop()

    # ── 2. 写到沙箱输入目录 ────────────────────
    # ui_session_id 在 session_state 保留，避免每次 rerun 都新生成（破坏后台任务跟踪）
    # 用 (city + period) 做 key，让不同月份/城市的生成各自独立
    sess_key = f'_mr_ui_session_id__{city}__{start_month}__{end_month}'
    if force_regen or sess_key not in st.session_state:
        st.session_state[sess_key] = uuid.uuid4().hex[:12]
    ui_session_id = st.session_state[sess_key]
    session_dir = SANDBOX_OUTPUT_ROOT / ui_session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # work/ 给输入用，output/ 给产物
    work_dir = session_dir / 'work'
    work_dir.mkdir(exist_ok=True)
    try:
        work_dir.chmod(0o777)
    except OSError:
        pass

    data_json_host = work_dir / 'data_summary.json'
    with open(data_json_host, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    # docx 文件名（容器内路径，AI 会自己写）
    safe_label = report['当期起止'].replace(' ', '').replace('~', '_to_')
    docx_filename = f"{city}_经营月报_{safe_label}.docx"

    status_box.info(
        f"📦 步骤 2/3：数据已写到沙箱 `{ui_session_id}/work/data_summary.json`，"
        f"启动 AI 沙箱生成 docx..."
    )

    # ── 3. 跑沙箱 SKILL ──────────────────────────
    integrity_msg = (
        '有缺口 — ' + '；'.join(report['数据完整性']['缺口'])
        if report['数据完整性']['有缺口'] else '完整'
    )
    prompt = f"""你现在要执行一个 SKILL：生成月度地市经营报告。

**第一步：必读两份文档（顺序）**
1. `/sandbox/skills/system-overview/SKILL.md` — 全平台指标 / 字段 / 双口径定义（**所有任务通用**）
2. `/sandbox/skills/monthly-city-report/SKILL.md` — 月报的 6 章模板 + 3 条铁律

**输入**：
- 数据 JSON：`/sandbox/output/work/data_summary.json`
- 地市：{report['地市']}
- 当期：{report['当期起止']}（{report['当期月数']} 个月）
- 同期：{report['时段']['yoy']['label']}
- 环期：{report['时段']['mom']['label']}
- 数据完整性：{integrity_msg}

**输出**：docx 报告写到 `/sandbox/output/{docx_filename}`

**三条铁律（SKILL.md 详述）**：
1. 数据表里的数字**全部从 `_显示` 字段 copy**，禁止自己用 Python 算同环比或重 format
2. 问题/工作计划里引用的每个数字 / 区县 / 服务商 / 业务员名，**必须能在前面数据表找到出处**
3. 数据缺失（`N/A`）时如实写 `N/A`，**不要假装有数据**

完成后 stdout 输出一句话核心结论摘要。"""

    # ── 主体两栏 ──
    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### 🤖 AI 工作过程")
        think_box = st.container()
    with right:
        st.markdown("#### 🔧 工具调用")
        tools_box = st.container()

    stderr_holder = st.empty()
    stderr_lines: list[str] = []

    final_result = None
    files_out: list[dict] = []
    started = time.time()
    n_turns = 0

    # ── 后台异步任务模式 — 容忍前端断开 ────────────
    # 启动 async run 后，把 run_id 存到 session_state，
    # 后续每次 rerun 都从文件读全量事件回放（不依赖 streamlit 单次 render 不中断）
    run_id_key = f'mr_run_{ui_session_id}'
    if run_id_key not in st.session_state:
        info = start_async_run(
            prompt,
            db_path=DB_PATH,
            output_root=SANDBOX_OUTPUT_ROOT,
            ui_session_id=ui_session_id,
            timeout=DEFAULT_TIMEOUT,
            max_turns=40,
            memory='2g', cpus='2',
        )
        st.session_state[run_id_key] = info['run_id']
        st.caption(f"🚀 已启动后台任务 `{info['run_id']}` — 可关 tab / 登出，回来时事件会从中断处继续渲染")

    # 拉全量事件流（每次 rerun 都重读，避免中断丢失）
    poll = get_async_run_status(st.session_state[run_id_key], SANDBOX_OUTPUT_ROOT)
    events_iter = poll['events']
    bg_status = poll['status']

    try:
        for event in events_iter:
            etype = event.get('type')

            if etype == 'meta':
                pass

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
                                with think_box:
                                    st.markdown(t)
                        elif blk.get('type') == 'tool_use':
                            n_turns += 1
                            with tools_box:
                                with st.expander(
                                    f"🔧 [{n_turns}] {blk.get('name')}",
                                    expanded=False,
                                ):
                                    inp = blk.get('input', {}) or {}
                                    if blk['name'] == 'Bash':
                                        st.code(inp.get('command', '')[:2000],
                                                language='bash')
                                    elif blk['name'] in ('Read', 'Write', 'Edit'):
                                        st.code(inp.get('file_path', ''), language=None)
                                        if 'content' in inp:
                                            st.code(inp['content'][:800],
                                                    language='python')
                                    else:
                                        st.json(inp)

            elif etype == 'user':
                tr = extract_tool_result(event)
                if tr:
                    with tools_box:
                        content = tr.get('content', '')
                        if isinstance(content, list):
                            content = '\n'.join(
                                b.get('text', '') for b in content if isinstance(b, dict)
                            )
                        prefix = '❌ ' if tr.get('is_error') else '↳ '
                        st.caption(f"{prefix}{str(content)[:300]}")

            elif etype == 'result':
                final_result = event

            elif etype == 'stderr':
                stderr_lines.append(event.get('text', ''))
                if len(stderr_lines) % 5 == 0:
                    with stderr_holder.container():
                        with st.expander(f"⚠️ stderr ({len(stderr_lines)} 行)", expanded=False):
                            st.code('\n'.join(stderr_lines[-50:]))

            elif etype == 'done':
                rc = event.get('returncode')
                files_out = event.get('files', [])
                if rc == 0:
                    status_box.success(
                        f"✅ 步骤 3/3 完成 · 用时 {event.get('duration_s')}s · "
                        f"{len(files_out)} 个产物"
                    )
                else:
                    status_box.error(f"❌ AI 退出码 {rc}，看 stderr")

            elif etype == 'error':
                status_box.error(f"❌ {event.get('error')}")

            # 实时 metrics
            duration_metric.metric("⏱ 已用", f"{int(time.time() - started)}s")
            turns_metric.metric("🔁 工具调用", str(n_turns))
            if final_result:
                cost_metric.metric(
                    "💰 成本",
                    f"${final_result.get('total_cost_usd') or 0:.4f}",
                )
            files_metric.metric("📂 产物", str(len(files_out)))

    except Exception as e:
        status_box.error(f"❌ Streamlit 异常：{e}")
        import traceback
        with st.expander("Traceback", expanded=True):
            st.code(traceback.format_exc())

    # ── 后台任务状态展示 + 自动刷新（仍 running 时）──
    if bg_status == 'running':
        status_box.info(
            f"🔄 后台任务进行中… ({len(events_iter)} 条事件，"
            f"已用 {int(time.time() - started)}s)。"
            "**你可以关闭页面 / 登出 / 切换 Tab，任务在后台继续跑。**"
            "5 秒后自动刷新…"
        )
        time.sleep(5)
        st.rerun()
    elif bg_status == 'failed':
        status_box.error(f"❌ 后台任务失败：{poll.get('error', '未知错误')}")
        if st.button("🔄 清理状态，重新发起"):
            st.session_state.pop(run_id_key, None)
            st.rerun()
        st.stop()  # 失败时不继续归档/下载块

    # ── 4. 归档到 monthly_report_log（无论成功失败都记一条）─
    docx_path_archived = None
    if files_out:
        docx_files = [f for f in files_out if f['name'].lower().endswith('.docx')]
        if docx_files:
            docx_path_archived = docx_files[0]['abs']

    try:
        archive_returncode = 0 if (final_result and docx_path_archived) else -1
        archive_cost = (final_result or {}).get('total_cost_usd') if final_result else None
        save_monthly_report(
            城市=city,
            时段_start=start_month,
            时段_end=end_month,
            时段_label=report['当期起止'],
            docx_path=docx_path_archived,
            核心结论=report.get('核心结论', ''),
            data_summary=report,
            ui_session_id=ui_session_id,
            returncode=archive_returncode,
            duration_ms=int((time.time() - started) * 1000),
            cost_usd=archive_cost,
        )
        # 用完后清掉重新生成 flag
        st.session_state.pop('mc_force_regen', None)
    except Exception as e:
        st.warning(f"⚠️ 归档失败（不影响 docx 下载）：{e}")

    # ── 5. 产物展示 + 下载 ─────────────────────
    st.markdown("---")
    st.markdown("#### 📂 报告产物")

    if not files_out:
        st.warning("沙箱没生成任何文件。可能 AI 出错了，看「stderr」展开看错误。")
    else:
        docx_files = [f for f in files_out if f['name'].lower().endswith('.docx')]
        other_files = [f for f in files_out if not f['name'].lower().endswith('.docx')]

        for f in docx_files:
            fcols = st.columns([6, 2, 2])
            fcols[0].markdown(f"**📄 {f['rel']}** · {f['size']/1024:.1f} KB")
            fp = Path(f['abs'])
            try:
                fcols[1].download_button(
                    "⬇️ 下载 docx",
                    data=fp.read_bytes(),
                    file_name=f['name'],
                    mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    key=f"dl_{f['rel']}",
                    use_container_width=True,
                )
            except Exception:
                pass

        if other_files:
            with st.expander(f"📦 其它产物 ({len(other_files)})", expanded=False):
                for f in other_files:
                    st.markdown(f"- {f['rel']}（{f['size']/1024:.1f} KB）")

    # ── 5. 也给个原始数据 JSON 下载 ────────────
    st.markdown("---")
    with st.expander("📋 数据汇总 JSON（喂给 AI 的输入，调试用）", expanded=False):
        st.json(report, expanded=False)
        st.download_button(
            "⬇️ 下载 data_summary.json",
            data=json.dumps(report, ensure_ascii=False, indent=2, default=str),
            file_name=f"{city}_{safe_label}_data_summary.json",
            mime='application/json',
            use_container_width=False,
        )


# ════════════════════════════════════════════════
# 📚 历史报告归档
# ════════════════════════════════════════════════
st.markdown("---")

# 先扫一下孤儿，决定 expander 默认展开还是收起
import time as _t
_hist_for_paths = None
try:
    _hist_for_paths = list_monthly_reports(limit=50)
except Exception:
    pass
_archived_paths_pre = set()
if _hist_for_paths is not None and not _hist_for_paths.empty:
    _archived_paths_pre = set(str(p) for p in _hist_for_paths['docx_path'].dropna().astype(str)
                              if p and p != 'nan')
_orphan_count = 0
if SANDBOX_OUTPUT_ROOT.exists():
    for d in SANDBOX_OUTPUT_ROOT.iterdir():
        if not d.is_dir():
            continue
        for docx in d.glob('*.docx'):
            if str(docx.resolve()) in _archived_paths_pre:
                continue
            _orphan_count += 1

# 头部明显提示
if _orphan_count > 0:
    st.info(
        f"📂 沙箱中有 **{_orphan_count} 个未归档的历史 docx**，"
        "展开下方「**📚 历史报告归档**」即可下载（默认已展开）"
    )

with st.expander(
    f"📚 历史报告归档（最近 50 份）"
    + (f" · 含 {_orphan_count} 个未归档" if _orphan_count > 0 else ""),
    expanded=(_orphan_count > 0),
):
    try:
        hist = list_monthly_reports(limit=50)
    except Exception as e:
        st.caption(f"读取归档失败：{e}")
        hist = None

    # 收集已归档的 docx 路径，用于剔除孤儿扫描
    archived_paths = set()
    if hist is not None and not hist.empty:
        archived_paths = set(str(p) for p in hist['docx_path'].dropna().astype(str)
                              if p and p != 'nan')

    # 📂 扫沙箱目录里"还没归档"的 docx 文件（之前跑成功但未入库的）
    orphan_runs = []
    if SANDBOX_OUTPUT_ROOT.exists():
        for d in sorted(SANDBOX_OUTPUT_ROOT.iterdir(),
                        key=lambda p: p.stat().st_mtime if p.is_dir() else 0,
                        reverse=True):
            if not d.is_dir():
                continue
            docx_files = list(d.glob('*.docx'))
            if not docx_files:
                continue
            for docx in docx_files:
                docx_abs = str(docx.resolve())
                if docx_abs in archived_paths:
                    continue
                # 读 _status.json 拿元信息（如有）
                status_file = d / '_status.json'
                meta = {}
                if status_file.exists():
                    try:
                        with open(status_file, encoding='utf-8') as f:
                            meta = json.load(f)
                    except Exception:
                        pass
                stat = docx.stat()
                orphan_runs.append({
                    'run_id': d.name,
                    'docx_path': docx_abs,
                    'name': docx.name,
                    'size_kb': stat.st_size / 1024,
                    'mtime': stat.st_mtime,
                    'mtime_str': _t.strftime('%Y-%m-%d %H:%M', _t.localtime(stat.st_mtime)),
                    'question': (meta.get('question') or '')[:80],
                })

    if orphan_runs:
        st.warning(
            f"⚠️ 发现沙箱里 **{len(orphan_runs)} 个 docx 未归档**"
            "（之前跑出来但没正常 archive 到 DB）—— 下方可直接下载。"
        )
        for o in orphan_runs:
            ocols = st.columns([5, 2, 1])
            ocols[0].markdown(f"📄 **{o['name']}**")
            ocols[0].caption(f"`{o['run_id']}` · {o['mtime_str']} · {o['size_kb']:.1f} KB"
                              + (f" · {o['question']}" if o['question'] else ''))
            try:
                fp = Path(o['docx_path'])
                ocols[1].download_button(
                    "⬇️ 下载",
                    data=fp.read_bytes(),
                    file_name=fp.name,
                    key=f"orphan_dl_{o['run_id']}",
                    use_container_width=True,
                )
            except Exception as e:
                ocols[1].caption(f"⚠️ {e}")
            if ocols[2].button("🗑", key=f"orphan_del_{o['run_id']}",
                                help="删除整个 run 目录",
                                use_container_width=True):
                import shutil as _shutil
                try:
                    _shutil.rmtree(Path(o['docx_path']).parent)
                    st.rerun()
                except Exception as e:
                    st.error(f"删除失败：{e}")
        st.markdown("---")

    if hist is None or hist.empty:
        if not orphan_runs:
            st.info("还没有归档")
    else:
        for _, row in hist.iterrows():
            rc_emoji = '✅' if row['returncode'] == 0 else '❌'
            hcols = st.columns([3, 2, 4, 2, 1])
            hcols[0].markdown(f"**{rc_emoji} {row['城市']}**")
            hcols[1].caption(row['时段_label'])
            hcols[2].caption(f"{row['生成时间']} · {row['duration_ms']/1000:.0f}s")

            # 下载按钮
            docx_path = row.get('docx_path')
            if docx_path and Path(str(docx_path)).exists():
                try:
                    fp = Path(str(docx_path))
                    hcols[3].download_button(
                        "⬇️",
                        data=fp.read_bytes(),
                        file_name=fp.name,
                        key=f"hist_dl_{row['id']}",
                        use_container_width=True,
                        help=f"下载 {fp.name}",
                    )
                except Exception:
                    hcols[3].caption("⚠️ 失效")
            else:
                hcols[3].caption("—")

            # 删除按钮
            if hcols[4].button("🗑", key=f"hist_del_{row['id']}",
                                help="从归档删除（含 docx 文件）",
                                use_container_width=True):
                delete_monthly_report(int(row['id']))
                st.rerun()

            # 核心结论展示（一行缩略）
            if row.get('核心结论'):
                st.caption(f"&nbsp;&nbsp;💡 {row['核心结论'][:200]}",
                            unsafe_allow_html=True)
            st.markdown("---")
