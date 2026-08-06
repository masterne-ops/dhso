#!/usr/bin/env python3
"""📋 业务员跑动任务管理

3 个 Tab：
  1. 任务分配（admin/manager）— 选地市+月份 → 一键生成下月任务
  2. 全市任务清单 — 按地市/业务员筛选，看清单 + 完成度
  3. 我的任务（业务员）— 自己本月任务，确认 / 剔除 / 业务员替换 / 手动添加
  4. 任务核验（admin/manager）— 手动触发月底核验
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, current_user  # noqa: E402
from _loaders import DB_PATH, scoped_cities  # noqa: E402
from _monthly_city_report import list_cities, list_months  # noqa: E402
from _ai_log import (  # noqa: E402
    bulk_insert_tasks, list_tasks, update_task_status,
    delete_task, delete_tasks_by_month,
    get_user_role, get_user_scope, get_bound_salesperson,
)
from _task_manager import (  # noqa: E402
    generate_tasks, verify_tasks, search_clients_for_replace,
    _next_month, _prev_month,
)

require_auth()

st.markdown("### 📋 业务员跑动任务管理")
st.caption("管理员按月分配任务 → 业务员确认 / 剔除 / 替换 → 月底核验完成度")

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.0rem !important; line-height: 1.1; }
[data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
</style>
""", unsafe_allow_html=True)


# ── 角色 + scope（无权限系统时全开放，admin 视为已设）──
user = current_user()
role = get_user_role(user) if user else 'admin'
scope = get_user_scope(user) if user else {}

if not role:
    role = 'admin'

is_admin = role in ('admin', 'manager')
is_salesperson = (role == 'salesperson')

# scope 过滤的地市/业务员
allowed_cities = scope.get('city', [])
allowed_sps = scope.get('salesperson', [])


# ── 顶部参数 ──
try:
    cities = scoped_cities(list_cities())   # 🔒 按 scope 过滤
    min_month, max_month = list_months()
except Exception as e:
    st.error(f"读 DB 失败：{e}")
    st.stop()


def cached_conn():
    return sqlite3.connect(str(DB_PATH))


tab_alloc, tab_list, tab_mine, tab_verify = st.tabs([
    "🎯 分配任务", "📋 任务清单", "👤 我的任务", "✅ 核验",
])


# ══════════════════════════════════════════════
# Tab 1：任务分配（admin/manager）
# ══════════════════════════════════════════════
with tab_alloc:
    if not is_admin:
        st.warning("⚠️ 仅管理员可分配任务。普通业务员请到「我的任务」Tab 查看自己的清单。")
    else:
        st.markdown("#### 一键生成下月任务")
        st.caption("基于当前月 page 06 跑动评估结果（漏跑 + 救援未果 + F 偏弱），自动给大华业务员分配下月任务。")

        months_list = [str(p) for p in pd.period_range(min_month, max_month, freq='M')]

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            sel_city = st.selectbox("📍 地市", cities,
                                    index=cities.index('杭州市') if '杭州市' in cities else 0)
        # 评估窗口默认当前 DB 最大月 / 往前 4 月
        with col2:
            eval_end = st.selectbox("评估期结束", months_list,
                                    index=len(months_list) - 1)
        with col3:
            eval_start = st.selectbox("评估期开始", months_list,
                                      index=max(0, len(months_list) - 4))
        with col4:
            per_sp = st.number_input("每人任务上限", min_value=10, max_value=200, value=50, step=10)

        target_month = _next_month(eval_end)
        st.info(f"📅 目标月份：**{target_month}** | 评估期：{eval_start} ~ {eval_end} | 业务员上限 {per_sp} 个/人")

        # 已存在的任务预览
        existing = list_tasks(任务年月=target_month, 地市=sel_city)
        if not existing.empty:
            st.warning(f"⚠️ {target_month} {sel_city} 已有 **{len(existing)}** 条任务（{existing['业务员'].nunique()} 名业务员）")
            cols = st.columns([2, 1])
            with cols[0]:
                if st.button("🗑️ 清空已存在再重新生成", type='secondary'):
                    n = delete_tasks_by_month(target_month, 地市=sel_city)
                    st.success(f"删除 {n} 条")
                    st.rerun()

        if st.button(f"🎯 生成 {target_month} 任务（{sel_city}）", type='primary',
                     disabled=(not existing.empty)):
            with st.spinner('正在分析评估期数据 + 算 RFM + 分任务……'):
                conn = cached_conn()
                try:
                    df = generate_tasks(conn, sel_city, target_month, eval_start, eval_end, per_sp_limit=int(per_sp))
                finally:
                    conn.close()

            if df.empty:
                st.warning("⚠️ 该城市评估期内无有 scope 的大华业务员，或所有人无候选任务。")
            else:
                st.session_state['_task_preview'] = df
                st.success(f"✅ 生成预览：**{len(df)}** 条任务，覆盖 **{df['业务员'].nunique()}** 名业务员。下方确认后入库。")

        # 预览 + 编辑 + 入库
        if '_task_preview' in st.session_state:
            df_preview = st.session_state['_task_preview']
            st.markdown("##### 任务预览（可编辑字段 / 勾选删除）")
            st.caption(
                "✏️ 直接在表里改 **优先级 / 任务类型 / 任务说明**； "
                "🗑️ 行首勾选「删除」→ 点表下方「删除选中行」按钮； "
                "➕ 想加客户 → 滚到下方「补充任务」搜索加入； "
                "改完点最底部「全部入库」"
            )

            # 业务员筛选（行多了好定位）
            sp_filter = st.multiselect(
                "按业务员筛选（不选=全部）",
                sorted(df_preview['业务员'].unique().tolist()),
                key='preview_sp_filter',
            )
            show_df = df_preview if not sp_filter else df_preview[df_preview['业务员'].isin(sp_filter)]
            show_df = show_df.reset_index(drop=True)

            # 加一列「🗑️」复选框 — 用户勾选要删的行
            show_df_with_del = show_df[
                ['业务员', '优先级', '任务类型', '客户名称', '区县', '所属一级客户', '任务说明']
            ].copy()
            show_df_with_del.insert(0, '🗑️', False)

            edited = st.data_editor(
                show_df_with_del,
                use_container_width=True,
                hide_index=True,
                num_rows='fixed',            # 不允许加行（加 → 用下方「补充任务」）
                height=min(500, 40 + 35 * len(show_df)),
                column_config={
                    '🗑️': st.column_config.CheckboxColumn(
                        '🗑️',
                        help='勾选后点表下方「删除选中行」按钮',
                        default=False, width='small',
                    ),
                    '业务员': st.column_config.TextColumn(disabled=True),
                    '客户名称': st.column_config.TextColumn(disabled=True),
                    '区县': st.column_config.TextColumn(disabled=True),
                    '所属一级客户': st.column_config.TextColumn(disabled=True),
                    '优先级': st.column_config.SelectboxColumn(
                        options=['🚨 高', '⚠️ 中', '🟢 低'], required=True,
                    ),
                    '任务类型': st.column_config.SelectboxColumn(
                        options=['严重漏跑救援', 'R预警救援', '维护到期复访',
                               'F偏弱补访', '手动添加'], required=True,
                    ),
                    '任务说明': st.column_config.TextColumn(),
                },
                key='task_editor',
            )

            # 🗑️ 删除选中行按钮
            n_to_delete = int(edited['🗑️'].sum()) if '🗑️' in edited.columns else 0
            dc1, dc2 = st.columns([2, 6])
            with dc1:
                if st.button(
                    f"🗑️ 删除选中 {n_to_delete} 行",
                    disabled=(n_to_delete == 0),
                    type='secondary',
                    use_container_width=True,
                    key='_p07_del_btn',
                ):
                    # 按 (业务员, 客户名称) 定位要删的行 — 两者在 editor 里都 disabled，不会改
                    to_del = edited[edited['🗑️'] == True][['业务员', '客户名称']]
                    to_del_keys = set(zip(
                        to_del['业务员'].astype(str),
                        to_del['客户名称'].astype(str),
                    ))
                    keep_mask = ~df_preview.apply(
                        lambda r: (str(r['业务员']), str(r['客户名称'])) in to_del_keys,
                        axis=1,
                    )
                    new_preview = df_preview[keep_mask].reset_index(drop=True)
                    st.session_state['_task_preview'] = new_preview
                    st.toast(f"🗑️ 已删除 {n_to_delete} 行")
                    st.rerun()
            with dc2:
                if n_to_delete > 0:
                    st.caption(f"已勾选 **{n_to_delete}** 行待删除，点左边按钮确认")

            # 把编辑结果合并回原预览（需保留客户编码等不可见字段）
            # show_df 和 edited 通过位置对应（hide_index=True）
            if not sp_filter:
                # 简单情形：edited 行数 ≤ show_df，按客户名称匹配回 df_preview
                pass

            st.caption("按业务员分布（编辑后实时反映）：")
            dist = edited.groupby(['业务员', '优先级']).size().unstack(fill_value=0) \
                if not edited.empty else pd.DataFrame()
            st.dataframe(dist, use_container_width=True)

            # ──────────────────────────────────────────
            # ➕ 给某个业务员补充任务（手动追加到预览）
            # ──────────────────────────────────────────
            st.markdown("---")
            st.markdown("##### ➕ 补充任务（给指定业务员手动加客户）")
            st.caption(
                "如果系统漏推或你想给某个业务员补一些客户，在这里搜索客户名/编码 → "
                "选客户 → 加到预览。**入库前所有改动都不持久**，最后点「全部入库」才真正写库。"
            )

            preview_sps = sorted(df_preview['业务员'].unique().tolist())

            ac1, ac2, ac3 = st.columns([2, 3, 2])
            with ac1:
                add_sp = st.selectbox(
                    "给谁加",
                    preview_sps,
                    key='_p07_add_sp',
                    help="只能选预览里已有的业务员（系统已识别有 scope 的人）",
                )
            with ac2:
                add_kw = st.text_input(
                    "搜索客户名 / 编码（模糊匹配，scope 内）",
                    key='_p07_add_kw',
                    placeholder="如：杭州 XX 安防",
                )
            with ac3:
                add_priority = st.selectbox(
                    "优先级",
                    ['🚨 高', '⚠️ 中', '🟢 低'],
                    index=1,
                    key='_p07_add_priority',
                )

            if add_kw and add_sp:
                conn = cached_conn()
                try:
                    cands = search_clients_for_replace(
                        conn, sel_city, add_sp, add_kw, limit=30,
                    )
                finally:
                    conn.close()
                if cands.empty:
                    st.info(
                        f"在 **{add_sp}** 的 scope 内（{sel_city}）没找到匹配「{add_kw}」的客户。"
                        f"提示：搜索只在「该业务员负责的代理商旗下」的客户里找。"
                    )
                else:
                    # 标记已在预览里的客户（避免重复添加）
                    cands_show = cands.copy()
                    in_preview = set(
                        df_preview.loc[df_preview['业务员'] == add_sp, '客户编码']
                                  .astype(str)
                    )
                    cands_show['状态'] = cands_show['客户编码'].astype(str).apply(
                        lambda c: '⚠️ 已在预览' if c in in_preview else '✅ 可加'
                    )
                    cands_show = cands_show[
                        ['状态', '客户名称', '客户编码', '区县',
                         '所属一级客户', '累计货值_万', '最近上线']
                    ]
                    ev = st.dataframe(
                        cands_show,
                        use_container_width=True, hide_index=True,
                        height=min(280, 40 + 35 * len(cands_show)),
                        selection_mode='single-row',
                        on_select='rerun',
                        key='_p07_add_cands_df',
                    )
                    sel_rows = ev.selection.rows if hasattr(ev, 'selection') else []
                    if sel_rows:
                        sel_idx = sel_rows[0]
                        sel_row = cands.iloc[sel_idx]
                        sel_code = str(sel_row['客户编码'])
                        already = sel_code in in_preview

                        memo_default = (
                            f"补充 | 累计 {sel_row['累计货值_万']}万 | "
                            f"最近上线 {sel_row['最近上线']}"
                        )
                        b1, b2 = st.columns([4, 1])
                        with b1:
                            new_memo = st.text_input(
                                "任务说明（可改）",
                                value=memo_default,
                                key='_p07_add_memo',
                            )
                        with b2:
                            if st.button(
                                f"➕ 加到 {add_sp}",
                                type='primary',
                                use_container_width=True,
                                disabled=already,
                                key='_p07_add_btn',
                            ):
                                new_row = {
                                    '任务年月': target_month,
                                    '地市': sel_city,
                                    '业务员': add_sp,
                                    '客户编码': sel_code,
                                    '客户名称': sel_row['客户名称'],
                                    '区县': sel_row['区县'],
                                    '所属一级客户': sel_row['所属一级客户'],
                                    '任务类型': '手动添加',
                                    '优先级': add_priority,
                                    '任务说明': new_memo,
                                }
                                st.session_state['_task_preview'] = pd.concat(
                                    [df_preview, pd.DataFrame([new_row])],
                                    ignore_index=True,
                                )
                                st.success(
                                    f"✅ 已加入预览：**{sel_row['客户名称']}** → **{add_sp}**"
                                )
                                st.rerun()
                        if already:
                            st.caption(f"⚠️ 该客户已在 {add_sp} 的预览里，不重复添加。")

            col_save, col_cancel = st.columns([1, 1])
            with col_save:
                if st.button(f"💾 入库 {len(edited)} 条", type='primary',
                             disabled=edited.empty):
                    # 入库前先去掉 🗑️ 列（只是 UI 用，不入库）
                    edited_for_save = (
                        edited.drop(columns=['🗑️'], errors='ignore')
                    )
                    # 把编辑后的字段合并回原 df_preview（含客户编码等隐藏字段）
                    base_cols = ['任务年月', '地市', '业务员', '客户编码', '客户名称',
                                 '区县', '所属一级客户']
                    # 按 (业务员, 客户名称) 合并：edited 给出新优先级/任务类型/任务说明
                    merged = edited_for_save.merge(
                        df_preview[base_cols],
                        on=['业务员', '客户名称'],
                        how='left',
                        suffixes=('', '_orig'),
                    )
                    # 若 sp_filter 不为空，还要保留未在编辑器里的另一些业务员的原任务
                    if sp_filter:
                        other = df_preview[~df_preview['业务员'].isin(sp_filter)]
                        records = pd.concat(
                            [merged.to_frame() if isinstance(merged, pd.Series) else merged, other],
                            ignore_index=True,
                        ).to_dict('records')
                    else:
                        records = merged.to_dict('records')

                    # 去掉 _orig 后缀字段（如有）
                    records = [{k: v for k, v in r.items() if not k.endswith('_orig')}
                               for r in records]
                    n = bulk_insert_tasks(records)
                    st.success(f"✅ 已入库 {n} 条"
                               f"{f'（{len(records) - n} 条因 (任务年月+业务员+客户编码) 重复跳过）' if len(records) - n else ''}")
                    del st.session_state['_task_preview']
                    st.rerun()
            with col_cancel:
                if st.button("✖️ 取消预览"):
                    del st.session_state['_task_preview']
                    st.rerun()


# ══════════════════════════════════════════════
# Tab 2：全市任务清单（按 scope 过滤）
# ══════════════════════════════════════════════
with tab_list:
    st.markdown("#### 全部任务清单")
    f1, f2, f3, f4 = st.columns(4)

    months_df = list_tasks(limit=10000)
    available_months = sorted(months_df['任务年月'].unique().tolist(), reverse=True) if not months_df.empty else []
    with f1:
        filter_month = st.selectbox("任务月份", ['全部'] + available_months, key='list_month')
    with f2:
        filter_city = st.selectbox("地市", ['全部'] + cities, key='list_city')
    with f3:
        if not months_df.empty:
            sps = sorted(months_df['业务员'].unique().tolist())
            if allowed_sps:
                sps = [s for s in sps if s in allowed_sps]
            filter_sp = st.selectbox("业务员", ['全部'] + sps, key='list_sp')
        else:
            filter_sp = '全部'
    with f4:
        filter_status = st.selectbox(
            "完成状态", ['全部', '未完成', '已拜访', '已激活', '逾期'], key='list_status',
        )

    df_list = list_tasks(
        任务年月=None if filter_month == '全部' else filter_month,
        地市=None if filter_city == '全部' else filter_city,
        业务员=None if filter_sp == '全部' else filter_sp,
        完成状态=None if filter_status == '全部' else filter_status,
        limit=10000,
    )

    # scope 过滤
    if allowed_cities:
        df_list = df_list[df_list['地市'].isin(allowed_cities)]
    if allowed_sps:
        df_list = df_list[df_list['业务员'].isin(allowed_sps)]

    if df_list.empty:
        st.info("无任务记录。")
    else:
        # 摘要 metrics
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("任务总数", len(df_list))
        c2.metric("已确认", int((df_list['确认状态'] == '已确认').sum()))
        c3.metric("✅ 已激活", int((df_list['完成状态'] == '已激活').sum()))
        c4.metric("🟡 已拜访", int((df_list['完成状态'] == '已拜访').sum()))
        c5.metric("❌ 未完成/逾期",
                  int(df_list['完成状态'].isin(['未完成', '逾期']).sum()))

        # 🏷️ 加标签列
        df_list_t = df_list.copy()
        try:
            from _tag_widget import attach_tags_column
            df_list_t = attach_tags_column(df_list_t, code_col='客户编码')
        except Exception:
            pass
        _list_cols = [
            '任务年月', '地市', '业务员', '优先级', '任务类型',
            '客户名称', '区县', '所属一级客户',
            '任务说明', '确认状态', '完成状态',
            '实际拜访日', '实际激活日',
        ]
        if '🏷️ 标签' in df_list_t.columns:
            # 标签放在 客户名称 后
            _list_cols = _list_cols[:_list_cols.index('客户名称') + 1] + \
                         ['🏷️ 标签'] + _list_cols[_list_cols.index('客户名称') + 1:]
        st.dataframe(
            df_list_t[[c for c in _list_cols if c in df_list_t.columns]],
            use_container_width=True, hide_index=True,
            height=min(550, 40 + 35 * len(df_list)),
        )

        csv = df_list.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            "⬇️ 下载 CSV", csv,
            file_name=f"跑动任务清单_{datetime.now().strftime('%Y%m%d')}.csv",
            mime='text/csv',
        )


# ══════════════════════════════════════════════
# Tab 3：我的任务（业务员视角）
# ══════════════════════════════════════════════
with tab_mine:
    if not user:
        st.warning("⚠️ 未登录，无法识别业务员身份。")
    else:
        # 🔒 用户 → 业务员名 解析优先级：
        #   1. bound_salesperson（最准 — admin 显式绑定的）
        #   2. scope.salesperson（旧 scope 兼容）
        #   3. username（兜底）
        _bound = get_bound_salesperson(user)
        if _bound:
            my_sp_names = [_bound]
            _binding_source = f"已绑定到业务员「{_bound}」"
        elif allowed_sps:
            my_sp_names = allowed_sps
            _binding_source = f"按旧 scope 识别为「{', '.join(allowed_sps)}」（建议去用户管理页绑定）"
        else:
            my_sp_names = [user]
            _binding_source = "⚠️ 未绑定业务员，按账号名匹配（多半找不到任务）"

        st.markdown(f"#### 我的任务（{', '.join(my_sp_names)}）")
        st.caption(f"身份识别：{_binding_source}")

        # 未绑定的提示（salesperson 必须；manager 自己跑客户时也应该绑）
        if not _bound:
            if role == 'salesperson':
                st.warning(
                    "⚠️ 你的账号还没绑定业务员名。请联系管理员到「用户与权限」页给你绑定 → "
                    "绑定后这里才能看到分配给你的任务。"
                )
            elif role == 'manager':
                st.info(
                    "💡 你是 manager 角色，没绑定到具体业务员；如果你自己也跑客户，"
                    "可以让管理员把你绑定到对应业务员名，绑定后这里也能查你自己的任务。"
                )

        # 月份选择
        all_my_months = sorted(
            list_tasks(limit=10000)['任务年月'].unique().tolist(),
            reverse=True,
        ) if not list_tasks(limit=10000).empty else []
        with st.container():
            cols = st.columns([2, 4])
            with cols[0]:
                my_month = st.selectbox("月份", all_my_months or ['（暂无）'], key='mine_month')

        if not all_my_months:
            st.info("还没有任何月份的任务。")
        else:
            # 当前 sp 的任务
            df_mine = list_tasks(任务年月=my_month, limit=10000)
            df_mine = df_mine[df_mine['业务员'].isin(my_sp_names)]

            if df_mine.empty:
                st.info(f"你（{', '.join(my_sp_names)}）在 {my_month} 没有分配的任务。")
            else:
                # 顶部状态卡
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("总任务", len(df_mine))
                c2.metric("待确认", int((df_mine['确认状态'] == '待确认').sum()))
                c3.metric("已确认", int((df_mine['确认状态'] == '已确认').sum()))
                c4.metric("✅ 已激活", int((df_mine['完成状态'] == '已激活').sum()))
                c5.metric("❌ 未完成", int(df_mine['完成状态'].isin(['未完成', '逾期']).sum()))

                # 一键全部确认
                pending = df_mine[df_mine['确认状态'] == '待确认']
                if not pending.empty:
                    if st.button(f"✅ 一键确认 {len(pending)} 条待确认任务", type='primary'):
                        for _, t in pending.iterrows():
                            update_task_status(int(t['id']), 确认状态='已确认')
                        st.success(f"✅ 已确认 {len(pending)} 条")
                        st.rerun()

                # 逐条操作
                st.markdown("##### 任务明细（可单独剔除）")
                for _, t in df_mine.iterrows():
                    with st.container(border=True):
                        cols = st.columns([3, 1, 1, 1])
                        with cols[0]:
                            st.write(f"**{t['优先级']} {t['任务类型']}** · {t['客户名称']} ({t['区县']})")
                            st.caption(t['任务说明'] or '—')
                        cols[1].write(f"确认: **{t['确认状态']}**")
                        cols[2].write(f"完成: **{t['完成状态'] or '—'}**")
                        with cols[3]:
                            if t['确认状态'] in ('待确认', '已确认'):
                                with st.popover("🗑️ 剔除"):
                                    reason = st.text_input(
                                        "剔除原因", key=f"rm_reason_{t['id']}",
                                        placeholder="如：客户已注销/已无意愿/出差等",
                                    )
                                    if st.button("确认剔除", key=f"rm_btn_{t['id']}",
                                                 disabled=not reason):
                                        update_task_status(
                                            int(t['id']), 确认状态='已剔除', 剔除原因=reason,
                                        )
                                        st.rerun()

                # ─── 手动添加任务 ───
                st.markdown("---")
                with st.container(border=True):
                    st.markdown("#### ➕ 我想自己加客户跑")
                    st.caption(
                        "系统推荐的客户不够，或你想跑某个特定客户 → 在这里搜索 → 选客户 → 加入任务清单。\n"
                        "**只能加你 scope 内的客户**（即你负责的代理商旗下的服务商）。"
                    )

                    if len(my_sp_names) > 1:
                        st.warning(
                            "⚠️ 当前账号映射多个业务员名："
                            f"`{', '.join(my_sp_names)}`，请联系管理员把权限收敛到唯一业务员才能手动添加。"
                        )
                    else:
                        ac1, ac2 = st.columns([5, 2])
                        with ac1:
                            add_kw = st.text_input(
                                "搜索客户名 / 编码（模糊匹配）",
                                key='add_keyword',
                                placeholder="如：杭州 XX 安防",
                            )
                        with ac2:
                            add_priority_mine = st.selectbox(
                                "优先级",
                                ['🚨 高', '⚠️ 中', '🟢 低'],
                                index=1,
                                key='_p07_mine_priority',
                            )

                        if add_kw:
                            conn = cached_conn()
                            try:
                                cands = search_clients_for_replace(
                                    conn, df_mine['地市'].iloc[0], my_sp_names[0],
                                    add_kw, limit=30,
                                )
                            finally:
                                conn.close()
                            if cands.empty:
                                st.info(
                                    f"在你 scope 内没找到匹配「{add_kw}」的客户。"
                                    "提示：搜索只在你负责的代理商旗下的服务商里找。"
                                )
                            else:
                                # 标记已在本月任务中的客户
                                in_tasks = set(df_mine['客户编码'].astype(str))
                                cands_show = cands.copy()
                                cands_show['状态'] = cands_show['客户编码'].astype(str).apply(
                                    lambda c: '⚠️ 已在任务' if c in in_tasks else '✅ 可加'
                                )
                                cands_show = cands_show[
                                    ['状态', '客户名称', '客户编码', '区县',
                                     '所属一级客户', '累计货值_万', '最近上线']
                                ]
                                ev = st.dataframe(
                                    cands_show,
                                    use_container_width=True, hide_index=True,
                                    height=min(280, 40 + 35 * len(cands_show)),
                                    selection_mode='single-row',
                                    on_select='rerun',
                                    key='_p07_mine_cands_df',
                                )
                                sel_rows = ev.selection.rows if hasattr(ev, 'selection') else []
                                if sel_rows:
                                    sel_idx = sel_rows[0]
                                    sel_row = cands.iloc[sel_idx]
                                    sel_code = str(sel_row['客户编码'])
                                    already = sel_code in in_tasks

                                    memo_default = (
                                        f"业务员补充 | 累计 {sel_row['累计货值_万']}万 | "
                                        f"最近上线 {sel_row['最近上线']}"
                                    )
                                    mb1, mb2 = st.columns([4, 1])
                                    with mb1:
                                        new_memo = st.text_input(
                                            "任务说明（可改）",
                                            value=memo_default,
                                            key='_p07_mine_memo',
                                        )
                                    with mb2:
                                        if st.button(
                                            "➕ 加入任务",
                                            type='primary',
                                            use_container_width=True,
                                            disabled=already,
                                            key='_p07_mine_add_btn',
                                        ):
                                            bulk_insert_tasks([{
                                                '任务年月': my_month,
                                                '地市': df_mine['地市'].iloc[0],
                                                '业务员': my_sp_names[0],
                                                '客户编码': sel_code,
                                                '客户名称': sel_row['客户名称'],
                                                '区县': sel_row['区县'],
                                                '所属一级客户': sel_row['所属一级客户'],
                                                '任务类型': '手动添加',
                                                '任务说明': new_memo,
                                                '优先级': add_priority_mine,
                                            }])
                                            st.success(f"✅ 加入任务：{sel_row['客户名称']}")
                                            st.rerun()
                                    if already:
                                        st.caption("⚠️ 该客户已在你本月任务中，不重复添加。")


# ══════════════════════════════════════════════
# Tab 4：核验
# ══════════════════════════════════════════════
with tab_verify:
    if not is_admin:
        st.warning("⚠️ 仅管理员可触发核验。")
    else:
        st.markdown("#### 月底核验任务完成情况")
        st.caption("核验逻辑：对比 `visit_record_v`（实际拜访）+ `install_redpack_v`（拜访后 30 天内激活），写回每条任务的「完成状态/实际拜访日/实际激活日」。")

        all_months = sorted(
            list_tasks(limit=100000)['任务年月'].unique().tolist(),
            reverse=True,
        ) if not list_tasks(limit=100000).empty else []
        if not all_months:
            st.info("还没有任何月份的任务。")
        else:
            v1, v2 = st.columns(2)
            with v1:
                verify_month = st.selectbox("核验月份", all_months, key='verify_month')
            with v2:
                verify_city = st.selectbox("地市（可空=全部）", ['全部'] + cities, key='verify_city')

            ex_tasks = list_tasks(任务年月=verify_month,
                                  地市=None if verify_city == '全部' else verify_city,
                                  limit=100000)
            st.info(f"📅 {verify_month} {verify_city if verify_city!='全部' else '全部'} 任务数：**{len(ex_tasks)}**")

            if st.button(f"✅ 核验 {verify_month} 任务", type='primary',
                         disabled=ex_tasks.empty):
                with st.spinner('正在比对 visit + install_redpack 数据……'):
                    conn = cached_conn()
                    try:
                        n = verify_tasks(
                            conn, verify_month,
                            city=None if verify_city == '全部' else verify_city,
                        )
                    finally:
                        conn.close()
                st.success(f"✅ 核验完成，更新 {n} 条任务状态。回 Tab 2 查看结果。")
