#!/usr/bin/env python3
"""
SO 环比分析看板
- 选定基准月 / 对比月
- 5 个 Tab 覆盖：总览、区县下降、产品系列下降、代理商/服务商、日期分布
"""

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# 引入共享加载（跨 page 共享缓存）
sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import load_main_shared, load_redpack_shared, DB_PATH  # noqa: E402

TABLE = "product_flow"
REDPACK_TABLE = "install_redpack"

# ── 维度配置（5 个分析维度，标注每个维度的数据口径）──────────
# 「全量感知」= product_flow 主表（市场总量，含异省/异市货来本地上线的，服务商关联可能不准）
# 「安装红包」= install_redpack 表（服务商账号绑定，销售链路明确，但不含未绑定的散户/项目直销）
DIM_CONFIG = {
    '城市（全量感知）':    ('main',    '上线城市'),
    '区县（全量感知）':    ('main',    '上线区县_全'),
    '产品系列（全量感知）': ('main',    '产品系列_有效'),
    '代理商出库（全量感知）':  ('main',    '出库客户名称'),
    '服务商（安装红包）':  ('redpack', '上线客户名称'),
}

require_auth()
st.markdown("### 📊 SO 月度对比分析（任意两月对比，不限于环比）")
st.caption(
    "💡 **双口径**：本页同时呈现「**全量感知**」（产品流向主表，市场总量）和「**安装红包**」（服务商账号绑定）两组数据。"
    "前者看市场销量容量，后者看服务商网络效能。**绑定率** = 红包货值 ÷ 全量货值。"
)
st.caption(
    "**金额口径**：全量感知用「最新分销价」，安装红包用「产品现有分销价」。两表实际值通常接近但不完全相同。"
)
st.markdown("""
<style>
    .stSubheader { margin-top: 5px; margin-bottom: 5px; font-size: 1.1rem; }
    [data-testid="stCaption"] { margin-bottom: -8px; font-size: 0.85rem; }
    .stDataFrame { margin-bottom: 5px; }
    .stDataFrame td { text-align: left !important; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────

def load_data() -> pd.DataFrame:
    """共享 loader 包装"""
    return load_main_shared()


def load_redpack() -> pd.DataFrame:
    """共享 loader 包装"""
    df = load_redpack_shared()
    if df.empty:
        return df
    # 兼容老代码：page 2 用的列名是「上线区县_全」（基于安装城市/区县）；
    # 共享 loader 派生的是「安装区县_全」，这里加个别名
    if '上线区县_全' not in df.columns and '安装区县_全' in df.columns:
        df = df.assign(**{'上线区县_全': df['安装区县_全']})
    return df


def filter_clean(df: pd.DataFrame, exclude_tags: list, drop_abnormal: bool) -> pd.DataFrame:
    """
    数据剔除字段是分类标签（其他/电商/4G/电商-4G），不是是否值。
    exclude_tags：用户在侧栏选中要剔除的标签
    drop_abnormal：是否过滤『客户行为异常 = Y』
    """
    out = df
    if exclude_tags and '数据剔除' in out.columns:
        out = out[~out['数据剔除'].fillna('').astype(str).str.strip().isin(exclude_tags)]
    if drop_abnormal and '客户行为异常' in out.columns:
        out = out[out['客户行为异常'].fillna('').astype(str).str.strip().str.upper() != 'Y']
    return out


# ──────────────────────────────────────────
# 数据导入（参照产品流向分析）
# ──────────────────────────────────────────

def sanitize_for_sqlite(df: pd.DataFrame) -> pd.DataFrame:
    """
    将 pandas 扩展类型（Int64、StringDtype、boolean 等）转为 SQLite 兼容的基础类型。
    这些类型用 pd.NA 而非 np.nan，SQLite 绑定参数时会报 'unsupported type'。
    """
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_extension_array_dtype(df[col].dtype):
            df[col] = df[col].astype(object).where(df[col].notna(), None)
    return df


def import_excel_to_db(uploaded_file, mode: str = 'merge') -> dict:
    """
    将上传的 Excel 文件导入到 SQLite 数据库

    支持三种模式：
    - merge:   合并模式，根据 ID 匹配，重合的覆盖（INSERT OR REPLACE），新的追加
    - append:  追加模式，已存在 ID 跳过（INSERT OR IGNORE）
    - replace: 覆盖模式，先清空表再导入

    返回：{'inserted': N, 'updated': M, 'total': T}
    """
    df_new = pd.read_excel(uploaded_file)
    df_new.columns = df_new.columns.str.strip()

    if 'ID' not in df_new.columns:
        raise ValueError("Excel 中没有 'ID' 列，无法导入")

    df_new['ID'] = df_new['ID'].astype(str).str.strip()

    # 去重：以 ID 为唯一键，保留最后一条
    dup = len(df_new) - df_new['ID'].nunique()
    if dup > 0:
        df_new = df_new.drop_duplicates(subset='ID', keep='last').reset_index(drop=True)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{TABLE}'")
    table_exists = cur.fetchone() is not None

    inserted = updated = 0

    if not table_exists or mode == 'replace':
        df_write = sanitize_for_sqlite(df_new)
        df_write.to_sql(TABLE, conn, if_exists='replace', index=False)
        inserted = len(df_write)
    else:
        # 对齐表结构：Excel 中新增的列也要加到表里
        cur.execute(f"PRAGMA table_info({TABLE})")
        db_cols = [r[1] for r in cur.fetchall()]
        new_cols = [c for c in df_new.columns if c not in db_cols]
        for c in new_cols:
            cur.execute(f'ALTER TABLE {TABLE} ADD COLUMN "{c}"')

        # 算 inserted / updated
        existing_ids = set(
            r[0] for r in cur.execute(f'SELECT "ID" FROM {TABLE}').fetchall()
        )
        new_rows = df_new[~df_new['ID'].isin(existing_ids)]
        update_rows = df_new[df_new['ID'].isin(existing_ids)]
        inserted = len(new_rows)
        updated = len(update_rows) if mode == 'merge' else 0

        df_aligned = sanitize_for_sqlite(df_new)
        df_aligned.to_sql('_tmp_import', conn, if_exists='replace', index=False)

        cols_sql = ', '.join(f'"{c}"' for c in df_aligned.columns)
        verb = 'INSERT OR REPLACE' if mode == 'merge' else 'INSERT OR IGNORE'
        cur.execute(f"""
            {verb} INTO {TABLE} ({cols_sql})
            SELECT {cols_sql} FROM _tmp_import
        """)
        cur.execute("DROP TABLE _tmp_import")

    # 索引（首次或已存在都安全）
    cur.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS idx_id ON {TABLE}("ID")')
    cur.execute(f'CREATE INDEX IF NOT EXISTS idx_online_time ON {TABLE}("上线时间")')
    cur.execute(f'CREATE INDEX IF NOT EXISTS idx_district ON {TABLE}("上线区县")')
    cur.execute(f'CREATE INDEX IF NOT EXISTS idx_dealer ON {TABLE}("出库客户名称")')
    cur.execute(f'CREATE INDEX IF NOT EXISTS idx_provider ON {TABLE}("上线自客户名称")')

    conn.commit()
    total = cur.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    conn.close()

    # 清缓存让 load_data 重新读
    load_data.clear()

    return {
        'inserted': inserted,
        'updated': updated,
        'total': total,
        'duplicates_in_file': dup,
    }


# ──────────────────────────────────────────
# 通用计算：维度环比
# ──────────────────────────────────────────

def agg_by(df: pd.DataFrame, dim_col: str,
            count_col: str = 'ID', amount_col: str = 'KPI金额') -> pd.DataFrame:
    """聚合：维度 → 台数 + 金额（按 count_col 计数，按 amount_col 求和金额）

    ⚠️ 默认金额列改为「KPI金额」(_loaders 派生：优先红包表「产品现有分销价」，fallback「最新分销价」)
    与公司 KPI 标准（按累计上线货值定级）一致
    """
    if amount_col not in df.columns:
        amount_col = '最新分销价'  # fallback
    g = df.groupby(dim_col, dropna=False).agg(
        台数=(count_col, 'count'),
        金额=(amount_col, 'sum'),
    ).reset_index()
    return g


def mom_compare(df_base: pd.DataFrame, df_curr: pd.DataFrame, dim_col: str,
                count_col: str = 'ID', amount_col: str = 'KPI金额') -> pd.DataFrame:
    """环比对比：维度 / 基准台数 / 当月台数 / 台数变化 / 台数变化率 / 金额同理"""
    a = agg_by(df_base, dim_col, count_col, amount_col).rename(
        columns={'台数': '基准台数', '金额': '基准金额'})
    b = agg_by(df_curr, dim_col, count_col, amount_col).rename(
        columns={'台数': '当月台数', '金额': '当月金额'})
    m = pd.merge(a, b, on=dim_col, how='outer').fillna(0)

    m['台数变化'] = m['当月台数'] - m['基准台数']
    m['金额变化'] = m['当月金额'] - m['基准金额']
    m['台数变化率'] = np.where(m['基准台数'] > 0, m['台数变化'] / m['基准台数'], np.nan)
    m['金额变化率'] = np.where(m['基准金额'] > 0, m['金额变化'] / m['基准金额'], np.nan)
    return m


def fmt_pct(v):
    if pd.isna(v):
        return '—'
    return f'{v * 100:+.1f}%'


def fmt_money(v):
    if pd.isna(v):
        return '—'
    return f'{v:,.0f}'


def style_change_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if '基准金额' in out.columns:
        out['基准金额'] = out['基准金额'].map(fmt_money)
    if '当月金额' in out.columns:
        out['当月金额'] = out['当月金额'].map(fmt_money)
    if '金额变化' in out.columns:
        out['金额变化'] = out['金额变化'].map(fmt_money)
    if '台数变化率' in out.columns:
        out['台数变化率'] = out['台数变化率'].map(fmt_pct)
    if '金额变化率' in out.columns:
        out['金额变化率'] = out['金额变化率'].map(fmt_pct)
    return out


# ──────────────────────────────────────────
# UI
# ──────────────────────────────────────────

def render_import_section():
    """折叠的数据导入区，参照产品流向分析的 UI"""
    db_exists = DB_PATH.exists()
    default_open = not db_exists  # 数据库还没建好时默认展开

    with st.expander("📥 数据导入（追加新月份 Excel）", expanded=default_open):
        uploaded = st.file_uploader(
            "选择 Excel 文件（.xlsx / .xls）",
            type=['xlsx', 'xls'],
            key="so_uploader",
        )
        if uploaded:
            mode = st.radio(
                "导入模式",
                options=['merge', 'append', 'replace'],
                format_func=lambda x: {
                    'merge':   '🔀 合并（ID 重合则覆盖，新行追加）— 推荐',
                    'append':  '➕ 追加（已存在的 ID 跳过，只加新行）',
                    'replace': '🔄 覆盖（先清空表，再导入）',
                }[x],
                index=0,
                horizontal=False,
            )
            if st.button("💾 开始导入", type="primary", key="so_import_btn"):
                with st.spinner("正在导入…大文件可能需要 30~60 秒"):
                    try:
                        r = import_excel_to_db(uploaded, mode=mode)
                        msg = (
                            f"✅ 导入成功："
                            f"新增 {r['inserted']:,} 行，"
                            f"更新 {r['updated']:,} 行，"
                            f"数据库现有 {r['total']:,} 行"
                        )
                        if r['duplicates_in_file'] > 0:
                            msg += f"（Excel 内 {r['duplicates_in_file']} 条 ID 重复，已保留最后一条）"
                        st.success(msg)
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ 导入失败：{e}")


def main():
    df_all = load_data()
    df_redpack_all = load_redpack()  # 可能为空（红包表未导入）

    if df_all.empty:
        st.warning("数据库中没有数据，请先到主页『📥 数据导入』上传 Excel 文件")
        try:
            st.page_link("app.py", label="↩ 返回主页导入数据", icon="📥")
        except Exception:
            pass
        st.stop()

    # ── 侧栏：月份选择 + 数据剔除开关 ──────────────────
    with st.sidebar:
        st.header("筛选")

        all_months = sorted(df_all['上线年月'].dropna().unique())
        if len(all_months) < 2:
            st.warning("数据库中只有不到 2 个月的数据，无法做环比")
            st.stop()

        default_curr = all_months[-1]
        default_base = all_months[-2]

        base_month = st.selectbox("基准月（上月）", all_months,
                                   index=all_months.index(default_base))
        curr_month = st.selectbox("对比月（当月）", all_months,
                                   index=all_months.index(default_curr))

        st.divider()
        st.caption("数据剔除规则")

        # 数据剔除：分类标签，让用户多选要剔除的
        if '数据剔除' in df_all.columns:
            tag_counts = (
                df_all['数据剔除'].fillna('(空)').astype(str).str.strip()
                .value_counts()
            )
            tag_options = [t for t in tag_counts.index if t != '其他' and t != '(空)']
            tag_help = "  ".join(f"{t}({tag_counts[t]:,})" for t in tag_counts.index)
            exclude_tags = st.multiselect(
                "剔除以下『数据剔除』标签",
                options=tag_options,
                default=tag_options,  # 默认剔除非"其他"的所有标签
                help=f"当前分布：{tag_help}",
            )
        else:
            exclude_tags = []

        drop_abnormal = st.checkbox("过滤『客户行为异常 = Y』", value=True)

        st.divider()
        st.caption(f"数据总行数：{len(df_all):,}")
        st.caption(f"覆盖月份：{all_months[0]} ~ {all_months[-1]}")

    # 过滤（主表）
    df = filter_clean(df_all, exclude_tags, drop_abnormal)
    df_base = df[df['上线年月'] == base_month]
    df_curr = df[df['上线年月'] == curr_month]

    if df_base.empty or df_curr.empty:
        st.warning("基准月或对比月没有数据，请重新选择")
        st.stop()

    # 红包表月份切片（不应用主表的剔除规则，红包表没有这两列）
    if not df_redpack_all.empty:
        rp_base = df_redpack_all[df_redpack_all['上线年月'] == base_month]
        rp_curr = df_redpack_all[df_redpack_all['上线年月'] == curr_month]
    else:
        rp_base = pd.DataFrame()
        rp_curr = pd.DataFrame()

    # ── 异常数据明细（始终展示，不论是否勾选过滤）──────────
    if '客户行为异常' in df_all.columns:
        df_anomaly = df_all[
            df_all['客户行为异常'].fillna('').astype(str).str.strip().str.upper() == 'Y'
        ]
        df_anomaly = df_anomaly[df_anomaly['上线年月'].isin([base_month, curr_month])]
        n_anom = len(df_anomaly)

        if n_anom > 0:
            badge = "✅" if drop_abnormal else "⚠️"
            status = "已剔除" if drop_abnormal else "**未剔除**（已计入下方所有指标）"

            # 通过产品序列号关联红包表，取服务商（上线客户名称）
            df_anom_show = df_anomaly.copy()
            if not df_redpack_all.empty and '产品序列号' in df_anom_show.columns:
                rp_lookup = (
                    df_redpack_all[['产品序列号', '上线客户名称']]
                    .drop_duplicates(subset='产品序列号')
                    .rename(columns={'上线客户名称': '服务商(红包表)'})
                )
                df_anom_show = df_anom_show.merge(rp_lookup, on='产品序列号', how='left')

            with st.expander(
                f"{badge} 客户行为异常明细：基准月+对比月共 {n_anom} 条（{status}）",
                expanded=False,
            ):
                cols_show = [c for c in [
                    '上线年月', 'ID', '产品序列号', '出库客户名称', '上线城市', '上线区县',
                    '服务商(红包表)', '内部型号', '产品系列_有效',
                    '数据剔除', '上线时间', '最新分销价',
                ] if c in df_anom_show.columns]
                st.dataframe(
                    df_anom_show[cols_show].sort_values('上线时间', na_position='last'),
                    use_container_width=True,
                    hide_index=True,
                )

    # ── Tab ──────────────────────────────────────────
    tab_overview, tab_district, tab_series, tab_dealer, tab_daily = st.tabs([
        "总览", "区县下降", "产品系列下降", "代理商 / 服务商", "日期分布",
    ])

    # ════════════════════ Tab 1：总览 ════════════════════
    with tab_overview:
        st.subheader(f"{base_month} → {curr_month} 环比总览")

        # ── 页面级筛选：上线城市 / 出货客户（代理商）──────────
        # 取两个月并集作为候选项，按当前总台数倒序
        df_two_months = pd.concat([df_base, df_curr], ignore_index=True)
        city_options = (
            df_two_months['上线城市'].dropna().astype(str).str.strip()
            .replace('', pd.NA).dropna()
            .value_counts().index.tolist()
        )
        dealer_options = (
            df_two_months['出库客户名称'].dropna().astype(str).str.strip()
            .replace('', pd.NA).dropna()
            .value_counts().index.tolist()
        )

        f1, f2 = st.columns(2)
        with f1:
            picked_cities = st.multiselect(
                "上线城市筛选（不选 = 全部）",
                options=city_options,
                default=[],
                key="overview_cities",
                help=f"两个月共 {len(city_options)} 个城市",
            )
        with f2:
            picked_dealers = st.multiselect(
                "出货客户筛选（不选 = 全部）",
                options=dealer_options,
                default=[],
                key="overview_dealers",
                help=f"两个月共 {len(dealer_options)} 个出货客户",
            )

        # 应用筛选（同时作用于主表 + 红包表）
        df_base_f, df_curr_f = df_base, df_curr
        rp_base_f, rp_curr_f = rp_base, rp_curr

        if picked_cities:
            df_base_f = df_base_f[df_base_f['上线城市'].astype(str).str.strip().isin(picked_cities)]
            df_curr_f = df_curr_f[df_curr_f['上线城市'].astype(str).str.strip().isin(picked_cities)]
            # 红包表对应字段是「安装城市」（设备实际落地城市）
            if not rp_base_f.empty:
                rp_base_f = rp_base_f[rp_base_f['安装城市'].astype(str).str.strip().isin(picked_cities)]
                rp_curr_f = rp_curr_f[rp_curr_f['安装城市'].astype(str).str.strip().isin(picked_cities)]
        if picked_dealers:
            df_base_f = df_base_f[df_base_f['出库客户名称'].astype(str).str.strip().isin(picked_dealers)]
            df_curr_f = df_curr_f[df_curr_f['出库客户名称'].astype(str).str.strip().isin(picked_dealers)]
            # 红包表对应的代理商字段叫"出货客户名称"
            if not rp_base_f.empty:
                rp_base_f = rp_base_f[rp_base_f['出货客户名称'].astype(str).str.strip().isin(picked_dealers)]
                rp_curr_f = rp_curr_f[rp_curr_f['出货客户名称'].astype(str).str.strip().isin(picked_dealers)]

        if picked_cities or picked_dealers:
            st.caption(
                f"已筛选：{len(picked_cities)} 城市 / {len(picked_dealers)} 出货客户 "
                f"→ 基准月 {len(df_base_f):,} 行，对比月 {len(df_curr_f):,} 行"
            )

        # ═══ 双口径 KPI ═══
        # 主表（全量感知）：用「最新分销价」，体现市场销售总量
        amt_col_main = '最新分销价'
        main_base_n, main_curr_n = len(df_base_f), len(df_curr_f)
        main_base_amt = df_base_f[amt_col_main].sum() if amt_col_main in df_base_f.columns else 0
        main_curr_amt = df_curr_f[amt_col_main].sum() if amt_col_main in df_curr_f.columns else 0

        # 红包表（安装红包）：用「产品现有分销价」，体现服务商绑定销量
        rp_base_n = len(rp_base_f) if not rp_base_f.empty else 0
        rp_curr_n = len(rp_curr_f) if not rp_curr_f.empty else 0
        rp_base_amt = rp_base_f['产品现有分销价'].sum() if not rp_base_f.empty and '产品现有分销价' in rp_base_f.columns else 0
        rp_curr_amt = rp_curr_f['产品现有分销价'].sum() if not rp_curr_f.empty and '产品现有分销价' in rp_curr_f.columns else 0

        # ─── 全量感知 ───
        st.markdown("**🌐 全量感知（产品流向主表 — 市场销量总盘）**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("基准月台数", f"{main_base_n:,}")
        c2.metric("当月台数", f"{main_curr_n:,}",
                   delta=f"{main_curr_n - main_base_n:+,}")
        c3.metric("基准月金额", f"{main_base_amt:,.0f}")
        c4.metric("当月金额", f"{main_curr_amt:,.0f}",
                   delta=f"{main_curr_amt - main_base_amt:+,.0f}")

        # ─── 安装红包 ───
        st.markdown("**🎯 安装红包（服务商绑定 — 销售链路明确）**")
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("基准月台数", f"{rp_base_n:,}")
        d2.metric("当月台数", f"{rp_curr_n:,}",
                   delta=f"{rp_curr_n - rp_base_n:+,}")
        d3.metric("基准月金额", f"{rp_base_amt:,.0f}")
        d4.metric("当月金额", f"{rp_curr_amt:,.0f}",
                   delta=f"{rp_curr_amt - rp_base_amt:+,.0f}")

        # ─── 绑定率（衡量服务商网络效能）───
        if main_curr_amt > 0:
            bind_curr = rp_curr_amt / main_curr_amt * 100
            bind_base = rp_base_amt / main_base_amt * 100 if main_base_amt > 0 else 0
            bind_delta = bind_curr - bind_base
            e1, e2, e3 = st.columns([1, 1, 2])
            e1.metric("📌 当月绑定率",
                       f"{bind_curr:.1f}%",
                       delta=f"{bind_delta:+.1f} pp",
                       help="红包货值 ÷ 全量货值。值低 = 大量销售没被本地服务商承接（外省货、项目直销、散户）")
            e2.metric("基准月绑定率", f"{bind_base:.1f}%")
            with e3:
                if bind_curr < 60:
                    st.warning(f"⚠️ 绑定率 {bind_curr:.0f}% 偏低，意味着 {100-bind_curr:.0f}% 销售游离在服务商管理体系外")

        st.divider()
        st.markdown("### 各维度环比全量明细（左：按下降排序 / 右：按上升排序）")
        st.caption("两张表是同一份数据的两个视角：左表从最大下降往下排（拉到底是上升项），右表从最大上升往下排（拉到底是下降项）")

        for label, (source, col) in DIM_CONFIG.items():
            if source == 'redpack':
                if rp_base_f.empty and rp_curr_f.empty:
                    with st.expander(f"{label}（⚠️ 红包记录表暂无数据，请到主页上传）", expanded=False):
                        st.info("此维度依赖『安装红包记录』表。请到主页选择该数据类型并上传 Excel。")
                    continue
                sub_base, sub_curr = rp_base_f, rp_curr_f
                m = mom_compare(sub_base, sub_curr, col,
                                count_col='产品序列号', amount_col='产品现有分销价')
            else:
                sub_base, sub_curr = df_base_f, df_curr_f
                m = mom_compare(sub_base, sub_curr, col)

            n_total = len(m)
            n_down = (m['台数变化'] < 0).sum()
            n_up = (m['台数变化'] > 0).sum()
            n_flat = (m['台数变化'] == 0).sum()

            left_view = m.sort_values('台数变化')  # 最下降在顶
            right_view = m.sort_values('台数变化', ascending=False)  # 最上升在顶

            default_open = label in ('区县', '产品系列')
            summary = f"共 {n_total} 项 · 下降 {n_down} · 上升 {n_up} · 持平 {n_flat}"
            with st.expander(f"{label}（{summary}）", expanded=default_open):
                col_l, col_r = st.columns(2)
                with col_l:
                    st.caption("📉 按下降排序")
                    st.dataframe(
                        style_change_table(left_view),
                        use_container_width=True,
                        hide_index=True,
                        height=500,
                    )
                with col_r:
                    st.caption("📈 按上升排序")
                    st.dataframe(
                        style_change_table(right_view),
                        use_container_width=True,
                        hide_index=True,
                        height=500,
                    )

    # ════════════════════ Tab 2：区县下降 ════════════════════
    with tab_district:
        st.subheader("区县 SO 环比下降")

        m = mom_compare(df_base, df_curr, '上线区县_全')
        m_down = m[m['台数变化'] < 0].sort_values('台数变化')

        c1, c2 = st.columns([1, 1])
        c1.metric("下降区县数", f"{len(m_down)}")
        c2.metric("下降总台数", f"{int(m_down['台数变化'].sum()):,}")

        st.dataframe(
            style_change_table(m_down),
            use_container_width=True,
            hide_index=True,
            height=400,
        )

        st.divider()
        st.markdown("### 下钻：选择一个区县看内部分布")
        if len(m_down) > 0:
            picked = st.selectbox(
                "区县",
                m_down['上线区县_全'].tolist(),
                key="district_drill",
            )
            sub_b = df_base[df_base['上线区县_全'] == picked]
            sub_c = df_curr[df_curr['上线区县_全'] == picked]

            # 显式告诉用户：这是该区县内的数据
            st.info(
                f"📍 **{picked}**　|　基准月 {len(sub_b):,} 台　→　对比月 {len(sub_c):,} 台"
                f"　（{len(sub_c) - len(sub_b):+,}）"
            )

            m_series = mom_compare(sub_b, sub_c, '产品系列_有效').sort_values('台数变化')
            m_dealer = mom_compare(sub_b, sub_c, '出库客户名称').sort_values('台数变化')

            colA, colB = st.columns(2)
            with colA:
                st.caption(f"📦 该区县内的产品系列（{len(m_series)} 个）")
                st.dataframe(
                    style_change_table(m_series),
                    use_container_width=True,
                    hide_index=True,
                    height=400,
                )
            with colB:
                st.caption(f"🏬 该区县内的代理商（{len(m_dealer)} 家）")
                st.dataframe(
                    style_change_table(m_dealer),
                    use_container_width=True,
                    hide_index=True,
                    height=400,
                )

    # ════════════════════ Tab 3：产品系列下降 ════════════════════
    with tab_series:
        st.subheader("产品系列 SO 环比下降")

        m = mom_compare(df_base, df_curr, '产品系列_有效')
        m_sorted = m.sort_values('台数变化')

        st.dataframe(
            style_change_table(m_sorted),
            use_container_width=True,
            hide_index=True,
            height=500,
        )

        st.divider()
        st.markdown("### 下钻：选择一个产品系列看城市分布")
        picked = st.selectbox(
            "产品系列",
            m_sorted['产品系列_有效'].dropna().tolist(),
            key="series_drill",
        )
        sub_b = df_base[df_base['产品系列_有效'] == picked]
        sub_c = df_curr[df_curr['产品系列_有效'] == picked]
        st.dataframe(
            style_change_table(
                mom_compare(sub_b, sub_c, '上线城市').sort_values('台数变化')
            ),
            use_container_width=True,
            hide_index=True,
        )

    # ════════════════════ Tab 4：代理商 / 服务商 ════════════════════
    with tab_dealer:
        sub_left, sub_right = st.tabs(["代理商（出库客户）", "服务商（上线自客户）"])

        with sub_left:
            st.subheader("代理商出货环比")
            st.caption(
                "🌐 **全量感知口径** — 数据源：`product_flow` · "
                "代理商 = 出库客户名称（大华出货给的对象） · 金额 = 最新分销价"
            )
            m = mom_compare(df_base, df_curr, '出库客户名称').sort_values('台数变化')
            st.dataframe(
                style_change_table(m),
                use_container_width=True,
                hide_index=True,
                height=500,
            )

        with sub_right:
            st.subheader("服务商上线环比")
            st.caption(
                "🎯 **安装红包口径** — 数据源：`install_redpack` · "
                "服务商 = 上线客户名称（按上线客户编码唯一） · 金额 = 产品现有分销价"
            )
            if rp_base.empty and rp_curr.empty:
                st.info("📦 安装红包记录表暂无数据。请到主页选择『安装红包记录』数据类型并上传 Excel。")
            else:
                m = mom_compare(rp_base, rp_curr, '上线客户名称',
                                count_col='产品序列号', amount_col='产品现有分销价'
                                ).sort_values('台数变化')
                st.dataframe(
                    style_change_table(m),
                    use_container_width=True,
                    hide_index=True,
                    height=500,
                )

    # ════════════════════ Tab 5：日期分布 ════════════════════
    with tab_daily:
        st.subheader("区县 SO 日期分布")
        st.caption("看选中区县在选中月份的每日 SO 台数曲线，识别波动")

        c1, c2 = st.columns(2)
        with c1:
            month_pick = st.selectbox(
                "月份", [base_month, curr_month], key="daily_month"
            )
        with c2:
            df_m = df[df['上线年月'] == month_pick]
            districts = (
                df_m.groupby('上线区县_全', dropna=False)['ID'].count()
                .sort_values(ascending=False)
            )
            district_pick = st.selectbox(
                "区县（按 SO 台数倒序）",
                districts.index.tolist(),
                key="daily_district",
            )

        sub = df_m[df_m['上线区县_全'] == district_pick]
        if sub.empty:
            st.info("该区县当月无数据")
        else:
            daily = sub.groupby('上线日期').agg(
                台数=('ID', 'count'),
                金额=('最新分销价', 'sum'),
            ).reset_index().sort_values('上线日期')

            # 波动指标
            mean_qty = daily['台数'].mean()
            std_qty = daily['台数'].std()
            cv = (std_qty / mean_qty) if mean_qty > 0 else np.nan
            max_share = daily['台数'].max() / daily['台数'].sum() if daily['台数'].sum() > 0 else np.nan

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("有数据天数", f"{len(daily)}")
            k2.metric("日均台数", f"{mean_qty:.1f}")
            k3.metric("变异系数 CV", f"{cv:.2f}" if pd.notna(cv) else "—",
                       help="CV = 标准差 / 均值，越大波动越剧烈")
            k4.metric("最大日占比", f"{max_share * 100:.1f}%" if pd.notna(max_share) else "—",
                       help="单日 SO 占整月 SO 比重，超过 30% 通常说明集中冲量")

            st.line_chart(daily.set_index('上线日期')['台数'])

            with st.expander("查看每日明细"):
                st.dataframe(daily, use_container_width=True, hide_index=True)


main()
