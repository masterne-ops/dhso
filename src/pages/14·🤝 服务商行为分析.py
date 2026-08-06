#!/usr/bin/env python3
"""
服务商行为分析（独立 page）
- 数据源：install_redpack（安装红包记录）
- 识别 3 类服务商：
    🚨 转向其他渠道：连续 N 月（默认 3）签约代理商进货数 = 0（仍在采购，但全转其他代理商）
    ⚠️ 混合采购：窗口内既有签约采购，也有非签约采购（可能产品缺货分流）
    🌾 无签约关系：所属一级客户为空，未签约的野生服务商
    ⚠️ 注意：完全停止采购的服务商不在红包表里 → 不会被识别（这是数据局限）
    ✅ 正常：仅从签约代理商采购
- 不依赖"基准月/对比月"概念，独立于 SO 环比分析
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# 引入共享加载（跨 page 共享缓存，避免重复加载 42 万行红包表）
sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import load_redpack_shared, DB_PATH  # noqa: E402

require_auth()
st.markdown("### 🤝 服务商行为分析（基于安装红包记录）")
st.caption("识别流失服务商（连续 N 月不从签约代理商进货）和混合采购异常（既签约又非签约）")


def load_redpack() -> pd.DataFrame:
    """共享 loader 包装，保持原函数名调用兼容"""
    return load_redpack_shared()


# ──────────────────────────────────────────
# 业务逻辑
# ──────────────────────────────────────────

def get_window_months(all_months: list, anchor: str, window: int) -> list:
    """从 all_months 中取以 anchor 为最末月、长度为 window 的连续月份列表。"""
    if anchor not in all_months:
        return []
    idx = all_months.index(anchor)
    start = max(0, idx - window + 1)
    return all_months[start:idx + 1]


def classify_providers(rp_window: pd.DataFrame) -> pd.DataFrame:
    """
    给窗口内的红包记录打标，返回每个服务商一行。
    列：上线客户编码 / 上线客户名称 / 签约代理商 / 状态 /
         签约进货 / 其他进货 / 总进货 / 签约占比 /
         主要其他代理商 / 主要其他代理商台数
    """
    if rp_window.empty:
        return pd.DataFrame()

    # ⚠️ 修复：用 .where(False, 0) 避免 lambda 反查 index 的 fragility
    rp_w = rp_window.copy()
    rp_w['_签约金额'] = rp_w['产品现有分销价'].where(rp_w['是否签约'], 0)
    rp_w['_其他金额'] = rp_w['产品现有分销价'].where(~rp_w['是否签约'], 0)

    # 按服务商汇总
    by_provider = rp_w.groupby(
        ['上线客户编码', '上线客户名称'], dropna=False
    ).agg(
        签约进货=('是否签约', lambda s: int(s.sum())),
        其他进货=('是否签约', lambda s: int((~s).sum())),
        总进货=('产品序列号', 'count'),
        签约金额=('_签约金额', 'sum'),
        其他金额=('_其他金额', 'sum'),
    ).reset_index()

    # 服务商签约的代理商：取窗口内最高频的『所属一级客户』（≠空）
    rp_w_with_dealer = rp_w[
        rp_w['所属一级客户'].fillna('').astype(str).str.strip() != ''
    ]
    if not rp_w_with_dealer.empty:
        signed = (
            rp_w_with_dealer.groupby('上线客户编码')['所属一级客户']
            .agg(lambda s: s.dropna().mode().iloc[0] if len(s.dropna().mode()) > 0 else None)
            .reset_index()
            .rename(columns={'所属一级客户': '签约代理商'})
        )
        by_provider = by_provider.merge(signed, on='上线客户编码', how='left')
    else:
        by_provider['签约代理商'] = None

    # ⚠️ 修复：标记"无签约"野生服务商（所属一级客户全空）
    no_sign_codes = set(
        rp_window[rp_window['所属一级客户'].fillna('').astype(str).str.strip() == '']
        ['上线客户编码'].unique()
    )

    # 状态判定（互斥）— 改名「流失」为「转向其他渠道」更准
    # 因为真正"完全停止采购"的服务商根本不在红包表里 → 不会被识别
    def classify(row):
        code = row['上线客户编码']
        if code in no_sign_codes and row['签约代理商'] is None:
            return '🌾 无签约关系'  # 野生服务商
        if row['签约进货'] == 0 and row['其他进货'] > 0:
            return '🚨 转向其他渠道'  # 改名（原"流失"语义错位）
        if row['签约进货'] > 0 and row['其他进货'] > 0:
            return '⚠️ 混合采购'
        if row['签约进货'] > 0 and row['其他进货'] == 0:
            return '✅ 正常'
        return '· 无效'

    by_provider['状态'] = by_provider.apply(classify, axis=1)

    # 签约占比
    by_provider['签约占比'] = np.where(
        by_provider['总进货'] > 0,
        by_provider['签约进货'] / by_provider['总进货'],
        np.nan,
    )

    # 主要"其他"代理商 Top 1
    other_records = rp_window[~rp_window['是否签约']]
    if not other_records.empty:
        top_other = (
            other_records.groupby(['上线客户编码', '出货客户名称'])
            .size().reset_index(name='_cnt')
            .sort_values(['上线客户编码', '_cnt'], ascending=[True, False])
            .drop_duplicates(subset='上线客户编码', keep='first')
            .rename(columns={'出货客户名称': '主要其他代理商', '_cnt': '主要其他代理商台数'})
        )
        by_provider = by_provider.merge(
            top_other[['上线客户编码', '主要其他代理商', '主要其他代理商台数']],
            on='上线客户编码', how='left',
        )
    else:
        by_provider['主要其他代理商'] = None
        by_provider['主要其他代理商台数'] = 0

    by_provider['主要其他代理商台数'] = by_provider['主要其他代理商台数'].fillna(0).astype(int)

    return by_provider


def fmt_pct(v):
    if pd.isna(v):
        return '—'
    return f'{v * 100:.1f}%'


def fmt_money(v):
    if pd.isna(v):
        return '—'
    return f'{v:,.0f}'


def beautify_provider_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if '签约占比' in out.columns:
        out['签约占比'] = out['签约占比'].map(fmt_pct)
    for c in ('签约金额', '其他金额'):
        if c in out.columns:
            out[c] = out[c].map(fmt_money)
    # 列顺序
    preferred = [
        '状态', '上线客户编码', '上线客户名称', '签约代理商',
        '签约进货', '其他进货', '总进货', '签约占比',
        '签约金额', '其他金额',
        '主要其他代理商', '主要其他代理商台数',
    ]
    cols_in_order = [c for c in preferred if c in out.columns]
    return out[cols_in_order]


# ──────────────────────────────────────────
# UI
# ──────────────────────────────────────────

def main():
    rp_all = load_redpack()
    if rp_all.empty:
        st.warning("数据库中没有『安装红包记录』数据。")
        try:
            st.page_link("app.py", label="↩ 到主页选择「安装红包记录」并上传 Excel", icon="📥")
        except Exception:
            pass
        st.stop()

    all_months = sorted(rp_all['上线年月'].dropna().unique())
    if not all_months:
        st.warning("红包表里没有有效的上线时间，无法分析。")
        st.stop()

    # 城市候选项（按全表服务商数倒序）
    city_options = []
    if '上线客户地市' in rp_all.columns:
        city_options = (
            rp_all['上线客户地市'].dropna().astype(str).str.strip()
            .replace('', pd.NA).dropna()
            .value_counts().index.tolist()
        )

    # ── 侧栏参数 ──────────────────────────
    with st.sidebar:
        st.header("分析参数")

        anchor = st.selectbox(
            "锚点月份（窗口的最后一个月）",
            options=all_months,
            index=len(all_months) - 1,
            help="以这个月为窗口末端，往前数 N 个月做分析",
        )

        window = st.selectbox(
            "窗口大小（月）",
            options=[1, 2, 3, 4, 6, 12],
            index=2,  # 默认 3
            help="连续 N 个月内观察签约/非签约采购情况；流失定义即基于此窗口",
        )

        picked_cities = st.multiselect(
            "上线客户地市筛选（不选 = 全部）",
            options=city_options,
            default=[],
            help=f"按服务商所在城市筛选，共 {len(city_options)} 个候选城市",
        )

        window_months = get_window_months(all_months, anchor, window)
        st.divider()
        st.caption(
            f"📅 分析窗口：**{window_months[0]} ~ {window_months[-1]}** "
            f"(共 {len(window_months)} 月)"
        )
        if len(window_months) < window:
            st.warning(f"⚠️ 数据不足 {window} 个月，实际窗口为 {len(window_months)} 月")

    # ── 计算 ──────────────────────────────
    rp_window = rp_all[rp_all['上线年月'].isin(window_months)]
    # 应用城市筛选
    if picked_cities and '上线客户地市' in rp_window.columns:
        rp_window = rp_window[
            rp_window['上线客户地市'].astype(str).str.strip().isin(picked_cities)
        ]
    if rp_window.empty:
        st.warning("窗口内没有红包记录，请调整锚点月份 / 窗口 / 城市筛选。")
        st.stop()

    if picked_cities:
        st.caption(f"🌆 已筛选地市：{', '.join(picked_cities)}　窗口内 {len(rp_window):,} 条记录")

    classified = classify_providers(rp_window)
    if classified.empty:
        st.info("窗口内没有可分析的服务商。")
        st.stop()

    # ── 顶部 KPI ──────────────────────────
    n_total = len(classified)
    n_lost = (classified['状态'] == '🚨 转向其他渠道').sum()
    n_mixed = (classified['状态'] == '⚠️ 混合采购').sum()
    n_normal = (classified['状态'] == '✅ 正常').sum()
    n_no_sign = (classified['状态'] == '🌾 无签约关系').sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("窗口内服务商总数", f"{n_total:,}")
    c2.metric(
        "🚨 转向其他渠道",
        f"{n_lost}",
        help=f"窗口 {len(window_months)} 月内 0 台从签约代理商进货（仍在采购，但全部转向其他代理商）"
             f"\n⚠️ 注意：完全停止采购的服务商不在红包表里 → 不会被识别为流失",
    )
    c3.metric("⚠️ 混合采购", f"{n_mixed}", help="既有签约采购也有非签约采购")
    c4.metric("✅ 正常", f"{n_normal}", help="仅从签约代理商采购")

    if n_no_sign > 0:
        st.warning(
            f"🌾 另有 **{n_no_sign}** 个服务商窗口内有上线但**所属一级客户为空**（无签约关系野生服务商），"
            f"已单独分类，未计入上面 4 个状态。"
        )

    if n_total > 0:
        rate_lost = n_lost / n_total
        rate_mixed = n_mixed / n_total
        st.caption(
            f"转向其他渠道率 {rate_lost * 100:.1f}% · 混合率 {rate_mixed * 100:.1f}% · "
            f"正常率 {(n_normal / n_total) * 100:.1f}%"
        )

    st.divider()

    # ── 三个 Tab ──────────────────────────
    tab_lost, tab_mixed, tab_normal = st.tabs([
        f"🚨 转向其他渠道 ({n_lost})",
        f"⚠️ 混合采购 ({n_mixed})",
        f"✅ 正常 ({n_normal})",
    ])

    with tab_lost:
        st.markdown("**判定**：窗口内 0 台从签约代理商进货，但有从其他代理商进货 → 视为流失或转向竞品")
        sub = classified[classified['状态'] == '🚨 转向其他渠道'].sort_values('其他进货', ascending=False)
        if sub.empty:
            st.success("窗口内没有流失服务商 🎉")
        else:
            st.dataframe(
                beautify_provider_table(sub),
                use_container_width=True,
                hide_index=True,
                height=500,
            )

    with tab_mixed:
        st.markdown(
            "**判定**：窗口内既从签约代理商进货，也从其他代理商进货 → "
            "可能是签约代理商缺货导致服务商分流，需提醒代理商"
        )
        sub = classified[classified['状态'] == '⚠️ 混合采购'].sort_values('其他进货', ascending=False)
        if sub.empty:
            st.success("窗口内没有混合采购的服务商")
        else:
            st.dataframe(
                beautify_provider_table(sub),
                use_container_width=True,
                hide_index=True,
                height=500,
            )

    with tab_normal:
        st.markdown("**判定**：窗口内只从签约代理商进货 — 关系健康")
        sub = classified[classified['状态'] == '✅ 正常'].sort_values('签约进货', ascending=False)
        if sub.empty:
            st.info("窗口内没有正常服务商")
        else:
            st.dataframe(
                beautify_provider_table(sub),
                use_container_width=True,
                hide_index=True,
                height=500,
            )

    st.divider()

    # ── 选定服务商下钻：每月明细 ──────────────────────────
    st.markdown("### 🔍 服务商下钻（按月明细）")
    provider_options = (
        classified[['上线客户编码', '上线客户名称', '状态']]
        .assign(_label=lambda d: d['状态'] + ' | ' + d['上线客户名称'].fillna('(空)') + ' | ' + d['上线客户编码'].astype(str))
        .sort_values(['状态', '上线客户名称'])
    )
    pick_label = st.selectbox(
        "选择服务商查看月度明细",
        options=provider_options['_label'].tolist(),
        index=0,
    )
    pick_code = pick_label.split(' | ')[-1]

    sub = rp_window[rp_window['上线客户编码'].astype(str) == str(pick_code)]
    if sub.empty:
        st.info("该服务商在窗口内无记录")
    else:
        # 月度签约 vs 非签约
        monthly = sub.groupby(['上线年月', '是否签约']).agg(
            台数=('产品序列号', 'count'),
            金额=('产品现有分销价', 'sum'),
        ).reset_index()
        pivot = monthly.pivot_table(
            index='上线年月',
            columns='是否签约',
            values='台数',
            fill_value=0,
        ).reset_index()
        pivot.columns = [
            '上线年月' if c == '上线年月' else ('签约采购' if c is True else '其他采购')
            for c in pivot.columns
        ]
        # 补齐缺列
        for c in ('签约采购', '其他采购'):
            if c not in pivot.columns:
                pivot[c] = 0
        pivot = pivot[['上线年月', '签约采购', '其他采购']].sort_values('上线年月')
        pivot['总计'] = pivot['签约采购'] + pivot['其他采购']

        # 补齐窗口内空月份
        existing_months = set(pivot['上线年月'].tolist())
        for m in window_months:
            if m not in existing_months:
                pivot = pd.concat([pivot, pd.DataFrame([{
                    '上线年月': m, '签约采购': 0, '其他采购': 0, '总计': 0,
                }])], ignore_index=True)
        pivot = pivot.sort_values('上线年月').reset_index(drop=True)

        col_l, col_r = st.columns([2, 1])
        with col_l:
            st.caption("月度签约 vs 其他采购")
            st.dataframe(pivot, use_container_width=True, hide_index=True)
        with col_r:
            st.caption("月度趋势")
            st.bar_chart(pivot.set_index('上线年月')[['签约采购', '其他采购']])

        # 其他代理商分布（如果有）
        sub_other = sub[~sub['是否签约']]
        if not sub_other.empty:
            st.caption("📌 该服务商在窗口内从哪些其他代理商采购")
            other_dist = (
                sub_other.groupby('出货客户名称')
                .agg(台数=('产品序列号', 'count'),
                     金额=('产品现有分销价', 'sum'))
                .reset_index()
                .sort_values('台数', ascending=False)
            )
            other_dist['金额'] = other_dist['金额'].map(fmt_money)
            st.dataframe(other_dist, use_container_width=True, hide_index=True)


main()
