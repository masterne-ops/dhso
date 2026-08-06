#!/usr/bin/env python3
"""🏠 门头建设 ROI 分析

数据源:provider_storefront_invest(服务商维度市场推广费用)
       × install_redpack_v(在投入年份内的红包扫码上线情况)

字段:
    投入客户编码 → 服务商唯一标识(对应 install_redpack.上线客户编码)
    投入金额_元 → 大华本年度补贴该服务商的门头建设费用
    当年上线金额_元 → 该服务商当年的红包上线金额(回款口径)
    是否激活 → 该服务商在当年是否实际产生上线交易

ROI = 当年上线金额 / 投入金额
"""
from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import load_redpack_shared  # noqa: E402
from _storefront_loader import (  # noqa: E402
    import_storefront_excel,
    load_storefront_invest,
)

st.set_page_config(page_title='门头建设 ROI', layout='wide')
require_auth()

st.markdown('### 🏠 门头建设 ROI 分析')
st.caption(
    '数据源:`provider_storefront_invest`(服务商门头投入)+ `install_redpack_v`(回款口径)。'
    '**ROI = 当年红包上线金额 / 投入金额**。'
)

# ────────────────────────────────────────────────
# Section 1:数据导入
# ────────────────────────────────────────────────

with st.expander('📥 数据导入(上传新的门头建设 Excel)', expanded=False):
    st.caption(
        '文件:`市场推广费用_服务商维度_YYYY.xlsx`(单行表头)。'
        '主键:(投入客户编码,投入年份)。年份从文件名推断。'
    )
    upl = st.file_uploader(
        '选择 Excel(.xlsx)',
        type=['xlsx', 'xls'],
        key='sf_upload',
    )
    if upl:
        c1, c2 = st.columns(2)
        mode = c1.radio(
            '导入模式',
            options=['merge', 'replace'],
            format_func=lambda x: {
                'merge': '🔀 合并(同年同编码覆盖,其余追加)— 推荐',
                'replace': '🔄 覆盖(先清空整张表再导)',
            }[x],
            horizontal=False,
        )
        year_in = c2.number_input(
            '投入年份(0 = 从文件名推断)',
            min_value=0, max_value=2100, value=0, step=1,
        )
        if st.button('💾 开始导入', type='primary', key='sf_import_btn'):
            with st.spinner('正在导入…'):
                try:
                    yr = int(year_in) if year_in else None
                    r = import_storefront_excel(upl, mode=mode, invest_year=yr)
                    st.success(
                        f'✅ 导入成功:新增 {r["inserted"]:,} / '
                        f'更新 {r["updated"]:,} / 库内总数 {r["total"]:,}(年份 = {r["year"]})'
                    )
                    load_storefront_invest.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f'❌ 导入失败:{e}')
                    import traceback
                    with st.expander('完整错误'):
                        st.code(traceback.format_exc())


# ────────────────────────────────────────────────
# Section 2:数据加载
# ────────────────────────────────────────────────

sf = load_storefront_invest()
if sf.empty:
    st.warning('📦 门头投入数据为空,请先在上方上传 Excel 文件。')
    st.stop()

rp = load_redpack_shared()


# ────────────────────────────────────────────────
# Section 3:筛选(侧边栏)
# ────────────────────────────────────────────────

with st.sidebar:
    st.header('筛选')

    year_options = sorted(sf['投入年份'].dropna().unique().astype(int).tolist())
    sel_years = st.multiselect(
        '投入年份(不选 = 全部)',
        year_options, default=year_options,
    )

    city_options = sorted(sf['地市'].dropna().astype(str).unique().tolist())
    sel_cities = st.multiselect(
        '地市(不选 = 全部)',
        city_options, default=[],
    )

    dealer_options = sorted(sf['所属一级名称'].dropna().astype(str).unique().tolist())
    sel_dealers = st.multiselect(
        '签约代理商(不选 = 全部)',
        dealer_options, default=[],
    )

    activated_filter = st.radio(
        '激活状态',
        options=['全部', '仅已激活(Y)', '仅未激活(N)'],
        index=0,
        horizontal=False,
    )

    st.divider()
    st.caption(f'库内总投入记录:{len(sf):,}')
    st.caption(f'年份覆盖:{", ".join(map(str, year_options))}')


# 应用筛选
df = sf.copy()
if sel_years:
    df = df[df['投入年份'].isin(sel_years)]
if sel_cities:
    df = df[df['地市'].isin(sel_cities)]
if sel_dealers:
    df = df[df['所属一级名称'].isin(sel_dealers)]
if activated_filter == '仅已激活(Y)':
    df = df[df['已激活']]
elif activated_filter == '仅未激活(N)':
    df = df[~df['已激活']]

if df.empty:
    st.warning('当前筛选下无数据')
    st.stop()


# ────────────────────────────────────────────────
# Section 4:核心指标
# ────────────────────────────────────────────────

n_total = len(df)
n_active = int(df['已激活'].sum())
total_invest = df['投入金额_元'].sum()
total_return = df['当年上线金额_元'].sum()
roi_overall = (total_return / total_invest) if total_invest > 0 else None
breakeven = int((df['ROI'] >= 1).sum())
neg_invest = int((df['投入金额_元'] > 0).sum() - breakeven)
zero_return = int((df['当年上线金额_元'] == 0).sum())

st.markdown('#### 📊 核心指标')
c = st.columns(8)
c[0].metric('门头服务商', f'{n_total:,}', help='投入门头费用的服务商总数')
c[1].metric('已激活', f'{n_active:,}',
            delta=f'{n_active / max(n_total, 1) * 100:.1f}%',
            delta_color='off', help='当年产生上线交易的服务商')
c[2].metric('总投入', f'¥{total_invest/10000:.1f}万',
            help='本年度门头建设补贴总额')
c[3].metric('总回款', f'¥{total_return/10000:.1f}万',
            help='当年红包上线金额合计')
c[4].metric('整体 ROI',
            f'{roi_overall:.2f}' if roi_overall else '—',
            help='= 总回款 / 总投入(>1 = 整体盈利)')
c[5].metric('ROI ≥ 1', f'{breakeven:,}',
            delta=f'{breakeven / max(n_total, 1) * 100:.1f}%',
            delta_color='off', help='回款 ≥ 投入的服务商数')
c[6].metric('ROI < 1', f'{neg_invest:,}', help='回款不抵投入的服务商数')
c[7].metric('零回款', f'{zero_return:,}',
            help='当年完全没有红包上线的服务商数')


# ────────────────────────────────────────────────
# Section 5:ROI 分布(直方图 / 桶)
# ────────────────────────────────────────────────

st.divider()
st.markdown('#### 📈 ROI 分布(桶)')
st.caption('查看每个 ROI 区间里有多少服务商。ROI ≥ 5 = 高回报;ROI < 1 = 不达标;0 = 完全没产出。')

bins = [-0.01, 0.001, 0.5, 1.0, 2.0, 5.0, 10.0, 1e9]
labels = ['0(零回款)', '0~0.5', '0.5~1', '1~2', '2~5', '5~10', '≥10']
df_roi = df.copy()
df_roi['ROI填'] = df_roi['ROI'].fillna(0)
df_roi['ROI桶'] = pd.cut(df_roi['ROI填'], bins=bins, labels=labels, include_lowest=True)

bucket = df_roi.groupby('ROI桶', observed=False).agg(
    服务商数=('投入客户编码', 'count'),
    投入合计=('投入金额_元', 'sum'),
    回款合计=('当年上线金额_元', 'sum'),
).reset_index()
bucket['占比'] = (bucket['服务商数'] / bucket['服务商数'].sum() * 100).round(1).astype(str) + '%'
bucket['投入合计'] = bucket['投入合计'].apply(lambda x: f'¥{x/10000:.2f}万')
bucket['回款合计'] = bucket['回款合计'].apply(lambda x: f'¥{x/10000:.2f}万')

st.dataframe(bucket, use_container_width=True, hide_index=True)


# ────────────────────────────────────────────────
# Section 6:地市维度汇总
# ────────────────────────────────────────────────

st.divider()
st.markdown('#### 🌆 地市维度 ROI')

city_agg = df.groupby('地市').agg(
    服务商数=('投入客户编码', 'count'),
    已激活=('已激活', 'sum'),
    投入金额=('投入金额_元', 'sum'),
    回款金额=('当年上线金额_元', 'sum'),
).reset_index()
city_agg['激活率'] = (city_agg['已激活'] / city_agg['服务商数'] * 100).round(1)
city_agg['ROI'] = (city_agg['回款金额'] / city_agg['投入金额']).round(2)
city_agg = city_agg.sort_values('投入金额', ascending=False)

st.dataframe(
    city_agg.assign(
        投入金额=lambda d: d['投入金额'].apply(lambda x: f'¥{x:,.0f}'),
        回款金额=lambda d: d['回款金额'].apply(lambda x: f'¥{x:,.0f}'),
        激活率=lambda d: d['激活率'].astype(str) + '%',
    )[['地市', '服务商数', '已激活', '激活率', '投入金额', '回款金额', 'ROI']],
    use_container_width=True, hide_index=True,
)


# ────────────────────────────────────────────────
# Section 7:签约代理商维度汇总
# ────────────────────────────────────────────────

st.divider()
st.markdown('#### 🏢 签约代理商维度 ROI')
st.caption('看每个代理商签约的「门头服务商」整体投入 / 回款 / ROI。')

dealer_agg = df.groupby('所属一级名称').agg(
    服务商数=('投入客户编码', 'count'),
    已激活=('已激活', 'sum'),
    投入金额=('投入金额_元', 'sum'),
    回款金额=('当年上线金额_元', 'sum'),
).reset_index()
dealer_agg = dealer_agg[dealer_agg['服务商数'] >= 1]
dealer_agg['激活率'] = (dealer_agg['已激活'] / dealer_agg['服务商数'] * 100).round(1)
dealer_agg['ROI'] = (dealer_agg['回款金额'] / dealer_agg['投入金额']).round(2)
dealer_agg = dealer_agg.sort_values('投入金额', ascending=False)

st.dataframe(
    dealer_agg.assign(
        投入金额=lambda d: d['投入金额'].apply(lambda x: f'¥{x:,.0f}'),
        回款金额=lambda d: d['回款金额'].apply(lambda x: f'¥{x:,.0f}'),
        激活率=lambda d: d['激活率'].astype(str) + '%',
    )[['所属一级名称', '服务商数', '已激活', '激活率', '投入金额', '回款金额', 'ROI']],
    use_container_width=True, hide_index=True, height=400,
)


# ────────────────────────────────────────────────
# Section 8:服务商明细(可排序 / 下载)
# ────────────────────────────────────────────────

st.divider()
st.markdown('#### 📋 服务商门头投入明细')

sort_key = st.radio(
    '排序',
    options=['ROI 从高到低(看明星)', 'ROI 从低到高(看问题)',
             '投入从高到低', '回款从高到低'],
    horizontal=True, index=0,
)
sort_map = {
    'ROI 从高到低(看明星)': ('ROI', False),
    'ROI 从低到高(看问题)': ('ROI', True),
    '投入从高到低': ('投入金额_元', False),
    '回款从高到低': ('当年上线金额_元', False),
}
sort_col, asc = sort_map[sort_key]

show = df.copy()
show = show.sort_values(sort_col, ascending=asc, na_position='last')
show['激活'] = show['已激活'].map({True: '✅', False: '❌'})
show['投入'] = show['投入金额_元'].apply(lambda x: f'¥{x:,.0f}')
show['回款'] = show['当年上线金额_元'].apply(lambda x: f'¥{x:,.0f}')
show['ROI显示'] = show['ROI'].apply(lambda x: f'{x:.2f}' if pd.notna(x) else '—')

st.dataframe(
    show[['投入年份', '地市', '投入客户名称', '所属一级名称',
          '激活', '投入', '回款', 'ROI显示', '投入客户编码']].rename(
        columns={'所属一级名称': '签约代理商', 'ROI显示': 'ROI', '投入客户编码': '编码'}
    ),
    use_container_width=True, hide_index=True, height=500,
    column_config={
        '投入客户名称': st.column_config.TextColumn(width='large'),
        '签约代理商': st.column_config.TextColumn(width='medium'),
    },
)

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine='openpyxl') as w:
    out_xlsx = df.copy()
    out_xlsx['ROI'] = out_xlsx['ROI'].round(3)
    out_xlsx[['投入年份', '地市', '投入客户编码', '投入客户名称',
              '所属一级名称', '投入金额_元', '当年上线金额_元', 'ROI', '是否激活']].to_excel(
        w, sheet_name='门头投入明细', index=False,
    )
st.download_button(
    f'📥 导出门头投入明细({len(df):,} 家)',
    data=buf.getvalue(),
    file_name=f'门头建设ROI明细_{datetime.now().strftime("%Y%m%d")}.xlsx',
    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
)


# ────────────────────────────────────────────────
# Section 9:零回款 / 未激活 预警名单
# ────────────────────────────────────────────────

st.divider()
st.markdown('#### 🚨 预警名单(投入了但当年完全没产生回款)')
st.caption('门头钱花了 + 当年红包扫码上线 = 0 → 重点排查。')

alert = df[(df['投入金额_元'] > 0) & (df['当年上线金额_元'] == 0)].copy()
if alert.empty:
    st.success('当前筛选下没有预警名单(挺好)')
else:
    alert = alert.sort_values('投入金额_元', ascending=False)
    alert_show = alert[['投入年份', '地市', '投入客户名称', '所属一级名称',
                        '投入金额_元', '是否激活', '投入客户编码']].copy()
    alert_show['投入金额_元'] = alert_show['投入金额_元'].apply(lambda x: f'¥{x:,.0f}')
    alert_show = alert_show.rename(columns={
        '所属一级名称': '签约代理商',
        '投入金额_元': '投入金额',
        '投入客户编码': '编码',
    })
    st.dataframe(alert_show, use_container_width=True, hide_index=True, height=400)
    st.caption(f'共 {len(alert)} 家零回款 / 投入合计 ¥{alert["投入金额_元"].sum():,.0f}')
