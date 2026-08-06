# -*- coding: utf-8 -*-
"""📋 代理商经营简报 —— 选时间范围，自动生成全省单文件 HTML。
固定代码取数(产品流向/安装红包/服务商签约/拜访) + LLM 写本周小结(套报告写作规范) + 固定渲染。
与本机 skill「代理商简报」同一套口径与写法。"""
import datetime
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin  # noqa: E402

require_auth()
if not is_admin():
    st.error("🔒 代理商经营简报仅管理员（admin）可访问")
    st.stop()

st.title("📋 代理商经营简报")
st.caption("选时间范围 → 固定口径取数 + LLM 写本周小结(套报告写作规范) → 全省单文件 HTML。"
           "可下载/预览：全部城市看分地市排名榜，选某代理商看完整经营简报，均可导出图片。月报=整月、周报=一周。")

# 小结依赖 Anthropic 配置（与「AI 代码助手」同一套），未配则小结为空
try:
    from _dealer_briefing import llm_ready as _llm_ready
    if not _llm_ready():
        st.warning("⚠️ 未检测到 LLM 配置（Anthropic），生成的「本周小结」会是空的。"
                   "请先到「🛠️ AI 代码助手」配置 Base URL / Token / Model，再回来生成。")
except Exception:
    pass

today = datetime.date.today()
last_mon = today - datetime.timedelta(days=today.weekday() + 7)   # 上周一
last_sun = last_mon + datetime.timedelta(days=6)                  # 上周日

c1, c2, c3 = st.columns([1, 1, 1.2])
start = c1.date_input("开始日期", last_mon)
end = c2.date_input("结束日期", last_sun)
c3.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
go = c3.button("🚀 生成全省简报", type="primary", use_container_width=True)

with st.expander("口径说明", expanded=False):
    st.markdown(
        "- **SO**=产品流向(出库客户=本代理商)；本市=上线城市本市，本省=上线省份浙江；金额=最新分销价/万。\n"
        "- **红包/交易服务商**=安装红包(出货客户=本代理商)。\n"
        "- **新注册**=区间内签约；**新激活**=区间内首次红包上线；**分级 V0–V5**=签约口径当前快照。\n"
        "- **跑动**=本代理商业务员有效拜访(剔异常)；**周曲线**=本年每周SO台数(高亮本期)；**排名/销冠**=城市内按红包上线金额。"
    )

if go:
    if start > end:
        st.error("开始日期不能晚于结束日期。")
        st.stop()
    try:
        from _dealer_briefing import build
    except Exception as e:
        st.error(f"加载编排模块失败：{e}")
        st.stop()
    bar = st.progress(0, "准备…")
    pmap = {"① 取数（生产库）…": 25, "② LLM 写本周小结…": 70, "③ 渲染 HTML…": 92}
    try:
        html, data = build(start.isoformat(), end.isoformat(),
                           progress=lambda m: bar.progress(pmap.get(m, 50), m))
        bar.progress(100, "完成")
        st.session_state["briefing_html"] = html
        st.session_state["briefing_name"] = f"浙江省_代理商经营简报_{start}_{end}.html"
        n = sum(len(c["dealers"]) for c in data["cities"].values())
        champs = "、".join(f"{c}:{(cd['销冠'] or '—')[:6]}" for c, cd in data["cities"].items())
        st.success(f"已生成：{len(data['cities'])} 市 / {n} 家代理商（{start}~{end}）")
        st.caption("各市销冠 — " + champs)
    except Exception as e:
        bar.empty()
        st.error(f"生成失败：{e}")
        st.info("若小结为空，多半是 LLM 未配置——到「🛠️ AI 代码助手」配置 Anthropic Base URL / Token / Model；取数失败请确认生产库可读。")

if st.session_state.get("briefing_html"):
    st.download_button("⬇ 下载 HTML（全省）", st.session_state["briefing_html"],
                       file_name=st.session_state["briefing_name"], mime="text/html",
                       use_container_width=True)
    with st.expander("预览", expanded=True):
        components.html(st.session_state["briefing_html"], height=820, scrolling=True)
