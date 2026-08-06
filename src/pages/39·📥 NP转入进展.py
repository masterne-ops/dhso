#!/usr/bin/env python3
"""📥 NP 转入客户进展 — 管理者看 NP 流转/一站式转入客户的转化漏斗与明细。

数据源:np_transfer_customer(migrations/load_np_transfer.py 入库,含新批「NP流转/一站式」
全字段 + 旧批「NP流转名单」公共字段,按数据时点/来源批次区分)。
快照口径:该表是快照表(PK=数据时点+外部客户名称,多期累积),默认「最新」= 每个来源
只取其最新数据时点的行,再按外部客户名称去重(跨来源同名保留字段更全的新批行);
选具体数据时点则查看该期历史快照(同样按外部客户名称去重)。
漏斗口径:
  转入   = 当前口径全部(见上)
  已报备 = 客户状态 = '已报备'
  相关   = 与我司业务相关 = 'Y'
  已签约 = 客户名称(内部签约名)命中 provider_contract
  已激活 = 该签约名 install_redpack 累计上线金额 ≥ 1000
"""
import sys
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin, current_user, get_current_role  # noqa: E402

DB_PATH = Path(__file__).parent.parent.parent / "db" / "product_flow.db"
ACT_THRESHOLD = 1000

require_auth()
if not is_admin():
    st.error(f"⛔ NP 转入进展仅 admin 可用。当前：`{current_user()}` / `{get_current_role()}`")
    st.stop()
st.markdown("### 📥 NP 转入客户进展")
st.caption(
    "NP 流转/一站式转入客户的转化漏斗与明细。"
    "转入 → 已报备 → 与我司相关 → 已签约(命中签约表) → 已激活(累计上线 ≥ 1000)。"
)


@st.cache_data(ttl=300, show_spinner="正在汇总 NP 转入进展…")
def load_np():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        t = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='np_transfer_customer'"
        ).fetchone()
        if not t:
            return None
        df = pd.read_sql("SELECT * FROM np_transfer_customer", conn)
        pc = set(r[0] for r in conn.execute("SELECT 客户名称 FROM provider_contract"))
        amt = {r[0]: (r[1] or 0) for r in conn.execute(
            "SELECT 上线客户名称, SUM(产品现有分销价) FROM install_redpack GROUP BY 1")}
    finally:
        conn.close()
    df['_已签约'] = df['客户名称'].apply(lambda x: bool(x) and x in pc)
    df['_已激活'] = df['客户名称'].apply(lambda x: bool(x) and amt.get(x, 0) >= ACT_THRESHOLD)
    return df


df = load_np()
if df is None or df.empty:
    st.warning("np_transfer_customer 表暂无数据。请先用 `migrations/load_np_transfer.py` 入库。")
    st.stop()

# ── 筛选 ──
fc = st.columns([1.1, 1.5, 1.4, 1, 1])
snap_opts = ['最新'] + sorted(df['数据时点'].dropna().astype(str).unique().tolist(), reverse=True)
snap = fc[0].selectbox("数据时点", snap_opts,
                       help="最新=每个来源取其最新快照(当前口径);选具体时点查看该期历史快照")
src_opts = ['全部'] + sorted(df['客户来源'].dropna().unique().tolist())
src = fc[1].selectbox("来源批次", src_opts)
city_opts = ['全部'] + sorted(df['市'].dropna().unique().tolist())
city = fc[2].selectbox("地市", city_opts)
trans = fc[3].selectbox("是否转出", ['全部', 'Y', 'N'])
signed_f = fc[4].selectbox("签约", ['全部', '已签约', '未签约'])

# 当前口径:快照表多期累积,每个来源只取其最新数据时点(或指定时点);
# 再按外部客户名称去重,跨来源同名保留数据时点更新、字段更全的新批行
if snap == '最新':
    cur = df[df['数据时点'] == df.groupby('客户来源')['数据时点'].transform('max')]
else:
    cur = df[df['数据时点'].astype(str) == snap]
cur = cur.assign(_填充=cur.notna().sum(axis=1)).sort_values(
    ['数据时点', '_填充'], ascending=False).drop_duplicates('外部客户名称').drop(columns='_填充')

f = cur.copy()
if src != '全部':
    f = f[f['客户来源'] == src]
if city != '全部':
    f = f[f['市'] == city]
if trans != '全部':
    f = f[f['是否已转出'] == trans]
if signed_f == '已签约':
    f = f[f['_已签约']]
elif signed_f == '未签约':
    f = f[~f['_已签约']]

# ── 漏斗 ──
st.markdown("#### 📊 转化漏斗")
n_in = len(f)
n_rep = int((f['客户状态'] == '已报备').sum())
n_rel = int((f['与我司业务相关'] == 'Y').sum())
n_sign = int(f['_已签约'].sum())
n_act = int(f['_已激活'].sum())
c = st.columns(5)
c[0].metric("转入", f"{n_in}")
c[1].metric("已报备", f"{n_rep}", help="客户状态=已报备")
c[2].metric("与我司相关", f"{n_rel}", help="与我司业务相关=Y")
c[3].metric("已签约", f"{n_sign}", help="客户名称命中 provider_contract")
c[4].metric("已激活", f"{n_act}", help=f"累计上线 ≥ {ACT_THRESHOLD}")
if n_in:
    st.caption(f"签约率 {n_sign / n_in * 100:.1f}%　·　激活率 {n_act / n_in * 100:.1f}%　·　已转出 {int((f['是否已转出'] == 'Y').sum())} 家")

# ── 来源批次概览 ──
st.markdown("#### 🗂️ 来源批次")
src_agg = df.groupby(['客户来源', '数据时点']).agg(
    转入=('外部客户名称', 'count'),
    已签约=('_已签约', 'sum'),
    已激活=('_已激活', 'sum'),
).reset_index().sort_values('数据时点', ascending=False)
st.dataframe(src_agg, use_container_width=True, hide_index=True)

# ── 地市分布 ──
st.markdown("#### 🗺️ 地市分布")
city_agg = f.groupby('市').agg(
    转入=('外部客户名称', 'count'),
    已报备=('客户状态', lambda s: int((s == '已报备').sum())),
    已签约=('_已签约', 'sum'),
    已激活=('_已激活', 'sum'),
).reset_index().sort_values('转入', ascending=False)
st.dataframe(city_agg, use_container_width=True, hide_index=True)

# ── 明细 ──
st.markdown(f"#### 📋 明细（{len(f)} 家）")
show = f[['外部客户名称', '客户名称', '市', '区县', '客户状态', '是否已转出',
          '与我司业务相关', '责任分销经理', '客户联系人电话', '客户来源', '数据时点']].copy()
show.insert(2, '已签约', f['_已签约'].map({True: '✅', False: ''}).values)
show.insert(3, '已激活', f['_已激活'].map({True: '✅', False: ''}).values)
st.dataframe(show, use_container_width=True, hide_index=True)
