# -*- coding: utf-8 -*-
"""🩺 代理商经营诊断 —— 对全省代理商按规模/设备/人员聚类，以3个匿名标杆(标杆1/2/3)对标。
选客户+对标对象 → 点按钮调 LLM 写诊断小结/追赶里程碑(套报告写作规范) → 雷达评分图 HTML，可导出。
仅 admin 可用。与本机 skill「代理商诊断」同一套口径与写法。"""
import re
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin  # noqa: E402

require_auth()
if not is_admin():
    st.error("🔒 本功能仅管理员（admin）可用。")
    st.stop()

import _dealer_diag as DG  # noqa: E402

st.title("🩺 代理商经营诊断 · 对标分析")
st.caption("全省代理商按规模/设备/人员聚类，以 3 个匿名标杆(标杆1/标杆2/标杆3)对标。"
           "选客户 + 对标对象 → 点按钮调 LLM 写诊断小结/追赶里程碑 → 雷达评分图，可导出图片。"
           "切换对标对象后再点一次按钮即重新生成。")

CITYORDER = ['杭州市', '宁波市', '温州市', '嘉兴市', '湖州市', '绍兴市', '金华市', '衢州市', '台州市', '丽水市', '舟山市']


def short(n):
    n = re.sub(r'（[^）]*）', '', n)
    n = re.sub(r'(科技|电子|安防设备|智能科技|网络科技|信息技术|信息科技|科技发展|供应链管理|商贸)?有限(责任)?公司$', '', n)
    return n or n


@st.cache_data(ttl=1800, show_spinner="① 取数 + 聚类（生产库）…")
def load_data(db, dbt):     # dbt(数据库时间)进缓存键：DB 更新即自动重取
    return DG.extract(db)


cc1, cc2 = st.columns([4, 1])
cc2.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
if cc2.button("↻ 刷新数据", use_container_width=True):
    load_data.clear()

DBT = DG.db_time(DG.PROD_DB)
try:
    data = load_data(DG.PROD_DB, DBT)
except Exception as e:
    st.error(f"取数失败：{e}（确认生产库 {DG.PROD_DB} 可读）")
    st.stop()

dealers = data['dealers']
cc1.caption(f"数据：本年度({data['meta']['本年度起']}起) · 数据库时间 {DBT} · 全省 {len(dealers)} 家（含3标杆）· 标杆 {'/'.join(data['meta']['标杆'])}")

c1, c2, c3 = st.columns([1, 1.8, 1])
cities = ['全部地市'] + sorted({x['城市'] for x in dealers if x['城市']},
                             key=lambda c: (CITYORDER.index(c) if c in CITYORDER else 99))
city = c1.selectbox("地市", cities)
pool = sorted([x for x in dealers if city == '全部地市' or x['城市'] == city], key=lambda x: -x['SO']['万'])
names = [x['名称'] for x in pool]
client = c2.selectbox("客户", names, format_func=short)
bench = c3.selectbox("对标对象", ['标杆1', '标杆2', '标杆3'])

st.caption(f"📑 报告索引 = 代理商「{short(client) if client else '—'}」 ｜ 对标「{bench}」 ｜ 数据库时间「{DBT}」。"
           "同索引已生成过的第二次直接复用缓存、不再调 LLM（省 token）。")
b1, b2 = st.columns([3, 1.4])
go = b1.button("🔄 生成诊断（同索引秒出·不耗 token）", type="primary", use_container_width=True)
force = b2.checkbox("强制重新生成", value=False, help="忽略缓存、重新调 LLM 生成小结/里程碑")

if go and client:
    bar = st.progress(0, "准备…")
    pmap = {"② LLM 写诊断小结/里程碑…": 60, "③ 渲染 HTML…": 90}
    try:
        html, notes, hit, dbt = DG.build(client, bench, data=data, force=force,
                                         progress=lambda m: bar.progress(pmap.get(m, 30), m))
        bar.progress(100, "完成")
        st.session_state["diag_html"] = html
        st.session_state["diag_name"] = f"{short(client)}_经营诊断_{bench}_{dbt[:10]}.html"
        if hit:
            st.success(f"✓ 命中缓存：{short(client)} 对标 {bench} · 数据库时间 {dbt} —— 未消耗 token。"
                       "（如需按最新口径更新，勾选「强制重新生成」）")
        else:
            st.success(f"✓ 新生成并已缓存：{short(client)} 对标 {bench} · 数据库时间 {dbt}。")
        with st.expander("本次诊断小结 / 里程碑（已烤入 HTML，页面内可再手改）", expanded=False):
            st.markdown(f"**诊断小结**：{notes.get('小结', '（空）')}")
            st.markdown("**追赶里程碑**：")
            st.text(notes.get('里程碑', '（空）'))
    except Exception as e:
        bar.empty()
        st.error(f"生成失败：{e}")
        st.info("若提示未配置，请先到「🛠️ AI 代码助手」侧栏配 Base URL / Token / Model；取数失败请确认生产库可读。")

if st.session_state.get("diag_html"):
    st.download_button("⬇ 下载诊断 HTML", st.session_state["diag_html"],
                       file_name=st.session_state["diag_name"], mime="text/html",
                       use_container_width=True)
    with st.expander("预览（HTML 内可再切地市/客户/对标、勾选隐藏排名后导出图片）", expanded=True):
        components.html(st.session_state["diag_html"], height=1000, scrolling=True)
