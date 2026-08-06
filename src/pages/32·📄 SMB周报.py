"""SMB 主管周报 — 按模板「省区SMB主管周报之Q2业务运营工作指引」结构呈现

数据来源:
  - 360 月报快照(city_snapshot_p3 / dealer_snapshot_p4 / dahua_dealer_owner)
  - 实时(install_redpack / product_flow / focus_target / kpi_targets)
  - 目标(provider_target / focus_target / focus_rhythm)

页面结构 = 4 个 tab:
  ① 分销商布局(SI + 省区 SO + 客户 SO)
  ② 服务商管理(签约/激活 + 等级分布 + 竞品 Top)
  ③ 技术行销(三大专项 SO + 呆滞品)
  ④ 其他工作(占位)
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from _auth import require_auth, is_admin  # noqa: E402
from _loaders import DB_PATH  # noqa: E402
from _weekly_report import gather_weekly_data, to_pct, fmt_num  # noqa: E402
from _weekly_report_docx import render_weekly_report_docx  # noqa: E402


require_auth()
if not is_admin():
    st.error("🔒 管理周报仅管理员（admin）可访问")
    st.stop()

st.markdown('### 📄 SMB 主管周报')
st.caption('按「省区SMB主管周报之Q2业务运营工作指引」模板结构 · 数据 = 360 月报快照 + 实时')


# ─── 顶部参数 ─────────
# 周报周期 = 周日 ~ 周六(7 天)。今天若是周日,本周从"上一个周日"算
# Python weekday: 周一=0 ... 周六=5, 周日=6
today = date.today()
days_since_last_sat = (today.weekday() - 5) % 7   # 上一个周六的距离
last_sat = today - timedelta(days=days_since_last_sat) if days_since_last_sat > 0 else today - timedelta(days=7)
last_sun = last_sat - timedelta(days=6)            # 上一个周日(本周报起始)
default_start = last_sun
default_end = last_sat
c1, c2, c3 = st.columns([1.5, 1.5, 2])
with c1:
    week_start = st.date_input('📅 本周起始日(周日)', value=default_start, key='w_start')
with c2:
    week_end = st.date_input('📅 本周结束日(周六)', value=default_end, key='w_end')
with c3:
    snapshot = st.selectbox('📊 月报快照(360 健康度)', ['2026-04'], index=0, key='w_snap',
                             help='当前仅入库 4 月快照,后续每月更新需重跑 migrations')


@st.cache_data(ttl=300, show_spinner='正在汇总周报数据…')
def _load(ws: str, we: str, snap: str):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return gather_weekly_data(conn, ws, we, snap)
    finally:
        conn.close()


data = _load(week_start.isoformat(), week_end.isoformat(), snapshot)
meta = data['_meta']

st.info(f"📅 周期 **{meta['week_start']} ~ {meta['week_end']}** · "
        f"月报快照 **{meta['snapshot_period']}** · 生成 {meta['generated_at']}")

# ─── 4 个 Tab ─────────
tab1, tab2, tab3, tab4 = st.tabs([
    '一、分销商布局', '二、服务商管理', '三、技术行销', '四、其他工作'
])

# ═══════════ 一、分销商布局 ═══════════
with tab1:
    st.markdown('#### 1.1 分销商签约(SI)')
    si = data['ch1_si_overall']
    st.caption(f"数据时点 {si.get('_数据时点', '-')} · dealer_si_snapshot 实时口径")
    cols = st.columns(5)
    cols[0].metric('全年签约目标',
                   fmt_num(si['全年签约总额_万'], ' 万'),
                   delta=f"{si['代理商总数']} 家代理商", delta_color='off')
    cols[1].metric('累计达成',
                   fmt_num(si['累计达成_万'], ' 万'))
    cols[2].metric('年度完成率',
                   to_pct(si.get('年度完成率'), plus_sign=False),
                   help='累计达成 ÷ 全年签约金额（年度口径）')
    cols[3].metric('阶段达标率',
                   to_pct(si['累计完成率'], plus_sign=False),
                   help=f"累计达成 ÷ 按节奏阶段任务 {fmt_num(si['累计任务_万'], ' 万')}")
    cols[4].metric('达标家数',
                   f"{si['累计达标数']}/{si['代理商总数']}",
                   delta=f"新签 {si['新签家数']} 家", delta_color='off')

    if si['未达标城市']:
        st.warning(f"⚠️ **签约目标未达成城市**:{','.join(si['未达标城市'])}")
    else:
        st.success('✅ 所有城市签约达标')

    st.markdown('🚧 **拓新代理商名单**:等用户给「拓新目标代理商名单」(M2 P1 阻塞)')

    st.divider()
    st.markdown('#### 1.2 省区 SO 进度')
    so = data['ch1_so_province']
    cols = st.columns(5)
    cols[0].metric('年度目标', fmt_num(so['年度目标_万'], ' 万', 0))
    cols[1].metric('YTD 达成',
                   fmt_num(so['YTD达成_万'], ' 万'),
                   delta=to_pct(so['YTD完成率'], plus_sign=False),
                   delta_color='off')
    cols[2].metric('YTD 同比',
                   to_pct(so['YTD同比'], plus_sign=True),
                   help='YTD vs 去年同期累计')
    cols[3].metric('本月达成', fmt_num(so['本月达成_万'], ' 万'))
    cols[4].metric('本月同比', to_pct(so['本月同比'], plus_sign=True))

    st.markdown('**🗺️ 11 地市 SO 表现**')
    df_so = pd.DataFrame(data['ch1_city_so'])
    if not df_so.empty:
        df_so['SO完成率'] = df_so['SO完成率_num'].apply(lambda v: to_pct(v) if pd.notna(v) else '—')
        df_so['SO同比'] = df_so['SO同比_num'].apply(lambda v: to_pct(v, plus_sign=True) if pd.notna(v) else '—')
        st.dataframe(df_so[['城市', 'SO金额目标', '累计SO金额', 'SO完成率',
                            'SO是否达标', 'SO同比', '是否连续3个月SO下降']],
                     use_container_width=True, hide_index=True)

    if data['ch1_so_not_达标_cities']:
        st.warning(f"⚠️ **SO 未达 100% 城市**:{','.join(data['ch1_so_not_达标_cities'])}")
    if data['ch1_so_yoy_negative_cities']:
        st.error(f"🔴 **SO 累计同比负增长城市**:{','.join(data['ch1_so_yoy_negative_cities'])}")

    st.divider()
    st.markdown('#### 1.3 客户(代理商)SO 排查')
    cdc1, cdc2 = st.columns(2)
    with cdc1:
        st.markdown('**未达 100% 客户**')
        df_n = pd.DataFrame(data['ch1_dealer_so_未达100'])
        if not df_n.empty:
            df_n['SO完成率'] = df_n['SO完成率'].apply(lambda v: to_pct(v))
            st.dataframe(df_n[['客户', '所在城市', '业务员', 'SO完成率']],
                         use_container_width=True, hide_index=True, height=300)
        else:
            st.success('✅ 全部客户 SO 完成率 ≥ 100%')
    with cdc2:
        st.markdown('**SO 同比负增长客户**')
        df_neg = pd.DataFrame(data['ch1_dealer_so_yoy_neg'])
        if not df_neg.empty:
            df_neg['SO同比'] = df_neg['SO同比'].apply(lambda v: to_pct(v, plus_sign=True))
            st.dataframe(df_neg[['客户', '所在城市', '业务员', 'SO同比']],
                         use_container_width=True, hide_index=True, height=300)
        else:
            st.success('✅ 无 SO 同比负增长客户')

# ═══════════ 二、服务商管理 ═══════════
with tab2:
    p = data['ch2_provider']
    st.markdown('#### 2.1 服务商签约')
    cols = st.columns(4)
    cols[0].metric('26 年签约目标', f"{p['签约目标']:,} 家")
    cols[1].metric('累计签约', f"{p['签约达成']:,} 家",
                   delta=to_pct(p['签约达成']/p['签约目标']) if p['签约目标'] else '—',
                   delta_color='off')
    cols[2].metric('新签目标', f"{p['新签目标']:,} 家")
    cols[3].metric('本周新签激活', f"{p['本周新增激活']:,} 家",
                   delta=f"本月 +{p['本月新增激活']}",
                   delta_color='off')

    st.divider()
    st.markdown('#### 2.2 服务商激活(按红包成交额分级)')
    cols = st.columns(4)
    cols[0].metric('激活目标 (V2+V3+V4)', f"{p['激活目标']:,}",
                   help='V2 1000-1万 + V3 1万-3万 + V4 3万+')
    cols[1].metric('已激活', f"{p['已激活']:,}",
                   delta=to_pct(p['已激活']/p['激活目标']) if p['激活目标'] else '—',
                   delta_color='off')
    cols[2].metric('本月新增', f"{p['本月新增激活']:,}")
    cols[3].metric('本周新增', f"{p['本周新增激活']:,}")

    st.markdown('**等级分布**')
    df_level = pd.DataFrame([
        {'等级': 'V2 (1000-1万)',  '目标': p['V2目标'], '达成': p['V2达成'],
         '完成率': to_pct(p['V2达成']/p['V2目标']) if p['V2目标'] else '—'},
        {'等级': 'V3 (1万-3万)',    '目标': p['V3目标'], '达成': p['V3达成'],
         '完成率': to_pct(p['V3达成']/p['V3目标']) if p['V3目标'] else '—'},
        {'等级': 'V4+ (3万及以上)', '目标': p['V4目标'], '达成': p['V4达成'],
         '完成率': to_pct(p['V4达成']/p['V4目标']) if p['V4目标'] else '—'},
    ])
    st.dataframe(df_level, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown('#### 2.3 竞品 top 服务商进展')
    if data.get('ch2_competitor_top_count'):
        st.write(f"📋 名单库 {data['ch2_competitor_top_count']} 家")
        st.caption('详情见 page 21 竞品开拓')
    else:
        st.info('竞品名单库为空,需先在 competitor_top_provider 表入库')

    st.divider()
    st.markdown('#### 2.4 NP 流转')
    np_d = data.get('ch2_np')
    if np_d:
        cols = st.columns(4)
        cols[0].metric('识别有效流转', f"{np_d['识别有效流转']:,} 家",
                       help=f"数据时点 {np_d['数据时点']}")
        cols[1].metric('已签约', f"{np_d['已签约']:,} 家",
                       delta=to_pct(np_d['已签约']/np_d['识别有效流转']) if np_d['识别有效流转'] else '—',
                       delta_color='off',
                       help='np_customer_pool 「签约服务商名称」非空')
        cols[2].metric('已激活', f"{np_d['已激活']:,} 家",
                       help='已签约 + install_redpack 累计 ≥ 1000 元')
        cols[3].metric('待签约', f"{np_d['识别有效流转'] - np_d['已签约']:,} 家")
    else:
        st.info('NP 流转名单未入库,建表 np_customer_pool 后展示')

    st.divider()
    st.markdown('#### 2.5 高德潜客')
    gd_d = data.get('ch2_gaode')
    if gd_d:
        cols = st.columns(5)
        cols[0].metric('累计下发', f"{gd_d['累计下发']:,} 家",
                       help=f"数据时点 {gd_d['数据时点']}")
        cols[1].metric('识别有效', f"{gd_d['识别有效']:,} 家",
                       delta=to_pct(gd_d['识别有效']/gd_d['累计下发']) if gd_d['累计下发'] else '—',
                       delta_color='off',
                       help='「与我司业务相关」= Y')
        cols[2].metric('计划签约', f"{gd_d['计划签约']:,} 家",
                       help='「下一步计划」= 签约服务商')
        cols[3].metric('已签约', f"{gd_d['已签约']:,} 家",
                       help='「客户名称」非空(签约后回填内部名)')
        cols[4].metric('已激活', f"{gd_d['已激活']:,} 家",
                       help='已签约 + install_redpack 累计 ≥ 1000 元')
        if gd_d['已签约'] == 0:
            st.caption(f"ℹ️ {gd_d['数据时点']} 快照里签约字段尚未回填,等下次快照更新")
    else:
        st.info('高德潜客名单未入库,建表 gaode_potential_customer 后展示')

# ═══════════ 三、技术行销 ═══════════
with tab3:
    st.markdown('#### 3.1 三大专项 SO 达成')
    df_f = pd.DataFrame(data['ch3_focus'])
    df_f['YTD完成率'] = df_f['YTD完成率'].apply(lambda v: to_pct(v) if pd.notna(v) else '—')
    cols = st.columns(3)
    for i, row in df_f.iterrows():
        cols[i].metric(f"🎯 {row['专项']}",
                       f"{row['YTD达成']:,} / {row['目标']:,} 台",
                       delta=row['YTD完成率'],
                       delta_color='off')
        cols[i].caption(f"本月 +{row['本月达成']} 台")

    st.dataframe(df_f[['专项', '目标', 'YTD达成', 'YTD完成率', '本月达成']],
                 use_container_width=True, hide_index=True)

    st.divider()
    st.markdown('#### 3.2 呆滞品管理')
    sk = data['ch3_stock']
    cols = st.columns(3)
    cols[0].metric('呆滞总台数', f"{sk['呆滞总台数']:,}")
    cols[1].metric('已消耗', f"{sk['已消耗']:,}",
                   delta=f"消化率 {sk['消化率pct']:.1f}%",
                   delta_color='off')
    cols[2].metric('待消化', f"{sk['呆滞总台数'] - sk['已消耗']:,}")
    st.caption(f"数据时点 {meta['snapshot_period']} · 来源 P4 分销商管理表")

    st.markdown('#### 3.3 无线铺货管理')
    st.warning('🚧 铺货数据(M2 P1 阻塞)— 等用户给铺货明细表')

# ═══════════ 四、其他工作 ═══════════
with tab4:
    st.markdown('#### 四、其他周重点跟进工作')
    st.info('📝 该章节支持周报撰写人自行补充。后续可加文本编辑器 + 保存到 weekly_report_log 表')

# ─── 导出区 ─────────
st.divider()
st.markdown('#### 📥 导出周报')
c1, c2 = st.columns(2)
with c1:
    docx_buf = render_weekly_report_docx(data)
    st.download_button(
        '📄 下载周报 docx',
        data=docx_buf,
        file_name=f"省区SMB主管周报_{meta['week_start']}_{meta['week_end']}.docx",
        mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        use_container_width=True,
        help='严格按「省区SMB主管周报之Q2业务运营工作指引」模板渲染'
    )
with c2:
    st.button('📋 复制纯文本汇总', disabled=True, help='复制 markdown 格式纯文本(待开发)')

st.caption(f"📊 数据基础 · {meta['snapshot_period']} 360 健康度月报快照 + install_redpack / product_flow 实时")
