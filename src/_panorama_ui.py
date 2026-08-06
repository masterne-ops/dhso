"""全景图 UI 渲染层 — 4 个 tab 通用模板，被 page 18/19/20 调用"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from _provider_quick_view import provider_picker_with_card  # noqa: E402


# ─── 通用 helper:列表 + 选中后弹详情卡 ─────────────
# Module-level 缓存当前 panorama 的 period,自动透传给所有 picker
_CURRENT_PERIOD: tuple = (None, None)


def _set_panorama_period(period_start, period_end):
    global _CURRENT_PERIOD
    _CURRENT_PERIOD = (period_start, period_end)


def _provider_table_with_card(df: pd.DataFrame, key: str, height: int = 320,
                              prefer_codes: tuple = ('客户编码', '服务商编码', '编码', '上线客户编码'),
                              period_start: str = None,
                              period_end: str = None):
    """列表 + 行选中触发服务商速览卡。自动识别编码列。

    period 透传给速览卡作为默认时段。
    period_start/end 不传时,从 _CURRENT_PERIOD(render_panorama 设置)取。
    """
    if df is None or df.empty:
        st.info("无数据")
        return
    if period_start is None and period_end is None:
        period_start, period_end = _CURRENT_PERIOD
    code_col = next((c for c in prefer_codes if c in df.columns), df.columns[0])
    name_col = next((c for c in ['客户名称', '服务商名称', '上线客户名称', '名称', '参会客户名称'] if c in df.columns), code_col)
    provider_picker_with_card(
        df, code_col=code_col, name_col=name_col,
        key=key, height=height,
        default_period_start=period_start,
        default_period_end=period_end,
    )


def _pct(v, plus_sign=True):
    if v is None:
        return 'N/A'
    sign = '+' if v >= 0 and plus_sign else ''
    return f"{sign}{v*100:.1f}%"


def _fmt(v, unit='', decimals=1):
    if v is None:
        return 'N/A'
    return f"{v:,.{decimals}f}{unit}"


def render_panorama(data: dict):
    """渲染全景图 tab。data = gather_panorama() 返回值
    - 代理商级别:首 tab 为 「📋 业务对标」
    - 城市/区县级别:首 tab 为 「📊 SO 增长」
    """
    meta = data['_meta']
    st.markdown(f"#### 📍 {meta['实体']} · {meta['period_start']} ~ {meta['period_end']}")

    # 设置全局 period,所有 _provider_table_with_card 调用自动透传给速览卡
    _set_panorama_period(meta.get('period_start'), meta.get('period_end'))

    is_dealer = bool(meta.get('dealer'))

    _render_authorized_provider(
        data.get('authorized_provider', {}),
        is_dealer=is_dealer,
        breakdown=data.get('authorized_provider_breakdown'),
    )
    st.divider()

    if is_dealer:
        tab_names = [
            "📋 业务对标", "⚔️ 阵地沙盘", "📊 SO 增长", "🆕 渠道健康度",
            "🎯 服务商档案", "👣 销售跑动", "🎁 红包投放",
            "🎤 推广会覆盖", "🥊 竞品开拓",
        ]
        tabs = st.tabs(tab_names)
        with tabs[0]:
            _render_dealer_benchmark(data.get('dealer_benchmark', {}))
        with tabs[1]:
            _render_battlefield(data.get('battlefield', {}), meta=meta)
        with tabs[2]:
            _render_so_growth(data['so_growth'])
        with tabs[3]:
            _render_channel_health(data.get('channel_health', {}), data.get('channel_health_cert'))
        with tabs[4]:
            _render_provider_quality(
                data['provider_quality'], is_dealer=True,
                battlefield=data.get('battlefield', {}),
            )
        with tabs[5]:
            _render_sales_visits(data['sales_visits'], is_dealer=True)
        with tabs[6]:
            _render_redpack(data['redpack'])
        with tabs[7]:
            _render_promotion_coverage(data.get('promotion_coverage', {}))
        with tabs[8]:
            _render_competitor_top(data.get('competitor_top', {}))
    else:
        tabs = st.tabs([
            "📊 SO 增长", "🆕 渠道健康度", "🎯 服务商质量", "🎁 红包投放",
            "👣 销售跑动", "⚔️ 竞品开拓",
        ])
        with tabs[0]:
            _render_so_growth(data['so_growth'])
        with tabs[1]:
            _render_channel_health(data.get('channel_health', {}), data.get('channel_health_cert'))
        with tabs[2]:
            _render_provider_quality(data['provider_quality'])
        with tabs[3]:
            _render_redpack(data['redpack'])
        with tabs[4]:
            _render_sales_visits(data['sales_visits'])
        with tabs[5]:
            _render_competitor_top(data.get('competitor_top', {}))


def _render_authorized_provider(
    s: dict, is_dealer: bool = False, breakdown: dict | None = None,
):
    """四级全景统一展示授权服务商签约覆盖和激活转化。"""
    if not s:
        return
    st.markdown("##### 🛡️ 授权服务商覆盖")
    cols = st.columns(5)
    estimate = s.get('测算服务商数')
    cols[0].metric(
        "测算服务商", f"{estimate:,}" if estimate is not None else "—",
        help=("代理商测算取 dealer_sandbox.下游服务商总数；未维护时不计算渗透率。"
              if is_dealer else "区域测算取 district_base.服务商体量。"),
    )
    cols[1].metric(
        "授权签约", f"{s.get('授权签约数', 0):,}",
        help="provider_contract.管理标签='授权服务商'；不含授牌服务商。",
    )
    cols[2].metric(
        "授权渗透率", _pct(s.get('授权渗透率'), plus_sign=False),
        help="授权签约 ÷ 测算服务商。比率不封顶；测算未维护时显示 N/A。",
    )
    cols[3].metric(
        "授权激活", f"{s.get('授权激活数', 0):,}",
        help="授权签约中 provider_contract.是否激活='Y'。",
    )
    cols[4].metric(
        "授权激活率", _pct(s.get('授权激活率'), plus_sign=False),
        help="授权激活 ÷ 授权签约。",
    )
    details = (breakdown or {}).get('明细') or []
    if details:
        level = breakdown.get('层级', '下级')
        st.markdown(f"###### {level}授权服务商覆盖明细")
        df = pd.DataFrame(details)
        for col in ['授权渗透率', '授权激活率']:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda value: _pct(value, plus_sign=False) if pd.notna(value) else '—'
                )
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=min(460, 40 + 35 * len(df)))


# ───────────────────────────────────────────────
# Tab 1：SO 增长
# ───────────────────────────────────────────────
def _render_so_growth(s: dict):
    口径 = s.get('_主口径', '全量感知')

    # ─── SO 目标进度条(年度 + YTD)──
    if s.get('年度目标_万'):
        st.markdown('##### 🎯 SO 目标进度')
        gc = st.columns(4)
        gc[0].metric('年度目标', _fmt(s['年度目标_万'], ' 万'))
        gc[1].metric('YTD 累计实际', _fmt(s.get('YTD累计_万'), ' 万'),
                     delta=_pct(s.get('年完成率'), plus_sign=False),
                     delta_color='off', help='YTD 累计 / 年度目标')
        gc[2].metric('YTD 应达成', _fmt(s.get('YTD应达成_万'), ' 万'),
                     help='年初至评估期末按节奏累计应完成')
        gc[3].metric('YTD 进度完成率', _pct(s.get('YTD完成率'), plus_sign=False),
                     help='YTD 累计 / YTD 应达成')

        ytd_rate = s.get('YTD完成率') or 0
        year_rate = s.get('年完成率') or 0
        bc1, bc2 = st.columns(2)
        with bc1:
            st.progress(min(ytd_rate, 1.0),
                         text=f"YTD 进度 {ytd_rate*100:.1f}% (vs 节奏)")
        with bc2:
            st.progress(min(year_rate, 1.0),
                         text=f"年度达成 {year_rate*100:.1f}% (vs 年度目标)")
        st.divider()

    st.caption(f"主口径：**{口径}**")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(f"当期 SO（{口径}）", _fmt(s['当期_主口径_万'], ' 万'))
    c2.metric("同期", _fmt(s['同期_万'], ' 万'),
              delta=_pct(s['同比']), help="同比 = 当期 vs 去年同期")
    c3.metric("环期", _fmt(s['环期_万'], ' 万'),
              delta=_pct(s['环比']), help="环比 = 当期 vs 紧邻当期前 N 个月")
    if s['应达成_万']:
        c4.metric("当期应达成", _fmt(s['应达成_万'], ' 万'),
                   help='评估期内月份按节奏应完成')
        c5.metric("当期完成率", _pct(s['完成率'], plus_sign=False))
    else:
        c4.metric("当期应达成", "—", help="该实体未配置目标")
        c5.metric("当期完成率", "—")

    # 月度趋势(每月节点标注 台数/金额万,黄底高亮评估期,与全省全景一致)
    if s['月度趋势']:
        st.markdown("##### 📈 月度 SO 走势（每月节点：台数/金额万，黄底=当前评估时段）")
        trend_df = pd.DataFrame(s['月度趋势'])
        if not trend_df.empty:
            import matplotlib.pyplot as plt
            from _so_trend_chart import render_so_trend
            ps, pe = _CURRENT_PERIOD
            fig = render_so_trend(trend_df, x_col='月份', period_start=ps, period_end=pe)
            st.pyplot(fig)
            plt.close(fig)

    # 区县分布（仅城市级有）
    if s.get('区县分布'):
        st.markdown("##### 区县分布（按金额降序）")
        df = pd.DataFrame(s['区县分布'])
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=min(450, 40 + 35 * len(df)))

    # 双口径对比
    with st.expander("📊 双口径对比 + 绑定率"):
        cols = st.columns(3)
        cols[0].metric("全量感知（产品流向）", _fmt(s['当期_全量感知_万'], ' 万'),
                       help="product_flow_v 中按选定实体过滤的最新分销价合计")
        cols[1].metric("红包扫码（安装红包）", _fmt(s['当期_红包扫码_万'], ' 万'),
                       help="install_redpack_v 中按选定实体过滤的产品现有分销价合计")
        if s['绑定率'] is not None:
            cols[2].metric("绑定率", _pct(s['绑定率'], plus_sign=False),
                           help="红包扫码 / 全量感知")
        else:
            cols[2].metric("绑定率", "—")


# ───────────────────────────────────────────────
# Tab 2：服务商质量
# ───────────────────────────────────────────────
def _render_provider_quality(s: dict, is_dealer: bool = False, battlefield: dict = None):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "🟢 活跃服务商", s['总数'],
        help="当期(评估期)内,**有任何红包扫码上线**的服务商总数。\n\n"
             "口径:install_redpack_v 评估期内 ≥ 1 笔上线。",
    )
    c2.metric(
        "🆕 新增", s['新增'],
        help="当期活跃 **−** 环期活跃 = **本期才冒头**的服务商。\n\n"
             "判定:**环期(紧邻评估期前 N 月)0 上线 + 当期有上线**。\n"
             "业务含义:新签约或者新激活的服务商,管道扩水池。",
    )
    c3.metric(
        "🔄 持续活跃", s['持续活跃'],
        help="**当期 + 环期 都有上线**的服务商 — 两个时段都在贡献。\n\n"
             "业务含义:稳定客户,基本盘。",
    )
    c4.metric(
        "💤 沉睡", s['沉睡'],
        help="**环期有上线 + 当期 0 上线** — 上一段还在跑,这段冷掉了。\n\n"
             "业务含义:**流失风险**,重点拜访挽回对象。明细在下方折叠面板。",
    )

    st.caption(
        "📅 **口径**:**当期** = 你选的评估期(period_start~period_end);"
        "**环期** = 紧邻评估期前的同等月数(N 个月,N = 评估期月数)。"
        "活跃 = 新增 + 持续活跃。"
    )

    # 等级分布（服务商明细表官方原始等级，非货值重算）
    st.markdown("##### 等级分布（官方原始等级）")
    tiers_order = ['V0', 'V1', 'V2', 'V3', 'V4', 'V5', '未签约']
    tier_data = s['等级分布']
    tier_rows = [{'等级': t, '数量': int(tier_data.get(t, 0))} for t in tiers_order if tier_data.get(t, 0) > 0]
    if tier_rows:
        df_t = pd.DataFrame(tier_rows)
        st.dataframe(df_t, use_container_width=True, hide_index=True, height=40 + 35 * len(df_t))

    # 授牌服务商(无价值客户,管理标签口径)单独筛出
    _total = s.get('签约服务商总数', 0)
    if _total:
        _wn = s.get('授牌服务商数', 0)
        st.caption(
            f"🚫 **授牌服务商(无价值客户): {_wn:,} 家** / 范围内签约 {_total:,} 家"
            f"(占 {_wn / _total * 100:.0f}%) · 其中当期活跃 {s.get('活跃中授牌数', 0)} 家 —— "
            "管理标签口径,已从任务分配/跑动名单排除")

    # 9 宫格
    if s['RFM_9宫格']:
        st.markdown("##### RFM 9 宫格分布（活跃客户）")
        grid_df = pd.DataFrame(s['RFM_9宫格'])
        # 透视成 R 档 × F 档矩阵
        try:
            pivot_n = grid_df.pivot(index='R档', columns='F档', values='客户数').fillna(0).astype(int)
            pivot_m = grid_df.pivot(index='R档', columns='F档', values='累计货值_万').fillna(0).round(1)
            cols = st.columns(2)
            cols[0].markdown("**客户数**")
            cols[0].dataframe(pivot_n, use_container_width=True)
            cols[1].markdown("**累计货值（万）**")
            cols[1].dataframe(pivot_m, use_container_width=True)
        except Exception:
            st.dataframe(grid_df, use_container_width=True, hide_index=True)

    # 异常服务商
    st.markdown("##### ⚠️ 异常服务商")
    ab = s['异常服务商']
    c1, c2, c3 = st.columns(3)
    c1.metric(
        "🎭 马甲", ab['马甲'],
        help="挂在该地市 / 代理商名下,但**实际是代理商自有账号**的服务商。\n\n"
             "来源:`vest_account` 人工识别名单。\n"
             "处理:全表 **EXCLUDE**(不进任何统计)。",
    )
    c2.metric(
        "☂️ 伞形(涉及户数)", ab.get('伞形_户数', 0),
        help=f"{ab.get('伞形_组数', 0)} 组老板挂多家。\n\n"
             "判定:同一(老板姓名 + 老板电话)在 provider_profile 表里挂 ≥ 2 家服务商账号。\n\n"
             "业务含义:**一个老板开多家公司**,可能套利、刷量、占地盘。",
    )
    c3.metric(
        "❌ 低效服务商", ab.get('低效服务商', 0),
        help="**签约 60+ 天 + 累计扫码上线 ≤ 2 台** = 签约几乎没产出。\n\n"
             "口径:provider_contract.签约日期 ≤ 数据最新日 - 60 天,"
             "AND install_redpack 累计扫码记录 ≤ 2 笔。\n\n"
             "业务含义:**签约成摆设**,要么激活,要么解约。",
    )

    # ─── 服务商个体档案(代理商场景重点展示)─────
    if s.get('服务商明细'):
        st.markdown(f"##### 📋 旗下服务商档案(共 {len(s['服务商明细'])} 家,带 RFM + 战略标签)")
        st.caption(
            "🏷️ **等级** = provider_contract 官方原始等级(26 年官方评定 V0-V5，未签约归「未签约」)·  "
            "**R 档** = 最近交易距今天数 🟢≤30 🟡30-60 🔴>60  ·  "
            "**F 档** = 近 12 月活跃天数 🟢≥12 🟡3-11 🔴<3"
        )
        df_pq = pd.DataFrame(s['服务商明细'])

        # ── 联动阵地沙盘:加战略标签列 ──
        if battlefield and not battlefield.get('_empty') and not battlefield.get('_no_table'):
            archive = pd.DataFrame(battlefield.get('档案明细', []))
            if not archive.empty:
                key = '服务商编码'
                join_cols = [c for c in ['类型', '归属', '价值', '主力采购品牌', '大华占比_pct'] if c in archive.columns]
                df_pq = df_pq.merge(
                    archive[[key] + join_cols],
                    left_on='客户编码', right_on=key, how='left',
                )
                # 未建档默认显示
                df_pq['类型'] = df_pq.get('类型').fillna('🚪 渠道(未建档)') if '类型' in df_pq.columns else '🚪 渠道(未建档)'
                if '归属' in df_pq.columns:
                    df_pq['归属'] = df_pq['归属'].fillna('—')
                if '价值' in df_pq.columns:
                    df_pq['价值'] = df_pq['价值'].fillna('—')
                if '主力采购品牌' in df_pq.columns:
                    df_pq['主力采购品牌'] = df_pq['主力采购品牌'].fillna('—')
                if '大华占比_pct' in df_pq.columns:
                    df_pq['大华占比_pct'] = df_pq['大华占比_pct'].fillna(0)
                if key in df_pq.columns:
                    df_pq = df_pq.drop(columns=[key])

        st.caption("⬇️ 点击行号选择服务商,下方自动显示该服务商的详细画像。")
        _provider_table_with_card(df_pq, key='pq_active', height=400)

    if s.get('沉睡明细'):
        with st.expander(f"💤 沉睡服务商(环期有上线 / 当期 0 上线,共 {len(s['沉睡明细'])} 家)"):
            df_sl = pd.DataFrame(s['沉睡明细'])
            _provider_table_with_card(df_sl, key='pq_sleep', height=320)


# ───────────────────────────────────────────────
# Tab 3：红包投放
# ───────────────────────────────────────────────
def _render_redpack(s: dict):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("当期红包总额", _fmt(s['当期红包额_元'], ' 元', decimals=0))
    c2.metric("当期上线额", _fmt(s['当期上线额_万'], ' 万'))
    c3.metric("本实体 ROI", _pct(s['本实体ROI'], plus_sign=False))
    if s['全省ROI基线'] is not None and s['偏离度'] is not None:
        c4.metric("vs 全省基线", f"{s['偏离度']:.2f}x",
                  help=f"全省 ROI 基线 = {s['全省ROI基线']*100:.2f}%；"
                       "1.0 = 正常，>1.5 红包多于行业平均，<0.5 红包少")
    else:
        c4.metric("vs 全省基线", "—")

    # 正偏离 / 负偏离 各 Top 50(上下分列,选中查看详情)
    n_h = len(s.get('正偏离名单_top50', []))
    st.markdown(f"##### 🚨 正偏离 TOP 50(>1.5x,红包多 vs 上线少,共 {n_h} 家)")
    if s.get('正偏离名单_top50'):
        _provider_table_with_card(pd.DataFrame(s['正偏离名单_top50']),
                                   key='rp_high', height=320)
    else:
        st.info("无正偏离记录")

    st.divider()

    n_l = len(s.get('负偏离名单_top50', []))
    st.markdown(f"##### ⚠️ 负偏离 TOP 50(<0.5x,红包少但上线高,共 {n_l} 家)")
    if s.get('负偏离名单_top50'):
        _provider_table_with_card(pd.DataFrame(s['负偏离名单_top50']),
                                   key='rp_low', height=320)
    else:
        st.info("无负偏离记录")

    # ─── 🎯 666 大额红包审核 ───
    audit = s.get('审核666') or {}
    if audit.get('总数', 0) > 0:
        st.divider()
        st.markdown("##### 🎯 666 大额红包审核 — 中奖后业务员到访情况")
        st.caption(
            f"评估期内中过 **666 元** 的服务商共 **{audit['总数']}** 家。"
            f"以「最近一次 666 中奖时间」为起点,看 **2 周 / 4 周** 窗口内是否有业务员到访。"
            f"\n\n📌 窗口完整性:数据末日 {audit.get('DB末日', '—')} · "
            f"2 周窗口完整 {audit['2周窗口已完整数']} 家 · "
            f"4 周窗口完整 {audit['4周窗口已完整数']} 家 · "
            f"率统计仅基于窗口已完整数据。"
        )

        # 2 周窗口
        st.markdown(f"**📅 2 周窗口(基于 {audit['2周窗口已完整数']} 家已完整数据)**")
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "🏢 大华业务员到访", f"{audit['2周_大华']} 家",
            delta=f"{audit['2周_大华_pct']}%" if audit['2周_大华_pct'] is not None else None,
            delta_color='off',
        )
        c2.metric(
            "🏪 代理商业务员到访", f"{audit['2周_代理商']} 家",
            delta=f"{audit['2周_代理商_pct']}%" if audit['2周_代理商_pct'] is not None else None,
            delta_color='off',
        )
        c3.metric(
            "✅ 任一业务员到访", f"{audit['2周_任一']} 家",
            delta=f"{audit['2周_任一_pct']}%" if audit['2周_任一_pct'] is not None else None,
            delta_color='off',
        )

        # 4 周窗口
        st.markdown(f"**📅 4 周窗口(基于 {audit['4周窗口已完整数']} 家已完整数据)**")
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "🏢 大华业务员到访", f"{audit['4周_大华']} 家",
            delta=f"{audit['4周_大华_pct']}%" if audit['4周_大华_pct'] is not None else None,
            delta_color='off',
        )
        c2.metric(
            "🏪 代理商业务员到访", f"{audit['4周_代理商']} 家",
            delta=f"{audit['4周_代理商_pct']}%" if audit['4周_代理商_pct'] is not None else None,
            delta_color='off',
        )
        c3.metric(
            "✅ 任一业务员到访", f"{audit['4周_任一']} 家",
            delta=f"{audit['4周_任一_pct']}%" if audit['4周_任一_pct'] is not None else None,
            delta_color='off',
        )

        # 明细名单
        if audit.get('明细'):
            with st.expander(f"📋 666 服务商审核明细(共 {audit['总数']} 家,点击行查看详情)"):
                df = pd.DataFrame(audit['明细'])
                # 显示用列(去掉布尔状态列,过于细节)
                show_cols = ['客户编码', '客户名称', '区县', '最近666时间', '中奖次数',
                             '2周大华访问', '2周大华_业务员数',
                             '2周代理商访问', '2周代理商_业务员数',
                             '4周大华访问', '4周大华_业务员数',
                             '4周代理商访问', '4周代理商_业务员数']
                show_cols = [c for c in show_cols if c in df.columns]
                _provider_table_with_card(df[show_cols], key='rp_666_audit', height=400)


# ───────────────────────────────────────────────
# Tab 4：销售跑动
# ───────────────────────────────────────────────
def _render_sales_visits(s: dict, is_dealer: bool = False):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("大华业务员", s['业务员_大华数'])
    c2.metric("代理商业务员", s['业务员_代理商数'])
    c3.metric("总拜访次数", s['总拜访次数'])
    c4.metric("总拜访客户数", s['总拜访客户数'])

    # 代理商场景:大华业务员明细 + 代理商业务员综合(拜访 × 贡献 一行一全景)
    if is_dealer:
        st.markdown("##### 👣 业务员跑动明细")

        # 大华业务员明细(独立,不用 join)
        st.caption(f"🏢 大华业务员(共 {len(s.get('大华业务员明细', []))} 人)")
        if s.get('大华业务员明细'):
            df = pd.DataFrame(s['大华业务员明细'])
            st.dataframe(df, use_container_width=True, hide_index=True,
                         height=min(300, 40 + 35 * len(df)))
        else:
            st.info("评估期内无大华业务员跑动记录")

        # 代理商业务员综合(拜访 + 贡献 一行一人)
        combined = s.get('代理商业务员综合', [])
        if combined:
            st.markdown(
                f"##### 🏪 代理商业务员综合(拜访 × 上线贡献,共 {len(combined)} 人)"
            )
            st.caption(
                "**一行一个业务员**,左侧拜访 + 右侧上线贡献 综合呈现。\n\n"
                "**状态标签**:🌟 主力 / 🟢 正常 / ⚠️ 拜访无产出(资源浪费) / "
                "🤔 有产出无拜访(管理盲区) / 🚨 产出暴跌(vs 历史) / 💤 静默。\n\n"
                "**产出_拜访比_万** = 当期上线货值 ÷ 总拜访次数,越高效率越高。"
                "**覆盖差** = 名下服务商数 − 拜访客户数,>0 说明有服务商没去拜访。"
            )
            df = pd.DataFrame(combined)
            st.dataframe(
                df, use_container_width=True, hide_index=True,
                height=min(500, 40 + 35 * len(df)),
                column_config={
                    '业务员': st.column_config.TextColumn(width='small'),
                    '状态': st.column_config.TextColumn(width='medium'),
                    '名下服务商数': st.column_config.NumberColumn('名下服务商'),
                    '拜访客户数': st.column_config.NumberColumn('拜访客户'),
                    '覆盖差': st.column_config.NumberColumn('覆盖差',
                        help='名下 − 拜访;>0 = 有服务商没拜访到'),
                    '总拜访次数': st.column_config.NumberColumn('总拜访次数'),
                    '当期上线台数': st.column_config.NumberColumn('当期上线台'),
                    '当期上线货值_万': st.column_config.NumberColumn(
                        '当期货值(万)', format='%.2f'),
                    '当期贡献占比_pct': st.column_config.NumberColumn(
                        '贡献占比%', format='%.1f'),
                    '产出_拜访比_万': st.column_config.NumberColumn(
                        '万/次', format='%.2f',
                        help='当期货值 ÷ 总拜访次数'),
                    '历史上线台数': st.column_config.NumberColumn('历史上线台'),
                    '历史上线货值_万': st.column_config.NumberColumn(
                        '历史货值(万)', format='%.2f'),
                    '最近上线': st.column_config.TextColumn(width='medium'),
                },
            )
        else:
            st.info("评估期内无代理商业务员的拜访或上线记录")

        # 服务商 × 业务员 跑动覆盖矩阵
        if s.get('服务商跑动覆盖'):
            st.markdown(f"##### 🎯 旗下服务商跑动覆盖明细(Top 200)")
            st.caption("按拜访总次数降序。**0 次** = 该服务商当期无任何业务员跑动。点击行号查看详情。")
            df = pd.DataFrame(s['服务商跑动覆盖'])
            df['总拜访次数'] = df['大华拜访次数'].fillna(0).astype(int) + df['代理商拜访次数'].fillna(0).astype(int)
            df = df[['客户编码', '客户名称', '区县',
                     '大华拜访次数', '大华业务员数',
                     '代理商拜访次数', '代理商业务员数',
                     '总拜访次数', '最近拜访']]
            _provider_table_with_card(df, key='sv_coverage', height=400)
    else:
        # 城市/区县:用原来的 top 10 列表
        st.markdown("##### 业务员拜访量 TOP 10")
        if s['业务员清单_top10']:
            st.dataframe(pd.DataFrame(s['业务员清单_top10']),
                         use_container_width=True, hide_index=True,
                         height=min(450, 40 + 35 * len(s['业务员清单_top10'])))
        else:
            st.info("无拜访记录")

    # 大华跑动评估(仅城市级)
    if s.get('大华跑动评估_top15'):
        st.markdown("##### 🏆 大华业务员跑动评估 TOP 15(按救援成功 + 正常 降序)")
        df = pd.DataFrame(s['大华跑动评估_top15'])
        cols_show = ['业务员', '所属', '负责区县', '拜访客户数',
                     '✅ 救援成功', '❌ 救援失败',
                     '🟢 正常', '🚨 流失',
                     '🚨 严重漏跑', '⚠️ 一般漏跑', '⏳ 待观察']
        cols_show = [c for c in cols_show if c in df.columns]
        st.dataframe(df[cols_show], use_container_width=True, hide_index=True,
                     height=min(550, 40 + 35 * len(df)))
    elif s.get('业务员_大华数', 0) > 0 and not is_dealer:
        st.caption("ℹ️ 大华业务员评估仅在城市级全景图中展示")


# ───────────────────────────────────────────────
# Tab 5：⚔️ 竞品开拓
# ───────────────────────────────────────────────
def _render_competitor_top(s: dict):
    if s.get('不适用'):
        st.info("ℹ️ 代理商维度暂不支持「竞品开拓」（competitor_top 表不含代理商字段）")
        return
    if s.get('总数', 0) == 0:
        st.info("本范围内暂无竞品 Top 服务商记录。"
                "可去主页「📥 数据导入」上传「⚔️ 竞品 Top 服务商」Excel。")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("竞品 Top 客户数", s['总数'])
    c2.metric("总竞品体量", f"{s['总竞品体量_万']:,.0f} 万",
              help="这部分是大华应该争取转化的市场份额")
    c3.metric("✅ 已混卖大华", f"{s['已混卖大华_数']}",
              help="开拓难度低，应优先突破")
    c4.metric("❌ 纯卖竞品", f"{s['纯竞品_数']}",
              help="开拓难度高但战略意义大")

    # 按城市分布
    if s.get('按城市分布'):
        st.markdown("##### 城市分布（按竞品体量降序）")
        st.dataframe(pd.DataFrame(s['按城市分布']),
                     use_container_width=True, hide_index=True,
                     height=min(400, 40 + 35 * len(s['按城市分布'])))

    # TOP 10 客户
    if s.get('名单_top10_体量'):
        st.markdown("##### TOP 10 客户（按竞品体量降序）")
        df = pd.DataFrame(s['名单_top10_体量'])
        df['🏷'] = df['在售大华'].apply(
            lambda x: '✅ 已混卖' if x == 1 else '❌ 纯竞品'
        )
        cols_show = ['🏷', '客户名称', '城市', '区县', '客户经营品牌',
                     '竞品体量_万', '责任人姓名', '责任人角色']
        cols_show = [c for c in cols_show if c in df.columns]
        st.dataframe(df[cols_show], use_container_width=True, hide_index=True,
                     height=min(500, 40 + 35 * len(df)))

    st.caption(
        "💡 完整列表 + 派任务到跑动任务管理：去 **⚔️ 竞品开拓** 页"
    )


# ───────────────────────────────────────────────
# Tab 2(新):🆕 渠道健康度 — 管道进/出水 + 大商动向
# ───────────────────────────────────────────────
def _render_channel_health(s: dict, s_cert: dict = None):
    """渠道健康度:看哪些服务商初次入场、哪些流失、大商两期对比。s_cert=认证SMB版,传入则显示筛选框。"""
    if s_cert is not None:
        _scope = st.radio("服务商范围", ["全部服务商", "仅认证 SMB 服务商"],
                          horizontal=True, key='ch_cert_filter',
                          help="认证 SMB = provider_contract.渠道客户类型='认证SMB服务商'")
        if _scope == "仅认证 SMB 服务商":
            s = s_cert
    if not s:
        st.info("(数据未生成)")
        return
    if s.get('_disabled'):
        st.info(f"⚠️ 当前评估期下渠道健康度不可用:{s.get('_reason', '')}")
        return

    st.caption(
        f"📅 基线期 **{s['基线期']}**(从 2025-01 起的全量历史)"
        f"  vs  评估期 **{s['评估期']}**  ·  "
        f"看哪些服务商**初次进入**渠道、哪些**流失**了"
    )

    # 5 张 KPI 卡
    c1, c2, c3, c4, c5 = st.columns(5)
    delta = s.get('活跃净变化') or 0
    c1.metric(
        "评估期活跃服务商", s['评估期活跃数'],
        delta=delta if delta != 0 else None,
        help=f"评估期内 ≥1 台扫码激活的不同服务商;基线期为 {s['基线期活跃数']} 家",
    )
    c2.metric(
        "🆕 新增(进水)", s['新增数'],
        help="自 2025-01 起从未激活、评估期内首次激活的服务商",
    )
    c3.metric(
        "📉 流失(出水)", s['流失数'],
        help=f"基线期活跃但评估期 0 台 — 其中 {s['流失_真停采']} 真停采,"
             f"{s['流失_转走']} 转向其他地区/代理商",
    )
    c4.metric(
        "净增(新增-流失)", s['净增'],
        help=">0 说明管道在扩水池",
    )
    rate = s.get('管道扩张率')
    c5.metric(
        "管道扩张率",
        f"{rate*100:.1f}%" if rate is not None else "—",
        help="新增 ÷ 基线期活跃数",
    )

    # 流失风险提示
    if s['流失数'] > 0:
        msg_parts = [f"📉 流失 **{s['流失数']}** 家"]
        if s['流失_真停采']:
            msg_parts.append(f"**{s['流失_真停采']}** 真停采(全局也没激活)")
        if s['流失_转走']:
            msg_parts.append(f"**{s['流失_转走']}** 仍在体系内但跑别处了(详见下方「转走去向」)")
        msg = " · ".join(msg_parts)
        if s['流失_真停采'] > 0:
            st.warning(msg)
        else:
            st.info(msg)

    # 加载服务商速览组件
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from _provider_quick_view import provider_picker_with_card

    # ─── 📉 流失服务商(核心,放上面,全量)─────
    lost_list = s.get('流失名单') or s.get('流失名单_top50') or []
    st.markdown(f"##### 📉 流失服务商名单(核心关注,**全量** {s['流失数']} 家)")
    st.caption("⬇️ 点击行号选择服务商,下方自动显示该服务商的详细画像。")
    if lost_list:
        _provider_table_with_card(pd.DataFrame(lost_list), key='ch_lost_picker', height=480)
    else:
        st.info("评估期无流失服务商")

    st.divider()

    # ─── 🆕 新增服务商(辅助,放下面,全量)─────
    new_list = s.get('新增名单') or s.get('新增名单_top50') or []
    st.markdown(f"##### 🆕 新增服务商名单(辅助参考,**全量** {s['新增数']} 家)")
    if new_list:
        _provider_table_with_card(pd.DataFrame(new_list), key='ch_new_picker', height=400)
    else:
        st.info("评估期无新增服务商")

    # 流失归因:转走的去哪了
    if s['转走去向_top30']:
        with st.expander(
            f"🧭 转走的服务商现在去哪了?(Top 30 by 评估期货值)",
            expanded=False,
        ):
            df = pd.DataFrame(s['转走去向_top30'])
            st.dataframe(df, use_container_width=True, hide_index=True,
                         height=min(450, 40 + 35 * len(df)))
            st.caption(
                "💡 这些服务商基线期在本实体下活跃,评估期没在本实体下激活,"
                "但**全局仍有激活** — 他们去了表中显示的「现去向地市 / 现签约代理商」"
            )

    # 大商动向
    st.markdown(f"##### 🏆 大商动向(基线+评估期 合并货值 Top 10)")
    if s['大商动向_top10']:
        df = pd.DataFrame(s['大商动向_top10'])
        df['变化率'] = df['变化率'].apply(
            lambda v: '∞ (新晋)' if v in ('', None) or pd.isna(v)
                      else f"{v*100:+.0f}%"
        )
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=min(450, 40 + 35 * len(df)))
        st.caption(
            "💡 状态:🆕 新晋 = 基线期 0 / ⬆️ 上位 = 评估期涨 > 50% / "
            "⬇️ 掉队 = 评估期跌 > 20% / 💀 退场 = 评估期 0"
        )
    else:
        st.info("无大商数据")


# ───────────────────────────────────────────────
# Tab 1(代理商专用):⚔️ 阵地沙盘(独立战略层数据)
# ───────────────────────────────────────────────
def _render_battlefield(s: dict, meta: dict = None):
    """代理商阵地沙盘 — 列出所有服务商,行内编辑战略标签,直接保存。"""
    import io
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from _position_loader import (
        import_battlefield_excel,
        load_battlefield as _load_bf,
        generate_template_excel,
        batch_upsert,
    )

    st.caption(
        "🎯 **战略层**:把旗下服务商按战略价值分类为「阵地」+「渠道」,标注 "
        "归属(我方/敌方/摇摆)/ 价值(A/B/C)/ 趋势(增/平/缩)/ 竞品/争取目标 等。"
        "**作为攻坚方案的输入**。数据独立存放,不影响服务商管理逻辑。"
    )

    dealer = meta.get('dealer') if meta else None

    if s.get('_no_table'):
        st.info("ℹ️ 阵地沙盘表还未创建。先在下面录入一行数据自动建表。")
        return
    if s.get('_empty') and not s.get('编辑表'):
        st.warning(f"📭 {s.get('提示', '本代理商旗下暂无任何服务商数据(install_redpack 里没有记录)。')}")
        return

    # === 行内编辑表(核心工作流)===
    st.markdown("##### ✏️ 旗下服务商战略录入(行内编辑 → 保存)")
    n_total = s.get('总服务商数', 0)
    n_done = s.get('已建档数', 0)
    n_pending = s.get('待录入数', 0)
    st.caption(
        f"📋 旗下共 **{n_total}** 家服务商  ·  "
        f"✅ 已建档 **{n_done}**  ·  ⏳ 待录入 **{n_pending}**。"
        "  左侧只读 = 现有数据,右侧可编辑 = 战略标签。改完点「💾 保存」。"
    )

    editor_rows = s.get('编辑表', [])
    if editor_rows:
        df_edit = pd.DataFrame(editor_rows)

        # 排序选项
        filter_col = st.columns([2, 2, 2, 4])
        with filter_col[0]:
            mode_filter = st.selectbox(
                '显示',
                options=['全部', '待录入', '已建档'],
                index=0, key='bf_filter',
            )
        with filter_col[1]:
            sort_by = st.selectbox(
                '排序',
                options=['历史货值_万 ↓', '当期货值_万 ↓', '最近交易 ↓', '服务商名称 ↑'],
                index=0, key='bf_sort',
            )
        with filter_col[2]:
            search_kw = st.text_input('🔍 搜索服务商名称', '', key='bf_search')

        # 过滤
        if mode_filter == '待录入':
            df_edit = df_edit[df_edit['类型'].isna()]
        elif mode_filter == '已建档':
            df_edit = df_edit[df_edit['类型'].notna()]
        if search_kw:
            df_edit = df_edit[df_edit['服务商名称'].astype(str).str.contains(search_kw, na=False)]

        sort_map = {
            '历史货值_万 ↓': ('历史货值_万', False),
            '当期货值_万 ↓': ('当期货值_万', False),
            '最近交易 ↓': ('最近交易', False),
            '服务商名称 ↑': ('服务商名称', True),
        }
        sc, asc = sort_map[sort_by]
        df_edit = df_edit.sort_values(sc, ascending=asc, na_position='last').reset_index(drop=True)

        # 重排列(只读在前,可编辑在后)
        readonly_cols = ['服务商编码', '服务商名称', '区县', '客户类型',
                         '历史台数', '历史货值_万', '当期台数', '当期货值_万', '最近交易']
        editable_cols = ['类型', '归属', '价值', '趋势',
                         '主力采购品牌', '次要品牌', '全年销量_万',
                         '大华占比_pct', '争取目标_万', '争取策略', '流失风险', '备注']
        all_cols = [c for c in readonly_cols + editable_cols if c in df_edit.columns]
        df_edit = df_edit[all_cols]

        # data_editor
        edited = st.data_editor(
            df_edit,
            use_container_width=True,
            hide_index=True,
            height=min(600, 40 + 35 * len(df_edit)),
            num_rows='fixed',
            disabled=[c for c in readonly_cols if c in df_edit.columns],
            column_config={
                '服务商编码': st.column_config.TextColumn(width='small'),
                '服务商名称': st.column_config.TextColumn(width='medium'),
                '区县': st.column_config.TextColumn(width='small'),
                '客户类型': st.column_config.TextColumn(width='small'),
                '历史货值_万': st.column_config.NumberColumn(format='%.2f'),
                '当期货值_万': st.column_config.NumberColumn(format='%.2f'),
                '类型': st.column_config.SelectboxColumn(
                    '类型', options=['阵地', '渠道'], required=False,
                    help='阵地 = 战略价值高,值得集中投入资源;渠道 = 中性卖货渠道'),
                '归属': st.column_config.SelectboxColumn(
                    '归属', options=['我方', '敌方', '摇摆'],
                    help='我方=主力进我方货;敌方=主力进竞品货;摇摆=多家同时进货'),
                '价值': st.column_config.SelectboxColumn(
                    '价值', options=['A', 'B', 'C'],
                    help='按年出货量分级:A=高 / B=中 / C=低'),
                '趋势': st.column_config.SelectboxColumn(
                    '趋势', options=['增长', '持平', '萎缩']),
                '主力采购品牌': st.column_config.SelectboxColumn(
                    '主力采购品牌',
                    options=['大华', '海康', '宇视', '天地伟业', '其他']),
                '次要品牌': st.column_config.SelectboxColumn(
                    '次要品牌',
                    options=['', '大华', '海康', '宇视', '天地伟业', '其他']),
                '流失风险': st.column_config.SelectboxColumn(
                    '流失风险', options=['高', '中', '低'],
                    help='仅在「归属=我方」时有意义'),
                '全年销量_万': st.column_config.NumberColumn(format='%.1f'),
                '大华占比_pct': st.column_config.NumberColumn(
                    '大华占比%', format='%.1f', min_value=0, max_value=100,
                    help='大华在该服务商总采购里的钱包份额(0-100%)'),
                '争取目标_万': st.column_config.NumberColumn(format='%.1f'),
                '争取策略': st.column_config.TextColumn(width='medium'),
                '备注': st.column_config.TextColumn(width='medium'),
            },
            key=f'bf_editor_{dealer}',
        )

        # 保存按钮
        col_save = st.columns([1, 1, 4])
        if col_save[0].button('💾 保存修改', type='primary', use_container_width=True):
            # 找出有任意战略字段被填的行
            edited_df = pd.DataFrame(edited)
            mask = edited_df[editable_cols].notna().any(axis=1)
            for c in editable_cols:
                if c in edited_df.columns and edited_df[c].dtype == object:
                    mask |= edited_df[c].astype(str).str.strip().ne('') & edited_df[c].notna()
            to_save = edited_df[mask]
            if to_save.empty:
                st.warning('未检测到任何修改')
            else:
                records = to_save.to_dict('records')
                with st.spinner(f'保存 {len(records)} 条…'):
                    r = batch_upsert(records)
                st.success(f'✅ 新增 {r["inserted"]} / 更新 {r["updated"]}')
                _load_bf.clear()
                st.rerun()

        if col_save[1].button('🔄 重新加载', use_container_width=True):
            _load_bf.clear()
            st.rerun()

    # === Excel 批量导入(辅助入口)===
    with st.expander("📥 Excel 批量导入(辅助,大量数据时用)", expanded=False):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.caption("适合一次性批量录入。Excel 必须包含 `服务商编码` 列,其他战略字段可选。")
        with c2:
            st.download_button(
                "📄 下载模板",
                data=generate_template_excel(),
                file_name='阵地沙盘模板.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                use_container_width=True,
            )
        upl = st.file_uploader('选择 Excel(.xlsx)', type=['xlsx'], key='bf_upload')
        if upl:
            mode = st.radio('导入模式',
                            options=['merge', 'replace'],
                            format_func=lambda x: {'merge': '🔀 合并', 'replace': '🔄 全表覆盖'}[x],
                            horizontal=True, key='bf_mode')
            if st.button('💾 开始导入', type='primary', key='bf_import_btn'):
                with st.spinner('导入中…'):
                    try:
                        r = import_battlefield_excel(upl, mode=mode)
                        st.success(f"✅ 新增 {r['inserted']} / 更新 {r['updated']} / 库内总数 {r['total']}")
                        _load_bf.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ 导入失败:{e}")

    # ─── 已建档时:展示战场态势 ─────
    if s.get('_empty_archive') or s.get('已建档数', 0) == 0:
        st.info('💡 已建档 0 家。在上面表格里填战略字段并保存,这里会自动出现 9 宫格 / 攻坚名单 / 流失预警。')
        return

    st.divider()
    st.markdown("##### 📊 战场态势(基于已建档)")
    own = s['归属分布']
    val = s['价值分布']
    c = st.columns(6)
    c[0].metric("已建档数", s['已建档数'],
                help=f"阵地 {s['阵地数']} / 渠道 {s['渠道数']}")
    c[1].metric("🟢 我方", own.get('我方', 0))
    c[2].metric("🔴 敌方", own.get('敌方', 0))
    c[3].metric("🟡 摇摆", own.get('摇摆', 0),
                help="摇摆阵地 = **市占率提升的最快来源**")
    c[4].metric("总争取目标", f"¥{s['总争取目标_万']:,.0f} 万",
                help="所有阵地争取目标加总")
    c[5].metric("钱包份额(加权)",
                f"{s['钱包份额加权_pct']:.1f}%" if s['钱包份额加权_pct'] is not None else "—",
                help="大华占比 × 全年销量 加权平均")

    # ─── 9 宫格 ─────
    if s.get('九宫格'):
        st.markdown("##### 🗺️ 归属 × 价值 9 宫格")
        st.caption("行 = 归属(我方/摇摆/敌方),列 = 价值(A/B/C) · 数字 = 家数(争取目标 万)")
        grid_df = pd.DataFrame(s['九宫格'])
        if not grid_df.empty:
            try:
                # 透视成 归属 × 价值 矩阵
                grid_df['_label'] = (
                    grid_df['家数'].astype(int).astype(str)
                    + ' (¥' + grid_df['争取目标_万'].fillna(0).astype(int).astype(str) + '万)'
                )
                pivot = grid_df.pivot_table(
                    index='归属', columns='价值', values='_label', aggfunc='first',
                ).fillna('-')
                # 按 归属 顺序:我方 → 摇摆 → 敌方
                own_order = [o for o in ['我方', '摇摆', '敌方', None] if o in pivot.index]
                val_order = [v for v in ['A', 'B', 'C', None] if v in pivot.columns]
                pivot = pivot.loc[own_order, val_order]
                st.dataframe(pivot, use_container_width=True)
            except Exception:
                st.dataframe(grid_df, use_container_width=True, hide_index=True)

    # ─── 攻坚优先级名单 ─────
    if s.get('攻坚名单_top100'):
        st.markdown(f"##### 🎯 攻坚优先级名单(共 {len(s['攻坚名单_top100'])} 家)")
        st.caption("优先级:**🟡 摇摆-A > 🔴 敌方-A > 🟡 摇摆-B > 🔴 敌方-B …** · 二级按争取目标降序 · 点击行号查看详情")
        df = pd.DataFrame(s['攻坚名单_top100'])
        _provider_table_with_card(df, key='bf_attack', height=420)
        # 下载攻坚清单
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as w:
            df.to_excel(w, sheet_name='攻坚名单', index=False)
        st.download_button(
            "📥 导出攻坚名单",
            data=buf.getvalue(),
            file_name=f'攻坚名单_{dealer}_{pd.Timestamp.now().strftime("%Y%m%d")}.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    # ─── 流失风险预警 ─────
    if s.get('流失风险名单'):
        st.markdown(f"##### 🚨 流失风险预警(我方阵地 × 高风险/萎缩,共 {len(s['流失风险名单'])} 家)")
        st.caption("⚠️ 这些是**我方阵地**,但流失风险高或趋势在萎缩 — 需要守住,优先维护。")
        df = pd.DataFrame(s['流失风险名单'])
        _provider_table_with_card(df, key='bf_risk', height=320)

    # ─── 全档案折叠 ─────
    with st.expander(f"📋 全部阵地档案明细({s['已建档数']} 家)"):
        df = pd.DataFrame(s['档案明细'])
        _provider_table_with_card(df, key='bf_all', height=400)


# ───────────────────────────────────────────────
# Tab 6(代理商专用):🎤 推广会覆盖
# ───────────────────────────────────────────────
def _render_promotion_coverage(s: dict):
    """代理商旗下服务商参与推广会的情况"""
    if not s or s.get('_no_data'):
        st.info("无推广会数据(promotion_meeting 表为空或本代理商旗下服务商均未参会)")
        return

    # 顶部 KPI
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("旗下活跃服务商", s['旗下活跃服务商数'],
              help="评估期内有红包扫码上线的服务商数")
    c2.metric("全历史服务商", s['旗下全历史服务商数'],
              help="所有时间段曾被本代理商合作过的服务商")
    c3.metric("参会服务商", s['参与推广会_服务商数'],
              delta=f"覆盖率 {s['活跃服务商覆盖率']*100:.1f}%",
              delta_color='off',
              help="(评估期活跃服务商 ∩ 参加过推广会)/ 评估期活跃服务商")
    c4.metric("总参会次数", s['总参会次数'])
    c5.metric("场次数", s.get('场次数', 0))

    # 进度条:覆盖率视觉化
    st.progress(min(s['活跃服务商覆盖率'], 1.0),
                text=f"活跃服务商推广会覆盖率 {s['活跃服务商覆盖率']*100:.1f}%")

    # 场次列表
    if s.get('场次列表'):
        st.markdown("##### 📅 旗下服务商参与的推广会场次")
        df = pd.DataFrame(s['场次列表'])
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=min(450, 40 + 35 * len(df)),
                     column_config={
                         '活动名称': st.column_config.TextColumn(width='large'),
                         '主办方代理商': st.column_config.TextColumn(width='medium'),
                     })

    # 服务商参会明细
    if s.get('服务商参会明细'):
        st.markdown(f"##### 👥 服务商参会次数明细(共 {len(s['服务商参会明细'])} 家)")
        df = pd.DataFrame(s['服务商参会明细'])
        _provider_table_with_card(df, key='pc_attended', height=400)

    # 未覆盖服务商(高价值,但未参加过推广会)
    if s.get('未覆盖服务商Top20'):
        st.markdown(
            f"##### 🚨 未参加推广会的高价值服务商 Top 20(累计货值降序)"
        )
        st.caption("⚠️ 这些服务商累计货值高但从未参加过推广会 — 应优先纳入下次会议邀请名单")
        df = pd.DataFrame(s['未覆盖服务商Top20'])
        _provider_table_with_card(df, key='pc_uncovered', height=320)


# ───────────────────────────────────────────────
# Tab 0(代理商专用):📋 业务对标
# ───────────────────────────────────────────────
def _render_dealer_benchmark(s: dict):
    """代理商业务对标视图(5 月对标会用)"""
    if not s:
        st.info("无对标数据(请先确认该代理商在 dealer_sandbox 沙盘表中)")
        return
    if s.get('_no_sandbox'):
        st.warning(f"⚠️ {s.get('提示', '')}")
        return

    p = s['画像']
    st.caption(f"📅 本月评估:**{s['本月']}** · SI 正式快照:**{s.get('SI数据时点') or '暂无'}** · 下方业务数据均为代理商全省口径")

    # === Section 1:代理商画像速览 ===
    st.markdown("##### 🏢 代理商画像速览")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("分销商认证", p.get('分销商认证') or 'N/A')
    c2.metric("客户所有者(业务员)", p.get('客户所有者') or 'N/A')
    c3.metric("覆盖范围", p.get('覆盖范围') or 'N/A')
    c4.metric("主要服务商类型", p.get('主要服务商类型') or 'N/A')
    c5.metric("公司总人数", _fmt(p.get('公司总人数'), '', 0))

    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric("老板姓名", p.get('老板姓名') or 'N/A')
    c7.metric("老板电话", p.get('老板电话') or 'N/A')
    c8.metric("老板类型", p.get('老板类型') or 'N/A')
    c9.metric("大华门店数量", _fmt(p.get('大华门店数量'), '', 0))
    c10.metric("门店销售人员", _fmt(p.get('大华门店销售人员数'), '', 0))

    # 经营类型占比
    if p.get('经营类型占比'):
        st.caption(f"📊 **经营类型占比**:{p.get('经营类型占比')}  ·  "
                   f"**详细地址**:{p.get('详细地址', 'N/A')}")

    # === Section 2:签约目标 vs SI 进度 ===
    st.markdown("##### 🎯 签约目标 vs SI 进度(年度)")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("签约目标", _fmt(s['签约目标_万'], ' 万'),
              help="dealer_si_snapshot.签约金额(无快照时回退沙盘年度目标)")
    c2.metric("YTD 实绩", _fmt(s['YTD_SO_万'], ' 万'),
              delta=_pct(s.get('累计同比')),
              help=f"dealer_si_snapshot.累计业绩达成（返利前）;同期 {_fmt(s['去年同期SO_万'], ' 万')}")
    if s['应达成_万']:
        c3.metric("截止上月应达成", _fmt(s['应达成_万'], ' 万'),
                  help="优先读取 dealer_si_snapshot.累计任务；无快照时按 kpi_rhythm 计算")
    else:
        c3.metric("应达成", "—")
    if s['进度达成率'] is not None:
        c4.metric("进度达成率", _pct(s['进度达成率'], plus_sign=False),
                  help="YTD 实绩 ÷ 截止上月应达成;100% = 按节奏达标")
    else:
        c4.metric("进度达成率", "—")
    if s['完成率'] is not None:
        c5.metric("年度完成率", _pct(s['完成率'], plus_sign=False),
                  help="YTD 实绩 ÷ 年度签约目标")
    else:
        c5.metric("年度完成率", "—")

    # === Section 3:本月 MTD 表现(代理商全省口径) ===
    st.markdown(f"##### 📈 {s['本月']} 月度表现(MTD)")
    st.caption("SO 口径 = product_flow · 出库客户名称；不限上线城市，统计该代理商全省全部出货")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"{s['本月']} SO · 全省", _fmt(s['本月_MTD_SO_万'], ' 万'),
              help="本月 1 日至评估期止 · 代理商全部出货")
    c2.metric("vs 上月", _pct(s.get('本月环比')),
              help=f"上月 SO(全省) {_fmt(s['上月SO_万'], ' 万')}")
    c3.metric("vs 去年同月", _pct(s.get('本月同比')),
              help=f"去年同月 SO(全省) {_fmt(s['去年同月SO_万'], ' 万')}")
    c4.metric("YTD 活跃服务商", _fmt(s['YTD服务商数'], '', 0))

    # === Section 4:历史标杆 ===
    st.markdown("##### 📊 历史标杆(2025 全年)")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("去年总销售规模", _fmt(p.get('去年总销售规模'), ' 万'))
    c2.metric("去年安防分销", _fmt(p.get('去年安防分销销售规模'), ' 万'))
    c3.metric("去年红包上线", _fmt(p.get('去年安装红包上线金额'), ' 万'))
    c4.metric("去年签约服务商", _fmt(p.get('去年签约服务商数'), '', 0))
    c5.metric("去年激活服务商", _fmt(p.get('去年激活服务商数'), '', 0))

    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric("去年三星及以上", _fmt(p.get('去年三星及以上服务商数'), '', 0))
    c7.metric("下游服务商总数", _fmt(p.get('下游服务商总数'), '', 0))
    c8.metric("去年客均产出", _fmt(p.get('去年安防分销客均产出_万'), ' 万'))
    c9.metric("现金投入", _fmt(p.get('现金投入'), ' 万'))
    c10.metric("去年盈利", _fmt(p.get('去年盈利金额'), ' 万'))

    # === Section 5:同地市横向榜单 ===
    if s.get('同地市榜单') and s.get('同地市排名'):
        st.markdown(f"##### 🏆 同地市榜单(当前排名 **{s['同地市排名']}/{s['同地市总数']}**)")
        df = pd.DataFrame(s['同地市榜单'])
        df['分销签约金额'] = df['分销签约金额'].apply(lambda v: _fmt(v, ' 万'))
        df['YTD_SO_万'] = df['YTD_SO_万'].apply(lambda v: _fmt(v, ' 万'))
        df['完成率'] = df['完成率'].apply(lambda v: _pct(v, plus_sign=False))
        # 按排名展示,带 ⭐ 高亮
        show_cols = ['当前', '排名', '客户名称', '分销签约金额', 'YTD_SO_万', '完成率']
        st.dataframe(
            df[show_cols],
            use_container_width=True, hide_index=True,
            height=min(500, 40 + 35 * len(df)),
            column_config={
                '当前': st.column_config.TextColumn(width='small'),
                '排名': st.column_config.NumberColumn(width='small'),
                '客户名称': st.column_config.TextColumn(width='large'),
            },
        )

    # === Section 6:资金 / 销售 / 服务能力扩展(折叠)===
    with st.expander("📦 资金 / 销售 / 服务能力详情(折叠)"):
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("**💰 资金能力**")
            st.write(f"现金投入:{p.get('现金投入', 'N/A')} 万")
            st.write(f"预估抵押物净值:{p.get('预估抵押物净值', 'N/A')} 万")
            st.write(f"财务人员类型:{p.get('财务人员类型', 'N/A')}")
            st.write(f"去年盈利金额:{p.get('去年盈利金额', 'N/A')} 万")
            st.write(f"客户期望毛利额:{p.get('客户期望毛利额', 'N/A')} 万")
            st.write(f"经营场所:{p.get('经营场所面积_平米', 'N/A')} ㎡ · {p.get('经营场所所有权', 'N/A')}")
            st.write(f"仓储:{p.get('仓储面积_平米', 'N/A')} ㎡ · {p.get('仓储所有权', 'N/A')}")
        with col_b:
            st.markdown("**👥 销售能力**")
            st.write(f"公司总人数:{p.get('公司总人数', 'N/A')}")
            st.write(f"安防销售人员:{p.get('安防销售人员数量', 'N/A')}")
            st.write(f"预计投入下沉人员:{p.get('预计投入下沉人员数', 'N/A')}")
            st.write(f"大华门店数量:{p.get('大华门店数量', 'N/A')}")
            st.write(f"大华门店销售人员:{p.get('大华门店销售人员数', 'N/A')}")
            st.write(f"是否专职操盘手:{p.get('是否专职操盘手', 'N/A')}")
            st.write(f"操盘手:{p.get('操盘手姓名', 'N/A')} ({p.get('操盘手电话', 'N/A')}) "
                     f"· {p.get('操盘手年龄', 'N/A')} 岁 · {p.get('操盘手性别', 'N/A')}")
        with col_c:
            st.markdown("**🛠️ 服务能力**")
            st.write(f"技术人员总数:{p.get('技术人员总数', 'N/A')}")
            st.write(f"售前人员:{p.get('售前人员数', 'N/A')}")
            st.write(f"售中/售后人员:{p.get('售中售后人员数', 'N/A')}")
            st.write(f"经营类型占比:{p.get('经营类型占比', 'N/A')}")
            st.write(f"物流方式:{p.get('物流方式', 'N/A')}")
            st.write(f"自有车辆:{p.get('自有车辆数量', 'N/A')}")
            st.write(f"公司资质:{p.get('公司资质', 'N/A')}")
