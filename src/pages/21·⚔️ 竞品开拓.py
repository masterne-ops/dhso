#!/usr/bin/env python3
"""⚔️ 竞品 Top 服务商开拓 — 海康/宇视等竞品核心服务商的开拓管理"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin, get_current_scope  # noqa: E402
from _loaders import DB_PATH, scoped_cities  # noqa: E402
from _monthly_city_report import list_cities  # noqa: E402
from _ai_log import list_competitor_top, bulk_insert_tasks  # noqa: E402

require_auth()

st.markdown("### ⚔️ 竞品 Top 服务商开拓")
st.caption(
    "海康/宇视等竞品的核心服务商，**资源应优先倾斜**，把竞品份额转化为大华份额。"
    "由业务方在「数据导入」页上传 Excel（如 `竞品top.xlsx`）维护。"
)

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.0rem !important; line-height: 1.1; }
[data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
</style>
""", unsafe_allow_html=True)


# ───────── 顶部筛选 ─────────
try:
    cities = ['全部'] + scoped_cities(list_cities())
except Exception:
    cities = ['全部']

c1, c2, c3 = st.columns([2, 2, 2])
with c1:
    city = st.selectbox("📍 地市", cities,
                        index=cities.index('杭州市') if '杭州市' in cities else 0)
with c2:
    status_filter = st.selectbox(
        "在售大华状态",
        ['全部', '✅ 已混卖大华（开拓难度低）', '❌ 纯卖竞品（开拓难度高）'],
    )
with c3:
    min_volume = st.number_input("最小竞品体量（万）", min_value=0, value=0, step=10)


# 拉数据 — 不能用 @st.cache_data（不同用户 scope 不同会串味）
# 数据本身很小（competitor_top_provider 全表 ~70 行），每次 query 都很快
df = list_competitor_top(city=None if city == '全部' else city)

# 🔒 非 admin：post-fetch 应用 scope 过滤
if not is_admin() and not df.empty:
    allowed = set(scoped_cities(list_cities()))
    df = df[df['城市'].astype(str).isin(allowed)]

if df.empty:
    st.info("还没有竞品 Top 数据。请到主页「📥 数据导入」上传 `⚔️ 竞品 Top 服务商` Excel。")
    st.stop()

# 二次过滤
if status_filter == '✅ 已混卖大华（开拓难度低）':
    df = df[df['在售大华'] == 1]
elif status_filter == '❌ 纯卖竞品（开拓难度高）':
    df = df[df['在售大华'] == 0]
if min_volume > 0:
    df = df[df['竞品体量_万'].fillna(0) >= min_volume]


# ───────── KPI 卡 ─────────
mc = st.columns(4)
mc[0].metric("竞品 Top 客户数", len(df))
mc[1].metric("竞品体量合计", f"{df['竞品体量_万'].fillna(0).sum():,.0f} 万")
n_mixed = int((df['在售大华'] == 1).sum())
mc[2].metric("✅ 已混卖大华", f"{n_mixed} ({n_mixed*100/max(1,len(df)):.0f}%)")
mc[3].metric("❌ 纯卖竞品", f"{len(df) - n_mixed}",
              help="开拓难度高但战略意义大")


# ───────── 城市 × 体量分布 ─────────
st.markdown("##### 🌆 城市分布（按竞品体量降序）")
city_dist = (df.groupby('城市')
               .agg(客户数=('客户编码', 'count'),
                    竞品体量_万=('竞品体量_万', 'sum'),
                    已混卖大华=('在售大华', 'sum'))
               .reset_index()
               .sort_values('竞品体量_万', ascending=False))
city_dist['未合作'] = city_dist['客户数'] - city_dist['已混卖大华']
st.dataframe(
    city_dist[['城市', '客户数', '竞品体量_万', '已混卖大华', '未合作']],
    use_container_width=True, hide_index=True,
)


# ───────── 主表 ─────────
st.markdown("##### 📋 详细清单（按竞品体量降序）")
df_show = df.copy()
df_show['🏷'] = df_show['在售大华'].apply(
    lambda x: '✅ 已混卖' if x == 1 else '❌ 纯竞品'
)
# 🏷️ 加全量标签列（看现状一目了然：除竞品Top 外还可能挂着 伞形/低效/无意向 等）
try:
    from _tag_widget import attach_tags_column
    df_show = attach_tags_column(df_show, code_col='客户编码')
except Exception:
    pass
show_cols = [
    '🏷', '客户名称', '城市', '区县',
    '客户经营品牌', '竞品体量_万',
    '责任人姓名', '责任人角色',
    '老板姓名', '老板手机号',
    '拜访内容', '转化策略', '备注',
]
# 把全标签列放到 🏷 后
if '🏷️ 标签' in df_show.columns:
    show_cols = ['🏷', '🏷️ 标签'] + [c for c in show_cols if c != '🏷']
show_cols = [c for c in show_cols if c in df_show.columns]
df_show = df_show.sort_values('竞品体量_万', ascending=False, na_position='last')

st.dataframe(
    df_show[show_cols],
    use_container_width=True, hide_index=True,
    height=min(500, 40 + 35 * len(df_show)),
)

csv = df_show[show_cols].to_csv(index=False).encode('utf-8-sig')
st.download_button("⬇️ 下载 CSV", csv,
                   file_name=f"竞品Top_{city}.csv", mime='text/csv')


# ───────── 一键派任务 ─────────
st.markdown("---")
st.markdown("##### 🚀 一键派给责任人（写入跑动任务）")
st.caption(
    "把当前筛选的客户作为「⚔️ 竞品开拓」类任务派给表里的责任人，写入 `salesperson_task`。"
    "下月业务员在「跑动任务管理」就能看到。"
)

if is_admin():
    months = [str(p) for p in pd.period_range(
        pd.Period(pd.Timestamp.today(), freq='M'),
        pd.Period(pd.Timestamp.today(), freq='M') + 3, freq='M',
    )]
    pcols = st.columns([2, 1, 1])
    target_month = pcols[0].selectbox("派往月份", months, index=1)
    n_to_dispatch = pcols[1].metric("当前筛选数", len(df_show))
    if pcols[2].button("🚀 派任务", type='primary'):
        records = []
        for _, r in df_show.iterrows():
            if not r.get('责任人姓名'):
                continue
            records.append({
                '任务年月': target_month,
                '地市': r['城市'],
                '业务员': r['责任人姓名'],
                '客户编码': str(r['客户编码']),
                '客户名称': r['客户名称'],
                '区县': r.get('区县'),
                '所属一级客户': None,
                '任务类型': '⚔️ 竞品开拓',
                '任务说明': (
                    f"{'已混卖' if r['在售大华'] else '纯竞品'} · 经营品牌: {r['客户经营品牌']} · "
                    f"竞品体量 {r['竞品体量_万']:.0f} 万 · "
                    f"策略: {r.get('转化策略', '') or '-'}"
                )[:200],
                '优先级': '🚨 高',
                '备注': r.get('拜访内容'),
            })
        n = bulk_insert_tasks(records)
        st.success(
            f"✅ 派任务 {n} 条到 {target_month}（含 {len(records) - n} 条因重复跳过）。"
            "去「📋 跑动任务管理」查看"
        )
else:
    st.info("⚠️ 仅 admin 可派任务")
