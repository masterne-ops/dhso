#!/usr/bin/env python3
"""
🔭 服务商全景雷达
- 输入名字片段 → 选服务商 → 一页看完整画像
- 顶部：基本信息 + 核心指标卡片
- 雷达图：6 维度（量、额、产品多样性、忠诚率、活跃月、红包密度）
- 5 个 Tab：产品 / 渠道 / 时间 / 地域 / 红包
"""

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# 共享加载（跨 page 缓存）
sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import (  # noqa: E402
    DB_PATH,
    load_main_shared,
    load_redpack_shared,
    load_provider_master_shared,
    load_visit_shared,
)
from _promotion_loader import load_promotion_shared  # noqa: E402
import sqlite3  # noqa: E402

require_auth()
st.markdown("### 🔭 服务商全景雷达")
st.caption("输入服务商名字 → 一页看完整画像：买了什么、和谁买、什么时候买、装在哪、红包拿了多少")

# 让顶部 metric 字体紧凑一点
st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.0rem !important; line-height: 1.1; }
[data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
[data-testid="stMetricDelta"] { font-size: 0.7rem !important; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────

rp_full = load_redpack_shared()
main_full = load_main_shared()
visit_full = load_visit_shared()      # 拜访(跑动) — 近 90 天指标用
promo_full = load_promotion_shared()  # 推广会参会 — 近 90 天指标用

if rp_full.empty:
    st.warning("📦 安装红包记录表暂无数据，请到主页『📥 数据导入』选择「安装红包记录」并上传 Excel。")
    st.stop()


# ──────────────────────────────────────────
# 中文字体（雷达图）
# ──────────────────────────────────────────

def get_chinese_font():
    import matplotlib.font_manager as fm
    candidate_paths = [
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Medium.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/System/Library/Fonts/Hiragino Sans GB.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf',
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        'C:/Windows/Fonts/msyh.ttc',
        'C:/Windows/Fonts/simhei.ttf',
    ]
    for p in candidate_paths:
        if Path(p).exists():
            try:
                return fm.FontProperties(fname=p, size=11)
            except Exception:
                continue
    return None


# ──────────────────────────────────────────
# 服务商搜索 + 选择
# ──────────────────────────────────────────

# 全量服务商（按总台数倒序，常用的排前面）
# 来源 1：红包表里有上线的（带上线台数）
provider_df = (
    rp_full.dropna(subset=['上线客户编码', '上线客户名称'])
    .groupby(['上线客户编码', '上线客户名称']).size()
    .reset_index(name='_cnt')
)
# 来源 2：并入签约表全量服务商 —— 让「已签约未激活（红包表里没有）」的也能被搜到 + 打标
try:
    _pc_conn = sqlite3.connect(str(DB_PATH))
    _pc_prov = pd.read_sql(
        "SELECT DISTINCT 客户编码 AS 上线客户编码, 客户名称 AS 上线客户名称 "
        "FROM provider_contract WHERE 客户编码 IS NOT NULL AND 客户名称 IS NOT NULL",
        _pc_conn)
    _pc_conn.close()
    _have = set(provider_df['上线客户编码'].astype(str))
    _pc_only = _pc_prov[~_pc_prov['上线客户编码'].astype(str).isin(_have)].copy()
    _pc_only['_cnt'] = 0
    provider_df = pd.concat([provider_df, _pc_only], ignore_index=True)
except Exception:
    pass
provider_df = provider_df.sort_values('_cnt', ascending=False).reset_index(drop=True)

search_col, _ = st.columns([2, 1])
with search_col:
    search = st.text_input(
        "🔍 搜索服务商名（输入名字片段）",
        placeholder="比如 杭州万仞 / 滨江",
        key="provider_search",
    )

if search.strip():
    matched = provider_df[
        provider_df['上线客户名称'].astype(str).str.contains(search.strip(), case=False, na=False)
    ]
else:
    matched = provider_df

if matched.empty:
    st.warning(f"没有匹配「{search}」的服务商")
    st.stop()

# 候选项展示：名字 (编码 · 总台数)
options = matched.apply(
    lambda r: f"{r['上线客户名称']} ({r['上线客户编码']} · {r['_cnt']:,} 台)",
    axis=1,
).tolist()
codes = matched['上线客户编码'].tolist()

picked_idx = st.selectbox(
    f"选择服务商（共 {len(matched):,} 个候选）",
    options=range(len(options)),
    format_func=lambda i: options[i],
    index=0,
    key="provider_pick",
)

picked_code = codes[picked_idx]
picked_name = matched.iloc[picked_idx]['上线客户名称']

# 取该服务商所有红包表记录
sp = rp_full[rp_full['上线客户编码'] == picked_code].copy()
# 签约在册但红包表无记录（已签约未激活）—— 不再直接 stop，下面只显示档案 + 打标面板
_no_online = sp.empty

# 关联主表（拿到出库时间、流通天数、是否异省/异城/异县）
sp_main = main_full[main_full['产品序列号'].isin(sp['产品序列号'])].copy()


st.divider()


# ──────────────────────────────────────────
# 基本信息
# ──────────────────────────────────────────

st.markdown(f"## 📌 {picked_name}")

# 派生
sp_geo_city = sp['上线客户地市'].dropna().mode().iloc[0] if not sp['上线客户地市'].dropna().empty else '—'
sp_geo_dist = sp['上线客户区县'].dropna().mode().iloc[0] if not sp['上线客户区县'].dropna().empty else '—'
signed_dealer = sp['所属一级客户'].dropna().mode().iloc[0] if not sp['所属一级客户'].dropna().empty else '—'
# 无上线记录时，城市/区县/代理商改从签约表取（让打标记录带上真实归属）
if _no_online:
    try:
        _c2 = sqlite3.connect(str(DB_PATH))
        _pcq = pd.read_sql(
            "SELECT 客户城市, 客户区县, 上级分销商名称 FROM provider_contract "
            "WHERE 客户编码=? LIMIT 1", _c2, params=(str(picked_code),))
        _c2.close()
        if not _pcq.empty:
            sp_geo_city = _pcq.iloc[0]['客户城市'] or '—'
            sp_geo_dist = _pcq.iloc[0]['客户区县'] or '—'
            signed_dealer = _pcq.iloc[0]['上级分销商名称'] or '—'
    except Exception:
        pass

# ──────────────────────────────────────────
# 🏷️ 标签栏（首行最显眼位置）
# ──────────────────────────────────────────
# 设计原则：先理解服务商再打标 — 因此把当前标签放在最显眼位置；
# admin/manager 在下方展开「添加/撤销」面板。业务员只看不能操作。
from _tag_widget import (  # noqa: E402
    render_provider_tag_panel as _render_tag_panel,
    can_mark_tag as _can_tag,
)
_tag_city = sp_geo_city if sp_geo_city != '—' else ''
_tag_dist = sp_geo_dist if sp_geo_dist != '—' else ''
with st.container(border=True):
    tc_l, tc_r = st.columns([6, 1])
    with tc_l:
        st.markdown("**🏷️ 标签**")
        _current_tags = _render_tag_panel(
            客户编码=str(picked_code),
            客户名称=str(picked_name or ''),
            城市=_tag_city, 区县=_tag_dist,
            source='page05_radar',
            key_prefix='p05top',
        )
    with tc_r:
        if _can_tag():
            st.caption("admin / manager")
        else:
            st.caption("仅查看")

# 已签约未激活：到此为止（档案 + 打标），画像 / 雷达 / 红包等需有上线记录才显示
if _no_online:
    ic0 = st.columns(3)
    ic0[0].markdown(f"**编码**：`{picked_code}`")
    ic0[1].markdown(f"**所在地**：{sp_geo_city} · {sp_geo_dist}")
    ic0[2].markdown(f"**签约代理商**：{signed_dealer}")
    st.info("📭 该服务商已签约在册，但红包上线表暂无记录（已签约未激活）。"
            "上方可正常打「⏳ 本月待激活」等标签；产生上线后才会显示画像 / 雷达 / 红包分析。")
    st.stop()


first_time = sp['上线时间'].min()
last_time = sp['上线时间'].max()
active_months = sp['上线年月'].dropna().nunique()
if pd.notna(first_time) and pd.notna(last_time):
    span_months = ((last_time.year - first_time.year) * 12 + last_time.month - first_time.month) + 1
else:
    span_months = 0
active_rate = active_months / span_months if span_months > 0 else 0

info_cols = st.columns(4)
info_cols[0].markdown(f"**编码**：`{picked_code}`")
info_cols[1].markdown(f"**所在地**：{sp_geo_city} · {sp_geo_dist}")
info_cols[2].markdown(f"**签约代理商**：{signed_dealer}")
info_cols[3].markdown(
    f"**在网时间**：{first_time.strftime('%Y-%m-%d') if pd.notna(first_time) else '—'} "
    f"~ {last_time.strftime('%Y-%m-%d') if pd.notna(last_time) else '—'} "
    f"（{active_months}/{span_months} 月活跃）"
)

# ── 核心指标 & tab 共用的基础计算（前置：下方 tabs 依赖以下变量，此处只算不显示）──
total_qty = len(sp)
total_amt = float(sp['产品现有分销价'].sum())
won_records = sp[sp['中奖金额'] > 0]
total_redpack_n = len(won_records)
total_redpack_amt = float(won_records['中奖金额'].sum())
red_to_so = total_redpack_amt / total_amt if total_amt > 0 else 0

# 忠诚率
sign_strip = sp['所属一级客户'].fillna('').astype(str).str.strip()
out_strip = sp['出货客户名称'].fillna('').astype(str).str.strip()
loyal_mask = (sign_strip == out_strip) & (sign_strip != '')
loyal = float(loyal_mask.mean()) if len(sp) > 0 else 0

# 产品多样性
n_series = sp['产品系列'].dropna().nunique()
n_models = sp['内部型号'].dropna().nunique()

st.divider()
# ──────────────────────────────────────────
# 5 个 Tab
# ──────────────────────────────────────────

tab_prod, tab_chan, tab_time, tab_geo, tab_red = st.tabs([
    "📦 产品组合",
    "🤝 采购渠道",
    "📅 上线节奏",
    "🌍 地域分布",
    "🎁 红包行为",
])


# ════════════════════ 📦 产品 ════════════════════
with tab_prod:
    st.markdown("**产品系列分布**")
    by_series = (
        sp.groupby('产品系列', dropna=False)
        .agg(台数=('产品序列号', 'count'), 金额=('产品现有分销价', 'sum'))
        .reset_index()
        .sort_values('台数', ascending=False)
    )
    by_series['占比'] = (by_series['台数'] / by_series['台数'].sum() * 100).round(1)
    by_series['占比'] = by_series['占比'].apply(lambda x: f"{x:.1f}%")
    by_series['金额'] = by_series['金额'].round(0)
    st.dataframe(by_series, use_container_width=True, hide_index=True, height=260)

    cols_p = st.columns(2)
    with cols_p[0]:
        st.markdown("**产品子系列-新 分布**")
        by_sub = (
            sp.groupby('产品子系列-新', dropna=False)
            .agg(台数=('产品序列号', 'count'), 金额=('产品现有分销价', 'sum'))
            .reset_index()
            .sort_values('台数', ascending=False)
            .head(15)
        )
        by_sub['金额'] = by_sub['金额'].round(0)
        st.dataframe(by_sub, use_container_width=True, hide_index=True, height=300)
    with cols_p[1]:
        st.markdown("**Top 15 内部型号**")
        by_model = (
            sp.groupby('内部型号', dropna=False)
            .agg(台数=('产品序列号', 'count'), 金额=('产品现有分销价', 'sum'))
            .reset_index()
            .sort_values('台数', ascending=False)
            .head(15)
        )
        by_model['金额'] = by_model['金额'].round(0)
        st.dataframe(by_model, use_container_width=True, hide_index=True, height=300)


# ════════════════════ 🤝 渠道 ════════════════════
with tab_chan:
    by_dealer = (
        sp.groupby('出货客户名称', dropna=False)
        .agg(台数=('产品序列号', 'count'), 金额=('产品现有分销价', 'sum'))
        .reset_index()
        .sort_values('台数', ascending=False)
    )
    by_dealer['占比'] = (by_dealer['台数'] / by_dealer['台数'].sum() * 100).round(1)
    by_dealer['占比'] = by_dealer['占比'].apply(lambda x: f"{x:.1f}%")
    by_dealer['是否签约'] = by_dealer['出货客户名称'].apply(
        lambda x: '✅ 签约' if str(x).strip() == str(signed_dealer).strip() else '⚠️ 跨渠道'
    )
    by_dealer['金额'] = by_dealer['金额'].round(0)

    st.markdown(f"**签约代理商：{signed_dealer}**")
    n_dealers = by_dealer['出货客户名称'].nunique()
    n_cross = (by_dealer['是否签约'] == '⚠️ 跨渠道').sum()

    cstats = st.columns(3)
    cstats[0].metric("出货代理商数", f"{n_dealers}")
    cstats[1].metric("跨渠道代理商数", f"{n_cross}")
    cstats[2].metric("签约忠诚率", f"{loyal * 100:.1f}%")

    st.dataframe(
        by_dealer[['出货客户名称', '是否签约', '台数', '金额', '占比']],
        use_container_width=True, hide_index=True, height=300,
    )

    if n_cross > 0:
        cross_names = by_dealer[by_dealer['是否签约'] == '⚠️ 跨渠道']['出货客户名称'].tolist()
        st.caption(f"⚠️ 跨渠道供应商：{'、'.join(map(str, cross_names))}"
                   f"　→ 该服务商除签约代理商外还从这些代理商进过货")


# ════════════════════ 📅 时间 ════════════════════
with tab_time:
    monthly = (
        sp.groupby('上线年月')
        .agg(台数=('产品序列号', 'count'), 金额=('产品现有分销价', 'sum'))
        .reset_index()
        .sort_values('上线年月')
    )
    monthly['金额'] = monthly['金额'].round(0)

    cstats = st.columns(4)
    cstats[0].metric("活跃月数", f"{active_months}")
    cstats[1].metric("月均台数", f"{(total_qty / max(active_months, 1)):.1f}")
    cstats[2].metric("月均金额", f"¥{(total_amt / max(active_months, 1)):,.0f}")
    cstats[3].metric("活跃月份占比",
                     f"{active_rate * 100:.0f}%",
                     help=f"{active_months} 月活跃 / {span_months} 月在网")

    st.markdown("**月度上线趋势**")
    if not monthly.empty:
        import altair as alt
        _stver = tuple(int(x) for x in st.__version__.split(".")[:2])
        _picked_months = []
        if _stver >= (1, 35):
            _msel = alt.selection_point(fields=['上线年月'], name='msel',
                                        on='click', clear='dblclick')
            _bar = (
                alt.Chart(monthly).mark_bar().encode(
                    x=alt.X('上线年月:O', title='上线年月'),
                    y=alt.Y('台数:Q', title='台数'),
                    color=alt.condition(_msel, alt.value('#ff5252'),
                                        alt.value('#4c78a8')),
                    tooltip=['上线年月', '台数', '金额'],
                ).properties(height=280).add_params(_msel)
            )
            _ev = st.altair_chart(_bar, use_container_width=True,
                                  on_select='rerun', key='p05_time_bar')
            try:
                _picked_months = [r['上线年月'] for r in _ev.selection['msel']]
            except Exception:
                _picked_months = []
        else:
            st.bar_chart(monthly.set_index('上线年月')[['台数']], height=280)
            _picked_months = st.multiselect(
                "选择月份（当前 Streamlit 版本不支持点击柱子，用下拉代替）",
                options=monthly['上线年月'].tolist(), key='p05_time_ms',
            )
        with st.expander("📋 月度明细表"):
            st.dataframe(monthly, use_container_width=True, hide_index=True)

        # ── 点击某月柱子 → 该月「型号 × 出货代理商」明细 ──
        if _picked_months:
            _md = sp[sp['上线年月'].isin(_picked_months)]
            _detail = (
                _md.groupby(['内部型号', '出货客户名称'], dropna=False)
                .agg(台数=('产品序列号', 'count'),
                     金额=('产品现有分销价', 'sum'))
                .reset_index().sort_values('台数', ascending=False)
            )
            _detail['金额'] = _detail['金额'].round(0)
            _detail = _detail.rename(columns={'出货客户名称': '出货代理商'})
            st.markdown(
                f"**📍 {'、'.join(map(str, _picked_months))} — 上线型号 × 出货代理商**"
                f"（{len(_md)} 台 / {_detail['内部型号'].nunique()} 个型号）"
            )
            st.dataframe(_detail, use_container_width=True,
                         hide_index=True, height=320)
        else:
            st.caption("👆 点击上方某月柱子（可多选；双击空白处清除），"
                       "下方列出该月上线的型号 & 来自哪家代理商出货")

    # 单日爆发
    st.markdown("**单日爆发 Top 10**")
    daily = (
        sp.groupby('上线日期')
        .agg(台数=('产品序列号', 'count'), 金额=('产品现有分销价', 'sum'))
        .reset_index().sort_values('台数', ascending=False).head(10)
    )
    daily['金额'] = daily['金额'].round(0)
    daily['上线日期'] = daily['上线日期'].astype(str)
    st.dataframe(daily, use_container_width=True, hide_index=True)


# ════════════════════ 🌍 地域 ════════════════════
with tab_geo:
    cstats = st.columns(2)
    with cstats[0]:
        st.markdown("**安装城市分布**")
        by_city = (
            sp.groupby('安装城市', dropna=False)
            .agg(台数=('产品序列号', 'count'), 金额=('产品现有分销价', 'sum'))
            .reset_index().sort_values('台数', ascending=False)
        )
        by_city['金额'] = by_city['金额'].round(0)
        st.dataframe(by_city, use_container_width=True, hide_index=True, height=300)
    with cstats[1]:
        st.markdown("**安装区县分布 Top 15**")
        by_dist = (
            sp.groupby('安装区县_全', dropna=False)
            .agg(台数=('产品序列号', 'count'), 金额=('产品现有分销价', 'sum'))
            .reset_index().sort_values('台数', ascending=False).head(15)
        )
        by_dist['金额'] = by_dist['金额'].round(0)
        st.dataframe(by_dist, use_container_width=True, hide_index=True, height=300)

    # 异省/异城/异县（来自主表）
    if not sp_main.empty and {'是否异省', '是否异城', '是否异县'}.issubset(sp_main.columns):
        st.markdown("**异地销售（来自主表）**")
        异省 = (sp_main['是否异省'] == 'Y').sum()
        异城 = (sp_main['是否异城'] == 'Y').sum()
        异县 = (sp_main['是否异县'] == 'Y').sum()
        ec = st.columns(3)
        ec[0].metric("异省台数",
                      f"{异省} / {len(sp_main)}",
                      f"{异省 / max(len(sp_main), 1) * 100:.1f}%")
        ec[1].metric("异城台数",
                      f"{异城} / {len(sp_main)}",
                      f"{异城 / max(len(sp_main), 1) * 100:.1f}%")
        ec[2].metric("异县台数",
                      f"{异县} / {len(sp_main)}",
                      f"{异县 / max(len(sp_main), 1) * 100:.1f}%")


# ════════════════════ 🎁 红包 ════════════════════
with tab_red:
    if won_records.empty:
        st.info("该服务商无中奖红包记录")
    else:
        cstats = st.columns(4)
        cstats[0].metric("红包总数", f"{total_redpack_n:,}")
        cstats[1].metric("红包总金额", f"¥{total_redpack_amt:,.0f}")
        cstats[2].metric("单个最大", f"¥{won_records['中奖金额'].max():.0f}")
        cstats[3].metric("平均红包", f"¥{won_records['中奖金额'].mean():.2f}")

        # 中奖率
        n_attempt = (sp['是否抽奖'] == 'Y').sum() if '是否抽奖' in sp.columns else len(sp)
        win_rate = total_redpack_n / max(n_attempt, 1) * 100
        st.caption(f"🎯 中奖率：**{win_rate:.1f}%**（{total_redpack_n} 中 / {n_attempt} 抽）")

        # 夜视王占比
        yeshi_n = won_records['产品子系列-新'].fillna('').astype(str).str.contains('夜视王', na=False).sum()
        yeshi_pct = yeshi_n / total_redpack_n * 100 if total_redpack_n > 0 else 0
        st.caption(f"🌙 夜视王红包占比：**{yeshi_pct:.1f}%**（{yeshi_n} 个）")

        # 月度红包趋势
        st.markdown("**月度红包趋势**")
        red_monthly = (
            won_records.groupby('上线年月')
            .agg(数量=('中奖金额', 'count'), 金额=('中奖金额', 'sum'))
            .reset_index().sort_values('上线年月')
        )
        red_monthly['金额'] = red_monthly['金额'].round(2)
        if not red_monthly.empty:
            st.bar_chart(red_monthly.set_index('上线年月')[['金额']], height=240)
            with st.expander("📋 月度红包明细"):
                st.dataframe(red_monthly, use_container_width=True, hide_index=True)

        # 中奖金额区间分布
        st.markdown("**单个红包金额分布**")
        bins = [0, 2, 5, 10, 50, 100, 500, 1000, 100000]
        labels = ['¥0-2', '¥2-5', '¥5-10', '¥10-50', '¥50-100', '¥100-500', '¥500-1k', '¥1k+']
        won_records = won_records.copy()
        won_records['区间'] = pd.cut(won_records['中奖金额'], bins=bins, labels=labels, right=False)
        bin_dist = won_records.groupby('区间', observed=True).size().reset_index(name='次数')
        st.dataframe(bin_dist, use_container_width=True, hide_index=True)

st.divider()
st.caption(
    f"💡 这是基于红包记录表的全景画像。如果数据有缺失（如未触发红包的设备），"
    f"该服务商的实际规模会比上面展示的略大。"
)

# ──────────────────────────────────────────
# 📋 详细画像（来自 沙盘 + 签约 合并视图）
# ──────────────────────────────────────────

master = load_provider_master_shared()
profile_row = None
if not master.empty and '客户编码' in master.columns:
    matched_master = master[master['客户编码'].astype(str) == str(picked_code)]
    if not matched_master.empty:
        profile_row = matched_master.iloc[0]

if profile_row is None:
    st.caption(
        "💡 该服务商在「服务商管理沙盘 / 签约明细」表中没有匹配记录。"
        "上传这两份 Excel 后可以看到老板/联系人/签约状态等详细画像。"
    )
else:
    src_p = bool(profile_row.get('_来源_沙盘', False))
    src_c = bool(profile_row.get('_来源_签约', False))
    src_label = []
    if src_p:
        src_label.append('📋 沙盘')
    if src_c:
        src_label.append('📜 签约')
    src_text = ' + '.join(src_label) if src_label else '—'

    def _v(field, default='—'):
        """安全取字段值"""
        v = profile_row.get(field)
        if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() in ('', 'nan'):
            return default
        return str(v).strip()

    def _date(field):
        v = profile_row.get(field)
        if v is None or pd.isna(v):
            return '—'
        try:
            return pd.to_datetime(v).strftime('%Y-%m-%d')
        except Exception:
            return str(v)

    with st.expander(
        f"📋 详细画像（数据来源：{src_text}）— 老板/联系人/签约状态/经营信息",
        expanded=True,
    ):
        # 4 列布局
        c1, c2, c3, c4 = st.columns(4)

        # ── 联系人（签约表为主）──
        with c1:
            st.markdown("**📞 联系人**")
            contact_name = _v('联系人')
            contact_pos = _v('联系人职位')
            contact_phone = _v('联系电话')
            if contact_name != '—' or contact_phone != '—':
                if contact_name != '—':
                    pos_str = f"（{contact_pos}）" if contact_pos != '—' else ""
                    st.markdown(f"{contact_name}{pos_str}")
                if contact_phone != '—':
                    st.markdown(f"📱 {contact_phone}")
            else:
                st.caption("—")

        # ── 老板（沙盘表）──
        with c2:
            st.markdown("**👔 老板**")
            boss = _v('老板姓名')
            boss_phone = _v('老板电话')
            boss_type = _v('老板类型')
            boss_age = _v('老板年龄')
            if boss != '—':
                st.markdown(f"{boss}（{boss_age}岁）" if boss_age != '—' else boss)
            if boss_phone != '—':
                st.markdown(f"📱 {boss_phone}")
            if boss_type != '—':
                st.caption(f"类型：{boss_type}")
            if boss == '—' and boss_phone == '—':
                st.caption("—")

        # ── 业务员（签约表）──
        with c3:
            st.markdown("**🤝 分销商业务员**")
            sales_name = _v('分销商业务员姓名')
            sales_phone = _v('分销商业务员手机号')
            owner_dept = _v('客户所有者部门')
            if sales_name != '—':
                st.markdown(sales_name)
            if sales_phone != '—':
                st.markdown(f"📱 {sales_phone}")
            if owner_dept != '—':
                st.caption(f"部门：{owner_dept}")
            if sales_name == '—' and sales_phone == '—':
                st.caption("—")

        # ── 责任人（沙盘+签约）──
        with c4:
            st.markdown("**👤 责任人**")
            owner = _v('客户所有者')
            owner_id = _v('客户所有者工号')
            mgr = _v('责任分销经理')
            if owner != '—':
                st.markdown(f"{owner}（{owner_id}）" if owner_id != '—' else owner)
            if mgr != '—':
                st.caption(f"分销经理：{mgr}")
            if owner == '—' and mgr == '—':
                st.caption("—")

        st.divider()

        # ── 签约状态（签约表）──
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("签约日期", _date('签约日期'))
        is_new = _v('是否新签')
        sc2.metric("是否新签", is_new if is_new != '—' else '—')
        is_active = _v('是否激活')
        sc3.metric("是否激活", is_active if is_active != '—' else '—',
                    help=f"激活时间：{_date('激活时间')}")
        is_repeat = _v('是否复购')
        sc4.metric("是否复购", is_repeat if is_repeat != '—' else '—',
                    help=f"复购时间：{_date('复购时间')}")

        st.divider()

        # ── 经营信息（沙盘表）──
        st.markdown("**🏢 经营信息**")
        b1, b2, b3, b4 = st.columns(4)
        b1.markdown(f"**客户分类**\n\n{_v('客户分类')}")
        b2.markdown(f"**服务商等级**\n\n{_v('服务商等级_原始') if _v('服务商等级_原始') != '—' else _v('服务商等级')}")
        b3.markdown(f"**客户分类**\n\n{_v('客户分类')}")
        b4.markdown(f"**业务类型**\n\n{_v('业务类型')}")

        b5, b6, b7, b8 = st.columns(4)
        b5.markdown(f"**主营品牌**\n\n{_v('主营品牌')}")
        b6.markdown(f"**线下店铺类型**\n\n{_v('线下店铺类型')}")
        b7.markdown(f"**店铺门头品牌**\n\n{_v('店铺门头品牌')}")
        b8.markdown(f"**服务用户类型**\n\n{_v('服务用户类型')}")

        # 规模
        b9, b10, b11, b12 = st.columns(4)
        b9.markdown(f"**公司总人数**\n\n{_v('公司总人数')}")
        b10.markdown(f"**安防销售人员**\n\n{_v('安防销售人员数量')}")
        b11.markdown(f"**技术人员**\n\n{_v('技术人员总数')}")
        b12.markdown(f"**仓储面积（㎡）**\n\n{_v('仓储面积（㎡）')}")

        # 风险标记
        is_competitor = _v('是否竞品TOP服务商（安防体量≥20W）')
        is_hk = _v('是否HKTOP竞品服务商')
        biz_status = _v('经营状态')
        if any(x != '—' for x in [is_competitor, is_hk, biz_status]):
            st.divider()
            st.markdown("**⚠️ 风险标记**")
            r_cols = st.columns(3)
            r_cols[0].markdown(f"**是否竞品TOP（≥20W）**\n\n{is_competitor}")
            r_cols[1].markdown(f"**是否HKTOP 竞品**\n\n{is_hk}")
            r_cols[2].markdown(f"**经营状态**\n\n{biz_status}")

        # 详细地址
        addr = _v('地址')
        if addr != '—':
            st.caption(f"📍 地址：{addr}")

st.markdown("##### 📊 核心指标")
m_cols = st.columns(6)
m_cols[0].metric("总上线台数", f"{total_qty:,}")
m_cols[1].metric("总上线金额", f"¥{total_amt:,.0f}")
m_cols[2].metric("红包数 / 金额", f"{total_redpack_n:,} / ¥{total_redpack_amt:,.0f}")
m_cols[3].metric("红包/上线比", f"{red_to_so * 100:.3f}%",
                  help="红包总金额 ÷ 总上线金额")
m_cols[4].metric("签约忠诚率", f"{loyal * 100:.1f}%",
                  help="出货客户 == 所属一级客户 的台数比例")
m_cols[5].metric("产品多样性", f"{n_series} 系列 / {n_models} 型号")

# ── 近 90 天活动（基准：各数据集最新日期往前 90 天）──
_v90_dahua = _v90_dealer = _promo90 = 0
_vbase = _pbase = None
if (not visit_full.empty and '客户编码' in visit_full.columns
        and '拜访时间' in visit_full.columns):
    _vbase = visit_full['拜访时间'].max()
    if pd.notna(_vbase):
        _v90 = visit_full[
            (visit_full['客户编码'].astype(str) == str(picked_code))
            & (visit_full['拜访时间'] >= _vbase - pd.Timedelta(days=90))
        ]
        if '_打卡方' in _v90.columns:
            _dh = _v90['_打卡方'].astype(str).str.contains('大华', na=False)
            _v90_dahua = int(_dh.sum())
            _v90_dealer = int(len(_v90) - _v90_dahua)
        else:
            _v90_dahua = int(len(_v90))
if (not promo_full.empty and '参会客户编码' in promo_full.columns
        and '活动开始时间' in promo_full.columns):
    _pbase = promo_full['活动开始时间'].max()
    if pd.notna(_pbase):
        _promo90 = int(len(promo_full[
            (promo_full['参会客户编码'].astype(str) == str(picked_code))
            & (promo_full['活动开始时间'] >= _pbase - pd.Timedelta(days=90))
        ]))

st.markdown("##### 🏃 近 90 天活动")
k2 = st.columns(4)
k2[0].metric("跑动合计", f"{_v90_dahua + _v90_dealer} 次",
             help="近 90 天拜访打卡次数（大华人员 + 代理商员工）")
k2[1].metric("· 大华人员", f"{_v90_dahua} 次")
k2[2].metric("· 代理商员工", f"{_v90_dealer} 次")
k2[3].metric("参加推广会", f"{_promo90} 次", help="近 90 天参加的推广会 / 沙龙场次")
_bits = []
if _vbase is not None and pd.notna(_vbase):
    _bits.append(f"拜访数据截至 {_vbase.strftime('%Y-%m-%d')}")
if _pbase is not None and pd.notna(_pbase):
    _bits.append(f"推广会数据截至 {_pbase.strftime('%Y-%m-%d')}")
if _bits:
    st.caption("基准：" + " · ".join(_bits) + "，各往前 90 天")


# ──────────────────────────────────────────
# 🩹 RFM 健康度 + 异常标识（马甲 / 伞形 / 低效服务商）
# ──────────────────────────────────────────
st.markdown("##### 🩹 RFM 健康度 + 异常标识")

# RFM 计算（基于该服务商 sp 数据）
sp_times = pd.to_datetime(sp['上线时间'], errors='coerce')
asof = sp_times.max() if len(sp_times) else pd.NaT
# 用 DB 全局最大时间作 R 的"今天"基准更准
db_max_dt = pd.to_datetime(rp_full['上线时间'], errors='coerce').max()
asof_for_R = db_max_dt if pd.notna(db_max_dt) else asof

if pd.notna(asof) and pd.notna(asof_for_R):
    last_visit = sp_times.max()
    R_days = (asof_for_R - last_visit).days
    win_start = asof_for_R - pd.Timedelta(days=365)
    cur_12m = sp[sp_times >= win_start]
    F_count = pd.to_datetime(cur_12m['上线时间'], errors='coerce').dt.date.nunique()
    M_12m = float(cur_12m['产品现有分销价'].sum())
else:
    R_days, F_count, M_12m = None, 0, 0.0


def _r_emoji(r):
    if r is None: return '?'
    return '🟢' if r <= 30 else ('🟡' if r <= 60 else '🔴')


def _f_emoji(f):
    return '🟢' if f >= 12 else ('🟡' if f >= 3 else '🔴')


rfm_cols = st.columns(3)
rfm_cols[0].metric(
    "R · 最近上线距今",
    f"{R_days} 天 {_r_emoji(R_days)}" if R_days is not None else "—",
    help="≤30 天 🟢 健康 / 30-60 天 🟡 预警 / >60 天 🔴 沉睡",
)
rfm_cols[1].metric(
    "F · 12 月内不同上线日",
    f"{F_count} 次 {_f_emoji(F_count)}",
    help="≥12 次 🟢 / 3-11 次 🟡 / <3 次 🔴",
)
rfm_cols[2].metric(
    "M · 12 月内累计货值",
    f"¥{M_12m:,.0f}",
    help="累计上线货值 = 服务商等级判定依据 (V4 ≥3万 / V3 1-3万 / V2 0.1-1万)",
)

# 三类异常标识
try:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # 🎭 马甲
        vest_n = conn.execute(
            "SELECT COUNT(*) FROM vest_account WHERE 服务商客户编码 = ?",
            (str(picked_code),),
        ).fetchone()[0]
        is_vest = vest_n > 0

        # ☂️ 伞形（同老板姓名 + 电话 注册 ≥ 2 家）
        is_umb = False
        umb_peers = []
        boss = conn.execute(
            "SELECT 老板姓名, 老板电话 FROM provider_profile WHERE 客户编码 = ?",
            (str(picked_code),),
        ).fetchone()
        if boss and boss[0] and boss[1]:
            peers_rows = conn.execute(
                "SELECT 客户编码, 公司名称 FROM provider_profile "
                "WHERE 老板姓名 = ? AND 老板电话 = ? AND 客户编码 != ?",
                (boss[0], boss[1], str(picked_code)),
            ).fetchall()
            is_umb = len(peers_rows) >= 1
            umb_peers = peers_rows

        # ⚠️ 低效签约（启发式：签约 60+ 天但累计上线 ≤ 2 台）
        is_fake_heuristic = False
        sign_date = None
        cnt_online = 0
        db_max = conn.execute(
            "SELECT MAX(上线时间) FROM install_redpack_v"
        ).fetchone()[0]
        if db_max:
            row = conn.execute("""
                SELECT pc.签约日期,
                       (SELECT COUNT(*) FROM install_redpack_v
                         WHERE 上线客户编码 = ?) AS n_online
                  FROM provider_contract pc
                 WHERE pc.客户编码 = ?
                   AND pc.签约日期 IS NOT NULL
                   AND date(pc.签约日期) <= date(?, '-60 day')
            """, (str(picked_code), str(picked_code), db_max)).fetchone()
            if row:
                sign_date = row[0]
                cnt_online = int(row[1])
                is_fake_heuristic = cnt_online <= 2

        # 🚫 明确无采购意向（人工标注，最高置信度）
        closed_row = conn.execute(
            "SELECT 标记类型, 导入时间 FROM closed_provider WHERE 客户编码 = ?",
            (str(picked_code),),
        ).fetchone()
        is_closed = closed_row is not None
        closed_type = closed_row[0] if closed_row else None

        # ⚔️ 竞品 Top 服务商（重点开拓目标）
        ctp_row = conn.execute("""
            SELECT 客户经营品牌, 在售大华, 竞品体量_万, 责任人姓名, 责任人角色, 转化策略
              FROM competitor_top_provider WHERE 客户编码 = ?
        """, (str(picked_code),)).fetchone()
        is_competitor_top = ctp_row is not None
        ctp_info = None
        if ctp_row:
            ctp_info = {
                '客户经营品牌': ctp_row[0],
                '在售大华': bool(ctp_row[1]),
                '竞品体量_万': ctp_row[2],
                '责任人': ctp_row[3],
                '责任人角色': ctp_row[4],
                '转化策略': ctp_row[5],
            }
    finally:
        conn.close()
except Exception as _e:
    is_vest = is_umb = is_fake_heuristic = is_closed = is_competitor_top = False
    umb_peers = []
    closed_type = None
    ctp_info = None

st.caption("🔎 **5 大标签判定证据**（顶部「🏷️ 标签」栏的具体来源）")
tag_cols = st.columns(5)
tag_cols[0].metric(
    "🎭 已确认马甲",
    "是" if is_vest else "否",
    help="来源：vest_account 表（人工维护的马甲清单）",
)
tag_cols[1].metric(
    "☂️ 伞形账号",
    f"是（共 {len(umb_peers)+1} 家）" if is_umb else "否",
    help="同老板姓名 + 同电话 注册了 ≥2 家不同服务商",
)
tag_cols[2].metric(
    "❌ 低效签约（启发式）",
    "是" if is_fake_heuristic else "否",
    help="签约 60+ 天但累计上线 ≤ 2 台",
)
tag_cols[3].metric(
    "🚫 明确无采购意向",
    "是" if is_closed else "否",
    help=f"来源：closed_provider 表（业务方人工标注，最高置信度）"
         + (f" · 类型: {closed_type}" if closed_type else ""),
)
tag_cols[4].metric(
    "⚔️ 竞品 Top 服务商",
    f"是（竞品 {ctp_info['竞品体量_万']:.0f} 万）" if is_competitor_top and ctp_info else "否",
    help="海康为主的核心服务商，大华应重点开拓 — 来源：competitor_top_provider 表",
)

# 🏷️ 复盘打标已迁移到页面顶部（首行）— 在 "##  📌 客户名" 之下的标签面板
# 此处保留 evidence 指标作为打标参考；如需打标 → 滚到顶部点击 "🛠️ 添加 / 撤销标签"

if is_closed:
    st.error(
        f"🚫 **此服务商已被业务方明确标注为「{closed_type or '无采购意向'}」**"
        " — 派单 / 任务系统已自动排除该客户，业务员无需再跑动。"
    )

if is_competitor_top and ctp_info:
    sells_dahua_tag = "✅ 已混卖大华" if ctp_info['在售大华'] else "❌ 纯卖海康（暂未合作）"
    st.success(
        f"⚔️ **重点开拓 · 竞品 Top 服务商** · {sells_dahua_tag} · "
        f"25 年竞品体量 **{ctp_info['竞品体量_万']:.0f} 万**"
    )
    info_cols = st.columns(3)
    info_cols[0].markdown(f"**经营品牌**\n\n{ctp_info['客户经营品牌'] or '—'}")
    info_cols[1].markdown(
        f"**责任人**\n\n{ctp_info['责任人']}（{ctp_info['责任人角色']}）"
    )
    info_cols[2].markdown(f"**竞品体量**\n\n{ctp_info['竞品体量_万']:.0f} 万 / 年")
    if ctp_info['转化策略']:
        st.info(f"💡 **转化策略**：{ctp_info['转化策略']}")

if is_umb and umb_peers:
    with st.expander(f"☂️ 该老板名下其他 {len(umb_peers)} 家关联服务商"):
        st.dataframe(
            pd.DataFrame(umb_peers, columns=['客户编码', '公司名称']),
            use_container_width=True, hide_index=True,
        )


# ──────────────────────────────────────────
# 🩺 对签约代理商的风险评估
# ──────────────────────────────────────────

def _assess_risk():
    """判断该服务商对其签约代理商是否处于危险状态"""
    if signed_dealer in (None, '—'):
        return '❓ 未关联', 'info', ['服务商未关联签约代理商，无法判断']

    factors = []

    # 数据库中的最近 3 月（不是该服务商的，是数据库整体的）
    db_months = sorted(rp_full['上线年月'].dropna().unique())
    db_months = [m for m in db_months if m and m != 'NaT']
    if len(db_months) == 0:
        return '❓ 无数据', 'info', ['数据库中无月份信息']
    recent_3 = db_months[-3:] if len(db_months) >= 3 else db_months
    recent_label = f"{recent_3[0]} ~ {recent_3[-1]}"

    sp_recent = sp[sp['上线年月'].isin(recent_3)]

    # 1) 近 3 月完全静默
    if sp_recent.empty:
        return ('🚨 高危 · 沉睡', 'error',
                [f"近 3 月（{recent_label}）无任何上线记录",
                 "可能已退出体系或转向竞品"])

    # 近 3 月忠诚率
    rec_sign = sp_recent['所属一级客户'].fillna('').astype(str).str.strip()
    rec_out = sp_recent['出货客户名称'].fillna('').astype(str).str.strip()
    rec_loyal_mask = (rec_sign == rec_out) & (rec_sign != '')
    n_recent_signed = int(rec_loyal_mask.sum())
    n_recent_total = len(sp_recent)
    recent_loyal = n_recent_signed / n_recent_total if n_recent_total > 0 else 0

    # 主要"其他"代理商
    other = sp_recent[~rec_loyal_mask]
    main_other = (other['出货客户名称'].dropna().mode().iloc[0]
                  if len(other) > 0 and not other['出货客户名称'].dropna().mode().empty
                  else None)

    # 2) 近 3 月签约 = 0 → 流失
    if n_recent_signed == 0:
        msgs = [f"近 3 月（{recent_label}）从签约代理商「{signed_dealer}」进货 = 0"]
        msgs.append(f"全部 {n_recent_total} 台从非签约代理商进货 → 视为流失")
        if main_other:
            msgs.append(f"主要替代代理商：**{main_other}**")
        return '🚨 高危 · 流失', 'error', msgs

    # 3) 整体忠诚率分级
    if loyal < 0.30:
        msgs = [
            f"整体忠诚率 **{loyal * 100:.0f}%**（< 30%）→ 大量跨渠道采购",
            f"近 3 月签约采购 {n_recent_signed}/{n_recent_total}，占比 {recent_loyal * 100:.0f}%",
        ]
        if main_other:
            msgs.append(f"主要其他代理商：**{main_other}**")
        return '🚨 高危 · 严重跨渠道', 'error', msgs

    if loyal < 0.70:
        msgs = [
            f"整体忠诚率 **{loyal * 100:.0f}%**（30%~70%）→ 多源采购",
            f"近 3 月签约采购 {n_recent_signed}/{n_recent_total}，占比 {recent_loyal * 100:.0f}%",
        ]
        if main_other:
            msgs.append(f"主要其他代理商：**{main_other}**（值得签约代理商关注）")
        if recent_loyal < loyal - 0.1:
            msgs.append(f"⚠️ 近 3 月忠诚率较整体下降 {(loyal - recent_loyal) * 100:.0f} 个点 → 关系恶化中")
        return '⚠️ 中危 · 混合采购', 'warning', msgs

    # 忠诚率 ≥ 70%
    msgs = [
        f"整体忠诚率 **{loyal * 100:.0f}%** → 主要从签约代理商进货",
    ]
    if recent_loyal < 0.5:
        msgs.append(
            f"⚠️ 但近 3 月忠诚率仅 **{recent_loyal * 100:.0f}%**"
            f"（{n_recent_signed}/{n_recent_total}）→ 关系可能在恶化"
        )
        if main_other:
            msgs.append(f"近期主要替代：**{main_other}**")
        return '⚠️ 中危 · 关系恶化中', 'warning', msgs

    msgs.append(f"近 3 月忠诚率 {recent_loyal * 100:.0f}%（{n_recent_signed}/{n_recent_total}）")
    return '✅ 健康', 'success', msgs


risk_label, risk_level, risk_msgs = _assess_risk()
st.markdown("##### 🩺 对签约代理商的风险评估")
if risk_level == 'error':
    st.error(f"**{risk_label}**")
elif risk_level == 'warning':
    st.warning(f"**{risk_label}**")
elif risk_level == 'success':
    st.success(f"**{risk_label}**")
else:
    st.info(f"**{risk_label}**")

for m in risk_msgs:
    st.caption(f"· {m}")


# ──────────────────────────────────────────
# 全景雷达图（6 维度，相对全省服务商百分位）
# ──────────────────────────────────────────

st.markdown("##### 🎯 全景雷达（vs 全省服务商百分位）")
st.caption("每个维度是该服务商在全省服务商中的百分位。越靠外 = 越优秀。")


# ⚠️ 不能加 @st.cache_data — 此函数闭包了 rp_full（已 scope 过滤），
#    跨用户共用 cache 会让 admin 的全省 baseline 被业务员看到，反之亦然
def compute_all_provider_stats():
    """该 scope 内所有服务商的 6 维度数据，用于百分位排名

    注：闭包变量 rp_full 已自动按当前用户 scope 过滤；
    所以 admin 看到的是全省 baseline，业务员看到的是 scope 内 baseline。
    """
    # 总台数
    qty = rp_full.groupby('上线客户编码').size().rename('qty')
    # 总金额
    amt = rp_full.groupby('上线客户编码')['产品现有分销价'].sum().rename('amt')
    # 红包金额
    won = rp_full[rp_full['中奖金额'] > 0]
    redpack = won.groupby('上线客户编码')['中奖金额'].sum().rename('redpack')
    # 产品多样性（系列数）
    series_n = rp_full.dropna(subset=['产品系列']).groupby('上线客户编码')['产品系列'].nunique().rename('series')
    # 忠诚率
    rp_full['_loyal'] = (rp_full['出货客户名称'].fillna('').astype(str).str.strip()
                          == rp_full['所属一级客户'].fillna('').astype(str).str.strip()) & \
                         (rp_full['所属一级客户'].fillna('').astype(str).str.strip() != '')
    loyal_avg = rp_full.groupby('上线客户编码')['_loyal'].mean().rename('loyal')
    # 活跃月份数
    months = rp_full.groupby('上线客户编码')['上线年月'].nunique().rename('months')

    out = pd.concat([qty, amt, redpack, series_n, loyal_avg, months], axis=1).fillna(0)
    return out


def percentile_rank(series, value):
    """返回 value 在 series 里的百分位排名（0~100）"""
    n = len(series)
    if n == 0:
        return 0
    return (series < value).sum() / n * 100


all_stats = compute_all_provider_stats()
my_qty = all_stats.loc[picked_code, 'qty'] if picked_code in all_stats.index else 0
my_amt = all_stats.loc[picked_code, 'amt'] if picked_code in all_stats.index else 0
my_red = all_stats.loc[picked_code, 'redpack'] if picked_code in all_stats.index else 0
my_ser = all_stats.loc[picked_code, 'series'] if picked_code in all_stats.index else 0
my_loy = all_stats.loc[picked_code, 'loyal'] if picked_code in all_stats.index else 0
my_mon = all_stats.loc[picked_code, 'months'] if picked_code in all_stats.index else 0

p_qty = percentile_rank(all_stats['qty'], my_qty)
p_amt = percentile_rank(all_stats['amt'], my_amt)
p_red = percentile_rank(all_stats['redpack'], my_red)
p_ser = percentile_rank(all_stats['series'], my_ser)
p_loy = percentile_rank(all_stats['loyal'], my_loy)
p_mon = percentile_rank(all_stats['months'], my_mon)

radar_labels = ['上线规模', '销售金额', '红包获益', '产品多样', '渠道忠诚', '上线频率']
radar_vals = [p_qty, p_amt, p_red, p_ser, p_loy, p_mon]

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    cn_font = get_chinese_font()
    matplotlib.rcParams['axes.unicode_minus'] = False

    N = len(radar_labels)
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    vals = radar_vals + radar_vals[:1]
    angles_close = angles + angles[:1]

    fig, ax = plt.subplots(figsize=(4.5, 4.5), subplot_kw=dict(polar=True))
    ax.plot(angles_close, vals, 'o-', linewidth=2, color='#10B981')
    ax.fill(angles_close, vals, alpha=0.3, color='#10B981')

    if cn_font is not None:
        ax.set_thetagrids(np.degrees(angles), radar_labels, fontproperties=cn_font, fontsize=9)
        ax.set_title(f"{picked_name} 全景画像", fontproperties=cn_font, fontsize=11, pad=16)
    else:
        ax.set_thetagrids(np.degrees(angles), radar_labels, fontsize=9)
        ax.set_title(f"Provider Profile", fontsize=11, pad=16)

    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(['20', '40', '60', '80', '100'], fontsize=7)

    img_buf = io.BytesIO()
    fig.savefig(img_buf, format='png', dpi=180, bbox_inches='tight')
    img_buf.seek(0)
    plt.close(fig)

    cc = st.columns([1, 2, 1])
    with cc[1]:
        st.image(img_buf.getvalue(), use_container_width=True)

    safe = ''.join(c if c.isalnum() or c in '_-（）' else '_' for c in picked_name)[:30]
    st.download_button(
        "📥 下载雷达图（PNG）",
        data=img_buf.getvalue(),
        file_name=f"画像_{safe}_{picked_code}.png",
        mime="image/png",
    )
except Exception as e:
    st.error(f"雷达图绘制失败：{e}")

# 百分位说明
st.caption(
    f"📊 百分位详情：上线规模 P{p_qty:.0f} · 销售金额 P{p_amt:.0f} · "
    f"红包获益 P{p_red:.0f} · 产品多样 P{p_ser:.0f} · "
    f"渠道忠诚 P{p_loy:.0f} · 上线频率 P{p_mon:.0f}"
)


st.divider()

# ══════════════════════════════════════════════
# V2 → V3 衔接
# ══════════════════════════════════════════════
from _ai_handoff import render_ai_followup_button as _render_ai_followup_button  # noqa: E402

st.divider()
st.markdown('#### 💬 想就这个服务商深挖个性化分析？')
st.caption('比如做对标、画时间序列、推断风险 —— 跳过去让 AI 写代码做。')
_render_ai_followup_button(
    source_page='05·🔭 服务商全景',
    context_summary=(
        '用户刚浏览了某个服务商的全景画像\n'
        '画像包含：基础档案（provider_profile）+ 上线明细（install_redpack_v）+ '
        '拜访明细（visit_record_v）+ 服务商等级（provider_tier_v）'
    ),
    suggested_followups=[
        '这个服务商 vs 同地市同等级中位数 — 哪些指标偏离最大',
        '把这个服务商所有上线产品按系列做饼图',
        '基于历史上线节奏推断这个服务商下次最可能激活的时间窗',
        '这个服务商关联的业务员有几个，谁拜访最多',
    ],
    key_suffix='page05_end',
)
