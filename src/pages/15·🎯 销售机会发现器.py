#!/usr/bin/env python3
"""
🎯 销售机会发现器
通过数据交叉穿透发现：
  A. 销售机会（找谁该补什么品）—— 2 个
  B. 渠道质量反推（反向评价代理商）—— 4 个
  C. 异常发现（沉默/爆发/异常价格等）—— 6 个
  D. 地理碰撞（空白市场/跨区/集中度）—— 3 个
  E. 红包激活（中奖率/触发率/异地）—— 3 个

每个发现器输出名单 + 一键导出 Excel。
"""

import io
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# 引入共享加载模块（跨 page 共享缓存）
sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import load_main_shared, load_redpack_shared, DB_PATH  # noqa: E402

require_auth()
st.markdown("### 🎯 销售机会发现器")
st.caption("数据交叉穿透 · 找新客户 / 反推渠道质量 / 异常预警。每个发现器输出可导出 Excel 名单。")
st.caption(
    "💡 **口径**：销量基线用 **🌐 全量感知**（product_flow），服务商画像 / 绑定用 **🎯 安装红包**（install_redpack）。"
    "「未承接销售」= 全量感知里有但红包表无绑定的销量（潜在新服务商签约目标）。"
)


# ──────────────────────────────────────────
# 数据加载（用共享 loader，避免每个 page 重复加载 42 万行红包表）
# ──────────────────────────────────────────

def load_main() -> pd.DataFrame:
    return load_main_shared()


def load_redpack() -> pd.DataFrame:
    return load_redpack_shared()


# ──────────────────────────────────────────
# 通用工具
# ──────────────────────────────────────────

def excel_download(df: pd.DataFrame, filename: str, key: str, label: str = None):
    """统一的 Excel 导出按钮"""
    if df.empty:
        return
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name='Sheet1')
    stamp = datetime.now().strftime('%Y%m%d')
    full_name = f"{filename}_{stamp}.xlsx"
    st.download_button(
        label or f"📥 导出 Excel（{len(df):,} 条）",
        data=buf.getvalue(),
        file_name=full_name,
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        key=key,
    )


def is_nvr(series: pd.Series) -> pd.Series:
    """判断产品系列是否属于 NVR 类（含 'NVR' 字样）"""
    return series.fillna('').astype(str).str.contains('NVR', case=False, na=False)


def is_ipc(series: pd.Series, nvr_mask: pd.Series = None) -> pd.Series:
    """非 NVR 的非空记录视为 IPC（粗分类）"""
    if nvr_mask is None:
        nvr_mask = is_nvr(series)
    return series.notna() & ~nvr_mask


def fmt_pct(v):
    if pd.isna(v):
        return '—'
    return f'{v * 100:.1f}%'


# ──────────────────────────────────────────
# 全局参数（侧栏）
# ──────────────────────────────────────────

_main_df_full = load_main()
_rp_df_full = load_redpack()

if _main_df_full.empty and _rp_df_full.empty:
    st.warning("📦 数据库为空，请先到主页『📥 数据导入』上传 Excel。")
    st.stop()

# 候选项（从全量取，不受当前筛选影响）
_city_options = sorted(set(
    (_rp_df_full['安装城市'].dropna().astype(str).str.strip()
     if not _rp_df_full.empty else pd.Series([], dtype=str)).tolist()
    + (_main_df_full['上线城市'].dropna().astype(str).str.strip()
       if not _main_df_full.empty else pd.Series([], dtype=str)).tolist()
))
_city_options = [c for c in _city_options if c]

_dealer_options = sorted(set(
    (_rp_df_full['出货客户名称'].dropna().astype(str).str.strip()
     if not _rp_df_full.empty else pd.Series([], dtype=str)).tolist()
    + (_main_df_full['出库客户名称'].dropna().astype(str).str.strip()
       if not _main_df_full.empty else pd.Series([], dtype=str)).tolist()
))
_dealer_options = [d for d in _dealer_options if d]


with st.sidebar:
    st.header("全局参数")

    # 时间窗口
    available_months = sorted(set(_rp_df_full['上线年月'].dropna().tolist() if not _rp_df_full.empty else [])
                              | set(_main_df_full['上线年月'].dropna().tolist() if not _main_df_full.empty else []))
    available_months = [m for m in available_months if m != 'NaT']

    if len(available_months) >= 1:
        anchor = st.selectbox(
            "锚点月份（窗口结尾）",
            options=available_months,
            index=len(available_months) - 1,
            help="多数发现器以此为窗口末端往前数 N 月",
        )
        window_size = st.selectbox(
            "窗口大小（月）",
            options=[1, 3, 6, 12, 24],
            index=2,
            help="决定『近 N 月』的范围",
        )
        idx = available_months.index(anchor)
        window_months = available_months[max(0, idx - window_size + 1): idx + 1]
        st.caption(f"📅 窗口：**{window_months[0]} ~ {window_months[-1]}**（{len(window_months)} 月）")
    else:
        anchor = None
        window_size = 6
        window_months = []
        st.warning("数据里没有有效月份")

    st.divider()

    # 全局：上线地市 / 出货客户筛选（作用于所有发现器）
    picked_cities = st.multiselect(
        "上线地市筛选",
        options=_city_options,
        default=[],
        help=f"主表用「上线城市」、红包表用「安装城市」过滤；不选 = 全部。共 {len(_city_options)} 个候选",
    )
    picked_dealers = st.multiselect(
        "出货客户（代理商）筛选",
        options=_dealer_options,
        default=[],
        help=f"主表用「出库客户名称」、红包表用「出货客户名称」过滤；不选 = 全部。共 {len(_dealer_options)} 个候选",
    )

    st.divider()


# ──────────────────────────────────────────
# 应用全局筛选 → 所有发现器都用筛选后的数据
# ──────────────────────────────────────────

def _apply_global_filter_main(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if picked_cities and '上线城市' in out.columns:
        out = out[out['上线城市'].astype(str).str.strip().isin(picked_cities)]
    if picked_dealers and '出库客户名称' in out.columns:
        out = out[out['出库客户名称'].astype(str).str.strip().isin(picked_dealers)]
    return out


def _apply_global_filter_rp(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if picked_cities and '安装城市' in out.columns:
        out = out[out['安装城市'].astype(str).str.strip().isin(picked_cities)]
    if picked_dealers and '出货客户名称' in out.columns:
        out = out[out['出货客户名称'].astype(str).str.strip().isin(picked_dealers)]
    return out


main_df = _apply_global_filter_main(_main_df_full)
rp_df = _apply_global_filter_rp(_rp_df_full)

# 在侧栏 footer 显示当前筛选效果
with st.sidebar:
    st.caption(f"主表：{len(main_df):,} / {len(_main_df_full):,} 行")
    st.caption(f"红包表：{len(rp_df):,} / {len(_rp_df_full):,} 行")
    if picked_cities or picked_dealers:
        st.caption(
            f"🔍 已筛选：{len(picked_cities)} 城市 / {len(picked_dealers)} 出货客户"
        )

# 顶部状态条
if picked_cities or picked_dealers:
    parts = []
    if picked_cities:
        parts.append(f"🏙️ 上线地市：**{', '.join(picked_cities)}**")
    if picked_dealers:
        parts.append(f"🏛️ 出货客户：**{', '.join(picked_dealers)}**")
    st.info(" · ".join(parts))


def in_window(df, time_col='上线年月'):
    if df.empty or not window_months:
        return df
    return df[df[time_col].isin(window_months)]


# ──────────────────────────────────────────
# Tab 总入口
# ──────────────────────────────────────────

tab_a, tab_b, tab_c, tab_d, tab_e = st.tabs([
    "🛒 销售机会（2）",
    "👀 渠道质量反推（4）",
    "🚨 异常发现（6）",
    "🌐 地理碰撞（3）",
    "🎁 红包激活（3）",
])


# ════════════════════════════════════════════════════════════════════
# Tab A：销售机会 #1 #2
# ════════════════════════════════════════════════════════════════════
with tab_a:
    if rp_df.empty:
        st.warning("此 Tab 依赖『安装红包记录』表，请先到主页上传。")
    else:
        # ===== 本 Tab 局部城市筛选 =====
        _ci_opts = sorted(rp_df['安装城市'].dropna().astype(str).str.strip().unique().tolist())
        _ci_opts = [c for c in _ci_opts if c]
        _picked_local_ci = st.multiselect(
            "🏙️ 本 Tab 城市筛选（叠加在侧栏全局上，仅影响本 Tab）",
            options=_ci_opts, default=[], key="tab_a_local_cities",
        )
        _saved_rp, _saved_main = rp_df, main_df
        if _picked_local_ci:
            rp_df = rp_df[rp_df['安装城市'].astype(str).str.strip().isin(_picked_local_ci)]
            if not main_df.empty:
                main_df = main_df[main_df['上线城市'].astype(str).str.strip().isin(_picked_local_ci)]
        # ===== 局部筛选 完毕 =====

        # ───────── #1 品类缺口扫描 ─────────
        with st.expander("📦 #1 品类缺口扫描 — 上了 IPC 但没上 NVR 的服务商", expanded=True):
            st.caption("逻辑：在窗口内，服务商上了某类产品 ≥ N 台，但完全没上另一类 → 推后者")

            rp_w = in_window(rp_df)
            series_options = sorted(rp_w['产品系列'].dropna().unique().tolist())

            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                a_series = st.multiselect(
                    "已上的产品系列（A）",
                    options=series_options,
                    default=[s for s in series_options
                             if any(k in s for k in ['14', '12', '24', 'A40'])
                             and 'NVR' not in s.upper()],
                    key="f1_a",
                )
            with c2:
                b_series_default = [s for s in series_options if 'NVR' in s.upper()]
                b_series = st.multiselect(
                    "未上的产品系列（B）",
                    options=series_options,
                    default=b_series_default,
                    key="f1_b",
                )
            with c3:
                a_threshold = st.number_input("A 类台数 ≥", min_value=1, value=5, key="f1_th")

            if a_series and b_series:
                rp_w_valid = rp_w[rp_w['产品系列'].notna()]
                a_mask = rp_w_valid['产品系列'].isin(a_series)
                b_mask = rp_w_valid['产品系列'].isin(b_series)

                ag = rp_w_valid.groupby(['上线客户编码', '上线客户名称']).agg(
                    A台数=('产品序列号', lambda x: a_mask.loc[x.index].sum()),
                    A金额=('产品现有分销价', lambda x: x[a_mask.loc[x.index]].sum()),
                    B台数=('产品序列号', lambda x: b_mask.loc[x.index].sum()),
                    签约代理商=('所属一级客户',
                                lambda x: x.dropna().mode().iloc[0]
                                if len(x.dropna().mode()) > 0 else None),
                ).reset_index()

                gap = ag[(ag['A台数'] >= a_threshold) & (ag['B台数'] == 0)]\
                    .sort_values('A台数', ascending=False)
                gap['A金额'] = gap['A金额'].round(0).astype(int)

                st.markdown(f"**🎯 发现 {len(gap)} 个潜在 NVR 客户**")
                if not gap.empty:
                    show = gap[['上线客户编码', '上线客户名称', '签约代理商',
                                'A台数', 'A金额', 'B台数']].rename(
                        columns={'A台数': 'A类台数', 'A金额': 'A类金额', 'B台数': 'B类台数（=0）'})
                    st.dataframe(show, use_container_width=True, hide_index=True, height=400)
                    excel_download(show, "F1_品类缺口", "f1_dl")
                else:
                    st.info("当前阈值下无候选。降低阈值或调整产品系列试试。")
            else:
                st.info("请选择 A 类和 B 类产品系列")

        # ───────── #2 配比失衡扫描 ─────────
        with st.expander("⚖️ #2 配比失衡扫描 — IPC 远多于 NVR（推算应配 NVR 数）", expanded=False):
            st.caption("逻辑：1 台 NVR 通常接 N 路 IPC（默认 16）。IPC 数 ÷ NVR 数 > N 即失衡，多余 IPC 怀疑接到了竞品 NVR。")

            c1, c2 = st.columns([1, 1])
            with c1:
                ratio_threshold = st.number_input(
                    "IPC ÷ NVR 比例阈值（超过即失衡）", min_value=2, value=16, key="f2_ratio")
            with c2:
                min_ipc = st.number_input(
                    "IPC 台数 ≥", min_value=1, value=10, key="f2_minipc")

            rp_w = in_window(rp_df)
            rp_w = rp_w[rp_w['产品系列'].notna()].copy()
            rp_w['_is_nvr'] = is_nvr(rp_w['产品系列'])
            rp_w['_is_ipc'] = ~rp_w['_is_nvr']

            ag = rp_w.groupby(['上线客户编码', '上线客户名称']).apply(
                lambda x: pd.Series({
                    'IPC台数': x['_is_ipc'].sum(),
                    'NVR台数': x['_is_nvr'].sum(),
                    '签约代理商': (x['所属一级客户'].dropna().mode().iloc[0]
                                    if len(x['所属一级客户'].dropna().mode()) > 0 else None),
                })
            ).reset_index()
            ag['比例'] = np.where(ag['NVR台数'] > 0,
                                  ag['IPC台数'] / ag['NVR台数'].replace(0, np.nan),
                                  np.inf)
            ag['推算缺NVR'] = np.where(
                ag['IPC台数'] >= min_ipc,
                np.maximum(0, np.ceil(ag['IPC台数'] / ratio_threshold).astype(int)
                            - ag['NVR台数']),
                0,
            )

            imb = ag[(ag['IPC台数'] >= min_ipc)
                    & ((ag['NVR台数'] == 0) | (ag['比例'] > ratio_threshold))]\
                .sort_values('推算缺NVR', ascending=False)

            st.markdown(f"**🎯 发现 {len(imb)} 家配比失衡服务商**")
            if not imb.empty:
                imb_show = imb.copy()
                imb_show['比例'] = imb_show['比例'].apply(
                    lambda v: '∞（无 NVR）' if v == np.inf else f'{v:.1f}')
                st.dataframe(
                    imb_show[['上线客户编码', '上线客户名称', '签约代理商',
                              'IPC台数', 'NVR台数', '比例', '推算缺NVR']],
                    use_container_width=True, hide_index=True, height=400,
                )
                excel_download(imb_show, "F2_配比失衡", "f2_dl")

# ════════════════════════════════════════════════════════════════════
# Tab B：渠道质量反推 #6 #7 #8 #9
# ════════════════════════════════════════════════════════════════════

        # ===== 恢复 rp_df / main_df，避免污染下一个 Tab =====
        rp_df, main_df = _saved_rp, _saved_main
with tab_b:
    if rp_df.empty:
        st.warning("此 Tab 依赖『安装红包记录』表，请先到主页上传。")
    else:
        # ===== 本 Tab 局部城市筛选 =====
        _ci_opts = sorted(rp_df['安装城市'].dropna().astype(str).str.strip().unique().tolist())
        _ci_opts = [c for c in _ci_opts if c]
        _picked_local_ci = st.multiselect(
            "🏙️ 本 Tab 城市筛选（叠加在侧栏全局上，仅影响本 Tab）",
            options=_ci_opts, default=[], key="tab_b_local_cities",
        )
        _saved_rp, _saved_main = rp_df, main_df
        if _picked_local_ci:
            rp_df = rp_df[rp_df['安装城市'].astype(str).str.strip().isin(_picked_local_ci)]
            if not main_df.empty:
                main_df = main_df[main_df['上线城市'].astype(str).str.strip().isin(_picked_local_ci)]
        # ===== 局部筛选 完毕 =====

        # ───────── #6 多源采购红榜 ─────────
        with st.expander("🔄 #6 多源采购红榜 — 服务商从多家代理商进货", expanded=True):
            st.caption("逻辑：服务商在窗口内从 ≥ N 家代理商进货 → 忠诚度低 / 主签约代理商服务差")

            n_dealers = st.number_input("代理商数 ≥", min_value=2, value=2, key="f6_n")

            rp_w = in_window(rp_df).copy()
            rp_w = rp_w[rp_w['出货客户名称'].notna()]

            grp = rp_w.groupby(['上线客户编码', '上线客户名称']).apply(
                lambda x: pd.Series({
                    '总台数': len(x),
                    '代理商数': x['出货客户名称'].nunique(),
                    '签约代理商': (x['所属一级客户'].dropna().mode().iloc[0]
                                    if len(x['所属一级客户'].dropna().mode()) > 0 else None),
                    '签约采购台数': (x['出货客户名称'].astype(str).str.strip()
                                      == x['所属一级客户'].astype(str).str.strip()).sum(),
                    '主供应代理商': (x['出货客户名称'].mode().iloc[0]
                                      if len(x['出货客户名称'].mode()) > 0 else None),
                })
            ).reset_index()
            grp['忠诚率'] = grp['签约采购台数'] / grp['总台数'].replace(0, np.nan)
            multi = grp[grp['代理商数'] >= n_dealers].sort_values('代理商数', ascending=False)

            st.markdown(f"**🎯 发现 {len(multi)} 家多源采购服务商**")
            if not multi.empty:
                m_show = multi.copy()
                m_show['忠诚率'] = m_show['忠诚率'].apply(fmt_pct)
                st.dataframe(
                    m_show[['上线客户编码', '上线客户名称', '签约代理商',
                            '代理商数', '总台数', '签约采购台数', '忠诚率', '主供应代理商']],
                    use_container_width=True, hide_index=True, height=400,
                )
                excel_download(m_show, "F6_多源采购", "f6_dl")

        # ───────── #7 流失风险预警 ─────────
        with st.expander("💔 #7 流失风险预警 — 连续 N 月未从签约代理商进货", expanded=False):
            st.caption("逻辑：窗口内 0 台从签约代理商进货 + 有非签约采购 → 流失风险")

            rp_w = in_window(rp_df).copy()
            rp_w = rp_w[rp_w['所属一级客户'].notna() & rp_w['出货客户名称'].notna()]
            rp_w['_signed'] = (rp_w['出货客户名称'].astype(str).str.strip()
                                == rp_w['所属一级客户'].astype(str).str.strip())

            grp = rp_w.groupby(['上线客户编码', '上线客户名称']).apply(
                lambda x: pd.Series({
                    '签约代理商': (x['所属一级客户'].dropna().mode().iloc[0]
                                    if len(x['所属一级客户'].dropna().mode()) > 0 else None),
                    '签约进货': x['_signed'].sum(),
                    '其他进货': (~x['_signed']).sum(),
                    '主要其他代理商': (x[~x['_signed']]['出货客户名称'].mode().iloc[0]
                                        if (~x['_signed']).any()
                                        and len(x[~x['_signed']]['出货客户名称'].mode()) > 0
                                        else None),
                })
            ).reset_index()
            churn = grp[(grp['签约进货'] == 0) & (grp['其他进货'] > 0)]\
                .sort_values('其他进货', ascending=False)

            st.markdown(f"**🎯 发现 {len(churn)} 个流失服务商（窗口 {len(window_months)} 月）**")
            if not churn.empty:
                st.dataframe(
                    churn[['上线客户编码', '上线客户名称', '签约代理商',
                           '签约进货', '其他进货', '主要其他代理商']],
                    use_container_width=True, hide_index=True, height=400,
                )
                excel_download(churn, "F7_流失风险", "f7_dl")

        # ───────── #8 代理商旗下沉睡率 ─────────
        with st.expander("😴 #8 代理商旗下沉睡率 — 哪些代理商在睡觉", expanded=False):
            st.caption(
                "逻辑：代理商签约的服务商总数 vs 窗口内有动作的数。"
                "沉睡率高 + 总数大 = 该代理商对旗下服务商缺乏激活"
            )

            min_total = st.number_input("旗下服务商总数 ≥", min_value=1, value=10, key="f8_min")

            # 全量算签约关系（不限窗口）
            all_signs = (rp_df.dropna(subset=['所属一级客户', '上线客户编码'])
                         [['所属一级客户', '上线客户编码']].drop_duplicates())
            total_per_dealer = all_signs.groupby('所属一级客户').size().rename('旗下总数')

            # 窗口内活跃服务商
            rp_w = in_window(rp_df)
            active_pairs = (rp_w.dropna(subset=['所属一级客户', '上线客户编码'])
                            [['所属一级客户', '上线客户编码']].drop_duplicates())
            active_per_dealer = active_pairs.groupby('所属一级客户').size().rename('窗口内活跃')

            sleep_df = pd.concat([total_per_dealer, active_per_dealer], axis=1).fillna(0)
            sleep_df['沉睡数'] = sleep_df['旗下总数'] - sleep_df['窗口内活跃']
            sleep_df['沉睡率'] = sleep_df['沉睡数'] / sleep_df['旗下总数']
            sleep_df = sleep_df[sleep_df['旗下总数'] >= min_total]\
                .sort_values(['沉睡率', '旗下总数'], ascending=[False, False])

            st.markdown(f"**🎯 找到 {len(sleep_df)} 家代理商**（旗下服务商 ≥ {min_total}）")
            if not sleep_df.empty:
                show = sleep_df.reset_index().rename(columns={'所属一级客户': '代理商'})
                show['旗下总数'] = show['旗下总数'].astype(int)
                show['窗口内活跃'] = show['窗口内活跃'].astype(int)
                show['沉睡数'] = show['沉睡数'].astype(int)
                show['沉睡率'] = show['沉睡率'].apply(fmt_pct)
                st.dataframe(show, use_container_width=True, hide_index=True, height=400)
                excel_download(show, "F8_旗下沉睡率", "f8_dl")

        # ───────── #9 跨区域代理商扫描 ─────────
        with st.expander("🌍 #9 跨区域代理商扫描 — 出货跑出经营地市", expanded=False):
            st.caption("逻辑：代理商经营范围是地市；安装城市 ≠ 出货客户城市 = 跨区域出货")

            c1, c2 = st.columns([1, 1])
            with c1:
                min_qty = st.number_input(
                    "代理商总出货 ≥", min_value=1, value=50, key="f9_minqty")
            with c2:
                only_show_high = st.checkbox(
                    "只看跨区域率 ≥ 30%", value=False, key="f9_only_high")

            rp_w = in_window(rp_df).copy()
            rp_w = rp_w[rp_w['出货客户名称'].notna() & rp_w['出货客户城市'].notna()
                        & rp_w['安装城市'].notna()]
            rp_w['_cross'] = (rp_w['出货客户城市'].astype(str).str.strip()
                                != rp_w['安装城市'].astype(str).str.strip())

            grp = rp_w.groupby('出货客户名称').apply(
                lambda x: pd.Series({
                    '所在地市': (x['出货客户城市'].dropna().mode().iloc[0]
                                  if len(x['出货客户城市'].dropna().mode()) > 0 else None),
                    '总台数': len(x),
                    '跨区域台数': x['_cross'].sum(),
                    '主要去向地市': (x[x['_cross']]['安装城市'].mode().iloc[0]
                                      if x['_cross'].any()
                                      and len(x[x['_cross']]['安装城市'].mode()) > 0 else None),
                })
            ).reset_index()
            grp['跨区域率'] = grp['跨区域台数'] / grp['总台数']
            grp = grp[grp['总台数'] >= min_qty]
            if only_show_high:
                grp = grp[grp['跨区域率'] >= 0.3]
            grp = grp.sort_values(['跨区域率', '跨区域台数'], ascending=[False, False])

            st.markdown(f"**🎯 找到 {len(grp)} 家代理商**")
            if not grp.empty:
                show = grp.copy()
                show['跨区域率'] = show['跨区域率'].apply(fmt_pct)
                show['总台数'] = show['总台数'].astype(int)
                show['跨区域台数'] = show['跨区域台数'].astype(int)
                st.dataframe(
                    show.rename(columns={'出货客户名称': '代理商'}),
                    use_container_width=True, hide_index=True, height=400,
                )
                excel_download(show, "F9_跨区域代理商", "f9_dl")


# ════════════════════════════════════════════════════════════════════
# Tab C：异常发现 #10 #11 #12 #13 #14 #15
# ════════════════════════════════════════════════════════════════════

        # ===== 恢复 rp_df / main_df，避免污染下一个 Tab =====
        rp_df, main_df = _saved_rp, _saved_main
with tab_c:
    if rp_df.empty:
        st.warning("此 Tab 依赖『安装红包记录』表，请先到主页上传。")
    else:
        # ===== 本 Tab 局部城市筛选 =====
        _ci_opts = sorted(rp_df['安装城市'].dropna().astype(str).str.strip().unique().tolist())
        _ci_opts = [c for c in _ci_opts if c]
        _picked_local_ci = st.multiselect(
            "🏙️ 本 Tab 城市筛选（叠加在侧栏全局上，仅影响本 Tab）",
            options=_ci_opts, default=[], key="tab_c_local_cities",
        )
        _saved_rp, _saved_main = rp_df, main_df
        if _picked_local_ci:
            rp_df = rp_df[rp_df['安装城市'].astype(str).str.strip().isin(_picked_local_ci)]
            if not main_df.empty:
                main_df = main_df[main_df['上线城市'].astype(str).str.strip().isin(_picked_local_ci)]
        # ===== 局部筛选 完毕 =====

        # ───────── #10 沉默服务商唤醒 ─────────
        with st.expander("📞 #10 沉默服务商唤醒 — 历史活跃 + 近期归零", expanded=True):
            st.caption("逻辑：历史月均台数 ≥ X，但近 N 月台数 = 0 → 应回访")

            c1, c2, c3 = st.columns(3)
            with c1:
                hist_avg = st.number_input("历史月均 ≥", min_value=1, value=5, key="f10_hist")
            with c2:
                silent_n = st.selectbox("近 N 月归零", options=[2, 3, 6], index=1, key="f10_n")
            with c3:
                pass

            if window_months and len(available_months) > silent_n:
                # 最近 N 月（取锚点月往前 N 个月）
                end_idx = available_months.index(anchor)
                recent = available_months[max(0, end_idx - silent_n + 1): end_idx + 1]
                history = [m for m in available_months if m not in recent]

                rp_recent = rp_df[rp_df['上线年月'].isin(recent)]
                rp_hist = rp_df[rp_df['上线年月'].isin(history)]

                hist_g = rp_hist.groupby(['上线客户编码', '上线客户名称']).agg(
                    历史台数=('产品序列号', 'count'),
                    上次活跃=('上线时间', 'max'),
                    签约代理商=('所属一级客户',
                                lambda x: x.dropna().mode().iloc[0]
                                if len(x.dropna().mode()) > 0 else None),
                ).reset_index()
                hist_g['历史月均'] = hist_g['历史台数'] / max(len(history), 1)

                recent_active = set(rp_recent['上线客户编码'].dropna())
                silent = hist_g[
                    (hist_g['历史月均'] >= hist_avg)
                    & ~hist_g['上线客户编码'].isin(recent_active)
                ].sort_values('历史月均', ascending=False)

                st.markdown(f"**🎯 找到 {len(silent)} 个沉默服务商**（近 {silent_n} 月：{recent[0]}~{recent[-1]}）")
                if not silent.empty:
                    show = silent.copy()
                    show['历史月均'] = show['历史月均'].round(1)
                    show['上次活跃'] = show['上次活跃'].dt.strftime('%Y-%m-%d')
                    st.dataframe(
                        show[['上线客户编码', '上线客户名称', '签约代理商',
                              '历史台数', '历史月均', '上次活跃']],
                        use_container_width=True, hide_index=True, height=400,
                    )
                    excel_download(show, "F10_沉默唤醒", "f10_dl")
            else:
                st.info("数据月份太少，无法判定沉默")

        # ───────── #11 爆发服务商跟进 ─────────
        with st.expander("🔥 #11 爆发服务商跟进 — 当月台数翻倍以上", expanded=False):
            st.caption("逻辑：锚点月台数 ≥ 历史月均 × N → 可能拿到大项目，跟进配套销售")

            multiplier = st.slider("当月 / 历史月均 ≥", 2.0, 10.0, 3.0, 0.5, key="f11_mult")
            min_qty = st.number_input("当月台数 ≥", min_value=1, value=10, key="f11_minqty")

            if anchor and len(available_months) > 1:
                cur_df = rp_df[rp_df['上线年月'] == anchor]
                hist_months = [m for m in available_months if m != anchor]
                hist_df = rp_df[rp_df['上线年月'].isin(hist_months)]

                cur_g = cur_df.groupby(['上线客户编码', '上线客户名称']).agg(
                    当月台数=('产品序列号', 'count'),
                    签约代理商=('所属一级客户',
                                lambda x: x.dropna().mode().iloc[0]
                                if len(x.dropna().mode()) > 0 else None),
                ).reset_index()

                hist_g = hist_df.groupby('上线客户编码').agg(
                    历史月均=('产品序列号', lambda x: len(x) / max(len(hist_months), 1)),
                ).reset_index()

                merged = cur_g.merge(hist_g, on='上线客户编码', how='left').fillna({'历史月均': 0})
                merged['倍数'] = np.where(
                    merged['历史月均'] > 0,
                    merged['当月台数'] / merged['历史月均'],
                    np.inf,
                )
                burst = merged[(merged['当月台数'] >= min_qty)
                              & (merged['倍数'] >= multiplier)]\
                    .sort_values('倍数', ascending=False)

                st.markdown(f"**🎯 找到 {len(burst)} 个爆发服务商（{anchor}）**")
                if not burst.empty:
                    show = burst.copy()
                    show['历史月均'] = show['历史月均'].round(1)
                    show['倍数'] = show['倍数'].apply(
                        lambda v: '∞（新增）' if v == np.inf else f'{v:.1f}×')
                    st.dataframe(
                        show[['上线客户编码', '上线客户名称', '签约代理商',
                              '当月台数', '历史月均', '倍数']],
                        use_container_width=True, hide_index=True, height=400,
                    )
                    excel_download(show, "F11_爆发跟进", "f11_dl")

        # ───────── #12 极速流通工程订单 ─────────
        with st.expander("⚡ #12 极速流通工程订单 — 出库到上线 < N 天", expanded=False):
            st.caption("逻辑：出库即上线 → 大概率是工程项目，跟单配套有机会")

            c1, c2 = st.columns(2)
            with c1:
                fast_days = st.number_input("流通天数 <", min_value=1, value=3, key="f12_days")
            with c2:
                min_count = st.number_input(
                    "服务商累计极速台数 ≥", min_value=1, value=5, key="f12_minc")

            if not main_df.empty:
                main_w = main_df[main_df['上线年月'].isin(window_months)]\
                    if window_months else main_df
                fast = main_w[(main_w['流通天数'] >= 0) & (main_w['流通天数'] < fast_days)]

                # 通过产品序列号关联红包表，拿到真服务商名
                # 红包表的列重命名避免与主表 merge 冲突（主表也有「所属一级客户」列）
                rp_lookup = (rp_df[['产品序列号', '上线客户名称', '所属一级客户']]
                             .drop_duplicates(subset='产品序列号')
                             .rename(columns={'上线客户名称': '_rp_provider',
                                              '所属一级客户': '_rp_signed'}))
                fast = fast.merge(rp_lookup, on='产品序列号', how='left')
                fast['_provider'] = fast['_rp_provider'].fillna(fast['上线自客户名称'])
                # 签约代理商优先用红包表（更准），fallback 主表
                fast['_signed'] = fast['_rp_signed'].fillna(fast['所属一级客户'])

                grp = fast.dropna(subset=['_provider']).groupby('_provider').agg(
                    极速台数=('产品序列号', 'count'),
                    平均流通天数=('流通天数', 'mean'),
                    签约代理商=('_signed',
                                lambda x: x.dropna().mode().iloc[0]
                                if len(x.dropna().mode()) > 0 else None),
                    出库代理商TOP=('出库客户名称',
                                lambda x: x.mode().iloc[0]
                                if len(x.mode()) > 0 else None),
                ).reset_index().rename(columns={'_provider': '服务商'})
                grp = grp[grp['极速台数'] >= min_count]\
                    .sort_values('极速台数', ascending=False)

                st.markdown(f"**🎯 找到 {len(grp)} 个极速流通服务商**")
                if not grp.empty:
                    grp['平均流通天数'] = grp['平均流通天数'].round(1)
                    st.dataframe(grp, use_container_width=True, hide_index=True, height=400)
                    excel_download(grp, "F12_极速流通", "f12_dl")

        # ───────── #13 滞销库存识别 ─────────
        with st.expander("🐢 #13 滞销库存识别 — 流通天数 > N 天的代理商", expanded=False):
            st.caption("逻辑：出库后 N 天才上线 → 该代理商有滞销库存，可帮促销")

            slow_days = st.number_input("流通天数 >", min_value=30, value=180, key="f13_days")

            if not main_df.empty:
                slow = main_df[main_df['流通天数'] > slow_days]
                grp = slow.groupby('出库客户名称').agg(
                    滞销台数=('产品序列号', 'count'),
                    总出货台数=('产品序列号', lambda _: 0),  # 占位后面改
                    平均滞销天数=('流通天数', 'mean'),
                    代表型号=('内部型号',
                              lambda x: x.mode().iloc[0]
                              if len(x.mode()) > 0 else None),
                ).reset_index()

                # 总出货量
                total_g = main_df.groupby('出库客户名称').size().rename('总出货台数')
                grp = grp.drop(columns=['总出货台数'])\
                    .merge(total_g.reset_index(), on='出库客户名称', how='left')
                grp['滞销率'] = grp['滞销台数'] / grp['总出货台数']
                grp = grp.sort_values('滞销台数', ascending=False)

                st.markdown(f"**🎯 找到 {len(grp)} 家代理商有滞销库存**")
                if not grp.empty:
                    grp['平均滞销天数'] = grp['平均滞销天数'].round(0).astype(int)
                    grp['滞销率'] = grp['滞销率'].apply(fmt_pct)
                    show = grp[['出库客户名称', '滞销台数', '总出货台数', '滞销率',
                                '平均滞销天数', '代表型号']].rename(
                        columns={'出库客户名称': '代理商'})
                    st.dataframe(show, use_container_width=True, hide_index=True, height=400)
                    excel_download(show, "F13_滞销库存", "f13_dl")

        # ───────── #14 价格洼地服务商 ─────────
        with st.expander("💰 #14 价格洼地服务商 — 均价远低于同型号全省均值", expanded=False):
            st.caption("逻辑：服务商在同型号上的均价 < 全省同型号均价 ×（1 - X）→ 可能被竞品压价")

            deviation = st.slider("低于全省均价 ≥", 0.05, 0.5, 0.20, 0.05, key="f14_dev")
            min_qty = st.number_input("总台数 ≥", min_value=1, value=10, key="f14_minqty")

            rp_w = in_window(rp_df).copy()
            rp_w = rp_w[rp_w['内部型号'].notna() & (rp_w['产品现有分销价'] > 0)]

            # 全省同型号均价
            model_mean = rp_w.groupby('内部型号')['产品现有分销价'].mean().rename('全省均价')

            # 服务商-型号 均价
            sm = rp_w.groupby(['上线客户编码', '上线客户名称', '内部型号']).agg(
                服务商均价=('产品现有分销价', 'mean'),
                台数=('产品序列号', 'count'),
            ).reset_index().merge(model_mean, on='内部型号', how='left')
            sm['偏离率'] = (sm['服务商均价'] - sm['全省均价']) / sm['全省均价']
            sm = sm[sm['偏离率'] <= -deviation]

            # 聚合到服务商：低价台数加权
            grp = sm.groupby(['上线客户编码', '上线客户名称']).agg(
                低价型号数=('内部型号', 'nunique'),
                低价台数=('台数', 'sum'),
                平均偏离=('偏离率', 'mean'),
            ).reset_index()
            grp = grp[grp['低价台数'] >= min_qty].sort_values('平均偏离', ascending=True)

            st.markdown(f"**🎯 找到 {len(grp)} 个价格洼地服务商**")
            if not grp.empty:
                show = grp.copy()
                show['平均偏离'] = show['平均偏离'].apply(fmt_pct)
                st.dataframe(show, use_container_width=True, hide_index=True, height=400)
                excel_download(show, "F14_价格洼地", "f14_dl")

        # ───────── #15 集中爆发日 ─────────
        with st.expander("📅 #15 集中爆发日 — 单日上线 ≥ N 台（疑似工程订单）", expanded=False):
            st.caption("逻辑：服务商单日累计上线 ≥ N 台 → 大概率工程，跟项目机会")

            burst_qty = st.number_input("单日台数 ≥", min_value=5, value=10, key="f15_qty")

            rp_w = in_window(rp_df).copy()
            rp_w = rp_w[rp_w['上线时间'].notna()]
            rp_w['_date'] = rp_w['上线时间'].dt.date

            daily = rp_w.groupby(['_date', '上线客户编码', '上线客户名称']).agg(
                当日台数=('产品序列号', 'count'),
                代表型号=('内部型号',
                          lambda x: x.mode().iloc[0]
                          if len(x.mode()) > 0 else None),
                签约代理商=('所属一级客户',
                            lambda x: x.dropna().mode().iloc[0]
                            if len(x.dropna().mode()) > 0 else None),
            ).reset_index()
            burst = daily[daily['当日台数'] >= burst_qty]\
                .sort_values(['当日台数', '_date'], ascending=[False, False])

            st.markdown(f"**🎯 找到 {len(burst)} 个集中爆发记录**")
            if not burst.empty:
                show = burst.rename(columns={'_date': '爆发日期'})
                show['爆发日期'] = show['爆发日期'].astype(str)
                st.dataframe(show, use_container_width=True, hide_index=True, height=400)
                excel_download(show, "F15_集中爆发日", "f15_dl")


# ════════════════════════════════════════════════════════════════════
# Tab D：地理碰撞 #16 #17 #18
# ════════════════════════════════════════════════════════════════════

        # ===== 恢复 rp_df / main_df，避免污染下一个 Tab =====
        rp_df, main_df = _saved_rp, _saved_main
with tab_d:
    if rp_df.empty:
        st.warning("此 Tab 依赖『安装红包记录』表，请先到主页上传。")
    else:
        # ===== 本 Tab 局部城市筛选 =====
        _ci_opts = sorted(rp_df['安装城市'].dropna().astype(str).str.strip().unique().tolist())
        _ci_opts = [c for c in _ci_opts if c]
        _picked_local_ci = st.multiselect(
            "🏙️ 本 Tab 城市筛选（叠加在侧栏全局上，仅影响本 Tab）",
            options=_ci_opts, default=[], key="tab_d_local_cities",
        )
        _saved_rp, _saved_main = rp_df, main_df
        if _picked_local_ci:
            rp_df = rp_df[rp_df['安装城市'].astype(str).str.strip().isin(_picked_local_ci)]
            if not main_df.empty:
                main_df = main_df[main_df['上线城市'].astype(str).str.strip().isin(_picked_local_ci)]
        # ===== 局部筛选 完毕 =====

        # ───────── #16 区县产品空白 ─────────
        with st.expander("🗺️ #16 区县产品空白 — 同地市其他区县都有，唯独某区县 0 台", expanded=True):
            st.caption("逻辑：同地市内的其他区县都上过该产品系列，唯独这个区县没上 → 销售空白")

            series_options = sorted(rp_df['产品系列'].dropna().unique().tolist())
            picked_series = st.multiselect(
                "看哪些产品系列的空白",
                options=series_options,
                default=[s for s in series_options if 'NVR' in s.upper()][:3],
                key="f16_series",
            )

            if picked_series:
                rp_w = in_window(rp_df).copy()
                rp_w = rp_w[rp_w['产品系列'].isin(picked_series)
                            & rp_w['安装城市'].notna()
                            & rp_w['安装区县'].notna()]

                # 列出每个 (城市, 区县, 系列) 的台数
                qty = rp_w.groupby(['安装城市', '安装区县', '产品系列']).size()\
                    .reset_index(name='台数')

                # 全部可能的 (城市, 区县) 对
                all_districts = (rp_df.dropna(subset=['安装城市', '安装区县'])
                                 [['安装城市', '安装区县']].drop_duplicates())

                results = []
                for series in picked_series:
                    series_data = qty[qty['产品系列'] == series]
                    cities_with_series = set(series_data['安装城市'].unique())
                    # 只看至少有一个区县出过该系列的城市
                    for city in cities_with_series:
                        # 该城市内出过该系列的区县
                        active = set(series_data[series_data['安装城市'] == city]['安装区县'])
                        # 该城市所有区县
                        all_in_city = set(
                            all_districts[all_districts['安装城市'] == city]['安装区县'])
                        blank = all_in_city - active
                        for district in blank:
                            results.append({
                                '城市': city,
                                '区县': district,
                                '产品系列': series,
                                '该城市其他活跃区县数': len(active),
                            })

                if results:
                    blank_df = pd.DataFrame(results).sort_values(
                        ['产品系列', '城市', '该城市其他活跃区县数'],
                        ascending=[True, True, False],
                    )
                    st.markdown(f"**🎯 找到 {len(blank_df)} 处区县-产品空白**")
                    st.dataframe(blank_df, use_container_width=True, hide_index=True, height=400)
                    excel_download(blank_df, "F16_区县空白", "f16_dl")
                else:
                    st.success("没有发现空白（很完美）")

        # ───────── #17 跨省套利识别 ─────────
        with st.expander("🚚 #17 跨省套利识别 — 服务商地市 ≠ 安装地 ≠ 出货代理商地", expanded=False):
            st.caption("逻辑：三者都不一致 → 设备绕了远路，可能是串货/跨省接活")

            rp_w = in_window(rp_df).copy()
            rp_w = rp_w[rp_w['上线客户地市'].notna()
                        & rp_w['安装城市'].notna()
                        & rp_w['出货客户城市'].notna()]
            a = rp_w['上线客户地市'].astype(str).str.strip()
            b = rp_w['安装城市'].astype(str).str.strip()
            c = rp_w['出货客户城市'].astype(str).str.strip()
            mask = (a != b) & (b != c) & (a != c)
            arb = rp_w[mask]

            grp = arb.groupby(['上线客户编码', '上线客户名称']).agg(
                服务商地市=('上线客户地市',
                            lambda x: x.mode().iloc[0]
                            if len(x.mode()) > 0 else None),
                台数=('产品序列号', 'count'),
                金额=('产品现有分销价', 'sum'),
                安装地市集合=('安装城市',
                            lambda x: '、'.join(sorted(set(x.dropna()))[:3])),
                出货代理商集合=('出货客户名称',
                                lambda x: '、'.join(sorted(set(x.dropna()))[:3])),
            ).reset_index().sort_values('台数', ascending=False)

            st.markdown(f"**🎯 找到 {len(grp)} 个跨省套利嫌疑服务商**")
            if not grp.empty:
                grp['金额'] = grp['金额'].round(0).astype(int)
                st.dataframe(grp, use_container_width=True, hide_index=True, height=400)
                excel_download(grp, "F17_跨省套利", "f17_dl")

        # ───────── #18 区县代理商集中度 ─────────
        with st.expander("🏛️ #18 区县代理商集中度 — 垄断 vs 竞争", expanded=False):
            st.caption("逻辑：单一代理商占某区县出货 ≥ X% = 垄断；< Y% = 高度竞争")

            # 局部筛选：省份 / 地市（聚焦看某省/市的区县）
            province_options = sorted(
                rp_df['安装省份'].dropna().astype(str).str.strip().unique().tolist()
            ) if '安装省份' in rp_df.columns else []
            city_options_18 = sorted(
                rp_df['安装城市'].dropna().astype(str).str.strip().unique().tolist()
            )

            f1, f2 = st.columns(2)
            with f1:
                f18_provinces = st.multiselect(
                    "省份筛选",
                    options=province_options,
                    default=[],
                    key="f18_prov",
                    help=f"不选 = 全部，共 {len(province_options)} 个省份",
                )
            with f2:
                # 如果选了省份，地市候选限制在这些省份内
                if f18_provinces and '安装省份' in rp_df.columns:
                    city_options_18 = sorted(
                        rp_df[rp_df['安装省份'].astype(str).str.strip().isin(f18_provinces)]
                        ['安装城市'].dropna().astype(str).str.strip().unique().tolist()
                    )
                f18_cities = st.multiselect(
                    "地市筛选",
                    options=city_options_18,
                    default=[],
                    key="f18_city",
                    help=f"不选 = 全部，共 {len(city_options_18)} 个地市",
                )

            c1, c2 = st.columns(2)
            with c1:
                mono_pct = st.slider("垄断阈值（Top1 占比 ≥）", 0.5, 1.0, 0.8, 0.05, key="f18_mono")
            with c2:
                min_qty = st.number_input("区县总台数 ≥", min_value=1, value=20, key="f18_min")

            rp_w = in_window(rp_df).copy()
            rp_w = rp_w[rp_w['出货客户名称'].notna()
                        & rp_w['安装城市'].notna()
                        & rp_w['安装区县'].notna()]

            # 应用局部筛选
            if f18_provinces and '安装省份' in rp_w.columns:
                rp_w = rp_w[rp_w['安装省份'].astype(str).str.strip().isin(f18_provinces)]
            if f18_cities:
                rp_w = rp_w[rp_w['安装城市'].astype(str).str.strip().isin(f18_cities)]

            grp = rp_w.groupby(['安装城市', '安装区县']).apply(
                lambda x: pd.Series({
                    '总台数': len(x),
                    '代理商数': x['出货客户名称'].nunique(),
                    'Top1代理商': x['出货客户名称'].value_counts().index[0],
                    'Top1台数': x['出货客户名称'].value_counts().iloc[0],
                })
            ).reset_index()
            grp = grp[grp['总台数'] >= min_qty].copy()
            grp['Top1占比'] = grp['Top1台数'] / grp['总台数']
            grp['状态'] = grp['Top1占比'].apply(
                lambda v: '🏛️ 垄断' if v >= mono_pct
                else '⚖️ 竞争（> 3 家）' if v < 0.5 else '◐ 寡头'
            )
            grp = grp.sort_values('Top1占比', ascending=False)

            st.markdown(f"**🎯 共 {len(grp)} 个区县**（垄断 {(grp['Top1占比'] >= mono_pct).sum()} / "
                        f"寡头 {((grp['Top1占比'] >= 0.5) & (grp['Top1占比'] < mono_pct)).sum()} / "
                        f"竞争 {(grp['Top1占比'] < 0.5).sum()}）")
            if not grp.empty:
                show = grp.copy()
                show['Top1占比'] = show['Top1占比'].apply(fmt_pct)
                show['总台数'] = show['总台数'].astype(int)
                show['Top1台数'] = show['Top1台数'].astype(int)
                show['代理商数'] = show['代理商数'].astype(int)
                st.dataframe(
                    show[['安装城市', '安装区县', '状态', '总台数', '代理商数',
                          'Top1代理商', 'Top1台数', 'Top1占比']],
                    use_container_width=True, hide_index=True, height=400,
                )
                excel_download(show, "F18_集中度", "f18_dl")


# ════════════════════════════════════════════════════════════════════
# Tab E：红包激活 #19 #20 #21
# ════════════════════════════════════════════════════════════════════

        # ===== 恢复 rp_df / main_df，避免污染下一个 Tab =====
        rp_df, main_df = _saved_rp, _saved_main
with tab_e:
    if rp_df.empty:
        st.warning("此 Tab 依赖『安装红包记录』表，请先到主页上传。")
    else:
        # ===== 本 Tab 局部城市筛选 =====
        _ci_opts = sorted(rp_df['安装城市'].dropna().astype(str).str.strip().unique().tolist())
        _ci_opts = [c for c in _ci_opts if c]
        _picked_local_ci = st.multiselect(
            "🏙️ 本 Tab 城市筛选（叠加在侧栏全局上，仅影响本 Tab）",
            options=_ci_opts, default=[], key="tab_e_local_cities",
        )
        _saved_rp, _saved_main = rp_df, main_df
        if _picked_local_ci:
            rp_df = rp_df[rp_df['安装城市'].astype(str).str.strip().isin(_picked_local_ci)]
            if not main_df.empty:
                main_df = main_df[main_df['上线城市'].astype(str).str.strip().isin(_picked_local_ci)]
        # ===== 局部筛选 完毕 =====

        # ───────── #19 抽奖运气最差 ─────────
        with st.expander("🎰 #19 抽奖运气最差 — 装得多但中奖少", expanded=True):
            st.caption("逻辑：服务商抽奖 ≥ N 次但中奖率（中奖金额 > 0 占比）很低 → 工人体验差")

            min_draws = st.number_input("抽奖次数 ≥", min_value=5, value=20, key="f19_min")

            rp_w = in_window(rp_df).copy()
            if '是否抽奖' in rp_w.columns:
                rp_w = rp_w[rp_w['是否抽奖'] == 'Y']
                rp_w['_win'] = rp_w['中奖金额'] > 0

                grp = rp_w.groupby(['上线客户编码', '上线客户名称']).agg(
                    抽奖次数=('产品序列号', 'count'),
                    中奖次数=('_win', 'sum'),
                    累计中奖金额=('中奖金额', 'sum'),
                    签约代理商=('所属一级客户',
                                lambda x: x.dropna().mode().iloc[0]
                                if len(x.dropna().mode()) > 0 else None),
                ).reset_index()
                grp['中奖率'] = grp['中奖次数'] / grp['抽奖次数']
                grp = grp[grp['抽奖次数'] >= min_draws]\
                    .sort_values(['中奖率', '抽奖次数'], ascending=[True, False])

                st.markdown(f"**🎯 找到 {len(grp)} 个抽奖次数充足的服务商**（默认低中奖率排前）")
                if not grp.empty:
                    show = grp.copy()
                    show['中奖率'] = show['中奖率'].apply(fmt_pct)
                    show['累计中奖金额'] = show['累计中奖金额'].round(0).astype(int)
                    st.dataframe(show, use_container_width=True, hide_index=True, height=400)
                    excel_download(show, "F19_抽奖运气", "f19_dl")
            else:
                st.info("红包表里没有『是否抽奖』字段")

        # ───────── #20 抽奖触发率异常 ─────────
        with st.expander("⚠️ #20 抽奖触发率异常 — 远高于均值（刷单嫌疑）", expanded=False):
            st.caption("逻辑：服务商的抽奖率（是否抽奖 = Y 比例）显著高于全省 → 可能刷单")

            sigma = st.slider("超过均值 + N 个标准差", 1.0, 5.0, 3.0, 0.5, key="f20_sigma")
            min_qty = st.number_input("总台数 ≥", min_value=1, value=20, key="f20_min")

            rp_w = in_window(rp_df).copy()
            if '是否抽奖' in rp_w.columns:
                rp_w['_drew'] = rp_w['是否抽奖'] == 'Y'

                grp = rp_w.groupby(['上线客户编码', '上线客户名称']).agg(
                    总台数=('产品序列号', 'count'),
                    抽奖次数=('_drew', 'sum'),
                    签约代理商=('所属一级客户',
                                lambda x: x.dropna().mode().iloc[0]
                                if len(x.dropna().mode()) > 0 else None),
                ).reset_index()
                grp['抽奖率'] = grp['抽奖次数'] / grp['总台数']
                grp = grp[grp['总台数'] >= min_qty]

                if not grp.empty:
                    mean = grp['抽奖率'].mean()
                    std = grp['抽奖率'].std()
                    threshold = mean + sigma * std
                    abn = grp[grp['抽奖率'] > threshold]\
                        .sort_values('抽奖率', ascending=False)

                    st.markdown(f"**🎯 全省均值 {mean * 100:.1f}% ± {std * 100:.1f}%；"
                                f"阈值 {threshold * 100:.1f}%；"
                                f"找到 {len(abn)} 个异常服务商**")
                    if not abn.empty:
                        show = abn.copy()
                        show['抽奖率'] = show['抽奖率'].apply(fmt_pct)
                        st.dataframe(show, use_container_width=True, hide_index=True, height=400)
                        excel_download(show, "F20_抽奖触发异常", "f20_dl")

        # ───────── #21 异地销售排行 ─────────
        with st.expander("🌐 #21 异地销售排行 — 异省/异城/异县占比高", expanded=False):
            st.caption("逻辑：主表的『是否异省 = Y』比例越高，说明设备走得越远（跨地区接单）")

            min_qty = st.number_input("总台数 ≥", min_value=1, value=20, key="f21_min")
            top_dim = st.radio("查看维度", options=['服务商', '代理商'], horizontal=True, key="f21_dim")

            if not main_df.empty and {'是否异省', '是否异城', '是否异县'}.issubset(main_df.columns):
                m_w = main_df[main_df['上线年月'].isin(window_months)]\
                    if window_months else main_df

                # 用红包表的真服务商名（如果有）
                if top_dim == '服务商':
                    # 重命名避免和主表的「所属一级客户」列冲突
                    rp_lookup = (rp_df[['产品序列号', '上线客户名称']]
                                 .drop_duplicates(subset='产品序列号')
                                 .rename(columns={'上线客户名称': '_rp_provider'}))
                    m_w = m_w.merge(rp_lookup, on='产品序列号', how='left')
                    m_w['_key'] = m_w['_rp_provider'].fillna(m_w['上线自客户名称'])
                    group_key = '_key'
                else:
                    group_key = '出库客户名称'
                    m_w = m_w.dropna(subset=[group_key])

                m_w = m_w.dropna(subset=[group_key])

                grp = m_w.groupby(group_key).agg(
                    总台数=('产品序列号', 'count'),
                    异省=('是否异省', lambda x: (x == 'Y').sum()),
                    异城=('是否异城', lambda x: (x == 'Y').sum()),
                    异县=('是否异县', lambda x: (x == 'Y').sum()),
                ).reset_index()
                grp['异省率'] = grp['异省'] / grp['总台数']
                grp = grp[grp['总台数'] >= min_qty]\
                    .sort_values('异省率', ascending=False)

                st.markdown(f"**🎯 找到 {len(grp)} 个 {top_dim}**")
                if not grp.empty:
                    show = grp.rename(columns={group_key: top_dim}).copy()
                    show['异省率'] = show['异省率'].apply(fmt_pct)
                    show['总台数'] = show['总台数'].astype(int)
                    show['异省'] = show['异省'].astype(int)
                    show['异城'] = show['异城'].astype(int)
                    show['异县'] = show['异县'].astype(int)
                    st.dataframe(show, use_container_width=True, hide_index=True, height=400)
                    excel_download(show, "F21_异地销售", "f21_dl")


        # ===== 恢复 rp_df / main_df，避免污染下一个 Tab =====
        rp_df, main_df = _saved_rp, _saved_main