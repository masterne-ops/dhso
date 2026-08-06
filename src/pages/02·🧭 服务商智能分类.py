#!/usr/bin/env python3
"""
🧭 服务商智能分类（核心页）
- RFM 5 类 MECE 智能分群（🏆 核心活跃 / 🌱 培育客户 / ⚠️ 流失风险 / 🪦 已流失 / ❓ 未采购客户）
- × 沙盘+签约 × 跑动 三组数据交叉，输出"该派单"名单
- 4 大派单模块：紧急派单 / 漏跑预警 / 培育客户 / 空跑浪费
- 每张名单都可导出 Excel 直接给业务跟进
"""

import io
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import (  # noqa: E402
    load_redpack_shared,
    load_provider_master_shared,
    load_visit_shared,
)

require_auth()
st.markdown("### 🧭 服务商智能分类")
st.caption(
    "三组数据交叉：**RFM 价值（红包表）× 客户画像（沙盘+签约）× 跑动行为（打卡）**  →  "
    "每天打开就知道：**该跑谁、找谁聊、聊什么**"
)

# 紧凑指标
st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.1rem !important; line-height: 1.1; }
[data-testid="stMetricLabel"] { font-size: 0.78rem !important; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────

rp = load_redpack_shared()
master = load_provider_master_shared()
visits = load_visit_shared()

# 授牌服务商 = 无价值客户(管理标签口径,见 _provider_flags):本页是派单/跑动分配页,整体排除
from _provider_flags import worthless_codes as _worthless_codes  # noqa: E402
_wc = _worthless_codes()
if _wc and '客户编码' in master.columns:
    _n0 = len(master)
    master = master[~master['客户编码'].astype(str).isin(_wc)]
    if _n0 - len(master):
        st.caption(f"🚫 已排除 管理标签=授牌服务商(无价值客户) {_n0 - len(master):,} 家 — 不进分类与派单")

if visits.empty:
    st.warning(
        "📦 业务员跑动记录暂无数据。请到主页选择「业务员跑动记录」并上传 Excel。"
    )
    st.stop()


# ──────────────────────────────────────────
# 工具：基于红包表派生 RFM 类别（与 page 0 同口径）
# ──────────────────────────────────────────

# ⚠️ 这 3 个函数不能用 @st.cache_data — 它们的输入（rp/visits 模块变量）已经是
#    "当前用户 scope 过滤后" 的数据，跨用户共用一个 cache 会泄露其他用户的数据。
#    底层 _loaders.compute_rfm_shared / load_redpack_shared / load_visit_shared
#    已自己做了 raw 缓存 + scope 包装，这层不必再缓存。

def compute_rfm_classes(window_months: int = 12):
    """复用共享 RFM 算法（_loaders.compute_rfm_shared）— 5 类 MECE 分类

    🔒 _loaders.compute_rfm_shared 已自动按当前用户 scope 过滤。
    """
    from _loaders import compute_rfm_shared
    rfm = compute_rfm_shared(window_months=window_months)
    if rfm.empty:
        return pd.DataFrame()
    return rfm[['客户编码', '上线客户名称', '签约代理商', 'R', 'F', 'M', 'RFM类别']]


def compute_last_visit():
    """每个客户最近一次拜访的关键信息（基于 visits — 已 scope 过滤）"""
    if visits.empty:
        return pd.DataFrame()
    v = visits.copy()
    v = v.dropna(subset=['客户编码', '拜访时间'])
    v['客户编码'] = v['客户编码'].astype(str).str.strip()
    # 排序后取每个客户最近一次
    v = v.sort_values('拜访时间', ascending=False)
    last = v.drop_duplicates(subset='客户编码', keep='first')
    return last[[
        '客户编码', '拜访客户', '拜访时间',
        '打卡人姓名', '打卡人所属公司', '_打卡方',
        '客户所有者', '所有者部门',
        '距离偏离_米',
    ]]


def compute_visit_count_by_client(days: int = None):
    """每个客户被拜访的次数（基于 visits — 已 scope 过滤）"""
    if visits.empty:
        return pd.DataFrame()
    v = visits.dropna(subset=['客户编码', '拜访时间']).copy()
    v['客户编码'] = v['客户编码'].astype(str).str.strip()
    if days:
        cutoff = visits['拜访时间'].max() - pd.Timedelta(days=days)
        v = v[v['拜访时间'] >= cutoff]
    return v.groupby('客户编码').agg(
        拜访次数=('活动编号', 'count'),
        拜访人=('打卡人姓名', lambda x: ', '.join(x.dropna().unique()[:5])),
        拜访人数=('打卡人姓名', lambda x: x.dropna().nunique()),
    ).reset_index()


# ──────────────────────────────────────────
# 侧栏：参数 + 筛选
# ──────────────────────────────────────────

with st.sidebar:
    st.header("派单参数")

    # 时间窗口
    window_months = st.selectbox(
        "RFM 分析窗口（月）",
        options=[3, 6, 12, 24],
        index=2,
        key="dispatch_window",
        help="基于这个窗口算 RFM",
    )

    st.divider()
    st.markdown("**漏跑判定阈值**")
    risk_th = st.number_input("⚠️ 流失风险超期天数",
                                 value=14, min_value=1, key="risk_th",
                                 help="流失风险客户应该立即跟进，超 14 天就算太晚")
    cham_th = st.number_input("🏆 核心活跃漏跑天数",
                                 value=30, min_value=7, key="cham_th",
                                 help="核心活跃 = 频次高 或 货值高 + 最近还在采购；超 30 天没拜访 = 漏跑")

    st.divider()
    st.markdown("**异常筛查**")
    dist_th = st.number_input("距离偏离超过（米）",
                                value=1000, min_value=100, key="dist_th")
    purpose_min_chars = st.number_input("拜访目的字数 ≤",
                                            value=10, min_value=0, key="purp_th",
                                            help="字数太少 = 应付填写嫌疑")

    st.divider()
    st.markdown("**地理筛选（三级联动）**")

    # 省份候选
    if not visits.empty:
        prov_opts = sorted(
            visits['拜访客户省份'].dropna().astype(str).str.strip().unique().tolist()
        )
        prov_opts = [p for p in prov_opts if p]
    else:
        prov_opts = []

    picked_provinces = st.multiselect(
        "省份（不选 = 全部）",
        options=prov_opts,
        default=[],
        key="dispatch_provs",
    )

    # 地市候选根据省份联动
    if picked_provinces and not visits.empty:
        city_pool = visits[
            visits['拜访客户省份'].astype(str).str.strip().isin(picked_provinces)
        ]
    else:
        city_pool = visits
    city_opts = sorted(
        city_pool['拜访客户城市'].dropna().astype(str).str.strip().unique().tolist()
    ) if not city_pool.empty else []
    city_opts = [c for c in city_opts if c]

    picked_cities = st.multiselect(
        "地市（不选 = 该省份全部）",
        options=city_opts,
        default=[],
        key="dispatch_cities",
    )

    # 区县候选根据地市联动
    if picked_cities and not visits.empty:
        district_pool = visits[
            visits['拜访客户城市'].astype(str).str.strip().isin(picked_cities)
        ]
    elif picked_provinces and not visits.empty:
        district_pool = visits[
            visits['拜访客户省份'].astype(str).str.strip().isin(picked_provinces)
        ]
    else:
        district_pool = visits
    district_opts = sorted(
        district_pool['拜访客户区县'].dropna().astype(str).str.strip().unique().tolist()
    ) if not district_pool.empty else []
    district_opts = [d for d in district_opts if d]

    picked_districts = st.multiselect(
        "区县（不选 = 该地市全部）",
        options=district_opts,
        default=[],
        key="dispatch_districts",
    )

    st.divider()
    st.markdown("**代理商筛选**")

    # 代理商候选：取自 master 的「上级分销商名称」+ visits 的「打卡人所属公司」
    dealer_pool = set()
    if not master.empty and '上级分销商名称' in master.columns:
        # 已应用地理筛选条件下的代理商
        m_filt = master.copy()
        m_filt['客户编码'] = m_filt['客户编码'].astype(str).str.strip()
        if picked_provinces and '省份' in m_filt.columns:
            m_filt = m_filt[m_filt['省份'].astype(str).str.strip().isin(picked_provinces)]
        if picked_cities and '地市' in m_filt.columns:
            m_filt = m_filt[m_filt['地市'].astype(str).str.strip().isin(picked_cities)]
        if picked_districts and '区县' in m_filt.columns:
            m_filt = m_filt[m_filt['区县'].astype(str).str.strip().isin(picked_districts)]
        dealer_pool |= set(
            m_filt['上级分销商名称'].dropna().astype(str).str.strip().tolist()
        )
    if not visits.empty and '打卡人所属公司' in visits.columns:
        v_filt = visits.copy()
        if picked_provinces and '拜访客户省份' in v_filt.columns:
            v_filt = v_filt[
                v_filt['拜访客户省份'].astype(str).str.strip().isin(picked_provinces)
            ]
        if picked_cities and '拜访客户城市' in v_filt.columns:
            v_filt = v_filt[
                v_filt['拜访客户城市'].astype(str).str.strip().isin(picked_cities)
            ]
        if picked_districts and '拜访客户区县' in v_filt.columns:
            v_filt = v_filt[
                v_filt['拜访客户区县'].astype(str).str.strip().isin(picked_districts)
            ]
        dealer_pool |= set(
            v_filt['打卡人所属公司'].dropna().astype(str).str.strip().tolist()
        )
    dealer_pool.discard('')
    dealer_opts = sorted(dealer_pool)

    picked_dealers = st.multiselect(
        "代理商（不选 = 全部）",
        options=dealer_opts,
        default=[],
        key="dispatch_dealers",
        help=(
            "同时匹配两个口径：\n"
            "① 客户的「上级分销商名称」（沙盘+签约表）\n"
            "② 业务员打卡的「打卡人所属公司」（跑动表）"
        ),
    )


def _apply_geo_filter(df, prov_col, city_col, dist_col):
    """对一个 df 应用三级地理筛选"""
    if df.empty:
        return df
    out = df
    if picked_provinces and prov_col in out.columns:
        out = out[out[prov_col].astype(str).str.strip().isin(picked_provinces)]
    if picked_cities and city_col in out.columns:
        out = out[out[city_col].astype(str).str.strip().isin(picked_cities)]
    if picked_districts and dist_col in out.columns:
        out = out[out[dist_col].astype(str).str.strip().isin(picked_districts)]
    return out


# 兼容老调用
def _filter_city(df, col):
    if df.empty:
        return df
    # 推断省/市/县列
    if '拜访客户' in col or col == '拜访客户城市':
        return _apply_geo_filter(df, '拜访客户省份', '拜访客户城市', '拜访客户区县')
    if col == '地市':
        return _apply_geo_filter(df, '省份', '地市', '区县')
    return df


# 计算
rfm = compute_rfm_classes(window_months)
last_visit = compute_last_visit()
anchor_visit = visits['拜访时间'].max() if not visits.empty else pd.NaT


# ──────────────────────────────────────────
# 顶部 KPI
# ──────────────────────────────────────────

# 关联沙盘+签约+RFM+最近拜访
master_subset = master.copy() if not master.empty else pd.DataFrame()
if not master_subset.empty:
    master_subset['客户编码'] = master_subset['客户编码'].astype(str).str.strip()
    master_subset = _apply_geo_filter(master_subset, '省份', '地市', '区县')

# 跑动覆盖的客户（按省/市/县筛选）
visits_filtered = _apply_geo_filter(visits, '拜访客户省份', '拜访客户城市', '拜访客户区县')

# 应用代理商筛选（同时匹配 上级分销商 和 打卡人所属公司）
if picked_dealers:
    dealer_set = set(picked_dealers)

    # master_subset 按 上级分销商名称 过滤
    if not master_subset.empty and '上级分销商名称' in master_subset.columns:
        master_subset = master_subset[
            master_subset['上级分销商名称'].astype(str).str.strip().isin(dealer_set)
        ]

    # visits_filtered：保留两类打卡
    #   (a) 打卡人所属公司在筛选代理商内
    #   (b) 客户编码在 master_subset（被筛过的代理商客户）内
    keep_codes = (
        set(master_subset['客户编码'].astype(str).str.strip())
        if not master_subset.empty else set()
    )
    if not visits_filtered.empty:
        cond_company = (
            visits_filtered['打卡人所属公司'].astype(str).str.strip().isin(dealer_set)
            if '打卡人所属公司' in visits_filtered.columns
            else pd.Series(False, index=visits_filtered.index)
        )
        cond_code = (
            visits_filtered['客户编码'].astype(str).str.strip().isin(keep_codes)
            if keep_codes else pd.Series(False, index=visits_filtered.index)
        )
        visits_filtered = visits_filtered[cond_company | cond_code]
unique_visited_codes = set(
    visits_filtered['客户编码'].dropna().astype(str).str.strip()
) if not visits_filtered.empty else set()

# RFM 类别人数（用筛选后的跑动客户编码 + 沙盘客户编码 取并集筛 RFM）
rfm_filtered = rfm.copy()
geo_active = picked_provinces or picked_cities or picked_districts
if geo_active and not rfm_filtered.empty:
    valid_codes = set()
    if not visits_filtered.empty:
        valid_codes |= set(visits_filtered['客户编码'].astype(str).str.strip())
    if not master_subset.empty:
        valid_codes |= set(master_subset['客户编码'].astype(str).str.strip())
    rfm_filtered = rfm_filtered[rfm_filtered['客户编码'].isin(valid_codes)]

# 异常打卡（排除「系统客户地址信息维护错误 / 客户多地址办公」等已标注的非真实异常）
abn_count = 0
if not visits_filtered.empty:
    visits_filtered['距离偏离_米'] = pd.to_numeric(
        visits_filtered['距离偏离_米'], errors='coerce'
    )
    invalid_anomaly_set = {
        '系统客户地址信息维护错误，后续更正',
        '系统客户地址信息维护错误,后续更正',
        '客户多地址办公',
        '系统客户地址信息维护错误',
    }
    is_invalid_anomaly = visits_filtered.get(
        '打卡异常类型', pd.Series('', index=visits_filtered.index)
    ).fillna('').astype(str).isin(invalid_anomaly_set)
    abn_count = int(
        ((visits_filtered['距离偏离_米'] > dist_th).fillna(False) & ~is_invalid_anomaly).sum()
    )

st.markdown("##### 📊 全局概览")
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("跑动覆盖服务商", f"{len(unique_visited_codes):,}")
k2.metric("窗口内打卡量",
          f"{len(visits_filtered):,}",
          help=f"全部时段（{visits['拜访时间'].min().strftime('%Y-%m') if pd.notna(visits['拜访时间'].min()) else '—'} ~ "
               f"{anchor_visit.strftime('%Y-%m') if pd.notna(anchor_visit) else '—'}）")
k3.metric("异常打卡",
          f"{abn_count:,}",
          help=f"距离偏离 > {dist_th} 米")
n_da = (visits_filtered['_是否大华']).sum() if not visits_filtered.empty else 0
n_dl = (~visits_filtered['_是否大华']).sum() if not visits_filtered.empty else 0
k4.metric("🏢 大华打卡", f"{n_da:,}")
k5.metric("🏪 代理商打卡", f"{n_dl:,}")

if not rfm_filtered.empty:
    cat_dist = rfm_filtered['RFM类别'].value_counts()
    n_core = int(cat_dist.get('🏆 核心活跃', 0))
    n_risk = int(cat_dist.get('⚠️ 流失风险', 0))
    n_grow = int(cat_dist.get('🌱 培育客户', 0))
    n_lost = int(cat_dist.get('🪦 已流失', 0))
    st.caption(
        f"📊 当前 RFM 分布（基于红包表，仅覆盖有上线记录的客户）："
        f"🏆 {n_core} · ⚠️ {n_risk} · 🌱 {n_grow} · 🪦 {n_lost}"
    )
else:
    st.caption("⚠️ 红包表暂无数据，无法生成 RFM 分类（漏跑/紧急派单的优先级会受限）")

st.divider()


# ──────────────────────────────────────────
# 🎯 RFM × 拜访次数 矩阵（核心：四象限分析）
# ──────────────────────────────────────────

st.markdown("### 🎯 RFM × 拜访次数 矩阵")

with st.expander("ℹ️ RFM 五类定义 — MECE 业务分类（点开看）", expanded=False):
    st.markdown(
        "**判定逻辑**：先看是否有红包记录？没有 → ❓ 未采购客户；有 → 按 R（最近活跃）× 价值（F 频次 或 M 货值）分 4 类。\n\n"
        "| 类别 | 判定条件 | 业务含义 | 优先动作 |\n"
        "|------|----------|----------|----------|\n"
        "| 🏆 **核心活跃** | R ≤ 60 天 AND (F ≥ 6 次 OR M ≥ 1万元) | 频次高 或 货值高 + 最近还在采购 | **重点保留 / 持续维护** |\n"
        "| ⚠️ **流失风险** | R > 60 天 AND (F ≥ 6 次 OR M ≥ 1万元) | 曾是好客户，最近 2 月没动 | **紧急救援** |\n"
        "| 🌱 **培育客户** | R ≤ 60 天 AND F < 6 次 AND M < 1万元 | 最近在采购但量小 | **培育拉单 / 提频次** |\n"
        "| 🪦 **已流失** | R > 60 天 AND F < 6 次 AND M < 1万元 | 量小 + 长期不动 | **战略放弃 / 简化跟进** |\n"
        "| ❓ **未采购客户** | 在拜访 / 签约表里有，但红包扫码 0 条 | 完全没采购历史 | **看签约/拜访状态决定** |\n\n"
        "**阈值说明**：\n"
        "- R = 60 天：业务行业惯例，活跃/沉睡分界线\n"
        "- F = 6 次/12 月：≥ 2 个月一次稳定采购\n"
        "- M = 1 万元：相当于 V3 服务商等级（V3 = 累计 1-3 万）\n\n"
        "5 类 MECE — 每个有红包记录的客户精确归 1 类；没红包的统一归「❓ 未采购客户」。"
    )

st.caption(
    "每个服务商按 RFM 类别 + 窗口内拜访次数定位到一个格子。"
    "🟢 健康（高价值高频/低价值低频）｜ 🟡 漏跑（高价值低频）｜ 🔴 空跑（低价值高频）｜ ⚪ 合理"
)

# 用户可调"高价值"和"高频"的判定
mtx_cols = st.columns([1, 1, 3])
with mtx_cols[0]:
    mtx_window_days = st.number_input(
        "拜访窗口（天）", value=90, min_value=7, key="mtx_window",
        help="只看最近 N 天的拜访",
    )
with mtx_cols[1]:
    mtx_high_threshold = st.number_input(
        "高频阈值（≥次）", value=3, min_value=2, key="mtx_high",
        help="窗口内拜访 ≥ N 次算「高频」",
    )

# 计算每个客户在窗口内的拜访次数
visit_count_window = compute_visit_count_by_client(days=mtx_window_days)
if visit_count_window is None or visit_count_window.empty:
    visit_count_window = pd.DataFrame(columns=['客户编码', '拜访次数', '拜访人'])

# 应用筛选
if (picked_provinces or picked_cities or picked_districts) and not visits_filtered.empty:
    codes_in_geo = set(visits_filtered['客户编码'].astype(str).str.strip())
    visit_count_window = visit_count_window[
        visit_count_window['客户编码'].astype(str).isin(codes_in_geo)
    ]

# 准备完整服务商池：rfm_filtered + 沙盘 + 跑动 编码并集
all_codes = set()
if not rfm_filtered.empty:
    all_codes |= set(rfm_filtered['客户编码'].astype(str))
if not master_subset.empty:
    all_codes |= set(master_subset['客户编码'].astype(str))
if not visits_filtered.empty:
    all_codes |= set(visits_filtered['客户编码'].dropna().astype(str).str.strip())

# 拜访次数 -> 编码字典
vc_map = dict(zip(
    visit_count_window['客户编码'].astype(str),
    visit_count_window['拜访次数'].astype(int),
))
# RFM 类别 -> 编码字典
rfm_map = dict(zip(
    rfm_filtered['客户编码'].astype(str),
    rfm_filtered['RFM类别'],
)) if not rfm_filtered.empty else {}


def _bin_visit(n):
    if n == 0:
        return '0 次'
    if n == 1:
        return '1 次'
    if n < mtx_high_threshold:
        return f'2~{mtx_high_threshold - 1} 次'
    return f'≥ {mtx_high_threshold} 次（高频）'


# 类别顺序（高价值 → 低价值）
from _loaders import (  # noqa: E402
    RFM_CATEGORIES_ORDERED as _RFM_ORDER_BASE,
    RFM_HIGH_VALUE_CATS,
)
RFM_ORDER = list(_RFM_ORDER_BASE)  # 5 类：🏆 核心活跃 / 🌱 培育客户 / ⚠️ 流失风险 / 🪦 已流失 / ❓ 未采购客户
HIGH_VALUE_CATS = RFM_HIGH_VALUE_CATS
UNKNOWN_CAT = '❓ 未采购客户'   # 完全没有红包扫码记录的客户

VISIT_BIN_ORDER = ['0 次', '1 次',
                    f'2~{mtx_high_threshold - 1} 次',
                    f'≥ {mtx_high_threshold} 次（高频）']

# 构造矩阵
mtx_rows = []
for code in all_codes:
    cat = rfm_map.get(code, UNKNOWN_CAT)
    n = vc_map.get(code, 0)
    mtx_rows.append({
        '客户编码': code,
        'RFM类别': cat,
        '拜访次数': n,
        '_bin': _bin_visit(n),
    })
mtx_df_full = pd.DataFrame(mtx_rows)

if not mtx_df_full.empty:
    pivot = (mtx_df_full.groupby(['RFM类别', '_bin']).size()
             .unstack('_bin', fill_value=0)
             .reindex(RFM_ORDER, fill_value=0)
             .reindex(VISIT_BIN_ORDER, axis=1, fill_value=0))
    # 添加合计
    pivot['合计'] = pivot.sum(axis=1)
    # 转换为可显示的 DataFrame
    pivot_display = pivot.reset_index().rename(columns={'RFM类别': '⬇️ RFM 类别 / 拜访 ➡️'})

    # 颜色编码逻辑（用 Styler）
    high_freq_label = f'≥ {mtx_high_threshold} 次（高频）'
    low_freq_labels = {'0 次', '1 次'}

    def _color_cell(val, row_label, col_label):
        if col_label == '合计' or col_label == '⬇️ RFM 类别 / 拜访 ➡️':
            return ''
        if val == 0:
            return ''
        is_high_value = row_label in HIGH_VALUE_CATS
        is_high_freq = col_label == high_freq_label
        is_low_freq = col_label in low_freq_labels
        if is_high_value and is_high_freq:
            return 'background-color: #d4edda'  # 绿
        if is_high_value and is_low_freq:
            return 'background-color: #fff3cd'  # 黄
        if not is_high_value and is_high_freq:
            return 'background-color: #f8d7da'  # 红
        return ''

    def _style(df):
        def apply_row(row):
            row_label = row['⬇️ RFM 类别 / 拜访 ➡️']
            return [_color_cell(row[col], row_label, col) for col in df.columns]
        return df.style.apply(apply_row, axis=1)

    st.dataframe(
        _style(pivot_display),
        use_container_width=True,
        hide_index=True,
        height=min(60 + len(pivot_display) * 36, 380),
    )

    # 4 象限 KPI
    st.markdown("##### 🎯 四象限分布")
    n_healthy = int(mtx_df_full[
        (mtx_df_full['RFM类别'].isin(HIGH_VALUE_CATS))
        & (mtx_df_full['拜访次数'] >= mtx_high_threshold)
    ].shape[0])
    n_missed = int(mtx_df_full[
        (mtx_df_full['RFM类别'].isin(HIGH_VALUE_CATS))
        & (mtx_df_full['拜访次数'] < mtx_high_threshold)
    ].shape[0])
    low_value_cats = set(RFM_ORDER) - HIGH_VALUE_CATS - {UNKNOWN_CAT}
    n_wasted = int(mtx_df_full[
        (mtx_df_full['RFM类别'].isin(low_value_cats))
        & (mtx_df_full['拜访次数'] >= mtx_high_threshold)
    ].shape[0])
    n_reasonable = int(mtx_df_full[
        (mtx_df_full['RFM类别'].isin(low_value_cats))
        & (mtx_df_full['拜访次数'] < mtx_high_threshold)
    ].shape[0])
    n_unknown = int(mtx_df_full[
        mtx_df_full['RFM类别'] == UNKNOWN_CAT
    ].shape[0])

    q1, q2, q3, q4, q5 = st.columns(5)
    q1.metric("🟢 健康忠诚", f"{n_healthy:,}",
               help="高价值（🏆 核心活跃 / ⚠️ 流失风险）+ 高频拜访 → 关系稳定，继续维护")
    q2.metric("🟡 漏跑警报", f"{n_missed:,}",
               help="高价值 + 低频/0 次拜访 → 立即派单！")
    q3.metric("🔴 空跑浪费", f"{n_wasted:,}",
               help="低价值（🌱 培育客户 / 🪦 已流失）+ 高频拜访 → 资源错配，建议转移")
    q4.metric("⚪ 合理", f"{n_reasonable:,}",
               help="低价值 + 低频 → 投入合理")
    q5.metric("❓ 未采购客户", f"{n_unknown:,}",
               help="完全没有红包扫码记录 — 待开发；看跑动量决定优先级")

    if n_missed > 0 or n_wasted > 0:
        st.warning(
            f"⚠️ **行动建议**："
            f"立即派单 **{n_missed}** 个漏跑客户；"
            f"评估 **{n_wasted}** 个空跑客户是否值得继续投入"
        )

st.divider()


# ──────────────────────────────────────────
# 通用：构造派单名单
# ──────────────────────────────────────────

def build_candidate_table(codes_set, rfm_df, master_df, last_visit_df):
    """对一批客户编码，拼装含 RFM + 画像 + 最近拜访 的派单表"""
    if not codes_set:
        return pd.DataFrame()
    df = pd.DataFrame({'客户编码': list(codes_set)})
    df['客户编码'] = df['客户编码'].astype(str).str.strip()

    # 拼 RFM
    if not rfm_df.empty:
        df = df.merge(rfm_df, on='客户编码', how='left')

    # 拼画像（沙盘+签约）
    if not master_df.empty:
        keep = ['客户编码', '公司名称', '地市', '联系人', '联系电话',
                '老板姓名', '老板电话', '客户所有者',
                '分销商业务员姓名', '分销商业务员手机号',
                '上级分销商名称', '服务商等级_原始', '是否激活', '是否新签']
        keep_in = [c for c in keep if c in master_df.columns]
        df = df.merge(
            master_df[keep_in].drop_duplicates(subset='客户编码'),
            on='客户编码', how='left',
        )

    # 拼最近拜访
    if not last_visit_df.empty:
        lv = last_visit_df.rename(columns={
            '拜访客户': '_lv_拜访客户',
            '拜访时间': '最近拜访时间',
            '打卡人姓名': '最近拜访人',
            '_打卡方': '最近拜访方',
        })
        df = df.merge(
            lv[['客户编码', '最近拜访时间', '最近拜访人', '最近拜访方', '_lv_拜访客户']],
            on='客户编码', how='left',
        )
        # 距今天数
        if pd.notna(anchor_visit):
            df['距今天数'] = (anchor_visit - df['最近拜访时间']).dt.days

    # 服务商名优先用红包表的，fallback 公司名/拜访名
    df['服务商名称'] = df.get('上线客户名称')
    if df['服务商名称'].isna().any():
        df['服务商名称'] = df['服务商名称'].fillna(df.get('公司名称'))
    if df['服务商名称'].isna().any() and '_lv_拜访客户' in df.columns:
        df['服务商名称'] = df['服务商名称'].fillna(df['_lv_拜访客户'])
    df['服务商名称'] = df['服务商名称'].fillna('（未知）')

    # 联系电话优先：联系电话 > 老板电话 > 分销商业务员手机号
    df['联系电话_最佳'] = (
        df.get('联系电话', pd.Series(dtype=object)).fillna('')
        .replace(['', 'nan'], np.nan)
        .fillna(df.get('老板电话', pd.Series(dtype=object)))
        .fillna(df.get('分销商业务员手机号', pd.Series(dtype=object)))
    )

    return df


def show_candidate_table(df, title_extra='', priority_cols=None):
    """展示派单表（标准列顺序）+ 导出"""
    if df.empty:
        st.success("✅ 此类下当前无待派单服务商")
        return

    show = df.copy()
    show['最近拜访时间'] = pd.to_datetime(show.get('最近拜访时间'), errors='coerce')
    show['最近拜访时间'] = show['最近拜访时间'].dt.strftime('%Y-%m-%d').fillna('—')
    if 'M' in show.columns:
        show['M'] = show['M'].fillna(0).round(0).astype(int)
    show = show.fillna('—')

    preferred = priority_cols or [
        'RFM类别', '服务商名称', '客户编码',
        '地市', '上级分销商名称', '服务商等级_原始',
        '联系人', '联系电话_最佳',
        '客户所有者', '分销商业务员姓名',
        '最近拜访时间', '距今天数', '最近拜访方', '最近拜访人',
        'R', 'F', 'M',
    ]
    cols_show = [c for c in preferred if c in show.columns]

    # 🏷️ 加标签列（看上去就知道这个客户的状态：伞形 / 无意向 / 竞品Top 等）
    try:
        from _tag_widget import attach_tags_column
        show = attach_tags_column(show, code_col='客户编码')
        if '🏷️ 标签' in show.columns:
            # 把标签列插在客户编码之后
            if '客户编码' in cols_show:
                idx = cols_show.index('客户编码') + 1
                cols_show = cols_show[:idx] + ['🏷️ 标签'] + cols_show[idx:]
            else:
                cols_show.insert(0, '🏷️ 标签')
    except Exception:
        pass

    st.dataframe(
        show[cols_show].rename(columns={'联系电话_最佳': '联系电话'}),
        use_container_width=True,
        hide_index=True,
        height=min(60 + len(show) * 36, 400),
    )

    # 导出
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        show[cols_show].to_excel(w, index=False)
    stamp = datetime.now().strftime('%Y%m%d')
    name_safe = ''.join(c for c in title_extra if c.isalnum() or c in '_-')[:30]
    st.download_button(
        f"📥 导出 Excel（{len(show):,} 家）",
        data=buf.getvalue(),
        file_name=f"派单_{name_safe}_{stamp}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        key=f"dl_{title_extra}",
    )


# ──────────────────────────────────────────
# 模块 1：🚨 紧急派单（流失风险/沉睡 + 超期）
# ──────────────────────────────────────────

st.markdown("### 🚨 紧急派单（流失风险 + 超期未拜访）")
st.caption(
    f"逻辑：RFM=「⚠️ 流失风险」（曾是好客户但最近不来了）+ 距上次拜访 > {risk_th} 天 → 立即派单"
)

if not rfm_filtered.empty and not last_visit.empty:
    risk_cats = ['⚠️ 流失风险']
    risk_codes = set(rfm_filtered[rfm_filtered['RFM类别'].isin(risk_cats)]['客户编码'])
    # 跟最近拜访 join
    lv = last_visit.copy()
    lv['距今天数'] = (anchor_visit - lv['拜访时间']).dt.days
    risk_overdue = lv[
        lv['客户编码'].astype(str).isin(risk_codes)
        & ((lv['距今天数'] > risk_th) | lv['距今天数'].isna())
    ]
    # 也要包括"完全没被拜访"的流失风险客户
    visited_codes = set(lv['客户编码'].astype(str))
    never_visited = risk_codes - visited_codes
    target_codes = set(risk_overdue['客户编码'].astype(str)) | never_visited
    df1 = build_candidate_table(target_codes, rfm_filtered, master_subset, last_visit)
    if not df1.empty:
        df1 = df1.sort_values(['距今天数'], ascending=False, na_position='first')
    show_candidate_table(df1, title_extra='紧急派单')
else:
    st.info("RFM 或拜访数据不全，无法生成紧急派单清单")

st.divider()


# ──────────────────────────────────────────
# 模块 2：⚠️ 漏跑预警（冠军/潜力 + 超期）
# ──────────────────────────────────────────

st.markdown(f"### ⚠️ 核心客户漏跑（🏆 核心活跃 > {cham_th} 天未拜访）")
st.caption(
    "逻辑：RFM=「🏆 核心活跃」+ 距上次拜访 > "
    f"{cham_th} 天 → 命脉客户被忽略警报。"
    "⚠️ 培育客户不放这里 — 培育是长期工作，看「下方培育客户表」单独管理。"
)

if not rfm_filtered.empty and not last_visit.empty:
    lv = last_visit.copy()
    lv['距今天数'] = (anchor_visit - lv['拜访时间']).dt.days

    cham_codes = set(rfm_filtered[rfm_filtered['RFM类别'] == '🏆 核心活跃']['客户编码'])

    cham_late = set(lv[
        lv['客户编码'].astype(str).isin(cham_codes)
        & (lv['距今天数'] > cham_th)
    ]['客户编码'].astype(str))
    # 完全没拜访的核心活跃客户也算漏跑
    visited_codes = set(lv['客户编码'].astype(str))
    cham_never = cham_codes - visited_codes

    target = cham_late | cham_never
    df2 = build_candidate_table(target, rfm_filtered, master_subset, last_visit)
    if not df2.empty:
        df2 = df2.sort_values('M', ascending=False, na_position='last')
    show_candidate_table(df2, title_extra='核心客户漏跑')

st.divider()


# ──────────────────────────────────────────
# 模块 3：🆕 培育新人（新签未激活 + RFM=新人）
# ──────────────────────────────────────────

st.markdown("### 🌱 新客成长池（新签未激活 + 培育客户）")
st.caption(
    "**业务目标**：长期培育这部分客户从「🌱 培育」 → 「🏆 核心活跃」（关系维度，不看拜访时效）。\n\n"
    "纳入条件：签约表「是否新签=Y」+「累计上线 < ¥1,000」（未激活） 或 RFM=「🌱 培育客户」。"
    "**激活判定用 KPI 标准**（累计上线 ≥¥1k 即视为已激活），不依赖签约表「是否激活」字段（口径不准）。\n\n"
    "💡 区别于上方「核心客户漏跑」表：那张是时间维度（应该跑但忘了跑），这张是关系维度（持续培育才能成长）。"
)

if not master_subset.empty:
    # 改用 KPI 标准：累计上线金额 < ¥1k 视为未激活（即使签约表标了「是否激活=Y」）
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _loaders import load_provider_tier_shared as _load_tier  # noqa: E402
    tier_df = _load_tier()
    inactive_codes = set()
    if not tier_df.empty:
        # 上线累计 < ¥1k = 未激活（含 v0未激活 + 已激活但未到 v2）
        inactive_codes = set(
            tier_df[tier_df['上线累计'] < 1000]['客户编码'].astype(str).str.strip()
        )

    cond_newsigned = (
        master_subset.get('是否新签', pd.Series(dtype=object)).astype(str) == 'Y'
    )
    new_signed_codes = set(
        master_subset[cond_newsigned]['客户编码'].astype(str)
    ) if cond_newsigned.any() else set()

    # 新签 AND 未激活（按 KPI 标准）
    new_unactive_codes = new_signed_codes & inactive_codes

    grow_codes = set(rfm_filtered[rfm_filtered['RFM类别'] == '🌱 培育客户']['客户编码']) \
        if not rfm_filtered.empty else set()

    target = new_unactive_codes | grow_codes
    df3 = build_candidate_table(target, rfm_filtered, master_subset, last_visit)
    show_candidate_table(df3, title_extra='新客成长池')
else:
    st.info("沙盘+签约数据未导入，无法识别新签未激活客户")

st.divider()


# ──────────────────────────────────────────
# 模块 4：💸 空跑浪费（低价值客户被频繁拜访）
# ──────────────────────────────────────────

st.markdown(f"### 💸 资源错配 — 跑错对象（🪦 已流失客户被频繁拜访）")
st.caption(
    "**业务诉求**：调整**拜访对象** — 把跑「🪦 已流失」客户的资源转移到核心活跃 / 流失风险。\n\n"
    f"逻辑：客户已经在「🪦 已流失」类别 + 窗口内拜访 ≥ {mtx_high_threshold} 次 → "
    "**资源错配**，业务员的时间投到了低产出的客户上。"
)

if not rfm_filtered.empty and not visit_count_window.empty:
    waste_cats = ['🪦 已流失']
    waste_codes = set(rfm_filtered[rfm_filtered['RFM类别'].isin(waste_cats)]['客户编码'])
    high_freq_codes = set(visit_count_window[
        visit_count_window['拜访次数'] >= mtx_high_threshold
    ]['客户编码'].astype(str))
    target = waste_codes & high_freq_codes
    if target:
        df_waste = build_candidate_table(target, rfm_filtered, master_subset, last_visit)
        df_waste = df_waste.merge(
            visit_count_window[['客户编码', '拜访次数', '拜访人']]
            .assign(客户编码=lambda d: d['客户编码'].astype(str)),
            on='客户编码', how='left',
        )
        df_waste = df_waste.sort_values('拜访次数', ascending=False, na_position='last')
        show_candidate_table(
            df_waste, title_extra='资源错配',
            priority_cols=[
                'RFM类别', '服务商名称', '客户编码',
                '拜访次数', '拜访人',
                'M', '上级分销商名称', '客户所有者',
            ],
        )
    else:
        st.success("✅ 当前阈值下无跑错对象情况")
else:
    st.info("RFM 或拜访数据不全，无法识别资源错配")

st.divider()


# ──────────────────────────────────────────
# 模块 5：🔁 重复拜访浪费（1 月内 ≥ 3 次）
# ──────────────────────────────────────────

st.markdown("### 🔁 人员重复 — 多业务员撞客户（最近 30 天 ≥ 3 次 + ≥ 2 个不同业务员）")
st.caption(
    "**业务诉求**：调整**人员分工** — 同一客户被多个业务员重复拜访，应明确负责人。\n\n"
    "逻辑：30 天内同一服务商被**多个不同业务员**拜访 ≥ 3 次 → 协作重复。"
    "**仅算多业务员重复**（同一业务员高频是正常跟进，不算重复）。\n\n"
    "💡 区别于上方「资源错配」表：那张是「跑错对象」（业务员投错时间），这张是「人员撞车」（多个业务员投到同一对象上）。"
)

vc30 = compute_visit_count_by_client(days=30)
if not vc30.empty:
    # 必须 ≥3 次 AND 至少 2 个不同业务员
    repeat = vc30[(vc30['拜访次数'] >= 3) & (vc30['拜访人数'] >= 2)].copy()
    if (picked_provinces or picked_cities or picked_districts) and not visits_filtered.empty:
        codes_in_geo = set(visits_filtered['客户编码'].astype(str).str.strip())
        repeat = repeat[repeat['客户编码'].astype(str).isin(codes_in_geo)]

    if not repeat.empty:
        repeat = repeat.sort_values('拜访次数', ascending=False)
        # 拼接客户名 + RFM
        df4 = build_candidate_table(
            set(repeat['客户编码'].astype(str)),
            rfm_filtered, master_subset, last_visit,
        )
        df4 = df4.merge(
            repeat[['客户编码', '拜访次数', '拜访人']]
            .assign(客户编码=lambda d: d['客户编码'].astype(str)),
            on='客户编码', how='left',
        )
        df4 = df4.sort_values('拜访次数', ascending=False, na_position='last')
        show_candidate_table(
            df4,
            title_extra='人员重复',
            priority_cols=[
                '服务商名称', 'RFM类别', '客户编码',
                '拜访次数', '拜访人',
                '上级分销商名称', '客户所有者',
            ],
        )
    else:
        st.success("✅ 最近 30 天没有发现多业务员撞客户的情况")

st.divider()


# ──────────────────────────────────────────
# 模块 5：🎭 异常打卡名单
# ──────────────────────────────────────────

st.markdown(f"### 🎭 异常打卡名单（距离 > {dist_th} 米 + 拜访目的 ≤ {purpose_min_chars} 字）")
st.caption(
    "逻辑：距离偏离过大 + 拜访目的填写敷衍 → 数据真实性需核查。"
    "**已排除**「系统客户地址信息维护错误」「客户多地址办公」等已标注的合理异常 —— "
    "这些是地址登记错误，不是业务员造假。"
)

if not visits_filtered.empty:
    abn = visits_filtered.copy()
    abn['距离偏离_米'] = pd.to_numeric(abn['距离偏离_米'], errors='coerce')
    abn['拜访目的字数'] = abn['拜访目的'].fillna('').astype(str).str.len()

    # 排除"系统客户地址信息维护错误"等数据问题导致的距离异常
    invalid_anomaly_set = {
        '系统客户地址信息维护错误，后续更正',
        '系统客户地址信息维护错误,后续更正',
        '客户多地址办公',
        '系统客户地址信息维护错误',
    }
    is_invalid_anomaly = abn.get(
        '打卡异常类型', pd.Series('', index=abn.index)
    ).fillna('').astype(str).isin(invalid_anomaly_set)

    suspicious = abn[
        (abn['距离偏离_米'] > dist_th)
        & (abn['拜访目的字数'] <= purpose_min_chars)
        & (~is_invalid_anomaly)
    ].copy()

    if not suspicious.empty:
        suspicious = suspicious.sort_values('距离偏离_米', ascending=False)
        cols_abn = [
            '拜访时间', '拜访客户', '客户编码',
            '打卡人姓名', '打卡人所属公司', '_打卡方',
            '距离偏离_米', '拜访目的字数',
            '拜访目的', '达成结果',
            '打卡异常类型', '打卡异常描述',
        ]
        cols_abn = [c for c in cols_abn if c in suspicious.columns]
        show = suspicious[cols_abn].copy()
        show['拜访时间'] = pd.to_datetime(show['拜访时间']).dt.strftime('%Y-%m-%d %H:%M')
        show['距离偏离_米'] = show['距离偏离_米'].round(0).astype(int)
        st.dataframe(
            show.head(500),
            use_container_width=True,
            hide_index=True,
            height=400,
        )
        if len(show) > 500:
            st.caption(f"⚙️ 仅显示前 500 条，共 {len(show):,} 条。请通过导出查看全量。")

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as w:
            show.to_excel(w, sheet_name='异常打卡', index=False)
        stamp = datetime.now().strftime('%Y%m%d')
        st.download_button(
            f"📥 导出全量 Excel（{len(show):,} 条）",
            data=buf.getvalue(),
            file_name=f"派单_异常打卡_{stamp}.xlsx",
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            key="dl_abnormal",
        )
    else:
        st.success("✅ 当前阈值下无可疑打卡记录")

st.divider()
st.caption(
    "💡 派单建议优先级：🚨 紧急派单 > ⚠️ 核心客户漏跑 > 🌱 新客成长池 > 💸 资源错配 > 🔁 人员重复。"
    "建议每天打开此页面，把名单分给责任 BD / 区域经理跟进。"
)


# ══════════════════════════════════════════════
# 🤖 AI 智能诊断 — 调 Claude Code CLI 跑 skill: provider-classification-diagnosis
# ══════════════════════════════════════════════
st.divider()

# 构造给 AI 的上下文：当前筛选范围 + 5 类分布 + 5 张派单表的关键指标
def _build_diagnosis_context() -> dict:
    """组装当前页面的诊断上下文（地理筛选 / 时间窗 / 阈值 / 5 类分布 / 5 张表）"""
    ctx = {
        'filters': {
            '省份': list(picked_provinces) if picked_provinces else None,
            '城市': list(picked_cities) if picked_cities else None,
            '区县': list(picked_districts) if picked_districts else None,
            'RFM 时间窗_月': int(window_months),
            '矩阵拜访窗口_天': int(mtx_window_days),
            '矩阵高频阈值_次': int(mtx_high_threshold),
            '流失风险超期阈值_天': int(risk_th),
            '核心活跃漏跑阈值_天': int(cham_th),
        },
        'rfm_thresholds': {
            'R 高/低分界_天': 60,
            'F 高/低分界_次': 6,
            'M 高/低分界_元': 10000,
        },
        'distribution': {},
        'dispatch_tables': {},
    }

    # 5 类分布（基于 scope/筛选后的 rfm_filtered）
    if 'rfm_filtered' in dir() or 'rfm_filtered' in globals():
        try:
            dist = rfm_filtered['RFM类别'].value_counts()
            ctx['distribution'] = {
                '🏆 核心活跃': int(dist.get('🏆 核心活跃', 0)),
                '⚠️ 流失风险': int(dist.get('⚠️ 流失风险', 0)),
                '🌱 培育客户': int(dist.get('🌱 培育客户', 0)),
                '🪦 已流失': int(dist.get('🪦 已流失', 0)),
                '❓ 未采购客户(矩阵中)': int(mtx_df_full[mtx_df_full['RFM类别'] == UNKNOWN_CAT].shape[0])
                                          if 'mtx_df_full' in globals() and not mtx_df_full.empty else 0,
            }
        except Exception:
            pass

    # 5 张派单表概要（行数）— 用 try 包，避免变量未定义时报错
    # 紧急派单
    try:
        ctx['dispatch_tables']['🚨 紧急派单'] = {
            '条件': f'RFM=⚠️ 流失风险 + 距上次拜访 > {risk_th} 天',
            '数量': int(len(df1)) if 'df1' in globals() and isinstance(df1, pd.DataFrame) else 0,
        }
    except Exception:
        pass
    # 核心活跃漏跑
    try:
        ctx['dispatch_tables']['⚠️ 核心客户漏跑'] = {
            '条件': f'RFM=🏆 核心活跃 + 距上次拜访 > {cham_th} 天',
            '数量': int(len(df2)) if 'df2' in globals() and isinstance(df2, pd.DataFrame) else 0,
        }
    except Exception:
        pass
    # 新客成长池
    try:
        ctx['dispatch_tables']['🌱 新客成长池'] = {
            '条件': '新签 + 累计上线 <¥1k(未激活) 或 RFM=🌱 培育客户',
            '数量': int(len(df3)) if 'df3' in globals() and isinstance(df3, pd.DataFrame) else 0,
        }
    except Exception:
        pass
    # 资源错配
    try:
        ctx['dispatch_tables']['💸 资源错配'] = {
            '条件': f'RFM=🪦 已流失 + 窗口拜访 ≥ {mtx_high_threshold} 次',
            '数量': int(len(df_waste)) if 'df_waste' in globals() and isinstance(df_waste, pd.DataFrame) else 0,
        }
    except Exception:
        pass
    # 人员重复
    try:
        ctx['dispatch_tables']['🔁 人员重复'] = {
            '条件': '30 天内同一客户被 ≥3 次拜访 且 ≥2 个不同业务员',
            '数量': int(len(df4)) if 'df4' in globals() and isinstance(df4, pd.DataFrame) else 0,
        }
    except Exception:
        pass

    # 矩阵四象限统计
    try:
        ctx['matrix_quadrant'] = {
            '🟢 健康忠诚(高价值高频)': int(n_healthy) if 'n_healthy' in globals() else 0,
            '🟡 漏跑警报(高价值低频)': int(n_missed) if 'n_missed' in globals() else 0,
            '🔴 空跑浪费(低价值高频)': int(n_wasted) if 'n_wasted' in globals() else 0,
            '⚪ 合理(低价值低频)': int(n_reasonable) if 'n_reasonable' in globals() else 0,
            '❓ 未采购': int(n_unknown) if 'n_unknown' in globals() else 0,
        }
    except Exception:
        pass

    return ctx


import json as _json  # noqa: E402

di_cols = st.columns([4, 2, 4])
with di_cols[1]:
    diag_btn = st.button(
        '🤖 AI 智能诊断',
        type='primary', use_container_width=True,
        help='把当前数据范围的所有派单表 + 类别分布交给 AI，让 Claude Code CLI '
             '基于 skill: provider-classification-diagnosis 生成一份业务诊断报告',
        key='_p02_ai_diag_btn',
    )

st.caption(
    '🧠 AI 基于当前筛选范围回答 **2 个核心问题** —— '
    '① 哪些重要客户没跑动？② 哪些跑动是没效果的？'
    '产出 **HTML 单文件诊断报告**（图文混排、可双击打开）+ 200 字精简结论。'
)

if diag_btn:
    from _ai_handoff import HANDOFF_KEY
    diag_ctx = _build_diagnosis_context()

    # 拼一个 context_summary（给 build_prompt_prefix 用）
    ctx_summary = (
        '## 当前页面筛选\n'
        f'```json\n{_json.dumps(diag_ctx["filters"], ensure_ascii=False, indent=2)}\n```\n\n'
        '## RFM 阈值\n'
        f'```json\n{_json.dumps(diag_ctx["rfm_thresholds"], ensure_ascii=False, indent=2)}\n```\n\n'
        '## RFM 5 类分布（筛选后）\n'
        f'```json\n{_json.dumps(diag_ctx["distribution"], ensure_ascii=False, indent=2)}\n```\n\n'
        '## 5 张派单表数量\n'
        f'```json\n{_json.dumps(diag_ctx["dispatch_tables"], ensure_ascii=False, indent=2)}\n```\n\n'
        '## RFM × 拜访 矩阵四象限\n'
        f'```json\n{_json.dumps(diag_ctx.get("matrix_quadrant", {}), ensure_ascii=False, indent=2)}\n```'
    )

    # 这条问题里明确要 AI 读 SKILL
    diag_question = (
        '基于当前页面的数据范围（见上方上下文），做一份**服务商分类诊断**。\n\n'
        '⚠️ 必读：\n'
        '1. `/sandbox/skills/system-overview/SKILL.md` — 平台数据结构 + 双口径定义\n'
        '2. `/sandbox/skills/provider-classification-diagnosis/SKILL.md` — 诊断方法与产出格式\n\n'
        '业务员只关心两件事：\n'
        '1. **哪些重要客户没跑动？** — 漏跑名单（流失风险 + 核心活跃）+ 应负责业务员\n'
        '2. **哪些跑动是没有效果的？** — 资源错配（已流失被频繁拜访）+ 人员撞车（多业务员同跑）\n\n'
        '**产出**：\n'
        '1. HTML 单文件 → `/sandbox/output/服务商分类诊断_YYYYMMDD_HHMMSS.html`\n'
        '   - 1 句核心结论（具体数字 + 关键业务员/客户）\n'
        '   - 1 张漏跑 TOP 20 表 + 2 张无效跑动 TOP 10 表（图文混排）\n'
        '   - 3 条行动建议（必须落到 **业务员姓名 + 具体客户名 + 时限**）\n'
        '2. 最后 assistant 文本贴 200 字简述（格式见 SKILL §五.4）'
    )

    # 把 ctx 塞进 handoff key（page 09 自动拼到 prompt 前）
    st.session_state[HANDOFF_KEY] = {
        'source_page': '02·🧭 服务商智能分类',
        'context_summary': ctx_summary,
        'data_snapshot': None,
        'suggested_followups': [
            '把诊断结果按业务员维度拆细',
            '把诊断结果按区县维度拆细',
            '给紧急派单表里 V3/V4 客户排一份具体的本周跑动计划',
        ],
        'created_at': datetime.now().isoformat(timespec='seconds'),
    }
    # 把诊断问题作为预填的 question 传给 page 09
    st.session_state['ca_question'] = diag_question
    # 清掉旧 session（开一个新 thread）
    st.session_state.pop('ca_question_input', None)
    st.session_state.pop('ca_claude_session_id', None)
    st.session_state['ca_history'] = []
    try:
        st.switch_page('pages/09·🛠️ AI 代码助手.py')
    except Exception:
        st.success('上下文已保存。请左侧栏切到「09·🛠️ AI 代码助手」继续。')
