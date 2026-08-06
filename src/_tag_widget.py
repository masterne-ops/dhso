"""服务商打标组件 V2 — 基于新的 provider_tag_def / assignment 体系

权限：admin / manager 才能打 / 撤销（业务员不能打，包括 scope 内）。

主要 API：
    from _tag_widget import (
        can_mark_tag, render_tag_badges, render_tag_editor,
        render_provider_tag_panel, format_tag_text,
    )

    # 1. 仅展示某服务商的标签 chip（任何角色都能看）
    render_tag_badges('1@xxx')

    # 2. 完整面板：badges + 添加/撤销（仅 admin/manager 可操作）
    render_provider_tag_panel(
        客户编码='1@xxx', 客户名称='杭州XX科技',
        城市='杭州市', 区县='上城区',
        source='page05_radar',
    )

    # 3. 表格行的内联渲染辅助
    format_tag_text(tags) -> '🚫 明确无采购意向 / ⚔️ 竞品 Top'

兼容性：
  - 旧函数 `tag_button` / `tag_table` / `tag_inline_action` 暂保留 deprecation stub，但内部走新 API；
    新代码请用本文件顶部列出的 API。
"""
from __future__ import annotations

import streamlit as st


def can_mark_tag() -> bool:
    """权限判断：仅 admin / manager 能打标 / 撤销（默认全局规则）"""
    try:
        from _auth import get_current_role
        role = get_current_role()
        return role in ('admin', 'manager')
    except Exception:
        return False


# 「打标」权限放开给所有账号的标签（撤销仍仅 admin/manager）
OPEN_ASSIGN_TAGS = {'pending_activation'}


def can_assign_tag(tag_key: str) -> bool:
    """能否给某标签『打标』。OPEN_ASSIGN_TAGS 里的对所有登录账号开放；其余仅 admin/manager。"""
    if tag_key in OPEN_ASSIGN_TAGS:
        return True
    return can_mark_tag()


def can_revoke_tag(tag_key: str = '') -> bool:
    """能否『撤销』某标签。一律仅 admin/manager（含 pending_activation）。"""
    return can_mark_tag()


# ══════════════════════════════════════════════════════
# 数据查询包装
# ══════════════════════════════════════════════════════

def _fetch_tags(客户编码: str) -> list[dict]:
    try:
        from _ai_log import get_provider_tags
        return get_provider_tags(str(客户编码)) or []
    except Exception:
        return []


def _fetch_all_tag_defs() -> list[dict]:
    """所有 enabled 的标签定义（给"添加标签"下拉用）"""
    try:
        from _ai_log import list_tag_defs
        df = list_tag_defs(only_enabled=True)
        return df.to_dict('records') if not df.empty else []
    except Exception:
        return []


# ══════════════════════════════════════════════════════
# 文本格式化（给表格用）
# ══════════════════════════════════════════════════════

def format_tag_text(tags: list[dict], *, max_count: int = 4) -> str:
    """把 tag 列表转成紧凑文本：'🚫 明确无采购意向 / ⚔️ 竞品 Top / ...'

    给 DataFrame 单元格用 — 不能渲染富文本，所以用 emoji + 名字
    """
    if not tags:
        return ''
    parts = []
    for t in tags[:max_count]:
        parts.append(f"{t.get('tag_icon', '')} {t.get('tag_name', t.get('tag_key', ''))}".strip())
    if len(tags) > max_count:
        parts.append(f"…+{len(tags) - max_count}")
    return ' / '.join(parts)


def format_tag_keys(tags: list[dict]) -> str:
    """逗号拼接 tag_key（用于过滤）"""
    if not tags:
        return ''
    return ','.join(t.get('tag_key', '') for t in tags)


# ══════════════════════════════════════════════════════
# 渲染：badges（只展示）
# ══════════════════════════════════════════════════════

def render_tag_badges(客户编码: str, *, fallback: str = '（无标签）',
                       inline: bool = True) -> list[dict]:
    """展示某客户的当前标签 chip 列表。返回 tags（给调用方继续用）"""
    tags = _fetch_tags(客户编码)
    if not tags:
        st.caption(fallback)
        return tags
    # 用 markdown :color[text] badge 渲染
    if inline:
        parts = []
        for t in tags:
            color = t.get('tag_color') or 'gray'
            label = f"{t.get('tag_icon', '')} {t.get('tag_name', '')}".strip()
            parts.append(f":{color}-badge[{label}]")
        st.markdown(' '.join(parts))
    else:
        for t in tags:
            color = t.get('tag_color') or 'gray'
            label = f"{t.get('tag_icon', '')} {t.get('tag_name', '')}".strip()
            st.markdown(
                f":{color}-badge[{label}] "
                f"<span style='color:#888;font-size:0.8em;'>· {t.get('标记来源', '?')} · {t.get('标记人', '?')}</span>",
                unsafe_allow_html=True,
            )
    return tags


# ══════════════════════════════════════════════════════
# 渲染：☂️ 伞形组编辑器（独立于普通标签编辑器）
# ══════════════════════════════════════════════════════

def render_umbrella_group_editor(
    *, 客户编码: str, 客户名称: str = '',
    城市: str = '', 区县: str = '',
    key_prefix: str = '',
    already_in_umbrella: bool = False,
):
    """伞形是组关系：选择/建组 → 一次性挂多个客户"""
    if not can_mark_tag():
        return
    try:
        from _ai_log import (
            get_umbrella_group_of, list_umbrella_groups,
            list_umbrella_group_members, suggest_umbrella_candidates,
            create_umbrella_group, add_umbrella_members,
            remove_umbrella_member, dissolve_umbrella_group,
        )
    except Exception as e:
        st.error(f"伞形组函数不可用：{e}")
        return
    from _auth import current_user
    user = current_user()

    cur_group = get_umbrella_group_of(客户编码)

    with st.container(border=True):
        st.markdown("**☂️ 伞形组 — 一组共享伞形身份**")

        # ─── 已属于某组：展示 + 提供「退组 / 解散组 / 加成员」 ───
        if cur_group:
            gid = cur_group['group_id']
            is_leader = str(cur_group['主账号_客户编码']) == str(客户编码)
            st.caption(
                f"📌 当前已在组 `#{gid}` · 主账号：**{cur_group['主账号_客户名称'] or cur_group['主账号_客户编码']}** "
                + ('（**该客户是主账号**）' if is_leader else '')
            )
            # 成员列表
            mdf = list_umbrella_group_members(gid)
            if not mdf.empty:
                show = mdf[['是否主账号', '客户编码', '客户名称', '城市', '区县',
                             '老板姓名', '老板电话', '红包数', '累计货值_万']].copy()
                show['是否主账号'] = show['是否主账号'].map({1: '👑 主', 0: ''})
                show['累计货值_万'] = show['累计货值_万'].round(2)
                st.dataframe(show, use_container_width=True, hide_index=True,
                              height=min(220, 40 + 35 * len(show)))
            else:
                st.caption("（组内无成员？数据异常，请联系 admin）")

            ac1, ac2, ac3 = st.columns([2, 2, 2])
            with ac1:
                if st.button(
                    "↩️ 把本客户移出此组",
                    key=f"{key_prefix}_leave_btn_{客户编码}",
                    use_container_width=True,
                ):
                    ok = remove_umbrella_member(
                        group_id=gid, 客户编码=str(客户编码),
                        撤销人=user, 撤销原因='页面操作 / 退组',
                    )
                    if ok:
                        st.toast("✅ 已移出本组")
                        st.rerun()
                    else:
                        st.warning("移出失败")
            with ac2:
                if st.button(
                    "💥 解散整个组",
                    key=f"{key_prefix}_dissolve_btn_{客户编码}",
                    use_container_width=True,
                    type='secondary',
                ):
                    if st.session_state.get(f"{key_prefix}_dissolve_confirm_{gid}"):
                        ok = dissolve_umbrella_group(
                            group_id=gid, 解散人=user, 解散原因='页面操作',
                        )
                        if ok:
                            st.toast(f"💥 组 #{gid} 已解散")
                            st.session_state.pop(f"{key_prefix}_dissolve_confirm_{gid}", None)
                            st.rerun()
                    else:
                        st.session_state[f"{key_prefix}_dissolve_confirm_{gid}"] = True
                        st.warning("⚠️ 再点一次确认解散整组（所有成员伞形标签会自动消失）")
            with ac3:
                with st.popover("➕ 给本组加成员", use_container_width=True):
                    new_codes_text = st.text_area(
                        "新成员客户编码（每行一个）",
                        key=f"{key_prefix}_addmem_text_{gid}",
                        placeholder="1@xxx\n2@yyy",
                        height=120,
                    )
                    if st.button("提交", key=f"{key_prefix}_addmem_btn_{gid}",
                                  type='primary'):
                        codes = [c.strip() for c in (new_codes_text or '').splitlines()
                                  if c.strip()]
                        if not codes:
                            st.warning("请输入至少一个客户编码")
                        else:
                            try:
                                res = add_umbrella_members(
                                    group_id=gid, codes=codes, 加入人=user,
                                )
                                st.success(f"✅ 加入 {len(res['added_codes'])} 个 · 跳过 {len(res['skipped_codes'])} 个")
                                st.rerun()
                            except Exception as e:
                                st.error(f"加成员失败：{e}")
            return  # 已在组里就不显示「建新组」入口

        # ─── 未在任何组：显示「建组」入口 ───
        st.caption("📍 此客户当前不在任何伞形组里")

        # 模式 1：加入已有组（如果已有其他组在）
        existing_groups = list_umbrella_groups(active_only=True, limit=500)
        mode_options = ["🆕 建新组（当前客户作主账号）"]
        if not existing_groups.empty:
            mode_options.append("📥 加入已有组")
        mode = st.radio(
            "操作",
            mode_options,
            key=f"{key_prefix}_mode_{客户编码}",
            horizontal=True,
        )

        if mode == "📥 加入已有组" and not existing_groups.empty:
            opts = [(int(r['group_id']),
                     f"#{int(r['group_id'])} {r['group_name']} "
                     f"· {int(r['成员数'])} 成员 · 货值 {float(r['累计货值_万'] or 0):.1f}万")
                    for _, r in existing_groups.iterrows()]
            idx = st.selectbox(
                "选组",
                list(range(len(opts))),
                format_func=lambda i: opts[i][1],
                key=f"{key_prefix}_join_sel_{客户编码}",
            )
            target_gid = opts[idx][0]
            if st.button(
                "➕ 加入本组",
                key=f"{key_prefix}_join_btn_{客户编码}",
                type='primary', use_container_width=True,
            ):
                try:
                    res = add_umbrella_members(
                        group_id=target_gid,
                        codes=[{'客户编码': str(客户编码), '客户名称': 客户名称}],
                        加入人=user,
                    )
                    if res['added_codes']:
                        st.toast(f"✅ 已加入组 #{target_gid}")
                        st.rerun()
                    else:
                        st.info("已在该组中")
                except Exception as e:
                    st.error(f"加入失败：{e}")

        else:
            # 建新组：列候选成员
            candidates_df = suggest_umbrella_candidates(客户编码, limit=30)

            if candidates_df.empty:
                st.warning(
                    "💡 该客户「老板姓名+电话」字段缺失，或没有其他客户与之匹配。"
                    "你可以手动输入要绑组的客户编码（每行一个）。"
                )
                manual_text = st.text_area(
                    "要加入新组的其他客户编码（每行一个；不填则只挂当前客户）",
                    key=f"{key_prefix}_manual_codes_{客户编码}",
                    placeholder="1@xxx\n2@yyy",
                    height=100,
                )
                gname = st.text_input(
                    "组名（默认 = 当前客户名）",
                    value=客户名称 or 客户编码,
                    key=f"{key_prefix}_gname_{客户编码}",
                )
                gremark = st.text_input(
                    "备注（可空）",
                    key=f"{key_prefix}_gremark_{客户编码}",
                    placeholder="如：同一店主控制的店铺群",
                )
                if st.button(
                    "🆕 创建新组并标记伞形",
                    key=f"{key_prefix}_create_solo_{客户编码}",
                    type='primary', use_container_width=True,
                ):
                    extra = [c.strip() for c in (manual_text or '').splitlines() if c.strip()]
                    extra = [{'客户编码': c} for c in extra]
                    try:
                        res = create_umbrella_group(
                            主账号_客户编码=str(客户编码),
                            主账号_客户名称=客户名称 or '',
                            城市=城市, 区县=区县,
                            group_name=gname,
                            创建人=user, 备注=gremark or '',
                            成员明细=extra,
                        )
                        st.success(
                            f"✅ 组 #{res['group_id']} 已创建 "
                            f"· 成员 {len(res['added_codes'])} 个"
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"建组失败：{e}")
            else:
                st.caption(
                    f"💡 启发式发现 **{len(candidates_df)}** 个候选成员"
                    "（同老板姓名+电话）— 勾选要纳入本组的："
                )
                # 用 dataframe 多选
                cand_show = candidates_df[[
                    '客户编码', '客户名称', '城市', '区县',
                    '老板姓名', '老板电话', '红包数', '累计货值_万', 'in_group_id',
                ]].copy()
                cand_show['累计货值_万'] = cand_show['累计货值_万'].round(2)
                cand_show['已在他组'] = cand_show['in_group_id'].apply(
                    lambda x: f"⚠️ 组 #{int(x)}" if x else '✅ 可加入'
                )
                cand_show = cand_show.drop(columns=['in_group_id'])

                ev = st.dataframe(
                    cand_show,
                    use_container_width=True, hide_index=True,
                    height=min(280, 40 + 35 * len(cand_show)),
                    selection_mode='multi-row',
                    on_select='rerun',
                    key=f"{key_prefix}_cand_df_{客户编码}",
                )
                selected_rows = ev.selection.rows if hasattr(ev, 'selection') else []
                n_sel = len(selected_rows)

                # 额外手动输入 codes（候选外的）
                extra_text = st.text_area(
                    "🔍 还要加上的客户编码（候选之外，每行一个，可空）",
                    key=f"{key_prefix}_extra_codes_{客户编码}",
                    placeholder="比如启发式没识别但你知道是一伙的：1@xxx",
                    height=70,
                )

                gc1, gc2 = st.columns([3, 2])
                with gc1:
                    gname2 = st.text_input(
                        "组名",
                        value=客户名称 or 客户编码,
                        key=f"{key_prefix}_gname2_{客户编码}",
                    )
                with gc2:
                    gremark2 = st.text_input(
                        "备注（可空）",
                        key=f"{key_prefix}_gremark2_{客户编码}",
                    )

                if st.button(
                    f"🆕 建组 + 标记本客户与 {n_sel} 个候选 + 手动追加",
                    key=f"{key_prefix}_create_btn_{客户编码}",
                    type='primary', use_container_width=True,
                ):
                    # 组装成员
                    members = []
                    if n_sel > 0:
                        sel_df = candidates_df.iloc[selected_rows]
                        # 警告已在他组的
                        in_other = sel_df[sel_df['in_group_id'].notna()]
                        if not in_other.empty:
                            st.warning(
                                f"⚠️ {len(in_other)} 个候选已在其他组里 — "
                                "提交后会自动从原组移出，加入本新组。"
                            )
                        for _, r in sel_df.iterrows():
                            members.append({
                                '客户编码': str(r['客户编码']),
                                '客户名称': str(r['客户名称'] or ''),
                            })
                    # 手动追加
                    extra = [c.strip() for c in (extra_text or '').splitlines() if c.strip()]
                    for c in extra:
                        if c != str(客户编码):
                            members.append({'客户编码': c})
                    try:
                        res = create_umbrella_group(
                            主账号_客户编码=str(客户编码),
                            主账号_客户名称=客户名称 or '',
                            城市=城市, 区县=区县,
                            group_name=gname2,
                            创建人=user, 备注=gremark2 or '',
                            成员明细=members,
                        )
                        st.success(
                            f"✅ 组 #{res['group_id']} 已创建 "
                            f"· 加入 {len(res['added_codes'])} 个客户"
                            + (f" · 跳过 {len(res['skipped_codes'])} 个"
                               if res['skipped_codes'] else '')
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"建组失败：{e}")


# ══════════════════════════════════════════════════════
# 渲染：添加 / 撤销标签的编辑器
# ══════════════════════════════════════════════════════

def render_tag_editor(
    *, 客户编码: str, 客户名称: str = '',
    城市: str = '', 区县: str = '',
    source: str = '', key_prefix: str = '',
    current_tags: list[dict] | None = None,
):
    """对单个客户的标签做添加 / 撤销操作 — 只有 admin/manager 看得到

    特殊处理：
      - tag_key == 'umbrella' → 跳到伞形组编辑器（必须建组/加组）
      - 其他 tag → 普通"备注+打标"流程
    """
    # 业务员只能打「开放标签」(pending_activation)；admin/manager 能打全部 + 撤销
    _can_revoke = can_mark_tag()
    try:
        from _ai_log import assign_tag, revoke_assignment
    except Exception as e:
        st.error(f"标签函数不可用：{e}")
        return

    from _auth import current_user
    user = current_user()
    if current_tags is None:
        current_tags = _fetch_tags(客户编码)

    all_defs = _fetch_all_tag_defs()
    # 把当前已挂的 tag_key 标出来
    current_keys = {t['tag_key'] for t in current_tags}

    # ─── 普通标签编辑器（更常用 → 放上面）───
    # 行内布局：左侧选择标签 + 备注 + 添加按钮；右侧撤销当前任一 manual/hybrid 标签
    with st.container(border=True):
        st.markdown("**🏷️ 添加 / 撤销标签**")
        c1, c2, c3 = st.columns([3, 4, 2])
        with c1:
            # 伞形从下拉里剥离（下面有专门的组编辑器）
            avail = [d for d in all_defs
                      if d['tag_key'] not in current_keys
                      and d['tag_key'] != 'umbrella'
                      and can_assign_tag(d['tag_key'])]
            if not avail:
                st.caption("（该客户已挂所有可用标签）")
                add_key = None
            else:
                labels = [f"{d['tag_icon']} {d['tag_name']}" for d in avail]
                idx = st.selectbox(
                    "选要打的标签",
                    list(range(len(avail))),
                    format_func=lambda i: labels[i],
                    key=f"{key_prefix}_te_add_sel_{客户编码}",
                    label_visibility='collapsed',
                )
                add_key = avail[idx]['tag_key']
                # source_type 提示
                src_type = avail[idx]['source_type']
                src_hint = {
                    'heuristic': '⚠️ 规则自动算 — 人工挂的会跟规则并存（不冲突）',
                    'imported': '⚠️ 外部导入维护 — 通常不需人工挂',
                    'manual': '✋ 人工标签',
                    'hybrid': '🔀 业务可补打',
                }.get(src_type, '')
                if src_hint:
                    st.caption(src_hint)
        with c2:
            remark = st.text_input(
                "备注（可空）",
                key=f"{key_prefix}_te_remark_{客户编码}",
                placeholder="如：电话沟通三次，明确说只用海康",
                label_visibility='collapsed',
            )
        with c3:
            if st.button(
                "➕ 打标",
                key=f"{key_prefix}_te_add_btn_{客户编码}",
                type='primary',
                use_container_width=True,
                disabled=not add_key,
            ):
                try:
                    assign_tag(
                        客户编码=str(客户编码), tag_key=add_key,
                        客户名称=客户名称 or '',
                        城市=城市 or '', 区县=区县 or '',
                        标记来源=source, 标记人=user, 备注=remark or '',
                    )
                    st.toast(f"✅ 已为 {客户名称 or 客户编码} 打上标签")
                    st.rerun()
                except Exception as e:
                    st.error(f"打标失败：{e}")

        # 列出当前可撤销的标签（仅 admin/manager 可见；业务员不能撤任何标签，含 pending_activation）
        revocables = ([t for t in current_tags if t.get('origin') in ('manual', 'hybrid')]
                      if _can_revoke else [])
        if revocables:
            st.markdown("---")
            st.caption("🗑️ 当前可撤销的人工标签：")
            for t in revocables:
                rc1, rc2, rc3 = st.columns([5, 4, 2])
                with rc1:
                    st.markdown(
                        f":{t.get('tag_color', 'gray')}-badge"
                        f"[{t.get('tag_icon', '')} {t.get('tag_name', '')}]"
                        f" `{t.get('标记来源', '?')}` · {t.get('标记人', '?')}"
                    )
                with rc2:
                    rev_reason = st.text_input(
                        f"撤销原因（{t['tag_key']}）",
                        key=f"{key_prefix}_te_rev_reason_{客户编码}_{t['tag_key']}",
                        label_visibility='collapsed',
                        placeholder="撤销原因",
                    )
                with rc3:
                    if st.button(
                        "↩️ 撤销",
                        key=f"{key_prefix}_te_rev_btn_{客户编码}_{t['tag_key']}",
                        type='secondary',
                        use_container_width=True,
                    ):
                        try:
                            ok = revoke_assignment(
                                客户编码=str(客户编码), tag_key=t['tag_key'],
                                撤销人=user, 撤销原因=rev_reason or '',
                            )
                            if ok:
                                st.toast(f"↩️ 已撤销 {t['tag_name']}")
                                st.rerun()
                            else:
                                st.warning("撤销失败（可能已被他人撤销）")
                        except Exception as e:
                            st.error(f"撤销失败：{e}")

    # ─── ☂️ 伞形组编辑器（次常用 → 放在普通标签编辑器下方）───
    render_umbrella_group_editor(
        客户编码=客户编码, 客户名称=客户名称,
        城市=城市, 区县=区县,
        key_prefix=f"{key_prefix}_umb",
        already_in_umbrella='umbrella' in current_keys,
    )


# ══════════════════════════════════════════════════════
# 综合面板：badges + 编辑（适合 page 05 首行展示）
# ══════════════════════════════════════════════════════

def render_provider_tag_panel(
    *, 客户编码: str, 客户名称: str = '',
    城市: str = '', 区县: str = '',
    source: str = '', key_prefix: str = 'tagpanel',
):
    """组合面板：上行展示当前 chip，下行（仅 admin/manager 可见）编辑器"""
    tags = _fetch_tags(客户编码)

    # 上行：chip 展示
    if tags:
        parts = []
        for t in tags:
            color = t.get('tag_color') or 'gray'
            label = f"{t.get('tag_icon', '')} {t.get('tag_name', '')}".strip()
            parts.append(f":{color}-badge[{label}]")
        st.markdown(' '.join(parts))
    else:
        st.caption("🏷️ 暂无标签")

    # 下行：编辑器。admin/manager 看完整（打全部 + 撤销）；
    # 业务员也能展开，但只能打「开放标签」(本月待激活)、无撤销区
    _ed_label = "🛠️ 添加 / 撤销标签" if can_mark_tag() else "➕ 打标（本月待激活客户）"
    with st.expander(_ed_label, expanded=False):
        render_tag_editor(
            客户编码=客户编码, 客户名称=客户名称,
            城市=城市, 区县=区县,
            source=source, key_prefix=key_prefix,
            current_tags=tags,
        )

    return tags


# ══════════════════════════════════════════════════════
# 表格辅助：批量给一组 DataFrame 加「标签」列
# ══════════════════════════════════════════════════════

def attach_tags_column(df, *, code_col: str = '客户编码',
                       column_name: str = '🏷️ 标签',
                       max_count: int = 4,
                       only_keys: list[str] | None = None):
    """给 DataFrame 加一列「标签」文本（'🚫 明确无采购意向 / ⚔️ 竞品 Top'）

    Args:
        df: 要加列的 DataFrame
        code_col: 客户编码列名
        column_name: 新加的列名
        max_count: 单单元格最多显示几个标签，超出显示 …+N
        only_keys: 只显示指定 tag_key 列表（None=全部）；用于场景过滤
    """
    if df is None or df.empty or code_col not in df.columns:
        return df
    try:
        from _ai_log import get_provider_tags_batch
    except Exception:
        return df
    codes = df[code_col].dropna().astype(str).unique().tolist()
    tag_map = get_provider_tags_batch(codes)
    def _fmt(code):
        ts = tag_map.get(str(code), [])
        if only_keys:
            ts = [t for t in ts if t.get('tag_key') in only_keys]
        return format_tag_text(ts, max_count=max_count)
    out = df.copy()
    out[column_name] = out[code_col].astype(str).map(_fmt)
    return out


# ══════════════════════════════════════════════════════
# 兼容旧 API（page 05 / page 06 已有调用）
# ══════════════════════════════════════════════════════

def tag_button(
    *,
    客户编码: str,
    客户名称: str,
    城市: str = '',
    区县: str = '',
    source: str = '',
    key_prefix: str = '',
    inline: bool = True,
):
    """兼容旧 API — 行内一个 popover 触发的打标 UI

    新代码请改用 render_provider_tag_panel。
    """
    if not can_mark_tag():
        return
    tags = _fetch_tags(客户编码)
    has_active_closed = any(t['tag_key'] == 'closed' for t in tags)
    btn_label = '🚫' if has_active_closed else '🏷️'
    btn_help = (
        '已标记，点击查看 / 撤销'
        if has_active_closed else
        f'打标 / 撤销 — {客户名称}'
    )
    with st.popover(btn_label if inline else f"{btn_label} 标签", help=btn_help):
        st.markdown(f"**🏷️ 「{客户名称 or 客户编码}」**")
        if tags:
            parts = [
                f":{t.get('tag_color', 'gray')}-badge[{t.get('tag_icon', '')} {t.get('tag_name', '')}]"
                for t in tags
            ]
            st.markdown(' '.join(parts))
        else:
            st.caption("当前无标签")
        st.markdown("---")
        render_tag_editor(
            客户编码=客户编码, 客户名称=客户名称,
            城市=城市, 区县=区县,
            source=source, key_prefix=key_prefix,
            current_tags=tags,
        )


def tag_inline_action(
    candidates: list,
    *,
    source: str = '',
    key_prefix: str = '',
):
    """兼容旧 API — 表格下方放一个 select-and-tag widget"""
    if not can_mark_tag():
        return
    if not candidates:
        return
    st.markdown("##### 🏷️ 给客户打标")
    st.caption(
        "manager / admin 可在此把列表里的某个客户挂上任意标签 — "
        "标了之后所有 page（派单 / 任务 / 全景图等）会感知。"
    )
    options = [(str(c[0]), c[1] if len(c) > 1 else '',
                c[2] if len(c) > 2 else '', c[3] if len(c) > 3 else '')
               for c in candidates]
    labels = [f"{name} ({code})" for code, name, *_ in options]
    sel = st.selectbox(
        "选客户",
        list(range(len(options))),
        format_func=lambda i: labels[i],
        key=f"{key_prefix}_tag_select",
    )
    sel_code, sel_name, sel_city, sel_district = options[sel]
    tag_button(
        客户编码=sel_code, 客户名称=sel_name,
        城市=sel_city, 区县=sel_district,
        source=source, key_prefix=f"{key_prefix}_inline_act",
        inline=False,
    )


def tag_table(df, *args, **kwargs):
    """⚠️ 已废弃 — 旧 page 06 批量打标 UI

    现在打标统一在 page 05 服务商画像 做（"先理解再打"）。
    此函数保留为 deprecation stub，仅展示无操作的表格。
    """
    if df is None or df.empty:
        st.info("（无数据）")
        return
    extra_cols = kwargs.get('extra_cols')
    height = kwargs.get('height', 400)
    show = df[[c for c in extra_cols if c in df.columns]] if extra_cols else df
    st.dataframe(show, use_container_width=True, hide_index=True, height=height)
