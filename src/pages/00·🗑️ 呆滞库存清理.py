#!/usr/bin/env python3
"""🗑️ 呆滞库存清理专项（admin only）

代理商仓库 25 年初盘出的呆滞库存（多为 2022/2023 年出库），盯紧清理进度。
- 进度看板：总览 + 月度趋势 + 代理商/型号/年份下钻 + 本期清理清单
- 数据源：stale_inventory（每期盘库快照入库；上线=已清理）
- 进度跟踪靠每期上传新盘库清单更新（系统序列号匹配率低，不走自动）
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin  # noqa: E402
from _loaders import DB_PATH  # noqa: E402
import sqlite3  # noqa: E402

st.set_page_config(page_title='呆滞库存清理专项', layout='wide')
require_auth()
if not is_admin():
    st.error("🔒 此页仅管理员（admin）可访问")
    st.stop()

st.markdown("### 🗑️ 呆滞库存清理专项")
st.caption("代理商仓库 25 年初盘出的呆滞库存（多为 2022/2023 年出库）。**上线 = 已清理**。盯紧每期清理进度。")

# 顶部 metric 紧凑
st.markdown("""<style>
[data-testid="stMetricValue"]{font-size:1.1rem!important;}
[data-testid="stMetricLabel"]{font-size:0.78rem!important;}
</style>""", unsafe_allow_html=True)


@st.cache_data(ttl=300)
def load_stale():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return pd.read_sql("SELECT * FROM stale_inventory", conn)
    finally:
        conn.close()


df = load_stale()
if df.empty:
    st.warning("📦 暂无呆滞库存数据，请先把盘库清单入库到 stale_inventory。")
    st.stop()

df['_done'] = df['是否上线'] == 'Y'
df['下单时单价'] = pd.to_numeric(df['下单时单价'], errors='coerce').fillna(0)
df['_undone_amt'] = df['下单时单价'].where(~df['_done'], 0)

# ════════ 总览 ════════
total = len(df)
done = int(df['_done'].sum())
undone = total - done
amt_total = df['下单时单价'].sum()
amt_done = df.loc[df['_done'], '下单时单价'].sum()
amt_undone = amt_total - amt_done
done_months = df.loc[df['_done'], '上线年月'].dropna()
latest_m = done_months.max() if not done_months.empty else None
latest_done = int((df['上线年月'] == latest_m).sum()) if latest_m else 0

st.markdown("#### 📊 总览")
c = st.columns(5)
c[0].metric("呆滞总量", f"{total:,} 台", help=f"总货值 {amt_total/1e4:.0f} 万")
c[1].metric("已清理", f"{done:,} 台", f"{done/total*100:.0f}%")
c[2].metric("未清理", f"{undone:,} 台", help=f"待清货值 {amt_undone/1e4:.0f} 万")
c[3].metric("已清理货值", f"{amt_done/1e4:.0f} 万")
c[4].metric(f"最新月清理（{latest_m or '—'}）", f"{latest_done:,} 台")
st.progress(done / total, text=f"整体清理进度 {done/total*100:.1f}%（{done:,} / {total:,}）")

# ════════ 月度趋势 ════════
st.markdown("#### 📈 月度清理趋势")
trend = (df[df['_done']].groupby('上线年月').size()
         .reset_index(name='清理台数').sort_values('上线年月'))
if not trend.empty:
    st.bar_chart(trend.set_index('上线年月')['清理台数'], height=240)

# ════════ 下钻 tabs ════════
t1, t2, t3, t4 = st.tabs(["🏢 代理商进度", "📦 型号攻坚", "📅 出库年份", "🗓️ 本期清理清单"])

with t1:
    g = df.groupby('代理商').agg(
        呆滞总量=('序列号', 'count'),
        已清理=('_done', 'sum'),
        剩余货值万=('_undone_amt', lambda s: round(s.sum() / 1e4, 1)),
    ).reset_index()
    g['已清理'] = g['已清理'].astype(int)
    g['未清理'] = g['呆滞总量'] - g['已清理']
    g['清理率'] = (g['已清理'] / g['呆滞总量'] * 100).round(0).astype(int).astype(str) + '%'
    g = g[['代理商', '呆滞总量', '已清理', '未清理', '清理率', '剩余货值万']].sort_values('未清理', ascending=False)
    st.caption("按未清理台数降序 —— 重点盯前几家、以及清理率低的")
    st.dataframe(g, use_container_width=True, hide_index=True, height=440)

with t2:
    und = df[~df['_done']]
    m = und.groupby('内部型号').agg(
        剩余台数=('序列号', 'count'),
        剩余货值万=('下单时单价', lambda s: round(s.sum() / 1e4, 1)),
        主出库年份=('出库年份', lambda s: int(s.mode().iloc[0]) if not s.mode().dropna().empty else None),
    ).reset_index().sort_values('剩余台数', ascending=False).head(30)
    st.caption("未清理型号 Top30 —— 改造（换镜头/换POE）或甩卖政策的攻坚对象")
    st.dataframe(m, use_container_width=True, hide_index=True, height=440)

with t3:
    y = df.groupby('出库年份').agg(
        总量=('序列号', 'count'), 已清理=('_done', 'sum'),
    ).reset_index()
    y['已清理'] = y['已清理'].astype(int)
    y['未清理'] = y['总量'] - y['已清理']
    y['清理率'] = (y['已清理'] / y['总量'] * 100).round(0).astype(int).astype(str) + '%'
    y = y.sort_values('出库年份')
    st.caption("越老的货往往越难清 —— 盯清理率低的年份")
    cc = st.columns([3, 4])
    cc[0].dataframe(y[['出库年份', '总量', '已清理', '未清理', '清理率']], use_container_width=True, hide_index=True, height=320)
    cc[1].bar_chart(y.set_index('出库年份')[['已清理', '未清理']], height=320)

with t4:
    months = sorted(df.loc[df['_done'], '上线年月'].dropna().unique(), reverse=True)
    if months:
        pick = st.selectbox("选清理月份", months, index=0)
        cur = df[df['上线年月'] == pick]
        st.caption(f"**{pick}** 清理 {len(cur):,} 台 / {cur['下单时单价'].sum()/1e4:.1f} 万")
        cg = cur.groupby('代理商').agg(
            清理台数=('序列号', 'count'),
            货值万=('下单时单价', lambda s: round(s.sum() / 1e4, 1)),
        ).reset_index().sort_values('清理台数', ascending=False)
        st.markdown("**本期各代理商清理：**")
        st.dataframe(cg, use_container_width=True, hide_index=True)
        with st.expander(f"📋 {pick} 清理明细（代理商 × 型号 × 序列号）"):
            st.dataframe(
                cur[['代理商', '内部型号', '序列号', '出库年份', '下单时单价', '上线时间']],
                use_container_width=True, hide_index=True, height=320)
    else:
        st.info("暂无已清理记录")

st.divider()

# ════════ 周报导出（Word） ════════
st.markdown("#### 📄 生成周报（Word）")
st.caption("系统出标准化数据（整体进度 + 各代理商进度条 + 本期清理产品 + 攻坚型号）＋ 你填一段综述 → 合成 docx。")
rc = st.columns([1, 2])
with rc[0]:
    rep_months = sorted(df.loc[df['_done'], '上线年月'].dropna().unique(), reverse=True)
    rep_period = st.selectbox("本期清理月份", rep_months, index=0, key='rep_period') if rep_months else None
with rc[1]:
    user_note = st.text_area(
        "本期综述 / 点评 / 下周计划（写进周报开头，可留空）",
        height=120, key='rep_note',
        placeholder="例如：本周重点推进鼎点、鼎博两家 2022 年呆滞清理；已和万仞敲定 HDW1235C 换镜头改造方案，下周落地…",
    )
if rep_period and st.button("📄 生成周报 docx", type='primary', key='gen_rep'):
    from _stale_report import build_stale_report_docx
    try:
        data = build_stale_report_docx(df, rep_period, user_note)
        st.download_button(
            "⬇️ 下载周报", data,
            file_name=f"呆滞库存清理周报_{rep_period}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key='dl_rep',
        )
        st.success("✅ 周报已生成，点上方按钮下载")
    except Exception as e:
        st.error(f"生成失败：{e}")

st.divider()
st.caption("💡 数据源 `stale_inventory`（5月底盘库快照）。进度靠每期上传新盘库清单更新。**型号政策标注**下一版加。")
