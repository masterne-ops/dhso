# -*- coding: utf-8 -*-
"""城市月度规划（admin）· 工作流枢纽

①复盘当月进展 → ②定城市SO目标 → ③把SO任务拆到各代理商(下发) → ④汇总各家激励(上收)。
与「代理商月度规划」同源（共享 dealer_month_plan 账本）：城市拆的SO任务下发给代理商页，
代理商页定的激励自动汇总回本页。
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
    st.error("🔒 城市月度规划仅管理员（admin）可访问")
    st.stop()

st.markdown("### 🏙️ 城市月度规划")
st.caption("复盘进展 → 定城市目标 → 拆SO任务到各代理商(下发) → 汇总各家激励(上收)。各家明细在「代理商月度规划」页填，本页自动汇总。")


@st.cache_data(ttl=300)
def _cities():
    cc = sqlite3.connect(str(DB_PATH))
    try:
        return cp.city_list(cc)
    finally:
        cc.close()


@st.cache_data(ttl=600)
def _defaults(city, month):
    """各代理商：签约值 + 测算6月SO(=签约×0.7×节奏)。"""
    cc = sqlite3.connect(str(DB_PATH)); cc.row_factory = sqlite3.Row
    try:
        so_r = bp.load_rhythm(cc, bp.SO_RHYTHM)
        rh = so_r.get(int(month), 0.095)
        out = {}
        for dl in cp.city_dealers(cc, city):
            rec = bp.recommend_targets(cc, dl)
            out[dl] = {'sign': rec['si_year'], 'so_est': round(rec['so_year'] * rh, 1)}
        return out
    finally:
        cc.close()


conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
c1, c2 = st.columns([2, 1])
city = c1.selectbox("城市", _cities(), index=0)
month = int(c2.selectbox("月份", list(range(1, 13)), index=5))

# ① 复盘
st.markdown("#### ① 本月复盘（实时进展）")
prog = cp.city_progress(conn, city, month)
k1, k2, k3 = st.columns(3)
k1.metric(f"{month}月SO实际(上线城市口径)", f"{prog['so_month']}万", f"{prog['so_month_units']}台")
k2.metric(f"{month}月激活(严口径)", f"{prog['act_month']}家")
k3.metric("YTD SO累计", f"{prog['so_ytd']}万")

# ② 定城市目标
st.markdown("#### ② 定城市SO目标")
defs = _defaults(city, month)
est_sum = round(sum(v['so_est'] for v in defs.values()), 1)
g1, g2 = st.columns(2)
city_target = g1.number_input(f"{city} {month}月 SO目标(万)", value=float(round(prog['so_month'] or est_sum)), step=10.0,
                              help="城市进度条目标（如杭州6月=418）；落后可加码")
g2.metric("本地代理商测算合计", f"{est_sum}万", "签约×0.7×节奏")

# ③ 拆解SO任务到代理商
st.markdown("#### ③ 拆解 SO 任务到各代理商（下发）")
dealers = cp.city_dealers(conn, city)
rows = []
for dl in dealers:
    sv = cp.get_plan(conn, city, month, dl)
    cur = sv['so_task'] if (sv and sv['so_task']) else defs.get(dl, {}).get('so_est', 0)
    rows.append({'代理商': dl, '签约(万)': defs.get(dl, {}).get('sign', 0),
                 '测算SO(万)': defs.get(dl, {}).get('so_est', 0), 'SO任务(万)': float(cur or 0)})
df = pd.DataFrame(rows)
edited = st.data_editor(df, hide_index=True, use_container_width=True, key=f"split_{city}_{month}",
                        column_config={'代理商': st.column_config.TextColumn(disabled=True),
                                       '签约(万)': st.column_config.NumberColumn(disabled=True),
                                       '测算SO(万)': st.column_config.NumberColumn(disabled=True),
                                       'SO任务(万)': st.column_config.NumberColumn(min_value=0.0)})
split_sum = round(float(edited['SO任务(万)'].sum()), 1)
gap = round(city_target - split_sum, 1)
i1, i2, i3 = st.columns(3)
i1.metric("拆解合计", f"{split_sum}万")
i2.metric("城市目标", f"{city_target:.0f}万")
i3.metric("差额=外地市/外省卖进(***)", f"{gap}万", "不可控，不拆" if gap > 0 else "已拆满")
if st.button("📤 下发SO任务到各代理商", type="primary"):
    cp.save_so_tasks(conn, city, month, {r['代理商']: r['SO任务(万)'] for _, r in edited.iterrows()})
    st.success(f"已下发 {len(edited)} 家SO任务（合计{split_sum}万）。各家到「代理商月度规划」页定关键指标+激励。")

# ④ 汇总激励（上收）
st.markdown("#### ④ 激励汇总（各家规划上收）")
roll = cp.city_rollup(conn, city, month)
if not roll:
    st.info("各代理商尚未在「代理商月度规划」页保存规划。下发SO任务后，去代理商页逐家填激励，这里自动汇总。")
else:
    rr = pd.DataFrame([{'代理商': x['dealer'], 'SO目标(万)': x['so'], 'SO台数': x['units'],
                        'SO预算(元)': x['so_budget'], '激活目标(家)': x['act_tgt'],
                        '指标激励(元)': x['ind_budget'], '本月合计(元)': x['total']} for x in roll])
    tot_so = round(rr['SO目标(万)'].sum(), 1); tot_units = int(rr['SO台数'].sum())
    tot_act = int(rr['激活目标(家)'].sum()); tot_bud = int(rr['本月合计(元)'].sum())
    st.dataframe(rr, hide_index=True, use_container_width=True)
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("本月SO目标合计", f"{tot_so}万", f"{tot_units}台")
    s2.metric("激活目标合计", f"{tot_act}家")
    s3.metric("本月激励总投入", f"{tot_bud:,}元")
    s4.metric("占城市目标", f"{(tot_so/city_target*100) if city_target else 0:.0f}%", f"外地{gap}万不算")
    st.caption(f"📌 申请：用 {tot_bud:,}元 激励本地{len(roll)}家，撬动 {tot_so}万SO + {tot_act}家激活；城市{city_target:.0f}万里另有 {gap}万 为外地卖进(不可控)。")
conn.close()
