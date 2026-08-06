#!/usr/bin/env python3
"""☂️ 伞形组管理

显示所有人工建立的伞形组 + 选中组查看成员明细。

数据来源：
  - provider_umbrella_group / provider_umbrella_member
  - 启发式同老板姓名+电话识别（仅作 background 统计，不在此页操作）

打标入口在 page 05 服务商画像（"添加 伞形 标签"按钮）— 因为打标需要先理解客户。
本页用来：
  ① 看全省所有已建组的全貌（多少组 / 累计货值 / 牵涉客户数）
  ② 点开任意组看成员细节、解散组、移除成员
  ③ 看哪些客户是启发式识别但还没明确建组（建议给 admin 建组）
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import (  # noqa: E402
    require_auth, current_user, is_admin, is_privileged, get_current_role,
    filter_by_scope,
)
from _ai_log import (  # noqa: E402
    list_umbrella_groups, list_umbrella_group_members,
    umbrella_group_quick_stats, dissolve_umbrella_group,
    remove_umbrella_member, add_umbrella_members,
    bulk_create_umbrella_groups_from_heuristic,
)

require_auth()


st.markdown("### ☂️ 伞形组管理")
st.caption(
    "**伞形不是单户标签，是组关系：一组共享伞形身份。** "
    "打标入口在 🔭 服务商画像页（"
    "搜服务商 → 点添加 伞形 → 选候选成员）。"
    "本页只看 + 解散组 / 移除单成员。"
)

role = (get_current_role() or '').strip().lower()
can_edit = role in ('admin', 'manager') or is_privileged()

# 顶部角色 + 权限指示器（便于排查"为什么没勾选框"）
_role_label = {
    'admin': '🛡 admin（全部权限）',
    'manager': '👔 manager（可建/解散组）',
    'salesperson': '🧑‍💼 salesperson（只读）',
    'guest': '👀 guest（只读）',
    '': '❓ 未识别',
}.get(role, f'❓ {role}')
st.caption(
    f"当前用户：**{current_user() or '—'}** · 角色：**{_role_label}** · "
    f"可建/解散组：**{'✅ 是' if can_edit else '⛔ 否（需 admin / manager）'}**"
)


# ──────────────────────────────────────────
# 总览
# ──────────────────────────────────────────
stats = umbrella_group_quick_stats()
mc1, mc2, mc3 = st.columns(3)
mc1.metric("活跃组数", f"{stats['n_groups']:,}")
mc2.metric("总成员数", f"{stats['n_members']:,}",
            help="所有 active 组的成员合计")
mc3.metric("累计货值合计", f"{stats['累计货值_万']:,.1f} 万",
            help="所有 active 组成员的红包扫码累计货值")

st.divider()


# ──────────────────────────────────────────
# 视图切换
# ──────────────────────────────────────────
tab_active, tab_dissolved, tab_heuristic = st.tabs([
    "✅ 活跃组",
    "🗑️ 已解散组",
    "💡 启发式未建组",
])


# ──────────────────────────────────────────
# Tab 1：活跃组
# ──────────────────────────────────────────
with tab_active:
    groups = list_umbrella_groups(active_only=True, limit=2000)

    # scope 过滤（业务员/manager 只看自己的城市）
    groups = filter_by_scope(groups, city_col='城市')

    if groups.empty:
        st.info(
            "📭 还没有任何活跃伞形组。\n\n"
            "💡 去 🔭 服务商画像 页面，选一个伞形客户 → 在「🏷️ 标签 → ☂️ 伞形组」面板创建第一个组。"
        )
    else:
        st.markdown(f"##### 📋 共 {len(groups)} 组")
        cols_show = [
            'group_id', 'group_name', '主账号_客户编码', '主账号_客户名称',
            '城市', '区县', '成员数', '累计货值_万',
            '创建时间', '创建人', '备注',
        ]
        gshow = groups[cols_show].copy()
        gshow['累计货值_万'] = gshow['累计货值_万'].round(2)

        ev = st.dataframe(
            gshow,
            use_container_width=True, hide_index=True,
            height=min(500, 40 + 35 * len(gshow)),
            selection_mode='single-row',
            on_select='rerun',
            key='_g_table',
            column_config={
                'group_id': st.column_config.NumberColumn('组ID', width='small'),
                'group_name': st.column_config.TextColumn('组名'),
                '成员数': st.column_config.NumberColumn('成员数', width='small'),
                '累计货值_万': st.column_config.NumberColumn(
                    '累计货值(万)', format='%.2f', width='small',
                ),
            },
        )

        # 下载
        csv = gshow.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            "⬇️ 下载组列表 CSV", csv,
            file_name=f"伞形组_{len(gshow)}组.csv", mime='text/csv',
        )

        # 详细
        selected_rows = ev.selection.rows if hasattr(ev, 'selection') else []
        if selected_rows:
            sel_g = groups.iloc[selected_rows[0]]
            gid = int(sel_g['group_id'])
            st.divider()
            st.markdown(f"#### #{gid} · {sel_g['group_name']}")
            head_l, head_r = st.columns([4, 2])
            with head_l:
                st.caption(
                    f"主账号：**{sel_g['主账号_客户名称'] or sel_g['主账号_客户编码']}** "
                    f"({sel_g['主账号_客户编码']}) · "
                    f"创建于 {sel_g['创建时间'] or '—'} · "
                    f"创建人 {sel_g['创建人'] or '—'}"
                )
                if sel_g['备注']:
                    st.caption(f"备注：{sel_g['备注']}")
            with head_r:
                if can_edit:
                    confirm_key = f"_dissolve_conf_{gid}"
                    if st.session_state.get(confirm_key):
                        if st.button(
                            f"💥 确认解散组 #{gid}",
                            key=f"_dissolve_yes_{gid}",
                            type='primary', use_container_width=True,
                        ):
                            ok = dissolve_umbrella_group(
                                group_id=gid, 解散人=current_user(),
                                解散原因='页面操作',
                            )
                            if ok:
                                st.toast(f"💥 组 #{gid} 已解散")
                                st.session_state.pop(confirm_key, None)
                                st.rerun()
                        if st.button(
                            "取消", key=f"_dissolve_no_{gid}",
                            use_container_width=True,
                        ):
                            st.session_state.pop(confirm_key, None)
                            st.rerun()
                    else:
                        if st.button(
                            "💥 解散此组",
                            key=f"_dissolve_init_{gid}",
                            use_container_width=True,
                        ):
                            st.session_state[confirm_key] = True
                            st.rerun()

            # 成员明细
            members = list_umbrella_group_members(gid)
            if members.empty:
                st.info("（组内无成员）")
            else:
                mshow = members[[
                    '是否主账号', '客户编码', '客户名称', '城市', '区县',
                    '老板姓名', '老板电话', '红包数', '累计货值_万',
                    '加入时间', '加入人',
                ]].copy()
                mshow['是否主账号'] = mshow['是否主账号'].map({1: '👑 主账号', 0: ''})
                mshow['累计货值_万'] = mshow['累计货值_万'].round(2)
                st.dataframe(
                    mshow,
                    use_container_width=True, hide_index=True,
                    height=min(400, 40 + 35 * len(mshow)),
                )

                # 单成员撤销
                if can_edit:
                    rc1, rc2, rc3 = st.columns([3, 3, 2])
                    with rc1:
                        # 不能移除主账号（会让组失去 leader）
                        non_leader = members[members['是否主账号'] == 0]
                        if non_leader.empty:
                            st.caption("（仅有主账号，无可移除成员）")
                            rm_code = None
                        else:
                            opts = list(zip(
                                non_leader['客户编码'].astype(str),
                                non_leader['客户名称'].astype(str),
                            ))
                            labels = [f"{n} ({c})" for c, n in opts]
                            sel_idx = st.selectbox(
                                "移除单成员",
                                list(range(len(opts))),
                                format_func=lambda i: labels[i],
                                key=f"_rm_sel_{gid}",
                            )
                            rm_code = opts[sel_idx][0]
                    with rc2:
                        rm_reason = st.text_input(
                            "撤销原因（可空）",
                            key=f"_rm_reason_{gid}",
                        )
                    with rc3:
                        if rm_code and st.button(
                            "↩️ 移除",
                            key=f"_rm_btn_{gid}",
                            use_container_width=True,
                        ):
                            ok = remove_umbrella_member(
                                group_id=gid, 客户编码=rm_code,
                                撤销人=current_user(), 撤销原因=rm_reason,
                            )
                            if ok:
                                st.toast(f"↩️ 已移除 {rm_code}")
                                st.rerun()

                # 加成员
                if can_edit:
                    with st.expander("➕ 给本组追加成员（输入客户编码）"):
                        new_codes = st.text_area(
                            "新成员客户编码（每行一个）",
                            key=f"_add_text_{gid}",
                            placeholder="1@xxx\n2@yyy",
                            height=120,
                        )
                        if st.button(
                            "提交追加",
                            key=f"_add_btn_{gid}",
                            type='primary',
                        ):
                            codes = [c.strip() for c in (new_codes or '').splitlines()
                                      if c.strip()]
                            if not codes:
                                st.warning("请输入至少一个客户编码")
                            else:
                                try:
                                    res = add_umbrella_members(
                                        group_id=gid, codes=codes,
                                        加入人=current_user(),
                                    )
                                    st.success(
                                        f"✅ 加入 {len(res['added_codes'])} 个 · "
                                        f"跳过 {len(res['skipped_codes'])} 个"
                                    )
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"加成员失败：{e}")


# ──────────────────────────────────────────
# Tab 2：已解散组
# ──────────────────────────────────────────
with tab_dissolved:
    dissolved = list_umbrella_groups(active_only=False, limit=2000)
    dissolved = dissolved[dissolved['解散时间'].notna()] if not dissolved.empty else dissolved
    dissolved = filter_by_scope(dissolved, city_col='城市')

    if dissolved.empty:
        st.info("📭 暂无已解散组")
    else:
        st.markdown(f"##### 🗑️ 共 {len(dissolved)} 组（已解散）")
        cols_show = [
            'group_id', 'group_name', '主账号_客户编码', '主账号_客户名称',
            '城市', '区县', '解散时间', '解散人', '创建时间', '创建人', '备注',
        ]
        d_show = dissolved[cols_show].copy()
        st.dataframe(d_show, use_container_width=True, hide_index=True,
                      height=min(400, 40 + 35 * len(d_show)))


# ──────────────────────────────────────────
# Tab 3：启发式未建组 — 一键勾选批量建组
# ──────────────────────────────────────────
with tab_heuristic:
    st.caption(
        "💡 系统按「同老板姓名 + 老板电话 ≥ 2 家」启发式发现的潜在伞形组合（**置信度高，直接勾选建组即可**）。\n\n"
        "建组逻辑：勾选行 → 点底部「一键建组」→ 每行建一个新伞形组，"
        "主账号自动选成员里**累计货值最大**的客户。"
    )
    import sqlite3
    from _loaders import DB_PATH

    # 多取 累计货值 / 红包数（给主账号判断 + UI 展示）
    conn = sqlite3.connect(str(DB_PATH))
    try:
        df_h = pd.read_sql("""
            WITH cust_amt AS (
                SELECT 上线客户编码 AS code,
                       COUNT(*) AS n_red,
                       SUM(产品现有分销价) AS amt
                  FROM install_redpack
                 GROUP BY 上线客户编码
            ),
            elig AS (
                SELECT pp.客户编码, pp.公司名称, pp.外部客户名称,
                       pp.地市, pp.区县, pp.老板姓名, pp.老板电话,
                       COALESCE(ca.n_red, 0) AS n_red,
                       COALESCE(ca.amt, 0) AS amt
                  FROM provider_profile pp
                  LEFT JOIN cust_amt ca ON ca.code = pp.客户编码
                 WHERE COALESCE(pp.老板姓名, '') <> ''
                   AND COALESCE(pp.老板电话, '') <> ''
                   AND pp.客户编码 NOT IN (
                       SELECT m.客户编码
                         FROM provider_umbrella_member m
                         JOIN provider_umbrella_group g ON g.group_id = m.group_id
                        WHERE m.撤销时间 IS NULL AND g.解散时间 IS NULL
                   )
            )
            SELECT 老板姓名, 老板电话,
                   COUNT(*) AS 户数,
                   GROUP_CONCAT(客户编码, ',') AS 客户编码列表,
                   GROUP_CONCAT(COALESCE(公司名称, 外部客户名称), ' / ') AS 公司列表,
                   GROUP_CONCAT(DISTINCT 地市) AS 城市列表,
                   SUM(n_red) AS 红包数合计,
                   SUM(amt) / 10000.0 AS 累计货值_万
              FROM elig
             GROUP BY 老板姓名, 老板电话
             HAVING COUNT(*) >= 2
             ORDER BY 累计货值_万 DESC, 户数 DESC
             LIMIT 500
        """, conn)
    finally:
        conn.close()

    if df_h.empty:
        st.success("🎉 所有启发式可疑组合都已经被人工建组。")
    else:
        # scope 过滤（粗：只要城市列表里包含 scope 城市）
        if not is_admin():
            from _auth import get_current_scope
            scope = get_current_scope()
            if scope and scope.get('city'):
                allowed = set(scope['city'])
                mask = df_h['城市列表'].apply(
                    lambda x: bool(set(str(x).split(',')) & allowed) if x else False
                )
                df_h = df_h[mask]

        # 工具栏：快速筛选
        fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 2])
        with fc1:
            min_n = st.number_input(
                "最少户数", min_value=2, max_value=20, value=2, step=1,
                key='_h_min_n',
            )
        with fc2:
            max_n = st.number_input(
                "最多户数（伞形通常不超过 5）",
                min_value=2, max_value=999, value=999, step=1,
                key='_h_max_n',
            )
        with fc3:
            cities_h = sorted(
                set(c.strip() for row in df_h['城市列表'].dropna()
                    for c in str(row).split(',') if c.strip())
            )
            city_filter = st.multiselect(
                "城市", cities_h, placeholder="不选 = 全部",
                key='_h_city',
            )
        with fc4:
            min_amt = st.number_input(
                "最少累计货值(万)", min_value=0.0,
                value=0.0, step=1.0, key='_h_min_amt',
            )

        df_view = df_h.copy()
        df_view = df_view[(df_view['户数'] >= min_n) & (df_view['户数'] <= max_n)]
        if min_amt > 0:
            df_view = df_view[df_view['累计货值_万'] >= min_amt]
        if city_filter:
            df_view = df_view[df_view['城市列表'].apply(
                lambda x: any(c in str(x) for c in city_filter)
            )]

        st.markdown(f"##### 💡 {len(df_view)} 个候选组合（共 {len(df_h)} 个）")

        # 表格视图列
        df_view_show = df_view.copy()
        df_view_show['累计货值_万'] = df_view_show['累计货值_万'].round(2)
        cols_show = [
            '老板姓名', '老板电话', '户数',
            '城市列表', '公司列表',
            '红包数合计', '累计货值_万',
            '客户编码列表',
        ]
        df_view_show = df_view_show[cols_show].reset_index(drop=True)

        # 勾选 + 一键建组
        if can_edit:
            ev = st.dataframe(
                df_view_show,
                use_container_width=True, hide_index=True,
                height=min(500, 40 + 35 * len(df_view_show)),
                selection_mode='multi-row',
                on_select='rerun',
                key='_h_select_df',
                column_config={
                    '户数': st.column_config.NumberColumn('户数', width='small'),
                    '红包数合计': st.column_config.NumberColumn('红包数', width='small'),
                    '累计货值_万': st.column_config.NumberColumn(
                        '累计货值(万)', format='%.2f', width='small',
                    ),
                    '客户编码列表': st.column_config.TextColumn(
                        '客户编码列表', width='medium',
                    ),
                },
            )
            sel_rows = ev.selection.rows if hasattr(ev, 'selection') else []
            n_sel = len(sel_rows)

            ac1, ac2, ac3 = st.columns([3, 3, 2])
            with ac1:
                st.metric("已勾选", f"{n_sel} 行")
            with ac2:
                if n_sel > 0:
                    # 预估即将建组的统计
                    sel_df = df_view.iloc[sel_rows]
                    total_codes = sum(int(r['户数']) for _, r in sel_df.iterrows())
                    total_amt = float(sel_df['累计货值_万'].sum() or 0)
                    st.metric(
                        "预估即将建组",
                        f"{n_sel} 组 / {total_codes} 户 / {total_amt:.1f} 万",
                    )
                else:
                    st.caption("勾选上方表里的行 → 每行建一个组")
            with ac3:
                if st.button(
                    f"☂️ 一键建 {n_sel} 个组" if n_sel > 0 else "☂️ 一键建组",
                    key='_h_bulk_btn',
                    type='primary' if n_sel > 0 else 'secondary',
                    disabled=(n_sel == 0),
                    use_container_width=True,
                ):
                    sel_df = df_view.iloc[sel_rows]
                    with st.spinner(f"建 {n_sel} 个组..."):
                        res = bulk_create_umbrella_groups_from_heuristic(
                            sel_df.to_dict('records'),
                            创建人=current_user(),
                            备注_前缀='启发式批量建组',
                        )
                    msgs = [f"✅ 建组 **{res['n_groups_created']}** 个"]
                    msgs.append(f"·  挂标客户 **{res['n_members_added']}** 个")
                    if res['skipped_already_in_group']:
                        msgs.append(f"·  跳过已在他组 {res['skipped_already_in_group']} 个客户")
                    if res['errors']:
                        msgs.append(f"·  ⚠️ {len(res['errors'])} 行失败")
                    st.success(' '.join(msgs))
                    if res['errors']:
                        with st.expander(f"⚠️ {len(res['errors'])} 行失败明细"):
                            for boss_name, err in res['errors']:
                                st.write(f"- **{boss_name}**: {err}")
                    st.rerun()
        else:
            # 无打标权限只看
            st.dataframe(
                df_view_show,
                use_container_width=True, hide_index=True,
                height=min(500, 40 + 35 * len(df_view_show)),
            )
            st.caption("⛔ 你不是 admin/manager，无法批量建组")

        csv = df_view_show.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            "⬇️ 下载启发式候选 CSV", csv,
            file_name=f"启发式伞形候选_{len(df_view_show)}组.csv", mime='text/csv',
        )
