#!/usr/bin/env python3
"""🏢 代理商全景图（按一级代理商 = 所属一级客户）"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import DB_PATH, scoped_cities  # noqa: E402
from _monthly_city_report import list_cities, list_months  # noqa: E402
from _panorama import gather_panorama, list_dealers  # noqa: E402
from _panorama_ui import render_panorama  # noqa: E402

require_auth()

st.markdown("### 🏢 代理商全景图")
st.caption("地市仅用于筛选注册在当地的代理商；选中代理商后，下方统一展示该代理商的全省数据")
st.caption("📊 代理商口径:全量感知用 `product_flow.出库客户名称`(100% 填充);"
           "红包扫码用 `install_redpack.所属一级客户`")

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.0rem !important; line-height: 1.1; }
[data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
</style>
""", unsafe_allow_html=True)


try:
    cities = scoped_cities(list_cities())
    min_month, max_month = list_months()
except Exception as e:
    st.error(f"读 DB 失败：{e}")
    st.stop()


def month_range(start: str, end: str) -> list[str]:
    s = pd.Period(start, freq='M')
    e = pd.Period(end, freq='M')
    return [str(s + i) for i in range((e - s).n + 1)]


months = month_range(min_month, max_month)


@st.cache_data(ttl=600, show_spinner=False)
def cached_dealers(city):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return list_dealers(conn, city)
    finally:
        conn.close()


c1, c2, c3, c4 = st.columns([2, 3, 2, 2])
with c1:
    city = st.selectbox("📍 地市", cities,
                        index=cities.index('杭州市') if '杭州市' in cities else 0,
                        key='ap_city')
with c2:
    dealers = cached_dealers(city)
    if not dealers:
        st.error(f"{city} 没有代理商数据")
        st.stop()
    dealer = st.selectbox("🏢 一级代理商", dealers, key='ap_dealer')
with c3:
    period_start = st.selectbox("📅 开始月份", months,
                                index=max(0, len(months) - 4))
with c4:
    period_end = st.selectbox("📅 结束月份", months, index=len(months) - 1)

if period_start > period_end:
    st.error("开始月份必须 ≤ 结束月份")
    st.stop()


@st.cache_data(ttl=600, show_spinner="正在汇总代理商全景数据……")
def cached_panorama(city, dealer, period_start, period_end):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return gather_panorama(conn, city, dealer=dealer,
                               period_start=period_start, period_end=period_end)
    finally:
        conn.close()


data = cached_panorama(city, dealer, period_start, period_end)
render_panorama(data)
