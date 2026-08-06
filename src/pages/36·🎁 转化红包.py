#!/usr/bin/env python3
"""🎁 转化红包管理 — 大华业务员转化红包 配额/发放/解锁 跟踪（仅 admin）

业务：公司给业务员月配额 → 业务员发抵用券给服务商（如 50 元券=激活 5 台解锁）
      → 服务商激活够台数→券已兑换（解锁红包）。活动 2025-06 起。
数据：dahua_redpack_quota（配额,业务员×月）+ dahua_redpack_grant（发放明细,每张券）
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin, current_user, get_current_role  # noqa: E402
from _loaders import DB_PATH  # noqa: E402

require_auth()
if not is_admin():
    st.error(f"⛔ 转化红包管理仅 admin 可用。当前：`{current_user()}` / `{get_current_role()}`")
    st.stop()

st.markdown("### 🎁 转化红包管理")
st.caption("大华业务员转化红包：发抵用券（激活 N 台解锁）→ 服务商激活领红包。活动 2025-06 起。"
           "数字会随卡券状态/激活进展更新（每次入库 REPLACE 刷新）。")


@st.cache_data(ttl=300, show_spinner="读转化红包数据……")
def load():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        q = pd.read_sql("SELECT * FROM dahua_redpack_quota", conn)
        g = pd.read_sql("SELECT * FROM dahua_redpack_grant", conn)
    finally:
        conn.close()
    return q, g


try:
    q, g = load()
except Exception as e:
    st.error(f"读表失败（可能尚未入库）：{e}")
    st.stop()

g['_val'] = pd.to_numeric(g['发放红包值'], errors='coerce').fillna(0)
g['_so'] = pd.to_numeric(g['红包发放后30天内安装红包上线金额'], errors='coerce').fillna(0)
g['_月'] = pd.to_datetime(g['发放时间'], errors='coerce').dt.strftime('%Y-%m')

# ── 总览 ──
st.markdown("##### 📊 总览")
m = st.columns(5)
n_cashed = (g['卡券状态'] == '已兑换').sum()
m[0].metric("累计发券", f"{len(g):,} 张")
m[1].metric("发放总额", f"{g['_val'].sum():,.0f} 元")
m[2].metric("已兑换（解锁）", f"{n_cashed} 张", help="服务商激活够台数、已领红包")
m[3].metric("兑换率", f"{n_cashed/len(g)*100:.0f}%" if len(g) else "—")
m[4].metric("带动30天上线", f"{g['_so'].sum()/1e4:,.0f} 万", help="发券客户发放后 30 天内安装红包上线金额")

c1, c2, c3 = st.columns(3)
c1.caption("**卡券状态**：" + " · ".join(f"{k} {v}" for k, v in g['卡券状态'].value_counts().items()))
c2.caption("**发放场景**：" + " · ".join(f"{k} {v}" for k, v in g['发放场景'].value_counts().head(4).items()))
c3.caption("**券面额**：" + " · ".join(f"{int(k)}元 {v}张" for k, v in g['_val'].value_counts().items() if k > 0))

st.divider()
tab1, tab2, tab3 = st.tabs(["🏅 业务员效果", "📋 发放明细 / 待跟进", "💰 月度配额"])

# ── Tab1 业务员效果 ──
with tab1:
    st.caption("按发放人聚合：发券数 / 发放额 / 已兑换（解锁）/ 兑换率 / 带动上线。兑换率高=红包用得有效。")
    agg = g.groupby('发放人姓名').agg(
        发券数=('卡券编码', 'count'),
        发放额=('_val', 'sum'),
        已兑换=('卡券状态', lambda s: (s == '已兑换').sum()),
        带动上线万=('_so', lambda s: round(s.sum() / 1e4, 1)),
    ).reset_index()
    agg['兑换率'] = (agg['已兑换'] / agg['发券数'] * 100).round(0).astype(int).astype(str) + '%'
    agg = agg.sort_values('发券数', ascending=False)
    st.dataframe(agg, use_container_width=True, hide_index=True, height=460)

# ── Tab2 发放明细 + 待跟进 ──
with tab2:
    f = st.columns(4)
    who = f[0].selectbox("发放人", ['全部'] + sorted(g['发放人姓名'].dropna().unique().tolist()))
    stt = f[1].selectbox("卡券状态", ['全部'] + sorted(g['卡券状态'].dropna().unique().tolist()))
    scn = f[2].selectbox("发放场景", ['全部'] + sorted(g['发放场景'].dropna().unique().tolist()))
    mon = f[3].selectbox("发放月", ['全部'] + sorted(g['_月'].dropna().unique().tolist(), reverse=True))
    d = g.copy()
    if who != '全部':
        d = d[d['发放人姓名'] == who]
    if stt != '全部':
        d = d[d['卡券状态'] == stt]
    if scn != '全部':
        d = d[d['发放场景'] == scn]
    if mon != '全部':
        d = d[d['_月'] == mon]

    n_pending = d['卡券状态'].isin(['待解锁', '待兑换']).sum()
    n_expired = (d['卡券状态'] == '已失效').sum()
    if n_pending or n_expired:
        st.warning(f"⚠️ 当前筛选下 **待解锁/待兑换 {n_pending} 张**（券还在、催服务商激活够台数）、"
                   f"**已失效 {n_expired} 张**（没激活够、券已过期）—— 重点跟进对象。")
    show = d[['发放时间', '发放人姓名', '发放客户名称', '卡券名称', '卡券状态',
              '发放场景', '是否激活（实时）', '是否复购（实时）', '签约一级客户名称', '红包发放后30天内安装红包上线金额']].copy()
    show = show.rename(columns={'红包发放后30天内安装红包上线金额': '30天上线金额', '签约一级客户名称': '所属代理商'})
    st.caption(f"共 {len(show):,} 张券")
    st.dataframe(show.sort_values('发放时间', ascending=False), use_container_width=True, hide_index=True, height=460)

# ── Tab3 月度配额 ──
with tab3:
    st.caption("业务员每月红包配额 + 使用/解锁/转化情况。配额使用率 = 已用÷总额；解锁率 = 服务商解锁÷已用。")
    f2 = st.columns(2)
    qm = f2[0].selectbox("月份", ['全部'] + sorted(q['时间'].dropna().astype(str).unique().tolist(), reverse=True), key='qm')
    qp = f2[1].selectbox("分销经理", ['全部'] + sorted(q['分销经理'].dropna().unique().tolist()), key='qp')
    qd = q.copy()
    if qm != '全部':
        qd = qd[qd['时间'].astype(str) == qm]
    if qp != '全部':
        qd = qd[qd['分销经理'] == qp]
    cols = ['时间', '分销经理', '部门', '红包总额（元）', '红包使用金额（元）', '发放客户数',
            '红包发放后30天内转化激活客户数', '红包配额使用率', '服务商红包已解锁金额（元）', '服务商红包解锁率']
    cols = [c for c in cols if c in qd.columns]
    st.caption(f"共 {len(qd):,} 行")
    st.dataframe(qd[cols].sort_values('时间', ascending=False), use_container_width=True, hide_index=True, height=460)
