#!/usr/bin/env python3
"""🗺️ 全省全景 — 经营总览(总盘/趋势/11地市) + 渠道健康度(进出水/V0-V4)"""
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import DB_PATH  # noqa: E402
from _monthly_city_report import list_months  # noqa: E402
from _province_panorama import gather_province, province_health  # noqa: E402

require_auth()

st.markdown("### 🗺️ 全省全景")
st.caption("全省总盘 + 月度 SO 趋势 + 11 地市对比 + 渠道健康度")
st.markdown("""<style>
[data-testid="stMetricValue"]{font-size:0.95rem!important;line-height:1.05;}
[data-testid="stMetricLabel"]{font-size:0.72rem!important;}
[data-testid="stMetricDelta"]{font-size:0.68rem!important;}
div[data-testid="stHorizontalBlock"]{gap:0.4rem;}
</style>""", unsafe_allow_html=True)


def mrange(s, e):
    sp, ep = pd.Period(s, freq='M'), pd.Period(e, freq='M')
    return [str(sp + i) for i in range((ep - sp).n + 1)]


try:
    min_month, max_month = list_months()
except Exception as e:
    st.error(f"读 DB 失败：{e}")
    st.stop()

months = mrange(min_month, max_month)
c1, c2 = st.columns(2)
period_start = c1.selectbox("📅 开始月", months, index=max(0, len(months) - 4))
period_end = c2.selectbox("📅 结束月", months, index=len(months) - 1)
if period_start > period_end:
    st.error("开始月份必须 ≤ 结束月份")
    st.stop()


@st.cache_data(ttl=600, show_spinner="正在汇总全省全景……")
def cached(ps, pe):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return gather_province(conn, ps, pe)
    finally:
        conn.close()


@st.cache_data(ttl=600, show_spinner="正在汇总渠道健康度……")
def cached_health(ps, pe, cert_only):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return province_health(conn, ps, pe, cert_only)
    finally:
        conn.close()


d = cached(period_start, period_end)


def pct(v):
    return f"{v*100:.1f}%" if v is not None else "—"


tab_overview, tab_health = st.tabs(["📊 经营总览", "🆕 渠道健康度"])

# ══════════════════ Tab 1：经营总览 ══════════════════
with tab_overview:
    t = d['总盘']
    st.markdown("##### 📊 全省总盘")
    st.caption(f"**SI（签约业绩 · 时点 {d['_SI时点']}）**")
    msi = st.columns(3)
    msi[0].metric("SI 截止应达成", f"{t['SI截止应达成_万']:,.0f} 万", help="截止当前的 SI 累计任务（dealer_si_snapshot.累计任务）")
    msi[1].metric("SI 实际达成", f"{t['SI总金额_万']:,.0f} 万", help="SI 累计业绩达成（计任务）")
    msi[2].metric("SI 进度完成率", pct(t['SI完成率']), help="SI 实际 ÷ 截止应达成")
    st.caption("**SO（出货 · 全量感知）**")
    mso = st.columns(5)
    mso[0].metric("SO 总金额", f"{t['SO总金额_万']:,.0f} 万")
    mso[1].metric("SO 总台数", f"{t['SO总台数']:,}")
    mso[2].metric("SO 截止应达成", f"{t['YTD应达成_万']:,.0f} 万", help="年初→结束月 SO 进度目标")
    mso[3].metric("SO 实际达成", f"{t['YTD实际_万']:,.0f} 万")
    mso[4].metric("SO 进度完成率", pct(t['YTD完成率']), help="SO 实际 ÷ 截止应达成")

    st.caption("**授权服务商覆盖（剔除授牌服务商）**")
    auth = t['授权服务商覆盖']
    ma = st.columns(5)
    ma[0].metric("测算服务商", f"{auth['测算服务商数']:,}", help="district_base.服务商体量全省汇总")
    ma[1].metric("授权签约", f"{auth['授权签约数']:,}", help="provider_contract.管理标签='授权服务商'")
    ma[2].metric("授权渗透率", pct(auth['授权渗透率']), help="授权签约 ÷ 测算服务商")
    ma[3].metric("授权激活", f"{auth['授权激活数']:,}", help="授权签约中 provider_contract.是否激活='Y'")
    ma[4].metric("授权激活率", pct(auth['授权激活率']), help="授权激活 ÷ 授权签约")

    auth_city = pd.DataFrame(d['地市对比'])[
        ['城市', '测算服务商数', '授权签约数', '授权渗透率', '授权激活数', '授权激活率']
    ].copy()
    auth_city['授权渗透率'] = auth_city['授权渗透率'].apply(
        lambda value: pct(value) if pd.notna(value) else '—')
    auth_city['授权激活率'] = auth_city['授权激活率'].apply(
        lambda value: pct(value) if pd.notna(value) else '—')
    st.markdown("###### 地市授权服务商覆盖明细")
    st.dataframe(auth_city, use_container_width=True, hide_index=True, height=430)

    st.caption(f"服务商等级分布（provider_contract 官方等级 V0-V5，共 {t['服务商总数']:,} 家签约服务商）")
    tc = st.columns(7)
    for i, x in enumerate(t['V0_V5分布']):
        tc[i].metric(x['等级'], f"{x['数量']:,}", delta=pct(x['占比']), delta_color='off')
    _wn = t.get('授牌服务商数', 0)
    tc[6].metric("🚫 授牌(无价值)", f"{_wn:,}",
                 delta=pct(_wn / t['服务商总数']) if t['服务商总数'] else None, delta_color='off',
                 help="管理标签=授牌服务商:无价值客户,已从所有任务分配/跑动名单排除(与左侧等级分布交叉,非独立分类)")

    st.markdown("##### 📈 全省月度 SO 趋势（每月节点：台数/金额万，黄底=当前评估时段）")
    trend_df = pd.DataFrame(d['月度趋势'])
    if not trend_df.empty:
        import matplotlib.pyplot as plt
        from _so_trend_chart import render_so_trend
        fig = render_so_trend(trend_df, x_col='月',
                              period_start=period_start, period_end=period_end)
        st.pyplot(fig)
        plt.close(fig)

    st.markdown("##### 🏙️ 11 地市横向对比")
    city_df = pd.DataFrame(d['地市对比'])
    if not city_df.empty:
        show = city_df.copy()
        show['SO完成率'] = show['SO完成率'].apply(lambda v: pct(v) if v is not None else '—')
        show['授权渗透率'] = show['授权渗透率'].apply(lambda v: pct(v) if v is not None else '—')
        show['授权激活率'] = show['授权激活率'].apply(lambda v: pct(v) if v is not None else '—')
        show = show.rename(columns={'SO目标万': 'SO目标(万)', 'SO实际万': 'SO实际(万)'})
        order = ['城市', 'SO目标(万)', 'SO实际(万)', 'SO完成率', 'SO台数',
                 '测算服务商数', '授权签约数', '授权渗透率', '授权激活数', '授权激活率', '服务商数',
                 'V0', 'V1', 'V2', 'V3', 'V4', 'V5', '授牌数']
        order = [c for c in order if c in show.columns]
        st.dataframe(show[order], use_container_width=True, hide_index=True, height=430)
        st.caption("「授牌数」= 管理标签=授牌服务商(无价值客户),已从任务分配/跑动名单排除。")

# ══════════════════ Tab 2：渠道健康度 ══════════════════
with tab_health:
    cert_choice = st.radio("服务商范围", ["全部服务商", "仅认证 SMB 服务商"],
                           horizontal=True, key='cert_filter',
                           help="认证 SMB = provider_contract.渠道客户类型='认证SMB服务商'")
    cert_only = (cert_choice == "仅认证 SMB 服务商")
    hd = cached_health(period_start, period_end, cert_only)
    h = hd['渠道健康度']
    _tag = "认证 SMB 服务商" if cert_only else "全部服务商"

    st.markdown(f"##### 🆕 全省渠道健康度（进出水）· {_tag}")
    st.caption(f"范围 **{_tag}（{h['服务商总数']:,} 家）**　·　基线期 **{h['基线期']}** → 评估期 **{d['_周期']}**")
    hc = st.columns(6)
    hc[0].metric("服务商总数", f"{h['服务商总数']:,}")
    hc[1].metric("评估期活跃", f"{h['评估期活跃']:,}", help="评估期内有红包上线的服务商数")
    hc[2].metric("🆕 新增", f"{h['新增']:,}", delta=f"+{h['新增']}", delta_color='normal',
                 help="基线期从未激活、评估期首次激活（进水）")
    hc[3].metric("💤 流失", f"{h['流失']:,}", delta=f"-{h['流失']}", delta_color='inverse',
                 help="基线期活跃、评估期 0（出水）")
    hc[4].metric("净流失", f"{h['净流失']:+,}", help="流失 − 新增")
    hc[5].metric("激活率", pct(h['激活率']), help="V2 以上(V2+V3+V4+V5) ÷ 服务商总数")

    st.markdown("##### 🏙️ 11 地市健康度 + 服务商等级分布")
    st.caption(f"范围:{_tag}　·　活跃/新增/流失=评估期口径；V0-V5=该地市服务商等级分布")
    ch_df = pd.DataFrame(hd['地市健康度'])
    if not ch_df.empty:
        ch_df['激活率'] = ch_df['激活率'].apply(lambda v: pct(v) if v is not None else '—')
        order = ['城市', '活跃数', '新增', '流失', '净流失', '激活率', '服务商数', 'V0', 'V1', 'V2', 'V3', 'V4', 'V5']
        st.dataframe(ch_df[order], use_container_width=True, hide_index=True, height=430)

st.caption("💡 SI=累计业绩达成 · SO=product_flow 全量感知 · 健康度基线 2025-01 起 · 等级=provider_contract 官方原始等级")
