"""产品专项 page 共享 UI — 3 个专项 page(夜视王 / 无线 / 场景化)调用 render_focus_page(focus)

布局:
  顶部参数(时段 + 地市 + 代理商)
  Tab 1 · 📊 现状(多源数据穿透)
  Tab 2 · 🎯 怎么做(攻坚 + 项目管理,等目标数据)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from _auth import require_auth, is_admin  # noqa: E402
from _loaders import DB_PATH, scoped_cities  # noqa: E402
from _focus_section import gather_focus_data  # noqa: E402
from _focus_loader import (  # noqa: E402
    get_focus_summary,
    import_focus_target_excel,
    generate_target_template_excel,
    load_focus_target,
)
from _provider_quick_view import provider_picker_with_card  # noqa: E402


FOCUS_ICONS = {'夜视王': '🌙', '无线': '📡', '场景化': '🎯'}
FOCUS_DESC = {
    '夜视王': '夜视王专项 — 主打夜间全彩 + 红外,对标海康臻全彩',
    '无线':   '无线专项 — 4G / 物联 / 无线传输系列',
    '场景化': '场景化专项 — 行业 / 特种 / 边缘安装等定制场景',
}


@st.cache_data(ttl=300, show_spinner=False)
def _list_months():
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql(
        "SELECT DISTINCT 上线年月 FROM install_redpack_v WHERE 上线年月 IS NOT NULL ORDER BY 上线年月",
        conn,
    )
    conn.close()
    return df['上线年月'].tolist()


@st.cache_data(ttl=300, show_spinner=False)
def _list_dealers_for_city(city: str):
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql("""
        SELECT 所属一级客户, COUNT(*) AS n
          FROM install_redpack_v
         WHERE 上线客户地市 = ? AND 所属一级客户 IS NOT NULL AND 所属一级客户 != ''
         GROUP BY 1 ORDER BY 2 DESC LIMIT 50
    """, conn, params=(city,))
    conn.close()
    return df['所属一级客户'].tolist()


@st.cache_data(ttl=300, show_spinner=f'正在汇总专项数据…')
def _cached_focus_data(focus: str, period_start: str, period_end: str,
                        city: str = None, dealer: str = None):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return gather_focus_data(conn, focus, period_start, period_end, city, dealer)
    finally:
        conn.close()


def _pct(v, plus_sign=True):
    if v is None:
        return '—'
    sign = '+' if v >= 0 and plus_sign else ''
    return f"{sign}{v * 100:.1f}%"


def _fmt(v, unit='', decimals=1):
    if v is None:
        return '—'
    return f"{v:,.{decimals}f}{unit}"


def render_focus_page(focus: str):
    """专项 page 主体"""
    require_auth()
    if not is_admin():
        st.error("🔒 产品专项攻坚仅管理员（admin）可访问")
        st.stop()
    icon = FOCUS_ICONS.get(focus, '🎯')
    desc = FOCUS_DESC.get(focus, '')

    st.markdown(f"### {icon} {focus}专项")
    st.caption(desc)

    # ─── 顶部参数 ─────────
    months = _list_months()
    if not months:
        st.error("无数据,请先导入 install_redpack")
        return

    cities = scoped_cities([])
    if not cities:
        # 没权限范围 → 退到全省
        conn = sqlite3.connect(str(DB_PATH))
        try:
            cities = pd.read_sql("""
                SELECT DISTINCT 上线客户地市 FROM install_redpack_v
                 WHERE 上线客户地市 IS NOT NULL AND 上线客户地市 != ''
                 ORDER BY 上线客户地市
            """, conn)['上线客户地市'].tolist()
        finally:
            conn.close()

    c1, c2, c3, c4 = st.columns([1.5, 1.5, 2, 2])
    with c1:
        period_start = st.selectbox('📅 评估期 起', months,
                                     index=max(0, len(months) - 4),
                                     key=f'fp_{focus}_ps')
    with c2:
        end_opts = [m for m in months if m >= period_start]
        period_end = st.selectbox('📅 评估期 止', end_opts,
                                   index=min(3, len(end_opts) - 1),
                                   key=f'fp_{focus}_pe')
    with c3:
        city = st.selectbox('🏙️ 地市', ['(全省)'] + cities, key=f'fp_{focus}_city')
        city_v = None if city == '(全省)' else city
    with c4:
        if city_v:
            dealers = ['(全部)'] + _list_dealers_for_city(city_v)
            dealer = st.selectbox('🏢 代理商', dealers, key=f'fp_{focus}_dealer')
            dealer_v = None if dealer == '(全部)' else dealer
        else:
            st.text_input('🏢 代理商', value='(请先选地市)', disabled=True,
                          key=f'fp_{focus}_dealer_dis')
            dealer_v = None

    # ─── 拉数据 ─────────
    data = _cached_focus_data(focus, period_start, period_end, city_v, dealer_v)

    # ─── Tab 切换 ─────────
    tab_now, tab_action = st.tabs(['📊 现状(多源穿透)', '🎯 怎么做(攻坚 / 项目管理)'])

    with tab_now:
        _render_status(data)
    with tab_action:
        _render_action(data)


# ══════════════════════════════════════════════
# Tab 1:📊 现状
# ══════════════════════════════════════════════

def _render_status(data: dict):
    meta = data['_meta']
    k = data['kpi_overview']

    # ─── Section 0:🎯 年度目标进度(头条)─────────
    has_target = k.get('目标_台数') or k.get('目标_货值_万')
    if has_target:
        st.markdown(f"##### 🎯 {k['_target_年度']} 年度目标进度({k['_target_城市']})")
        cols = st.columns(4)
        if k.get('目标_台数'):
            cols[0].metric(
                '年度目标(台)', f"{k['目标_台数']:,}",
                help=f"focus_target 表 · {k['_target_城市']} · 专项 {meta['focus']}",
            )
            cols[1].metric(
                'YTD 实绩(台)',
                f"{k['YTD_台数']:,}",
                delta=_pct(k.get('完成率_台数'), plus_sign=False),
                delta_color='off',
                help=f'YTD = 年初至 {meta["period_end"]} 累计;Δ 是完成率',
            )
            # 进度条
            if k.get('完成率_台数') is not None:
                cols[2].progress(
                    min(k['完成率_台数'], 1.0),
                    text=f"台数完成率 {k['完成率_台数']*100:.1f}%",
                )
        if k.get('目标_货值_万'):
            cols[3].metric(
                '年度目标(万)', f"¥{k['目标_货值_万']:,.0f}",
                delta=_pct(k.get('完成率_货值'), plus_sign=False),
                delta_color='off',
                help=f"YTD 货值 ¥{k['YTD_货值_万']:.1f}万",
            )
        st.divider()
    else:
        st.warning(
            f"⚠️ 该专项 / 该地市 暂无年度目标 — 请在下方「目标数据导入」区上传目标 Excel"
        )

    # ─── Section 0.5:🎯 目标数据导入(展开)─────────
    with st.expander('📥 专项目标数据导入(地市 × 专项 × 年度)', expanded=False):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.caption(
                "录入年度目标(地市级 + 全省合计),系统会自动算 YTD 完成率。"
                "目标 = 全年完成,跟评估期无关。"
            )
        with c2:
            st.download_button(
                '📄 下载录入模板',
                data=generate_target_template_excel(),
                file_name='专项目标录入模板.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                use_container_width=True,
                key=f"tgt_dl_{meta['focus']}",
            )
        upl = st.file_uploader('选择目标 Excel(.xlsx)', type=['xlsx'],
                                key=f"tgt_upl_{meta['focus']}")
        if upl:
            mode = st.radio(
                '导入模式',
                options=['replace', 'merge'],
                format_func=lambda x: {'replace': '🔄 全表替换(同年度)', 'merge': '🔀 合并'}[x],
                horizontal=True, key=f"tgt_mode_{meta['focus']}",
            )
            if st.button('💾 开始导入', type='primary', key=f"tgt_btn_{meta['focus']}"):
                with st.spinner('导入中…'):
                    try:
                        r = import_focus_target_excel(upl, mode=mode)
                        st.success(f"✅ 入库 {r['inserted']} 行,库内总数 {r['total']}")
                        load_focus_target.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ 导入失败:{e}")

    # ─── Section 1:评估期 KPI ─────────
    st.markdown(f"##### 📊 评估期 KPI({meta['period_start']} ~ {meta['period_end']})")
    st.caption(
        f"专项 SKU **{meta['sku_总数']}** 个(在售 {meta['sku_在售']})· "
        f"地域 {meta['city']} / 代理商 {meta['dealer']}"
    )

    cols = st.columns(6)
    cols[0].metric('🌐 出货货值(产品流向)',
                   _fmt(k['出货_万'], ' 万'),
                   delta=_pct(k['出货_同比']),
                   help='product_flow 评估期内该专项出货货值,同比 = vs 去年同期')
    cols[1].metric('🎯 上线货值(红包扫码)',
                   _fmt(k['上线_万'], ' 万'),
                   delta=_pct(k['上线_同比']),
                   help='install_redpack 评估期内该专项实际激活上线')
    cols[2].metric('🔗 绑定率',
                   _pct(k['绑定率'], plus_sign=False),
                   help='上线 ÷ 出货,越高说明产品越精准激活')
    cols[3].metric('👥 活跃服务商', f"{k['活跃服务商数']:,}",
                   help='评估期内有该专项上线的服务商数')
    cols[4].metric('🎁 红包总额', f"¥{k['红包额_元']:,.0f}")
    cols[5].metric('💰 红包 ROI',
                   _pct(k['红包ROI'], plus_sign=False),
                   help='红包额 ÷ 上线货值,越低越好(红包发得少但拉动多)')

    st.divider()

    # ─── Section 1.5:月度节奏 vs 实绩(节奏表) ─────────
    rhythm_rows = data.get('monthly_rhythm') or []
    if rhythm_rows:
        st.markdown(f"##### 📅 月度节奏 vs 实绩({k['_target_城市']} · {k['_target_年度']})")
        st.caption('🛈 月度目标 = 该地市年度目标 × 节奏%。月度实绩 = product_flow 当月该专项上线台数。')
        df_r = pd.DataFrame(rhythm_rows)
        df_r['月'] = df_r['月份'].apply(lambda m: f"{int(m)}月")
        df_r['节奏'] = df_r['占比_pct'].apply(lambda v: f"{v:.1f}%")
        df_r['月度完成率'] = df_r['月度完成率'].apply(
            lambda v: _pct(v, plus_sign=False) if pd.notna(v) else '—'
        )
        df_r['累计完成率'] = df_r['累计完成率'].apply(
            lambda v: _pct(v, plus_sign=False) if pd.notna(v) else '—'
        )

        col_order = ['月', '节奏', '月度目标', '实绩台数', '月度完成率',
                     '累计目标', '累计实绩', '累计完成率']
        st.dataframe(df_r[col_order], use_container_width=True, hide_index=True,
                     height=min(500, 40 + 35 * len(df_r)))

        # 折线图:月度目标 vs 月度实绩
        plot_df = pd.DataFrame(rhythm_rows)[['月份', '月度目标', '实绩台数']].set_index('月份')
        plot_df.columns = ['月度目标', '月度实绩']
        st.line_chart(plot_df, height=240)
        st.divider()

    # ─── Section 2:月度走势 ─────────
    if data.get('monthly_trend'):
        st.markdown('##### 📈 月度走势(近 12 月)')
        df_t = pd.DataFrame(data['monthly_trend'])
        # 转长格式画 line chart
        plot_df = df_t.set_index('月份')[['出货货值_万', '上线货值_万']]
        st.line_chart(plot_df, height=240)
        with st.expander('查看明细数据'):
            st.dataframe(df_t, use_container_width=True, hide_index=True)

    st.divider()

    # ─── Section 3:11 地市分布(仅全省视图,含目标 + 完成率) ─────────
    if data.get('city_breakdown'):
        st.markdown('##### 🗺️ 11 地市分布(评估期 出货 / 上线 / YTD vs 目标)')
        df_c = pd.DataFrame(data['city_breakdown'])
        df_c['绑定率'] = df_c['绑定率'].apply(lambda v: _pct(v, plus_sign=False))
        if '完成率_台数' in df_c.columns:
            df_c['完成率_台数'] = df_c['完成率_台数'].apply(
                lambda v: _pct(v, plus_sign=False) if pd.notna(v) else '—'
            )
        if '完成率_货值' in df_c.columns:
            df_c['完成率_货值'] = df_c['完成率_货值'].apply(
                lambda v: _pct(v, plus_sign=False) if pd.notna(v) else '—'
            )
        for c in ('出货台数', '上线台数', '服务商数', 'YTD台数', '目标台数'):
            if c in df_c.columns:
                df_c[c] = df_c[c].fillna(0).astype(int)

        # 列顺序
        col_order = ['地市',
                     '出货台数', '出货货值_万',
                     '上线台数', '上线货值_万', '服务商数', '绑定率',
                     'YTD台数', 'YTD货值_万',
                     '目标台数', '目标货值_万',
                     '完成率_台数', '完成率_货值']
        col_order = [c for c in col_order if c in df_c.columns]
        st.dataframe(df_c[col_order], use_container_width=True, hide_index=True,
                     height=min(450, 40 + 35 * len(df_c)))
        st.divider()

    # ─── Section 3.5:业务员维度 — 地市级目标分解(focus_target_salesperson) ─────────
    sp_rows = data.get('salesperson_breakdown') or []
    if sp_rows:
        st.markdown('##### 👨‍💼 业务员维度(地市级目标分解 vs YTD 实绩估算)')
        st.caption(
            '🛈 **实绩为估算口径** — 总负责行 = 该地市 YTD 总台数;'
            '分担行 = 地市 YTD × 占比%。精确口径需先建「大华业务员 ↔ 一级代理商」映射表。'
        )
        df_sp = pd.DataFrame(sp_rows)
        df_sp['是否总责'] = df_sp['是否地市总负责'].map({'Y': '🔴 总责', 'N': '分担'})
        df_sp['占比'] = df_sp['占比_pct'].apply(
            lambda v: f"{v:.1f}%" if pd.notna(v) else '—'
        )
        df_sp['完成率_估算'] = df_sp['完成率_估算'].apply(
            lambda v: _pct(v, plus_sign=False) if pd.notna(v) else '—'
        )
        for c in ('目标台数', '地市YTD', '地市评估期', 'YTD台数_估算'):
            if c in df_sp.columns:
                df_sp[c] = df_sp[c].fillna(0).astype(int)

        col_order = ['地市', '业务员', '是否总责', '占比',
                     '目标台数', 'YTD台数_估算', '完成率_估算',
                     '地市YTD', '地市评估期']
        col_order = [c for c in col_order if c in df_sp.columns]
        st.dataframe(df_sp[col_order], use_container_width=True, hide_index=True,
                     height=min(500, 40 + 35 * len(df_sp)))
        st.divider()

    # ─── Section 4:Top 代理商 ─────────
    if data.get('dealer_top'):
        st.markdown('##### 🏢 Top 20 代理商(按出货货值)')
        df_d = pd.DataFrame(data['dealer_top'])
        st.dataframe(df_d, use_container_width=True, hide_index=True,
                     height=min(400, 40 + 35 * len(df_d)))
        st.divider()

    # ─── Section 5:Top 服务商 + 速览卡 ─────────
    if data.get('provider_top100'):
        st.markdown(f"##### 🎯 Top 100 服务商(按上线货值)— 共 {data['kpi_overview']['活跃服务商数']:,} 家活跃")
        st.caption('点行 → 速览卡(基础信息 / RFM / 跑动 / 采购 / 推广会),时段已锁定评估期')
        df_p = pd.DataFrame(data['provider_top100'])
        provider_picker_with_card(
            df_p, code_col='客户编码', name_col='客户名称',
            key=f"focus_top_{meta['focus']}", height=400,
            default_period_start=meta['period_start'],
            default_period_end=meta['period_end'],
        )
        st.divider()

    # ─── Section 6:SKU 销售排行 ─────────
    if data.get('sku_top50'):
        st.markdown(f"##### 📦 Top 50 SKU(按上线货值)— 共 {meta['sku_总数']} 个 SKU")
        cols = st.columns(2)
        # 状态分布
        status = data.get('sku_status') or {}
        if status:
            cols[0].markdown('**SKU 状态分布**')
            df_s = pd.DataFrame([
                {'状态': k, '数量': v} for k, v in status.items()
            ])
            cols[0].dataframe(df_s, use_container_width=True, hide_index=True)
        with cols[1]:
            tier = data.get('provider_tier') or {}
            if tier:
                st.markdown('**服务商等级分布(卖该专项的)**')
                order = ['V4', 'V3', 'V2', '已激活', 'v0']
                df_tier = pd.DataFrame([
                    {'等级': t, '数量': int(tier.get(t, 0))}
                    for t in order if tier.get(t, 0) > 0
                ])
                if not df_tier.empty:
                    st.dataframe(df_tier, use_container_width=True, hide_index=True)
        # Top SKU 表
        df_sku = pd.DataFrame(data['sku_top50'])
        st.dataframe(df_sku, use_container_width=True, hide_index=True,
                     height=min(450, 40 + 35 * len(df_sku)))
        st.divider()

    # ─── Section 7:跑动覆盖 ─────────
    vc = data.get('visit_coverage')
    if vc:
        st.markdown('##### 👣 跑动覆盖(卖该专项的服务商,评估期内业务员是否到访)')
        cols = st.columns(4)
        cols[0].metric('🏢 大华到访', f"{vc['大华业务员到访数']} 家",
                       delta=_pct(vc['大华业务员到访率'], plus_sign=False),
                       delta_color='off')
        cols[1].metric('🏪 代理商到访', f"{vc['代理商业务员到访数']} 家",
                       delta=_pct(vc['代理商业务员到访率'], plus_sign=False),
                       delta_color='off')
        cols[2].metric('✅ 任一到访', f"{vc['任一到访数']} 家",
                       delta=_pct(vc['任一到访率'], plus_sign=False),
                       delta_color='off')
        cols[3].metric('🚨 未拜访', f"{vc['未拜访数']} 家",
                       help='评估期内卖了该专项但没业务员拜访 — 攻坚漏洞')
        st.divider()

    # ─── Section 8:推广会覆盖 ─────────
    pc = data.get('promotion_coverage')
    if pc:
        st.markdown('##### 🎤 推广会覆盖(卖该专项的服务商,历史参会情况)')
        cols = st.columns(3)
        cols[0].metric('总服务商', f"{pc['总服务商数']}")
        cols[1].metric('参加过推广会', f"{pc['参加过推广会数']}")
        cols[2].metric('覆盖率', _pct(pc['覆盖率'], plus_sign=False))


# ══════════════════════════════════════════════
# Tab 2:🎯 怎么做(项目管理 + 攻坚)
# ══════════════════════════════════════════════

def _render_action(data: dict):
    meta = data['_meta']
    st.markdown(f"##### 🎯 {meta['focus']}专项攻坚(项目管理)")

    st.info(
        "**Phase 2 待启动** — 攻坚名单 + 项目管理依赖以下数据,等用户提供后实施:\n\n"
        "1. **专项年度目标**(全省 + 11 地市,台数或货值)\n"
        "2. **核心/培育阈值**(默认 5 万,可调)\n"
        "3. **同类产品定义**(用于「机会客户」判定)\n"
        "4. (夜视王专项)战役 Owner / KR / 试点城市 / 资源包 / 臻全彩竞品情报 / 调研问卷\n\n"
        "**M13 设计已就绪**:见 TODO.md §13.1-13.9。"
    )

    st.markdown('**预计内容**(数据到位后)')
    st.markdown("""
- **🎯 目标进度看板**:年目标 / YTD 达成 / 完成率(进度条)/ 同比 / 渗透率
- **📊 战场地图 4 档分布**(BCG):
    - 🌟 核心(卖该专项 + 货值 ≥ X)
    - 🌱 培育(卖该专项 + 货值 < X)
    - 🎯 机会(没卖该专项 + 卖过同类)— **攻坚重点**
    - ❌ 休眠(都没卖)
- **🎯 攻坚优先级名单**:智能排序(同类货值 / 服务商等级 / 阵地标签),Top N 录入 `focus_attack_plan`
- **📋 进度跟踪表**:责任人 / 状态(待启动/进行中/已成交/暂缓/放弃)/ 行动计划 / 已成交台
- **📅 月度复盘 PDCA**:新增/成交/停滞/放弃
""")

    # 临时提供:让用户先看到现状中"已卖该专项的高价值服务商"
    if data.get('provider_top100'):
        st.divider()
        st.markdown('##### 💡 临时:已卖该专项 Top 服务商(供参考)')
        st.caption(
            '在 Phase 2 完成前,可先把这批"核心客户"作为维护对象。'
            '"机会客户"(没卖但卖过同类的)需要 Phase 2 的 4 档算法。'
        )
        df = pd.DataFrame(data['provider_top100'])
        provider_picker_with_card(
            df.head(30), code_col='客户编码', name_col='客户名称',
            key=f"focus_action_top_{meta['focus']}", height=400,
            default_period_start=meta['period_start'],
            default_period_end=meta['period_end'],
        )
