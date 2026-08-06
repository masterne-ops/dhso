#!/usr/bin/env python3
"""📰 经营周报 — 全省 / 地市,选周截止日一键生成（仅 admin）

工作流（复用周报现有渲染）：
  1. UI：选范围（全省 / 11 地市）+ 周截止日
  2. 后端：gather_prov_weekly / gather_city_weekly 算周数据 → weekly.json（数字准）
  3. V3 沙箱 AI 跑 SKILL weekly-report：读 weekly.json → 写「分析」键 → 写回
  4. 后端 subprocess 调 report_tools/render_weekly_prov|city → HTML(手机版) + docx
  5. UI 下载
"""
import json
import sys
import time
import uuid
import subprocess
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin, current_user, get_current_role  # noqa: E402
from _loaders import DB_PATH  # noqa: E402
from _code_agent import (  # noqa: E402
    DEFAULT_IMAGE, DEFAULT_TIMEOUT, docker_available, image_exists,
    is_configured, start_async_run, get_async_run_status,
)
from _weekly_prov import gather_prov_weekly  # noqa: E402
from _weekly_city import gather_city_weekly  # noqa: E402
from _monthly_province_report import ZHEJIANG_CITIES  # noqa: E402

require_auth()
if not is_admin():
    st.error(f"⛔ 经营周报（AI 生成）仅 admin 可用。当前：`{current_user()}` / `{get_current_role()}`")
    st.stop()

st.markdown("### 📰 每周省区和地市周报")
st.caption("选范围 + 周截止日 → 一键生成经营周报（HTML 手机版 + docx）。"
           "**数据由 SQL 算准**，**分析由服务器 AI 撰写**。一周 = 周截止日往前 7 天。")

PROJECT_ROOT = Path(__file__).parent.parent.parent
SANDBOX_OUTPUT_ROOT = PROJECT_ROOT / 'v3' / 'sandbox-output'
SANDBOX_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
# 持久报告目录:按「范围-周」确定性命名,同范围同周重生成才覆盖,历史长期保留
REPORTS_DIR = PROJECT_ROOT / 'v3' / 'weekly-reports'
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
REPORT_TOOLS = PROJECT_ROOT / 'report_tools'

_DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def _report_dir(scope_name, wk_end):
    """确定性产物目录:范围+周。同范围同周重生成 → 覆盖。"""
    return REPORTS_DIR / f"{scope_name}-{wk_end}"


# ── 📂 历史报告（持久保留,随时下载;最新在前）──
with st.expander("📂 历史报告（已生成，随时下载）", expanded=True):
    hist = sorted([d for d in REPORTS_DIR.iterdir() if d.is_dir()],
                  key=lambda d: d.stat().st_mtime, reverse=True)
    if not hist:
        st.caption("暂无历史报告。生成后会自动保留在这里，刷新/重开页面都能找回。")
    for d in hist:
        files = sorted(d.glob('*.html')) + sorted(d.glob('*.docx'))
        if not files:
            continue
        import datetime as _dt2
        mt = _dt2.datetime.fromtimestamp(d.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        st.markdown(f"**{d.name}** · 生成于 {mt}")
        for fp in files:
            hc = st.columns([6, 2])
            hc[0].caption(f"📄 {fp.name} · {fp.stat().st_size/1024:.1f} KB")
            mime = 'text/html' if fp.suffix == '.html' else _DOCX_MIME
            hc[1].download_button("⬇️ 下载", data=fp.read_bytes(), file_name=fp.name,
                                  mime=mime, key=f"hist_{d.name}_{fp.name}",
                                  use_container_width=True)

# ── 选参 ──
c1, c2 = st.columns([2, 2])
with c1:
    scope = st.selectbox("📍 范围", ['浙江全省'] + ZHEJIANG_CITIES)
with c2:
    import datetime as _dt
    week_end_d = st.date_input("📅 周截止日（本周最后一天）", value=_dt.date(2026, 6, 13))
week_end = week_end_d.strftime('%Y-%m-%d')
is_prov = (scope == '浙江全省')
week_start = (week_end_d - _dt.timedelta(days=6)).strftime('%Y-%m-%d')
st.caption(f"本周区间：**{week_start} ~ {week_end}**（{'全省 11 地市' if is_prov else scope}）")

# ── 环境检查 ──
with st.expander("🩺 V3 沙箱环境检查", expanded=False):
    docker_ok, docker_msg = docker_available()
    st.success("✅ Docker 就绪") if docker_ok else st.error(f"❌ {docker_msg}")
    if docker_ok and image_exists(DEFAULT_IMAGE):
        st.success(f"✅ 镜像 `{DEFAULT_IMAGE}` 已构建")
    else:
        st.warning(f"⚠️ 镜像 `{DEFAULT_IMAGE}` 未构建")
    st.success("✅ Anthropic 端点已配置") if is_configured() else st.warning("⚠️ Anthropic 端点未配置（去 page 09 配）")
    st.success("✅ report_tools 就位") if (REPORT_TOOLS / 'render_weekly_prov.py').exists() else st.error("❌ report_tools 缺失（需 deploy 同步）")

btn_cols = st.columns([1, 1, 4])
go = btn_cols[0].button("🚀 生成周报", type="primary",
                        disabled=not (docker_ok and is_configured()), use_container_width=True)
preview = btn_cols[1].button("👁️ 仅预览数据", use_container_width=True)

# ── 仅预览数据 ──
if preview:
    with st.spinner("算周数据中…"):
        rep = (gather_prov_weekly(week_end, db_path=DB_PATH) if is_prov
               else gather_city_weekly(scope, week_end, db_path=DB_PATH))
    st.json(rep, expanded=False)

# ── 完整生成 ──
# 点按钮只置门控标志;生成/轮询/渲染块由 session_state 门控（与 go 解耦，st.rerun() 后仍能进入）
active_key = f'_wk_active__{scope}__{week_end}'
if go:
    st.session_state[active_key] = True

if st.session_state.get(active_key):
    st.markdown("---")
    status_box = st.empty()

    # 1. 沙箱输入目录（session 稳定，避免 rerun 重生成）
    sess_key = f'_wk_sid__{scope}__{week_end}'
    if sess_key not in st.session_state:
        st.session_state[sess_key] = uuid.uuid4().hex[:12]
    ui_session_id = st.session_state[sess_key]
    session_dir = SANDBOX_OUTPUT_ROOT / ui_session_id
    work_dir = session_dir / 'work'
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        work_dir.chmod(0o777)
    except OSError:
        pass
    weekly_json = work_dir / 'weekly.json'

    range_label = '浙江全省（11 地市）' if is_prov else scope
    prompt = f"""执行 weekly-report SKILL：给经营周报写分析。

**先读两份文档**：
1. `/sandbox/skills/system-overview/SKILL.md`（全平台口径）
2. `/sandbox/skills/weekly-report/SKILL.md`（周报分析规范 + 你要写的「分析」键）

**输入**：`/sandbox/output/work/weekly.json`（{range_label} 周数据，本周 {week_start}~{week_end}，**无「分析」键**）。
**任务**：按 SKILL 读数据 → 写「分析」键（{'全省' if is_prov else '地市'}版键集）→ `json.dump` 写回原文件 `/sandbox/output/work/weekly.json`。
完成后 stdout 输出本周核心结论一句话。"""

    # 2+3. 算数据 + 写沙箱输入 + 启动 async agent —— 只在「尚未启动 run」时做一次;
    # 轮询/渲染分支绝不再覆写 weekly.json，避免清掉沙箱 AI 写回的「分析」键
    run_id_key = f'wk_run_{ui_session_id}'
    if run_id_key not in st.session_state:
        status_box.info("📊 步骤 1/3：SQL 算周数据…")
        try:
            rep = (gather_prov_weekly(week_end, db_path=DB_PATH) if is_prov
                   else gather_city_weekly(scope, week_end, db_path=DB_PATH))
        except Exception as e:
            status_box.error(f"❌ 数据汇总失败：{e}")
            st.session_state.pop(active_key, None)
            st.stop()
        with open(weekly_json, 'w', encoding='utf-8') as f:
            json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
        status_box.info("📦 步骤 2/3：启动服务器 AI 写分析（可关页面，后台继续）…")
        info = start_async_run(prompt, db_path=DB_PATH, output_root=SANDBOX_OUTPUT_ROOT,
                               ui_session_id=ui_session_id, timeout=DEFAULT_TIMEOUT,
                               max_turns=30, memory='2g', cpus='2')
        st.session_state[run_id_key] = info['run_id']

    poll = get_async_run_status(st.session_state[run_id_key], SANDBOX_OUTPUT_ROOT)
    bg_status = poll['status']
    n_tools = sum(1 for e in poll['events']
                  if e.get('type') == 'assistant'
                  for b in (e.get('message', {}).get('content') or [])
                  if isinstance(b, dict) and b.get('type') == 'tool_use')

    # AI 文本流
    with st.expander("🤖 AI 工作过程", expanded=(bg_status == 'running')):
        for e in poll['events']:
            if e.get('type') == 'assistant':
                for b in (e.get('message', {}).get('content') or []):
                    if isinstance(b, dict) and b.get('type') == 'text' and b.get('text'):
                        st.markdown(b['text'])

    if bg_status == 'running':
        status_box.info(f"🔄 AI 写分析中…（{len(poll['events'])} 事件 / {n_tools} 工具调用）。5 秒后刷新…")
        time.sleep(5)
        st.rerun()
    elif bg_status == 'failed':
        status_box.error(f"❌ AI 失败：{poll.get('error', '未知')}")
        if st.button("🔄 重试"):
            st.session_state.pop(run_id_key, None)
            st.rerun()
        st.stop()

    # agent 级失败(超时/docker启动失败)会 yield type=='error' 事件,但 worker 仍写 status='completed',
    # 不进上面的 failed 分支 —— 单独拦,避免静默产出「无分析」报告
    err_ev = next((e for e in poll['events'] if e.get('type') == 'error'), None)
    if err_ev:
        status_box.error(f"❌ AI 运行出错（未完成分析）：{err_ev.get('error', '未知错误')}")
        if st.button("🔄 重试", key='wk_retry_err'):
            st.session_state.pop(run_id_key, None)
            st.rerun()
        st.stop()

    # 4. agent done → 检查分析已写回 → render
    status_box.info("🎨 步骤 3/3：AI 完成，渲染 HTML + docx…")
    try:
        with open(weekly_json, encoding='utf-8') as f:
            jj = json.load(f)
        if '分析' not in jj or not jj['分析']:
            st.warning("⚠️ AI 没写回「分析」键，报告将只有数据、无分析。")
    except Exception as e:
        status_box.error(f"❌ 读回 weekly.json 失败：{e}")
        st.stop()

    # 产物写到确定性持久目录（范围+周）；同范围同周重生成 → 覆盖旧的，历史长期保留
    out_dir = _report_dir(scope, week_end)
    out_dir.mkdir(parents=True, exist_ok=True)
    if is_prov:
        base = f"全省经营周报-{week_end}"
        cmd = [sys.executable, 'render_weekly_prov.py', '--in', str(weekly_json),
               '--outdir', str(out_dir), '--basename', base]
    else:
        base = f"{scope}经营周报-{week_end}"
        cmd = [sys.executable, 'render_weekly_city.py', '--in', str(weekly_json),
               '--outdir', str(out_dir)]
    r = subprocess.run(cmd, cwd=str(REPORT_TOOLS), capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        status_box.error("❌ 渲染失败")
        st.code(r.stderr[-2000:])
        st.stop()
    status_box.success(f"✅ 完成！已保留到历史报告，可随时下载。{r.stdout.strip()}")

    # 5. 产物下载
    st.markdown("#### 📂 周报产物")
    out_files = sorted(out_dir.glob('*.html')) + sorted(out_dir.glob('*.docx'))
    for fp in out_files:
        fcols = st.columns([6, 2])
        fcols[0].markdown(f"**📄 {fp.name}** · {fp.stat().st_size/1024:.1f} KB")
        mime = ('text/html' if fp.suffix == '.html'
                else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        fcols[1].download_button("⬇️ 下载", data=fp.read_bytes(), file_name=fp.name,
                                 mime=mime, key=f"dl_{fp.name}", use_container_width=True)
    if not out_files:
        st.warning("没生成产物，看上面 stderr。")

    with st.expander("📋 数据 JSON（调试）", expanded=False):
        st.download_button("⬇️ weekly.json", data=weekly_json.read_text(encoding='utf-8'),
                           file_name=f"{base}_data.json", mime='application/json')

    # 渲染完成 → 清门控标志，后续交互不再重复走生成块（产物已入「历史报告」，随时可下）
    st.session_state.pop(active_key, None)
