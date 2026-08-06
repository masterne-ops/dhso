"""📦 库存进销存(仅 admin)

数据源:inventory_snapshot(序列号级盘库快照,严口径)
4 tab:库存总览 / 呆滞品攻坚 / 库存周转 / 库存趋势
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from _auth import require_auth, current_user, is_admin  # noqa: E402
from _inventory_loader import (  # noqa: E402
    list_quarters, get_overview, get_dazhi, list_dazhi_detail,
    get_turnover, get_trend,
)

require_auth()
if not is_admin():
    st.error('🔒 仅 admin 可访问此页面')
    st.stop()

st.markdown('### 📦 库存进销存')
st.caption('数据源:代理商盘库明细(序列号级)· 严口径(无上线时间=真在库)· 全浙江')

quarters = list_quarters()
if not quarters:
    st.error('inventory_snapshot 表无数据')
    st.stop()

q = st.selectbox('📅 盘库季度', quarters, index=0, key='inv_q')


@st.cache_data(ttl=600, show_spinner='正在汇总库存数据…')
def _overview(quarter): return get_overview(quarter)
@st.cache_data(ttl=600, show_spinner=False)
def _dazhi(quarter, th): return get_dazhi(quarter, th)
@st.cache_data(ttl=600, show_spinner=False)
def _turnover(quarter): return get_turnover(quarter)
@st.cache_data(ttl=600, show_spinner=False)
def _trend(): return get_trend()


tab1, tab2, tab3, tab4 = st.tabs(['📦 库存总览', '🐌 呆滞品攻坚', '🔄 库存周转', '📈 库存趋势'])

# ═══════════════════════ Tab 1 ═══════════════════════
with tab1:
    d = _overview(q)
    k = d['kpi']
    c = st.columns(4)
    c[0].metric('在库台数', f"{k['在库台数']:,}")
    c[1].metric('在库货值', f"{k['在库货值_万']:,.1f} 万")
    c[2].metric('涉及代理商', f"{k['代理商数']}")
    c[3].metric('在库 SKU', f"{k['SKU数']:,}")

    st.divider()
    cc = st.columns(2)
    with cc[0]:
        st.markdown('**🎯 三大专项在库**')
        if d['focus']:
            st.dataframe(pd.DataFrame(d['focus']), use_container_width=True, hide_index=True)
    with cc[1]:
        st.markdown('**📊 库龄分布**')
        df_age = pd.DataFrame(d['age'])
        if not df_age.empty:
            st.dataframe(df_age, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown('**📦 品类分布(产品二级 Top 15)**')
    st.dataframe(pd.DataFrame(d['category']), use_container_width=True, hide_index=True,
                 height=min(400, 40 + 35 * len(d['category'])))

    st.divider()
    st.markdown(f"**🏢 分代理商库存({k['代理商数']} 家)**")
    df_dealer = pd.DataFrame(d['dealer'])
    if not df_dealer.empty:
        df_dealer['呆滞占比'] = (df_dealer['呆滞台数'] / df_dealer['在库台数'] * 100).round(1).astype(str) + '%'
        st.dataframe(df_dealer, use_container_width=True, hide_index=True,
                     height=min(500, 40 + 35 * len(df_dealer)))

# ═══════════════════════ Tab 2 ═══════════════════════
with tab2:
    th = st.slider('呆滞阈值(库龄天数 >)', 90, 730, 365, step=30, key='dz_th')
    d = _dazhi(q, th)
    k = d['kpi']

    c = st.columns(4)
    c[0].metric(f'呆滞台数(>{th}天)', f"{k['呆滞台数']:,}",
                delta=f"占在库 {k['呆滞台数占比']*100:.1f}%", delta_color='off')
    c[1].metric('呆滞货值', f"{k['呆滞货值_万']:,.1f} 万",
                delta=f"占在库货值 {k['呆滞货值占比']*100:.1f}%", delta_color='off')
    c[2].metric('涉及代理商', f"{k['涉及代理商']}")
    c[3].metric('涉及 SKU', f"{k['涉及SKU']:,}")

    st.info(f"💡 呆滞品攻坚:>{th} 天在库 **{k['呆滞台数']:,}** 台 / **{k['呆滞货值_万']:,.1f}** 万,"
            f"可参照「批发铺货战役」做成行动计划管理(需要的话告诉我升级成战役 page)")

    st.divider()
    cc = st.columns(2)
    with cc[0]:
        st.markdown('**🏢 呆滞 Top 代理商**')
        st.dataframe(pd.DataFrame(d['dealer']), use_container_width=True, hide_index=True,
                     height=min(450, 40 + 35 * len(d['dealer'])))
    with cc[1]:
        st.markdown('**📦 呆滞 Top 50 型号**')
        st.dataframe(pd.DataFrame(d['sku']), use_container_width=True, hide_index=True,
                     height=min(450, 40 + 35 * len(d['sku'])))

    st.divider()
    st.markdown('**🔍 呆滞明细(序列号级,Top 500)**')
    fc = st.columns([2, 3])
    with fc[0]:
        dealers = ['全部'] + [r['代理商'] for r in d['dealer']]
        f_dealer = st.selectbox('代理商', dealers, key='dz_dealer')
    with fc[1]:
        kw = st.text_input('🔍 型号/产品名', key='dz_kw', label_visibility='collapsed',
                           placeholder='搜型号/产品名')
    detail = list_dazhi_detail(q, th, f_dealer, kw if kw.strip() else None)
    st.dataframe(detail, use_container_width=True, hide_index=True, height=400)
    st.caption(f'显示 {len(detail)} 条(上限 500)')

# ═══════════════════════ Tab 3 ═══════════════════════
with tab3:
    d = _turnover(q)
    f = d['flow']
    st.markdown('**🔄 进销存闭环(本季度盘库范围)**')
    c = st.columns(4)
    c[0].metric('盘到总数', f"{f['盘到总数']:,}", help='本季度盘库活动盘到的序列号总数')
    c[1].metric('仍在库', f"{f['在库']:,}")
    c[2].metric('已动销(上线)', f"{f['已动销']:,}")
    c[3].metric('动销率', f"{f['动销率']*100:.1f}%",
                help='已动销 ÷ 盘到总数,越高说明库存周转越快')

    st.divider()
    st.markdown('**🏢 分代理商动销率(盘到 ≥ 100 台)**')
    df = pd.DataFrame(d['dealer'])
    if not df.empty:
        df['动销率'] = (df['动销率'] * 100).round(1).astype(str) + '%'
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=min(550, 40 + 35 * len(df)))
    st.caption('动销率高 = 周转快(进货后快速卖出);在库平均库龄高 = 压货严重')

# ═══════════════════════ Tab 4 ═══════════════════════
with tab4:
    d = _trend()
    df_q = pd.DataFrame(d['quarter'])
    periods = ' → '.join(df_q['盘库季度'].astype(str)) if not df_q.empty else '暂无数据'
    st.markdown(f'**📈 季度库存趋势({periods})**')
    if not df_q.empty:
        c = st.columns(2)
        with c[0]:
            st.markdown('**在库货值(万)**')
            st.bar_chart(df_q.set_index('盘库季度')['在库货值_万'], height=240)
        with c[1]:
            st.markdown('**在库台数 vs 呆滞台数**')
            st.bar_chart(df_q.set_index('盘库季度')[['在库台数', '呆滞台数']], height=240)
        st.dataframe(df_q, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown('**🎯 三大专项库存趋势**')
    df_f = pd.DataFrame(d['focus'])
    if not df_f.empty:
        pivot = df_f.pivot(index='盘库季度', columns='专项', values='在库台数').fillna(0)
        # 复用总趋势中的动态季度顺序，避免新增季度后漏画。
        order = [x for x in df_q['盘库季度'].tolist() if x in pivot.index]
        pivot = pivot.reindex(order)
        st.line_chart(pivot, height=260)
        st.dataframe(pivot.reset_index(), use_container_width=True, hide_index=True)

st.divider()
st.caption('⚠️ 库存 = 严口径(盘库时点在库 + 无上线时间)· 每季度盘库后更新 · 数据治理见 docs/inventory-data/README.md')
