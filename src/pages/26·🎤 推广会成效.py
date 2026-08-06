#!/usr/bin/env python3
"""🎤 推广会成效分析

数据源:
  - promotion_meeting(参会名单)
  - install_redpack_v(看会后采购)
  - visit_record_v(看大华业务员是否跟进)

核心指标(会后:当天 + 30 天 两个口径):
  - 现场活跃(当天上线)
  - 30 天转化(30 天内有上线)
  - 30 天跟进(大华业务员拜访)
  - 重复参会(资源低效)
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import (  # noqa: E402
    load_redpack_shared,
    load_visit_shared,
    load_profile_shared,
    DB_PATH,
)
from _promotion_loader import (  # noqa: E402
    import_promotion_excel,
    load_promotion_shared,
)

st.set_page_config(page_title='推广会成效', layout='wide')
require_auth()

st.markdown('### 🎤 推广会成效分析')
st.caption(
    '数据源:`promotion_meeting`(参会明细)+ `install_redpack_v`(会后采购)+ `visit_record_v`(大华业务员跟进)。'
    '**两个时间口径**:当天(现场活跃)+ 30 天(中期转化 / 业务员跟进)。'
)

# ────────────────────────────────────────────────
# Section 1:数据导入
# ────────────────────────────────────────────────

with st.expander('📥 数据导入(上传新的推广会 Excel)', expanded=False):
    st.caption(
        '文件:`会议沙龙参会客户明细表_*.xlsx`(双行表头格式)。'
        '主键:(ADSPID, 参会客户编码) — 同一会同一服务商只一行。'
    )
    upl = st.file_uploader(
        '选择 Excel(.xlsx)',
        type=['xlsx', 'xls'],
        key='pm_upload',
    )
    if upl:
        mode = st.radio(
            '导入模式',
            options=['merge', 'append', 'replace'],
            format_func=lambda x: {
                'merge': '🔀 合并(PK 重合则覆盖,新行追加)— 推荐',
                'append': '➕ 追加(已存在 PK 跳过)',
                'replace': '🔄 覆盖(先清空再导)',
            }[x],
            horizontal=False,
        )
        if st.button('💾 开始导入', type='primary', key='pm_import_btn'):
            with st.spinner('正在导入…'):
                try:
                    r = import_promotion_excel(upl, mode=mode)
                    msg = (
                        f'✅ 导入成功:新增 {r["inserted"]:,} / '
                        f'更新 {r["updated"]:,} / 库内总数 {r["total"]:,}'
                    )
                    if r['duplicates_in_file'] > 0:
                        msg += f'(文件内 {r["duplicates_in_file"]} 条 PK 重复,留最后一条)'
                    st.success(msg)
                    load_promotion_shared.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f'❌ 导入失败:{e}')
                    import traceback
                    with st.expander('完整错误'):
                        st.code(traceback.format_exc())


# ────────────────────────────────────────────────
# Section 2:数据加载
# ────────────────────────────────────────────────

pm = load_promotion_shared()
if pm.empty:
    st.warning('📦 推广会数据为空,请先在上方上传 Excel 文件。')
    st.stop()

rp = load_redpack_shared()
vr = load_visit_shared()
profile = load_profile_shared()


# ────────────────────────────────────────────────
# Section 3:服务商池子 + 筛选
# ────────────────────────────────────────────────

EXCLUDE_KW = ['大华技术', '云商专用', '云商账号']


@st.cache_data(ttl=600)
def build_sp_info(rp: pd.DataFrame) -> pd.DataFrame:
    """每个服务商一行:服务商所在地市 + 签约代理商(取该服务商记录里最高频)"""
    if rp.empty:
        return pd.DataFrame(columns=['服务商编码', '所在地市', '签约代理商'])
    g = rp.groupby('上线客户编码').agg(
        所在地市=('上线客户地市', lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else None),
        签约代理商=('所属一级客户', lambda x: x.dropna().mode().iloc[0] if len(x.dropna().mode()) > 0 else None),
    ).reset_index().rename(columns={'上线客户编码': '服务商编码'})
    g['服务商编码'] = g['服务商编码'].astype(str).str.strip()
    return g


sp_info = build_sp_info(rp)

# 服务商池子:profile 表(沙盘服务商,全集)+ rp 里活跃过的(补漏)
pool_codes = set()
if not profile.empty and '客户编码' in profile.columns:
    pool_codes |= set(profile['客户编码'].astype(str).str.strip())
if not rp.empty:
    pool_codes |= set(rp['上线客户编码'].astype(str).str.strip())
pool_codes.discard('')
pool_codes.discard('nan')


with st.sidebar:
    st.header('筛选')

    # 时段:活动开始时间
    min_dt = pm['活动开始时间'].min().date() if pd.notna(pm['活动开始时间'].min()) else datetime(2025, 1, 1).date()
    max_dt = pm['活动开始时间'].max().date() if pd.notna(pm['活动开始时间'].max()) else datetime.now().date()
    date_range = st.date_input(
        '活动开始日期范围',
        value=(min_dt, max_dt),
        min_value=min_dt,
        max_value=max_dt,
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_dt, end_dt = date_range
    else:
        start_dt, end_dt = min_dt, max_dt

    # 地市筛选(对服务商所在地市)
    city_options = sorted(sp_info['所在地市'].dropna().astype(str).unique().tolist()) if not sp_info.empty else []
    city_options = [c for c in city_options if c and '***' not in c]
    sel_cities = st.multiselect(
        '服务商所在地市(不选 = 全部)',
        city_options, default=[],
        help='按参会服务商所在地市过滤(install_redpack.上线客户地市)',
    )

    # 签约代理商筛选(参会服务商签约的一级代理商)
    dealer_options = sorted(sp_info['签约代理商'].dropna().astype(str).unique().tolist()) if not sp_info.empty else []
    sel_signed_dealers = st.multiselect(
        '参会服务商签约代理商(不选 = 全部)',
        dealer_options, default=[],
        help='按参会服务商签约的一级代理商过滤(install_redpack.所属一级客户)',
    )

    # 主办代理商
    host_dealers = sorted(pm['主办方代理商'].dropna().unique().tolist())
    sel_host_dealers = st.multiselect(
        '主办代理商(办会方)',
        host_dealers, default=[],
    )

    # 排除大华内部
    exclude_internal = st.checkbox(
        '排除大华内部账号(默认勾)',
        value=True,
        help='过滤掉 "大华技术 / 云商专用 / 云商账号" 等内部测试账号',
    )

    # 仅签到
    only_signed = st.checkbox(
        '仅看签到(真出席)',
        value=True,
        help='Excel 里没签到时间 = 报名未到',
    )

    st.divider()
    st.caption(f'库内总参会人次:{len(pm):,}')
    st.caption(f'活动场次:{pm["活动名称"].nunique()}')
    st.caption(f'服务商池子:{len(pool_codes):,}')

# 应用筛选
df = pm.copy()
df = df[
    (df['活动开始时间'].dt.date >= start_dt)
    & (df['活动开始时间'].dt.date <= end_dt)
]
if sel_host_dealers:
    df = df[df['主办方代理商'].isin(sel_host_dealers)]
if exclude_internal:
    mask = df['参会客户名称'].fillna('').apply(
        lambda s: not any(kw in str(s) for kw in EXCLUDE_KW)
    )
    df = df[mask]
if only_signed:
    df = df[df['是否签到']]

# 把 sp_info join 进 df(供后续地市/代理商筛选)
if not sp_info.empty:
    df = df.merge(
        sp_info, left_on='参会客户编码', right_on='服务商编码', how='left',
    )
else:
    df['所在地市'] = None
    df['签约代理商'] = None

# 应用地市 / 签约代理商 筛选
filtered_pool_codes = pool_codes
if sel_cities:
    df = df[df['所在地市'].isin(sel_cities)]
    # 也把"服务商池子"限定到这些地市,否则参加率分母错
    if not sp_info.empty:
        codes_in_cities = set(sp_info[sp_info['所在地市'].isin(sel_cities)]['服务商编码'])
        filtered_pool_codes = filtered_pool_codes & codes_in_cities
if sel_signed_dealers:
    df = df[df['签约代理商'].isin(sel_signed_dealers)]
    if not sp_info.empty:
        codes_in_dealers = set(sp_info[sp_info['签约代理商'].isin(sel_signed_dealers)]['服务商编码'])
        filtered_pool_codes = filtered_pool_codes & codes_in_dealers

if df.empty:
    st.warning('当前筛选下无数据')
    st.stop()


# ────────────────────────────────────────────────
# Section 4:核心指标 KPI
# ────────────────────────────────────────────────

# 准备 lookup
# 服务商上线时间字典(精确到 日)
rp_lookup = pd.DataFrame()
if not rp.empty:
    rp_lookup = rp[['上线客户编码', '上线时间']].copy()
    rp_lookup['上线日期'] = pd.to_datetime(rp_lookup['上线时间']).dt.date

# 大华业务员拜访字典
vr_lookup = pd.DataFrame()
if not vr.empty:
    vr_dh = vr[(vr['_打卡方'] == '🏢 大华')
               & (vr['_打卡异常无效'] == 0)
               & (vr['_真异常打卡'] == 0)].copy()
    vr_lookup = vr_dh[['客户编码', '拜访时间', '打卡人姓名']].copy()
    vr_lookup['拜访日期'] = pd.to_datetime(vr_lookup['拜访时间']).dt.date


@st.cache_data(ttl=600, show_spinner='计算成效指标中…')
def compute_efficacy(df_pm: pd.DataFrame, rp_lookup: pd.DataFrame, vr_lookup: pd.DataFrame) -> pd.DataFrame:
    """对每一行参会记录,计算 3 个指标。"""
    out = df_pm.copy()
    out['活动日期_'] = out['活动开始时间'].dt.date

    # 构造 服务商编码 → [上线日期] 列表
    if not rp_lookup.empty:
        rp_by_code = rp_lookup.groupby('上线客户编码')['上线日期'].apply(set).to_dict()
    else:
        rp_by_code = {}
    if not vr_lookup.empty:
        vr_by_code = vr_lookup.groupby('客户编码')['拜访日期'].apply(set).to_dict()
    else:
        vr_by_code = {}

    def for_code(code, event_date):
        if not isinstance(event_date, (datetime, pd.Timestamp)):
            event_date_d = event_date
        else:
            event_date_d = event_date.date() if hasattr(event_date, 'date') else event_date

        end_30 = event_date_d + timedelta(days=30)

        # 当天上线
        rp_dates = rp_by_code.get(str(code), set())
        same_day = any(d == event_date_d for d in rp_dates)
        # 30 天内上线
        in_30 = any(event_date_d <= d <= end_30 for d in rp_dates)
        # 30 天内业务员拜访
        vr_dates = vr_by_code.get(str(code), set())
        visited = any(event_date_d <= d <= end_30 for d in vr_dates)

        return same_day, in_30, visited

    sd_list, in30_list, vis_list = [], [], []
    for _, row in out.iterrows():
        sd, in30, vis = for_code(row['参会客户编码'], row['活动日期_'])
        sd_list.append(sd)
        in30_list.append(in30)
        vis_list.append(vis)

    out['当天上线'] = sd_list
    out['30天上线'] = in30_list
    out['30天业务员跟进'] = vis_list
    return out


eff = compute_efficacy(df, rp_lookup, vr_lookup)

n_total = len(eff)
n_sp = eff['参会客户编码'].nunique()
n_event = eff['活动名称'].nunique()

# 重复参会计算
repeat_n = eff.groupby('参会客户编码').size()
repeat_n_2plus = (repeat_n >= 2).sum()
repeat_n_3plus = (repeat_n >= 3).sum()
repeat_total_attendees = repeat_n[repeat_n >= 2].sum()

n_followed_and_bought = eff[eff["30天业务员跟进"] & eff["30天上线"]]["参会客户编码"].nunique()
n_followed_only = eff[eff["30天业务员跟进"] & ~eff["30天上线"]]["参会客户编码"].nunique()
n_bought_only = eff[~eff["30天业务员跟进"] & eff["30天上线"]]["参会客户编码"].nunique()
n_neither = eff[~eff["30天业务员跟进"] & ~eff["30天上线"]]["参会客户编码"].nunique()

# 参加率(参会服务商 / 服务商池子)
n_pool = len(filtered_pool_codes)
attended_codes = set(eff['参会客户编码'].astype(str))
# 参加过的(在池子里)
attended_in_pool = attended_codes & filtered_pool_codes
n_attended_in_pool = len(attended_in_pool)
n_not_attended = n_pool - n_attended_in_pool
attend_rate = n_attended_in_pool / n_pool if n_pool > 0 else None

# 紧凑核心指标:8 个一行
st.markdown('#### 📊 核心指标')
c = st.columns(8)
c[0].metric('场次', f'{n_event:,}')
c[1].metric('参会人次', f'{n_total:,}')
c[2].metric('参会服务商', f'{n_sp:,}', help='扣除重复后的独立服务商')
c[3].metric(
    '参加率',
    f'{attend_rate*100:.1f}%' if attend_rate is not None else '—',
    delta=f'池 {n_pool:,}',
    delta_color='off',
    help=f'当前筛选范围内,参会服务商 / 服务商池子。池子 = provider_profile ∪ install_redpack 去重',
)
c[4].metric('未参加服务商', f'{n_not_attended:,}',
            help='池子里有,但筛选时段内一次都没来过推广会的')
c[5].metric(
    '当天上线',
    f'{eff[eff["当天上线"]]["参会客户编码"].nunique():,}',
    delta=f'{eff[eff["当天上线"]]["参会客户编码"].nunique() / max(n_sp, 1) * 100:.1f}%',
    delta_color='off', help='现场活跃率',
)
c[6].metric(
    '30天上线',
    f'{eff[eff["30天上线"]]["参会客户编码"].nunique():,}',
    delta=f'{eff[eff["30天上线"]]["参会客户编码"].nunique() / max(n_sp, 1) * 100:.1f}%',
    delta_color='off', help='30 天转化率',
)
c[7].metric(
    '30天跟进',
    f'{eff[eff["30天业务员跟进"]]["参会客户编码"].nunique():,}',
    delta=f'{eff[eff["30天业务员跟进"]]["参会客户编码"].nunique() / max(n_sp, 1) * 100:.1f}%',
    delta_color='off', help='大华业务员 30 天内拜访率',
)

# 四象限(紧凑一行)
st.markdown('##### 🚦 业务员跟进 × 采购 四象限')
g = st.columns(4)
g[0].metric('✅ 跟进+采购', f'{n_followed_and_bought:,}', help='业务员到位 + 客户成交,理想')
g[1].metric('🚨 跟进未采购', f'{n_followed_only:,}', help='业务员跑了但没成交 → 推广会效果有问题')
g[2].metric('⚠️ 未跟进有采购', f'{n_bought_only:,}', help='客户主动成交 → 业务员失位')
g[3].metric('❌ 都没动', f'{n_neither:,}', help='业务员不到位 + 推广会无效')


# ────────────────────────────────────────────────
# Section 5:重复参会(资源低效)
# ────────────────────────────────────────────────

st.divider()
st.markdown('#### 🔁 重复参会名单(资源低效投放预警)')
st.caption('同一服务商参加多场会 = 营销资源重复投放。优先看「重复参会 ≥ 3 场」的。')

rep_col1, rep_col2 = st.columns(2)
rep_col1.metric('参加 ≥ 2 场的服务商', f'{repeat_n_2plus:,}',
                delta=f'累计 {repeat_total_attendees:,} 人次')
rep_col2.metric('参加 ≥ 3 场的服务商', f'{repeat_n_3plus:,}')

# 重复参会名单
rep_df = repeat_n[repeat_n >= 2].sort_values(ascending=False).reset_index()
rep_df.columns = ['参会客户编码', '参会场次']
# 补名字
name_map = eff.groupby('参会客户编码')['参会客户名称'].first().to_dict()
rep_df['服务商名'] = rep_df['参会客户编码'].map(name_map)
# 补每个服务商的 30 天上线场次数 / 跟进场次数
sp_eff = eff.groupby('参会客户编码').agg(
    当天上线场次=('当天上线', 'sum'),
    内30天上线场次=('30天上线', 'sum'),
    被跟进场次=('30天业务员跟进', 'sum'),
).reset_index()
rep_df = rep_df.merge(sp_eff, on='参会客户编码', how='left')
rep_df['有效率'] = (rep_df['内30天上线场次'] / rep_df['参会场次'] * 100).round(1).astype(str) + '%'

st.dataframe(
    rep_df[['服务商名', '参会客户编码', '参会场次', '当天上线场次', '内30天上线场次', '被跟进场次', '有效率']],
    use_container_width=True, hide_index=True, height=400,
)

# 下载
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine='openpyxl') as w:
    rep_df.to_excel(w, sheet_name='重复参会', index=False)
st.download_button(
    f'📥 导出重复参会名单({len(rep_df):,} 家)',
    data=buf.getvalue(),
    file_name=f'重复参会名单_{datetime.now().strftime("%Y%m%d")}.xlsx',
    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
)


# ────────────────────────────────────────────────
# Section 6:推广会场次明细(按时间倒序)
# ────────────────────────────────────────────────

st.divider()
st.markdown('#### 📋 推广会场次明细(按活动时间倒序)')
st.caption('每场会的 4 指标:报名/签到/30 天采购/30 天跟进。点击下方可下钻到单场会。')

event_summary = eff.groupby(
    ['活动名称', '活动开始时间', '主办方代理商']
).agg(
    报名人次=('参会客户编码', 'count'),
    签到人数=('是否签到', 'sum'),
    服务商数=('参会客户编码', 'nunique'),
    当天上线=('当天上线', 'sum'),
    内30天上线=('30天上线', 'sum'),
    被跟进=('30天业务员跟进', 'sum'),
).reset_index().sort_values('活动开始时间', ascending=False)

# 计算率
event_summary['当天活跃率'] = (event_summary['当天上线'] / event_summary['签到人数'].replace(0, 1) * 100).round(1).astype(str) + '%'
event_summary['30天转化率'] = (event_summary['内30天上线'] / event_summary['签到人数'].replace(0, 1) * 100).round(1).astype(str) + '%'
event_summary['30天跟进率'] = (event_summary['被跟进'] / event_summary['签到人数'].replace(0, 1) * 100).round(1).astype(str) + '%'

event_summary['活动开始时间'] = pd.to_datetime(event_summary['活动开始时间']).dt.strftime('%Y-%m-%d %H:%M')

st.dataframe(
    event_summary[[
        '活动开始时间', '活动名称', '主办方代理商',
        '签到人数', '当天活跃率', '30天转化率', '30天跟进率',
        '当天上线', '内30天上线', '被跟进',
    ]],
    use_container_width=True, hide_index=True, height=500,
    column_config={
        '活动名称': st.column_config.TextColumn(width='large'),
        '主办方代理商': st.column_config.TextColumn(width='medium'),
    },
)

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine='openpyxl') as w:
    event_summary.to_excel(w, sheet_name='推广会场次', index=False)
st.download_button(
    f'📥 导出推广会场次明细({len(event_summary):,} 场)',
    data=buf.getvalue(),
    file_name=f'推广会场次明细_{datetime.now().strftime("%Y%m%d")}.xlsx',
    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    key='evt_dl',
)


# ────────────────────────────────────────────────
# Section 7:单场会下钻
# ────────────────────────────────────────────────

st.divider()
st.markdown('#### 🔍 单场会下钻分析')

evt_pick = st.selectbox(
    '选择一场推广会',
    options=event_summary['活动名称'].tolist(),
    key='evt_pick',
    help='查看该场会的参会名单 + 业务员跟进 + 采购明细',
)
if evt_pick:
    evt_eff = eff[eff['活动名称'] == evt_pick].copy()
    evt_dt = evt_eff['活动开始时间'].iloc[0]
    evt_dealer = evt_eff['主办方代理商'].iloc[0]

    st.info(
        f'**{evt_pick}**  ·  {evt_dt}  ·  主办:{evt_dealer}  ·  '
        f'签到 {evt_eff["是否签到"].sum()} 人,服务商 {evt_eff["参会客户编码"].nunique()} 家'
    )

    # 漏斗
    fc1, fc2, fc3, fc4 = st.columns(4)
    fc1.metric('报名', f'{len(evt_eff):,}')
    fc2.metric('签到', f'{evt_eff["是否签到"].sum():,}')
    fc3.metric('30 天内有采购', f'{evt_eff[evt_eff["30天上线"]]["参会客户编码"].nunique():,}')
    fc4.metric('30 天内有跟进', f'{evt_eff[evt_eff["30天业务员跟进"]]["参会客户编码"].nunique():,}')

    # 详细名单
    detail = evt_eff[[
        '参会客户名称', '参会客户编码', '参与人姓名',
        '渠道客户类型_实时', '星级_固化',
        '是否签到', '当天上线', '30天上线', '30天业务员跟进',
    ]].copy()
    detail.columns = [
        '服务商', '编码', '到场代表',
        '客户类型', '星级',
        '✓签到', '✓当天上线', '✓30天上线', '✓被跟进',
    ]
    for c in ['✓签到', '✓当天上线', '✓30天上线', '✓被跟进']:
        detail[c] = detail[c].map({True: '✅', False: ''})

    st.dataframe(detail, use_container_width=True, hide_index=True, height=400)

    # 状态分类
    st.markdown('##### 🎯 服务商成效分类(该场会)')
    grp_followed_bought = evt_eff[evt_eff['30天业务员跟进'] & evt_eff['30天上线']]
    grp_followed_no_buy = evt_eff[evt_eff['30天业务员跟进'] & ~evt_eff['30天上线']]
    grp_no_follow_bought = evt_eff[~evt_eff['30天业务员跟进'] & evt_eff['30天上线']]
    grp_neither = evt_eff[~evt_eff['30天业务员跟进'] & ~evt_eff['30天上线']]

    tabs = st.tabs([
        f'✅ 跟进+采购 ({len(grp_followed_bought)})',
        f'🚨 跟进未采购 ({len(grp_followed_no_buy)})',
        f'⚠️ 未跟进但采购 ({len(grp_no_follow_bought)})',
        f'❌ 都没动 ({len(grp_neither)})',
    ])
    for tab, sub_df, label in zip(
        tabs,
        [grp_followed_bought, grp_followed_no_buy, grp_no_follow_bought, grp_neither],
        ['跟进+采购', '跟进未采购', '未跟进但采购', '都没动'],
    ):
        with tab:
            if sub_df.empty:
                st.info(f'本场无{label}服务商')
            else:
                show = sub_df[['参会客户名称', '参会客户编码', '参与人姓名',
                               '渠道客户类型_实时', '星级_固化']]
                show.columns = ['服务商', '编码', '到场代表', '客户类型', '星级']
                st.dataframe(show, use_container_width=True, hide_index=True, height=300)
