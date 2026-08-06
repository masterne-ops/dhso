#!/usr/bin/env python3
"""
产品流向分析 - 透视表展示
交互方式：
  - 选中区县 → 点击「查看设备明细」按钮 → 下方列出明细
"""

import io
import sys
import sqlite3
from pathlib import Path
from typing import Optional

import streamlit as st
import pandas as pd

# 引入共享加载（跨 page 共享缓存）
BASE_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import load_main_shared, load_redpack_shared, DB_PATH  # noqa: E402

# ── F7 有效客户门槛（可在此处调整，无需改业务逻辑）────────────
EFFECTIVE_MIN_AMOUNT = 1000   # 年度累计金额门槛（元）
EFFECTIVE_MIN_QTY    = 5      # 年度累计台数门槛

# 非真实客户名称（系统兜底分类，排除在头部依赖率 / HHI 之外）
GHOST_NAMES = {'其他', '其它', '未知'}

st.markdown("""
<style>
    .stSubheader { margin-top: 5px; margin-bottom: 5px; font-size: 1.1rem; }
    [data-testid="stCaption"] { margin-bottom: -8px; font-size: 0.85rem; }
    .stDataFrame { margin-bottom: 5px; }
    .stDataFrame td { text-align: left !important; }
    .stMultiSelect label { display: none; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────
# 数据加载 & 处理
# ──────────────────────────────────────────

NEEDED_COLS = [
    'ID', '产品序列号', '出库客户名称', '出货客户城市',
    '上线区县', '上线城市', '上线时间', '最新分销价',
    '上线自客户名称', '产品子系列', '内部型号',
    '所属一级客户', '产品系列',
    '出库时间', '三大重点专项', '安装红包是否发放抽奖机会', '数据剔除'
]

# 红包表（服务商相关分析专用，区县构成 + 代理商详情都从这里取）
REDPACK_TABLE = "install_redpack"
REDPACK_NEEDED_COLS = [
    '产品序列号', '上线时间',
    '上线客户编码', '上线客户名称',
    '所属一级客户', '出货客户名称',
    '安装城市', '安装区县',
    '产品现有分销价',
    '产品系列', '产品子系列', '内部型号',
]

def load_data() -> pd.DataFrame:
    return load_main_shared()


def load_redpack() -> pd.DataFrame:
    return load_redpack_shared()


def filter_redpack(rp: pd.DataFrame, customers: list = None,
                   online_cities: list = None, months: list = None) -> pd.DataFrame:
    """按主表的筛选维度等价过滤红包表。
    customers→出货客户名称  online_cities→安装城市  months→上线月份
    """
    if rp.empty:
        return rp
    out = rp.copy()
    if customers:
        out = out[out['出货客户名称'].isin(customers)]
    if online_cities:
        out = out[out['安装城市'].isin(online_cities)]
    out = out[out['上线年份'].isin([2025, 2026])]
    if months:
        out = out[out['上线月份'].isin(months)]
    return out


def sanitize_for_sqlite(df: pd.DataFrame) -> pd.DataFrame:
    """
    将 pandas 扩展类型（Int64、StringDtype、boolean 等）转为 SQLite 兼容的基础类型。
    核心问题：这些类型用 pd.NA 而非 np.nan，SQLite 绑定参数时会报 'unsupported type'。
    """
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_extension_array_dtype(df[col].dtype):
            df[col] = df[col].astype(object).where(df[col].notna(), None)
    return df


def get_db_columns(conn) -> list:
    """读取数据库现有的列名列表"""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(product_flow)")
    return [row[1] for row in cur.fetchall()]


def import_excel_to_db(uploaded_file, mode: str = 'merge') -> int:
    """
    将上传的 Excel 文件导入到 SQLite 数据库

    支持三种模式：
    - merge: 合并模式（默认），根据 ID 匹配，重合的覆盖，新的追加
    - append: 追加模式，直接添加所有数据
    - replace: 覆盖模式，清空后导入

    返回导入后数据库总行数
    """
    df_new = pd.read_excel(uploaded_file)
    df_new.columns = df_new.columns.str.strip()

    conn = sqlite3.connect(DB_PATH)

    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='product_flow'")
    table_exists = cur.fetchone() is not None

    if not table_exists or mode == 'replace':
        df_write = sanitize_for_sqlite(df_new)
        df_write.to_sql('product_flow', conn, if_exists='replace', index=False)

    else:
        db_cols = get_db_columns(conn)
        df_aligned = pd.DataFrame(index=df_new.index)
        for col in db_cols:
            df_aligned[col] = df_new[col] if col in df_new.columns else None

        df_aligned = sanitize_for_sqlite(df_aligned)

        if mode == 'append':
            df_aligned.to_sql('_tmp_import', conn, if_exists='replace', index=False)
            cols_sql = ', '.join(f'"{c}"' for c in db_cols)
            conn.execute(f"""
                INSERT OR IGNORE INTO product_flow ({cols_sql})
                SELECT {cols_sql} FROM _tmp_import
            """)
            conn.execute("DROP TABLE _tmp_import")

        else:  # merge
            df_aligned.to_sql('_tmp_import', conn, if_exists='replace', index=False)
            cols_sql = ', '.join(f'"{c}"' for c in db_cols)
            conn.execute(f"""
                INSERT OR REPLACE INTO product_flow ({cols_sql})
                SELECT {cols_sql} FROM _tmp_import
            """)
            conn.execute("DROP TABLE _tmp_import")

    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_id ON product_flow(ID)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_time ON product_flow(上线时间)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_district ON product_flow(上线区县)")
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM product_flow").fetchone()[0]
    conn.close()

    load_data.clear()
    return total


def filter_data(df: pd.DataFrame, customers: list = None, cities: list = None,
                online_cities: list = None, months: list = None) -> pd.DataFrame:
    df = df.copy()
    if customers:
        df = df[df['出库客户名称'].isin(customers)]
    if cities:
        df = df[df['出货客户城市'].isin(cities)]
    if online_cities:
        df = df[df['上线城市'].isin(online_cities)]

    df['上线时间'] = pd.to_datetime(df['上线时间'], errors='coerce')
    df['上线年份'] = df['上线时间'].dt.year
    df['上线月份'] = df['上线时间'].dt.month
    df = df[df['上线年份'].isin([2025, 2026])]

    if months:
        df = df[df['上线月份'].isin(months)]
    return df


def build_pivot_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=['上线区县', '2025 台数', '2026 台数', '2025 金额', '2026 金额'])

    count_pivot = df.pivot_table(
        index='上线区县', columns='上线年份', values='产品序列号', aggfunc='count', fill_value=0)
    amount_pivot = df.pivot_table(
        index='上线区县', columns='上线年份', values='最新分销价', aggfunc='sum', fill_value=0)

    for year in [2025, 2026]:
        if year not in count_pivot.columns:
            count_pivot[year] = 0
        if year not in amount_pivot.columns:
            amount_pivot[year] = 0

    result = pd.DataFrame(index=count_pivot.index)
    result['上线区县'] = result.index
    result['2025 台数'] = count_pivot[2025].values
    result['2026 台数'] = count_pivot[2026].values
    result['2025 金额'] = amount_pivot[2025].values
    result['2026 金额'] = amount_pivot[2026].values
    return result.fillna(0).reset_index(drop=True)


def style_pivot_table(df: pd.DataFrame):
    def highlight_cells(row):
        styles = [''] * len(row)
        if row['2026 台数'] < row['2025 台数']:
            styles[list(row.index).index('2026 台数')] = 'background-color: #ffcccc; color: #cc0000'
        if row['2026 金额'] < row['2025 金额']:
            styles[list(row.index).index('2026 金额')] = 'background-color: #ffcccc; color: #cc0000'
        return styles
    return df.style.apply(highlight_cells, axis=1)


def get_customer_detail(redpack_df: pd.DataFrame, district: str) -> pd.DataFrame:
    """区县内服务商构成（基于红包表）。
    district 是主表的「上线区县」名（仅区县名，无城市前缀）；
    红包表里对应字段是「安装区县」。
    服务商列用红包表的「上线客户名称」，金额用「产品现有分销价」。
    """
    if redpack_df.empty:
        return pd.DataFrame()
    district_df = redpack_df[redpack_df['安装区县'] == district].copy()
    if district_df.empty:
        return pd.DataFrame()

    pivot_count = district_df.pivot_table(
        index='上线客户名称', columns='上线年份', values='产品序列号', aggfunc='count', fill_value=0)
    pivot_amount = district_df.pivot_table(
        index='上线客户名称', columns='上线年份', values='产品现有分销价', aggfunc='sum', fill_value=0)

    for year in [2025, 2026]:
        if year not in pivot_count.columns:
            pivot_count[year] = 0
        if year not in pivot_amount.columns:
            pivot_amount[year] = 0

    result = pd.DataFrame(index=pivot_count.index)
    result['客户名称'] = result.index
    result['2025 台数'] = pivot_count[2025].values
    result['2026 台数'] = pivot_count[2026].values
    result['2025 金额'] = pivot_amount[2025].values
    result['2026 金额'] = pivot_amount[2026].values
    result = result.fillna(0).sort_values('2026 金额', ascending=False)

    total_row = {
        '客户名称': '✅ 合计',
        '2025 台数': result['2025 台数'].sum(),
        '2026 台数': result['2026 台数'].sum(),
        '2025 金额': result['2025 金额'].sum(),
        '2026 金额': result['2026 金额'].sum()
    }
    return pd.concat([pd.DataFrame([total_row]), result], ignore_index=True)


def get_device_detail(redpack_df: pd.DataFrame, district: str, year: int,
                      customer: str = None) -> pd.DataFrame:
    """区县内设备明细（基于红包表）。
    customer 传入的是红包表的「上线客户名称」（与上面的服务商列对应）。
    """
    if redpack_df.empty:
        return pd.DataFrame()
    mask = (redpack_df['安装区县'] == district) & (redpack_df['上线年份'] == year)
    detail_df = redpack_df[mask].copy()
    if customer:
        detail_df = detail_df[detail_df['上线客户名称'] == customer]
    if detail_df.empty:
        return pd.DataFrame()
    cols = [c for c in ['产品子系列', '内部型号', '产品序列号', '上线时间', '产品现有分销价']
            if c in detail_df.columns]
    result = detail_df[cols].copy()
    return result.sort_values('产品现有分销价', ascending=False)


def parse_event(event, df: pd.DataFrame, name_col: str):
    """
    从 st.dataframe on_select 事件中提取选中的名称和年份。
    返回 (name, year)，未选则为 None
    """
    name, year = None, None
    if not (event and 'selection' in event):
        return name, year
    sel = event['selection']
    rows = sel.get('rows', [])
    columns = sel.get('columns', [])
    if rows and rows[0] < len(df):
        name = df.iloc[rows[0]][name_col]
    if columns:
        idx = columns[0]
        col_name = df.columns[idx] if isinstance(idx, int) and idx < len(df.columns) else str(idx)
        if '2025' in str(col_name):
            year = 2025
        elif '2026' in str(col_name):
            year = 2026
    return name, year


# ──────────────────────────────────────────
# 服务商采购行为分析
# ──────────────────────────────────────────

def _channel_stats(year_df: pd.DataFrame, primary):
    """返回 (总台数, 总金额, 跨渠道台数, 跨渠道金额, 跨渠道金额占比%)"""
    qty = len(year_df)
    amt = float(year_df['产品现有分销价'].sum())
    if primary is None or year_df.empty:
        return qty, amt, 0, 0.0, 0.0
    cross = year_df[year_df['出货客户名称'] != primary]
    ca = float(cross['产品现有分销价'].sum())
    cq = len(cross)
    pct = ca / amt * 100 if amt > 0 else 0.0
    return qty, amt, cq, ca, pct


def _render_supplier_single(view_df: pd.DataFrame, primary, label: str):
    """单年 / 合并模式视图：3 个指标 + 单列供应商表 + 单列产品系列表"""
    qty, amt, cq, ca, pct = _channel_stats(view_df, primary)
    c1, c2, c3 = st.columns(3)
    c1.metric(f"{label} 采购台数", f"{qty} 台")
    c2.metric(f"{label} 采购金额", f"¥{amt:,.0f}")
    c3.metric(f"{label} 跨渠道占比", f"{pct:.1f}%",
               help="从非归属代理商处采购的金额占比")

    st.markdown("---")
    st.markdown("#### 🚚 供应商结构（按出货客户名称）")

    if view_df.empty:
        st.info("无数据")
    else:
        sup = view_df.groupby('出货客户名称').agg(
            台数=('产品序列号', 'count'),
            金额=('产品现有分销价', 'sum'),
        ).reset_index()
        if primary:
            sup['渠道类型'] = sup['出货客户名称'].apply(
                lambda x: '✅ 归属代理商' if x == primary else '⚠️ 跨渠道采购'
            )
        else:
            sup['渠道类型'] = '未知'
        total = sup['金额'].sum()
        sup['占比'] = sup['金额'].apply(
            lambda x: f"{x / total * 100:.1f}%" if total > 0 else "—")
        sup['_sort'] = sup['渠道类型'].apply(lambda x: 0 if '归属' in x else 1)
        sup = sup.sort_values(['_sort', '金额'], ascending=[True, False]).drop(columns=['_sort'])

        st.dataframe(
            sup[['出货客户名称', '渠道类型', '台数', '金额', '占比']],
            use_container_width=True, hide_index=True, height=220,
            column_config={
                '出货客户名称': st.column_config.TextColumn("出货客户名称", width="medium"),
                '渠道类型':    st.column_config.TextColumn("渠道类型", width="medium"),
                '台数':       st.column_config.NumberColumn(f"{label}台数", format="%d", width="small"),
                '金额':       st.column_config.NumberColumn(f"{label}金额(¥)", format="¥%d", width="small"),
                '占比':       st.column_config.TextColumn("占比", width="small"),
            }
        )

        cross_names = sup[sup['渠道类型'] == '⚠️ 跨渠道采购']['出货客户名称'].tolist()
        if cross_names:
            st.caption(
                f"⚠️ 跨渠道供应商：{'、'.join(cross_names)}"
                f"　→ 可由此推断归属代理商在产品供应或服务上存在不足"
            )
        elif primary:
            st.caption("✅ 该服务商所有采购均来自归属代理商，渠道纯净")

    st.markdown("---")
    st.markdown("#### 📊 产品结构（按产品系列）")
    if view_df.empty:
        st.info("无数据")
    else:
        ser = view_df.groupby('产品系列').agg(
            台数=('产品序列号', 'count'),
            金额=('产品现有分销价', 'sum'),
        ).reset_index().sort_values('金额', ascending=False)
        st.dataframe(
            ser,
            use_container_width=True, hide_index=True, height=200,
            column_config={
                '产品系列': st.column_config.TextColumn("产品系列", width="medium"),
                '台数':    st.column_config.NumberColumn(f"{label}台数", format="%d", width="small"),
                '金额':    st.column_config.NumberColumn(f"{label}金额(¥)", format="¥%d", width="small"),
            }
        )


@st.dialog("📦 服务商采购行为分析", width="large")
def show_supplier_analysis(df: pd.DataFrame, customer: str):
    """
    弹出页：服务商采购行为分析

    数据源：install_redpack（安装红包记录），和"区县服务商构成"同源，避免双源对不上。
    业务逻辑：
      服务商（红包表 上线客户名称）在体系中归属于一个代理商（所属一级客户）。
      该服务商上线的每台设备，有一个实际出货的代理商（红包表 出货客户名称）。
      - 正常渠道：出货客户 == 所属一级客户
      - 跨渠道采购：出货客户 != 所属一级客户（即"串货"）

    数据范围：根据当年数据可用性，提供仅 2025 / 仅 2026 / 25+26 合并 / 25 vs 26 对比 四种视图。
    注：使用全量数据，不受主界面月份筛选影响。
    """
    # ── 红包表全量按年份过滤（排除月份筛选干扰）────────────
    rp = load_redpack()
    if rp.empty:
        st.warning("📦 安装红包记录表暂无数据，请到主页选择「安装红包记录」并上传 Excel。")
        return

    cust_all = rp[
        (rp['上线客户名称'] == customer) &
        (rp['上线年份'].isin([2025, 2026]))
    ].copy()

    if cust_all.empty:
        st.warning(f"服务商「{customer}」在红包表 2025/2026 年没有记录")
        return

    # ── 归属代理商（取众数，容忍少量脏数据）────────────────────
    primary_vals = cust_all['所属一级客户'].dropna()
    primary = primary_vals.mode().iloc[0] if not primary_vals.empty else None

    # ── 标题 + 归属说明 ──────────────────────────────────────
    st.markdown(f"#### 📍 {customer} — 采购行为分析")
    if primary:
        st.info(
            f"**归属代理商（所属一级客户）：** {primary}　｜　"
            f"正常渠道 = 出货客户与归属代理商相同；否则为跨渠道采购"
        )
    else:
        st.warning("⚠️ 该服务商未关联所属一级客户，无法判断渠道归属")

    # ── 数据范围选择 ─────────────────────────────────────────
    has25 = (cust_all['上线年份'] == 2025).any()
    has26 = (cust_all['上线年份'] == 2026).any()

    if has25 and has26:
        range_options = ['📊 2025 vs 2026 对比', '🗓️ 仅 2025', '🗓️ 仅 2026', '➕ 2025+2026 合并']
    elif has25:
        range_options = ['🗓️ 仅 2025']
    else:
        range_options = ['🗓️ 仅 2026']

    range_pick = st.radio(
        "数据范围",
        options=range_options,
        horizontal=True,
        index=0,
        key=f"supplier_range_{customer}",
    )

    st.markdown("---")

    # ── 单年 / 合并模式：直接渲染 ──────────────────────────
    if '仅 2025' in range_pick:
        _render_supplier_single(cust_all[cust_all['上线年份'] == 2025], primary, '2025')
        return
    if '仅 2026' in range_pick:
        _render_supplier_single(cust_all[cust_all['上线年份'] == 2026], primary, '2026')
        return
    if '合并' in range_pick:
        _render_supplier_single(cust_all, primary, '25+26')
        return

    # ── 25 vs 26 对比模式（原有逻辑）────────────────────────
    df25 = cust_all[cust_all['上线年份'] == 2025]
    df26 = cust_all[cust_all['上线年份'] == 2026]
    qty25, amt25, cq25, ca25, cp25 = _channel_stats(df25, primary)
    qty26, amt26, cq26, ca26, cp26 = _channel_stats(df26, primary)

    # ── 汇总指标卡 ───────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("2025 采购台数", f"{qty25} 台",
              delta=f"→ 2026：{qty26} 台", delta_color="off")
    c2.metric("2025 采购金额", f"¥{amt25:,.0f}",
              delta=f"→ 2026：¥{amt26:,.0f}", delta_color="off")
    c3.metric("2025 跨渠道占比", f"{cp25:.1f}%",
              help="从非归属代理商处采购的金额占全年比例")
    c4.metric("2026 跨渠道占比", f"{cp26:.1f}%",
              delta=f"{cp26 - cp25:+.1f}%",
              delta_color="inverse" if cp26 > cp25 else "normal")

    st.markdown("---")

    # ── 供应商结构（出货客户名称 × 年份）────────────────────
    st.markdown("#### 🚚 供应商结构（按出货客户名称）")

    def agg_by_col(year_df, group_col, sfx):
        if year_df.empty:
            return pd.DataFrame(columns=[group_col, f'{sfx}台数', f'{sfx}金额'])
        return year_df.groupby(group_col).agg(
            **{f'{sfx}台数': ('产品序列号', 'count'),
               f'{sfx}金额': ('产品现有分销价', 'sum')}
        ).reset_index()

    sup25 = agg_by_col(df25, '出货客户名称', '25年')
    sup26 = agg_by_col(df26, '出货客户名称', '26年')
    sup = pd.merge(sup25, sup26, on='出货客户名称', how='outer').fillna(0)

    # 渠道类型标注
    if primary:
        sup['渠道类型'] = sup['出货客户名称'].apply(
            lambda x: '✅ 归属代理商' if x == primary else '⚠️ 跨渠道采购'
        )
    else:
        sup['渠道类型'] = '未知'

    # 金额占比
    t25 = sup['25年金额'].sum()
    t26 = sup['26年金额'].sum()
    sup['25年占比'] = sup['25年金额'].apply(
        lambda x: f"{x / t25 * 100:.1f}%" if t25 > 0 else "—")
    sup['26年占比'] = sup['26年金额'].apply(
        lambda x: f"{x / t26 * 100:.1f}%" if t26 > 0 else "—")

    # 归属代理商排首行，其余按 2025 金额降序
    sup['_sort'] = sup['渠道类型'].apply(lambda x: 0 if '归属' in x else 1)
    sup = sup.sort_values(['_sort', '25年金额'], ascending=[True, False]).drop(columns=['_sort'])

    st.dataframe(
        sup[['出货客户名称', '渠道类型', '25年台数', '25年金额', '25年占比',
             '26年台数', '26年金额', '26年占比']],
        use_container_width=True, hide_index=True, height=220,
        column_config={
            '出货客户名称': st.column_config.TextColumn("出货客户名称", width="medium"),
            '渠道类型':    st.column_config.TextColumn("渠道类型", width="medium"),
            '25年台数':   st.column_config.NumberColumn("2025台数", format="%d", width="small"),
            '25年金额':   st.column_config.NumberColumn("2025金额(¥)", format="¥%d", width="small"),
            '25年占比':   st.column_config.TextColumn("2025占比", width="small"),
            '26年台数':   st.column_config.NumberColumn("2026台数", format="%d", width="small"),
            '26年金额':   st.column_config.NumberColumn("2026金额(¥)", format="¥%d", width="small"),
            '26年占比':   st.column_config.TextColumn("2026占比", width="small"),
        }
    )

    # 跨渠道供应商列表提示
    cross_names = sup[sup['渠道类型'] == '⚠️ 跨渠道采购']['出货客户名称'].tolist()
    if cross_names:
        st.caption(f"⚠️ 跨渠道供应商：{'、'.join(cross_names)}"
                   f"　→ 可由此推断归属代理商在产品供应或服务上存在不足")
    elif primary:
        st.caption("✅ 该服务商所有采购均来自归属代理商，渠道纯净")

    st.markdown("---")

    # ── 产品系列分析（产品系列 × 年份）─────────────────────
    st.markdown("#### 📊 产品结构（按产品系列）")

    ser25 = agg_by_col(df25, '产品系列', '25年')
    ser26 = agg_by_col(df26, '产品系列', '26年')
    ser = pd.merge(ser25, ser26, on='产品系列', how='outer').fillna(0)
    ser = ser.sort_values('25年金额', ascending=False)

    st.dataframe(
        ser,
        use_container_width=True, hide_index=True, height=200,
        column_config={
            '产品系列':  st.column_config.TextColumn("产品系列", width="medium"),
            '25年台数': st.column_config.NumberColumn("2025台数", format="%d", width="small"),
            '25年金额': st.column_config.NumberColumn("2025金额(¥)", format="¥%d", width="small"),
            '26年台数': st.column_config.NumberColumn("2026台数", format="%d", width="small"),
            '26年金额': st.column_config.NumberColumn("2026金额(¥)", format="¥%d", width="small"),
        }
    )


# ──────────────────────────────────────────
# F7：客户健康度分析（核心计算 + 图片导出 + 对话框）
# ──────────────────────────────────────────

def compute_customer_health(df: pd.DataFrame, district: str,
                            min_amount: float = EFFECTIVE_MIN_AMOUNT,
                            min_qty: int = EFFECTIVE_MIN_QTY) -> Optional[dict]:
    """
    计算某区县渠道客户健康度（F7核心逻辑）。

    设计原则：
      - 头部依赖率 / 篮子数以 2025 全年数据为基准（2026 仅 1-4 月，避免低估风险）
      - GHOST_NAMES（其他/其它/未知）排除在命名分析之外，防止污染头部指标
      - 客户分类覆盖全量 9 种类型，含流失/缩量/超大/新增/增长/正常/噪音/未归属

    返回 dict 或 None（区县无数据时）
    """
    dist_df = df[df['上线区县'] == district].copy()
    if dist_df.empty:
        return None

    # ── 按 客户 × 年份 聚合 ────────────────────────────────
    agg = dist_df.groupby(['上线自客户名称', '上线年份']).agg(
        qty=('产品序列号', 'count'),
        amt=('最新分销价', 'sum')
    ).reset_index()

    all_custs = agg['上线自客户名称'].unique()
    records = []
    for c in all_custs:
        c_data = agg[agg['上线自客户名称'] == c]
        d25 = c_data[c_data['上线年份'] == 2025]
        d26 = c_data[c_data['上线年份'] == 2026]
        records.append({
            '客户名称':  c,
            '2025台数':  int(d25['qty'].sum()) if not d25.empty else 0,
            '2025金额':  float(d25['amt'].sum()) if not d25.empty else 0.0,
            '2026台数':  int(d26['qty'].sum()) if not d26.empty else 0,
            '2026金额':  float(d26['amt'].sum()) if not d26.empty else 0.0,
        })

    cdf = pd.DataFrame(records)

    # ── 有效客户判定（年度累计口径）──────────────────────────
    cdf['2025有效'] = (cdf['2025金额'] >= min_amount) | (cdf['2025台数'] >= min_qty)
    cdf['2026有效'] = (cdf['2026金额'] >= min_amount) | (cdf['2026台数'] >= min_qty)

    # ── 区县总金额（含噪音，作头部依赖率分母）────────────────
    total_amt25 = float(dist_df[dist_df['上线年份'] == 2025]['最新分销价'].sum())
    total_amt26 = float(dist_df[dist_df['上线年份'] == 2026]['最新分销价'].sum())

    # ── 命名有效客户（排除兜底分类，用于头部指标 / HHI）────────
    eff25_named = cdf[cdf['2025有效'] & ~cdf['客户名称'].isin(GHOST_NAMES)]
    eff26_named = cdf[cdf['2026有效'] & ~cdf['客户名称'].isin(GHOST_NAMES)]

    # ── 头部依赖率（基准：2025 全年命名有效客户）──────────────
    top_customer, top_dep_rate_25, top_dep_rate_26 = "", 0.0, 0.0
    if not eff25_named.empty and total_amt25 > 0:
        top_row = eff25_named.loc[eff25_named['2025金额'].idxmax()]
        top_customer    = str(top_row['客户名称'])
        top_dep_rate_25 = float(top_row['2025金额']) / total_amt25
    if not eff26_named.empty and total_amt26 > 0:
        top_row26       = eff26_named.loc[eff26_named['2026金额'].idxmax()]
        top_dep_rate_26 = float(top_row26['2026金额']) / total_amt26

    # ── 客户篮子数（HHI 倒数，基于 2025 命名有效客户）──────────
    basket_count = None
    if not eff25_named.empty and total_amt25 > 0:
        named_total = float(eff25_named['2025金额'].sum())
        if named_total > 0:
            shares = eff25_named['2025金额'] / named_total
            hhi = float((shares ** 2).sum()) * 10000
            basket_count = max(1, round(10000 / hhi)) if hhi > 0 else len(eff25_named)

    # ── 客户类型分类（9 种，全覆盖）──────────────────────────
    def classify(row):
        name = str(row['客户名称'])
        is25, is26 = row['2025有效'], row['2026有效']
        q26, a25, a26 = row['2026台数'], row['2025金额'], row['2026金额']
        if name in GHOST_NAMES:
            return '⚫ 未归属记录'
        if not is25 and not is26:
            return '⚪ 噪音客户'
        if not is25 and is26:
            return '🆕 新增客户'
        if is25 and q26 == 0 and a26 == 0:
            return '🔴 流失客户'
        if is25 and (q26 > 0 or a26 > 0) and not is26:
            return '🟡 缩量客户'
        # 以下均为 is25 AND is26
        dep25 = a25 / total_amt25 if total_amt25 > 0 else 0
        if dep25 > 0.4:
            return '🔴 超大客户（缩量）' if (a25 > 0 and a26 < a25 * 0.7) else '🟡 超大客户（稳定）'
        if a25 > 0 and a26 > a25 * 1.1:
            return '✅ 增长客户'
        return '✅ 正常客户'

    cdf['客户类型'] = cdf.apply(classify, axis=1)

    ACTION_MAP = {
        '🔴 流失客户':        '立即联系，确认是否转向竞品',
        '🔴 超大客户（缩量）':  '重点拜访，了解 2026 项目进展',
        '🟡 缩量客户':        '跟进确认，了解采购计划是否减少',
        '🟡 超大客户（稳定）':  '定期维护，同步拓展其他客户降低依赖',
        '🆕 新增客户':        '建立联系，了解需求，培养为稳定客户',
        '✅ 增长客户':        '维持关系，复制增长经验',
        '✅ 正常客户':        '正常拜访频率',
        '⚫ 未归属记录':       '数据中未关联具体客户名称，建议补录',
        '⚪ 噪音客户':        '—',
    }
    cdf['建议动作'] = cdf['客户类型'].map(ACTION_MAP)

    # ── 健康度评级 ─────────────────────────────────────────
    lost_ratio = (cdf[cdf['客户类型'] == '🔴 流失客户']['2025金额'].sum() / total_amt25
                  if total_amt25 > 0 else 0)
    eff25_count = int(cdf['2025有效'].sum())
    eff26_count = int(cdf['2026有效'].sum())
    eff_drop    = (eff25_count - eff26_count) / eff25_count if eff25_count > 0 else 0

    if top_dep_rate_25 > 0.5 or lost_ratio > 0.15:
        health = '🔴 高危'
    elif top_dep_rate_25 > 0.3 or eff_drop > 0.3:
        health = '🟡 预警'
    else:
        health = '🟢 健康'

    return {
        'district':        district,
        'total_count':     len(cdf),
        'eff_25':          eff25_count,
        'eff_26':          eff26_count,
        'top_customer':    top_customer,
        'top_dep_rate_25': top_dep_rate_25,
        'top_dep_rate_26': top_dep_rate_26,
        'basket_count':    basket_count,
        'health':          health,
        'total_amt25':     total_amt25,
        'total_amt26':     total_amt26,
        'cdf':             cdf,
    }


def generate_health_image(h: dict) -> bytes:
    """将客户健康度分析结果渲染为 PNG 字节流（适合微信分享）"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from datetime import datetime

    matplotlib.rcParams['font.family'] = [
        'Noto Sans SC', 'Arial Unicode MS', 'PingFang SC', 'Heiti SC', 'SimHei', 'DejaVu Sans'
    ]
    matplotlib.rcParams['axes.unicode_minus'] = False

    TYPE_LABEL = {
        '🔴 流失客户':       '流失',    '🔴 超大客户（缩量）': '超大缩量',
        '🟡 缩量客户':       '缩量',    '🟡 超大客户（稳定）': '超大稳定',
        '🆕 新增客户':       '新增',    '✅ 增长客户':        '增长',
        '✅ 正常客户':       '正常',    '⚫ 未归属记录':       '未归属',
        '⚪ 噪音客户':       '噪音',
    }
    TYPE_BG = {
        '🔴 流失客户':       '#FFEBEE', '🔴 超大客户（缩量）': '#FFEBEE',
        '🟡 缩量客户':       '#FFFDE7', '🟡 超大客户（稳定）': '#FFFDE7',
        '🆕 新增客户':       '#E8F5E9', '✅ 增长客户':        '#E8F5E9',
        '✅ 正常客户':       '#FAFAFA', '⚫ 未归属记录':       '#EEEEEE',
        '⚪ 噪音客户':       '#F5F5F5',
    }
    TYPE_FG = {
        '流失': '#C62828', '超大缩量': '#C62828',
        '缩量': '#E65100', '超大稳定': '#E65100',
        '新增': '#1B5E20', '增长': '#1B5E20',
        '正常': '#455A64', '未归属': '#757575', '噪音': '#9E9E9E',
    }
    PRIORITY = {
        '🔴 流失客户': 0, '🔴 超大客户（缩量）': 1,
        '🟡 缩量客户': 2, '🟡 超大客户（稳定）': 3,
        '🆕 新增客户': 4, '✅ 增长客户': 5, '✅ 正常客户': 6,
        '⚫ 未归属记录': 7, '⚪ 噪音客户': 8,
    }
    cdf = h['cdf'].copy()
    cdf['_sort'] = cdf['客户类型'].map(PRIORITY).fillna(99)
    cdf = cdf.sort_values(['_sort', '2025金额'], ascending=[True, False]).drop(columns=['_sort'])
    show_df = cdf[cdf['客户类型'] != '⚪ 噪音客户'].head(30)
    n_rows  = len(show_df)

    DPI = 100; W = 13.0
    HDR_H = 1.30; MET_H = 1.50; ROW_H = 0.30; THDR_H = 0.38; FTR_H = 0.40
    table_h = THDR_H + n_rows * ROW_H
    FIG_H   = HDR_H + MET_H + table_h + FTR_H

    fig = plt.figure(figsize=(W, FIG_H), dpi=DPI, facecolor='white')

    health_txt = h['health']
    hcol = '#EF5350' if '高危' in health_txt else '#FF9800' if '预警' in health_txt else '#66BB6A'
    health_short = health_txt.replace('🔴 ', '').replace('🟡 ', '').replace('🟢 ', '')

    # 标题栏
    ax_hdr = fig.add_axes([0, (FIG_H - HDR_H) / FIG_H, 1, HDR_H / FIG_H])
    ax_hdr.set_facecolor('#1A3C5E')
    ax_hdr.set_xlim(0, W); ax_hdr.set_ylim(0, HDR_H); ax_hdr.axis('off')
    ax_hdr.text(0.32, HDR_H * 0.65, f"{h['district']}  渠道客户健康度分析",
                fontsize=21, fontweight='bold', color='white', va='center')
    badge = patches.FancyBboxPatch(
        (W - 2.20, HDR_H * 0.35), 1.90, HDR_H * 0.50,
        boxstyle="round,pad=0.06", facecolor=hcol, edgecolor='none')
    ax_hdr.add_patch(badge)
    ax_hdr.text(W - 1.25, HDR_H * 0.60, health_short,
                fontsize=14, color='white', ha='center', va='center', fontweight='bold')
    ax_hdr.text(0.32, HDR_H * 0.22,
                f"数据截止 2026-04  |  生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                fontsize=9, color='#90CAF9', va='center')

    # 指标卡
    met_b = (FTR_H + table_h) / FIG_H
    metric_items = [
        ("客户总数 / 有效客户", f"{h['total_count']} / {h['eff_26']}", f"2025有效 {h['eff_25']} 家"),
        ("头部依赖率（2025年）", f"{h['top_dep_rate_25']:.1%}",
         f"2026预估 {h['top_dep_rate_26']:.1%}" if h['total_amt26'] > 0 else "2026：暂无数据"),
        ("客户篮子数", str(h['basket_count']) if h['basket_count'] else "—", "数值越小集中度越高"),
        ("健康度", health_short, "高危 / 预警 / 健康 三档评级"),
    ]
    card_w = 1.0 / 4
    for i, (lbl, val, sub) in enumerate(metric_items):
        ax_m = fig.add_axes([i * card_w + 0.004, met_b + 0.010,
                             card_w - 0.008, MET_H / FIG_H - 0.020])
        ax_m.set_facecolor('#F0F4F8'); ax_m.set_xlim(0, 1); ax_m.set_ylim(0, 1)
        for sp in ['top', 'bottom', 'left', 'right']:
            ax_m.spines[sp].set_visible(True); ax_m.spines[sp].set_color('#CADAE6')
        ax_m.axis('off')
        vc = hcol if i == 3 else '#1A3C5E'
        ax_m.text(0.5, 0.80, lbl, fontsize=9.5, color='#607D8B', ha='center', va='center')
        ax_m.text(0.5, 0.50, val, fontsize=20, fontweight='bold', color=vc, ha='center', va='center')
        ax_m.text(0.5, 0.18, sub, fontsize=8.5, color='#90A4AE', ha='center', va='center')

    # 客户明细表
    ax_t = fig.add_axes([0.01, FTR_H / FIG_H, 0.98, table_h / FIG_H])
    ax_t.set_xlim(0, 1); ax_t.set_ylim(0, n_rows + 1); ax_t.axis('off')
    COLS = [
        ("客户名称",  0.000, 0.255, 'left'),  ("类型",     0.255, 0.340, 'center'),
        ("2025台数", 0.340, 0.410, 'right'), ("2026台数", 0.410, 0.480, 'right'),
        ("2025金额", 0.480, 0.590, 'right'), ("2026金额", 0.590, 0.695, 'right'),
        ("建议动作",  0.695, 1.000, 'left'),
    ]
    PAD = 0.010
    ax_t.add_patch(patches.Rectangle((0, n_rows), 1, 1, facecolor='#1A3C5E', edgecolor='none'))
    for (hdr_txt, xl, xr, align) in COLS:
        tx = xr - PAD if align == 'right' else (xl + xr) / 2 if align == 'center' else xl + PAD
        ax_t.text(tx, n_rows + 0.5, hdr_txt,
                  fontsize=9, color='white', fontweight='bold', ha=align, va='center')
    for ri, (_, row) in enumerate(show_df.iterrows()):
        yb = n_rows - 1 - ri
        ax_t.add_patch(patches.Rectangle((0, yb), 1, 1,
                       facecolor=TYPE_BG.get(row['客户类型'], '#FAFAFA'), edgecolor='none'))
        ax_t.plot([0, 1], [yb, yb], color='#E0E0E0', linewidth=0.4)
        ts = TYPE_LABEL.get(row['客户类型'], str(row['客户类型']))
        tf = TYPE_FG.get(ts, '#333333')
        vals = [str(row['客户名称'])[:26], ts,
                str(int(row['2025台数'])), str(int(row['2026台数'])),
                f"¥{int(row['2025金额']):,}", f"¥{int(row['2026金额']):,}",
                str(row['建议动作'])[:24] if pd.notna(row['建议动作']) else "—"]
        for vi, (val, (_, xl, xr, align)) in enumerate(zip(vals, COLS)):
            tx = xr - PAD if align == 'right' else (xl + xr) / 2 if align == 'center' else xl + PAD
            ax_t.text(tx, yb + 0.5, val, fontsize=8,
                      color=tf if vi == 1 else '#333333',
                      fontweight='bold' if vi == 1 else 'normal',
                      ha=align, va='center', clip_on=True)
    ax_t.plot([0, 1], [0, 0], color='#E0E0E0', linewidth=0.4)

    # 页脚
    ax_f = fig.add_axes([0, 0, 1, FTR_H / FIG_H])
    ax_f.set_facecolor('#EEF2F7'); ax_f.set_xlim(0, W); ax_f.set_ylim(0, FTR_H); ax_f.axis('off')
    ax_f.text(W / 2, FTR_H / 2,
              f"产品流向分析系统  ·  有效客户门槛：年度金额 ≥ ¥{EFFECTIVE_MIN_AMOUNT:,} 或台数 ≥ {EFFECTIVE_MIN_QTY} 台  "
              f"|  头部依赖率 > 50% 高危  30-50% 预警  <30% 健康",
              fontsize=8.5, color='#8090A0', ha='center', va='center')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=DPI, bbox_inches=None, facecolor='white', edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


@st.dialog("🏥 客户结构分析", width="large")
def show_customer_health(df: pd.DataFrame, district: str):
    """F7 弹出页：区县渠道客户健康度分析（全量客户视图）"""
    h = compute_customer_health(df, district)
    if h is None:
        st.warning(f"「{district}」没有数据")
        return

    # ── 标题 + 导出按钮 ───────────────────────────────────
    title_col, btn_col = st.columns([3, 1])
    with title_col:
        st.markdown(f"#### 📍 {district} — 渠道客户健康度")
        st.caption(
            f"有效客户门槛：年度累计金额 ≥ ¥{EFFECTIVE_MIN_AMOUNT:,} "
            f"或台数 ≥ {EFFECTIVE_MIN_QTY} 台（满足其一即可）"
        )
    with btn_col:
        try:
            img_bytes = generate_health_image(h)
            st.download_button(
                label="📥 导出图片",
                data=img_bytes,
                file_name=f"{district}_客户健康度分析.png",
                mime="image/png",
                use_container_width=True,
                help="导出为图片，可直接发送微信",
            )
        except Exception as _e:
            st.caption(f"导出不可用：{_e}")

    # ── 四格指标卡 ────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "客户总数 / 有效客户数",
        f"{h['total_count']} / {h['eff_26']}",
        delta=f"2025有效：{h['eff_25']} 家", delta_color="off"
    )
    c2.metric(
        "头部依赖率（2025全年）",
        f"{h['top_dep_rate_25']:.1%}",
        delta=f"2026预估：{h['top_dep_rate_26']:.1%}" if h['total_amt26'] > 0 else None,
        delta_color="inverse"
    )
    c3.metric(
        "客户篮子数",
        str(h['basket_count']) if h['basket_count'] else "—",
        delta="篮子数越少风险越高", delta_color="off"
    )
    c4.metric("健康度", h['health'])

    # ── 头部客户高亮 ──────────────────────────────────────
    if h['top_customer']:
        dep = h['top_dep_rate_25']
        tag = "🔴" if dep > 0.5 else "🟡" if dep > 0.3 else "🟢"
        st.info(
            f"{tag} **头部客户**：{h['top_customer']}  ·  "
            f"占 2025 年区县总金额 **{dep:.1%}**  ·  "
            f"2025 年贡献 ¥{dep * h['total_amt25']:,.0f}"
        )

    st.markdown("---")
    st.markdown("**所有客户明细**　　*按紧迫程度排序，点击表头可排序*")

    # ── 客户明细表（全量，含所有类型）────────────────────────
    cdf = h['cdf'].copy()
    PRIORITY = {
        '🔴 流失客户': 0,        '🔴 超大客户（缩量）': 1,
        '🟡 缩量客户': 2,        '🟡 超大客户（稳定）': 3,
        '🆕 新增客户': 4,        '✅ 增长客户': 5,
        '✅ 正常客户': 6,        '⚫ 未归属记录': 7,
        '⚪ 噪音客户': 8,
    }
    cdf['_sort'] = cdf['客户类型'].map(PRIORITY).fillna(99)
    cdf = cdf.sort_values(['_sort', '2025金额'], ascending=[True, False]).drop(columns=['_sort'])

    display = cdf[['客户名称', '客户类型', '2025台数', '2026台数',
                   '2025金额', '2026金额', '建议动作']].copy()
    display['2025金额'] = display['2025金额'].round(0).astype(int)
    display['2026金额'] = display['2026金额'].round(0).astype(int)

    st.dataframe(
        display,
        use_container_width=True, hide_index=True, height=380,
        column_config={
            '客户名称': st.column_config.TextColumn("客户名称", width="medium"),
            '客户类型': st.column_config.TextColumn("客户类型", width="medium"),
            '2025台数': st.column_config.NumberColumn("2025台数", format="%d", width="small"),
            '2026台数': st.column_config.NumberColumn("2026台数", format="%d", width="small"),
            '2025金额': st.column_config.NumberColumn("2025金额(¥)", format="¥%d", width="small"),
            '2026金额': st.column_config.NumberColumn("2026金额(¥)", format="¥%d", width="small"),
            '建议动作': st.column_config.TextColumn("建议动作", width="large"),
        }
    )

    # ── 类型汇总 ──────────────────────────────────────────
    type_counts = cdf['客户类型'].value_counts()
    summary_parts = [f"{lbl} {type_counts.get(lbl, 0)}家"
                     for lbl in PRIORITY if type_counts.get(lbl, 0) > 0]
    if summary_parts:
        st.caption("　".join(summary_parts))

    # ── 指标说明 ──────────────────────────────────────────
    with st.expander("📖 指标说明"):
        st.markdown(f"""
**有效客户**：年度累计金额 ≥ ¥{EFFECTIVE_MIN_AMOUNT:,} 或台数 ≥ {EFFECTIVE_MIN_QTY} 台，过滤偶发性小单。

**头部依赖率**：最大单个有效客户年度金额 ÷ 区县总金额（含噪音客户）。以 2025 全年为基准。
> 🔴 > 50% 高危　🟡 30–50% 预警　🟢 < 30% 健康

**客户篮子数**：1 ÷ HHI × 10,000，等效于「几个均量客户在支撑」，越小风险越高。

**客户类型说明**：
- 🔴 **流失客户**：2025年有效，2026年无任何交易
- 🔴 **超大客户（缩量）**：头部占比 > 40% 且 2026 同比下滑 > 30%
- 🟡 **缩量客户**：2025年有效，2026年有交易但未达有效门槛
- 🟡 **超大客户（稳定）**：头部占比 > 40% 且 2026 交易正常
- 🆕 **新增客户**：2025年无有效交易，2026年达到有效门槛
- ✅ **增长客户**：两年均有效，2026金额同比增长 > 10%
- ✅ **正常客户**：两年均有效，趋势平稳
- ⚫ **未归属记录**：「其他」等系统兜底分类，不计入头部依赖率
- ⚪ **噪音客户**：两年均不满足有效门槛的偶发采购
        """)


# ──────────────────────────────────────────
# 代理商综合能力评分
# ──────────────────────────────────────────

def compute_distributor_scores(df: pd.DataFrame, year: int = 2026) -> pd.DataFrame:
    """
    计算所有代理商综合能力评分。

    参数：
      year  — 使用哪一年的数据（默认2026；2026为截至当前月，2025为全年）

    数据过滤：数据剔除 == '其他'（排除电商/4G渠道）

    5个评分维度（满分100分）：
      D1 渠道覆盖宽度（30分）：有效服务商数量
      D2 货物流通效率（20分）：出库→上线中位天数（越短越好）
      D3 产品结构优化（20分）：单台均价（反映产品线品质）
      D4 服务激活率  （20分）：安装红包发放率
      D5 重点专项参与（10分）：三大重点专项参与率

    返回 DataFrame，每行一个代理商，按综合评分降序排列
    """
    base = df[df['上线年份'] == year].copy()
    if '数据剔除' in base.columns:
        base = base[base['数据剔除'].fillna('其他') == '其他']
    if base.empty:
        return pd.DataFrame()

    # ── 评分函数 ─────────────────────────────
    def score_d1(n):
        """渠道覆盖宽度：有效服务商数"""
        if n >= 100: return 30
        if n >= 30:  return 25
        if n >= 15:  return 20
        if n >= 10:  return 15
        if n >= 5:   return 10
        if n >= 2:   return 5
        if n >= 1:   return 2
        return 0

    def score_d2(d):
        """货物流通效率：出库→上线中位天数（越短越好）"""
        if pd.isna(d): return 0
        if d <= 15:  return 20
        if d <= 30:  return 18
        if d <= 60:  return 15
        if d <= 100: return 12
        if d <= 150: return 8
        if d <= 250: return 5
        if d <= 400: return 2
        return 0

    def score_d3(p):
        """产品结构：单台均价（基于实际价格区间 ¥140-¥300+）"""
        if p >= 300: return 20
        if p >= 260: return 16
        if p >= 220: return 12
        if p >= 190: return 8
        if p >= 160: return 4
        return 2

    def score_d4(r):
        """服务激活率：安装红包发放率"""
        if r >= 0.60: return 20
        if r >= 0.40: return 16
        if r >= 0.25: return 12
        if r >= 0.15: return 8
        if r >= 0.05: return 4
        if r >  0:    return 2
        return 0

    def score_d5(r):
        """重点专项参与率"""
        if r >= 0.40: return 10
        if r >= 0.25: return 8
        if r >= 0.15: return 6
        if r >= 0.10: return 4
        if r >= 0.05: return 2
        return 0

    results = []
    for dist in base['出库客户名称'].dropna().unique():
        sub = base[base['出库客户名称'] == dist]
        total_qty = len(sub)
        total_amt = float(sub['最新分销价'].sum())

        # D1: 有效服务商数
        srv_agg = sub.groupby('上线自客户名称').agg(
            qty=('产品序列号', 'count'),
            amt=('最新分销价', 'sum')
        )
        n_eff = int(((srv_agg['amt'] >= EFFECTIVE_MIN_AMOUNT) |
                     (srv_agg['qty'] >= EFFECTIVE_MIN_QTY)).sum())

        # D2: 流通天数中位数
        valid_days = sub['流通天数'].dropna()
        median_days = float(valid_days.median()) if not valid_days.empty else None

        # D3: 单台均价
        avg_price = total_amt / total_qty if total_qty > 0 else 0.0

        # D4: 红包率（字段值 == 'Y' 表示已发放红包抽奖机会）
        hb_col = '安装红包是否发放抽奖机会'
        if hb_col in sub.columns:
            hongbao_rate = float((sub[hb_col] == 'Y').sum()) / total_qty if total_qty > 0 else 0.0
        else:
            hongbao_rate = 0.0

        # D5: 专项率（'其他' 表示非专项；有具体专项名称才算参与）
        sp_col = '三大重点专项'
        if sp_col in sub.columns:
            in_special = sub[sp_col].notna() & (~sub[sp_col].isin(['其他', '']))
            special_rate = float(in_special.sum()) / total_qty if total_qty > 0 else 0.0
        else:
            special_rate = 0.0

        s1 = score_d1(n_eff)
        s2 = score_d2(median_days)
        s3 = score_d3(avg_price)
        s4 = score_d4(hongbao_rate)
        s5 = score_d5(special_rate)

        results.append({
            '代理商':      dist,
            '台数':        total_qty,
            '金额':        total_amt,
            '有效服务商数':  n_eff,
            '流通天数中位':  round(median_days) if pd.notna(median_days) else None,
            '单台均价':     round(avg_price, 0),
            '红包率':       round(hongbao_rate, 4),
            '专项率':       round(special_rate, 4),
            'D1_覆盖宽度':  s1,
            'D2_流通效率':  s2,
            'D3_产品结构':  s3,
            'D4_服务激活':  s4,
            'D5_专项参与':  s5,
            '综合评分':     s1 + s2 + s3 + s4 + s5,
        })

    result_df = pd.DataFrame(results)
    return result_df.sort_values('综合评分', ascending=False).reset_index(drop=True)


@st.dialog("🏆 代理商能力详情", width="large")
def show_distributor_detail(df: pd.DataFrame, distributor: str,
                            scores_df: pd.DataFrame, year: int = 2026):
    """弹出代理商综合能力详情（评分分解 + 服务商构成 + 产品结构）"""
    dist_mask = scores_df['代理商'] == distributor
    if not dist_mask.any():
        st.warning(f"未找到「{distributor}」的评分数据")
        return

    row = scores_df[dist_mask].iloc[0]
    rank = int(scores_df.index[dist_mask][0]) + 1
    total_n = len(scores_df)
    total_score = int(row['综合评分'])

    st.markdown(f"#### 🏢 {distributor}　<sub style='font-size:0.75rem;color:#888'>数据年份：{year}年</sub>",
                unsafe_allow_html=True)

    # ── 顶部指标卡 ──────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("综合评分", f"{total_score} / 100")
    c2.metric("评分排名", f"第 {rank} / {total_n}")
    c3.metric(f"{year}年台数", f"{int(row['台数']):,} 台")
    c4.metric("有效服务商", f"{int(row['有效服务商数'])} 家")
    flow_str = f"{int(row['流通天数中位'])} 天" if pd.notna(row['流通天数中位']) else "—"
    c5.metric("流通天数中位", flow_str)

    st.markdown("---")

    # ── 各维度评分条 ────────────────────────────
    st.markdown("#### 📊 各维度评分")

    dim_rows = [
        ("渠道覆盖宽度", row['D1_覆盖宽度'], 30,
         f"有效服务商 {int(row['有效服务商数'])} 家  ·  有效门槛：金额≥¥1,000 或台数≥5台"),
        ("货物流通效率", row['D2_流通效率'], 20,
         f"流通天数中位 {flow_str}  ·  从出库到上线，越短越好"),
        ("产品结构优化", row['D3_产品结构'], 20,
         f"单台均价 ¥{int(row['单台均价']):,}  ·  均价越高越好；市场均价约¥160-210"),
        ("服务激活率",   row['D4_服务激活'], 20,
         f"红包率 {row['红包率']:.1%}  ·  安装后触发红包的设备占比"),
        ("重点专项参与", row['D5_专项参与'], 10,
         f"专项率 {row['专项率']:.1%}  ·  参与三大重点专项的设备占比"),
    ]

    for dim_name, score, max_s, desc in dim_rows:
        pct = score / max_s if max_s > 0 else 0
        icon = "🟢" if pct >= 0.7 else "🟡" if pct >= 0.4 else "🔴"
        col_n, col_v, col_b = st.columns([2.5, 0.8, 3])
        with col_n:
            st.markdown(f"**{dim_name}**")
            st.caption(desc)
        with col_v:
            st.markdown(f"{icon} **{score}/{max_s}**")
        with col_b:
            st.progress(pct)

    st.markdown("---")

    # ── 服务商构成 + 产品结构（内嵌 tabs）────────────
    base = df[(df['出库客户名称'] == distributor) & (df['上线年份'] == year)].copy()
    if '数据剔除' in base.columns:
        base = base[base['数据剔除'].fillna('其他') == '其他']

    yr = str(year)
    tab_srv, tab_prod = st.tabs(["👥 服务商构成", "📦 产品结构"])

    with tab_srv:
        # 数据源切换到红包表：按所属一级客户(签约) == 当前代理商 过滤
        rp = load_redpack()
        if rp.empty:
            st.info("📦 安装红包记录表暂无数据，请到主页选择「安装红包记录」并上传 Excel。")
            srv_agg = pd.DataFrame()
        else:
            base_rp = rp[(rp['所属一级客户'] == distributor) & (rp['上线年份'] == year)].copy()
            if base_rp.empty:
                st.info(f"红包表里没有签约 **{distributor}**（{year} 年）的服务商记录")
                srv_agg = pd.DataFrame()
            else:
                st.caption(
                    f"数据源：安装红包记录 · 关联条件：所属一级客户 = {distributor} · "
                    f"金额 = 产品现有分销价"
                )
                srv_agg = (base_rp.groupby('上线客户名称')
                           .agg(台数=('产品序列号', 'count'),
                                金额=('产品现有分销价', 'sum'))
                           .reset_index()
                           .sort_values('金额', ascending=False))

        if not srv_agg.empty:
            srv_agg['有效服务商'] = (
                (srv_agg['金额'] >= EFFECTIVE_MIN_AMOUNT) |
                (srv_agg['台数'] >= EFFECTIVE_MIN_QTY)
            ).map({True: '✅', False: '—'})
            total_srv_amt = srv_agg['金额'].sum()
            srv_agg['金额占比'] = srv_agg['金额'].apply(
                lambda x: f"{x / total_srv_amt * 100:.1f}%" if total_srv_amt > 0 else "—")
            st.dataframe(
                srv_agg[['上线客户名称', '有效服务商', '台数', '金额', '金额占比']],
                use_container_width=True, hide_index=True, height=300,
                column_config={
                    '上线客户名称': st.column_config.TextColumn("服务商名称", width="medium"),
                    '有效服务商':   st.column_config.TextColumn("是否有效", width="small"),
                    '台数':         st.column_config.NumberColumn(f"{yr}台数", format="%d", width="small"),
                    '金额':         st.column_config.NumberColumn(f"{yr}金额(¥)", format="¥%d", width="small"),
                    '金额占比':     st.column_config.TextColumn("金额占比", width="small"),
                }
            )

    with tab_prod:
        prod_agg = (base.groupby('产品系列')
                    .agg(台数=('产品序列号', 'count'), 金额=('最新分销价', 'sum'))
                    .reset_index()
                    .sort_values('金额', ascending=False))
        total_prod_amt = prod_agg['金额'].sum()
        prod_agg['金额占比'] = prod_agg['金额'].apply(
            lambda x: f"{x / total_prod_amt * 100:.1f}%" if total_prod_amt > 0 else "—")
        prod_agg['单台均价'] = (prod_agg['金额'] / prod_agg['台数']).round(0).astype(int)
        st.dataframe(
            prod_agg,
            use_container_width=True, hide_index=True, height=300,
            column_config={
                '产品系列': st.column_config.TextColumn("产品系列", width="medium"),
                '台数':     st.column_config.NumberColumn(f"{yr}台数", format="%d", width="small"),
                '金额':     st.column_config.NumberColumn(f"{yr}金额(¥)", format="¥%d", width="small"),
                '金额占比': st.column_config.TextColumn("金额占比", width="small"),
                '单台均价': st.column_config.NumberColumn("单台均价(¥)", format="¥%d", width="small"),
            }
        )


def render_distributor_analysis(df: pd.DataFrame):
    """渲染代理商综合能力评分页面"""

    # ── 顶部：标题 + 年份选择 ──────────────────
    hdr_col, yr_col = st.columns([3, 1])
    with hdr_col:
        st.markdown("#### 🏆 代理商综合能力评分")
    with yr_col:
        sel_year = st.radio(
            "数据年份",
            options=[2026, 2025],
            format_func=lambda y: f"{y}年{'（截至当月）' if y == 2026 else '（全年）'}",
            index=0,          # 默认 2026
            horizontal=False,
            key="dist_year_radio",
            label_visibility="collapsed",
        )

    st.caption(
        f"基于 **{sel_year}年** 数据，从5个维度综合评估各代理商能力"
        f"{'（2026年为截至当前月的累计数据）' if sel_year == 2026 else ''}"
        "  ·  已排除电商/4G渠道"
    )

    # ── 提示缺失字段 ─────────────────────────
    missing_cols = [c for c in ['流通天数', '三大重点专项', '安装红包是否发放抽奖机会', '数据剔除']
                    if c not in df.columns]
    if missing_cols:
        st.warning(
            f"⚠️ 以下字段不在当前数据中，相关维度得分将为0：{', '.join(missing_cols)}。"
            "建议重新导入含完整字段的数据以获得准确评分。"
        )

    # ── 计算评分 ─────────────────────────────
    with st.spinner(f"正在计算 {sel_year} 年代理商评分..."):
        scores_df = compute_distributor_scores(df, year=sel_year)

    if scores_df.empty:
        st.info(f"没有 {sel_year} 年数据，无法计算代理商评分。请先导入数据。")
        return

    # ── 评级标签 ─────────────────────────────
    def grade_label(score):
        if score >= 70: return '⭐ 优秀'
        if score >= 50: return '✅ 良好'
        if score >= 30: return '🟡 一般'
        return '🔴 待提升'

    display = scores_df[[
        '代理商', '综合评分', '有效服务商数', '流通天数中位',
        'D1_覆盖宽度', 'D2_流通效率', 'D3_产品结构', 'D4_服务激活', 'D5_专项参与'
    ]].copy()
    display.insert(2, '评级', display['综合评分'].apply(grade_label))

    # 年份变化时强制重置表格选中状态
    score_event = st.dataframe(
        display,
        use_container_width=True, height=460, hide_index=True,
        selection_mode="single-row",
        key=f"dist_score_table_{sel_year}",   # key 含年份，切年时自动清空选中
        on_select="rerun",
        column_config={
            '代理商':      st.column_config.TextColumn("代理商", width="medium"),
            '综合评分':    st.column_config.ProgressColumn(
                "综合评分(100)", min_value=0, max_value=100, format="%d", width="medium"),
            '评级':        st.column_config.TextColumn("评级", width="small"),
            '有效服务商数': st.column_config.NumberColumn("有效服务商", format="%d", width="small"),
            '流通天数中位': st.column_config.NumberColumn("流通天数(天)", format="%d", width="small"),
            'D1_覆盖宽度': st.column_config.ProgressColumn(
                "覆盖(30)", min_value=0, max_value=30, format="%d", width="small"),
            'D2_流通效率': st.column_config.ProgressColumn(
                "效率(20)", min_value=0, max_value=20, format="%d", width="small"),
            'D3_产品结构': st.column_config.ProgressColumn(
                "产品(20)", min_value=0, max_value=20, format="%d", width="small"),
            'D4_服务激活': st.column_config.ProgressColumn(
                "激活(20)", min_value=0, max_value=20, format="%d", width="small"),
            'D5_专项参与': st.column_config.ProgressColumn(
                "专项(10)", min_value=0, max_value=10, format="%d", width="small"),
        }
    )

    # ── 读取选中代理商 ────────────────────────
    sel_dist = None
    if score_event and 'selection' in score_event:
        rows = score_event['selection'].get('rows', [])
        if rows and rows[0] < len(display):
            sel_dist = display.iloc[rows[0]]['代理商']

    # ── 操作按钮 ─────────────────────────────
    st.markdown("---")
    btn_col, status_col = st.columns([1, 3])
    with btn_col:
        if st.button(
            "🔍 查看代理商详情", type="primary",
            use_container_width=True,
            disabled=(sel_dist is None),
            help="点击上方表格中的代理商行后激活"
        ):
            if sel_dist:
                show_distributor_detail(df, sel_dist, scores_df, year=sel_year)
    with status_col:
        if sel_dist:
            r = scores_df[scores_df['代理商'] == sel_dist].iloc[0]
            st.info(
                f"已选择：**{sel_dist}**  ·  综合评分 **{int(r['综合评分'])}/100**  "
                f"·  有效服务商 {int(r['有效服务商数'])} 家  → 点击「查看代理商详情」"
            )
        else:
            st.caption("⚠️ 请点击上方表格中的代理商行进行选择，再点击「查看代理商详情」")

    # ── 评分维度说明 ──────────────────────────
    with st.expander("📖 评分维度说明"):
        st.markdown(f"""
**评分说明（满分100分）**

| 维度 | 权重 | 核心指标 | 评分逻辑 |
|------|:----:|---------|---------|
| 渠道覆盖宽度 | **30分** | 有效服务商数量 | ≥100家→30分；年度金额≥¥{EFFECTIVE_MIN_AMOUNT:,}或台数≥{EFFECTIVE_MIN_QTY}台算有效 |
| 货物流通效率 | **20分** | 出库→上线中位天数 | ≤15天→20分；越短越好；>400天→0分 |
| 产品结构优化 | **20分** | 单台均价 | ≥¥300→20分；反映代理商引导高端产品的能力（市场均价约¥160-210）|
| 服务激活率   | **20分** | 安装红包发放率 | ≥60%→20分；反映服务商激活质量（字段值='Y'算已激活）|
| 重点专项参与 | **10分** | 三大重点专项参与率 | ≥40%→10分；含夜视王/场景化/无线专项（非'其他'算参与）|

**数据范围**：已排除电商/4G渠道（数据剔除≠'其他'）；2026年为截至当前月的累计数据，与2025全年对比时可能偏低。
        """)


def main():
    require_auth()
    st.markdown("### 🏆 代理商能力评分（F7 健康度）")
    st.caption(
        "💡 **口径**：F7 评分基于 **🌐 全量感知**（product_flow 主表，含异地货来本地上线的全量销量），"
        "服务商关联部分用 **🎯 安装红包**（install_redpack）做销售链路明细 / 跨渠道分析。"
    )

    # ── 加载数据 ──────────────────────────
    df = load_data()
    if df.empty:
        st.warning("数据库中没有数据，请先到主页『📥 数据导入』上传 Excel 文件")
        try:
            st.page_link("app.py", label="↩ 返回主页导入数据", icon="📥")
        except Exception:
            pass
        return

    # 直接渲染代理商评分（独立 page，不再 Tab 切换）
    render_distributor_analysis(df)


def render_pivot_analysis(df: pd.DataFrame):
    """渲染透视表分析页面"""
    # ── 筛选条件 ──────────────────────────
    st.markdown("#### 🔍 筛选条件")
    customers = sorted(df['出库客户名称'].dropna().unique())
    cities = sorted(df['出货客户城市'].dropna().unique())
    online_cities = sorted(df['上线城市'].dropna().unique())

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        selected_customers = st.multiselect(
            "出库客户名称", customers, default=[],
            key="customers", label_visibility="collapsed", placeholder="按客户名称筛选")
    with col2:
        selected_cities = st.multiselect(
            "出货客户城市", cities, default=[],
            key="cities", label_visibility="collapsed", placeholder="按出货分销商城市多选")
    with col3:
        selected_online_cities = st.multiselect(
            "上线城市", online_cities, default=[],
            key="online_cities", label_visibility="collapsed", placeholder="按上线城市筛选")
    with col4:
        selected_months = st.multiselect(
            "月份（同比）", list(range(1, 13)), default=[],
            key="months", label_visibility="collapsed", placeholder="月份")

    filtered_df = filter_data(df, selected_customers, selected_cities, selected_online_cities, selected_months)
    if filtered_df.empty:
        st.info("筛选条件没有匹配到数据，请调整筛选条件")
        return

    # 同等过滤红包表（服务商构成 + 设备明细用）
    redpack_df = load_redpack()
    filtered_redpack = filter_redpack(
        redpack_df,
        customers=selected_customers,
        online_cities=selected_online_cities,
        months=selected_months,
    )

    if selected_months:
        st.caption(f"当前月份筛选：{sorted(selected_months)} 月（2025 和 2026 年同期对比）")

    # ── Session State 初始化 ──────────────
    for k in ['pending_district', 'pending_year', 'pending_customer',
              'active_district', 'active_year', 'active_customer']:
        if k not in st.session_state:
            st.session_state[k] = None

    # ── 透视表 ────────────────────────────
    st.markdown("#### 📈 区县分析")
    pivot_df = build_pivot_table(filtered_df)

    col_config = {
        "上线区县": st.column_config.TextColumn(width="small"),
        "2025 台数": st.column_config.NumberColumn(format="%d", width="small"),
        "2026 台数": st.column_config.NumberColumn(format="%d", width="small"),
        "2025 金额": st.column_config.NumberColumn(format="%d", width="small"),
        "2026 金额": st.column_config.NumberColumn(format="%d", width="small"),
    }

    col_left, col_right = st.columns([1, 1.3])

    # ----- 左：区县分析表 -----
    with col_left:
        st.caption("① 点击数字格子选择区县和年份")
        district_event = st.dataframe(
            style_pivot_table(pivot_df),
            use_container_width=True, height=400,
            hide_index=True,
            selection_mode=["single-row", "single-column"],
            key="district_table", on_select="rerun",
            column_config=col_config)

        district, year = parse_event(district_event, pivot_df, '上线区县')
        if district:
            if district != st.session_state.pending_district:
                st.session_state.pending_customer = None
            st.session_state.pending_district = district
        if year:
            st.session_state.pending_year = year

    # ----- 右：客户构成表 -----
    with col_right:
        pending_district = st.session_state.pending_district

        if pending_district:
            st.caption(
                f"📍 {pending_district} - 服务商构成（数据源：安装红包记录·按上线客户名称聚合）"
            )
            if filtered_redpack.empty:
                st.info("📦 安装红包记录表暂无数据，请到主页选择「安装红包记录」并上传 Excel。")
            customer_detail = get_customer_detail(filtered_redpack, pending_district)

            if not customer_detail.empty:
                customer_col_config = {
                    "客户名称": st.column_config.TextColumn(width="medium"),
                    "2025 台数": st.column_config.NumberColumn(format="%d", width="small"),
                    "2026 台数": st.column_config.NumberColumn(format="%d", width="small"),
                    "2025 金额": st.column_config.NumberColumn(format="%d", width="small"),
                    "2026 金额": st.column_config.NumberColumn(format="%d", width="small"),
                }
                customer_event = st.dataframe(
                    customer_detail,
                    use_container_width=True, height=400,
                    hide_index=True,
                    selection_mode=["single-row", "single-column"],
                    key="customer_table", on_select="rerun",
                    column_config=customer_col_config)

                customer, year = parse_event(customer_event, customer_detail, '客户名称')
                if customer and customer != '✅ 合计':
                    st.session_state.pending_customer = customer
                elif customer:
                    st.session_state.pending_customer = None
                if year:
                    st.session_state.pending_year = year
        else:
            st.caption("📍 客户构成")
            st.info("👈 先点击左侧区县表格，右侧显示该区县的客户构成")

    # ── 按钮区域 ─────────────────────────
    st.markdown("---")

    p_district = st.session_state.pending_district
    p_year     = st.session_state.pending_year
    p_customer = st.session_state.pending_customer

    btn1, btn2, btn3, status_col = st.columns([1, 1, 1, 2])

    with btn1:
        detail_disabled = not (p_district and p_year)
        clicked_detail = st.button(
            "🔍 查看设备明细",
            type="primary",
            use_container_width=True,
            disabled=detail_disabled,
            help="需先选择区县和年份（点左侧表格的数字列）"
        )

    with btn2:
        supplier_disabled = not (p_district and p_customer)
        clicked_supplier = st.button(
            "📦 采购行为分析",
            type="secondary",
            use_container_width=True,
            disabled=supplier_disabled,
            help="需先选择区县和客户（点击右侧表格的客户名称）"
        )

    with btn3:
        health_disabled = not p_district
        clicked_health = st.button(
            "🏥 客户结构分析",
            type="secondary",
            use_container_width=True,
            disabled=health_disabled,
            help="选择区县后可查看该区县的渠道客户健康度分析"
        )

    with status_col:
        if p_district and p_year:
            parts = [f"**{p_district}**", f"**{p_year} 年**"]
            if p_customer:
                parts.append(f"**{p_customer}**")
            st.info(f"当前选择：{'  ·  '.join(parts)} → 点击「采购行为分析」查看服务商采购行为，或点其他按钮")
        elif p_district:
            st.info(f"当前区县：**{p_district}** → 点击右侧表格选择客户，或点其他按钮查看设备明细/客户结构")
        else:
            missing = []
            if not p_district:
                missing.append("区县")
            if not p_year:
                missing.append("年份（点数字列）")
            st.caption(f"⚠️ 请先在上方表格中选择：{'、'.join(missing)}")

    # 按钮响应
    if clicked_detail:
        st.session_state.active_district = p_district
        st.session_state.active_year     = p_year
        st.session_state.active_customer = p_customer

    if clicked_supplier and p_customer:
        # 传入完整 df（不受月份筛选影响），服务商采购行为需要看全年数据
        show_supplier_analysis(df, p_customer)

    if clicked_health and p_district:
        # 传入完整 df（不受月份筛选影响），有效客户判定基于年度累计
        show_customer_health(df, p_district)

    # ── 设备明细展示 ──────────────────────
    a_district = st.session_state.active_district
    a_year     = st.session_state.active_year
    a_customer = st.session_state.active_customer

    detail_col = st.columns([1, 1.3])[0]
    with detail_col:
        if a_district and a_year:
            title_parts = [a_district, f"{a_year} 年"]
            if a_customer:
                title_parts.append(a_customer)
            st.caption(f"📋 设备明细 — {'  ·  '.join(title_parts)}")

            device_detail = get_device_detail(filtered_redpack, a_district, a_year, a_customer)

            if not device_detail.empty:
                st.caption(f"共 {len(device_detail)} 条记录")
                st.dataframe(device_detail, use_container_width=True, height=350, hide_index=True)
            else:
                st.info(f"{a_district} 在 {a_year} 年{'（' + a_customer + '）' if a_customer else ''} 没有数据")
        else:
            st.caption("📋 设备明细")
            st.info("👆 在上方表格中选好区县和年份，点击「查看设备明细」按钮")


main()
