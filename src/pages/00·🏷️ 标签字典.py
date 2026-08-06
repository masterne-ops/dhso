#!/usr/bin/env python3
"""🏷️ 服务商标签字典（admin only）

功能：
  - 管理标签定义（增 / 改名 / 启停 / 改图标颜色 / 排序）
  - 不允许删除（保留历史 assignment 引用） — 改为禁用
  - 查看每个标签当前覆盖的服务商数

设计：
  - 6 个内置标签首次启动自动 seed
  - tag_key 锁定（PK），admin 只能改 tag_name
  - heuristic 标签（伞形 / 低效签约）数量随数据库变化实时计算
  - imported 标签数量受外部数据导入控制
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, current_user, is_admin, get_current_role  # noqa: E402
from _ai_log import (  # noqa: E402
    list_tag_defs, get_tag_def, create_tag, rename_tag, update_tag_def,
    provider_tag_summary, providers_by_tag, rebuild_provider_tags_view,
)

require_auth()

st.markdown("### 🏷️ 服务商标签字典")
st.caption(
    "管理服务商身份/状态标签 — 控制各页面如何识别和过滤客户。"
    "可改名 / 改图标颜色 / 启停 / 排序；**不能删除**（保留历史引用）"
)

# admin only
if not is_admin():
    st.error(f"⛔ 仅管理员可访问。当前用户: `{current_user()}`，角色: `{get_current_role()}`")
    st.stop()

# ──────────────────────────────────────────────
# 总览 — 各标签覆盖数
# ──────────────────────────────────────────────
st.markdown("##### 📊 当前覆盖")
try:
    summary = provider_tag_summary()
except Exception as e:
    st.error(f"读取标签覆盖失败：{e}")
    st.stop()

if summary.empty:
    st.info("（标签字典为空）")
else:
    cols = st.columns(len(summary))
    for i, (_, r) in enumerate(summary.iterrows()):
        with cols[i]:
            label = f"{r['tag_icon']} {r['tag_name']}"
            value = f"{int(r['n_providers']):,}"
            st.metric(label, value)
            # 标签来源
            src_emoji = {
                'manual': '✋人工',
                'imported': '📥导入',
                'heuristic': '🧠规则',
                'hybrid': '🔀人工+导入',
            }.get(r['source_type'], r['source_type'])
            st.caption(src_emoji)

st.divider()


# ──────────────────────────────────────────────
# 标签表 + 编辑
# ──────────────────────────────────────────────
st.markdown("##### 📋 标签清单")
defs = list_tag_defs(only_enabled=False)

if defs.empty:
    st.info("（暂无标签）")
else:
    # 展示 + 编辑
    display_df = defs[['tag_key', 'tag_name', 'tag_icon', 'tag_color',
                        'source_type', 'enabled', 'sort_order', 'tag_desc',
                        'created_at', 'updated_at', 'updated_by']].copy()
    display_df['enabled'] = display_df['enabled'].map({1: '✅', 0: '⛔'})
    st.dataframe(display_df, use_container_width=True, hide_index=True,
                 height=min(450, 40 + 35 * len(display_df)))

st.divider()


# ──────────────────────────────────────────────
# 编辑现有标签
# ──────────────────────────────────────────────
st.markdown("##### ✏️ 编辑标签")
cols = st.columns([2, 5])
with cols[0]:
    if defs.empty:
        st.info("无可编辑标签")
        edit_key = None
    else:
        labels = [f"{r['tag_icon']} {r['tag_name']} ({r['tag_key']})"
                  for _, r in defs.iterrows()]
        idx = st.selectbox(
            "选择标签",
            list(range(len(defs))),
            format_func=lambda i: labels[i],
            key='_tag_edit_sel',
        )
        edit_key = defs.iloc[idx]['tag_key']

with cols[1]:
    if edit_key:
        d = get_tag_def(edit_key)
        if not d:
            st.warning("标签不存在")
        else:
            with st.form(f'_edit_form_{edit_key}'):
                st.markdown(f"**编辑：`{edit_key}` (source_type: `{d['source_type']}`)**")
                c1, c2, c3 = st.columns([3, 2, 2])
                with c1:
                    new_name = st.text_input(
                        "中文名（可改）",
                        value=d['tag_name'] or '',
                        key=f'_e_name_{edit_key}',
                    )
                with c2:
                    new_icon = st.text_input(
                        "图标 emoji",
                        value=d['tag_icon'] or '',
                        key=f'_e_icon_{edit_key}',
                    )
                with c3:
                    color_opts = ['red', 'orange', 'yellow', 'green',
                                  'blue', 'violet', 'gray']
                    cur_color = d['tag_color'] or 'gray'
                    new_color = st.selectbox(
                        "颜色",
                        color_opts,
                        index=color_opts.index(cur_color) if cur_color in color_opts else len(color_opts) - 1,
                        key=f'_e_color_{edit_key}',
                    )
                c4, c5 = st.columns([4, 1])
                with c4:
                    new_desc = st.text_input(
                        "描述",
                        value=d['tag_desc'] or '',
                        key=f'_e_desc_{edit_key}',
                    )
                with c5:
                    new_sort = st.number_input(
                        "排序",
                        min_value=0, max_value=9999,
                        value=int(d['sort_order'] or 100),
                        step=10,
                        key=f'_e_sort_{edit_key}',
                    )
                new_enabled = st.checkbox(
                    "启用（取消勾选 = 禁用，不删除）",
                    value=bool(d['enabled']),
                    key=f'_e_en_{edit_key}',
                )
                save_btn = st.form_submit_button(
                    "💾 保存修改", type='primary',
                )
                if save_btn:
                    changed = []
                    user = current_user()
                    # 改名
                    if new_name.strip() and new_name.strip() != (d['tag_name'] or ''):
                        try:
                            rename_tag(tag_key=edit_key, new_tag_name=new_name.strip(),
                                       updated_by=user)
                            changed.append(f"名称: {d['tag_name']} → {new_name.strip()}")
                        except Exception as e:
                            st.error(f"改名失败：{e}")
                    # 其他字段
                    upd_kwargs = {'tag_key': edit_key, 'updated_by': user}
                    if (new_icon or '') != (d['tag_icon'] or ''):
                        upd_kwargs['tag_icon'] = new_icon or ''
                        changed.append(f"图标: {d['tag_icon']} → {new_icon}")
                    if new_color != (d['tag_color'] or 'gray'):
                        upd_kwargs['tag_color'] = new_color
                        changed.append(f"颜色: {d['tag_color']} → {new_color}")
                    if (new_desc or '') != (d['tag_desc'] or ''):
                        upd_kwargs['tag_desc'] = new_desc or ''
                        changed.append("描述")
                    if int(new_sort) != int(d['sort_order'] or 100):
                        upd_kwargs['sort_order'] = int(new_sort)
                        changed.append(f"排序: {d['sort_order']} → {int(new_sort)}")
                    if bool(new_enabled) != bool(d['enabled']):
                        upd_kwargs['enabled'] = 1 if new_enabled else 0
                        changed.append("启用状态: " + ("开启" if new_enabled else "禁用"))
                    if len(upd_kwargs) > 2:  # 除 tag_key/updated_by 外有改动
                        update_tag_def(**upd_kwargs)
                    if changed:
                        st.success("✅ 已保存：" + "；".join(changed))
                        st.rerun()
                    else:
                        st.info("（未改动任何字段）")

st.divider()


# ──────────────────────────────────────────────
# 新建标签
# ──────────────────────────────────────────────
st.markdown("##### ➕ 新建标签")
with st.form('_new_tag_form', clear_on_submit=True):
    c1, c2, c3 = st.columns([2, 3, 2])
    with c1:
        new_key = st.text_input(
            "tag_key（ASCII 小写+下划线）",
            placeholder="如 high_value_potential",
            help="机器标识，创建后不可改",
        )
    with c2:
        new_name_n = st.text_input(
            "中文名（可改）",
            placeholder="如 高潜大客户",
        )
    with c3:
        new_icon_n = st.text_input(
            "图标 emoji",
            placeholder="🌟",
        )
    c4, c5, c6 = st.columns([2, 4, 2])
    with c4:
        new_color_n = st.selectbox(
            "颜色",
            ['red', 'orange', 'yellow', 'green', 'blue', 'violet', 'gray'],
            index=3,
        )
    with c5:
        new_desc_n = st.text_input(
            "描述（可选）",
            placeholder="什么样的服务商打这个标签？",
        )
    with c6:
        new_sort_n = st.number_input(
            "排序", min_value=0, max_value=9999, value=100, step=10,
        )
    submit = st.form_submit_button("➕ 创建标签", type='primary')
    if submit:
        if not new_key or not new_name_n:
            st.error("tag_key 和中文名都必填")
        else:
            try:
                create_tag(
                    tag_key=new_key.strip().lower(),
                    tag_name=new_name_n.strip(),
                    tag_icon=new_icon_n.strip(),
                    tag_color=new_color_n,
                    tag_desc=new_desc_n.strip(),
                    source_type='manual',
                    sort_order=int(new_sort_n),
                    created_by=current_user(),
                )
                st.success(f"✅ 标签 `{new_key}` 已创建")
                st.rerun()
            except Exception as e:
                st.error(f"创建失败：{e}")

st.divider()


# ──────────────────────────────────────────────
# 重建视图（数据导入后用）
# ──────────────────────────────────────────────
st.markdown("##### 🔄 维护")
mc1, mc2 = st.columns([1, 3])
with mc1:
    if st.button("🔄 重建标签视图", help="导入新数据后调用，重新计算 伞形/低效签约 标签"):
        ok = rebuild_provider_tags_view()
        if ok:
            st.success("✅ provider_tags_v 视图已重建")
        else:
            st.warning("⚠️ 缺依赖表（vest_account / closed_provider / provider_contract 等）")
with mc2:
    st.caption(
        "**说明：**\n"
        "- 🧠 规则标签（伞形 / 低效签约）：基于数据库当前数据实时计算\n"
        "- 📥 导入标签（马甲 / 竞品 Top）：由「系统」组的数据导入页面维护\n"
        "- ✋ 人工标签（下月重点 / 自定义）：由各业务页面的「打标」按钮维护\n"
        "- 🔀 混合（明确无采购意向）：业务方 excel 导入 + 各页面人工补打"
    )


# ──────────────────────────────────────────────
# 展开某标签的服务商列表
# ──────────────────────────────────────────────
st.divider()
st.markdown("##### 🔍 浏览某标签下的服务商")
defs_enabled = list_tag_defs(only_enabled=True)
if defs_enabled.empty:
    st.info("（无启用标签）")
else:
    bc1, bc2 = st.columns([2, 5])
    with bc1:
        labels_b = [f"{r['tag_icon']} {r['tag_name']}"
                    for _, r in defs_enabled.iterrows()]
        idx_b = st.selectbox(
            "标签",
            list(range(len(defs_enabled))),
            format_func=lambda i: labels_b[i],
            key='_tag_browse_sel',
        )
        browse_key = defs_enabled.iloc[idx_b]['tag_key']
    with bc2:
        st.caption(f"显示 `{browse_key}` 下的所有服务商")

    pdf = providers_by_tag(browse_key, limit=5000)
    st.caption(f"共 {len(pdf):,} 家")
    if not pdf.empty:
        st.dataframe(pdf, use_container_width=True, hide_index=True, height=400)
