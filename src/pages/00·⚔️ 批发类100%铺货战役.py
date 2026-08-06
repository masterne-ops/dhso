"""⚔️ 批发类客户实现 100% 铺货 — 战役管理 page

第一个战役式 page,模板参考 page 29/30/31 三大专项攻坚

6 大区块:
  0. 战役元信息(标题/Owner/期限/进度)
  1. 📊 现状穿透(总览 KPI + 城市分布)
  2. 🎯 目标 KR(进度条)
  3. 🏆 攻坚名单(902 家客户列表 + 速览卡)
  4. 📋 行动计划(责任人/计划日期/状态编辑)
  5. 📈 进度跟踪(每周新增 + 完成率曲线)
  6. 📂 战役定义 / SOP
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from _auth import require_auth, current_user, is_admin  # noqa: E402
from _campaign_loader import (  # noqa: E402
    get_campaign, list_targets, update_target, refresh_status_from_data,
    get_kpi, get_distribution_detail, get_install_detail,
    STATUSES, PRIORITIES,
)


require_auth()
user = current_user()

CAMPAIGN_CODE = 'wholesale100'

# ─── 顶部:战役元信息 ───
camp = get_campaign(CAMPAIGN_CODE)
if not camp:
    st.error(f'战役 {CAMPAIGN_CODE} 未在 DB 创建')
    st.stop()

st.markdown(f"### ⚔️ {camp['名称']}")
st.caption(camp['定位'])

mc = st.columns(5)
mc[0].metric('Owner', camp['Owner'] or '待定')
mc[1].metric('开始日期', camp['开始日期'])
mc[2].metric('截止日期', camp['截止日期'])
mc[3].metric('状态', camp['状态'])

kpi = get_kpi(camp['id'])
mc[4].metric('总进度', f"{kpi['已完成']}/{kpi['总数']}",
              delta=f"{kpi['已完成']/kpi['总数']*100:.1f}%" if kpi['总数'] else '—',
              delta_color='off')

# 刷新按钮
if is_admin():
    if st.button('🔄 根据铺货数据自动刷新状态', help='把 distribution_info 已铺货的客户自动标为「已完成」'):
        n = refresh_status_from_data(camp['id'])
        st.success(f'已刷新 {n} 个客户状态')
        st.rerun()

st.divider()

# ─── Tab 切换 ───
tab_now, tab_list, tab_plan, tab_track, tab_sop = st.tabs([
    '📊 现状', '🏆 攻坚名单', '📋 行动计划', '📈 进度跟踪', '📂 战役 SOP'
])

# ═══════════════════════════════
# 📊 现状穿透
# ═══════════════════════════════
with tab_now:
    st.markdown('#### 🎯 战役 KR')
    kr_cols = st.columns(3)
    progress = kpi['已完成'] / kpi['总数'] if kpi['总数'] else 0
    kr_cols[0].metric(f'KR1:全部铺货', f"{kpi['已完成']:,} / {kpi['总数']:,}",
                       delta=f"待执行 {kpi['待执行']:,} 家",
                       delta_color='off')
    kr_cols[0].progress(min(progress, 1.0), text=f"{progress*100:.1f}%")

    # 计算上线率
    df_all = list_targets(camp['id'])
    if not df_all.empty:
        bp_count = (df_all['已铺货台数'] > 0).sum()
        bp_online = ((df_all['已铺货台数'] > 0) & (df_all['上线记录数'] > 0)).sum()
        online_rate = bp_online / bp_count if bp_count else 0
        kr_cols[1].metric('KR2:铺货 → 上线率', f"{online_rate*100:.1f}%",
                           delta=f"{bp_online}/{bp_count} 家", delta_color='off')

        avg_units = df_all[df_all['已铺货台数'] > 0]['已铺货台数'].mean() if bp_count else 0
        kr_cols[2].metric('KR3:平均铺货', f"{avg_units:.1f} 台/家",
                           delta=f"最大 {int(df_all['已铺货台数'].max())} 台",
                           delta_color='off')

    st.divider()
    st.markdown('#### 📊 现状穿透')

    # 状态分布
    sc = st.columns(5)
    sc[0].metric('已完成', kpi['已完成'])
    sc[1].metric('待执行', kpi['待执行'])
    sc[2].metric('进行中', kpi['进行中'])
    sc[3].metric('暂缓', kpi['暂缓'])
    sc[4].metric('放弃', kpi['放弃'])

    # 城市分布
    st.markdown('**11 地市分布(按总数 + 完成率)**')
    df_city = df_all.groupby('城市').agg(
        总数=('id', 'count'),
        已完成=('状态', lambda s: (s == '已完成').sum()),
        平均铺货台数=('已铺货台数', lambda s: round(s.mean(), 1)),
    ).reset_index()
    df_city['完成率'] = (df_city['已完成'] / df_city['总数'] * 100).round(1).astype(str) + '%'
    df_city['未完成'] = df_city['总数'] - df_city['已完成']
    df_city = df_city.sort_values('总数', ascending=False)
    st.dataframe(df_city, use_container_width=True, hide_index=True)

    # 业务员分布(top)
    if df_all['客户所有者'].notna().any():
        st.markdown('**👨‍💼 客户所有者 Top 15(批发类客户密集度)**')
        df_owner = df_all.dropna(subset=['客户所有者']).groupby('客户所有者').agg(
            总数=('id', 'count'),
            已完成=('状态', lambda s: (s == '已完成').sum()),
        ).reset_index().sort_values('总数', ascending=False).head(15)
        df_owner['完成率'] = (df_owner['已完成'] / df_owner['总数'] * 100).round(1).astype(str) + '%'
        st.dataframe(df_owner, use_container_width=True, hide_index=True)

# ═══════════════════════════════
# 🏆 攻坚名单
# ═══════════════════════════════
with tab_list:
    st.markdown('#### 🏆 攻坚名单 902 家')

    fc = st.columns([2, 2, 3, 1])
    with fc[0]:
        filter_status = st.selectbox('状态', ['全部'] + STATUSES, index=1, key='f_st')
    with fc[1]:
        cities = ['全部'] + sorted(df_all['城市'].dropna().unique().tolist())
        filter_city = st.selectbox('城市', cities, key='f_city')
    with fc[2]:
        keyword = st.text_input('🔍 搜客户名 / 业务员 / 上级', key='f_kw',
                                  label_visibility='collapsed', placeholder='搜客户名 / 业务员 / 上级')
    with fc[3]:
        st.write('')
        only_undone = st.checkbox('仅未完成', value=True, key='f_undone')

    df = list_targets(camp['id'], 状态=filter_status, 城市=filter_city,
                       关键字=keyword if keyword.strip() else None,
                       only_undone=only_undone and filter_status == '全部')

    st.caption(f'共 {len(df)} 家(全战役 {kpi["总数"]} 家)')

    # 简表显示
    show_cols = ['客户名称', '城市', '区县', '客户所有者', '上级代理商',
                 '已铺货台数', '上线记录数', '状态', '责任人', '计划日期']
    df_show = df[show_cols].copy() if not df.empty else df
    st.dataframe(df_show, use_container_width=True, hide_index=True, height=min(500, 50 + 35 * len(df)))

    # 客户速览卡
    st.markdown('---')
    st.markdown('**🔍 客户速览卡**')
    if not df.empty:
        names = df['客户名称'].tolist()
        sel = st.selectbox('选客户看详情', [''] + names, key='detail_sel')
        if sel:
            row = df[df['客户名称'] == sel].iloc[0]
            ec = st.columns([1, 1, 1, 1])
            ec[0].metric('已铺货', f"{int(row['已铺货台数'])} 台")
            ec[1].metric('上线记录', f"{int(row['上线记录数'])} 条")
            ec[2].metric('状态', row['状态'])
            ec[3].metric('责任人', row['责任人'] or '未指定')

            with st.expander('📦 铺货明细(distribution_info)'):
                dd = get_distribution_detail(sel)
                if not dd.empty:
                    st.dataframe(dd, use_container_width=True, hide_index=True)
                else:
                    st.caption('该客户暂无铺货记录')

            with st.expander('🎯 上线明细(install_redpack 最近 50 条)'):
                ii = get_install_detail(sel)
                if not ii.empty:
                    st.dataframe(ii, use_container_width=True, hide_index=True)
                else:
                    st.caption('该客户暂无上线记录')

# ═══════════════════════════════
# 📋 行动计划(可编辑)
# ═══════════════════════════════
with tab_plan:
    st.markdown('#### 📋 行动计划编辑')
    st.caption('对未完成的客户分配责任人 / 计划日期 / 更新状态')

    df = list_targets(camp['id'], only_undone=True)
    if df.empty:
        st.success('🎉 全部客户已完成!')
    else:
        st.caption(f'待编辑 {len(df)} 家')

        # 用 data_editor 让用户批量编辑
        edit_cols = ['客户名称', '城市', '客户所有者', '状态', '责任人', '优先级', '计划日期', '计划备注']
        df_edit = df[['id'] + edit_cols].copy()
        df_edit['计划日期'] = pd.to_datetime(df_edit['计划日期'], errors='coerce')

        edited = st.data_editor(
            df_edit, hide_index=True, use_container_width=True,
            disabled=['id', '客户名称', '城市', '客户所有者'],
            column_config={
                'id': st.column_config.NumberColumn('ID', disabled=True),
                '状态': st.column_config.SelectboxColumn('状态', options=STATUSES, required=True),
                '优先级': st.column_config.SelectboxColumn('优先级', options=[''] + PRIORITIES),
                '计划日期': st.column_config.DateColumn('计划日期'),
            },
            num_rows='fixed',
            key='plan_editor',
            height=500,
        )

        if st.button('💾 保存所有更改', type='primary'):
            # diff 比对哪些行变了
            n_updated = 0
            for i, row in edited.iterrows():
                orig = df_edit.iloc[i]
                changed = {}
                for col in ['状态', '责任人', '优先级', '计划备注']:
                    if pd.notna(row[col]) and row[col] != orig[col]:
                        changed[col] = row[col]
                # 日期单独处理
                if pd.notna(row['计划日期']):
                    new_d = row['计划日期'].strftime('%Y-%m-%d') if hasattr(row['计划日期'], 'strftime') else str(row['计划日期'])[:10]
                    if new_d != (orig['计划日期'].strftime('%Y-%m-%d') if pd.notna(orig['计划日期']) else ''):
                        changed['计划日期'] = new_d
                if changed:
                    update_target(int(row['id']), _operator=user, **changed)
                    n_updated += 1
            st.success(f'✅ 已保存 {n_updated} 行更改')
            st.rerun()

# ═══════════════════════════════
# 📈 进度跟踪
# ═══════════════════════════════
with tab_track:
    st.markdown('#### 📈 累计铺货进度')

    # 战役开始日 + 每天累计铺货数
    start = camp['开始日期']
    end_date = date.today().isoformat()

    # 这里简化:用 distribution_info 的提交铺货时间作为"铺货日期"
    import sqlite3
    from _loaders import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    daily = pd.read_sql("""
        WITH wholesale AS (
          SELECT 客户名称 FROM provider_contract_v
           WHERE 客户分类_规范 = '批发门店'
        ),
        first_dist AS (
          SELECT di.客户名称_下级 AS 名,
                 MIN(substr(di.提交铺货时间, 1, 10)) AS 首铺日期
            FROM distribution_info di
            JOIN wholesale w ON w.客户名称 = di.客户名称_下级
           WHERE di.提交铺货时间 IS NOT NULL
           GROUP BY 1
        )
        SELECT 首铺日期 AS 日期, COUNT(*) AS 当日新增
          FROM first_dist GROUP BY 1 ORDER BY 1
    """, conn)
    conn.close()

    if not daily.empty:
        daily['累计'] = daily['当日新增'].cumsum()
        st.line_chart(daily.set_index('日期')['累计'], height=240)
        st.caption(f"全战役周期(自数据起):累计 {daily['累计'].iloc[-1]} 家完成首铺(目标 902)")
        with st.expander('查看明细'):
            st.dataframe(daily, use_container_width=True, hide_index=True, height=300)
    else:
        st.info('暂无铺货数据')

    st.divider()
    st.markdown('#### 📊 周内变化')

    # 本周 + 上周
    today = date.today()
    days_since_sat = (today.weekday() - 5) % 7
    last_sat = today - timedelta(days=days_since_sat) if days_since_sat > 0 else today - timedelta(days=7)
    last_sun = last_sat - timedelta(days=6)
    prev_sat = last_sun - timedelta(days=1)
    prev_sun = prev_sat - timedelta(days=6)

    conn = sqlite3.connect(str(DB_PATH))
    week_stats = pd.read_sql("""
        WITH wholesale AS (
          SELECT 客户名称 FROM provider_contract_v
           WHERE 客户分类_规范 = '批发门店'
        )
        SELECT
          COUNT(DISTINCT CASE WHEN substr(di.提交铺货时间,1,10) BETWEEN ? AND ?
                              THEN di.客户名称_下级 END) AS 本周铺货,
          COUNT(DISTINCT CASE WHEN substr(di.提交铺货时间,1,10) BETWEEN ? AND ?
                              THEN di.客户名称_下级 END) AS 上周铺货
          FROM distribution_info di
          JOIN wholesale w ON w.客户名称 = di.客户名称_下级
    """, conn, params=(last_sun.isoformat(), last_sat.isoformat(),
                        prev_sun.isoformat(), prev_sat.isoformat())).iloc[0]
    conn.close()

    wc = st.columns(2)
    wc[0].metric(f'本周({last_sun} ~ {last_sat})', f"{int(week_stats['本周铺货'])} 家")
    wc[1].metric(f'上周({prev_sun} ~ {prev_sat})', f"{int(week_stats['上周铺货'])} 家",
                  delta=int(week_stats['本周铺货']) - int(week_stats['上周铺货']))

# ═══════════════════════════════
# 📂 战役 SOP
# ═══════════════════════════════
with tab_sop:
    st.markdown(camp['描述'] or '_战役描述待补充_')
