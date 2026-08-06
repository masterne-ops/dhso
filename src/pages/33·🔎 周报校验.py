"""周报校验（AI）— 粘上周+本周两份 SMB 主管周报，AI 调生产库逐项核实，出点评卡片。

流程：
  1. 粘贴上周 + 本周周报文本
  2. AI（复用已配置 LLM）用 generic_sql 等工具查生产库核实数据真假、口径、待办兑现
  3. 渲染点评 HTML 卡片（评级徽章 + 彩色待办 + 导出长图）
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).parent.parent))

from _auth import require_auth  # noqa: E402
require_auth()

from _auth import is_admin, current_user, get_current_role  # noqa: E402
if not is_admin():
    st.error("🔒 本页仅管理员可用。")
    st.caption(f"当前用户: `{current_user()}`，角色: `{get_current_role()}`")
    st.stop()

from _anthropic_client import is_configured, get_config  # noqa: E402
from _weekly_check import run_check, render_check_html  # noqa: E402

st.markdown("### 🔎 周报校验")
st.caption("粘贴上周 + 本周两份 SMB 主管周报，AI 调生产库逐项核实数据真假、口径、上周待办兑现，输出点评。")

if not is_configured():
    st.warning("⚠️ 尚未配置 LLM。请到侧栏 **「AI 代码助手」** 填 Base URL / Token / Model。")
    st.stop()
cfg = get_config()
st.success(f"✅ 当前 LLM（复用 AI 代码助手）：{cfg['base_url']} · 模型 `{cfg['model']}`")

st.divider()

# ── 输入 ──
c1, c2 = st.columns(2)
with c1:
    st.markdown("#### 上周周报")
    last_week = st.text_area("上周周报原文", height=300, key='wc_last',
                             placeholder="粘贴上周（更早一周）的周报。用于核对上周待办本周兑现情况。可留空。")
with c2:
    st.markdown("#### 本周周报")
    this_week = st.text_area("本周周报原文", height=300, key='wc_this',
                             placeholder="粘贴本周周报（必填）。")

oc1, oc2 = st.columns([1, 2])
with oc1:
    week_end = st.text_input("本周截止日 YYYY-MM-DD", value=datetime.now().strftime('%Y-%m-%d'),
                             key='wc_end', help="本周=该日往前7天，帮 AI 锚定时间窗口")
with oc2:
    note = st.text_input("补充说明（可选）", key='wc_note',
                         placeholder="如：本月该片区SO目标已调整为 XX 万（优先级最高，AI 会以此为准）")

if st.button("🚀 开始校验（约 1-3 分钟，AI 会多轮查库）", type='primary',
             disabled=not this_week.strip()):
    prog = st.progress(0.0, text="AI 启动中…")
    log_box = st.expander("🔧 AI 查库过程（实时）", expanded=False)
    steps = {'n': 0}

    def on_tool(name, args, result):
        steps['n'] += 1
        prog.progress(min(steps['n'] / 15.0, 0.95), text=f"AI 第 {steps['n']} 次查库：{name}")
        with log_box:
            sql = args.get('sql', '') if isinstance(args, dict) else ''
            st.caption(f"**{steps['n']}. {name}** `{sql[:160]}`")

    t0 = time.time()
    try:
        out = run_check(last_week, this_week, week_end, note, on_tool_call=on_tool)
    except Exception as e:
        prog.empty()
        st.error(f"校验失败：{e}")
        st.stop()
    prog.progress(1.0, text=f"完成 · {time.time()-t0:.0f}s · 查库 {len(out['tool_calls'])} 次")

    if out.get('error'):
        st.error(f"LLM 报错：{out['error']}")
    R = out.get('result')
    if not R:
        st.warning("AI 没有输出结构化结果。下面是原始回复：")
        st.text(out.get('raw', '')[:5000])
        st.stop()

    st.session_state['wc_result'] = R

# ── 展示结果 ──
if 'wc_result' in st.session_state:
    R = st.session_state['wc_result']
    st.divider()
    grade = R.get('评级', '')
    st.markdown(f"## 校验结论：**{R.get('片区','')}** · 评级 **{grade}**")
    st.info(R.get('总评', ''))

    # 渲染 HTML 卡片（含导出长图按钮），用 components 内嵌
    html = render_check_html(R, standalone=True)
    # 估算高度
    n_items = sum(len(R.get(k) or []) for k in
                  ['待办兑现', '数据真实性', '口径问题', '漏项', '闭环', '建议'])
    height = 360 + n_items * 64
    components.html(html, height=min(height, 4000), scrolling=True)

    st.download_button("⬇️ 下载点评 HTML", data=html,
                       file_name=f"周报校验-{R.get('片区','')}-{R.get('周次','')}.html",
                       mime="text/html")
    st.caption("提示：HTML 里点右上角「📷 导出长图」可存成图片转发。")
