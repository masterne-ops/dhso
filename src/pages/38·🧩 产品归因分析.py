#!/usr/bin/env python3
"""🧩 产品视角经营归因分析 — 选周期+范围,从产品子系列穿透,LLM 归因/归咎+证据（仅 admin）

工作流（复用周报沙箱链路）：
  1. UI：选范围（全省 / 11 地市）+ 周期（起始~结束年月）+ 榜单长度
  2. 后端：_product_attribution.build 算识别+下钻+9因素 → attribution.json（数字准）
  3. V3 沙箱 AI 跑 SKILL product-attribution：读 json → 写「总览归因」+每切片「归因」键 → 写回
  4. 后端 subprocess 调 report_tools/render_attribution → HTML(手机版) + docx
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
from _product_attribution import build  # noqa: E402
from _monthly_province_report import ZHEJIANG_CITIES  # noqa: E402

require_auth()
if not is_admin():
    st.error(f"⛔ 产品归因分析仅 admin 可用。当前：`{current_user()}` / `{get_current_role()}`")
    st.stop()

st.markdown("### 🧩 产品视角经营归因分析")
st.caption("选周期 + 范围 → 从产品子系列穿透（服务商/代理商/地市/型号），找做得好/不好的点，"
           "**LLM 归因/归咎 + 9 因素证据**。数据由 SQL 算准，归因由服务器 AI 撰写。"
           "好坏按「同比增量金额」排（对大盘的绝对贡献）。")

PROJECT_ROOT = Path(__file__).parent.parent.parent
SANDBOX_OUTPUT_ROOT = PROJECT_ROOT / 'v3' / 'sandbox-output'
SANDBOX_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
REPORTS_DIR = PROJECT_ROOT / 'v3' / 'attribution-reports'
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
REPORT_TOOLS = PROJECT_ROOT / 'report_tools'

_DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

PROMPT = """执行 product-attribution SKILL:给产品视角经营归因写「归因」文字。
先读两份文档:
1. /sandbox/skills/system-overview/SKILL.md(全平台口径)
2. /sandbox/skills/product-attribution/SKILL.md(归因规范 + 你要写的键)
输入:/sandbox/output/work/attribution.json(已含 识别+下钻+9因素,无「归因」键)。
任务:按 SKILL 读数据 → 写顶层「总览归因」+ 每个亮点/问题切片的「归因」键 →
json.dump 写回原文件 /sandbox/output/work/attribution.json。
完成后 stdout 输出本期核心结论一句话。"""

# 年月选项（2025-01 ~ 2026-12）
_YM = [f"{y}-{m:02d}" for y in (2025, 2026) for m in range(1, 13)]


def _report_dir(scope_name, ps, pe):
    return REPORTS_DIR / f"{scope_name}-{ps}_{pe}"


# ── 📂 历史报告 ──
with st.expander("📂 历史报告（已生成，随时下载）", expanded=True):
    hist = sorted([d for d in REPORTS_DIR.iterdir() if d.is_dir()],
                  key=lambda d: d.stat().st_mtime, reverse=True)
    if not hist:
        st.caption("暂无历史报告。生成后会自动保留在这里。")
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
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    scope = st.selectbox("📍 范围", ['浙江全省'] + ZHEJIANG_CITIES)
with c2:
    ps = st.selectbox("📅 起始年月", _YM, index=_YM.index('2026-01'))
with c3:
    pe = st.selectbox("📅 结束年月", _YM, index=_YM.index('2026-04'))
c4, c5 = st.columns(2)
top_n = c4.slider("🟢 亮点数", 2, 8, 3)
bottom_n = c5.slider("🔴 问题数", 2, 8, 3)
is_prov = (scope == '浙江全省')
city = None if is_prov else scope
if ps > pe:
    st.error("起始年月不能晚于结束年月")
    st.stop()
st.caption(f"本期：**{ps} ~ {pe}**（{'全省' if is_prov else scope}）· "
           f"同比对去年同期、环比对紧邻上一段。亮点/问题各取增量金额前 {top_n}/{bottom_n}。")

# ── 环境检查 ──
with st.expander("🩺 V3 沙箱环境检查", expanded=False):
    docker_ok, docker_msg = docker_available()
    st.success("✅ Docker 就绪") if docker_ok else st.error(f"❌ {docker_msg}")
    if docker_ok and image_exists(DEFAULT_IMAGE):
        st.success(f"✅ 镜像 `{DEFAULT_IMAGE}` 已构建")
    else:
        st.warning(f"⚠️ 镜像 `{DEFAULT_IMAGE}` 未构建")
    st.success("✅ Anthropic 端点已配置") if is_configured() else st.warning("⚠️ Anthropic 端点未配置（去 page 09 配）")
    st.success("✅ report_tools 就位") if (REPORT_TOOLS / 'render_attribution.py').exists() else st.error("❌ render_attribution 缺失（需 deploy 同步）")

btn_cols = st.columns([1, 1, 4])
go = btn_cols[0].button("🚀 生成归因报告", type="primary",
                        disabled=not (docker_ok and is_configured()), use_container_width=True)
preview = btn_cols[1].button("👁️ 仅预览数据", use_container_width=True)

# ── 仅预览数据（识别+下钻+因素，不进沙箱）──
if preview:
    try:
        with st.spinner("算识别+下钻+9因素中…"):
            rep = build(ps, pe, city, top_n, bottom_n, with_factors=True)
    except Exception as e:
        st.error(f"❌ 数据汇总失败：{e}")
        st.stop()
    g = [f"{x['子系列']}(+{x['增量_万']}万)" for x in rep['亮点子系列']]
    b = [f"{x['子系列']}({x['增量_万']}万)" for x in rep['问题子系列']]
    st.success(f"🟢 亮点：{' '.join(g)}　🔴 问题：{' '.join(b)}")
    st.json(rep, expanded=False)

# ── 完整生成 ──
# 点按钮只置门控标志;生成/轮询/渲染块由 session_state 门控（与 go 解耦，st.rerun() 后仍能进入）
active_key = f'_attr_active__{scope}__{ps}__{pe}__{top_n}__{bottom_n}'
if go:
    st.session_state[active_key] = True

if st.session_state.get(active_key):
    st.markdown("---")
    status_box = st.empty()

    # 1. 沙箱输入目录（session 稳定，避免 rerun 重生成）
    sess_key = f'_attr_sid__{scope}__{ps}__{pe}__{top_n}__{bottom_n}'
    if sess_key not in st.session_state:
        st.session_state[sess_key] = uuid.uuid4().hex[:12]
    ui_session_id = st.session_state[sess_key]
    work_dir = SANDBOX_OUTPUT_ROOT / ui_session_id / 'work'
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        work_dir.chmod(0o777)
    except OSError:
        pass
    attr_json = work_dir / 'attribution.json'

    # 2+3. 算数据 + 写沙箱输入 + 启动 async agent —— 只在「尚未启动 run」时做一次;
    # 轮询/渲染分支绝不再覆写 attribution.json，避免清掉沙箱 AI 写回的「归因」键
    run_id_key = f'attr_run_{ui_session_id}'
    if run_id_key not in st.session_state:
        status_box.info("📊 步骤 1/3：SQL 算识别 + 下钻 + 9 因素…")
        try:
            rep = build(ps, pe, city, top_n, bottom_n, with_factors=True)
        except Exception as e:
            status_box.error(f"❌ 数据汇总失败：{e}")
            st.session_state.pop(active_key, None)
            st.stop()
        with open(attr_json, 'w', encoding='utf-8') as f:
            json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
        status_box.info("📦 步骤 2/3：启动服务器 AI 写归因（可关页面，后台继续）…")
        info = start_async_run(PROMPT, db_path=DB_PATH, output_root=SANDBOX_OUTPUT_ROOT,
                               ui_session_id=ui_session_id, timeout=DEFAULT_TIMEOUT,
                               max_turns=30, memory='2g', cpus='2')
        st.session_state[run_id_key] = info['run_id']

    poll = get_async_run_status(st.session_state[run_id_key], SANDBOX_OUTPUT_ROOT)
    bg_status = poll['status']
    n_tools = sum(1 for e in poll['events']
                  if e.get('type') == 'assistant'
                  for b in (e.get('message', {}).get('content') or [])
                  if isinstance(b, dict) and b.get('type') == 'tool_use')

    with st.expander("🤖 AI 工作过程", expanded=(bg_status == 'running')):
        for e in poll['events']:
            if e.get('type') == 'assistant':
                for b in (e.get('message', {}).get('content') or []):
                    if isinstance(b, dict) and b.get('type') == 'text' and b.get('text'):
                        st.markdown(b['text'])

    if bg_status == 'running':
        status_box.info(f"🔄 AI 写归因中…（{len(poll['events'])} 事件 / {n_tools} 工具调用）。5 秒后刷新…")
        time.sleep(5)
        st.rerun()
    elif bg_status == 'failed':
        status_box.error(f"❌ AI 失败：{poll.get('error', '未知')}")
        if st.button("🔄 重试"):
            st.session_state.pop(run_id_key, None)
            st.rerun()
        st.stop()

    # agent 级失败(超时/docker启动失败)会 yield type=='error' 事件,但 worker 仍写 status='completed',
    # 不进上面的 failed 分支 —— 单独拦,避免静默产出「无归因」报告
    err_ev = next((e for e in poll['events'] if e.get('type') == 'error'), None)
    if err_ev:
        status_box.error(f"❌ AI 运行出错（未完成归因）：{err_ev.get('error', '未知错误')}")
        if st.button("🔄 重试", key='attr_retry_err'):
            st.session_state.pop(run_id_key, None)
            st.rerun()
        st.stop()

    # 4. 检查归因写回 → render
    status_box.info("🎨 步骤 3/3：AI 完成，渲染 HTML + docx…")
    try:
        with open(attr_json, encoding='utf-8') as f:
            jj = json.load(f)
        if '总览归因' not in jj or not jj.get('总览归因'):
            st.warning("⚠️ AI 没写回「总览归因」，报告将只有数据、无归因。")
    except Exception as e:
        status_box.error(f"❌ 读回 attribution.json 失败：{e}")
        st.stop()

    out_dir = _report_dir(scope, ps, pe)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"产品归因-{scope}-{ps}_{pe}"
    cmd = [sys.executable, 'render_attribution.py', '--in', str(attr_json),
           '--outdir', str(out_dir), '--basename', base]
    r = subprocess.run(cmd, cwd=str(REPORT_TOOLS), capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        status_box.error("❌ 渲染失败")
        st.code(r.stderr[-2000:])
        st.stop()
    status_box.success(f"✅ 完成！已保留到历史报告，可随时下载。{r.stdout.strip()}")

    # 5. 产物下载
    st.markdown("#### 📂 归因报告产物")
    out_files = sorted(out_dir.glob('*.html')) + sorted(out_dir.glob('*.docx'))
    for fp in out_files:
        fcols = st.columns([6, 2])
        fcols[0].markdown(f"**📄 {fp.name}** · {fp.stat().st_size/1024:.1f} KB")
        mime = 'text/html' if fp.suffix == '.html' else _DOCX_MIME
        fcols[1].download_button("⬇️ 下载", data=fp.read_bytes(), file_name=fp.name,
                                 mime=mime, key=f"dl_{fp.name}", use_container_width=True)
    if not out_files:
        st.warning("没生成产物，看上面 stderr。")

    with st.expander("📋 数据 JSON（调试）", expanded=False):
        st.download_button("⬇️ attribution.json", data=attr_json.read_text(encoding='utf-8'),
                           file_name=f"{base}_data.json", mime='application/json')

    # 渲染完成 → 清门控标志，后续交互不再重复走生成块（产物已入「历史报告」，随时可下）
    st.session_state.pop(active_key, None)
