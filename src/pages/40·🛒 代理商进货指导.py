#!/usr/bin/env python3
"""🛒 代理商进货指导(仅 admin)— 代理商×内部型号:滞销预警 / 补货预警 / 单商透视

三源:动销=product_flow 上线(月度全量为主,窗口锚定数据水位) | 现存=最新盘库在库序列号
     −盘后已上线(序列号净额) | 进货=dealer_purchase(月粒度,滞后,仅参考)。
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin, current_user, get_current_role  # noqa: E402
from _restock_advisor import (  # noqa: E402
    get_matrix, inv_dealers, stale_alerts, restock_alerts, dealer_detail,
)

require_auth()
if not is_admin():
    st.error(f"⛔ 进货指导仅 admin 可用。当前:`{current_user()}` / `{get_current_role()}`")
    st.stop()

st.markdown("### 🛒 代理商进货指导")


@st.cache_data(ttl=600, show_spinner="📊 计算 代理商×内部型号 进销存矩阵…")
def _matrix():
    return get_matrix()


df = _matrix()
if df.empty:
    st.warning("矩阵为空 —— 检查 inventory_snapshot / product_flow 数据。")
    st.stop()

q = df['_meta_quarter'].iloc[0] if '_meta_quarter' in df.columns else None
asof = df['_meta_asof'].iloc[0] if '_meta_asof' in df.columns else '?'
n_inv = len(inv_dealers(df))
if not q or n_inv == 0:
    st.warning("⚠️ 无可用盘库季度数据 —— 现存/可售天数与双预警不可用,仅动销视角。")
st.caption(
    f"**口径**:动销 = product_flow 出库客户口径上线(**数据截至 {asof}**,30/90 天窗以此为锚);"
    f"估算现存 = **{q} 盘库在库序列号 − 盘库后已上线**(序列号级净额,"
    f"⚠️ 未含盘库日后的新进货 → 现存可能低估,补货预警偏保守);"
    f"进货 = dealer_purchase 最近 6 个可用月(数据滞后,仅参考)。"
    f"异省上线设备不在动销源内,跨省流通货的现存会偏高。"
    f"有盘库数据的代理商 **{n_inv} 家**(双预警完整),其余仅动销视角。"
)

tab1, tab2, tab3 = st.tabs(["🔴 滞销预警", "🟢 补货预警", "🔍 单代理商透视"])

# ══════════════ Tab1 滞销预警 ══════════════
with tab1:
    c1, c2, c3 = st.columns(3)
    max_s = c1.number_input("近90天动销 ≤(台)", 0, 20, 2, key='rs_maxs')
    min_u = c2.number_input("现存 ≥(台)", 1, 100, 5, key='rs_minu')
    min_v = c3.number_input("或 现存货值 ≥(元)", 0, 100000, 5000, step=1000, key='rs_minv')
    detail, prov = stale_alerts(df, max_sales90=int(max_s),
                                min_units=int(min_u), min_value=float(min_v))
    if detail.empty:
        st.success("🎉 当前阈值下无滞销预警。")
    else:
        k1, k2, k3 = st.columns(3)
        k1.metric("滞销 代理商×型号", len(detail))
        k2.metric("压货台数", int(detail['现存台数'].sum()))
        k3.metric("压货货值", f"{detail['现存货值_万'].sum():,.1f} 万")

        st.markdown("##### 🌏 全省型号视角(多家同滞销 = 产品级问题)")
        st.dataframe(prov.head(30), use_container_width=True, hide_index=True,
                     height=min(400, 42 + 35 * len(prov.head(30))))
        st.markdown("##### 📋 分代理商明细(按严重度 = 货值×库龄)")
        st.dataframe(detail, use_container_width=True, hide_index=True, height=460)
        st.download_button(
            "⬇️ 滞销明细 CSV", detail.to_csv(index=False).encode('utf-8-sig'),
            file_name="滞销型号预警.csv", mime='text/csv')
        st.caption("「呆滞清单=True」= 该 代理商×型号 已在呆滞清理专项中,勿重复推进货。")

# ══════════════ Tab2 补货预警 ══════════════
with tab2:
    c1, c2, c3 = st.columns(3)
    min_30 = c1.number_input("近30天动销 ≥(台)", 1, 50, 3, key='rs_min30')
    d_th = c2.number_input("可售天数 <", 3, 60, 15, key='rs_dth')
    t_days = c3.number_input("补到目标库存(天)", max(15, int(d_th)), 90,
                             max(30, int(d_th)), key='rs_target')
    alerts = restock_alerts(df, min_sales30=int(min_30),
                            days_threshold=int(d_th), target_days=int(t_days))
    if alerts.empty:
        st.success("🎉 当前阈值下无补货预警。")
    else:
        k1, k2, k3 = st.columns(3)
        k1.metric("需补货 代理商×型号", len(alerts))
        k2.metric("建议补货合计", f"{int(alerts['建议补货量'].sum())} 台")
        k3.metric("涉及代理商", alerts['代理商'].nunique())
        st.dataframe(alerts, use_container_width=True, hide_index=True, height=480)
        st.download_button(
            "⬇️ 补货建议 CSV", alerts.to_csv(index=False).encode('utf-8-sig'),
            file_name="补货建议清单.csv", mime='text/csv')
        st.caption(
            f"建议补货量 = ({t_days}天 − 可售天数) × 日均动销(近30天),向上取整;"
            "停售/退市型号自动拦截为 0。现存低估时建议量偏大,下单前结合台账核一眼。")

# ══════════════ Tab3 单代理商透视 ══════════════
with tab3:
    dealers = sorted(df['代理商'].dropna().unique())
    has_inv = inv_dealers(df)
    sel = st.selectbox(
        "选代理商", dealers,
        format_func=lambda d: f"{d}{'' if d in has_inv else '　⚠️无盘库'}",
        key='rs_dealer')
    kw = st.text_input("型号搜索(内部/外部型号模糊)", key='rs_kw',
                       placeholder="如 2449 / IPC-HFW")
    if sel:
        d = dealer_detail(df, sel)
        if kw:
            m = (d['内部型号'].fillna('').astype(str)
                 .str.contains(kw, case=False, regex=False)
                 | d['外部型号'].fillna('').astype(str)
                 .str.contains(kw, case=False, regex=False))
            d = d[m]
        if sel not in has_inv:
            st.warning("该代理商未参加最新盘库,「现存/可售天数」不可估,仅动销与进货参考。")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("在售型号(近90天)", int((d['动销90'] > 0).sum()))
        k2.metric("近30天动销", int(d['动销30'].sum()))
        k3.metric("现存台数", int(d['现存台数'].sum()))
        k4.metric("现存货值", f"{d['现存货值_万'].sum():,.1f} 万")
        st.dataframe(d, use_container_width=True, hide_index=True, height=520)
        st.download_button(
            "⬇️ 该商明细 CSV", d.to_csv(index=False).encode('utf-8-sig'),
            file_name=f"进销存-{sel}.csv", mime='text/csv')
