"""🎯 海康份额抢夺战役 page

目标池:269 家(competitor_top_provider 71 ∪ 是否竞品TOP=Y 210 去重)

Phase 1(现有数据):
- 客户分类(海康服务商 / 海康工程商)
- 大华占比反推(累计上线 / 年采购)
- 城市/业务员/上级 分布
Phase 2(待用户提供):
- 海康一级 / 海康金牌名单
- 业务员调研补充海康占比精确值
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from _auth import require_auth, current_user, is_admin  # noqa: E402
from _campaign_loader import (  # noqa: E402
    get_campaign, get_kpi, update_target,
    get_distribution_detail, get_install_detail,
    STATUSES, PRIORITIES,
)
from _loaders import DB_PATH  # noqa: E402


require_auth()
user = current_user()

CAMPAIGN_CODE = 'hikvision-share'

camp = get_campaign(CAMPAIGN_CODE)
if not camp:
    st.error(f'战役 {CAMPAIGN_CODE} 未在 DB 创建')
    st.stop()


@st.cache_data(ttl=300, show_spinner='正在汇总战役数据…')
def load_targets():
    """加载战役目标 + 自动打标 + 大华占比"""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        df = pd.read_sql("""
            SELECT
              ct.id, ct.客户编码, ct.客户名称, ct.城市, ct.区县,
              ct.客户所有者, ct.上级代理商, ct.责任人, ct.优先级,
              ct.计划日期, ct.状态, ct.备注,
              pc.客户分类_规范,
              CASE
                WHEN pc.客户分类_规范 = '中小工程商' THEN '海康工程商'
                WHEN pc.客户分类_规范 IN ('安装商', '夫妻门店', '批发门店') THEN '海康服务商'
                ELSE '未分类'
              END AS 海康分类,
              pc."是否竞品TOP服务商（安防体量≥20W）" AS 是否TOP,
              cm.客户经营品牌, cm.竞品体量_万 AS 海康体量_万,
              cm.在售大华,
              pc."年安防采购量 （年采购视频类产品金额）" AS 年采购_万,
              ROUND(pc.累计上线金额/10000, 2) AS 大华累计_万,
              CASE WHEN pc."年安防采购量 （年采购视频类产品金额）" > 0
                   THEN ROUND(pc.累计上线金额/10000.0 / pc."年安防采购量 （年采购视频类产品金额）" * 100, 1)
                   END AS 大华占比pct,
              CASE WHEN pc."年安防采购量 （年采购视频类产品金额）" > 0
                   THEN ROUND((1 - pc.累计上线金额/10000.0 / pc."年安防采购量 （年采购视频类产品金额）") * 100, 1)
                   END AS 非大华占比pct,
              pc.服务商等级, pc.是否激活
              FROM campaign_target ct
              JOIN campaign c ON c.id = ct.campaign_id
              LEFT JOIN provider_contract_v pc ON pc.客户编码 = ct.客户编码
              LEFT JOIN competitor_top_provider cm ON cm.客户编码 = ct.客户编码
             WHERE c.代号 = ?
             ORDER BY 大华占比pct ASC NULLS LAST, 年采购_万 DESC
        """, conn, params=(CAMPAIGN_CODE,))
        return df
    finally:
        conn.close()


df_all = load_targets()
kpi = get_kpi(camp['id'])

# ═══════════════════════════════════════
# 头部:战役元信息
# ═══════════════════════════════════════

st.markdown(f"### 🎯 {camp['名称']}")
st.caption(camp['定位'])

mc = st.columns(5)
mc[0].metric('Owner', camp['Owner'] or '待定')
mc[1].metric('开始日期', camp['开始日期'])
mc[2].metric('截止日期', camp['截止日期'])
mc[3].metric('状态', camp['状态'])
mc[4].metric('目标池', f"{kpi['总数']} 家", delta=f"已完成 {kpi['已完成']}", delta_color='off')

st.divider()

# ═══════════════════════════════════════
# Tab 切换
# ═══════════════════════════════════════

tab_now, tab_list, tab_plan, tab_sop = st.tabs([
    '📊 现状穿透', '🏆 攻坚名单 (含大华占比)', '📋 行动计划', '📂 战役 SOP'
])

# ═══════════════════════════════════════
# 📊 现状穿透
# ═══════════════════════════════════════
with tab_now:
    st.markdown('#### 🎯 战役 KR')

    # 计算关键指标
    n_total = len(df_all)
    n_servicer = (df_all['海康分类'] == '海康服务商').sum()
    n_engineer = (df_all['海康分类'] == '海康工程商').sum()
    has_ratio = df_all['大华占比pct'].notna()
    n_with_ratio = has_ratio.sum()

    avg_dahua = df_all.loc[has_ratio, '大华占比pct'].mean() if n_with_ratio else 0
    n_hikvision_strong = ((df_all['大华占比pct'] < 50) & has_ratio).sum()
    n_low10 = ((df_all['大华占比pct'] < 10) & has_ratio).sum()
    sum_annual = df_all['年采购_万'].sum()
    sum_dahua = df_all['大华累计_万'].sum()

    kr_cols = st.columns(4)
    kr_cols[0].metric('目标池规模', f"{n_total} 家")
    kr_cols[1].metric('当前大华占比均值', f"{avg_dahua:.1f}%",
                       delta=f'可算 {n_with_ratio}/{n_total} 家', delta_color='off')
    kr_cols[2].metric('海康强势(大华<50%)', f"{n_hikvision_strong} 家",
                       delta=f"{n_hikvision_strong/n_with_ratio*100:.1f}%" if n_with_ratio else '—',
                       delta_color='off')
    kr_cols[3].metric('深度海康(大华<10%)', f"{n_low10} 家",
                       delta='重点攻坚', delta_color='off')

    st.divider()

    # 海康分类拆分
    st.markdown('#### 📊 海康分类分布(基于客户分类_规范)')
    df_cat = df_all.groupby('海康分类').size().reset_index(name='家数')
    df_cat['占比'] = (df_cat['家数'] / n_total * 100).round(1).astype(str) + '%'
    c1, c2 = st.columns([1, 1])
    with c1:
        st.dataframe(df_cat, use_container_width=True, hide_index=True)
    with c2:
        # 客户分类_规范 细分
        df_sub = df_all.groupby('客户分类_规范').size().reset_index(name='家数')
        df_sub.columns = ['客户分类(MECE)', '家数']
        st.dataframe(df_sub, use_container_width=True, hide_index=True)

    st.divider()

    # 大华占比段位
    st.markdown('#### 🥋 大华占比段位分布')
    bins = pd.cut(df_all.loc[has_ratio, '大华占比pct'],
                   bins=[-1, 0.01, 10, 30, 50, 100, 99999],
                   labels=['0% (无大华)', '0-10%', '10-30%', '30-50%', '50-100%', '>100% (异常)'])
    df_bin = bins.value_counts().sort_index().reset_index()
    df_bin.columns = ['段位', '家数']
    df_bin['占比'] = (df_bin['家数'] / n_with_ratio * 100).round(1).astype(str) + '%'
    st.dataframe(df_bin, use_container_width=True, hide_index=True)
    st.caption(f"⚠️ {n_total - n_with_ratio} 家无年采购量数据,无法算大华占比")

    st.divider()

    # 城市分布
    st.markdown('#### 🗺️ 城市分布(按目标家数 + 年采购)')
    df_city = df_all.groupby('城市').agg(
        家数=('id', 'count'),
        年采购总_万=('年采购_万', lambda s: round(s.sum() if s.notna().any() else 0, 0)),
        大华累计总_万=('大华累计_万', lambda s: round(s.sum(), 1)),
    ).reset_index()
    df_city['大华占比'] = (df_city['大华累计总_万'] / df_city['年采购总_万'] * 100).round(1).astype(str) + '%'
    df_city = df_city.sort_values('家数', ascending=False)
    st.dataframe(df_city, use_container_width=True, hide_index=True)

    st.divider()

    # 业务员归属 top
    st.markdown('#### 👨‍💼 客户所有者 Top(海康强势客户密集度)')
    df_owner = df_all.dropna(subset=['客户所有者']).groupby('客户所有者').agg(
        家数=('id', 'count'),
        平均大华占比=('大华占比pct', lambda s: round(s.mean() if s.notna().any() else 0, 1)),
    ).reset_index().sort_values('家数', ascending=False).head(15)
    st.dataframe(df_owner, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════
# 🏆 攻坚名单
# ═══════════════════════════════════════
with tab_list:
    st.markdown('#### 🏆 攻坚名单 (按大华占比升序,占比低=优先攻坚)')

    fc = st.columns([1.5, 1.5, 2, 1.5, 1.5])
    with fc[0]:
        f_cat = st.selectbox('海康分类', ['全部', '海康服务商', '海康工程商', '未分类'], key='hk_cat')
    with fc[1]:
        cities = ['全部'] + sorted(df_all['城市'].dropna().unique().tolist())
        f_city = st.selectbox('城市', cities, key='hk_city')
    with fc[2]:
        kw = st.text_input('🔍 客户/业务员/上级', key='hk_kw', label_visibility='collapsed')
    with fc[3]:
        f_ratio = st.selectbox('大华占比', ['全部', '<10% 深度海康', '<30%', '<50% 海康强势', '≥50%'], key='hk_ratio')
    with fc[4]:
        st.write('')
        f_status = st.selectbox('状态', ['全部'] + STATUSES, key='hk_st')

    df = df_all.copy()
    if f_cat != '全部':
        df = df[df['海康分类'] == f_cat]
    if f_city != '全部':
        df = df[df['城市'] == f_city]
    if kw.strip():
        m = (df['客户名称'].str.contains(kw, na=False, case=False) |
             df['客户所有者'].astype(str).str.contains(kw, na=False, case=False) |
             df['上级代理商'].astype(str).str.contains(kw, na=False, case=False))
        df = df[m]
    if f_ratio == '<10% 深度海康':
        df = df[df['大华占比pct'] < 10]
    elif f_ratio == '<30%':
        df = df[df['大华占比pct'] < 30]
    elif f_ratio == '<50% 海康强势':
        df = df[df['大华占比pct'] < 50]
    elif f_ratio == '≥50%':
        df = df[df['大华占比pct'] >= 50]
    if f_status != '全部':
        df = df[df['状态'] == f_status]

    st.caption(f'筛选后 {len(df)} 家 / 全战役 {n_total} 家')

    show_cols = ['客户名称', '城市', '区县', '海康分类', '客户分类_规范',
                 '客户经营品牌', '海康体量_万', '年采购_万',
                 '大华累计_万', '大华占比pct', '非大华占比pct',
                 '客户所有者', '上级代理商', '状态']
    df_show = df[show_cols].copy()
    st.dataframe(df_show, use_container_width=True, hide_index=True,
                  height=min(550, 50 + 35 * len(df)))

    # 客户速览
    if not df.empty:
        st.markdown('---')
        st.markdown('**🔍 客户速览**')
        names = df['客户名称'].dropna().tolist()
        sel = st.selectbox('选客户看详情', [''] + names, key='hk_detail')
        if sel:
            row = df[df['客户名称'] == sel].iloc[0]
            ec = st.columns(5)
            ec[0].metric('海康分类', row['海康分类'])
            ec[1].metric('海康体量', f"{row['海康体量_万'] or '—'} 万" if pd.notna(row['海康体量_万']) else '—')
            ec[2].metric('年采购', f"{row['年采购_万'] or '—'} 万" if pd.notna(row['年采购_万']) else '—')
            ec[3].metric('大华累计', f"{row['大华累计_万']:.1f} 万" if pd.notna(row['大华累计_万']) else '—')
            ec[4].metric('大华占比', f"{row['大华占比pct']:.1f}%" if pd.notna(row['大华占比pct']) else '—')

            with st.expander('🎯 大华上线明细(install_redpack 最近 50 条)'):
                ii = get_install_detail(sel)
                if not ii.empty:
                    st.dataframe(ii, use_container_width=True, hide_index=True)
                else:
                    st.caption('暂无大华上线记录')


# ═══════════════════════════════════════
# 📋 行动计划
# ═══════════════════════════════════════
with tab_plan:
    st.markdown('#### 📋 行动计划编辑 (优先攻坚深度海康 < 10%)')

    df = df_all[df_all['状态'].isin(['待执行', '进行中'])].copy()
    # 默认按大华占比升序
    df = df.sort_values('大华占比pct', ascending=True, na_position='last')

    if df.empty:
        st.success('🎉 全部攻坚完成!')
    else:
        st.caption(f'待编辑 {len(df)} 家')

        edit_cols = ['客户名称', '城市', '海康分类', '大华占比pct', '年采购_万',
                      '客户所有者', '状态', '责任人', '优先级', '计划日期']
        df_edit = df[['id'] + edit_cols].copy()
        df_edit['计划日期'] = pd.to_datetime(df_edit['计划日期'], errors='coerce')

        edited = st.data_editor(
            df_edit, hide_index=True, use_container_width=True,
            disabled=['id', '客户名称', '城市', '海康分类', '大华占比pct', '年采购_万', '客户所有者'],
            column_config={
                'id': st.column_config.NumberColumn('ID', disabled=True),
                '大华占比pct': st.column_config.NumberColumn('大华占比%', format="%.1f"),
                '状态': st.column_config.SelectboxColumn('状态', options=STATUSES, required=True),
                '优先级': st.column_config.SelectboxColumn('优先级', options=[''] + PRIORITIES),
                '计划日期': st.column_config.DateColumn('计划日期'),
            },
            num_rows='fixed', height=500, key='hk_editor',
        )

        if st.button('💾 保存所有更改', type='primary'):
            n_updated = 0
            for i, row in edited.iterrows():
                orig = df_edit.iloc[i]
                changed = {}
                for col in ['状态', '责任人', '优先级']:
                    if pd.notna(row[col]) and row[col] != orig[col]:
                        changed[col] = row[col]
                if pd.notna(row['计划日期']):
                    new_d = row['计划日期'].strftime('%Y-%m-%d') if hasattr(row['计划日期'], 'strftime') else str(row['计划日期'])[:10]
                    if new_d != (orig['计划日期'].strftime('%Y-%m-%d') if pd.notna(orig['计划日期']) else ''):
                        changed['计划日期'] = new_d
                if changed:
                    update_target(int(row['id']), _operator=user, **changed)
                    n_updated += 1
            st.success(f'✅ 已保存 {n_updated} 行更改')
            st.cache_data.clear()
            st.rerun()


# ═══════════════════════════════════════
# 📂 战役 SOP
# ═══════════════════════════════════════
with tab_sop:
    st.markdown(camp['描述'] or '_战役描述待补充_')

    st.divider()
    st.info("""
**待你提供的数据(完善打标)**:
1. 📥 **海康一级名单**(Excel:客户编码 + 名称)
2. 📥 **海康金牌名单**(Excel)
3. 📥 **业务员调研:海康占比精确值**(每个攻坚客户)

收到后会自动升级打标精度,大华占比会从「反推」变成「双源验证」。
""")
