#!/usr/bin/env python3
"""
🎁 红包排行榜
- 服务商维度（按上线客户编码去重，名字取上线客户名称）
- 筛选项：年份 / 月份 / 地市 / 区县 / 归属代理商
- 默认按"总红包金额"从高到低排
- 字段：服务商名称 / 总红包数量 / 总红包金额 / 单个红包最大 / 平均红包 / 夜视王红包比例
- 顶部一行汇总指标
- 一键导出 Excel
"""

import io
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# 引入共享加载
sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import load_redpack_shared  # noqa: E402

require_auth()
st.markdown("### 🎁 红包排行榜（服务商维度）")
st.caption(
    "数据源：`install_redpack` · 中奖金额 > 0 视为有效红包 · "
    "默认按总红包金额从高到低排"
)


# ──────────────────────────────────────────
# 数据
# ──────────────────────────────────────────

rp_full = load_redpack_shared()
if rp_full.empty:
    st.warning("📦 安装红包记录表暂无数据，请到主页『📥 数据导入』选择「安装红包记录」并上传 Excel。")
    st.stop()


# ──────────────────────────────────────────
# 筛选项
# ──────────────────────────────────────────

st.markdown("#### 🔍 筛选")

# ⚠️ 修复：去掉硬编码上界 2030，避免数据扩展后被静默截断
years = sorted(int(y) for y in rp_full['上线年份'].dropna().unique() if y >= 2024)
city_options = sorted(rp_full['安装城市'].dropna().astype(str).str.strip().unique().tolist())
city_options = [c for c in city_options if c]
dealer_options = sorted(rp_full['所属一级客户'].dropna().astype(str).str.strip().unique().tolist())
dealer_options = [d for d in dealer_options if d]

f1, f2, f3, f4, f5 = st.columns([1, 1, 1.5, 1.5, 2])

with f1:
    year_pick = st.multiselect("年份", options=years, default=[], key="rb_year")
with f2:
    month_pick = st.multiselect("月份", options=list(range(1, 13)), default=[], key="rb_month")
with f3:
    picked_cities = st.multiselect(
        "地市（设备安装地）", options=city_options, default=[], key="rb_city")

# 区县候选根据已选地市动态过滤
if picked_cities:
    district_pool = rp_full[rp_full['安装城市'].astype(str).str.strip().isin(picked_cities)]
else:
    district_pool = rp_full
district_options = sorted(
    district_pool['安装区县'].dropna().astype(str).str.strip().unique().tolist()
)
district_options = [d for d in district_options if d]

with f4:
    picked_districts = st.multiselect(
        "区县", options=district_options, default=[], key="rb_district",
        help="跟着地市动态收窄；不选 = 该地市下全部",
    )
with f5:
    picked_dealers = st.multiselect(
        "归属代理商", options=dealer_options, default=[], key="rb_dealer")


# 应用筛选（df_all = 全量含未中奖；df = 仅中奖记录，用来算红包指标）
df_all = rp_full.copy()
if year_pick:
    df_all = df_all[df_all['上线年份'].isin(year_pick)]
if month_pick:
    df_all = df_all[df_all['上线月份'].isin(month_pick)]
if picked_cities:
    df_all = df_all[df_all['安装城市'].astype(str).str.strip().isin(picked_cities)]
if picked_districts:
    df_all = df_all[df_all['安装区县'].astype(str).str.strip().isin(picked_districts)]
if picked_dealers:
    df_all = df_all[df_all['所属一级客户'].astype(str).str.strip().isin(picked_dealers)]

# 仅中奖部分用于算红包指标
df = df_all[df_all['中奖金额'] > 0].copy()


# ──────────────────────────────────────────
# 顶部汇总
# ──────────────────────────────────────────

st.markdown("#### 📊 汇总")

if df.empty:
    st.warning("当前筛选条件下没有红包记录，请调整筛选项。")
    st.stop()

n_pack = len(df)
total_redpack = float(df['中奖金额'].sum())
total_so_amt = float(df_all['产品现有分销价'].sum())  # 全量上线金额（含未中奖）
n_provider = df['上线客户编码'].nunique()
avg_amt = total_redpack / n_pack if n_pack > 0 else 0
yeshi_mask = df['产品子系列-新'].fillna('').astype(str).str.contains('夜视王', na=False)
n_yeshi = int(yeshi_mask.sum())
yeshi_pct = n_yeshi / n_pack if n_pack > 0 else 0
red_to_so = total_redpack / total_so_amt if total_so_amt > 0 else 0

# 第 1 行：6 个汇总
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("红包总数", f"{n_pack:,}")
c2.metric("红包总金额", f"¥{total_redpack:,.0f}")
c3.metric("总上线金额", f"¥{total_so_amt:,.0f}",
          help="筛选范围内所有上线设备的产品现有分销价之和（含没中奖的）")
c4.metric("红包/上线金额比", f"{red_to_so * 100:.3f}%",
          help=f"红包总金额 ÷ 总上线金额 = {total_redpack:,.0f} ÷ {total_so_amt:,.0f}")
c5.metric("覆盖服务商数", f"{n_provider:,}")
c6.metric("平均单个红包", f"¥{avg_amt:.2f}")

# 第 2 行：夜视王（单独一行避免拥挤）
st.caption(
    f"🌙 夜视王红包占比：**{yeshi_pct * 100:.1f}%**（{n_yeshi:,} / {n_pack:,}）"
)


# ──────────────────────────────────────────
# 排行榜
# ──────────────────────────────────────────

st.markdown("#### 🏆 服务商红包排行榜")

# 红包指标(基于中奖记录,按客户编码去重)
# ⚠️ 修复:同一客户编码可能在 install_redpack 里有多个名字(如"张小军 义乌"vs"张小军义乌"
#         空格差异),双键 group by 会产生重复行 → 改单键聚合,名字取 mode
agg = df.groupby('上线客户编码').agg(
    上线客户名称=('上线客户名称',
                  lambda x: x.dropna().mode().iloc[0]
                  if len(x.dropna().mode()) > 0 else None),
    总红包数量=('中奖金额', 'count'),
    总红包金额=('中奖金额', 'sum'),
    单个红包最大=('中奖金额', 'max'),
    平均红包=('中奖金额', 'mean'),
    签约代理商=('所属一级客户',
                lambda x: x.dropna().mode().iloc[0]
                if len(x.dropna().mode()) > 0 else None),
).reset_index()

# 总上线金额 / 总上线台数（基于全量记录，含没中奖）
so_grp = df_all.groupby('上线客户编码').agg(
    总上线台数=('产品序列号', 'count'),
    总上线金额=('产品现有分销价', 'sum'),
).reset_index()
agg = agg.merge(so_grp, on='上线客户编码', how='left')
agg['总上线金额'] = agg['总上线金额'].fillna(0)
agg['总上线台数'] = agg['总上线台数'].fillna(0).astype(int)

# 红包/上线金额比（关键指标：红包成本占销售额的比例，越低越省钱）
agg['红包_上线金额比'] = np.where(
    agg['总上线金额'] > 0,
    agg['总红包金额'] / agg['总上线金额'],
    0,
)

# 夜视王红包数
yeshi_grp = (
    df[yeshi_mask]
    .groupby('上线客户编码').size()
    .rename('夜视王红包数')
    .reset_index()
)
agg = agg.merge(yeshi_grp, on='上线客户编码', how='left')
agg['夜视王红包数'] = agg['夜视王红包数'].fillna(0).astype(int)
agg['夜视王红包比例'] = np.where(
    agg['总红包数量'] > 0,
    agg['夜视王红包数'] / agg['总红包数量'],
    0,
)

# 排序
agg = agg.sort_values('总红包金额', ascending=False).reset_index(drop=True)
agg.insert(0, '排名', np.arange(1, len(agg) + 1))

# 展示版（格式化金额/比例）
show = agg.copy()
show['总红包金额'] = show['总红包金额'].round(2)
show['单个红包最大'] = show['单个红包最大'].round(2)
show['平均红包'] = show['平均红包'].round(2)
show['总上线金额'] = show['总上线金额'].round(0)
show['夜视王红包比例'] = show['夜视王红包比例'].apply(lambda v: f"{v * 100:.1f}%")
# 红包/上线金额比：千分位数显示更直观（一般 0.0X%~0.X%）
show['红包_上线金额比'] = show['红包_上线金额比'].apply(lambda v: f"{v * 100:.3f}%")

# 列顺序：先红包指标，再"上线"参照，再"红包/上线比"，最后夜视王
preferred = [
    '排名',
    '上线客户名称', '签约代理商',
    '总红包数量', '总红包金额',
    '单个红包最大', '平均红包',
    '总上线台数', '总上线金额',
    '红包_上线金额比',
    '夜视王红包数', '夜视王红包比例',
    '上线客户编码',
]
cols_show = [c for c in preferred if c in show.columns]

st.dataframe(
    show[cols_show].rename(columns={
        '上线客户名称': '服务商名称',
        '上线客户编码': '服务商编码',
        '红包_上线金额比': '红包/上线金额比',
    }),
    use_container_width=True,
    hide_index=True,
    height=600,
    column_config={
        '排名': st.column_config.NumberColumn(width="small"),
        '服务商名称': st.column_config.TextColumn(width="medium"),
        '签约代理商': st.column_config.TextColumn(width="medium"),
        '总红包数量': st.column_config.NumberColumn(format="%d", width="small"),
        '总红包金额': st.column_config.NumberColumn(format="¥%.2f", width="small"),
        '单个红包最大': st.column_config.NumberColumn(format="¥%.2f", width="small"),
        '平均红包': st.column_config.NumberColumn(format="¥%.2f", width="small"),
        '总上线台数': st.column_config.NumberColumn(format="%d", width="small"),
        '总上线金额': st.column_config.NumberColumn(format="¥%d", width="small"),
        '红包/上线金额比': st.column_config.TextColumn("红包/上线", width="small",
            help="红包总金额 ÷ 总上线金额（红包成本占销售额比，越低越省）"),
        '夜视王红包数': st.column_config.NumberColumn(format="%d", width="small"),
        '夜视王红包比例': st.column_config.TextColumn(width="small"),
    },
)


# ──────────────────────────────────────────
# 导出 Excel
# ──────────────────────────────────────────

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine='openpyxl') as w:
    out = agg.rename(columns={
        '上线客户名称': '服务商名称',
        '上线客户编码': '服务商编码',
        '红包_上线金额比': '红包/上线金额比',
    })
    out['夜视王红包比例'] = out['夜视王红包比例'].apply(lambda v: round(v, 4))
    out['红包/上线金额比'] = out['红包/上线金额比'].apply(lambda v: round(v, 5))
    out[['排名', '服务商名称', '签约代理商',
         '总红包数量', '总红包金额', '单个红包最大', '平均红包',
         '总上线台数', '总上线金额', '红包/上线金额比',
         '夜视王红包数', '夜视王红包比例', '服务商编码']].to_excel(
        w, sheet_name='红包排行榜', index=False)

stamp = datetime.now().strftime('%Y%m%d')
fname = f"红包排行榜_服务商_{stamp}.xlsx"
st.download_button(
    f"📥 导出 Excel（{len(agg):,} 个服务商）",
    data=buf.getvalue(),
    file_name=fname,
    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
)


# ──────────────────────────────────────────
# 其他维度的红包明细
# ──────────────────────────────────────────

st.markdown("---")
st.markdown("#### 📊 其他维度红包明细")
st.caption("跟服务商表同样的指标，按地市 / 区县 / 归属代理商聚合。")


def aggregate_redpack(df_won, df_total, group_cols):
    """通用聚合：红包指标 + 上线参照 + 红包/上线比 + 夜视王"""
    a = df_won.groupby(group_cols, dropna=False).agg(
        总红包数量=('中奖金额', 'count'),
        总红包金额=('中奖金额', 'sum'),
        单个红包最大=('中奖金额', 'max'),
        平均红包=('中奖金额', 'mean'),
    ).reset_index()

    so = df_total.groupby(group_cols, dropna=False).agg(
        总上线台数=('产品序列号', 'count'),
        总上线金额=('产品现有分销价', 'sum'),
    ).reset_index()
    a = a.merge(so, on=group_cols, how='left')
    a['总上线台数'] = a['总上线台数'].fillna(0).astype(int)
    a['总上线金额'] = a['总上线金额'].fillna(0)
    a['红包_上线金额比'] = np.where(
        a['总上线金额'] > 0,
        a['总红包金额'] / a['总上线金额'],
        0,
    )

    # 夜视王
    yeshi = df_won[
        df_won['产品子系列-新'].fillna('').astype(str).str.contains('夜视王', na=False)
    ]
    if not yeshi.empty:
        yh = yeshi.groupby(group_cols, dropna=False).size().rename('夜视王红包数').reset_index()
        a = a.merge(yh, on=group_cols, how='left')
    else:
        a['夜视王红包数'] = 0
    a['夜视王红包数'] = a['夜视王红包数'].fillna(0).astype(int)
    a['夜视王红包比例'] = np.where(
        a['总红包数量'] > 0,
        a['夜视王红包数'] / a['总红包数量'],
        0,
    )

    a = a.sort_values('总红包金额', ascending=False).reset_index(drop=True)
    a.insert(0, '排名', np.arange(1, len(a) + 1))
    return a


def render_rank_table(agg_df, name_label_map, sheet_name, key_prefix):
    """渲染排行榜表 + 导出按钮
    name_label_map: dict 形如 {'安装城市': '地市'} 用于显示重命名
    """
    if agg_df.empty:
        st.info("当前筛选下无数据")
        return

    show = agg_df.copy()
    show['总红包金额'] = show['总红包金额'].round(2)
    show['单个红包最大'] = show['单个红包最大'].round(2)
    show['平均红包'] = show['平均红包'].round(2)
    show['总上线金额'] = show['总上线金额'].round(0)
    show['夜视王红包比例'] = show['夜视王红包比例'].apply(lambda v: f"{v * 100:.1f}%")
    show['红包_上线金额比'] = show['红包_上线金额比'].apply(lambda v: f"{v * 100:.3f}%")
    show = show.rename(columns={**name_label_map, '红包_上线金额比': '红包/上线金额比'})

    name_keys = list(name_label_map.values())
    cols = (['排名'] + name_keys + [
        '总红包数量', '总红包金额', '单个红包最大', '平均红包',
        '总上线台数', '总上线金额', '红包/上线金额比',
        '夜视王红包数', '夜视王红包比例',
    ])
    cols = [c for c in cols if c in show.columns]

    st.dataframe(
        show[cols],
        use_container_width=True, hide_index=True, height=400,
        column_config={
            '排名': st.column_config.NumberColumn(width="small"),
            '总红包数量': st.column_config.NumberColumn(format="%d", width="small"),
            '总红包金额': st.column_config.NumberColumn(format="¥%.2f", width="small"),
            '单个红包最大': st.column_config.NumberColumn(format="¥%.2f", width="small"),
            '平均红包': st.column_config.NumberColumn(format="¥%.2f", width="small"),
            '总上线台数': st.column_config.NumberColumn(format="%d", width="small"),
            '总上线金额': st.column_config.NumberColumn(format="¥%d", width="small"),
            '红包/上线金额比': st.column_config.TextColumn("红包/上线", width="small",
                help="红包总金额 ÷ 总上线金额"),
            '夜视王红包数': st.column_config.NumberColumn(format="%d", width="small"),
            '夜视王红包比例': st.column_config.TextColumn(width="small"),
        },
    )

    # 导出
    buf2 = io.BytesIO()
    with pd.ExcelWriter(buf2, engine='openpyxl') as w:
        out = agg_df.rename(columns={**name_label_map, '红包_上线金额比': '红包/上线金额比'})
        out['红包/上线金额比'] = out['红包/上线金额比'].apply(lambda v: round(v, 5))
        out['夜视王红包比例'] = out['夜视王红包比例'].apply(lambda v: round(v, 4))
        out.to_excel(w, sheet_name=sheet_name, index=False)
    stamp2 = datetime.now().strftime('%Y%m%d')
    st.download_button(
        f"📥 导出 Excel（{len(agg_df):,} 项）",
        data=buf2.getvalue(),
        file_name=f"红包排行榜_{sheet_name}_{stamp2}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        key=f"{key_prefix}_dl",
    )


def _local_geo_filter(key_prefix):
    """每个 Tab 顶部加省份/地市筛选；返回 (df_local, df_all_local)"""
    prov_options = (
        sorted(df['安装省份'].dropna().astype(str).str.strip().unique().tolist())
        if '安装省份' in df.columns else []
    )
    prov_options = [p for p in prov_options if p]

    cols = st.columns(2)
    with cols[0]:
        picked_prov = st.multiselect(
            "省份筛选（局部）",
            options=prov_options,
            default=[],
            key=f"{key_prefix}_prov",
            help=f"不选 = 全部，共 {len(prov_options)} 个省份。仅作用于此 Tab，不影响顶部筛选",
        )
    # 地市候选随省份联动
    if picked_prov and '安装省份' in df.columns:
        city_pool = df[df['安装省份'].astype(str).str.strip().isin(picked_prov)]
    else:
        city_pool = df
    city_local_options = sorted(
        city_pool['安装城市'].dropna().astype(str).str.strip().unique().tolist()
    )
    city_local_options = [c for c in city_local_options if c]
    with cols[1]:
        picked_city = st.multiselect(
            "地市筛选（局部）",
            options=city_local_options,
            default=[],
            key=f"{key_prefix}_city2",
            help=f"不选 = 上方省份内全部，共 {len(city_local_options)} 个地市",
        )

    d_local = df
    d_all_local = df_all
    if picked_prov and '安装省份' in df.columns:
        d_local = d_local[d_local['安装省份'].astype(str).str.strip().isin(picked_prov)]
        d_all_local = d_all_local[d_all_local['安装省份'].astype(str).str.strip().isin(picked_prov)]
    if picked_city:
        d_local = d_local[d_local['安装城市'].astype(str).str.strip().isin(picked_city)]
        d_all_local = d_all_local[d_all_local['安装城市'].astype(str).str.strip().isin(picked_city)]

    return d_local, d_all_local


tab_city, tab_dist, tab_dealer = st.tabs([
    "🏙️ 地市维度",
    "🗺️ 区县维度",
    "🏛️ 归属代理商维度",
])

with tab_city:
    d, d_all = _local_geo_filter('city_rank')
    if d.empty:
        st.info("当前局部筛选下无数据")
    else:
        agg_city = aggregate_redpack(d, d_all, ['安装城市'])
        render_rank_table(agg_city, {'安装城市': '地市'}, '地市', 'city_rank')

with tab_dist:
    if '安装区县_全' in df.columns and '安装区县_全' in df_all.columns:
        d, d_all = _local_geo_filter('district_rank')
        if d.empty:
            st.info("当前局部筛选下无数据")
        else:
            agg_dist = aggregate_redpack(d, d_all, ['安装区县_全'])
            render_rank_table(agg_dist, {'安装区县_全': '区县（城市/区县）'},
                               '区县', 'district_rank')
    else:
        st.info("数据库中未派生「安装区县_全」字段")

with tab_dealer:
    d, d_all = _local_geo_filter('dealer_rank')
    if d.empty:
        st.info("当前局部筛选下无数据")
    else:
        agg_dealer = aggregate_redpack(d, d_all, ['所属一级客户'])
        render_rank_table(agg_dealer, {'所属一级客户': '归属代理商'},
                           '代理商', 'dealer_rank')
