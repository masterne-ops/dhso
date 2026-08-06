#!/usr/bin/env python3
"""🏘 区县全景图"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import DB_PATH, scoped_cities  # noqa: E402
from _monthly_city_report import list_cities, list_months  # noqa: E402
from _panorama import gather_panorama, list_districts  # noqa: E402
from _panorama_ui import render_panorama  # noqa: E402

require_auth()

st.markdown("### 🏘 区县全景图")
st.caption("选地市 + 区县 + 时间窗 → 看该区县的 SO 增长 / 服务商质量 / 红包 / 跑动")

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
def cached_districts(city):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return list_districts(conn, city)
    finally:
        conn.close()


c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
with c1:
    city = st.selectbox("📍 地市", cities,
                        index=cities.index('杭州市') if '杭州市' in cities else 0,
                        key='dp_city')
with c2:
    districts = cached_districts(city)
    if not districts:
        st.error(f"{city} 没有区县数据")
        st.stop()
    district = st.selectbox("🏘 区县", districts, key='dp_district')
with c3:
    period_start = st.selectbox("📅 开始月份", months,
                                index=max(0, len(months) - 4))
with c4:
    period_end = st.selectbox("📅 结束月份", months, index=len(months) - 1)

if period_start > period_end:
    st.error("开始月份必须 ≤ 结束月份")
    st.stop()


@st.cache_data(ttl=600, show_spinner="正在汇总区县全景数据……")
def cached_panorama(city, district, period_start, period_end):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return gather_panorama(conn, city, district=district,
                               period_start=period_start, period_end=period_end)
    finally:
        conn.close()


data = cached_panorama(city, district, period_start, period_end)
render_panorama(data)
