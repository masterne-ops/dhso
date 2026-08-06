#!/usr/bin/env python3
"""⏳ 待激活客户跟进 — 按 月/城市/代理商/大华业务员 看「本月待激活客户」，跟踪达标(V2)。

口径见 _pending_activation.py：
  达标 = 2026 累计 install_redpack.上线时下单价 ≥ 1000 元
  归属月 = 首次打标月；跨月未达标 → 后续月份继续显示并标「原月·未完成」。
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, current_user  # noqa: E402
from _pending_activation import (  # noqa: E402
    get_pending_board, get_client_detail,
    upsert_followup_note, get_followup_note, TARGET, YEAR,
)
from _monthly_city_report import list_months  # noqa: E402

require_auth()
st.markdown("### ⏳ 待激活客户跟进")
st.caption(
    f"按 月份 / 城市 / 代理商 / 大华业务员 看「⏳ 本月待激活客户」，"
    f"跟踪是否达标（{YEAR} 累计上线货值 ≥ {TARGET:,} 元 = V2）。"
    "归属月=首次打标月；未达标的会在后续月份继续显示并标「原月·未完成」。"
)

# ── 月份选择（2026 各月，到当前数据月）──
try:
    _min, _max = list_months()
except Exception:
    _max = f'{YEAR}-12'
months = [f'{YEAR}-{mm:02d}' for mm in range(1, 13) if f'{YEAR}-{mm:02d}' <= str(_max)]
if not months:
    months = [f'{YEAR}-01']
@st.cache_data(ttl=120, show_spinner="正在汇总待激活客户……")
def _board(_ym):
    return get_pending_board(_ym)


# ── 月份 + 城市/区县/代理商/业务员 并排一行（逐级联动）──
fc = st.columns([1.2, 1, 1, 1.4, 1.4])
ym = fc[0].selectbox("📅 月份", months, index=len(months) - 1)
board = _board(ym)
if board.empty:
    st.info(f"📭 {ym} 暂无「本月待激活客户」。去 🔭 服务商全景 给客户打上该标签后回来看。")
    st.stop()

city = fc[1].selectbox("🏙️ 城市", ['全部'] + sorted(board['城市'].dropna().unique().tolist()))
pool = board if city == '全部' else board[board['城市'] == city]
district = fc[2].selectbox("🏘 区县", ['全部'] + sorted(pool['区县'].dropna().unique().tolist()))
pool = pool if district == '全部' else pool[pool['区县'] == district]
dealer = fc[3].selectbox("🏢 代理商", ['全部'] + sorted(pool['所属代理商'].dropna().unique().tolist()))
pool = pool if dealer == '全部' else pool[pool['所属代理商'] == dealer]
owner = fc[4].selectbox("🧑‍💼 大华业务员", ['全部'] + sorted(pool['客户所有者'].dropna().unique().tolist()))

df = (pool if owner == '全部' else pool[pool['客户所有者'] == owner]).reset_index(drop=True)

# ── 汇总指标 ──
total = len(df)
done = int(df['达标'].sum())
legacy = int(df['是否遗留'].sum())
mc = st.columns(4)
mc[0].metric("待激活客户", total)
mc[1].metric("✅ 已达标", done, help=f"{YEAR} 累计上线货值 ≥ {TARGET:,} 元")
mc[2].metric("⏳ 未达标", total - done)
mc[3].metric("🔴 跨月遗留", legacy, help="更早月份打标、至今仍未达标")
if total:
    st.caption(f"达标率 {done / total * 100:.0f}%（{done}/{total}）")

# ── 清单（单选 → 下方详情）──
st.markdown("##### 📋 客户清单（点行号选中，下方出详情）")
show = df[['状态', '客户名称', '城市', '区县', '所属代理商', '客户所有者',
           '归属月', '累计货值', '台数', '备注']].copy()
show['累计货值'] = show['累计货值'].round(0)
ev = st.dataframe(
    show, use_container_width=True, height=380,
    on_select='rerun', selection_mode='single-row', key='pending_list',
)

sel_rows = ev.selection.rows if (ev and ev.selection) else []
if not sel_rows:
    st.caption("💡 选中上方任意一行，下方展示该客户的 SO 进展、跑动记录，并可填写跟进备注。")
    st.stop()

row = df.iloc[sel_rows[0]]
code = str(row['客户编码'])
name = row['客户名称']

st.divider()
st.markdown(f"#### {row['状态']}　{name}　`{code}`")
ic = st.columns(4)
ic[0].metric("城市 / 区县",
             f"{row['城市'] if pd.notna(row['城市']) else '—'} · "
             f"{row['区县'] if pd.notna(row['区县']) else '—'}")
ic[1].metric("大华业务员", row['客户所有者'] or '—')
ic[2].metric(f"{YEAR} 累计货值", f"¥{row['累计货值']:,.0f}",
             help=f"上线时下单价累计；达标线 {TARGET:,}")
ic[3].metric("上线台数", f"{row['台数']}")

# 达标进度
prog = min(1.0, row['累计货值'] / TARGET) if TARGET else 0.0
if row['达标']:
    st.progress(prog, text=f"✅ 已达标（{row['累计货值']:,.0f} / {TARGET:,}）")
else:
    st.progress(prog, text=f"⏳ 距达标还差 ¥{max(0, TARGET - row['累计货值']):,.0f}"
                           f"（{row['累计货值']:,.0f} / {TARGET:,}）")
if row['是否遗留'] and not row['达标']:
    st.warning(f"🔴 该客户 **{row['归属月']}** 就被标为待激活，至今（{ym}）仍未达标。")

# ── SO + 跑动 详情 ──
detail = get_client_detail(code, ym)
dc1, dc2 = st.columns(2)
with dc1:
    st.markdown(f"**📈 {YEAR} 月度 SO（截至 {ym}）**")
    som = detail['so_monthly']
    if som.empty:
        st.caption("（暂无上线记录 — 待激活）")
    else:
        st.bar_chart(som.set_index('月')[['货值']], height=200)
        st.dataframe(som, use_container_width=True, hide_index=True, height=150)
with dc2:
    st.markdown("**🏃 跑动记录（🏢我司 / 🏪代理商）**")
    vis = detail['visits']
    if vis.empty:
        st.caption("（暂无跑动记录）")
    else:
        piv = vis.pivot_table(index='月', columns='_打卡方', values='次数',
                              aggfunc='sum', fill_value=0)
        st.bar_chart(piv, height=200)
        n_dahua = int(vis[vis['_打卡方'].astype(str).str.contains('大华', na=False)]['次数'].sum())
        n_dealer = int(vis['次数'].sum()) - n_dahua
        st.caption(f"合计 {n_dahua + n_dealer} 次　·　🏢 我司 {n_dahua}　·　🏪 代理商 {n_dealer}")

# ── 跟进备注（按 客户×月）──
st.markdown(f"**📝 跟进备注（{ym}）**")
existing = get_followup_note(code, ym)
cur_note = existing['备注'] if existing else ''
note = st.text_area("备注内容", value=cur_note, key=f'note_{code}_{ym}',
                    placeholder="如：已电话沟通，约定本周上门；客户反馈在比价…", height=80,
                    label_visibility='collapsed')
nb1, nb2 = st.columns([1, 4])
with nb1:
    if st.button("💾 保存备注", type='primary', use_container_width=True):
        upsert_followup_note(客户编码=code, 归属年月=ym, 备注=note, 更新人=current_user())
        st.toast("✅ 备注已保存")
        st.rerun()
with nb2:
    if existing:
        st.caption(f"上次更新：{existing.get('更新时间', '')} · {existing.get('更新人', '')}")

# ── 可选：完整画像（复用服务商速览卡）──
with st.expander("🔭 看该服务商完整画像（基础信息 / RFM / 走势 / 采购 / 阵地）"):
    from _provider_quick_view import render_provider_quick_view
    render_provider_quick_view(code, key_prefix='pending_qv',
                               default_period_start=f'{YEAR}-01', default_period_end=ym)
