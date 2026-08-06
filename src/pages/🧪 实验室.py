#!/usr/bin/env python3
"""
🧪 实验室 — 新奇但实用的小工具
- 不属于核心分析模块的功能在这里试水
- 目前 2 个：批量序列号查询 / 数据排行榜
  （代理商擂台 PK 已迁移到独立 page「⚔️ 代理商大PK」）
- 后续按需扩展
"""

import sys
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# 引入共享加载模块（跨 page 共享缓存）
sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import (  # noqa: E402
    load_main_shared, load_redpack_shared, load_visit_shared,
    load_contract_shared, DB_PATH,
)

require_auth()
st.markdown("### 🧪 实验室 — 新奇功能试水")
st.caption("这里放一些不属于主分析模块、但可能有用的小工具。觉得好用，可以挪到正式 page。")


# 兼容老调用方：保留同名 wrapper（cols 参数被忽略，共享加载）
# 不再 .copy() —— streamlit cache_data 已自动隔离，再 copy 200MB 浪费
def load_main(cols=None) -> pd.DataFrame:
    return load_main_shared()


def load_redpack(cols=None) -> pd.DataFrame:
    return load_redpack_shared()


def db_ready() -> bool:
    if not DB_PATH.exists():
        st.warning("📦 数据库尚未建立，请先到主页『📥 数据导入』上传 Excel。")
        return False
    return True


# ──────────────────────────────────────────
# 红包刺激分析专用 cache 函数
# ──────────────────────────────────────────

@st.cache_data(ttl=600)
def _stim_event_study(_big_events: pd.DataFrame, _all_records: pd.DataFrame, n_days: int):
    """对每个服务商，找首次大红包事件 t0，统计前后 N 天的上线台数。
    参数名 _ 前缀 → 跳过 DataFrame hash"""
    if _big_events.empty:
        return pd.DataFrame()

    first_big = _big_events.sort_values('_事件时间').drop_duplicates(
        subset='上线客户编码', keep='first'
    )[['上线客户编码', '上线客户名称', '_事件时间', '中奖金额']].rename(
        columns={'_事件时间': 't0', '中奖金额': '大红包金额'}
    )

    rows = []
    grouped = _all_records.groupby('上线客户编码')
    for code, sub in grouped:
        sub_first = first_big[first_big['上线客户编码'] == code]
        if sub_first.empty:
            continue
        t0 = sub_first['t0'].iloc[0]
        amt_big = sub_first['大红包金额'].iloc[0]
        name = sub_first['上线客户名称'].iloc[0]
        pre_start = t0 - pd.Timedelta(days=n_days)
        post_end = t0 + pd.Timedelta(days=n_days)
        pre_mask = (sub['_事件时间'] >= pre_start) & (sub['_事件时间'] < t0)
        post_mask = (sub['_事件时间'] > t0) & (sub['_事件时间'] <= post_end)
        n_pre = int(pre_mask.sum())
        n_post = int(post_mask.sum())
        amt_pre = float(sub.loc[pre_mask, '产品现有分销价'].sum())
        amt_post = float(sub.loc[post_mask, '产品现有分销价'].sum())
        rows.append({
            '客户编码': code, '服务商': name, 't0': t0, '大红包金额': amt_big,
            '前N天台数': n_pre, '后N天台数': n_post,
            '提振台数': n_post - n_pre,
            '提振率': (n_post - n_pre) / max(n_pre, 1),
            '前N天金额': amt_pre, '后N天金额': amt_post,
            '金额提振率': (amt_post - amt_pre) / max(amt_pre, 1),
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=600)
def _stim_amount_curve(_all_won: pd.DataFrame, _all_records: pd.DataFrame, n_days: int):
    """对所有中奖事件做事件研究，按金额分桶。"""
    if _all_won.empty:
        return pd.DataFrame()
    first_win = _all_won.sort_values('_事件时间').drop_duplicates(
        subset='上线客户编码', keep='first'
    )[['上线客户编码', '上线客户名称', '_事件时间', '中奖金额']].rename(
        columns={'_事件时间': 't0', '中奖金额': '金额'}
    )
    rows = []
    grouped = _all_records.groupby('上线客户编码')
    for code, sub in grouped:
        sub_first = first_win[first_win['上线客户编码'] == code]
        if sub_first.empty:
            continue
        t0 = sub_first['t0'].iloc[0]
        amt = sub_first['金额'].iloc[0]
        pre_mask = (sub['_事件时间'] >= t0 - pd.Timedelta(days=n_days)) & (sub['_事件时间'] < t0)
        post_mask = (sub['_事件时间'] > t0) & (sub['_事件时间'] <= t0 + pd.Timedelta(days=n_days))
        rows.append({
            '客户编码': code, '金额': amt,
            'pre': int(pre_mask.sum()), 'post': int(post_mask.sum()),
            '提振台数': int(post_mask.sum()) - int(pre_mask.sum()),
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=600)
def _stim_did(_big_events: pd.DataFrame, _all_records: pd.DataFrame, n_days: int):
    """简化版 DID：处理组 vs 对照组的窗口期上线对比。"""
    if _big_events.empty:
        return None
    first_big = _big_events.sort_values('_事件时间').drop_duplicates(
        subset='上线客户编码', keep='first'
    )[['上线客户编码', '_事件时间']]
    t0_map = dict(zip(first_big['上线客户编码'], first_big['_事件时间']))

    treated_pre, treated_post = [], []
    for code, t0 in t0_map.items():
        sub = _all_records[_all_records['上线客户编码'] == code]
        treated_pre.append(int(((sub['_事件时间'] >= t0 - pd.Timedelta(days=n_days)) & (sub['_事件时间'] < t0)).sum()))
        treated_post.append(int(((sub['_事件时间'] > t0) & (sub['_事件时间'] <= t0 + pd.Timedelta(days=n_days))).sum()))

    won_codes = set(_all_records.loc[_all_records['中奖金额'] > 0, '上线客户编码'])
    control_codes = set(_all_records['上线客户编码']) - won_codes
    if not control_codes:
        return None

    pseudo_t0 = pd.Timestamp(
        np.array(list(t0_map.values()), dtype='datetime64[ns]').astype('int64').mean(),
        unit='ns',
    )
    control_records = _all_records[_all_records['上线客户编码'].isin(control_codes)]
    pre_mask = (control_records['_事件时间'] >= pseudo_t0 - pd.Timedelta(days=n_days)) & (control_records['_事件时间'] < pseudo_t0)
    post_mask = (control_records['_事件时间'] > pseudo_t0) & (control_records['_事件时间'] <= pseudo_t0 + pd.Timedelta(days=n_days))
    ctrl_pre = control_records[pre_mask].groupby('上线客户编码').size()
    ctrl_post = control_records[post_mask].groupby('上线客户编码').size()
    active_ctrl = set(ctrl_pre.index) | set(ctrl_post.index)
    if not active_ctrl:
        return None

    return {
        '处理组N': len(treated_pre),
        '对照组N': len(active_ctrl),
        '处理_前': float(np.mean(treated_pre)),
        '处理_后': float(np.mean(treated_post)),
        '处理_增量': float(np.mean(treated_post)) - float(np.mean(treated_pre)),
        '对照_前': float(ctrl_pre.reindex(list(active_ctrl), fill_value=0).mean()),
        '对照_后': float(ctrl_post.reindex(list(active_ctrl), fill_value=0).mean()),
        '对照_增量': float(ctrl_post.reindex(list(active_ctrl), fill_value=0).mean()) - float(ctrl_pre.reindex(list(active_ctrl), fill_value=0).mean()),
        'DID': (float(np.mean(treated_post)) - float(np.mean(treated_pre)))
               - (float(ctrl_post.reindex(list(active_ctrl), fill_value=0).mean())
                  - float(ctrl_pre.reindex(list(active_ctrl), fill_value=0).mean())),
        'pseudo_t0': pseudo_t0,
    }


# ──────────────────────────────────────────
# 关联服务商挖掘（团伙识别）— cache 函数
# ──────────────────────────────────────────






# ──────────────────────────────────────────
# 实验入口
# ──────────────────────────────────────────

if not db_ready():
    st.stop()

tab1, tab2, tab4, tab_stim = st.tabs([
    "🔍 批量序列号查询",
    "🏆 数据排行榜",
    "📦 产品组合关联分析",
    "💉 红包刺激分析",
])


# ════════════════════ 实验 1：批量序列号查询 ════════════════════
with tab1:
    st.markdown("#### 🔍 批量序列号查询")
    st.caption("粘贴一堆产品序列号（一行一个），一键查询每台设备的去向、服务商、代理商等关键信息。")

    sample = "示例：\nMAC-XXX-001\nMAC-XXX-002\n..."
    raw = st.text_area(
        "产品序列号列表（每行一个）",
        height=180,
        placeholder=sample,
        key="serial_input",
    )

    if st.button("🔎 查询", type="primary", key="serial_btn"):
        # 解析输入
        serials = [s.strip() for s in raw.replace(',', '\n').replace('，', '\n').splitlines() if s.strip()]
        if not serials:
            st.warning("请先在上方粘贴序列号")
        else:
            st.caption(f"输入 {len(serials)} 个序列号（已去重）")
            serials = list(dict.fromkeys(serials))  # 保序去重

            with st.spinner("查询中…"):
                main = load_main([
                    'ID', '产品序列号', '上线时间', '出库客户名称',
                    '上线城市', '上线区县', '内部型号', '产品系列',
                    '上线自客户名称', '所属一级客户', '最新分销价',
                ])
                rp = load_redpack([
                    '产品序列号', '上线时间', '上线客户编码', '上线客户名称',
                    '所属一级客户', '出货客户名称', '安装城市', '安装区县',
                    '产品现有分销价',
                ])

                # 主表查询
                main_hit = main[main['产品序列号'].astype(str).isin(serials)] if not main.empty else pd.DataFrame()
                rp_hit = rp[rp['产品序列号'].astype(str).isin(serials)] if not rp.empty else pd.DataFrame()

                # 用主表为底，左 join 红包表（红包补充服务商真名）
                rows = []
                for s in serials:
                    m_row = main_hit[main_hit['产品序列号'].astype(str) == s]
                    r_row = rp_hit[rp_hit['产品序列号'].astype(str) == s]
                    rec = {'产品序列号': s}

                    if not m_row.empty:
                        m = m_row.iloc[0]
                        rec.update({
                            '上线时间': m.get('上线时间'),
                            '上线城市': m.get('上线城市'),
                            '上线区县': m.get('上线区县'),
                            '出库客户': m.get('出库客户名称'),
                            '内部型号': m.get('内部型号'),
                            '产品系列': m.get('产品系列'),
                            '主表服务商': m.get('上线自客户名称'),
                            '最新分销价': m.get('最新分销价'),
                        })
                    if not r_row.empty:
                        r = r_row.iloc[0]
                        rec.update({
                            '红包服务商': r.get('上线客户名称'),
                            '签约代理商': r.get('所属一级客户'),
                            '红包出货客户': r.get('出货客户名称'),
                            '产品现有分销价': r.get('产品现有分销价'),
                        })
                        # 是否跨渠道
                        a = str(r.get('出货客户名称') or '').strip()
                        b = str(r.get('所属一级客户') or '').strip()
                        if a and b:
                            rec['是否跨渠道采购'] = '⚠️ 是' if a != b else '✅ 否'

                    if len(rec) == 1:
                        rec['_状态'] = '❌ 两表都未找到'
                    else:
                        rec['_状态'] = (
                            '✅ 主+红包都有' if (not m_row.empty and not r_row.empty)
                            else ('⚠️ 仅主表' if not m_row.empty else '⚠️ 仅红包表')
                        )
                    rows.append(rec)

                result = pd.DataFrame(rows)

                hit_main = (result.get('上线时间', pd.Series([])).notna().sum()
                            if '上线时间' in result.columns else 0)
                hit_rp = (result.get('红包服务商', pd.Series([])).notna().sum()
                          if '红包服务商' in result.columns else 0)
                miss = (result['_状态'] == '❌ 两表都未找到').sum()

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("输入条数", f"{len(serials)}")
                c2.metric("主表命中", f"{hit_main}")
                c3.metric("红包命中", f"{hit_rp}")
                c4.metric("两表都没找到", f"{miss}")

                # 摆好列顺序
                preferred = [
                    '_状态', '产品序列号', '上线时间', '上线城市', '上线区县',
                    '出库客户', '签约代理商', '红包出货客户', '是否跨渠道采购',
                    '主表服务商', '红包服务商',
                    '内部型号', '产品系列',
                    '最新分销价', '产品现有分销价',
                ]
                cols_show = [c for c in preferred if c in result.columns]
                st.dataframe(
                    result[cols_show].rename(columns={'_状态': '状态'}),
                    use_container_width=True,
                    hide_index=True,
                    height=400,
                )


# ════════════════════ 实验 2：数据排行榜 ════════════════════
with tab2:
    st.markdown("#### 🏆 数据排行榜（年度之最）")

    main_full = load_main([
        'ID', '产品序列号', '上线时间', '上线城市', '上线区县',
        '出库客户名称', '上线自客户名称', '所属一级客户',
        '最新分销价', '产品系列',
        '是否异省', '是否异城', '是否异县',
    ])
    rp_full = load_redpack()

    if main_full.empty:
        st.warning("主表暂无数据，请先到主页上传。")
    else:
        years = sorted([y for y in main_full['上线年份'].dropna().unique() if 2024 <= y <= 2030])
        if not years:
            st.warning("数据里没有有效年份")
        else:
            year_pick = st.selectbox(
                "看哪一年的排行榜",
                options=years,
                index=len(years) - 1,
                key="rank_year",
            )
            df = main_full[main_full['上线年份'] == year_pick]
            df_prev = main_full[main_full['上线年份'] == year_pick - 1] if (year_pick - 1) in years else pd.DataFrame()

            # 计算
            ranks = []

            # 1. SO 台数王（区县）
            if not df.empty:
                qty_top = df.groupby('上线区县').size().sort_values(ascending=False)
                if len(qty_top) > 0:
                    ranks.append({
                        '🏅': '🥇 SO 台数冠军区县',
                        '名称': qty_top.index[0],
                        '指标': f"{qty_top.iloc[0]:,} 台",
                        '说明': f"超过第 2 名 {qty_top.index[1]} {qty_top.iloc[0] - qty_top.iloc[1]:,} 台" if len(qty_top) > 1 else "—",
                    })

            # 2. SO 金额王（区县）
            if not df.empty:
                amt_top = df.groupby('上线区县')['最新分销价'].sum().sort_values(ascending=False)
                if len(amt_top) > 0:
                    ranks.append({
                        '🏅': '💰 SO 金额冠军区县',
                        '名称': amt_top.index[0],
                        '指标': f"¥{amt_top.iloc[0]:,.0f}",
                        '说明': f"占全省 {amt_top.iloc[0] / amt_top.sum() * 100:.1f}%",
                    })

            # 3. 黑马区县（同比增长 Top 1）
            if not df_prev.empty:
                cur_q = df.groupby('上线区县').size()
                prev_q = df_prev.groupby('上线区县').size()
                merged = pd.concat([cur_q, prev_q], axis=1, keys=['cur', 'prev']).fillna(0)
                merged = merged[merged['prev'] >= 50]  # 排除小基数
                if not merged.empty:
                    merged['rate'] = (merged['cur'] - merged['prev']) / merged['prev']
                    horse = merged.sort_values('rate', ascending=False).head(1)
                    if not horse.empty:
                        ranks.append({
                            '🏅': '🚀 黑马区县（同比增长率 Top 1）',
                            '名称': horse.index[0],
                            '指标': f"+{horse['rate'].iloc[0] * 100:.1f}%",
                            '说明': f"{int(horse['prev'].iloc[0])} → {int(horse['cur'].iloc[0])} 台",
                        })

            # 4. 跌幅王（同比下跌 Top 1）
            if not df_prev.empty:
                cur_q = df.groupby('上线区县').size()
                prev_q = df_prev.groupby('上线区县').size()
                merged = pd.concat([cur_q, prev_q], axis=1, keys=['cur', 'prev']).fillna(0)
                merged = merged[merged['prev'] >= 50]
                if not merged.empty:
                    merged['rate'] = (merged['cur'] - merged['prev']) / merged['prev']
                    fall = merged.sort_values('rate').head(1)
                    if not fall.empty:
                        ranks.append({
                            '🏅': '💔 跌幅最大区县',
                            '名称': fall.index[0],
                            '指标': f"{fall['rate'].iloc[0] * 100:.1f}%",
                            '说明': f"{int(fall['prev'].iloc[0])} → {int(fall['cur'].iloc[0])} 台",
                        })

            # 5. 出货代理商之王
            if not df.empty:
                d_top = df.groupby('出库客户名称').size().sort_values(ascending=False)
                if len(d_top) > 0:
                    ranks.append({
                        '🏅': '🏛️ 出货代理商台数冠军',
                        '名称': d_top.index[0],
                        '指标': f"{d_top.iloc[0]:,} 台",
                        '说明': f"占全省 {d_top.iloc[0] / d_top.sum() * 100:.1f}%",
                    })

            # 6. 跨渠道嫌疑代理商（出货量大 + 多个服务商签约的不是它）
            if not rp_full.empty:
                rp_y = rp_full[rp_full['上线年份'] == year_pick]
                if not rp_y.empty:
                    cross = rp_y[rp_y['出货客户名称'].astype(str).str.strip()
                                  != rp_y['所属一级客户'].astype(str).str.strip()]
                    if not cross.empty:
                        cross_top = cross.groupby('出货客户名称').size().sort_values(ascending=False)
                        if len(cross_top) > 0:
                            ranks.append({
                                '🏅': '🌪️ 跨渠道接货量冠军（红包表）',
                                '名称': cross_top.index[0],
                                '指标': f"{cross_top.iloc[0]:,} 台",
                                '说明': "其他代理商的服务商最多从这里进货",
                            })

            # 7. 全年 SO 第一服务商（红包表）
            if not rp_full.empty:
                rp_y = rp_full[rp_full['上线年份'] == year_pick]
                if not rp_y.empty and '上线客户名称' in rp_y.columns:
                    s_top = rp_y.groupby('上线客户名称').size().sort_values(ascending=False)
                    if len(s_top) > 0:
                        ranks.append({
                            '🏅': '👑 服务商上线台数冠军',
                            '名称': s_top.index[0],
                            '指标': f"{s_top.iloc[0]:,} 台",
                            '说明': "红包表口径，按上线客户名称聚合",
                        })

            # 8. 异地销售之王（异省+异城+异县均为 Y 的台数）
            if not df.empty and {'是否异省', '是否异城', '是否异县'}.issubset(df.columns):
                far = df[(df['是否异省'] == 'Y') & (df['是否异城'] == 'Y') & (df['是否异县'] == 'Y')]
                if not far.empty:
                    far_top = far.groupby('出库客户名称').size().sort_values(ascending=False)
                    if len(far_top) > 0:
                        ranks.append({
                            '🏅': '🌐 异地销售之王',
                            '名称': far_top.index[0],
                            '指标': f"{far_top.iloc[0]:,} 台",
                            '说明': "异省+异城+异县全 Y 的台数最多（设备走得最远）",
                        })

            if not ranks:
                st.info("当前数据范围内还没有可计算的排行榜")
            else:
                rank_df = pd.DataFrame(ranks)
                st.dataframe(
                    rank_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        '🏅': st.column_config.TextColumn("奖项", width="medium"),
                        '名称': st.column_config.TextColumn("得主", width="medium"),
                        '指标': st.column_config.TextColumn("指标", width="small"),
                        '说明': st.column_config.TextColumn("备注", width="medium"),
                    },
                    height=min(60 + len(rank_df) * 36, 600),
                )


# ════════════════════ 实验 4：产品组合关联分析 ════════════════════
with tab4:
    st.markdown("#### 📦 产品组合关联分析（购物篮分析）")
    st.caption(
        "篮子定义：同一服务商在 ≤ 3 天内上线的产品集合（一个项目）· "
        "粒度：产品系列 · 用 Apriori 找频繁项集 + 强关联规则 → 反向定位销售机会"
    )

    rp_ar = load_redpack()
    if rp_ar.empty:
        st.warning("📦 安装红包记录表暂无数据")
    else:
        # ── 参数 ──────────────────────────
        p_cols = st.columns([1, 1, 1, 2])
        with p_cols[0]:
            min_support = st.number_input(
                "最低支持度 (%)", min_value=0.1, max_value=20.0,
                value=1.0, step=0.5, key="ar_supp",
                help="组合在所有篮子里出现的最低占比",
            ) / 100
        with p_cols[1]:
            min_confidence = st.number_input(
                "最低置信度 (%)", min_value=10, max_value=100,
                value=50, step=5, key="ar_conf",
                help="买了 A 的人也买 B 的最低概率",
            ) / 100
        with p_cols[2]:
            min_lift = st.number_input(
                "最低提升度", min_value=1.0, max_value=10.0,
                value=2.0, step=0.5, key="ar_lift",
                help=">1 = 正相关；>2 = 强相关；<1 = 替代品",
            )
        with p_cols[3]:
            ar_city_options = sorted(
                rp_ar['安装城市'].dropna().astype(str).str.strip().unique().tolist()
            )
            ar_city_options = [c for c in ar_city_options if c]
            ar_cities = st.multiselect(
                "城市筛选",
                options=ar_city_options,
                default=[],
                key="ar_cities",
            )

        # 数据筛选
        rp_data = rp_ar.copy()
        if ar_cities:
            rp_data = rp_data[
                rp_data['安装城市'].astype(str).str.strip().isin(ar_cities)
            ]

        # 必备字段
        rp_data = rp_data.dropna(subset=['上线客户编码', '上线时间', '产品系列'])

        if rp_data.empty:
            st.warning("当前筛选下无数据")
        else:
            # ── 构造篮子 ────────────────────
            with st.spinner("构造项目篮子（同服务商 ≤ 3 天）…"):
                rp_data = rp_data.sort_values(['上线客户编码', '上线时间']).reset_index(drop=True)
                # 同服务商内，相邻记录时间差 > 3 天 = 新项目
                gap = rp_data['上线时间'].diff().dt.days
                same_provider = rp_data['上线客户编码'] == rp_data['上线客户编码'].shift(1)
                new_project = (~same_provider) | (gap > 3) | (gap.isna())
                rp_data['_proj_id'] = new_project.cumsum()

                # 按项目聚合产品系列（去重）
                baskets = (rp_data.groupby('_proj_id')
                           .agg({
                               '上线客户编码': 'first',
                               '上线客户名称': 'first',
                               '产品系列': lambda x: set(x.dropna()),
                               '产品序列号': 'count',
                           })
                           .rename(columns={'产品序列号': '台数'}))

                # 只保留多产品篮子（单产品无关联意义）
                multi = baskets[baskets['产品系列'].apply(len) >= 2]
                n_baskets = len(multi)

            if n_baskets < 10:
                st.warning(f"多产品项目篮子数 {n_baskets} 太少（< 10），无法做关联分析。")
            else:
                # ── Apriori 简化版（仅 1 项 + 2 项）──────
                from collections import Counter

                # 1-项集
                item_count = Counter()
                for s in multi['产品系列']:
                    for it in s:
                        item_count[it] += 1
                min_count = n_baskets * min_support
                frequent_1 = {it for it, c in item_count.items() if c >= min_count}

                # 2-项集
                pair_count = Counter()
                for s in multi['产品系列']:
                    items = sorted(s & frequent_1)
                    for i in range(len(items)):
                        for j in range(i + 1, len(items)):
                            pair_count[(items[i], items[j])] += 1
                frequent_2 = {p: c for p, c in pair_count.items() if c >= min_count}

                # 关联规则（A→B 和 B→A）
                rules = []
                for (a, b), sab in frequent_2.items():
                    sa, sb = item_count[a], item_count[b]
                    # A → B
                    conf_ab = sab / sa
                    lift_ab = conf_ab / (sb / n_baskets)
                    if conf_ab >= min_confidence and lift_ab >= min_lift:
                        rules.append({
                            'A': a, 'B': b,
                            '篮子数': sab,
                            '支持度': sab / n_baskets,
                            '置信度': conf_ab,
                            '提升度': lift_ab,
                        })
                    # B → A
                    conf_ba = sab / sb
                    lift_ba = conf_ba / (sa / n_baskets)
                    if conf_ba >= min_confidence and lift_ba >= min_lift:
                        rules.append({
                            'A': b, 'B': a,
                            '篮子数': sab,
                            '支持度': sab / n_baskets,
                            '置信度': conf_ba,
                            '提升度': lift_ba,
                        })

                rules.sort(key=lambda r: r['提升度'], reverse=True)

                # ── 概览 ──────────────────────────
                k_cols = st.columns(4)
                k_cols[0].metric("项目篮子数", f"{n_baskets:,}",
                                  help=f"全量 {len(baskets):,}，其中多产品 {n_baskets:,}")
                k_cols[1].metric("产品种类", f"{len(item_count)}")
                k_cols[2].metric("频繁 1-项集", f"{len(frequent_1)}")
                k_cols[3].metric("强关联规则", f"{len(rules)}")

                # ── Top 频繁项集（典型套餐）────────
                st.markdown("##### 📦 Top 20 频繁项集（典型套餐）")
                if frequent_2:
                    top_pairs = sorted(frequent_2.items(), key=lambda x: x[1], reverse=True)[:20]
                    pair_df = pd.DataFrame([{
                        '产品组合': f"{a}  ＋  {b}",
                        '共现篮子数': c,
                        '支持度': f"{c / n_baskets * 100:.1f}%",
                    } for (a, b), c in top_pairs])
                    st.dataframe(pair_df, use_container_width=True, hide_index=True, height=300)
                else:
                    st.info("无频繁项集（降低支持度阈值再试）")

                # ── Top 强关联规则 ──────────────
                st.markdown("##### 🔗 Top 20 强关联规则（按提升度排序）")
                if rules:
                    rule_df = pd.DataFrame([{
                        'A（前因）': r['A'],
                        'B（后果）': r['B'],
                        '篮子数': r['篮子数'],
                        '支持度': f"{r['支持度'] * 100:.1f}%",
                        '置信度': f"{r['置信度'] * 100:.0f}%",
                        '提升度': f"{r['提升度']:.1f}×",
                        '解读': '⭐ 强相关，建议成套销售'
                            if r['提升度'] >= 3
                            else '正相关，可考虑捆绑',
                    } for r in rules[:20]])
                    st.dataframe(rule_df, use_container_width=True, hide_index=True, height=300)
                else:
                    st.info("无符合阈值的强关联规则（降低置信度/提升度阈值再试）")

                # ── 销售机会名单：上 A 没上 B 的服务商 ────────
                st.markdown("##### 🎯 销售机会名单（上了 A 但没上 B 的服务商）")
                st.caption("对每条强规则，找出符合「上过 A」但「没上过 B」的服务商 → 精准销售目标")

                if rules:
                    # 全量服务商-产品集合（基于全部红包数据）
                    provider_products = (rp_ar.dropna(subset=['产品系列'])
                                          .groupby('上线客户编码')['产品系列']
                                          .apply(set))
                    provider_names = (rp_ar.dropna(subset=['上线客户编码'])
                                       .groupby('上线客户编码')['上线客户名称']
                                       .first())
                    provider_dealer = (rp_ar.dropna(subset=['上线客户编码'])
                                        .groupby('上线客户编码')['所属一级客户']
                                        .agg(lambda x: x.dropna().mode().iloc[0]
                                             if len(x.dropna().mode()) > 0 else None))

                    # 按规则展示前 8 条（避免信息过载）
                    for i, rule in enumerate(rules[:8]):
                        A, B = rule['A'], rule['B']
                        candidates_codes = []
                        for code, products in provider_products.items():
                            if A in products and B not in products:
                                candidates_codes.append(code)

                        n_cand = len(candidates_codes)
                        with st.expander(
                            f"📌 规则 {i + 1}：**{A}** → **{B}**"
                            f"（提升度 {rule['提升度']:.1f}× · 置信度 {rule['置信度'] * 100:.0f}%）"
                            f"　→ 找到 **{n_cand}** 个潜在 {B} 客户",
                            expanded=(i == 0),
                        ):
                            if n_cand == 0:
                                st.success(f"全部上 {A} 的服务商都已上 {B}，无潜在客户。")
                                continue

                            # 取这些服务商的 A 类台数（推测潜力）
                            sub_a = rp_ar[
                                (rp_ar['上线客户编码'].isin(candidates_codes))
                                & (rp_ar['产品系列'] == A)
                            ]
                            qty_a = sub_a.groupby('上线客户编码').agg(
                                **{f'{A}_台数': ('产品序列号', 'count'),
                                   f'{A}_金额': ('产品现有分销价', 'sum')}
                            )

                            # 拼装名单
                            cand_df = pd.DataFrame({
                                '服务商编码': candidates_codes,
                                '服务商名称': [provider_names.get(c, '—') for c in candidates_codes],
                                '签约代理商': [provider_dealer.get(c, '—') for c in candidates_codes],
                            }).merge(qty_a.reset_index().rename(
                                columns={'上线客户编码': '服务商编码'}),
                                on='服务商编码', how='left',
                            )
                            cand_df[f'{A}_金额'] = cand_df[f'{A}_金额'].round(0).fillna(0).astype(int)
                            cand_df[f'{A}_台数'] = cand_df[f'{A}_台数'].fillna(0).astype(int)
                            cand_df = cand_df.sort_values(f'{A}_台数', ascending=False)

                            st.dataframe(
                                cand_df,
                                use_container_width=True,
                                hide_index=True,
                                height=min(60 + n_cand * 36, 320),
                            )

                            import io
                            bf = io.BytesIO()
                            with pd.ExcelWriter(bf, engine='openpyxl') as w:
                                cand_df.to_excel(w, sheet_name='销售机会', index=False)
                            safe = ''.join(c if c.isalnum() else '_' for c in f"{A}_TO_{B}")[:40]
                            st.download_button(
                                f"📥 导出 Excel（{n_cand} 家潜在 {B} 客户）",
                                data=bf.getvalue(),
                                file_name=f"AR_{safe}.xlsx",
                                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                key=f"ar_dl_{i}",
                            )

                    if len(rules) > 8:
                        st.caption(f"⚙️ 仅展示前 8 条规则的销售机会。共 {len(rules)} 条规则，"
                                    "如需更多，请提高提升度阈值缩小规则集。")


# ════════════════════ 实验 5：红包刺激分析 ════════════════════
with tab_stim:
    import io as _io
    from datetime import datetime as _dt

    st.markdown("#### 💉 红包刺激分析（因果视角）")
    st.caption(
        "核心问题：**大红包真的能让服务商多上线吗？** "
        "方法：对每个中大红包的服务商，对比中奖前后 N 天的上线量（事件研究法 Event Study）。"
    )

    rp_stim = load_redpack()
    if rp_stim.empty:
        st.warning("📦 安装红包记录暂无数据。请到主页选择「安装红包记录」并上传 Excel。")
    else:
        rp_stim = rp_stim.copy()
        rp_stim['_事件时间'] = pd.to_datetime(rp_stim.get('抽奖机会发放时间'), errors='coerce')
        rp_stim['_事件时间'] = rp_stim['_事件时间'].fillna(rp_stim['上线时间'])
        rp_stim['上线客户编码'] = rp_stim['上线客户编码'].astype(str).str.strip()
        rp_stim['中奖金额'] = pd.to_numeric(rp_stim['中奖金额'], errors='coerce').fillna(0)
        won_stim = rp_stim[rp_stim['中奖金额'] > 0].copy()

        # ── A. 红包池概况 ──────────────────────────
        st.markdown("##### A. 红包池概况")
        total_records = len(rp_stim)
        n_won = len(won_stim)
        total_amt = float(won_stim['中奖金额'].sum())
        n_provider_won = won_stim['上线客户编码'].nunique() if not won_stim.empty else 0
        n_provider_all = rp_stim['上线客户编码'].nunique()
        win_rate = n_won / total_records if total_records else 0
        avg_amt = total_amt / n_won if n_won else 0

        a1, a2, a3, a4, a5 = st.columns(5)
        a1.metric("上线记录总数", f"{total_records:,}")
        a2.metric("中奖记录数", f"{n_won:,}", help=f"中奖率 {win_rate:.1%}")
        a3.metric("中奖金额总和", f"¥{total_amt:,.0f}")
        a4.metric("平均单包金额", f"¥{avg_amt:,.0f}")
        a5.metric("中奖服务商 / 全量",
                  f"{n_provider_won:,} / {n_provider_all:,}",
                  help=f"覆盖率 {n_provider_won / max(n_provider_all, 1):.1%}")

        if n_won > 0:
            qs_stim = won_stim['中奖金额'].quantile([0.5, 0.75, 0.9, 0.95, 0.99]).round(2)
            st.caption(
                f"💰 中奖金额分位数：P50=¥{qs_stim.iloc[0]:,.0f} · P75=¥{qs_stim.iloc[1]:,.0f} · "
                f"**P90=¥{qs_stim.iloc[2]:,.0f}** · P95=¥{qs_stim.iloc[3]:,.0f} · P99=¥{qs_stim.iloc[4]:,.0f} · "
                f"最大=¥{won_stim['中奖金额'].max():,.0f}"
            )

        # 阈值配置
        st.markdown("###### 🎚️ 大红包阈值")
        ctl_cols = st.columns([1, 1, 1, 3])
        with ctl_cols[0]:
            th_mode = st.radio("阈值方式", options=['分位数', '固定金额'],
                               index=0, horizontal=True, key="stim_mode")
        with ctl_cols[1]:
            if th_mode == '分位数':
                big_q = st.selectbox("分位", options=[0.75, 0.9, 0.95, 0.99],
                                      index=1, key="stim_q",
                                      format_func=lambda x: f"P{int(x * 100)}")
                big_th = float(won_stim['中奖金额'].quantile(big_q)) if n_won > 0 else 0
            else:
                big_th = st.number_input(
                    "金额阈值（≥）",
                    value=int(qs_stim.iloc[2]) if n_won > 0 else 1000,
                    min_value=1, step=100, key="stim_th_value",
                )
        with ctl_cols[2]:
            window_days = st.number_input(
                "对比窗口（天）", value=30, min_value=7, max_value=180,
                step=1, key="stim_window",
                help="t0 前 N 天 vs 后 N 天",
            )

        big_stim = won_stim[won_stim['中奖金额'] >= big_th].copy()
        n_big = len(big_stim)
        n_provider_big = big_stim['上线客户编码'].nunique() if not big_stim.empty else 0

        st.caption(
            f"🎯 大红包定义：中奖金额 ≥ **¥{big_th:,.0f}** → 共 **{n_big:,}** 个事件，"
            f"涉及 **{n_provider_big:,}** 个服务商"
        )

        st.divider()

        if big_stim.empty:
            st.info("当前阈值下无大红包事件，请调低阈值。")
        else:
            # ── B. 事件研究 ──────────────────────────
            st.markdown("##### B. 大红包前后上线量对比（事件研究）")
            st.caption(
                f"对每个中过大红包的服务商：找首次中奖时点 t0，对比 [t0-{window_days}d, t0) 和 "
                f"(t0, t0+{window_days}d] 的上线台数。提振率 = (后 - 前) / max(前, 1)。"
            )

            with st.spinner("正在计算事件前后对比..."):
                es = _stim_event_study(big_stim, rp_stim, window_days)

            if not es.empty:
                n_total = len(es)
                n_up = int((es['提振台数'] > 0).sum())
                n_flat = int((es['提振台数'] == 0).sum())
                n_down = int((es['提振台数'] < 0).sum())
                mean_lift_台 = es['提振台数'].mean()
                median_lift_台 = es['提振台数'].median()
                mean_lift_rate = es['提振率'].mean()
                n_zero_baseline = int((es['前N天台数'] == 0).sum())

                b1, b2, b3, b4, b5 = st.columns(5)
                b1.metric("受影响服务商", f"{n_total:,}")
                b2.metric("📈 上线增加", f"{n_up:,}",
                          delta=f"{n_up / n_total:.0%}",
                          help=f"中奖后 {window_days} 天上线台数 > 中奖前 {window_days} 天")
                b3.metric("➡️ 持平", f"{n_flat:,}", delta=f"{n_flat / n_total:.0%}")
                b4.metric("📉 上线减少", f"{n_down:,}", delta=f"-{n_down / n_total:.0%}")
                b5.metric("平均提振", f"{mean_lift_台:+.1f} 台",
                          help=f"中位数 {median_lift_台:+.1f} 台 / 平均提振率 {mean_lift_rate:+.1%}")

                if mean_lift_台 > 0 and n_up > n_down:
                    st.success(
                        f"✅ **整体结论：大红包有提振作用**。{n_up / n_total:.0%} 的服务商在中奖后上线增加，"
                        f"平均每个服务商在中奖后 {window_days} 天比前 {window_days} 天多上 **{mean_lift_台:.1f} 台**。"
                    )
                elif n_up <= n_down:
                    st.warning(
                        f"⚠️ **结论存疑**：只有 {n_up / n_total:.0%} 的服务商在中奖后上线增加，"
                        f"而 {n_down / n_total:.0%} 反而减少。建议看 D 段处理组 vs 对照组。"
                    )
                else:
                    st.info(
                        f"📊 中性结论：上线增加 {n_up / n_total:.0%}，"
                        f"减少 {n_down / n_total:.0%}，平均提振 {mean_lift_台:+.1f} 台。"
                    )

                if n_zero_baseline > 0:
                    st.caption(
                        f"⚠️ 其中 {n_zero_baseline} 个服务商中奖前 {window_days} 天没有任何上线（首次上线就中大红包），"
                        f"提振率会被 max(前, 1) 兜底，看绝对台数更准。"
                    )

                # 提振分布直方图
                hist_data = pd.cut(
                    es['提振台数'],
                    bins=[-np.inf, -10, -5, -1, 0, 1, 5, 10, 20, np.inf],
                    labels=['↓>10', '↓5~10', '↓1~5', '0', '↑1', '↑2~5', '↑6~10', '↑11~20', '↑>20'],
                ).value_counts().reindex(
                    ['↓>10', '↓5~10', '↓1~5', '0', '↑1', '↑2~5', '↑6~10', '↑11~20', '↑>20'],
                    fill_value=0,
                )
                st.markdown("###### 提振分布（按台数）")
                st.bar_chart(hist_data)

                # 详细名单
                st.markdown("###### 服务商级别详细名单")
                sort_by = st.radio(
                    "排序",
                    options=['提振台数（降序）', '大红包金额（降序）',
                             '提振率（降序）', '提振台数（升序，看下降）'],
                    horizontal=True, key="stim_sort",
                )
                sort_map = {
                    '提振台数（降序）': ('提振台数', False),
                    '大红包金额（降序）': ('大红包金额', False),
                    '提振率（降序）': ('提振率', False),
                    '提振台数（升序，看下降）': ('提振台数', True),
                }
                col_s, asc_s = sort_map[sort_by]
                es_show = es.sort_values(col_s, ascending=asc_s).copy()
                es_show['t0'] = pd.to_datetime(es_show['t0']).dt.strftime('%Y-%m-%d')
                es_show['大红包金额'] = es_show['大红包金额'].round(0).astype(int)
                es_show['前N天金额'] = es_show['前N天金额'].round(0).astype(int)
                es_show['后N天金额'] = es_show['后N天金额'].round(0).astype(int)
                es_show['提振率'] = es_show['提振率'].apply(lambda v: f"{v:+.1%}")
                es_show['金额提振率'] = es_show['金额提振率'].apply(lambda v: f"{v:+.1%}")

                st.dataframe(
                    es_show[[
                        '服务商', '客户编码', 't0', '大红包金额',
                        '前N天台数', '后N天台数', '提振台数', '提振率',
                        '前N天金额', '后N天金额', '金额提振率',
                    ]].head(500),
                    use_container_width=True, hide_index=True, height=380,
                )

                buf_es = _io.BytesIO()
                with pd.ExcelWriter(buf_es, engine='openpyxl') as w:
                    es_show.to_excel(w, sheet_name='事件研究', index=False)
                stamp = _dt.now().strftime('%Y%m%d')
                st.download_button(
                    f"📥 导出 Excel（{len(es_show):,} 家）",
                    data=buf_es.getvalue(),
                    file_name=f"红包刺激_事件研究_{stamp}.xlsx",
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    key='stim_dl_event',
                )

            st.divider()

            # ── C. 金额-效应曲线 ──────────────────────────
            st.markdown("##### C. 金额-效应曲线：多大才有效？")
            st.caption(
                "把所有中奖事件（含小红包）按金额分桶，看每桶服务商的平均上线提振。"
                "目标：找拐点——多大金额开始有效？是否边际递减？"
            )

            with st.spinner("计算金额-效应曲线..."):
                curve_df = _stim_amount_curve(won_stim, rp_stim, window_days)

            if not curve_df.empty:
                bins = [0, 50, 100, 300, 500, 1000, 3000, 5000, 10000, np.inf]
                labels = ['<50', '50-100', '100-300', '300-500',
                          '500-1k', '1k-3k', '3k-5k', '5k-10k', '>10k']
                curve_df['金额桶'] = pd.cut(curve_df['金额'], bins=bins, labels=labels, right=True)

                bucket_stats = curve_df.groupby('金额桶', observed=True).agg(
                    服务商数=('客户编码', 'count'),
                    平均提振=('提振台数', 'mean'),
                    中位提振=('提振台数', 'median'),
                    提振率=('提振台数', lambda x: (x > 0).mean()),
                ).reindex(labels).fillna(0)
                bucket_stats['平均提振'] = bucket_stats['平均提振'].round(2)
                bucket_stats['中位提振'] = bucket_stats['中位提振'].round(1)
                bucket_stats['提振率'] = (bucket_stats['提振率'] * 100).round(1).astype(str) + '%'

                bcol1, bcol2 = st.columns([2, 3])
                with bcol1:
                    st.dataframe(
                        bucket_stats.reset_index(),
                        use_container_width=True, hide_index=True, height=380,
                    )
                with bcol2:
                    st.bar_chart(
                        curve_df.groupby('金额桶', observed=True)['提振台数']
                        .mean().reindex(labels).fillna(0),
                    )

                positive_buckets = bucket_stats[bucket_stats['平均提振'] > 0]
                if not positive_buckets.empty:
                    first_eff = positive_buckets.index[0]
                    st.success(
                        f"✅ **金额拐点提示**：从 **{first_eff}** 元金额段开始平均提振转正。"
                        f"建议：低于此金额的红包对上线刺激很弱，可考虑收紧成本或合并到更大单包。"
                    )

            st.divider()

            # ── D. 处理组 vs 对照组 ──────────────────────────
            st.markdown("##### D. 处理组 vs 对照组（剔除大盘趋势）")
            st.caption(
                "**处理组**：中过大红包的服务商；**对照组**：同期有上线但**从未中过任何红包**的服务商。"
                "如果处理组涨幅明显高于对照组 → 大红包真有提振；如果两组趋势一致 → 大盘在涨而非红包效应。"
            )

            with st.spinner("计算 DID..."):
                did = _stim_did(big_stim, rp_stim, window_days)

            if did:
                d1, d2, d3 = st.columns(3)
                d1.metric("处理组人均增量", f"{did['处理_增量']:+.2f} 台",
                          help=f"前 {did['处理_前']:.1f} → 后 {did['处理_后']:.1f}，N={did['处理组N']}")
                d2.metric("对照组人均增量", f"{did['对照_增量']:+.2f} 台",
                          help=f"前 {did['对照_前']:.1f} → 后 {did['对照_后']:.1f}，N={did['对照组N']}")
                d3.metric("🎯 净提振（DID）", f"{did['DID']:+.2f} 台/服务商",
                          help="(处理组增量) - (对照组增量)，剔除大盘趋势后的净红包效应")

                if did['DID'] > 0.5:
                    st.success(
                        f"✅ **大红包确实有效**：剔除大盘趋势后，每个中大红包的服务商比未中红包的多上线 "
                        f"**{did['DID']:.1f} 台**。"
                    )
                elif did['DID'] > 0:
                    st.info(f"📊 **轻度提振**：净效应 +{did['DID']:.2f} 台/人，效应较弱但为正。")
                else:
                    st.warning(
                        f"⚠️ **效果不明显**：净效应 {did['DID']:+.2f} 台/人，"
                        f"大红包后处理组的上线表现不如对照组。需结合金额段、产品类别再深挖。"
                    )
                st.caption(
                    f"伪事件时间（对照组锚点）：{did['pseudo_t0'].strftime('%Y-%m-%d')} = 所有大红包事件时间的均值"
                )

            st.divider()

            # ── E. 单服务商时间序列 ──────────────────────────
            st.markdown("##### E. 单服务商时间序列深挖")
            candidates = (
                big_stim[['上线客户编码', '上线客户名称']]
                .drop_duplicates(subset='上线客户编码')
                .sort_values('上线客户名称')
            )
            candidates['标签'] = (
                candidates['上线客户名称'].fillna('') + ' (' + candidates['上线客户编码'] + ')'
            )

            picked_label = st.selectbox(
                "选服务商（仅含中过大红包的）",
                options=candidates['标签'].tolist(),
                key="stim_single_provider",
            )
            if picked_label:
                picked_code = candidates.loc[candidates['标签'] == picked_label, '上线客户编码'].iloc[0]
                sub = rp_stim[rp_stim['上线客户编码'] == picked_code].copy()
                sub['_事件时间'] = pd.to_datetime(sub['_事件时间'])
                sub = sub.dropna(subset=['_事件时间']).sort_values('_事件时间')

                if not sub.empty:
                    sub['月份'] = sub['_事件时间'].dt.to_period('M').astype(str)
                    monthly = sub.groupby('月份').agg(
                        上线台数=('产品序列号', 'count'),
                        上线金额=('产品现有分销价', 'sum'),
                        中奖次数=('中奖金额', lambda x: (x > 0).sum()),
                        中奖总额=('中奖金额', 'sum'),
                        大红包次数=('中奖金额', lambda x: (x >= big_th).sum()),
                    ).reset_index()
                    monthly['上线金额'] = monthly['上线金额'].round(0)
                    monthly['中奖总额'] = monthly['中奖总额'].round(0)

                    st.markdown("**月度上线 vs 红包**")
                    st.dataframe(
                        monthly,
                        use_container_width=True, hide_index=True,
                        height=min(60 + len(monthly) * 36, 380),
                    )

                    big_events_for_provider = sub[sub['中奖金额'] >= big_th][['_事件时间', '中奖金额']]
                    st.markdown("**该服务商的大红包事件**")
                    if not big_events_for_provider.empty:
                        ev_show = big_events_for_provider.copy()
                        ev_show['_事件时间'] = ev_show['_事件时间'].dt.strftime('%Y-%m-%d')
                        ev_show['中奖金额'] = ev_show['中奖金额'].round(0).astype(int)
                        st.dataframe(ev_show, use_container_width=True, hide_index=True)

                    st.markdown("**月度上线台数趋势**")
                    st.bar_chart(monthly.set_index('月份')['上线台数'])


st.divider()
st.divider()
st.caption(
    "💡 想要新功能？告诉我你的想法。已有候选：「自然语言问答」「设备明细 PDF 导出」"
    "「服务商关系网络图」「下月 SO 预测」「项目订单识别」「健康度综合评分」等。"
)
