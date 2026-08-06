#!/usr/bin/env python3
"""⚖️ 代理商产品均衡管理 — 产品经理工作板块。

数据源: product_line_balance(RP10-SMB产品线分析-客户,智能批量导入,snapshot 按数据时点)。
政策(2026): CCTV销售额占比 ≥70% 且 (数通占比 >3% 或 配套占比 >10%)
→ 代理商拿产品均衡 1% 返点;季度结算、年度补齐(年末整体达标可补前期季度)。
数据为本年累计(到月)口径:每期快照的占比 = 年初至该时点的累计占比,
年末快照达标即年度整体达标。

占比口径: 优先用文件自带占比列;为空且实销金额>0 时按 实销金额分量/实销金额 推算。
一级代理商匹配: 客户编码命中 signed_customer_monthly(SI 签约名册);未命中的客户单独列出。
"""
import sys
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin, filter_by_scope, get_current_scope  # noqa: E402

DB_PATH = Path(__file__).parent.parent.parent / "db" / "product_flow.db"

# 政策阈值
TH_CCTV, TH_SHUTONG, TH_PEITAO = 0.70, 0.03, 0.10
REBATE = 0.01

require_auth()
st.markdown("### ⚖️ 代理商产品均衡管理")
st.caption(
    f"**政策(2026)**: CCTV 销售额占比 ≥{TH_CCTV:.0%} 且 (数通占比 >{TH_SHUTONG:.0%} 或 配套占比 >{TH_PEITAO:.0%}) "
    f"→ 产品均衡 **{REBATE:.0%} 返点**;按季度结算、**年度可补齐**(年末累计达标可补前期未达成季度)。"
    "数据 = RP10 产品线分析(本年累计到月,万元)。"
)


@st.cache_data(ttl=300, show_spinner="正在读取产品均衡数据…")
def load_data():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        t = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='product_line_balance'"
        ).fetchone()
        if not t:
            return None, set()
        df = pd.read_sql("SELECT * FROM product_line_balance", conn)
        dealers = set(str(r[0]) for r in conn.execute(
            "SELECT 客户编码 FROM signed_customer_monthly"))
    finally:
        conn.close()
    return df, dealers


df_all, dealer_codes = load_data()
if df_all is None or df_all.empty:
    st.warning("product_line_balance 暂无数据。请到「数据导入与概览」页用智能批量导入上传"
               "「RP10-SMB产品线分析-客户」Excel(自动识别,按数据时点全量更新)。")
    st.stop()

# ── 快照选择 ──
snaps = sorted(df_all['数据时点'].dropna().astype(str).unique(), reverse=True)
fc = st.columns([1.2, 1.2, 1.2, 1.4])
snap = fc[0].selectbox("数据时点(快照)", snaps, help="每次导入=一期快照;季度末导入的快照用于季度结算")
only_dealer = fc[1].selectbox("范围", ['一级代理商', '全部客户'])

df = df_all[df_all['数据时点'].astype(str) == snap].copy()
df['_是否一级代理商'] = df['客户编码'].astype(str).isin(dealer_codes)

# 权限:向所有分销经理开放,但必须有地市权限——admin 全省;
# 其他角色 scope 无 city 则拦下,有则先按权限收窄(总览/明细/下拉全部基于收窄后数据)
if not is_admin():
    _scope = get_current_scope() or {}
    if not _scope.get('city'):
        st.warning("⛔ 本页按地市权限开放:你的账号未分配地市权限,请联系管理员在「用户管理」分配。")
        st.stop()
    df = filter_by_scope(df, city_col='客户城市', district_col='客户区县')

city_opts = ['全部'] + sorted(df['客户城市'].dropna().astype(str).unique())
city_pick = fc[2].selectbox("地市", city_opts)
if city_pick != '全部':
    df = df[df['客户城市'] == city_pick]


def _ratio(row, ratio_col, amt_col):
    """占比: 文件占比优先,否则用 分量实销/实销金额 推算。返回 float 或 None。"""
    v = row[ratio_col]
    if pd.notna(v):
        return float(v)
    total = row['实销金额']
    part = row[amt_col]
    if pd.notna(total) and float(total) > 0:
        return float(part or 0) / float(total)
    return None


df['_CCTV占比'] = df.apply(lambda r: _ratio(r, 'CCTV占比', 'CCTV实销'), axis=1)
df['_配套占比'] = df.apply(lambda r: _ratio(r, '配套占比', '配套实销'), axis=1)
df['_数通占比'] = df.apply(lambda r: _ratio(r, '数通实销占比', '数通实销'), axis=1)


def _qualified(r):
    if r['_CCTV占比'] is None:
        return None
    ok = (r['_CCTV占比'] >= TH_CCTV) and (
        (r['_数通占比'] or 0) > TH_SHUTONG or (r['_配套占比'] or 0) > TH_PEITAO)
    return bool(ok)


df['_达标'] = df.apply(_qualified, axis=1)

# 范围过滤(scope 已在上方对 df 收窄)
view = df[df['_是否一级代理商']] if only_dealer == '一级代理商' else df

# ── 总览 ──
st.markdown(f"#### 📊 {snap} 快照总览")
n_dealer = int(df['_是否一级代理商'].sum())
hit = view[view['_达标'] == True]  # noqa: E712
c = st.columns(4)
c[0].metric("客户总数", f"{len(df):,}")
c[1].metric("匹配一级代理商", f"{n_dealer}", help="客户编码命中 SI 签约名册(signed_customer_monthly)")
c[2].metric("当前范围", f"{len(view)}")
c[3].metric("✅ 达标(可拿返点)", f"{len(hit)}")

# ── 明细 ──
st.markdown("#### 📋 产品均衡明细")


def _fmt_pct(v):
    return '' if v is None or pd.isna(v) else f"{v * 100:.1f}%"


show = view[['客户编码', '客户名称', '客户城市', '客户区县', '实销金额']].copy()
show['实销金额_万'] = pd.to_numeric(view['实销金额'], errors='coerce').round(1)
show['CCTV占比'] = view['_CCTV占比'].map(_fmt_pct)
show['配套占比'] = view['_配套占比'].map(_fmt_pct)
show['数通占比'] = view['_数通占比'].map(_fmt_pct)
show['达标'] = view['_达标'].map({True: '✅', False: '❌', None: '—'})
show['一级代理商'] = view['_是否一级代理商'].map({True: '⭐', False: ''})
show = show.drop(columns=['实销金额']).sort_values(
    ['达标', '实销金额_万'], ascending=[True, False])
st.dataframe(show, use_container_width=True, hide_index=True, height=420)
st.caption("达标=—:该客户无占比数据(实销为 0 或文件未给占比且无法推算)。占比为本年累计口径。")

# ── 季度快照趋势(多期后自动生效) ──
st.markdown("#### 🗓️ 各期快照达标跟踪(季度结算依据)")
if len(snaps) < 2:
    st.info("目前只有 1 期快照。之后每期(建议季度末)导入 RP10 文件,这里会累积出"
            "「代理商 × 各期快照」的达标矩阵,支撑季度结算与年度补齐核对。")
else:
    hist = df_all.copy()
    if not is_admin():
        hist = filter_by_scope(hist, city_col='客户城市', district_col='客户区县')
    hist['_是否一级代理商'] = hist['客户编码'].astype(str).isin(dealer_codes)
    hist = hist[hist['_是否一级代理商']]
    hist['_CCTV占比'] = hist.apply(lambda r: _ratio(r, 'CCTV占比', 'CCTV实销'), axis=1)
    hist['_配套占比'] = hist.apply(lambda r: _ratio(r, '配套占比', '配套实销'), axis=1)
    hist['_数通占比'] = hist.apply(lambda r: _ratio(r, '数通实销占比', '数通实销'), axis=1)
    hist['_达标'] = hist.apply(_qualified, axis=1)
    pv = hist.pivot_table(index='客户名称', columns='数据时点', values='_达标',
                          aggfunc='last')
    pv = pv.replace({True: '✅', False: '❌', None: '—'}).fillna('—')
    st.dataframe(pv, use_container_width=True)
    st.caption("年度补齐:年末快照(累计口径)达标 = 年度整体达标,前期未达成季度的返点可补发。")

# ── 未匹配客户 ──
unmatched = df[~df['_是否一级代理商']]
with st.expander(f"🔍 未匹配为一级代理商的客户({len(unmatched)} 家)"):
    um = unmatched[['客户编码', '客户名称', '客户城市', '客户区县', '实销金额']].copy()
    um['实销金额'] = pd.to_numeric(um['实销金额'], errors='coerce').round(2)
    st.dataframe(um.sort_values('实销金额', ascending=False),
                 use_container_width=True, hide_index=True, height=300)
    st.caption("这些客户在 RP10 文件中但不在 SI 签约名册(signed_customer_monthly)——"
               "多为二级/非签约客户,不参与产品均衡返点。")
