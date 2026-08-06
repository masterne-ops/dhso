"""服务商速览卡 — 选中服务商后展示的统一组件,可嵌入多个 page

设计原则:
- 紧凑:5-6 个 section 一屏内呈现
- 独立 query:接受服务商编码,自己查所有数据(不依赖外部传入)
- 缓存:@st.cache_data ttl=300,按 code 缓存
- 可复用:渠道健康度 / 服务商档案 / 阵地沙盘 / 推广会 / 爆款穿透 等多 page 共用
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402
from _metrics_rfm import calc_rfm, r_grade, f_grade  # noqa: E402

# 原始服务商等级映射:provider_contract.服务商等级 取值 v0服务商~v5服务商 → V0-V5
_TIER_LABEL = {f'v{i}服务商': f'V{i}' for i in range(6)}


@st.cache_data(ttl=3600, show_spinner=False)
def _get_month_options() -> list:
    """从 install_redpack_v 拿所有月份选项,降序"""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    try:
        df = pd.read_sql(
            "SELECT DISTINCT 上线年月 FROM install_redpack_v WHERE 上线年月 IS NOT NULL ORDER BY 上线年月",
            conn,
        )
    finally:
        conn.close()
    return df['上线年月'].tolist()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_provider_quick(service_code: str,
                          period_start: str = None,
                          period_end: str = None) -> dict:
    """一次性把单服务商的所有详情数据查出来。

    Args:
        service_code: 服务商编码
        period_start / period_end: 'YYYY-MM' 格式;不传 = 全历史模式

    period 影响:
      - 当期 SO 走势(月度柱状图):只显示 period_start ~ period_end 区间
      - 最近 N 次跑动:period 内的拜访
      - Top 采购产品:period 内的采购
      - 推广会:period 内的参会
      - 基础信息 / 等级 / 累计 / RFM 仍按全历史(等级是历史定义)
    """
    if not service_code:
        return {}
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur0 = conn.cursor()
        out = {'_code': service_code}

        # 基础信息(provider_profile 主)
        pp = pd.read_sql("""
            SELECT * FROM provider_profile WHERE 客户编码 = ? LIMIT 1
        """, conn, params=(service_code,))
        out['_profile'] = pp.iloc[0].to_dict() if not pp.empty else {}

        # 最新所属代理商 + 客户类型(install_redpack 最末一条)
        latest = pd.read_sql("""
            SELECT 上线客户名称, 上线客户地市, 上线客户区县,
                   上线客户渠道客户类型 AS 客户类型,
                   所属一级客户, 所属一级客户编码
              FROM install_redpack_v
             WHERE 上线客户编码 = ?
             ORDER BY 上线时间 DESC LIMIT 1
        """, conn, params=(service_code,))
        out['_latest'] = latest.iloc[0].to_dict() if not latest.empty else {}

        # 累计数据 / RFM
        agg = pd.read_sql("""
            SELECT COUNT(*) AS 累计台数,
                   ROUND(SUM(产品现有分销价)/10000, 2) AS 累计货值_万,
                   MIN(上线时间) AS 首次上线,
                   MAX(上线时间) AS 最近上线,
                   SUM(中奖金额) AS 累计红包_元
              FROM install_redpack_v
             WHERE 上线客户编码 = ?
        """, conn, params=(service_code,))
        out['_agg'] = agg.iloc[0].to_dict() if not agg.empty else {}

        # 等级 + RFM(全部走 _metrics_rfm,口径统一)
        rfm_raw = pd.read_sql("""
            SELECT 上线时间, 产品现有分销价
              FROM install_redpack_v
             WHERE 上线客户编码 = ?
        """, conn, params=(service_code,))

        # 服务商等级展示 = 原始(provider_contract.服务商等级, 26年官方评定 v0~v5服务商→V0-V5)
        # 注意:不再用累计货值现算(那是「货值档」,口径不同)
        _grade_raw = None
        try:
            _gr = cur0.execute(
                "SELECT 服务商等级 FROM provider_contract WHERE 客户编码 = ? LIMIT 1",
                (service_code,),
            ).fetchone()
            if _gr and _gr[0] is not None and str(_gr[0]).strip() not in ('', 'nan'):
                _gr_val = str(_gr[0]).strip()
                # v{N}服务商 → V{N};其他取值原样
                _grade_raw = _TIER_LABEL.get(_gr_val, _gr_val)
        except Exception:
            _grade_raw = None
        out['_tier'] = _grade_raw or '—'

        if not rfm_raw.empty:
            rfm_raw['上线时间'] = pd.to_datetime(rfm_raw['上线时间'], errors='coerce')
            # ref date = 数据集全局最新日(让"流失"和"R" 口径一致)
            global_max = pd.read_sql(
                "SELECT MAX(上线时间) AS d FROM install_redpack_v",
                conn,
            ).iloc[0]['d']
            ref_date = pd.to_datetime(global_max) if global_max else pd.Timestamp.now()
            # 用一个伪 asof = ref_date + 1ns,让 calc_rfm 把最后一次上线计入"before"
            asof = ref_date + pd.Timedelta(nanoseconds=1)
            r = calc_rfm(rfm_raw, asof, with_growth=False)
            if r:
                out['_rfm'] = {
                    'R': r['R'], 'F': r['F'], 'M_万': r['M_万'],
                    '_ref_date': ref_date.strftime('%Y-%m-%d'),
                    '_last_active': r['_last_active'].strftime('%Y-%m-%d')
                                    if pd.notna(r['_last_active']) else None,
                }
            else:
                out['_rfm'] = {'R': None, 'F': 0, 'M_万': 0,
                               '_ref_date': ref_date.strftime('%Y-%m-%d'),
                               '_last_active': None}
        else:
            out['_rfm'] = {'R': None, 'F': 0, 'M_万': 0,
                           '_ref_date': None, '_last_active': None}

        # ─── period filter 组件 ───
        has_period = bool(period_start and period_end)
        period_clause_rp = ''
        period_clause_visit = ''
        period_clause_pm_start = ''
        period_clause_pm_end = ''
        period_params: list = []
        if has_period:
            period_clause_rp = ' AND 上线年月 BETWEEN ? AND ?'
            period_clause_visit = ' AND 拜访年月 BETWEEN ? AND ?'
            # promotion_meeting 用 活动开始时间(text 比较 YYYY-MM)
            period_clause_pm_start = period_start + '-01'
            period_clause_pm_end = period_end + '-31 23:59:59'

        # 月度走势:有 period → 显示区间内月度;无 → 全历史最近 12 月
        if has_period:
            trend = pd.read_sql(f"""
                SELECT 上线年月 AS 月份,
                       COUNT(*) AS 台数,
                       ROUND(SUM(产品现有分销价)/10000, 2) AS 货值_万
                  FROM install_redpack_v
                 WHERE 上线客户编码 = ?
                   AND 上线年月 BETWEEN ? AND ?
                 GROUP BY 上线年月
                 ORDER BY 上线年月
            """, conn, params=(service_code, period_start, period_end))
        else:
            trend = pd.read_sql("""
                SELECT 上线年月 AS 月份,
                       COUNT(*) AS 台数,
                       ROUND(SUM(产品现有分销价)/10000, 2) AS 货值_万
                  FROM install_redpack_v
                 WHERE 上线客户编码 = ?
                   AND 上线年月 IS NOT NULL
                 GROUP BY 上线年月
                 ORDER BY 上线年月 DESC LIMIT 12
            """, conn, params=(service_code,))
            trend = trend.sort_values('月份')
        out['_trend'] = trend.to_dict('records') if not trend.empty else []

        # 最近 N 次跑动(period 内全量,否则最近 10 次)
        if has_period:
            visits = pd.read_sql(f"""
                SELECT 拜访时间, 打卡人姓名 AS 业务员, _打卡方 AS 所属,
                       COALESCE(拜访目的, '') AS 目的,
                       COALESCE(达成结果, '') AS 结果
                  FROM visit_record_v
                 WHERE 客户编码 = ?
                   AND _打卡异常无效 = 0 AND _真异常打卡 = 0
                   AND 拜访年月 BETWEEN ? AND ?
                 ORDER BY 拜访时间 DESC
            """, conn, params=(service_code, period_start, period_end))
        else:
            visits = pd.read_sql("""
                SELECT 拜访时间, 打卡人姓名 AS 业务员, _打卡方 AS 所属,
                       COALESCE(拜访目的, '') AS 目的,
                       COALESCE(达成结果, '') AS 结果
                  FROM visit_record_v
                 WHERE 客户编码 = ?
                   AND _打卡异常无效 = 0 AND _真异常打卡 = 0
                 ORDER BY 拜访时间 DESC LIMIT 10
            """, conn, params=(service_code,))
        if not visits.empty:
            visits['拜访时间'] = pd.to_datetime(visits['拜访时间'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M')
        out['_visits'] = visits.to_dict('records') if not visits.empty else []

        # Top 10 采购产品系列(有 period 则 period 内)
        if has_period:
            prods = pd.read_sql(f"""
                SELECT COALESCE([产品子系列-新], 产品子系列, '未分类') AS 子系列,
                       COUNT(*) AS 台数,
                       ROUND(SUM(产品现有分销价)/10000, 2) AS 货值_万,
                       MAX(产品名称) AS 产品名称样例
                  FROM install_redpack_v
                 WHERE 上线客户编码 = ?
                   AND 上线年月 BETWEEN ? AND ?
                 GROUP BY 1
                 ORDER BY 货值_万 DESC LIMIT 10
            """, conn, params=(service_code, period_start, period_end))
        else:
            prods = pd.read_sql("""
                SELECT COALESCE([产品子系列-新], 产品子系列, '未分类') AS 子系列,
                       COUNT(*) AS 台数,
                       ROUND(SUM(产品现有分销价)/10000, 2) AS 货值_万,
                       MAX(产品名称) AS 产品名称样例
                  FROM install_redpack_v
                 WHERE 上线客户编码 = ?
                 GROUP BY 1
                 ORDER BY 货值_万 DESC LIMIT 10
            """, conn, params=(service_code,))
        out['_products'] = prods.to_dict('records') if not prods.empty else []

        # 推广会参与(有 period 则 period 内)
        if has_period:
            pm = pd.read_sql(f"""
                SELECT 活动名称, 活动开始时间, 主办方代理商,
                       签到时间 IS NOT NULL AS 是否签到
                  FROM promotion_meeting
                 WHERE 参会客户编码 = ?
                   AND 活动开始时间 >= ? AND 活动开始时间 <= ?
                 ORDER BY 活动开始时间 DESC
            """, conn, params=(service_code, period_clause_pm_start, period_clause_pm_end))
        else:
            pm = pd.read_sql("""
                SELECT 活动名称, 活动开始时间, 主办方代理商,
                       签到时间 IS NOT NULL AS 是否签到
                  FROM promotion_meeting
                 WHERE 参会客户编码 = ?
                 ORDER BY 活动开始时间 DESC LIMIT 10
            """, conn, params=(service_code,))
        if not pm.empty:
            pm['活动开始时间'] = pd.to_datetime(pm['活动开始时间'], errors='coerce').dt.strftime('%Y-%m-%d')
            pm['是否签到'] = pm['是否签到'].astype(int)
        out['_promotions'] = pm.to_dict('records') if not pm.empty else []
        out['_pm_count'] = int(pm['是否签到'].sum()) if not pm.empty else 0
        out['_pm_total'] = len(pm)
        out['_period'] = {'start': period_start, 'end': period_end} if has_period else None

        # 异常标签
        cur = conn.cursor()
        is_vest = cur.execute(
            "SELECT 1 FROM vest_account WHERE 服务商客户编码 = ? LIMIT 1",
            (service_code,),
        ).fetchone() is not None
        is_closed = cur.execute(
            "SELECT 1 FROM closed_provider WHERE 客户编码 = ? LIMIT 1",
            (service_code,),
        ).fetchone() is not None
        out['_vest'] = is_vest
        out['_closed'] = is_closed

        # 阵地档案(如已建档)
        bf = pd.read_sql("""
            SELECT * FROM dealer_battlefield WHERE 服务商编码 = ? LIMIT 1
        """, conn, params=(service_code,))
        out['_battlefield'] = bf.iloc[0].to_dict() if not bf.empty else {}

        return out
    finally:
        conn.close()


def render_provider_quick_view(service_code: str, key_prefix: str = 'pqv',
                                 default_period_start: str = None,
                                 default_period_end: str = None):
    """渲染服务商速览卡 — 紧凑布局,5 个 section。

    Args:
        service_code: 服务商编码
        key_prefix: streamlit 控件唯一 key 前缀
        default_period_start / default_period_end: 'YYYY-MM',默认时段
            (通常从 page 上下文传入,如代理商全景的评估期)
    """
    if not service_code:
        return

    # ─── 时段筛选器(默认折叠,可调)─────
    # 取数据库月份范围作为可选项
    period_start = default_period_start
    period_end = default_period_end
    use_period = bool(default_period_start and default_period_end)

    with st.expander(
        f"📅 时段筛选(当前:{'全历史' if not use_period else f'{period_start} ~ {period_end}'})",
        expanded=False,
    ):
        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            month_options = _get_month_options()
            ps_default = period_start if period_start in month_options else (month_options[-4] if len(month_options) >= 4 else month_options[0] if month_options else '')
            ps = st.selectbox(
                '起 (YYYY-MM)', [''] + month_options,
                index=(month_options.index(ps_default) + 1) if ps_default in month_options else 0,
                key=f'{key_prefix}_pqv_period_start',
            )
        with c2:
            pe_default = period_end if period_end in month_options else (month_options[-1] if month_options else '')
            pe = st.selectbox(
                '止 (YYYY-MM)', [''] + month_options,
                index=(month_options.index(pe_default) + 1) if pe_default in month_options else 0,
                key=f'{key_prefix}_pqv_period_end',
            )
        with c3:
            st.markdown(' ')
            st.markdown(' ')
            if st.button('🔄 重置(全历史)', key=f'{key_prefix}_pqv_reset',
                         use_container_width=True):
                ps = ''
                pe = ''
        if ps and pe:
            if ps > pe:
                st.error('起始月份不能大于终止月份')
            else:
                period_start, period_end = ps, pe
                use_period = True
        elif not (default_period_start and default_period_end):
            use_period = False
            period_start = None
            period_end = None

    data = fetch_provider_quick(
        service_code,
        period_start=period_start if use_period else None,
        period_end=period_end if use_period else None,
    )
    if not data:
        st.info(f"未找到服务商 {service_code}")
        return

    profile = data.get('_profile', {})
    latest = data.get('_latest', {})
    agg = data.get('_agg', {})
    rfm = data.get('_rfm', {})
    bf = data.get('_battlefield', {})

    name = profile.get('公司名称') or latest.get('上线客户名称') or '(无名称)'
    city = profile.get('地市') or latest.get('上线客户地市') or ''
    district = profile.get('区县') or latest.get('上线客户区县') or ''
    addr = profile.get('详细地址') or ''
    boss_name = profile.get('老板姓名') or ''
    boss_phone = profile.get('老板电话') or ''
    dealer = latest.get('所属一级客户') or ''
    cust_type = latest.get('客户类型') or ''
    tier = data.get('_tier', '—')  # 原始服务商等级(provider_contract,V0-V5)

    # 标签
    badges = []
    if data.get('_vest'): badges.append('🎭 马甲')
    if data.get('_closed'): badges.append('🚫 已关闭')
    if bf:
        bf_type = bf.get('类型') or ''
        bf_own = bf.get('归属') or ''
        if bf_type: badges.append(f'⚔️ {bf_type}')
        if bf_own: badges.append(f'·{bf_own}')

    badge_str = ' '.join(badges) if badges else ''

    # ─── 顶部一行:名称 + 标签 ─────
    st.markdown(f"### 🔎 {name}  {badge_str}")
    st.caption(
        f"📍 {city} / {district}  ·  📋 {cust_type}  ·  🏷 等级 **{tier}**  ·  "
        f"🏢 所属代理商:**{dealer or '(无签约)'}**  ·  "
        f"📞 {boss_name} {boss_phone}  ·  🏠 {addr[:50]}"
    )

    # ─── KPI 一行 6 个 ─────
    c = st.columns(6)
    c[0].metric("累计货值",
                f"¥{agg.get('累计货值_万') or 0:.1f}万",
                help=f"累计 {int(agg.get('累计台数') or 0):,} 台")
    r_val = rfm.get('R')
    r_tag = '🟢' if r_val is not None and r_val <= 30 else ('🟡' if r_val is not None and r_val <= 60 else '🔴')
    c[1].metric("R 最近距今",
                f"{r_val} 天" if r_val is not None else '—',
                help=(
                    f"R档:{r_tag}(🟢≤30 / 🟡30-60 / 🔴>60)\n\n"
                    f"参考日(数据集最新):{rfm.get('_ref_date') or '—'}\n"
                    f"该服务商最后上线:{rfm.get('_last_active') or '—'}"
                ))
    c[2].metric("F 近12月活跃天",
                f"{int(rfm.get('F') or 0)}",
                help=f"F档:{'🟢' if (rfm.get('F') or 0) >= 12 else '🟡' if (rfm.get('F') or 0) >= 3 else '🔴'}")
    c[3].metric("M 近12月货值", f"¥{rfm.get('M_万', 0):.1f}万")
    c[4].metric("累计红包",
                f"¥{(agg.get('累计红包_元') or 0):.0f} 元")
    c[5].metric("推广会",
                f"{data.get('_pm_count', 0)}/{data.get('_pm_total', 0)}",
                help="签到/总报名")

    # ─── 月度走势(SVG 小图)─────
    trend = data.get('_trend', [])
    if trend:
        st.markdown("**📈 近 12 月走势**")
        w, h = 720, 100
        pad_l, pad_r, pad_t, pad_b = 40, 10, 8, 20
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b
        vals = [t['货值_万'] or 0 for t in trend]
        max_v = max(vals) * 1.15 or 1
        bar_w = max(15, plot_w / max(len(trend), 1) * 0.7)
        bars = []
        for i, t in enumerate(trend):
            x = pad_l + (i + 0.5) * plot_w / len(trend) - bar_w / 2
            v = vals[i]
            y = pad_t + plot_h - (v / max_v * plot_h)
            bars.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                f'height="{plot_h - (y - pad_t):.1f}" fill="#1f6feb" rx="2"/>'
                f'<text x="{x + bar_w/2:.1f}" y="{y - 2:.1f}" text-anchor="middle" '
                f'font-size="9" fill="#444">{v:.1f}</text>'
            )
        x_labels = ''.join(
            f'<text x="{pad_l + (i + 0.5) * plot_w / len(trend):.1f}" y="{h - 4}" '
            f'text-anchor="middle" font-size="9" fill="#666">{t["月份"][-5:]}</text>'
            for i, t in enumerate(trend)
        )
        svg = (
            f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:{h}px">'
            f'{"".join(bars)}{x_labels}</svg>'
        )
        st.markdown(svg, unsafe_allow_html=True)

    # ─── 跑动 + 采购 并排 ─────
    cols = st.columns(2)
    with cols[0]:
        st.markdown(f"**👣 最近跑动({len(data.get('_visits', []))} 次)**")
        if data.get('_visits'):
            df = pd.DataFrame(data['_visits'])
            st.dataframe(df, use_container_width=True, hide_index=True,
                         height=min(280, 40 + 35 * len(df)),
                         column_config={
                             '业务员': st.column_config.TextColumn(width='small'),
                             '所属': st.column_config.TextColumn(width='small'),
                             '目的': st.column_config.TextColumn(width='small'),
                             '结果': st.column_config.TextColumn(width='medium'),
                         })
        else:
            st.caption("无跑动记录")

    with cols[1]:
        st.markdown(f"**📦 Top 采购产品({len(data.get('_products', []))} 个系列)**")
        if data.get('_products'):
            df = pd.DataFrame(data['_products'])
            st.dataframe(df[['子系列', '台数', '货值_万']],
                         use_container_width=True, hide_index=True,
                         height=min(280, 40 + 35 * len(df)))
        else:
            st.caption("无采购记录")

    # ─── 阵地档案(已建档时显示)─────
    if bf:
        st.markdown("**⚔️ 阵地档案**")
        c = st.columns(6)
        c[0].metric("类型", bf.get('类型') or '—')
        c[1].metric("归属", bf.get('归属') or '—')
        c[2].metric("价值", bf.get('价值') or '—')
        c[3].metric("趋势", bf.get('趋势') or '—')
        c[4].metric("主力品牌", bf.get('主力采购品牌') or '—')
        c[5].metric("大华占比%", f"{bf.get('大华占比_pct') or 0:.1f}")
        if bf.get('争取策略') or bf.get('备注'):
            st.caption(f"🎯 策略:{bf.get('争取策略') or '—'}  ·  📝 备注:{bf.get('备注') or '—'}")

    # ─── 推广会参与(折叠)─────
    if data.get('_promotions'):
        with st.expander(f"🎤 推广会参与明细({len(data['_promotions'])} 场)"):
            df = pd.DataFrame(data['_promotions'])
            st.dataframe(df, use_container_width=True, hide_index=True,
                         height=min(300, 40 + 35 * len(df)))


def provider_picker_with_card(
    df: pd.DataFrame,
    code_col: str = '客户编码',
    name_col: str = '客户名称',
    key: str = 'provider_picker',
    height: int = 400,
    show_index_col: bool = False,
    default_period_start: str = None,
    default_period_end: str = None,
):
    """组合控件:展示表格,选中一行后下方自动显示服务商速览卡

    Args:
        df: DataFrame,必须含 code_col(服务商编码)
        code_col: 服务商编码列名(默认'客户编码')
        name_col: 服务商名称列名(默认'客户名称')
        key: streamlit 控件唯一 key
        height: 表格高度
        show_index_col: 是否显示索引列
        default_period_start / default_period_end: 速览卡默认时段(YYYY-MM)
            通常从 page 上下文(panorama 评估期)透传
    """
    if df is None or df.empty:
        st.info("无数据")
        return

    event = st.dataframe(
        df, use_container_width=True, hide_index=not show_index_col,
        height=height,
        on_select='rerun',
        selection_mode='single-row',
        key=key,
    )

    selected_code = None
    if event and event.selection and event.selection.rows:
        idx = event.selection.rows[0]
        try:
            selected_code = str(df.iloc[idx][code_col])
        except Exception:
            selected_code = None

    if selected_code:
        with st.container(border=True):
            render_provider_quick_view(
                selected_code, key_prefix=key,
                default_period_start=default_period_start,
                default_period_end=default_period_end,
            )
    else:
        st.caption("💡 选中上方任意一行,下方将展示该服务商的详细画像(基础信息 / RFM / 走势 / 跑动 / 采购 / 阵地 / 推广会),可调时段筛选")
