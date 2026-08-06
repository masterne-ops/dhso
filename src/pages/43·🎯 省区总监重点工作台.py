#!/usr/bin/env python3
"""🎯 省区总监重点工作台 — 两大专项的任务设置(全省目标按基线拆地市)+ 结果检查(进度条)。

专项与指标:
  ① NP流转专项: 拜访覆盖家数(累计) / 签约家数(累计) / 本月SO金额_万
     基数 = np_transfer_customer 当前口径(每来源最新时点,按外部客户名称去重);
     覆盖 = 被拜访过(visit_record 客户编码或客户名称双匹配);签约 = 命中 provider_contract;
     SO = 签约名 install_redpack 当月上线金额。
  ② 无线产品专项: 覆盖服务商数(本月有无线上线) / 铺货服务商数(本月无线铺货) / SO台数(本月)
     覆盖&铺货 = install_redpack / distribution_info JOIN product_focus(专项='无线');
     SO台数 = product_flow.三大重点专项='无线专项'(与工作台 KPI 同口径)。

任务设置: 输入全省目标 → 按各市基线占比自动拆分 → 可微调 → 存 director_task(专项,月份,地市,指标)。
结果检查: 全省/各市进度条 + 明细(可按 地市/代理商/分销经理 筛选,落到客户/服务商)。
"""
import sys
import sqlite3
import datetime as dt
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin, current_user, get_current_role, filter_by_scope  # noqa: E402
from _wireless_detail import get_wireless_provider_table  # noqa: E402

DB_PATH = Path(__file__).parent.parent.parent / "db" / "product_flow.db"

SPECIALS = {
    'NP流转专项': {
        'metrics': ['拜访覆盖家数', '签约家数', 'SO金额_万'],
        'int_metrics': {'拜访覆盖家数', '签约家数'},
        'desc': '三阶段管理:① 覆盖(什么时候拜访到,累计) → ② 签约(什么时候签下来,累计) → '
                '③ 产单(每月产多少单,当月SO);每个阶段按月设目标,见任务设置「📅 多月阶段计划」',
    },
    '无线产品专项': {
        'metrics': ['覆盖服务商数', '铺货服务商数', 'SO台数'],
        'int_metrics': {'覆盖服务商数', '铺货服务商数', 'SO台数'},
        'desc': '覆盖=本月有无线上线的服务商;铺货=本月无线铺货服务商;SO=本月无线专项台数',
    },
}

# NP 三阶段展示标签(存库指标名不变)
NP_STAGE = {'拜访覆盖家数': '① 覆盖·拜访覆盖家数(累计)',
            '签约家数': '② 签约·签约家数(累计)',
            'SO金额_万': '③ 产单·SO金额_万(当月)'}


def _month_end(ym_str):
    d1 = dt.date.fromisoformat(f"{ym_str}-01")
    return (d1.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)

require_auth()
if not (is_admin() or get_current_role() == 'product_manager'):
    st.error(f"⛔ 省区总监重点工作台仅 admin / 产品经理 可用。当前:`{current_user()}` / `{get_current_role()}`")
    st.stop()

st.markdown("### 🎯 省区总监重点工作台")

today = dt.date.today()
months = [f"{today:%Y-%m}"] + [f"{(today.replace(day=1) - dt.timedelta(days=30 * i)):%Y-%m}" for i in range(1, 4)]
months = list(dict.fromkeys(months))

hc = st.columns([1.6, 1.2, 3])
special = hc[0].selectbox("专项", list(SPECIALS.keys()) + ['GTM攻坚专项'])
ym = hc[1].selectbox("任务月份", months)

# ── GTM 攻坚专项:名单制,直接读 GTM 看板(名单生成/管理在「产品经理工作→GTM 管理」页) ──
if special == 'GTM攻坚专项':
    st.caption("名单制专项:官方任务/系统推荐名单 → 跑动 + 动作跟踪(推广会/铺货/红包/上线)。"
               "名单的生成/移出/剔除在 [🚀 GTM 管理](/gtm) 页操作。")

    @st.cache_data(ttl=300, show_spinner="正在读取 GTM 看板…")
    def _gtm_board(_ym):
        from _gtm import get_gtm_board
        return get_gtm_board(_ym)

    board = _gtm_board(ym)
    if board is None or board.empty:
        st.info(f"{ym} 暂无 GTM 名单。到 [🚀 GTM 管理](/gtm) 生成或导入官方任务名单。")
        st.stop()

    gc = st.columns([1.3, 1.6, 1.6])
    g_city = gc[0].selectbox("地市", ['全省'] + sorted(board['城市'].dropna().unique().tolist()))
    g_dealer = gc[1].selectbox("代理商", ['全部'] + sorted(board['所属代理商'].dropna().unique().tolist()))
    g_mgr = gc[2].selectbox("分销经理", ['全部'] + sorted(board['分销经理'].dropna().unique().tolist()))
    bv = board.copy()
    if g_city != '全省':
        bv = bv[bv['城市'] == g_city]
    if g_dealer != '全部':
        bv = bv[bv['所属代理商'] == g_dealer]
    if g_mgr != '全部':
        bv = bv[bv['分销经理'] == g_mgr]

    st.markdown(f"#### 📊 {('全省' if g_city == '全省' else g_city)} GTM 进度({ym})")
    n, n_run = len(bv), int(bv['跑动完成'].sum())
    n_done = int(bv['GTM完成'].sum())
    pc = st.columns(3)
    pc[0].metric("目标家数", f"{n}")
    pc[1].metric("已跑动", f"{n_run}", delta=f"{n_run / n * 100:.0f}%" if n else None, delta_color="off")
    pc[1].progress(n_run / n if n else 0.0)
    pc[2].metric("GTM 完成(跑动+≥1动作)", f"{n_done}",
                 delta=f"{n_done / n * 100:.0f}%" if n else None, delta_color="off")
    pc[2].progress(n_done / n if n else 0.0)

    st.markdown("#### 🗺️ 各地市完成情况")
    agg = board.groupby('城市').agg(目标=('客户编码', 'count'), 已跑动=('跑动完成', 'sum'),
                                  GTM完成=('GTM完成', 'sum')).reset_index()
    agg['跑动率'] = (agg['已跑动'] / agg['目标']).round(3)
    agg['完成率'] = (agg['GTM完成'] / agg['目标']).round(3)
    st.dataframe(agg.sort_values('完成率', ascending=False), hide_index=True, use_container_width=True,
                 column_config={'跑动率': st.column_config.ProgressColumn('跑动率', min_value=0, max_value=1, format="percent"),
                                '完成率': st.column_config.ProgressColumn('完成率', min_value=0, max_value=1, format="percent")})

    with st.expander("按分销经理汇总"):
        am = board.groupby('分销经理').agg(目标=('客户编码', 'count'), 已跑动=('跑动完成', 'sum'),
                                        GTM完成=('GTM完成', 'sum')).reset_index()
        am['完成率'] = (am['GTM完成'] / am['目标']).round(3)
        st.dataframe(am.sort_values('目标', ascending=False), hide_index=True, use_container_width=True,
                     column_config={'完成率': st.column_config.ProgressColumn('完成率', min_value=0, max_value=1, format="percent")})

    st.markdown(f"#### 📋 GTM 目标客户明细({len(bv)} 家 · 本月攻坚名单,含跑动/动作状态)")
    dcols = [c for c in ['分销经理', '客户名称', '所属代理商', '城市', '区县', '推荐产品类',
                         '状态', '首访日', '铺货台数', '上线台数', '来源'] if c in bv.columns]
    st.dataframe(bv[dcols].sort_values(['状态', '分销经理']), hide_index=True,
                 use_container_width=True, height=400)
    st.stop()

st.caption(SPECIALS[special]['desc'])
m_start, m_end = f"{ym}-01", f"{ym}-31"


def _conn():
    return sqlite3.connect(str(DB_PATH))


def _ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS director_task (
            专项 TEXT NOT NULL, 月份 TEXT NOT NULL, 地市 TEXT NOT NULL, 指标 TEXT NOT NULL,
            目标 REAL, 设置人 TEXT, 设置时间 TEXT,
            PRIMARY KEY (专项, 月份, 地市, 指标)
        )""")
    conn.commit()


# ══════════ 实际值 + 基线 取数 ══════════

@st.cache_data(ttl=300, show_spinner="正在计算 NP 专项数据…")
def np_data(ym_start, ym_end):
    """NP 当前口径客户级明细:市/责任分销经理/覆盖(累计)/签约/当月SO_万。"""
    conn = _conn()
    try:
        df = pd.read_sql(f"""
            WITH cur0 AS (
              SELECT * FROM np_transfer_customer n
               WHERE 数据时点 = (SELECT MAX(数据时点) FROM np_transfer_customer x
                                  WHERE x.客户来源 = n.客户来源)),
            -- 跨来源同名客户去重:优先保留数据时点更新的行(新批全字段)
            cur AS (SELECT * FROM cur0 n
                     WHERE 数据时点 = (SELECT MAX(数据时点) FROM cur0 x
                                        WHERE x.外部客户名称 = n.外部客户名称)
                     GROUP BY 外部客户名称)
            SELECT c.外部客户名称, c.客户名称, c.客户编码, c.市, c.区县,
                   c.责任分销经理 AS 分销经理,
                   CASE WHEN EXISTS (
                        SELECT 1 FROM visit_record v
                         WHERE substr(v.拜访时间,1,10) <= ?
                           AND ((COALESCE(c.客户编码,'') != '' AND v.客户编码 = c.客户编码)
                                OR v.拜访客户 = c.外部客户名称)
                   ) THEN 1 ELSE 0 END AS 已覆盖,
                   CASE WHEN COALESCE(c.客户名称,'') != '' AND EXISTS (
                        SELECT 1 FROM provider_contract pc WHERE pc.客户名称 = c.客户名称
                   ) THEN 1 ELSE 0 END AS 已签约,
                   (SELECT MIN(substr(v.拜访时间,1,10)) FROM visit_record v
                     WHERE (COALESCE(c.客户编码,'') != '' AND v.客户编码 = c.客户编码)
                        OR v.拜访客户 = c.外部客户名称) AS 首次覆盖日,
                   (SELECT MIN(substr(pc.签约日期,1,10)) FROM provider_contract pc
                     WHERE COALESCE(c.客户名称,'') != '' AND pc.客户名称 = c.客户名称) AS 签约日期,
                   COALESCE((SELECT SUM(ir.产品现有分销价) FROM install_redpack ir
                              WHERE ir.上线客户名称 = c.客户名称
                                AND substr(ir.上线时间,1,7) = ?), 0) / 10000.0 AS 当月SO_万
              FROM cur c
        """, conn, params=(ym_end, ym_end[:7]))
    finally:
        conn.close()
    df['市'] = df['市'].fillna('(未知)')
    df['分销经理'] = df['分销经理'].fillna('(未分配)')
    return df


@st.cache_data(ttl=300, show_spinner="正在计算无线专项数据…")
def wireless_data(ym):
    """无线专项:服务商级明细(本月上线/铺货台数) + 各市SO台数 + 上月基线。"""
    conn = _conn()
    try:
        # 服务商级:本月无线上线(红包口径)
        cov = pd.read_sql("""
            SELECT ir.上线客户编码 AS 服务商编码, ir.上线客户名称 AS 服务商,
                   ir.上线客户地市 AS 市, COALESCE(ir.所属一级客户,'(无归属)') AS 代理商,
                   COUNT(*) AS 上线台数
              FROM install_redpack ir
              JOIN product_focus fc ON fc.物料号 = ir.物料号 AND fc.专项 = '无线'
             WHERE substr(ir.上线时间,1,7) = ?
             GROUP BY 1,2,3,4""", conn, params=(ym,))
        # 服务商级:本月无线铺货
        dist = pd.read_sql("""
            SELECT d.客户编码_下级 AS 服务商编码, d.客户名称_下级 AS 服务商,
                   d.客户所在城市_下级 AS 市, COALESCE(d.客户名称_上级,'(无归属)') AS 代理商,
                   COUNT(*) AS 铺货台数
              FROM distribution_info d
              JOIN product_focus fc ON fc.物料号 = d.物料号 AND fc.专项 = '无线'
             WHERE substr(d.提交铺货时间,1,7) = ?
             GROUP BY 1,2,3,4""", conn, params=(ym,))
        # 各市 SO 台数(主口径) + 代理商级
        so = pd.read_sql("""
            SELECT COALESCE(上线城市,'(未知)') AS 市,
                   COALESCE(出库客户名称,'(未知代理商)') AS 代理商, COUNT(*) AS SO台数
              FROM product_flow
             WHERE 三大重点专项 = '无线专项' AND substr(上线时间,1,7) = ?
             GROUP BY 1,2""", conn, params=(ym,))
        owner = pd.read_sql("SELECT 客户名称, 客户所有者 FROM signed_customer_monthly", conn)
    finally:
        conn.close()
    omap = dict(zip(owner['客户名称'].astype(str).str.strip(), owner['客户所有者']))
    sp = cov.merge(dist, on=['服务商编码'], how='outer', suffixes=('', '_d'))
    for col, alt in [('服务商', '服务商_d'), ('市', '市_d'), ('代理商', '代理商_d')]:
        if alt in sp.columns:
            sp[col] = sp[col].fillna(sp[alt])
    sp = sp[['服务商编码', '服务商', '市', '代理商', '上线台数', '铺货台数']].fillna(
        {'上线台数': 0, '铺货台数': 0})
    sp['分销经理'] = sp['代理商'].astype(str).str.strip().map(omap).fillna('(未映射)')
    so['分销经理'] = so['代理商'].astype(str).str.strip().map(omap).fillna('(未映射)')
    return sp, so


def np_city_actual(df):
    return df.groupby('市').agg(拜访覆盖家数=('已覆盖', 'sum'), 签约家数=('已签约', 'sum'),
                                SO金额_万=('当月SO_万', 'sum')).reset_index().rename(columns={'市': '地市'})


def wl_city_actual(sp, so):
    a = sp[sp['上线台数'] > 0].groupby('市')['服务商编码'].nunique().rename('覆盖服务商数')
    b = sp[sp['铺货台数'] > 0].groupby('市')['服务商编码'].nunique().rename('铺货服务商数')
    c_ = so.groupby('市')['SO台数'].sum()
    out = pd.concat([a, b, c_], axis=1).fillna(0).astype(int).reset_index().rename(columns={'市': '地市'})
    return out


def np_baseline(df):
    """拆分基线:各市 NP 客户数(覆盖/签约用),SO 用客户数同比例。"""
    base = df.groupby('市')['外部客户名称'].nunique().rename('基线').reset_index().rename(columns={'市': '地市'})
    return {m: base.copy() for m in SPECIALS['NP流转专项']['metrics']}


@st.cache_data(ttl=600, show_spinner="正在统计门店渠道覆盖…")
def store_channel_stats(days: int = 30):
    """批发门店/夫妻门店 无线覆盖与铺货(近 N 天滚动,默认30)。
    覆盖=近N天有无线上线(红包口径);铺货=近N天有无线铺货。门店类型=客户分类_规范。"""
    win = f'-{int(days)} day'
    conn = _conn()
    try:
        stores = pd.read_sql("""
            SELECT 客户编码, 客户名称, 客户城市 AS 市, 客户分类_规范 AS 类型,
                   上级分销商名称 AS 代理商
              FROM provider_contract_v
             WHERE 客户分类_规范 IN ('批发门店', '夫妻门店')""", conn)
        cov = set(str(r[0]) for r in conn.execute("""
            SELECT DISTINCT ir.上线客户编码 FROM install_redpack ir
              JOIN product_focus fc ON fc.物料号 = ir.物料号 AND fc.专项 = '无线'
             WHERE substr(ir.上线时间,1,10) >= date('now', ?)""", (win,)))
        stk = set(str(r[0]) for r in conn.execute("""
            SELECT DISTINCT d.客户编码_下级 FROM distribution_info d
              JOIN product_focus fc ON fc.物料号 = d.物料号 AND fc.专项 = '无线'
             WHERE substr(d.提交铺货时间,1,10) >= date('now', ?)""", (win,)))
    finally:
        conn.close()
    stores['已覆盖'] = stores['客户编码'].astype(str).isin(cov)
    stores['已铺货'] = stores['客户编码'].astype(str).isin(stk)
    return stores


@st.cache_data(ttl=300)
def np_so_by_month():
    """NP 客户(签约名命中)的 月度 SO(万):客户名称 × 月份。"""
    conn = _conn()
    df = pd.read_sql("""
        SELECT ir.上线客户名称 AS 客户名称, substr(ir.上线时间,1,7) AS 月份,
               SUM(ir.产品现有分销价) / 10000.0 AS SO_万
          FROM install_redpack ir
         WHERE EXISTS (SELECT 1 FROM np_transfer_customer n WHERE n.客户名称 = ir.上线客户名称)
         GROUP BY 1, 2""", conn)
    conn.close()
    return df


@st.cache_data(ttl=600)
def owner_name_map():
    """代理商名称 → 分销经理(客户所有者,SI名册)。"""
    conn = _conn()
    df = pd.read_sql("SELECT 客户名称, 客户所有者 FROM signed_customer_monthly", conn)
    conn.close()
    return dict(zip(df['客户名称'].astype(str).str.strip(), df['客户所有者']))


@st.cache_data(ttl=300, show_spinner="正在汇总无线渠道明细…")
def wl_channel_detail(s, e):
    return get_wireless_provider_table(s, e)


@st.cache_data(ttl=600)
def wl_baseline(prev_ym):
    """拆分基线 = 上月各市实际(覆盖/铺货/SO)。"""
    sp, so = wireless_data(prev_ym)
    act = wl_city_actual(sp, so)
    out = {}
    for m in SPECIALS['无线产品专项']['metrics']:
        out[m] = act[['地市', m]].rename(columns={m: '基线'})
    return out


def load_task(conn, special, ym):
    return pd.read_sql("SELECT 地市, 指标, 目标 FROM director_task WHERE 专项=? AND 月份=?",
                       conn, params=(special, ym))


# ══════════ 数据准备 ══════════
try:
    if special == 'NP流转专项':
        np_df = np_data(m_start, m_end)
        city_actual = np_city_actual(np_df)
        baselines = np_baseline(np_df)
    else:
        sp_df, so_df = wireless_data(ym)
        city_actual = wl_city_actual(sp_df, so_df)
        prev_ym = f"{(dt.date.fromisoformat(m_start) - dt.timedelta(days=1)):%Y-%m}"
        baselines = wl_baseline(prev_ym)
except Exception as e:
    st.error(f"数据不可用:{e}")
    st.stop()

conn = _conn()
_ensure_tables(conn)
task_df = load_task(conn, special, ym)
conn.close()

tab_check, tab_set = st.tabs(["📈 结果检查", "📋 任务设置"])

# ══════════ 任务设置 ══════════
with tab_set:
    if not is_admin():
        st.info("任务设置仅 admin 可操作。")
    else:
        st.markdown(f"**{special} · {ym} 目标设置** — 输入全省目标,按各市基线占比自动拆分,可微调后保存。")
        metrics = SPECIALS[special]['metrics']
        int_m = SPECIALS[special]['int_metrics']

        # 全省目标输入
        cols = st.columns(len(metrics))
        prov_targets = {}
        for i, m in enumerate(metrics):
            cur_sum = float(task_df[task_df['指标'] == m]['目标'].sum()) if len(task_df) else 0.0
            prov_targets[m] = cols[i].number_input(
                f"全省目标·{m}", min_value=0.0, value=float(cur_sum), step=1.0, key=f"pt_{special}_{ym}_{m}")

        if st.button("🔀 按基线拆分到地市", key="split_btn"):
            rows = []
            for m in metrics:
                base = baselines[m].copy()
                base = base[base['地市'].notna() & (base['地市'] != '(未知)')]
                tot = float(base['基线'].sum())
                T = prov_targets[m]
                if tot <= 0 or T <= 0:
                    base['目标'] = 0
                else:
                    raw = base['基线'] / tot * T
                    if m in int_m:
                        base['目标'] = raw.round().astype(int)
                        diff = int(round(T)) - int(base['目标'].sum())
                        if diff != 0 and len(base):   # 四舍五入差额补到基线最大的市
                            idx = base['基线'].idxmax()
                            base.loc[idx, '目标'] += diff
                    else:
                        base['目标'] = raw.round(1)
                for _, r in base.iterrows():
                    rows.append({'地市': r['地市'], '指标': m, '目标': float(r['目标']),
                                 '基线': float(r['基线'])})
            st.session_state['split_result'] = pd.DataFrame(rows)

        edit_src = st.session_state.get('split_result')
        if edit_src is None and len(task_df):
            edit_src = task_df.copy()
            edit_src['基线'] = None
        if edit_src is not None and len(edit_src):
            pv = edit_src.pivot_table(index='地市', columns='指标', values='目标', aggfunc='first').reset_index()
            st.caption("↓ 可直接改格子;保存 = 覆盖该专项该月全部目标")
            edited = st.data_editor(pv, hide_index=True, use_container_width=True,
                                    key=f"editor_{special}_{ym}")
            csum = st.columns(len(metrics))
            for i, m in enumerate(metrics):
                if m in edited.columns:
                    csum[i].caption(f"{m} 合计:**{edited[m].sum():g}**")
            if st.button("💾 保存目标", type="primary", key="save_task"):
                conn = _conn()
                _ensure_tables(conn)
                conn.execute("DELETE FROM director_task WHERE 专项=? AND 月份=?", (special, ym))
                now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                n = 0
                for _, r in edited.iterrows():
                    for m in metrics:
                        if m in edited.columns and pd.notna(r[m]):
                            conn.execute("""INSERT OR REPLACE INTO director_task
                                (专项, 月份, 地市, 指标, 目标, 设置人, 设置时间) VALUES (?,?,?,?,?,?,?)""",
                                         (special, ym, str(r['地市']), m, float(r[m]), current_user(), now))
                            n += 1
                conn.commit()
                conn.close()
                st.session_state.pop('split_result', None)
                st.success(f"✅ 已保存 {n} 条目标")
                st.rerun()
        else:
            st.caption("尚未设置目标:输入全省目标后点「按基线拆分到地市」。")

        with st.expander("查看各市基线"):
            bshow = None
            for m in metrics:
                b = baselines[m].rename(columns={'基线': m}).set_index('地市')
                bshow = b if bshow is None else bshow.join(b, how='outer')
            st.dataframe(bshow.fillna(0), use_container_width=True)
            st.caption("NP 专项基线=各市 NP 客户数;无线专项基线=上月各市实际。")

        # ── 📅 多月阶段计划:每个阶段(指标)一张 地市×月份 网格,按月设目标 ──
        st.divider()
        st.markdown("**📅 多月阶段计划(每个阶段按月设目标)**")
        st.caption("行=地市,列=月份;留空=该月不设目标。"
                   + ("覆盖/签约 = 累计到该月末的里程碑(如 8 月列填 300 = 8 月底累计覆盖 300 家);"
                      "SO = 该月当月产单目标。" if special == 'NP流转专项' else
                      "各指标均为该月目标。"))
        conn_m = _conn()
        _ensure_tables(conn_m)
        all_t = pd.read_sql("SELECT 月份, 地市, 指标, 目标 FROM director_task WHERE 专项=?",
                            conn_m, params=(special,))
        conn_m.close()
        grid_months = sorted(set(all_t['月份'].astype(str)) |
                             set(str(p) for p in pd.period_range(f"{today:%Y-%m}", periods=6, freq='M')))
        base_cities = sorted(set().union(*[
            set(baselines[m]['地市'].dropna()) - {'(未知)'} for m in metrics]) |
            set(all_t['地市'].dropna()))
        stage_tabs = st.tabs([NP_STAGE.get(m, m) if special == 'NP流转专项' else m for m in metrics])
        edited_grids = {}
        for m, stab in zip(metrics, stage_tabs):
            with stab:
                tgt = all_t[all_t['指标'] == m]
                grid = pd.DataFrame(index=base_cities, columns=grid_months, dtype=float)
                for _, r in tgt.iterrows():
                    if str(r['月份']) in grid.columns and r['地市'] in grid.index:
                        grid.loc[r['地市'], str(r['月份'])] = r['目标']
                grid = grid.reset_index().rename(columns={'index': '地市'})
                edited_grids[m] = st.data_editor(grid, hide_index=True, use_container_width=True,
                                                 key=f"mgrid_{special}_{m}")
                sums = edited_grids[m][grid_months].sum(numeric_only=True)
                st.caption("全省合计:" + "  ·  ".join(f"{mm} **{v:g}**" for mm, v in sums.items() if v > 0))
        if st.button("💾 保存多月计划(全部阶段)", type="primary", key="save_multi"):
            conn_m = _conn()
            _ensure_tables(conn_m)
            now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ph_m = ','.join('?' * len(grid_months))
            n = 0
            for m in metrics:
                conn_m.execute(f"DELETE FROM director_task WHERE 专项=? AND 指标=? AND 月份 IN ({ph_m})",
                               (special, m, *grid_months))
                g = edited_grids[m]
                for _, r in g.iterrows():
                    for mm in grid_months:
                        v = r.get(mm)
                        if pd.notna(v) and float(v) != 0:
                            conn_m.execute("""INSERT OR REPLACE INTO director_task
                                (专项, 月份, 地市, 指标, 目标, 设置人, 设置时间) VALUES (?,?,?,?,?,?,?)""",
                                           (special, mm, str(r['地市']), m, float(v), current_user(), now))
                            n += 1
            conn_m.commit()
            conn_m.close()
            st.success(f"✅ 已保存 {n} 条月度目标")
            st.rerun()

# ══════════ 结果检查 ══════════
with tab_check:
    metrics = SPECIALS[special]['metrics']
    if task_df.empty:
        st.warning(f"{special} {ym} 尚未设置目标 → 先到「📋 任务设置」拆分保存。下方仍展示实际值。")

    # 筛选
    fc = st.columns([1.3, 1.6, 1.6])
    city_opts = ['全省'] + sorted([x for x in city_actual['地市'].dropna().unique() if x != '(未知)'])
    f_city = fc[0].selectbox("地市", city_opts)
    if special == 'NP流转专项':
        mgr_opts = ['全部'] + sorted(np_df['分销经理'].dropna().unique())
        f_mgr = fc[1].selectbox("分销经理(责任人)", mgr_opts)
        f_dealer = '全部'
    else:
        dealer_opts = ['全部'] + sorted(set(sp_df['代理商'].dropna()) | set(so_df['代理商'].dropna()))
        f_dealer = fc[1].selectbox("代理商", dealer_opts)
        mgr_opts = ['全部'] + sorted(set(sp_df['分销经理'].dropna()) | set(so_df['分销经理'].dropna()))
        f_mgr = fc[2].selectbox("分销经理", mgr_opts)

    # 全省/单市 进度条
    st.markdown(f"#### 📊 {('全省' if f_city == '全省' else f_city)} 进度({ym})")
    tgt_pv = task_df.pivot_table(index='地市', columns='指标', values='目标', aggfunc='first') \
        if len(task_df) else pd.DataFrame()
    act_pv = city_actual.set_index('地市')
    pc = st.columns(len(metrics))
    for i, m in enumerate(metrics):
        if f_city == '全省':
            a = float(act_pv[m].sum()) if m in act_pv.columns else 0.0
            t = float(tgt_pv[m].sum()) if m in tgt_pv.columns else 0.0
        else:
            a = float(act_pv.loc[f_city, m]) if (f_city in act_pv.index and m in act_pv.columns) else 0.0
            t = float(tgt_pv.loc[f_city, m]) if (f_city in tgt_pv.index and m in tgt_pv.columns) else 0.0
        pct = (a / t) if t > 0 else None
        m_title = NP_STAGE.get(m, m) if special == 'NP流转专项' else m
        pc[i].metric(m_title, f"{a:g}", delta=(f"目标 {t:g} · {pct*100:.0f}%" if pct is not None else "未设目标"),
                     delta_color="off")
        pc[i].progress(min(1.0, pct) if pct is not None else 0.0)

    # 无线专项:门店渠道覆盖(近 N 天滚动,默认30)
    if special == '无线产品专项':
        hc2 = st.columns([2.4, 1, 2.6])
        hc2[0].markdown("#### 🏪 门店渠道覆盖(滚动窗口)")
        win_days = hc2[1].selectbox("覆盖窗口(天)", [30, 60, 90], index=0, key="store_win",
                                    help="近 N 天内有无线上线/铺货即算覆盖/铺货,默认 30 天")
        try:
            stores = store_channel_stats(win_days)
            sv = stores if f_city == '全省' else stores[stores['市'] == f_city]
            # 跟随页面筛选:代理商(上级分销商) / 分销经理(代理商→客户所有者)
            omap_n = owner_name_map()
            if f_dealer != '全部':
                sv = sv[sv['代理商'].astype(str).str.strip() == f_dealer]
            if f_mgr != '全部':
                sv = sv[sv['代理商'].astype(str).str.strip().map(omap_n).fillna('(未映射)') == f_mgr]
            mc = st.columns(4)
            for i, typ in enumerate(['批发门店', '夫妻门店']):
                t_ = sv[sv['类型'] == typ]
                n = len(t_)
                cov_n, stk_n = int(t_['已覆盖'].sum()), int(t_['已铺货'].sum())
                mc[i * 2].metric(f"{typ}覆盖率", f"{(cov_n / n * 100) if n else 0:.1f}%",
                                 delta=f"{cov_n}/{n} 家", delta_color="off",
                                 help=f"近{win_days}天内有无线上线(红包口径)即算覆盖")
                mc[i * 2 + 1].metric(f"{typ}铺货率", f"{(stk_n / n * 100) if n else 0:.1f}%",
                                     delta=f"{stk_n}/{n} 家", delta_color="off",
                                     help=f"近{win_days}天内有无线铺货")
            with st.expander("按地市:门店覆盖/铺货 明细"):
                agg = sv.groupby(['市', '类型']).agg(
                    门店数=('客户编码', 'count'), 覆盖=('已覆盖', 'sum'), 铺货=('已铺货', 'sum')).reset_index()
                agg['覆盖率'] = (agg['覆盖'] / agg['门店数']).round(3)
                agg['铺货率'] = (agg['铺货'] / agg['门店数']).round(3)
                st.dataframe(agg.sort_values(['类型', '门店数'], ascending=[True, False]),
                             hide_index=True, use_container_width=True,
                             column_config={
                                 '覆盖率': st.column_config.ProgressColumn('覆盖率', min_value=0, max_value=1, format="percent"),
                                 '铺货率': st.column_config.ProgressColumn('铺货率', min_value=0, max_value=1, format="percent")})
            with st.expander(f"🎯 未覆盖门店名单(近{win_days}天无无线上线 — 待触达)"):
                un = sv[~sv['已覆盖']][['类型', '客户名称', '市', '代理商', '已铺货']].copy()
                un['已铺货'] = un['已铺货'].map({True: '✅已铺未动销', False: ''})
                st.dataframe(un.sort_values(['类型', '市']), hide_index=True,
                             use_container_width=True, height=300)
                st.caption(f"「✅已铺未动销」= 铺了货但近{win_days}天没上线,优先激活;其余为待铺货+待覆盖。"
                           "完整服务商级明细见「📶 无线渠道明细」页。")
        except Exception as e:
            st.warning(f"门店渠道统计不可用:{e}")

    # 各市完成情况表(带进度条列)
    st.markdown("#### 🗺️ 各地市完成情况")
    rows = []
    for city in sorted(set(list(act_pv.index) + (list(tgt_pv.index) if len(tgt_pv) else []))):
        if city == '(未知)':
            continue
        row = {'地市': city}
        for m in metrics:
            a = float(act_pv.loc[city, m]) if (city in act_pv.index and m in act_pv.columns) else 0.0
            t = float(tgt_pv.loc[city, m]) if (len(tgt_pv) and city in tgt_pv.index and m in tgt_pv.columns) else 0.0
            row[f"{m}·实际"] = round(a, 1)
            row[f"{m}·目标"] = round(t, 1)
            row[f"{m}·完成率"] = round(min(a / t, 1.5), 3) if t > 0 else None
        rows.append(row)
    city_table = pd.DataFrame(rows)
    if f_city != '全省' and len(city_table):
        city_table = city_table[city_table['地市'] == f_city]
    colcfg = {f"{m}·完成率": st.column_config.ProgressColumn(f"{m}完成率", min_value=0, max_value=1.5,
                                                            format="percent") for m in metrics}
    st.dataframe(city_table, hide_index=True, use_container_width=True, column_config=colcfg)

    # ── NP:三阶段月度进度(每月目标 vs 实际) ──
    if special == 'NP流转专项':
        st.markdown("#### 🗓️ 三阶段月度进度(每月目标 vs 实际)")
        try:
            conn_p = _conn()
            _ensure_tables(conn_p)
            all_t = pd.read_sql("SELECT 月份, 地市, 指标, 目标 FROM director_task WHERE 专项=?",
                                conn_p, params=(special,))
            conn_p.close()
            base = np_df.copy()
            if f_city != '全省':
                base = base[base['市'] == f_city]
                all_t = all_t[all_t['地市'] == f_city]
            if f_mgr != '全部':
                base = base[base['分销经理'] == f_mgr]
            so_m = np_so_by_month()
            so_map = so_m[so_m['客户名称'].isin(set(base['客户名称'].dropna()))] \
                .groupby('月份')['SO_万'].sum().to_dict()
            mlist = sorted(set(all_t['月份'].astype(str)) | {f"{today:%Y-%m}"})
            cov_d = pd.to_datetime(base['首次覆盖日'], errors='coerce')
            sig_d = pd.to_datetime(base['签约日期'], errors='coerce')
            rows_m = []
            for mth in mlist:
                mend = pd.Timestamp(_month_end(mth))
                fut = dt.date.fromisoformat(f"{mth}-01") > today
                tg = all_t[all_t['月份'].astype(str) == mth].groupby('指标')['目标'].sum()
                r = {'月份': mth}
                for key, label, actual in [
                        ('拜访覆盖家数', '覆盖', None if fut else int((cov_d <= mend).sum())),
                        ('签约家数', '签约', None if fut else int((sig_d <= mend).sum())),
                        ('SO金额_万', '产单SO_万', None if fut else round(so_map.get(mth, 0.0), 2))]:
                    t = float(tg.get(key, 0) or 0)
                    r[f'{label}·目标'] = t if t else None
                    r[f'{label}·实际'] = actual
                    r[f'{label}·完成率'] = (round(min(actual / t, 1.5), 3)
                                          if (t and actual is not None) else None)
                rows_m.append(r)
            mtab = pd.DataFrame(rows_m)
            mcfg = {f'{lb}·完成率': st.column_config.ProgressColumn(f'{lb}完成率', min_value=0,
                                                                  max_value=1.5, format="percent")
                    for lb in ['覆盖', '签约', '产单SO_万']}
            st.dataframe(mtab, hide_index=True, use_container_width=True, column_config=mcfg)
            st.caption("覆盖/签约 = **累计到该月末**(里程碑口径,首次拜访日/签约日期判定);"
                       "产单SO = **该月当月**。未来月份实际留空。"
                       + ("选了分销经理时目标仍为地市级合计,完成率仅供参考。" if f_mgr != '全部' else ""))
        except Exception as e:
            st.warning(f"月度进度不可用:{e}")

    # 明细(按筛选,落到客户/服务商/分销经理)
    if special == 'NP流转专项':
        det = np_df.copy()
        if f_city != '全省':
            det = det[det['市'] == f_city]
        if f_mgr != '全部':
            det = det[det['分销经理'] == f_mgr]
        st.markdown(f"#### 📋 NP 转入客户跟进明细({len(det)} 家 · 覆盖→签约→SO 漏斗)")
        show = det[['外部客户名称', '市', '区县', '分销经理']].copy()
        show['已覆盖'] = det['已覆盖'].map({1: '✅', 0: ''})
        show['已签约'] = det['已签约'].map({1: '✅', 0: ''})
        show['当月SO_万'] = det['当月SO_万'].round(2)
        show = show.sort_values(['已签约', '已覆盖', '当月SO_万'], ascending=False)
        st.dataframe(show, hide_index=True, use_container_width=True, height=380)
        st.caption("✅已覆盖 = 分销经理拜访过(触达,按客户编码/名称匹配拜访记录) / "
                   "✅已签约 = 已进服务商签约表。**已覆盖未签约 = 报备/意向培育中,重点转化对象**;"
                   "未覆盖 = 待拜访名单。")
        by_mgr = det.groupby('分销经理').agg(客户数=('外部客户名称', 'count'), 已覆盖=('已覆盖', 'sum'),
                                          已签约=('已签约', 'sum'), 当月SO_万=('当月SO_万', 'sum')).reset_index()
        by_mgr['覆盖率'] = (by_mgr['已覆盖'] / by_mgr['客户数']).round(3)
        with st.expander("按分销经理汇总(覆盖率/签约/SO)"):
            st.dataframe(by_mgr.sort_values('客户数', ascending=False), hide_index=True,
                         use_container_width=True,
                         column_config={'覆盖率': st.column_config.ProgressColumn('覆盖率', min_value=0,
                                                                               max_value=1, format="percent")})
    else:
        det = sp_df.copy()
        if f_city != '全省':
            det = det[det['市'] == f_city]
        if f_dealer != '全部':
            det = det[det['代理商'] == f_dealer]
        if f_mgr != '全部':
            det = det[det['分销经理'] == f_mgr]
        det = det.sort_values(['上线台数', '铺货台数'], ascending=False)
        st.markdown(f"#### 📋 本月无线动销服务商明细({len(det)} 家 · 当月有无线上线或铺货)")
        st.dataframe(det[['分销经理', '代理商', '服务商', '市', '上线台数', '铺货台数']],
                     hide_index=True, use_container_width=True, height=380)
        st.caption("圈定标准 = 任务月内「有无线上线(红包口径,物料判无线)」∪「有无线铺货」的服务商并集;"
                   "本月没动无线的不在此表(待触达门店见上方「未覆盖门店名单」)。"
                   "注意:上线客户不要求已签约,可能含未签约/意向客户。")
        so_v = so_df.copy()
        if f_city != '全省':
            so_v = so_v[so_v['市'] == f_city]
        if f_dealer != '全部':
            so_v = so_v[so_v['代理商'] == f_dealer]
        if f_mgr != '全部':
            so_v = so_v[so_v['分销经理'] == f_mgr]
        with st.expander("无线 SO 台数·按代理商/分销经理(主口径)"):
            ag = so_v.groupby(['分销经理', '代理商'])['SO台数'].sum().reset_index() \
                .sort_values('SO台数', ascending=False)
            st.dataframe(ag, hide_index=True, use_container_width=True)

        # ── 📶 无线渠道明细(原独立页整合入专项;服务商级激活/铺货/完成率)──
        st.markdown("#### 📶 无线渠道明细(服务商级)")
        dc = st.columns([1, 1, 2.5])
        _d1 = dt.date.fromisoformat(m_start)
        _me = (_d1.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        wd_start = dc[0].date_input("起始", value=_d1, min_value=dt.date(2025, 1, 1),
                                    max_value=today, key="wd_start")
        wd_end = dc[1].date_input("截止", value=min(today, _me), min_value=dt.date(2025, 1, 1),
                                  max_value=today, key="wd_end")
        if wd_start > wd_end:
            st.warning("起始日期不能晚于截止日期。")
        else:
            try:
                wd = wl_channel_detail(str(wd_start), str(wd_end))
                # 数据权限(缓存全量,权限过滤放缓存后) + 跟随页面筛选
                wd = filter_by_scope(wd, city_col='地市', district_col='区县', dealer_col='所属一级')
                if f_city != '全省':
                    wd = wd[wd['地市'] == f_city]
                if f_dealer != '全部':
                    wd = wd[wd['所属一级'].astype(str).str.strip() == f_dealer]
                if f_mgr != '全部':
                    omap_n = owner_name_map()
                    wd = wd[wd['所属一级'].astype(str).str.strip().map(omap_n).fillna('(未映射)') == f_mgr]
                tsel = st.selectbox("🏷️ 类型", ['全部'] + sorted(wd['类型'].dropna().unique().tolist()),
                                    key="wd_type")
                if tsel != '全部':
                    wd = wd[wd['类型'] == tsel]
                if wd.empty:
                    st.info("📭 当前筛选无服务商。")
                else:
                    mc2 = st.columns(5)
                    mc2[0].metric("服务商数", f"{len(wd):,}")
                    mc2[1].metric("激活台数", f"{int(wd['激活台数'].sum()):,}")
                    mc2[2].metric("无线激活台数", f"{int(wd['无线激活台数'].sum()):,}")
                    mc2[3].metric("总铺货台数", f"{int(wd['总铺货台数'].sum()):,}")
                    mc2[4].metric("无线铺货台数", f"{int(wd['无线铺货台数'].sum()):,}")
                    _act = int(wd['激活台数'].sum())
                    _wact = int(wd['无线激活台数'].sum())
                    st.caption(f"无线激活占比 {(_wact / _act * 100 if _act else 0):.1f}% · "
                               f"红包扫码口径 · {wd_start} ~ {wd_end}")

                    def _pct(v):
                        return f"{v * 100:.0f}%" if pd.notna(v) else '—'
                    disp = wd.copy()
                    disp['总体铺货完成率'] = disp['总体铺货完成率'].map(_pct)
                    disp['无线铺货完成率'] = disp['无线铺货完成率'].map(_pct)
                    st.dataframe(disp, use_container_width=True, height=480, hide_index=True)
                    st.download_button(
                        "⬇️ 导出表格 (CSV)",
                        disp.to_csv(index=False).encode('utf-8-sig'),
                        file_name=f"无线渠道明细_{wd_start}_{wd_end}.csv", mime="text/csv")
            except Exception as e:
                st.warning(f"渠道明细不可用:{e}")

st.caption("口径:NP=当前口径客户,覆盖/签约累计、SO当月;无线覆盖/铺货=红包/铺货表(物料判无线),"
           "SO台数=product_flow 无线专项(与产品经理工作台一致)。任务拆分基线:NP=各市客户数,无线=上月各市实际。")
