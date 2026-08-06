#!/usr/bin/env python3
"""🌆 地市全景图 — 一页看完整城市的 SO 增长 / 服务商质量 / 红包 / 跑动"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import DB_PATH, scoped_cities  # noqa: E402
from _monthly_city_report import list_cities, list_months  # noqa: E402
from _panorama import gather_panorama  # noqa: E402
from _panorama_ui import render_panorama  # noqa: E402

require_auth()

st.markdown("### 🌆 地市全景图")
st.caption("选地市 + 时间窗 → 4 个 Tab 看完整：SO 增长 / 服务商质量 / 红包投放 / 销售跑动")

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.0rem !important; line-height: 1.1; }
[data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
</style>
""", unsafe_allow_html=True)


# 顶部选参
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

c1, c2, c3 = st.columns([2, 2, 2])
with c1:
    city = st.selectbox("📍 地市", cities,
                        index=cities.index('杭州市') if '杭州市' in cities else 0)
with c2:
    period_start = st.selectbox("📅 开始月份", months,
                                index=max(0, len(months) - 4))
with c3:
    period_end = st.selectbox("📅 结束月份", months, index=len(months) - 1)

if period_start > period_end:
    st.error("开始月份必须 ≤ 结束月份")
    st.stop()


@st.cache_data(ttl=600, show_spinner="正在汇总地市全景数据……")
def cached_panorama(city, period_start, period_end):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return gather_panorama(conn, city, period_start=period_start, period_end=period_end)
    finally:
        conn.close()


data = cached_panorama(city, period_start, period_end)
render_panorama(data)
