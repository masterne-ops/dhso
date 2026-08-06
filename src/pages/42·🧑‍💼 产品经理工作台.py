#!/usr/bin/env python3
"""🧑‍💼 产品经理工作台 — 打开就知道:上周干成了什么、本周该干什么、找谁去干。

四块:
  1. KPI 仪表盘(带目标与达成率,目标 admin 可在页内设置,存 pm_kpi_target)
  2. 上周成果(数据自动统计)
  3. 本周工作清单(规则自动生成,可打勾留痕 pm_task_check)
  4. 每项工作可下钻:数据落到 分销经理 → 代理商 → 服务商,直接变成"找谁干什么"。

口径:产品均衡达标同 page41;三大专项 = product_flow.三大重点专项;滞销同 page40 默认阈值;
代理商→分销经理 = signed_customer_monthly.客户所有者;GTM 明细 = page39 看板(服务商级)。
配套工作方法见 docs/产品经理工作指引-2026H2.md。
"""
import sys
import sqlite3
import datetime as dt
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin, get_current_role, current_user  # noqa: E402

DB_PATH = Path(__file__).parent.parent.parent / "db" / "product_flow.db"
TH_CCTV, TH_SHUTONG, TH_PEITAO = 0.70, 0.03, 0.10
ZHUANXIANG = ('夜视王专项', '无线专项', '场景化专项')

# 默认目标(admin 可在页内「🎯 目标设置」改;0=未设定)
KPI_TARGET_SEEDS = [
    ('产品均衡达标家数', '2026Q3', 22),
    ('产品均衡达标家数', '2026Q4', 26),
    ('夜视王SO月台数', '月度', 3000),
    ('无线SO月台数', '月度', 3500),
    ('场景化SO月台数', '月度', 0),
    ('滞销预警金额万', '2026年末', 700),
]

require_auth()
if not (is_admin() or get_current_role() == 'product_manager'):
    st.error(f"⛔ 产品经理工作台仅 admin / 产品经理 可用。当前:`{current_user()}` / `{get_current_role()}`")
    st.stop()

today = dt.date.today()
week_start = today - dt.timedelta(days=today.weekday())
last_week_start = week_start - dt.timedelta(days=7)
last_week_end = week_start - dt.timedelta(days=1)
prev_week_start = last_week_start - dt.timedelta(days=7)
cur_quarter = f"{today.year}Q{(today.month - 1) // 3 + 1}"

st.markdown("### 🧑‍💼 产品经理工作台")
st.caption(f"本周 {week_start} ~ {week_start + dt.timedelta(days=6)} · "
           f"上周 {last_week_start} ~ {last_week_end} · 工作方法见《产品经理工作指引-2026H2》")


def _conn():
    return sqlite3.connect(str(DB_PATH))


def _ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pm_task_check (
            周 TEXT NOT NULL, 任务ID TEXT NOT NULL, 任务 TEXT,
            完成 INTEGER DEFAULT 0, 完成时间 TEXT, 操作人 TEXT,
            PRIMARY KEY (周, 任务ID)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pm_kpi_target (
            指标 TEXT NOT NULL, 周期 TEXT NOT NULL, 目标 REAL,
            PRIMARY KEY (指标, 周期)
        )""")
    for k, p, v in KPI_TARGET_SEEDS:
        conn.execute("INSERT OR IGNORE INTO pm_kpi_target(指标, 周期, 目标) VALUES (?,?,?)", (k, p, v))
    conn.commit()


@st.cache_data(ttl=120)
def load_targets():
    conn = _conn()
    _ensure_tables(conn)
    df = pd.read_sql("SELECT 指标, 周期, 目标 FROM pm_kpi_target", conn)
    conn.close()
    return df


def target_of(tdf, 指标, 周期候选):
    for p in 周期候选:
        r = tdf[(tdf['指标'] == 指标) & (tdf['周期'] == p)]
        if len(r) and pd.notna(r.iloc[0]['目标']) and float(r.iloc[0]['目标']) > 0:
            return float(r.iloc[0]['目标'])
    return None


# ══════════ 取数(每块独立 try,坏一块不影响整页) ══════════

@st.cache_data(ttl=600, show_spinner="正在汇总产品均衡…")
def load_balance():
    """产品均衡(口径同 page41)。返回 (df 含达标/占比/分销经理, 一级代理商总数)。"""
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT 数据时点, 客户编码, 客户名称, 客户城市, 实销金额,
                   CCTV占比, 配套占比, 数通实销占比, CCTV实销, 配套实销, 数通实销
              FROM product_line_balance""", conn)
        owner = pd.read_sql("SELECT 客户编码, 客户所有者 FROM signed_customer_monthly", conn)
    finally:
        conn.close()
    df['客户编码'] = df['客户编码'].astype(str)
    owner['客户编码'] = owner['客户编码'].astype(str)
    df = df[df['客户编码'].isin(set(owner['客户编码']))]
    df = df.merge(owner, on='客户编码', how='left')
    df['分销经理'] = df['客户所有者'].fillna('(未映射)')

    def ratio(r, rc, ac):
        if pd.notna(r[rc]):
            return float(r[rc])
        t = r['实销金额']
        return (float(r[ac] or 0) / float(t)) if pd.notna(t) and float(t) > 0 else None

    df['_cctv'] = df.apply(lambda r: ratio(r, 'CCTV占比', 'CCTV实销'), axis=1)
    df['_pt'] = df.apply(lambda r: ratio(r, '配套占比', '配套实销'), axis=1)
    df['_st'] = df.apply(lambda r: ratio(r, '数通实销占比', '数通实销'), axis=1)
    df['_ok'] = df.apply(lambda r: r['_cctv'] is not None and r['_cctv'] >= TH_CCTV
                         and ((r['_st'] or 0) > TH_SHUTONG or (r['_pt'] or 0) > TH_PEITAO), axis=1)
    return df, len(codes)


@st.cache_data(ttl=600)
def so_by_special(d1, d2):
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT 三大重点专项, COUNT(*) FROM product_flow
             WHERE substr(上线时间,1,10) BETWEEN ? AND ? AND 三大重点专项 IN (?,?,?)
             GROUP BY 1""", (str(d1), str(d2), *ZHUANXIANG)).fetchall()
    finally:
        conn.close()
    return {k: v for k, v in rows}


@st.cache_data(ttl=600, show_spinner="正在下钻三大专项…")
def special_drill(d1, d2):
    """专项 SO 下钻(与 KPI 同口径 product_flow):专项 × 代理商 台数 + 分销经理映射。"""
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT 三大重点专项 AS 专项, COALESCE(出库客户名称,'(未知代理商)') AS 代理商,
                   COUNT(*) AS 台数
              FROM product_flow
             WHERE substr(上线时间,1,10) BETWEEN ? AND ? AND 三大重点专项 IN (?,?,?)
             GROUP BY 1,2""", conn, params=(str(d1), str(d2), *ZHUANXIANG))
        owner = pd.read_sql("SELECT 客户名称, 客户所有者 FROM signed_customer_monthly", conn)
    finally:
        conn.close()
    df = df.merge(owner, left_on='代理商', right_on='客户名称', how='left')
    df['分销经理'] = df['客户所有者'].fillna('(未映射)')
    return df[['专项', '分销经理', '代理商', '台数']]


@st.cache_data(ttl=600)
def special_drill_provider(d1, d2):
    """专项 × 服务商 下钻(红包口径:上线客户名称=真实服务商,物料号→product_focus 判专项;
    product_flow 的上线自客户名称大量为'其他'占位,不可用作服务商维度)。"""
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT fc.专项, ir.上线客户名称 AS 服务商,
                   COALESCE(ir.所属一级客户,'(无归属)') AS 代理商,
                   COUNT(*) AS 台数
              FROM install_redpack ir
              JOIN product_focus fc ON fc.物料号 = ir.物料号
             WHERE substr(ir.上线时间,1,10) BETWEEN ? AND ?
             GROUP BY 1,2,3""", conn, params=(str(d1), str(d2)))
        owner = pd.read_sql("SELECT 客户名称, 客户所有者 FROM signed_customer_monthly", conn)
    finally:
        conn.close()
    df = df.merge(owner, left_on='代理商', right_on='客户名称', how='left')
    df['分销经理'] = df['客户所有者'].fillna('(未映射)')
    return df[['专项', '分销经理', '代理商', '服务商', '台数']]


@st.cache_data(ttl=600, show_spinner="正在计算滞销预警…")
def stale_data():
    from _restock_advisor import get_matrix, stale_alerts
    m = get_matrix()
    alerts, _ = stale_alerts(m)
    val_col = next((c for c in ['现存货值', '货值'] if c in alerts.columns), None)
    return alerts, val_col


@st.cache_data(ttl=600)
def owner_by_name():
    conn = _conn()
    df = pd.read_sql("SELECT 客户名称, 客户所有者 FROM signed_customer_monthly", conn)
    conn.close()
    return dict(zip(df['客户名称'].astype(str).str.strip(), df['客户所有者']))


@st.cache_data(ttl=600)
def data_watermarks():
    conn = _conn()
    out = []
    try:
        checks = [
            ('product_flow(SO主表)', "SELECT MAX(substr(上线时间,1,10)) FROM product_flow"),
            ('install_redpack(红包)', "SELECT MAX(substr(上线时间,1,10)) FROM install_redpack"),
            ('visit_record(跑动)', "SELECT MAX(substr(拜访时间,1,10)) FROM visit_record"),
            ('distribution_info(铺货)', "SELECT MAX(substr(提交铺货时间,1,10)) FROM distribution_info"),
            ('product_line_balance(RP10)', "SELECT MAX(数据时点) FROM product_line_balance"),
            ('dealer_purchase(进货)', "SELECT MAX(数据年份)||'-'||printf('%02d',MAX(月份)) FROM dealer_purchase WHERE 数据年份=(SELECT MAX(数据年份) FROM dealer_purchase)"),
        ]
        for name, q in checks:
            try:
                v = conn.execute(q).fetchone()[0]
                lag = None
                if v and len(str(v)) >= 10:
                    lag = (dt.date.today() - dt.date.fromisoformat(str(v)[:10])).days
                out.append((name, str(v), lag))
            except Exception as e:
                out.append((name, f'查询失败:{e}', None))
    finally:
        conn.close()
    return out


@st.cache_data(ttl=600, show_spinner="正在读取 GTM 看板…")
def gtm_board_cached(year_month):
    from _gtm import get_gtm_board
    return get_gtm_board(year_month)


def gtm_status():
    try:
        from _gtm import list_target_months, get_targets
        months = list_target_months()
        cur = today.strftime('%Y-%m')
        if cur in months:
            return cur, len(get_targets(cur))
        return cur, None
    except Exception:
        return today.strftime('%Y-%m'), None


tdf = load_targets()
bal_df, n_dealers = None, 0
try:
    bal_df, n_dealers = load_balance()
except Exception as e:
    st.warning(f"产品均衡数据不可用:{e}")

# ══════════ 1. KPI 仪表盘(带目标) ══════════
st.markdown("#### 📊 KPI 仪表盘")
c = st.columns(6)

snaps, cur_snap, ok_now, ok_prev = [], None, set(), set()
if bal_df is not None and not bal_df.empty:
    snaps = sorted(bal_df['数据时点'].astype(str).unique())
    cur_snap = snaps[-1]
    ok_now = set(bal_df[(bal_df['数据时点'].astype(str) == cur_snap) & bal_df['_ok']]['客户名称'])
    if len(snaps) >= 2:
        ok_prev = set(bal_df[(bal_df['数据时点'].astype(str) == snaps[-2]) & bal_df['_ok']]['客户名称'])
    t = target_of(tdf, '产品均衡达标家数', [cur_quarter, '月度'])
    delta = f"{len(ok_now) - t:+.0f} vs 目标{t:g}" if t else "未设目标"
    c[0].metric("⚖️ 产品均衡达标", f"{len(ok_now)}/{n_dealers}", delta=delta,
                delta_color=("normal" if t else "off"),
                help=f"快照 {cur_snap} · 目标周期 {cur_quarter} · CCTV≥70% 且 数通>3% 或配套>10%")
else:
    c[0].metric("⚖️ 产品均衡达标", "—")

mtd = {}
try:
    mtd = so_by_special(today.replace(day=1), today)
    for i, (zx, icon, tkey) in enumerate([('夜视王专项', '🌙', '夜视王SO月台数'),
                                          ('无线专项', '📶', '无线SO月台数'),
                                          ('场景化专项', '🎬', '场景化SO月台数')]):
        t = target_of(tdf, tkey, ['月度', today.strftime('%Y-%m')])
        v = mtd.get(zx, 0)
        delta = f"月目标{t:g} · 达成{v / t * 100:.0f}%" if t else "未设目标"
        c[1 + i].metric(f"{icon} {zx[:3]}SO(月)", f"{v:,}", delta=delta, delta_color="off",
                        help="本月 MTD 台数 vs 月度目标;点下方「三大专项下钻」看落到谁")
except Exception as e:
    c[1].caption(f"专项SO不可用:{e}")

stale_alerts_df, stale_val_col = None, None
try:
    stale_alerts_df, stale_val_col = stale_data()
    v_stale = round(float(stale_alerts_df[stale_val_col].sum()) / 10000, 0) if stale_val_col else None
    t = target_of(tdf, '滞销预警金额万', ['2026年末', cur_quarter, '月度'])
    delta = (f"目标≤{t:g}万 · 现{v_stale:,.0f}万" if (t and v_stale is not None) else
             (f"货值{v_stale:,.0f}万" if v_stale is not None else "未设目标"))
    c[4].metric("📦 滞销预警", f"{len(stale_alerts_df):,} 条", delta=delta, delta_color="off",
                help="口径同 page40 默认阈值;点下方任务下钻看 TOP 代理商")
except Exception as e:
    c[4].caption(f"滞销不可用:{e}")

wm = data_watermarks()
n_lag = sum(1 for _, _, lag in wm if lag is not None and lag > 7)
c[5].metric("🕐 数据滞后表", f"{n_lag}", delta="目标 0", delta_color=("inverse" if n_lag else "off"),
            help="水位落后 7 天以上的表数(详见上周成果区)")

# 🎯 目标设置(admin)
if is_admin():
    with st.expander("🎯 目标设置(admin)"):
        st.caption("目标=0 视为未设定。产品均衡按季度(周期=2026Q3/2026Q4),专项按「月度」,滞销按「2026年末」。可增行。")
        edited = st.data_editor(tdf, num_rows="dynamic", use_container_width=True, key="kpi_target_editor")
        if st.button("💾 保存目标", key="save_targets"):
            conn = _conn()
            conn.execute("DELETE FROM pm_kpi_target")
            for _, r in edited.iterrows():
                if pd.notna(r['指标']) and pd.notna(r['周期']):
                    conn.execute("INSERT OR REPLACE INTO pm_kpi_target(指标, 周期, 目标) VALUES (?,?,?)",
                                 (str(r['指标']), str(r['周期']),
                                  float(r['目标']) if pd.notna(r['目标']) else None))
            conn.commit()
            conn.close()
            load_targets.clear()
            st.success("已保存")
            st.rerun()

# 🔍 三大专项本月下钻:分销经理 → 代理商 → 服务商
with st.expander("🔍 三大专项本月下钻(分销经理 → 代理商 → 服务商)"):
    try:
        dr = special_drill(today.replace(day=1), today)
        t1, t2, t3 = st.tabs(["按分销经理", "按代理商", "按服务商"])
        with t1:
            pv = dr.pivot_table(index='分销经理', columns='专项', values='台数',
                                aggfunc='sum', fill_value=0)
            pv['合计'] = pv.sum(axis=1)
            st.dataframe(pv.sort_values('合计', ascending=False), use_container_width=True)
            st.caption("分销经理 = 代理商的客户所有者(SI 名册);(未映射)= 出库代理商不在 SI 名册。")
        with t2:
            pv = dr.pivot_table(index=['分销经理', '代理商'], columns='专项', values='台数',
                                aggfunc='sum', fill_value=0)
            pv['合计'] = pv.sum(axis=1)
            st.dataframe(pv.sort_values('合计', ascending=False).head(40), use_container_width=True)
        with t3:
            drp = special_drill_provider(today.replace(day=1), today)
            st.dataframe(drp.sort_values('台数', ascending=False).head(40),
                         hide_index=True, use_container_width=True)
            st.caption("服务商维度=红包口径(上线客户名称,物料号判专项),与前两个 tab 的"
                       "SO 主口径(product_flow)总数略有出入,用于定位'哪个服务商在卖专项'。")
    except Exception as e:
        st.warning(f"下钻不可用:{e}")

st.divider()

# ══════════ 2. 上周成果 ══════════
st.markdown("#### ✅ 上周成果(自动统计)")
g1, g2 = st.columns(2)

with g1:
    st.markdown("**三大专项 SO(上周 vs 前一周)**")
    try:
        lw = so_by_special(last_week_start, last_week_end)
        pw = so_by_special(prev_week_start, last_week_start - dt.timedelta(days=1))
        rows = []
        for zx in ZHUANXIANG:
            a, b = lw.get(zx, 0), pw.get(zx, 0)
            rows.append({'专项': zx.replace('专项', ''), '上周SO': a, '前一周': b,
                         '环比': (f"{(a - b) / b * 100:+.0f}%" if b else '—')})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        with st.expander("下钻:上周专项 SO 落到谁"):
            dr_lw = special_drill(last_week_start, last_week_end)
            pv = dr_lw.pivot_table(index=['分销经理', '代理商'], columns='专项',
                                   values='台数', aggfunc='sum', fill_value=0)
            pv['合计'] = pv.sum(axis=1)
            st.dataframe(pv.sort_values('合计', ascending=False).head(30), use_container_width=True)
    except Exception as e:
        st.warning(f"不可用:{e}")

    st.markdown("**产品均衡变化(最新快照 vs 上一期)**")
    if bal_df is not None and not bal_df.empty and len(snaps) >= 2:
        new_ok = ok_now - ok_prev
        lost = ok_prev - ok_now
        st.write(f"达标 {len(ok_prev)} → {len(ok_now)} 家")
        if new_ok:
            st.success("新增达标:" + "、".join(sorted(new_ok)))
        if lost:
            st.error("跌出达标:" + "、".join(sorted(lost)))
    else:
        st.caption("暂只有 1 期 RP10 快照,导入下一期后自动对比。")

with g2:
    st.markdown("**数据水位(上周导数结果)**")
    wm_df = pd.DataFrame(wm, columns=['表', '最新数据', '滞后天数'])
    wm_df['状态'] = wm_df['滞后天数'].map(
        lambda x: '🟢' if (x is not None and x <= 7) else ('🔴 需导' if x is not None else '—'))
    st.dataframe(wm_df, hide_index=True, use_container_width=True)

    st.markdown("**上周任务完成**")
    try:
        conn = _conn()
        _ensure_tables(conn)
        rows = conn.execute("SELECT 任务, 完成 FROM pm_task_check WHERE 周=?",
                            (str(last_week_start),)).fetchall()
        conn.close()
        if rows:
            done = [t for t, d in rows if d]
            undone = [t for t, d in rows if not d]
            st.write(f"完成 **{len(done)}/{len(rows)}**")
            if undone:
                st.warning("未完成:" + " / ".join(undone))
        else:
            st.caption("上周无任务打卡记录(本周开始使用下方清单即可累计)。")
    except Exception as e:
        st.warning(f"不可用:{e}")

st.divider()

# ══════════ 3. 本周工作清单(自动生成 + 打勾留痕 + 下钻到人) ══════════
st.markdown("#### 📋 本周工作清单")
owner_map = {}
try:
    owner_map = owner_by_name()
except Exception:
    pass

tasks = []   # (task_id, 标题, 说明, 下钻渲染函数 or None)

# a. 数据导入
lag_tables = [(n, v, lag) for n, v, lag in wm if lag is not None and lag > 7]
if lag_tables:
    names = "、".join(n.split('(')[0] for n, _, _ in lag_tables)
    tasks.append(('import', f"周一导数:{names} 已滞后 7 天+",
                  "data-import 页智能批量导入;注意预览里的🚨警告", None))
else:
    tasks.append(('import', "周一导数:各表水位正常,导本周增量即可", "data-import 页", None))

# b. 产品均衡差一口气名单(下钻:代理商+分销经理+差多少)
near_df = None
if bal_df is not None and not bal_df.empty and cur_snap:
    cur = bal_df[bal_df['数据时点'].astype(str) == cur_snap]
    near_df = cur[(~cur['_ok']) & (cur['_cctv'].notna()) & (cur['_cctv'] >= TH_CCTV)
                  & ((cur['_st'] > 0.015) | (cur['_pt'] > 0.06))]

    def _near_drill():
        show = near_df[['分销经理', '客户名称', '客户城市']].copy()
        show['CCTV占比'] = near_df['_cctv'].map(lambda v: f"{v*100:.1f}%")
        show['数通占比'] = near_df['_st'].map(lambda v: f"{(v or 0)*100:.2f}%")
        show['数通缺口'] = near_df.apply(
            lambda r: (f"再做 {max(0.0, (TH_SHUTONG - (r['_st'] or 0))) * float(r['实销金额']):.1f} 万数通"
                       if pd.notna(r['实销金额']) else '—'), axis=1)
        show['配套占比'] = near_df['_pt'].map(lambda v: f"{(v or 0)*100:.1f}%")
        show['配套缺口'] = near_df.apply(
            lambda r: (f"再做 {max(0.0, (TH_PEITAO - (r['_pt'] or 0))) * float(r['实销金额']):.1f} 万配套"
                       if pd.notna(r['实销金额']) else '—'), axis=1)
        show['实销_万'] = pd.to_numeric(near_df['实销金额'], errors='coerce').round(1)
        st.dataframe(show.sort_values(['分销经理', '实销_万'], ascending=[True, False]),
                     hide_index=True, use_container_width=True)
        st.caption("缺口=按当前实销体量推算,补足数通或配套任一项即达标(CCTV 已达标前提下)。"
                   "按分销经理分组,直接派任务。")

    if len(near_df):
        tasks.append(('balance_near',
                      f"跟进产品均衡「差一口气」代理商 {len(near_df)} 家(涉及 {near_df['分销经理'].nunique()} 名分销经理)",
                      "CCTV 已达标,数通/配套差一点;下钻看每家找哪个分销经理、缺口多少万", _near_drill))

# c. 滞销 TOP(下钻:代理商×型号+分销经理)
if stale_alerts_df is not None and stale_val_col and len(stale_alerts_df):
    top_stale = (stale_alerts_df.groupby('代理商')[stale_val_col].sum()
                 .sort_values(ascending=False).head(5) / 10000).round(1)

    def _stale_drill():
        det_cols = [cc for cc in ['代理商', '内部型号', '外部型号', '现存台数', stale_val_col,
                                  '动销90', '专项', '呆滞清单'] if cc in stale_alerts_df.columns]
        det = stale_alerts_df[stale_alerts_df['代理商'].isin(top_stale.index)][det_cols].copy()
        det.insert(0, '分销经理', det['代理商'].astype(str).str.strip().map(owner_map).fillna('(未映射)'))
        st.dataframe(det.sort_values(stale_val_col, ascending=False).head(40),
                     hide_index=True, use_container_width=True)
        st.caption("TOP5 滞销代理商的型号级明细;出差/电话前先看 page40 该代理商完整矩阵。")

    tasks.append(('stale_top', "联系滞销 TOP5 代理商推清库存方案",
                  " · ".join(f"{k} {v}万" for k, v in top_stale.items()), _stale_drill))

# d. GTM(下钻:分销经理×状态,服务商级明细)
gtm_month, gtm_n = gtm_status()
if gtm_n is None:
    tasks.append(('gtm_gen', f"生成 {gtm_month} GTM 攻坚名单", "page39 🚀 GTM 管理 → 生成推荐名单", None))
else:
    def _gtm_drill():
        try:
            board = gtm_board_cached(gtm_month)
            agg = board.groupby('分销经理').agg(
                目标家数=('客户编码', 'count'),
                待跑动=('状态', lambda s: int((s == '⏳ 待跑动').sum())),
                已跑动待动作=('状态', lambda s: int((s == '🚶 已跑动·待动作').sum())),
                GTM完成=('GTM完成', 'sum'),
            ).reset_index().sort_values('待跑动', ascending=False)
            st.dataframe(agg, hide_index=True, use_container_width=True)
            pend = board[board['状态'] != '✅ GTM完成']
            pcols = [cc for cc in ['分销经理', '客户名称', '所属代理商', '城市', '区县',
                                   '推荐产品类', '状态'] if cc in pend.columns]
            st.markdown("**未完成明细(服务商级)**")
            st.dataframe(pend[pcols].head(50), hide_index=True, use_container_width=True)
        except Exception as e:
            st.warning(f"GTM 看板不可用:{e}")

    tasks.append(('gtm_follow', f"跟进本月 GTM 目标池 {gtm_n} 家的跑动与动作",
                  "下钻看每个分销经理还欠几家、具体哪家服务商", _gtm_drill))

# e. 日历节点
if today.day <= 7:
    tasks.append(('attribution', "月度归因复盘:上月哪些产品涨/跌、为什么",
                  "page38 🧩 产品归因分析,输出行动清单", None))
if today.month in (3, 6, 9, 12) and today.day >= 15:
    tasks.append(('rp10_quarter', "⚠️ 季度末:导 RP10 快照(产品均衡结算依据,红线)",
                  "季度结束前务必导入最新 RP10 文件", None))
tasks.append(('weekly_report', "周五:输出一页纸周报(KPI 读数+动作挂结果)", "模板见工作指引", None))

# 渲染 + 持久化
conn = _conn()
_ensure_tables(conn)
saved = {r[0]: r[1] for r in conn.execute(
    "SELECT 任务ID, 完成 FROM pm_task_check WHERE 周=?", (str(week_start),)).fetchall()}
for tid, title, note, drill in tasks:
    cols = st.columns([0.06, 0.94])
    checked = cols[0].checkbox(" ", value=bool(saved.get(tid, 0)),
                               key=f"task_{week_start}_{tid}", label_visibility="collapsed")
    cols[1].markdown(f"**{title}**  \n<small>{note}</small>", unsafe_allow_html=True)
    if drill is not None:
        with st.expander(f"　└ 🔍 下钻:{title[:24]}…" if len(title) > 24 else f"　└ 🔍 下钻:{title}"):
            drill()
    if bool(saved.get(tid, 0)) != checked:
        conn.execute("""
            INSERT INTO pm_task_check(周, 任务ID, 任务, 完成, 完成时间, 操作人)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(周, 任务ID) DO UPDATE SET 完成=excluded.完成, 完成时间=excluded.完成时间, 操作人=excluded.操作人
        """, (str(week_start), tid, title, int(checked),
              dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S') if checked else None, current_user()))
        conn.commit()
conn.close()

st.caption("清单由数据+日历自动生成,打勾留痕,下周自动回看完成率。每项下钻均落到"
           "分销经理→代理商→服务商。KPI 口径:产品均衡同 page41 / 专项=product_flow.三大重点专项 / 滞销同 page40。")
