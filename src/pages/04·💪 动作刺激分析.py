#!/usr/bin/env python3
"""
💪 动作刺激分析（哪类动作对上线刺激最强？）
- 独立 page
- 6 类动作：大华拜访 / 代理商拜访 / 红包中奖 / 大红包 / 签约 / 激活
- 事件研究法：每个服务商首次发生该动作的时点 t0，对比 t0 前后 N 天的上线台数
"""

import io
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import (  # noqa: E402
    load_redpack_shared, load_visit_shared, load_contract_shared,
)

require_auth()
st.markdown("### 💪 动作刺激分析（哪类动作对上线刺激最强？）")
st.caption(
    "对每类「**业务动作**」做事件研究：每个服务商首次发生该动作的时点 t0，"
    "对比 [t0-N天, t0) 和 (t0, t0+N天] 的上线台数。"
    "**输出**：每类动作的平均提振 + 投资回报排行，告诉你哪个动作最值得做。"
)

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.1rem !important; line-height: 1.1; }
[data-testid="stMetricLabel"] { font-size: 0.78rem !important; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────

rp_act = load_redpack_shared()
visits_act = load_visit_shared()
contract_act = load_contract_shared()

st.markdown("#### 📦 数据状态")
ds = st.columns(3)
ds[0].metric("红包/上线记录", f"{len(rp_act):,}", help="install_redpack")
ds[1].metric("拜访记录", f"{len(visits_act):,}", help="visit_record")
ds[2].metric("签约/激活记录", f"{len(contract_act):,}", help="provider_contract")

if rp_act.empty:
    st.warning("📦 红包表为空，无法计算上线响应。请先到主页『📥 数据导入』上传安装红包记录。")
    st.stop()


# ──────────────────────────────────────────
# 参数
# ──────────────────────────────────────────

st.markdown("#### ⚙️ 参数")
ap = st.columns([1, 1, 3])
with ap[0]:
    act_window = st.number_input(
        "对比窗口（天）",
        value=30, min_value=7, max_value=90,
        key="act_window",
        help="动作前 N 天 vs 动作后 N 天",
    )
with ap[1]:
    act_min_post_days = st.number_input(
        "最小数据要求（天）",
        value=7, min_value=1, max_value=30,
        key="act_min_post",
        help="动作时点必须至少在数据末尾前 N 天才算入分析（保证有完整 post 窗口）",
    )

run_action = st.button("🚀 计算动作刺激", type="primary", key="act_run_btn", use_container_width=True)

if not run_action:
    st.info("👆 点上方「🚀 计算动作刺激」按钮启动分析。首次约 10-15 秒。")
    st.stop()


# ──────────────────────────────────────────
# 流式计算
# ──────────────────────────────────────────

with st.status("📊 多动作事件研究中…", expanded=True) as status:
    t_total = time.time()

    # ── 步骤 1：上线时间序列字典 ──
    st.write("**步骤 1/3**：构建上线时间序列索引…")
    t = time.time()
    rp_act = rp_act.copy()
    rp_act['_code'] = rp_act['上线客户编码'].astype(str).str.strip()
    rp_act['_t'] = pd.to_datetime(rp_act['上线时间'], errors='coerce')
    rec_clean = rp_act.dropna(subset=['_code', '_t'])
    code_to_times = {}
    for code, sub in rec_clean.groupby('_code'):
        times = sub['_t'].sort_values().to_numpy(dtype='datetime64[ns]')
        code_to_times[code] = times
    anchor = rec_clean['_t'].max()
    st.write(f"&nbsp;&nbsp;✅ {len(code_to_times):,} 个服务商建立时间序列 · {time.time() - t:.1f}s")

    # ── 步骤 2：收集 6 类动作 ──
    st.write("**步骤 2/3**：收集 6 类动作事件（首次事件作为锚点）…")
    t = time.time()
    actions = {}

    # 1. 大华业务员拜访
    if not visits_act.empty:
        v_dh = visits_act[visits_act['_打卡方'] == '🏢 大华'].copy()
        v_dh['_code'] = v_dh['客户编码'].astype(str).str.strip()
        v_dh['_t'] = pd.to_datetime(v_dh['拜访时间'], errors='coerce')
        v_dh = v_dh.dropna(subset=['_code', '_t'])
        if not v_dh.empty:
            first_dh = v_dh.sort_values('_t').drop_duplicates(subset='_code', keep='first')
            actions['🏢 大华业务员拜访（首次）'] = first_dh[['_code', '_t']].rename(columns={'_t': 't0'})

    # 2. 代理商业务员拜访
    if not visits_act.empty:
        v_dl = visits_act[visits_act['_打卡方'] == '🏪 代理商'].copy()
        v_dl['_code'] = v_dl['客户编码'].astype(str).str.strip()
        v_dl['_t'] = pd.to_datetime(v_dl['拜访时间'], errors='coerce')
        v_dl = v_dl.dropna(subset=['_code', '_t'])
        if not v_dl.empty:
            first_dl = v_dl.sort_values('_t').drop_duplicates(subset='_code', keep='first')
            actions['🏪 代理商业务员拜访（首次）'] = first_dl[['_code', '_t']].rename(columns={'_t': 't0'})

    # 3. 红包中奖（首次）
    rp_won = rp_act[(pd.to_numeric(rp_act['中奖金额'], errors='coerce').fillna(0) > 0)].copy()
    rp_won['_code'] = rp_won['上线客户编码'].astype(str).str.strip()
    rp_won['_t'] = pd.to_datetime(
        rp_won.get('抽奖机会发放时间'), errors='coerce'
    ).fillna(pd.to_datetime(rp_won['上线时间'], errors='coerce'))
    rp_won = rp_won.dropna(subset=['_code', '_t'])
    if not rp_won.empty:
        first_red = rp_won.sort_values('_t').drop_duplicates(subset='_code', keep='first')
        actions['🎁 红包中奖（首次）'] = first_red[['_code', '_t']].rename(columns={'_t': 't0'})

    # 4. 大红包（≥¥1k）首次
    rp_big = rp_won[pd.to_numeric(rp_won['中奖金额'], errors='coerce').fillna(0) >= 1000].copy()
    if not rp_big.empty:
        first_big = rp_big.sort_values('_t').drop_duplicates(subset='_code', keep='first')
        actions['🎁 大红包（≥¥1k）首次'] = first_big[['_code', '_t']].rename(columns={'_t': 't0'})

    # 5. 签约
    if not contract_act.empty and '签约日期' in contract_act.columns:
        c_sign = contract_act[['客户编码', '签约日期']].copy()
        c_sign['_code'] = c_sign['客户编码'].astype(str).str.strip()
        c_sign['_t'] = pd.to_datetime(c_sign['签约日期'], errors='coerce')
        c_sign = c_sign.dropna(subset=['_code', '_t'])
        if not c_sign.empty:
            actions['📝 签约'] = c_sign[['_code', '_t']].rename(columns={'_t': 't0'})

    # 6. 激活
    if not contract_act.empty and '激活时间' in contract_act.columns:
        c_act = contract_act[['客户编码', '激活时间']].copy()
        c_act['_code'] = c_act['客户编码'].astype(str).str.strip()
        c_act['_t'] = pd.to_datetime(c_act['激活时间'], errors='coerce')
        c_act = c_act.dropna(subset=['_code', '_t'])
        if not c_act.empty:
            actions['🚀 激活'] = c_act[['_code', '_t']].rename(columns={'_t': 't0'})

    st.write(f"&nbsp;&nbsp;✅ 收集 {len(actions)} 类动作 · {time.time() - t:.1f}s")

    # ── 步骤 3：事件研究 ──
    st.write(f"**步骤 3/3**：事件研究（窗口 ±{act_window} 天，numpy.searchsorted 高速版）…")
    t = time.time()
    cutoff_for_post = anchor - pd.Timedelta(days=int(act_min_post_days))
    window_ns = np.timedelta64(int(act_window), 'D')

    summary_rows = []
    detail_rows = {}
    for action_name, df_act in actions.items():
        pre_list = []
        post_list = []
        code_pre_post = []
        for code, t0_ts in zip(df_act['_code'], df_act['t0']):
            if pd.isna(t0_ts) or t0_ts > cutoff_for_post:
                continue
            times = code_to_times.get(code)
            if times is None or len(times) == 0:
                continue
            t0_np = np.datetime64(t0_ts.to_datetime64() if hasattr(t0_ts, 'to_datetime64') else t0_ts, 'ns')
            pre_start = t0_np - window_ns
            post_end = t0_np + window_ns
            i_pre_start = np.searchsorted(times, pre_start, side='left')
            i_t0_left = np.searchsorted(times, t0_np, side='left')
            n_pre = int(i_t0_left - i_pre_start)
            i_t0_right = np.searchsorted(times, t0_np, side='right')
            i_post_end = np.searchsorted(times, post_end, side='right')
            n_post = int(i_post_end - i_t0_right)

            pre_list.append(n_pre)
            post_list.append(n_post)
            code_pre_post.append((code, n_pre, n_post))

        if not pre_list:
            continue

        pre_arr = np.array(pre_list)
        post_arr = np.array(post_list)
        lift_arr = post_arr - pre_arr

        summary_rows.append({
            '动作类型': action_name,
            '事件数': len(pre_list),
            '涉及服务商': len(set(c for c, _, _ in code_pre_post)),
            '前 N 天均上线': round(float(pre_arr.mean()), 1),
            '后 N 天均上线': round(float(post_arr.mean()), 1),
            '平均提振（台）': round(float(lift_arr.mean()), 1),
            '中位提振（台）': float(np.median(lift_arr)),
            '提振率': f"{(lift_arr > 0).mean() * 100:.0f}%",
            '↑提升占比': float((lift_arr > 0).mean()),
            '↓下降占比': float((lift_arr < 0).mean()),
            '➡️持平占比': float((lift_arr == 0).mean()),
        })
        detail_rows[action_name] = code_pre_post

    st.write(f"&nbsp;&nbsp;✅ {len(summary_rows)} 类动作算完 · {time.time() - t:.1f}s")

    status.update(
        label=f"✅ 完成 · 总耗时 {time.time() - t_total:.1f}s",
        state="complete",
        expanded=False,
    )


# ──────────────────────────────────────────
# 渲染结果
# ──────────────────────────────────────────

if not summary_rows:
    st.info("没有足够数据计算（可能各类动作事件都不够）")
    st.stop()

summary_df = pd.DataFrame(summary_rows).sort_values('平均提振（台）', ascending=False)

# 顶部 4 个 metric — 关键 KPI 节点
st.markdown("#### 🏆 总览：6 类动作平均提振")
top_action = summary_df.iloc[0]
mk = st.columns(4)
mk[0].metric(
    "🥇 最强动作",
    top_action['动作类型'].split('（')[0].split(' ')[1] if ' ' in top_action['动作类型'] else top_action['动作类型'],
    delta=f"+{top_action['平均提振（台）']:.1f} 台",
)
mk[1].metric(
    "覆盖服务商",
    f"{top_action['涉及服务商']:,}",
    help=f"涉及 {top_action['事件数']:,} 个事件",
)
mk[2].metric(
    "动作后均上线",
    f"{top_action['后 N 天均上线']:.1f} 台",
    delta=f"vs 前 {top_action['前 N 天均上线']:.1f}",
)
mk[3].metric(
    "提振率",
    top_action['提振率'],
    help="该动作后上线增加的服务商占比",
)

st.success(
    f"🏆 **{top_action['动作类型']}** 刺激最强：平均每个服务商在动作后 {act_window} 天比前 {act_window} 天"
    f"多上线 **{top_action['平均提振（台）']:.1f} 台**，"
    f"覆盖 {top_action['涉及服务商']:,} 个服务商，"
    f"{top_action['提振率']} 的服务商上线增加。"
)

st.divider()


# ── 排行表 ──
st.markdown("#### 📊 动作刺激排行（按平均提振）")
show_cols = ['动作类型', '事件数', '涉及服务商', '前 N 天均上线', '后 N 天均上线',
              '平均提振（台）', '中位提振（台）', '提振率']
st.dataframe(
    summary_df[show_cols],
    use_container_width=True, hide_index=True,
)

# 排行图
col_chart_1, col_chart_2 = st.columns(2)
with col_chart_1:
    st.markdown("##### 平均提振（台）")
    chart_df = summary_df.set_index('动作类型')[['平均提振（台）']]
    st.bar_chart(chart_df)

with col_chart_2:
    st.markdown("##### 提升 / 持平 / 下降 占比")
    stack_df = summary_df.set_index('动作类型')[['↑提升占比', '➡️持平占比', '↓下降占比']]
    st.bar_chart(stack_df)


st.divider()


# ── 服务商级明细（可下钻看每个动作的服务商）──
st.markdown("#### 🔬 动作明细（每个服务商的提振分布）")
picked_action = st.selectbox(
    "选一类动作看服务商级别明细",
    options=list(detail_rows.keys()),
    key="act_detail_pick",
)
if picked_action:
    rows = detail_rows[picked_action]
    detail_df = pd.DataFrame(rows, columns=['客户编码', '前N天台数', '后N天台数'])
    detail_df['提振'] = detail_df['后N天台数'] - detail_df['前N天台数']
    detail_df = detail_df.sort_values('提振', ascending=False)

    cd = st.columns(3)
    cd[0].metric("✅ 提振 > 0", f"{(detail_df['提振'] > 0).sum():,}")
    cd[1].metric("➡️ 持平", f"{(detail_df['提振'] == 0).sum():,}")
    cd[2].metric("❌ 提振 < 0", f"{(detail_df['提振'] < 0).sum():,}")

    st.dataframe(
        detail_df.head(200),
        use_container_width=True, hide_index=True,
        height=min(60 + len(detail_df) * 36, 400),
    )


st.divider()


# ── 一键导出 ──
buf_act = io.BytesIO()
with pd.ExcelWriter(buf_act, engine='openpyxl') as w:
    summary_df[show_cols].to_excel(w, sheet_name='动作刺激排行', index=False)
    for action_name, rows in detail_rows.items():
        d = pd.DataFrame(rows, columns=['客户编码', '前N天台数', '后N天台数'])
        d['提振'] = d['后N天台数'] - d['前N天台数']
        d = d.sort_values('提振', ascending=False)
        safe_name = ''.join(ch for ch in action_name if ch.isalnum() or ch in '_-')[:30] or 'detail'
        d.to_excel(w, sheet_name=safe_name, index=False)

stamp_act = datetime.now().strftime('%Y%m%d')
st.download_button(
    f"📥 导出动作刺激分析（含每动作服务商级明细）",
    data=buf_act.getvalue(),
    file_name=f"动作刺激排行_{stamp_act}.xlsx",
    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    key='act_dl',
)

st.caption(
    "💡 **管理用途**：① 把排行交给销售总监 — 决定营销资源往哪个动作倾斜；"
    "② 提振率低的动作（< 30%）需要审视：是不是动作本身没价值，还是执行没到位；"
    "③ 拜访 vs 红包对比：如果红包提振 >> 拜访 — 业务员的「水卡」性价比就低了。"
)


# ══════════════════════════════════════════════
# V2 → V3 衔接
# ══════════════════════════════════════════════
from _ai_handoff import render_ai_followup_button as _render_ai_followup_button  # noqa: E402

st.divider()
st.markdown('#### 💬 想就动作 ROI 继续深挖？')
_render_ai_followup_button(
    source_page='04·💪 动作刺激分析',
    context_summary=(
        '用户刚浏览各类动作（拜访 / 红包 / ...）的服务商提振率排行\n'
        '提振率定义：动作发生后 30 天内服务商货值环比变化 > X%'
    ),
    suggested_followups=[
        '对比红包提振率 vs 拜访提振率，按服务商等级分组',
        '画一张红包发放后 30 天的上线增量曲线（事件研究图）',
        '哪些区域的拜访提振率明显低于平均（找问题区域）',
        '把"低提振率"动作和具体业务员关联，找执行差的人',
    ],
    key_suffix='page04_end',
)
