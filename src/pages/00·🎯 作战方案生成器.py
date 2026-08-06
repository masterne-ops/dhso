# -*- coding: utf-8 -*-
"""代理商作战方案生成器（admin）

输入代理商 + 目标月 + 年度目标参数 → 一键产出：
  目标全景 / 逐月SO·SI / 当月目标(基准+挑战) / 激励方案(挑战值机制) + 可下载 HTML。
后端：_battle_plan.py    方法论：月度SO作战方案/代理商目标分解与激励方法论-v1.md
"""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from _auth import require_auth, is_admin  # noqa: E402
from _loaders import DB_PATH  # noqa: E402
import _battle_plan as bp  # noqa: E402

require_auth()
if not is_admin():
    st.error("🔒 代理商作战方案生成器仅管理员（admin）可访问")
    st.stop()

st.markdown("### 🎯 代理商作战方案生成器")
st.caption("选代理商 → 调目标参数 → 出「目标全景 + 当月目标 + 激励方案（含挑战值机制）」。口径：SO=产品流向最新分销价；服务商=严口径剔马甲。")


@st.cache_data(ttl=600)
def _dealers():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return bp.list_dealers(conn, min_units=500)
    finally:
        conn.close()


@st.cache_data(ttl=600)
def _recommend(dealer):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        return bp.recommend_targets(conn, dealer)
    finally:
        conn.close()


dealers = _dealers()
if not dealers:
    st.error("未取到代理商列表，请检查数据库。")
    st.stop()

# ── 输入区 ──
c1, c2, c3 = st.columns([2, 1, 1])
default_idx = next((i for i, d in enumerate(dealers) if '万仞' in d), 0)
dealer = c1.selectbox("代理商", dealers, index=default_idx)
month = c2.selectbox("目标月", list(range(1, 13)), index=5)  # 默认6月
price = c3.number_input("均价(元/台)", value=180.0, step=10.0)

rec = _recommend(dealer)
with st.expander("年度目标参数（已按所选代理商现状自动推荐，可改）", expanded=True):
    st.caption(f"换代理商自动重算（官方政策）：SI=签约金额{rec['si_year']}万、SO=签约值×0.7={rec['so_year']}万、V档=现状{rec['tiers_now']['V2']}/{rec['tiers_now']['V3']}/{rec['tiers_now']['V4']}×1.5、月月动销=活跃{rec['active_now']}家×10%、品类=补到全省均值。以下可手改。")
    a1, a2, a3 = st.columns(3)
    so_year = a1.number_input("全年SO目标(万)", value=float(rec['so_year']), step=50.0, key=f"so_{dealer}")
    si_year = a2.number_input("全年SI目标(万)", value=float(rec['si_year']), step=50.0, key=f"si_{dealer}")
    loyal_year = a3.number_input("年底月月动销(家)", value=int(rec['loyal']), step=1, key=f"loy_{dealer}")
    b1, b2, b3 = st.columns(3)
    v2 = b1.number_input("年底V2(家)", value=int(rec['v2']), step=10, key=f"v2_{dealer}")
    v3 = b2.number_input("年底V3(家)", value=int(rec['v3']), step=5, key=f"v3_{dealer}")
    v4 = b3.number_input("年底V4(家)", value=int(rec['v4']), step=2, key=f"v4_{dealer}")
    st.caption("激活=新增V2，由 V2/V3/V4 目标经漏斗自动反推；爬坡按 6月30%/9月50%/12月100% 里程碑曲线。")
    e1, e2, e3 = st.columns(3)
    wl = e1.number_input("无线占比目标(%)", value=float(rec['wireless']), step=1.0, key=f"wl_{dealer}")
    ys = e2.number_input("夜视王占比目标(%)", value=float(rec['nightking']), step=1.0, key=f"ys_{dealer}")
    cj = e3.number_input("场景化占比目标(%)", value=float(rec['scenario']), step=1.0, key=f"cj_{dealer}")

with st.expander("挑战值机制参数", expanded=True):
    h1, h2 = st.columns(2)
    chal_mult = h1.number_input("挑战激励倍数", value=1.5, step=0.1,
                                help="冲到挑战SO目标，全维度激励×此倍数")
    chal_factor = h2.number_input("挑战SO系数", value=1.083, step=0.01,
                                  help="挑战SO目标 = 基准SO × 此系数（1.083≈基准66.5万→挑战72万）")
    st.caption("激励 = 基数(6月目标量) × 单价；总盘自动算。导出的HTML里基数/单价可逐项手改。")

go = st.button("🚀 生成作战方案", type="primary")

if go:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        with st.spinner("取数 + 测算中…"):
            plan = bp.build_plan(
                conn, dealer, int(month), year=2026,
                so_year_wan=so_year, si_year_wan=si_year, price=price,
                so_challenge_factor=chal_factor, challenge_mult=chal_mult,
                tier_year={'V2': int(v2), 'V3': int(v3), 'V4': int(v4)},
                loyal_year=int(loyal_year),
                cat_year={'无线': wl / 100, '夜视王': ys / 100, '场景化': cj / 100})
    finally:
        conn.close()

    d = plan['diag']; inc = plan['incentive']
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("成交服务商(剔马甲)", f"{d['tier_active']}家", f"V4={d['tiers']['V4']}")
    k2.metric("月月动销(近3月连续)", f"{d['loyal_recent']}家")
    k3.metric("当月激活近月均", f"{d['act_recent']}家")
    k4.metric(f"YTD SO(至{int(month)-1}月)", f"{d['ytd_so_wan']}万")

    st.markdown("#### 一、目标全景")
    st.dataframe(pd.DataFrame(plan['overview']).rename(columns={
        'dim': '维度', 'ind': '指标', 'cur': '现状', 'y26': '2026', 'y27': '2027'}),
        hide_index=True, use_container_width=True)

    st.markdown("#### 二、逐月 SO / SI")
    mo = pd.DataFrame(plan['monthly'])
    mo['节奏'] = (mo['rhythm'] * 100).round(1).astype(str) + '%'
    st.dataframe(mo[['month', '节奏', 'so_wan', 'so_units', 'si_wan']].rename(columns={
        'month': '月', 'so_wan': 'SO(万)', 'so_units': 'SO台数', 'si_wan': 'SI(万)'}),
        hide_index=True, use_container_width=True)

    st.markdown("#### 二·B、逐月渐进目标（存量/品类爬坡 · 非首月顶年底值）")
    st.caption("存量与比率类指标从现状逐月线性爬坡到年底目标；当月目标=爬坡到当月的值。")
    rp = pd.DataFrame(plan['ramp'])
    st.dataframe(rp.rename(columns={
        'month': '月', 'so_units': 'SO台', '激活': '激活(新V2)',
        '无线台': '无线', '夜视王台': '夜视王', '场景化台': '场景化'}),
        hide_index=True, use_container_width=True)

    st.markdown(f"#### 三、{int(month)}月目标（基准 / 挑战 / 实际）")
    st.caption("「实际目标」默认=基准值，可在导出的HTML里逐项填，自动带入激励表基数。")
    mt = pd.DataFrame(plan['month_targets'])
    st.dataframe(mt[['dim', 'ind', 'base', 'chal', 'tgt', 'cal']].rename(columns={
        'dim': '维度', 'ind': '指标', 'base': '基准', 'chal': '挑战', 'tgt': '实际目标(默认)', 'cal': '计量口径'}),
        hide_index=True, use_container_width=True)

    st.markdown(f"#### 四、{int(month)}月激励方案（挑战值机制）")
    st.info(f"🎯 达基准 SO **{inc['so_base_wan']}万** → 基准池 **{int(inc['base_pool_wan']*10000)}元**；"
            f"冲挑战 SO **{inc['so_chal_wan']}万** → 全维度×{inc['mult']} = **{int(inc['challenge_pool_wan']*10000)}元**")
    st.caption("公式：基准激励 = 基数(6月目标量) × 单价。导出HTML可逐项改基数/单价。")
    ic = pd.DataFrame(inc['rows'])
    st.dataframe(ic[['dim', 'form', 'qty', 'unit', 'price', 'base_amt', 'chal_amt']].rename(columns={
        'dim': '维度', 'form': '激励形式', 'qty': '基数', 'unit': '单位', 'price': '单价(元)',
        'base_amt': '基准激励(元)', 'chal_amt': '挑战激励(元)'}),
        hide_index=True, use_container_width=True)

    html = bp.render_html(plan)
    st.download_button("⬇️ 下载 HTML 方案", data=html.encode('utf-8'),
                       file_name=f"{dealer}-{2026}年{int(month)}月作战方案.html",
                       mime="text/html")
    with st.expander("预览 HTML"):
        components.html(html, height=600, scrolling=True)
