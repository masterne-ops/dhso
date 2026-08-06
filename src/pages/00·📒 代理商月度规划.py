# -*- coding: utf-8 -*-
"""代理商月度规划（admin）

工作流第③步：接城市下发的SO任务 → 定本月关键指标(激活/留存/升档/品类)+激励单价 → 保存（回传城市汇总）。
与「城市月度规划」同源（共享 dealer_month_plan 账本）。
"""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from _auth import require_auth, is_admin  # noqa: E402
from _loaders import DB_PATH  # noqa: E402
import _battle_plan as bp  # noqa: E402
import _city_plan as cp  # noqa: E402

require_auth()
if not is_admin():
    st.error("🔒 代理商月度规划仅管理员（admin）可访问")
    st.stop()

st.markdown("### 📒 代理商月度规划")
st.caption("接城市下发的SO任务 → 定本月关键指标(激活/留存/升档/品类)+激励 → 保存（自动汇总到城市页）。激励=基数×单价。")

DEF_PRICE = {'激活': 60, '留存': 360, '升档': 110, '无线': 4, '夜视王': 3, '场景化': 1.5}
DEFAULT_ON = {'激活', '留存', '升档'}


@st.cache_data(ttl=300)
def _cities():
    cc = sqlite3.connect(str(DB_PATH))
    try:
        return cp.city_list(cc)
    finally:
        cc.close()


conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

c1, c2, c3 = st.columns(3)
city = c1.selectbox("城市", _cities(), index=0)
month = int(c2.selectbox("月份", list(range(1, 13)), index=5))
dealers = cp.city_dealers(conn, city)
if not dealers:
    st.warning("该城市暂无代理商数据。")
    st.stop()
dealer = c3.selectbox("代理商", dealers)

# 默认值：recommend + build（背景测算） + 已保存的规划
rec = bp.recommend_targets(conn, dealer)
plan = bp.build_plan(conn, dealer, month, so_year_wan=rec['so_year'], si_year_wan=rec['si_year'], price=180,
                     tier_year={'V2': rec['v2'], 'V3': rec['v3'], 'V4': rec['v4']}, loyal_year=rec['loyal'],
                     cat_year={'无线': rec['wireless'] / 100, '夜视王': rec['nightking'] / 100, '场景化': rec['scenario'] / 100})
mt = {t['key']: t for t in plan['month_targets'] if t.get('key')}
so_base = round(plan['incentive']['so_base_wan'], 1)
saved = cp.get_plan(conn, city, month, dealer)

d = plan['diag']
st.caption(f"现状：成交服务商{d['tier_active']}家(V2/V3/V4={d['tiers']['V2']}/{d['tiers']['V3']}/{d['tiers']['V4']})、"
           f"月月动销{d['loyal_recent']}、激活近月{d['act_recent']} ｜ 测算6月SO{so_base}万 ｜ "
           f"{'✅ 已有保存的规划' if saved else '🆕 尚未保存（下方为测算默认值）'}")

# ── SO任务（城市下发）──
st.markdown("#### ① 本月 SO 任务（城市下发，可微调）")
s1, s2 = st.columns(2)
default_so = float(saved['so_task']) if (saved and saved['so_task']) else float(so_base)
so_task = s1.number_input("本月SO任务(万)", value=default_so, step=5.0,
                          help="默认=城市下发的SO任务；城市页拆解后会带过来")
so_price = s2.number_input("SO每台奖(元/台)", value=float(saved['so_price']) if saved else 1.0, step=0.5)
so_units = int(round(so_task * 10000 / 180))
s1.caption(f"= {so_units} 台 (@180元/台)")

# ── 关键指标 + 激励 ──
st.markdown("#### ② 本月关键指标 + 激励（勾选重点，设目标与单价）")
rows = []
for k in cp.IND_KEYS:
    sv = (saved['indicators'].get(k) if saved else None) or {}
    tgt_def = mt.get(k, {}).get('tgt', 0)
    rows.append({'指标': k, '重点': bool(sv.get('on', k in DEFAULT_ON)),
                 '本月目标': int(sv.get('tgt', tgt_def) or 0), '单位': cp.IND_UNIT[k],
                 '单价(元)': float(sv.get('price', DEF_PRICE.get(k, 0)))})
df = pd.DataFrame(rows)
edited = st.data_editor(
    df, hide_index=True, use_container_width=True, key=f"ind_{city}_{month}_{dealer}",
    column_config={
        '指标': st.column_config.TextColumn(disabled=True),
        '单位': st.column_config.TextColumn(disabled=True, width='small'),
        '重点': st.column_config.CheckboxColumn(help='勾选=本月重点激励指标'),
        '本月目标': st.column_config.NumberColumn(min_value=0),
        '单价(元)': st.column_config.NumberColumn(min_value=0.0),
    })

# 本月激励 = SO预算 + Σ(重点指标 目标×单价)
edited = edited.copy()
edited['本月激励'] = (edited['本月目标'] * edited['单价(元)'] * edited['重点'].astype(int)).round().astype(int)
so_budget = round(so_units * so_price)
ind_budget = int(edited['本月激励'].sum())
total = so_budget + ind_budget

st.dataframe(edited[['指标', '重点', '本月目标', '单位', '单价(元)', '本月激励']], hide_index=True, use_container_width=True)
m1, m2, m3 = st.columns(3)
m1.metric("SO预算", f"{so_budget:,} 元", f"{so_units}台×{so_price}")
m2.metric("指标激励", f"{ind_budget:,} 元")
m3.metric("本月合计投入", f"{total:,} 元")

note = st.text_input("备注（可选）", value=saved['note'] if saved else "")
if st.button("💾 保存本月规划", type="primary"):
    indicators = {}
    for _, r in edited.iterrows():
        indicators[r['指标']] = {'on': bool(r['重点']), 'tgt': int(r['本月目标']), 'price': float(r['单价(元)'])}
    cp.save_plan(conn, city, month, dealer, so_task, so_price, indicators, note)
    st.success(f"已保存 {dealer} {month}月规划：SO {so_task}万 + 激励 {total:,}元。已同步到城市汇总。")
conn.close()
