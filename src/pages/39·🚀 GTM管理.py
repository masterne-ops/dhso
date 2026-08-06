#!/usr/bin/env python3
"""🚀 GTM 管理(仅 admin)— 月度产品攻坚:名单 → 动作跟踪 → SO 成效 → 考核

三条客户任务线之三(RFM派单/待激活/GTM)。GTM 对象 = 可能产单、能卖
夜视王/无线/场景化(product_focus 白名单口径)的服务商。
  Tab1 名单:系统推荐(机会+培育) + 手动调整 → 入库(每人≈10家)
  Tab2 跟进:任务月内跑动 + 拜访后7天内 推广会/铺货/红包/上线(事实表实时算)
  Tab3 成效:GTM完成对象自首访日起 90 天 SO 透视(30/60/90 分段+专项拆分)
  Tab4 考核:分销经理级漏斗(名单→跑动→动作→完成→SO)
"""
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin, current_user, get_current_role  # noqa: E402
from _gtm import (  # noqa: E402
    GTM_FOCUS, generate_candidates, get_targets, list_target_months,
    bulk_insert_targets, delete_target, clear_month, search_providers,
    get_gtm_board, get_so_effect, get_assessment, list_managers,
    get_rollover_candidates, exclude_customer, unexclude_customer, list_excluded,
)

require_auth()
if not is_admin():
    st.error(f"⛔ GTM 管理仅 admin 可用。当前:`{current_user()}` / `{get_current_role()}`")
    st.stop()

st.markdown("### 🚀 GTM 管理")
st.caption(
    "月度产品攻坚(夜视王/无线/场景化,product_focus 口径)。"
    "**完成 = 名单下发后、任务月内的大华侧有效跑动 + 拜访后 7 天内 ≥1 个动作**"
    "(推广会签到/铺货/中红包/上线设备;拜访剔除打卡异常与可疑打卡,代理商侧拜访不计跑动);"
    "完成对象观察自首访日起 **90 天 SO**(install_redpack 服务商级口径)。"
)

cur_month = datetime.now().strftime('%Y-%m')
months = list_target_months()
if cur_month not in months:
    months = [cur_month] + months


@st.cache_data(ttl=120, show_spinner="📊 计算 GTM 跟进看板…")
def _cached_board(ym):
    return get_gtm_board(ym)


@st.cache_data(ttl=300, show_spinner="📈 计算 SO 成效…")
def _cached_so(ym):
    return get_so_effect(ym)


@st.cache_data(ttl=120, show_spinner="🏅 计算考核…")
def _cached_ass(ym):
    return get_assessment(ym)


def _clear_caches():
    _cached_board.clear(); _cached_so.clear(); _cached_ass.clear()


# 统一任务月(四个 Tab 共用,避免各 Tab 看错月份)
ym = st.selectbox("📅 任务月", months, key='gtm_ym')

tab1, tab2, tab3, tab4 = st.tabs(
    ["📋 名单生成", "👣 跟进看板", "📈 SO 成效", "🏅 考核"])

# ══════════════════ Tab1 名单生成 ══════════════════
with tab1:
    targets = get_targets(ym)

    if not targets.empty:
        st.markdown(f"#### 本月名单({len(targets)} 家 / "
                    f"{targets['分销经理'].nunique()} 个分销经理)")
        show = targets[['id', '分销经理', '客户名称', '城市', '所属代理商',
                        '推荐产品类', '推荐依据', '来源']]
        st.dataframe(show, use_container_width=True, hide_index=True, height=320)
        d1, d2, d3 = st.columns([2, 2, 2])
        with d1:
            del_id = st.selectbox(
                "选行操作", targets['id'].tolist(),
                format_func=lambda i: f"#{i} " + targets.set_index('id').loc[i, '客户名称'],
                key='gtm_del_sel', index=None, placeholder="选客户")
            if st.button("🗑 删除该行(仅本月)", disabled=del_id is None):
                delete_target(del_id)
                _clear_caches()
                st.rerun()
        with d2:
            st.caption("永久剔除 = 移出本月 + 以后不再自动推荐")
            if st.button("🚫 剔除并不再推荐", disabled=del_id is None):
                r = targets.set_index('id').loc[del_id]
                exclude_customer(r['客户编码'], r['客户名称'],
                                 原因=f'{ym} 手动剔除', 操作人=current_user())
                delete_target(del_id)
                _clear_caches()
                st.rerun()
        with d3:
            with st.popover("🧹 清空本月名单"):
                st.warning(f"将删除 {ym} 全部 {len(targets)} 条(动作/SO 均实时算,无留痕损失)")
                if st.button("确认清空", type='primary'):
                    clear_month(ym)
                    _clear_caches()
                    st.rerun()

    else:
        st.info(f"📭 {ym} 还没有 GTM 名单,先生成推荐或手动添加。")

    excluded = list_excluded()
    if not excluded.empty:
        with st.expander(f"🚫 永久剔除名单({len(excluded)} 家,不参与自动推荐)"):
            st.dataframe(excluded, use_container_width=True, hide_index=True)
            back = st.selectbox(
                "恢复客户", excluded['客户编码'].tolist(),
                format_func=lambda c: excluded.set_index('客户编码').loc[c, '客户名称'],
                key='gtm_unexcl', index=None, placeholder="选要恢复的客户")
            if st.button("♻️ 恢复(重新参与推荐)", disabled=back is None):
                unexclude_customer(back)
                st.rerun()

    st.markdown("---")
    st.markdown("#### 🤖 系统推荐(机会+培育画像)")
    st.caption(
        "候选 = 签约服务商(剔马甲) ∩ 客户所有者∈20分销经理 ∩ 近90天活跃;"
        "每专项近180天 **0台=机会 / 1~5台=培育**(>5台=核心毕业不推荐);至少命中一类才入选,"
        "按近90天货值排序取每人 Top N。"
        "**重复GTM政策**:完成但无产出→冷却2个月;手动剔除→永久;上月未跑动→自动顺延(不占名额)。"
    )
    g1, g2 = st.columns([2, 4])
    per_n = g1.number_input("每人名单数", 5, 30, 10, key='gtm_pern')
    if g2.button("🔮 生成推荐名单", type='primary'):
        with st.spinner("按画像扫描候选(冷却/剔除已过滤) + 上月未跑动顺延…"):
            fresh = generate_candidates(ym, int(per_n))
            roll = get_rollover_candidates(ym)
            st.session_state['_gtm_preview'] = {
                'ym': ym,
                'df': (pd.concat([roll, fresh], ignore_index=True)
                       if not roll.empty else fresh),
            }
            if not roll.empty:
                st.info(f"⏭ 上月未跑动顺延 {len(roll)} 家(标「上月顺延」,置顶)")
    _pv = st.session_state.get('_gtm_preview')
    # 预览绑定生成时的任务月:切月后作废,防止旧月候选误入新月
    if _pv is not None and _pv.get('ym') != ym:
        st.session_state.pop('_gtm_preview', None)
        _pv = None
        st.info("任务月已切换,之前的预览已作废 —— 请重新生成。")
    prev = _pv['df'] if _pv is not None else None
    if prev is not None:
        if prev.empty:
            st.warning("没有符合画像的新候选(可能都已入库)。")
        else:
            st.caption(f"预览 {len(prev)} 家(可改「推荐产品类」、取消勾选剔除),确认后入库:")
            prev = prev.copy()
            prev.insert(0, '选中', True)
            edited = st.data_editor(
                prev, use_container_width=True, hide_index=True, height=380,
                disabled=[c for c in prev.columns if c not in ('选中', '推荐产品类')],
                key='gtm_editor')
            picked = edited[edited['选中']]
            if st.button(f"✅ 入库 {len(picked)} 家", type='primary',
                         disabled=picked.empty):
                n = bulk_insert_targets(ym, picked.to_dict('records'))
                st.session_state.pop('_gtm_preview', None)
                _clear_caches()
                st.success(f"✅ 已入库 {n} 家")
                st.rerun()

    st.markdown("---")
    st.markdown("#### ✍️ 手动添加")
    m1, m2 = st.columns([3, 3])
    kw = m1.text_input("搜服务商名称", key='gtm_kw', placeholder="输入名称关键词")
    if kw:
        hits = search_providers(kw)
        if hits.empty:
            m2.warning("没搜到(签约表口径,已剔马甲)")
        else:
            pick = m1.selectbox(
                "选客户", hits['客户编码'].tolist(),
                format_func=lambda c: hits.set_index('客户编码').loc[c, '客户名称'],
                key='gtm_pick')
            fs = m2.multiselect("产品类(至少一类)", GTM_FOCUS, key='gtm_fs')
            if m2.button("➕ 加入名单", disabled=not fs):
                r = hits.set_index('客户编码').loc[pick]
                n = bulk_insert_targets(ym, [{
                    '客户编码': pick, '客户名称': r['客户名称'], '城市': r['城市'],
                    '区县': r['区县'], '所属代理商': r['所属代理商'],
                    '分销经理': r['分销经理'], '推荐产品类': ','.join(fs),
                    '推荐依据': '手动圈选', '来源': '手动添加'}])
                _clear_caches()
                st.success("✅ 已加入") if n else st.warning("⚠️ 未插入:本月已在名单中(或写库失败)")
                st.rerun()

# ══════════════════ Tab2 跟进看板 ══════════════════
with tab2:
    board = _cached_board(ym)
    if board.empty:
        st.info("本月无名单。")
    else:
        n = len(board)
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("名单", n)
        k2.metric("已跑动", int(board['跑动完成'].sum()),
                  f"{board['跑动完成'].mean():.0%}")
        k3.metric("已跑动·待动作",
                  int((board['跑动完成'] & ~board['GTM完成']).sum()))
        k4.metric("GTM 完成", int(board['GTM完成'].sum()),
                  f"{board['GTM完成'].mean():.0%}")
        k5.metric("待跑动", int((~board['跑动完成']).sum()))

        f1, f2 = st.columns([2, 2])
        mgr_sel = f1.multiselect("分销经理", sorted(board['分销经理'].unique()),
                                 placeholder="不选 = 全部", key='gtm_f_mgr')
        stat_sel = f2.multiselect("状态", ['✅ GTM完成', '🚶 已跑动·待动作', '⏳ 待跑动'],
                                  placeholder="不选 = 全部", key='gtm_f_stat')
        v = board.copy()
        if mgr_sel:
            v = v[v['分销经理'].isin(mgr_sel)]
        if stat_sel:
            v = v[v['状态'].isin(stat_sel)]
        show_cols = ['状态', '分销经理', '客户名称', '城市', '所属代理商', '推荐产品类',
                     '来源', '首访日', '推广会', '铺货台数', '红包台数', '上线台数', '动作数']
        st.dataframe(
            v[show_cols].sort_values(['分销经理', '状态']),
            use_container_width=True, hide_index=True, height=460,
            column_config={'推广会': st.column_config.CheckboxColumn('会')})
        st.download_button(
            "⬇️ 导出 CSV", v[show_cols].to_csv(index=False).encode('utf-8-sig'),
            file_name=f"GTM跟进-{ym}.csv", mime='text/csv')

# ══════════════════ Tab3 SO 成效 ══════════════════
with tab3:
    summary, detail = _cached_so(ym)
    if summary.empty:
        st.info("本月还没有 GTM 完成对象(或完成对象暂无窗口内 SO)。")
    else:
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("完成对象产出 SO(90天)", f"{summary['SO90_万'].sum():,.1f} 万")
        s2.metric("🌙 夜视王", f"{summary['夜视王_万'].sum():,.1f} 万")
        s3.metric("📡 无线", f"{summary['无线_万'].sum():,.1f} 万")
        s4.metric("🎯 场景化", f"{summary['场景化_万'].sum():,.1f} 万")
        st.caption("⚠️ 窗口 = 各对象首访日 + 90 天;未封窗的数值为**至今累计**(观察中)。"
                   "同客户跨月完成时窗口可能重叠,跨月 SO 不可直接加总。")
        st.markdown("##### 客户级汇总(30/60/90 分段 + 三专项拆分)")
        st.dataframe(summary, use_container_width=True, hide_index=True, height=380)
        with st.expander(f"🔬 SO 明细(序列号级,{len(detail)} 行)"):
            st.dataframe(detail, use_container_width=True, hide_index=True, height=400)
        e1, e2 = st.columns(2)
        e1.download_button(
            "⬇️ 汇总 CSV", summary.to_csv(index=False).encode('utf-8-sig'),
            file_name=f"GTM成效汇总-{ym}.csv", mime='text/csv')
        e2.download_button(
            "⬇️ 明细 CSV", detail.to_csv(index=False).encode('utf-8-sig'),
            file_name=f"GTM成效明细-{ym}.csv", mime='text/csv')

# ══════════════════ Tab4 考核 ══════════════════
with tab4:
    ass = _cached_ass(ym)
    if ass.empty:
        st.info("本月无名单,无从考核。")
    else:
        a1, a2, a3 = st.columns(3)
        a1.metric("覆盖分销经理", f"{len(ass)} / {len(list_managers())}")
        a2.metric("平均完成率", f"{ass['完成率'].mean():.0%}")
        a3.metric("合计 SO(90天)", f"{ass['SO90_万'].sum():,.1f} 万")
        st.markdown("##### 分销经理 GTM 漏斗(名单 → 跑动 → 动作 → 完成 → SO)")
        disp = ass.copy()
        disp[['跑动率', '完成率']] = (disp[['跑动率', '完成率']] * 100).round(0)
        st.dataframe(
            disp, use_container_width=True, hide_index=True, height=560,
            column_config={
                '跑动率': st.column_config.ProgressColumn('跑动率', format='%.0f%%',
                                                          min_value=0, max_value=100),
                '完成率': st.column_config.ProgressColumn('完成率', format='%.0f%%',
                                                          min_value=0, max_value=100),
            })
        st.download_button(
            "⬇️ 考核表 CSV", disp.to_csv(index=False).encode('utf-8-sig'),
            file_name=f"GTM考核-{ym}.csv", mime='text/csv')
        st.caption("排序 = 完成率↓、SO↓;SO 为该经理 GTM 完成对象 90 天窗口合计(含三专项拆分)。"
                   "未封窗月份的 SO 为至今累计;跨月窗口可能重叠,跨月 SO 不可直接加总。")
