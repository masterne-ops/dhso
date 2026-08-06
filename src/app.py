#!/usr/bin/env python3
"""
SO 数据分析平台 — 入口

用 st.navigation 做分组侧边栏。各 page 的 file/title/url/group 都在 `_page_registry.PAGES` 里。
首次进入会按角色过滤可见的 page。

主页（数据导入与概览）的实际内容在 `_home.py`。
"""

import streamlit as st

from _auth import require_auth, get_current_role, render_user_badge

st.set_page_config(page_title="SO 数据分析", layout="wide")
require_auth()

# ──────────────────────────────────────────────────
# 用户 + 角色（决定哪些 page 可见）
# ──────────────────────────────────────────────────
role = get_current_role() or ''  # 未登录时空


# ──────────────────────────────────────────────────
# 系统首次部署：DB 还没有任何用户 → 让所有人可以访问"用户与权限"页 bootstrap admin
# ──────────────────────────────────────────────────
from _ai_log import list_users  # noqa: E402

try:
    bootstrap_mode = list_users().empty
except Exception:
    bootstrap_mode = True


# ──────────────────────────────────────────────────
# 业务 page 注册（按 group 分组，按角色过滤）
# ──────────────────────────────────────────────────
from _page_registry import PAGES  # noqa: E402

# 产品经理：数据范围按「页面分配」——只看分配给他的页面白名单（scope_type='page'）。
# 循环外取一次 scope，避免逐页查 DB。
pm_pages = None
if role == 'product_manager':
    from _auth import get_current_scope
    pm_pages = set(get_current_scope().get('page', []))

groups: dict[str, list] = {}
for p in PAGES:
    # 按角色过滤
    req_role = p.get('role')
    if req_role == 'admin' and role != 'admin':
        # Bootstrap 例外：DB 没用户时，「用户与权限」对所有人可见（用于创建首个 admin）
        if bootstrap_mode and p.get('url') == 'users':
            pass
        else:
            continue

    # 产品经理：普通页里也只显示分配给他的白名单页面；default 主页(智能搜索)始终保留——
    # 避免产品经理未分配页面时侧边栏为空导致 st.navigation 报错，且需要一个着陆页
    if pm_pages is not None and not p.get('default') and p.get('url') not in pm_pages:
        continue

    page_obj = st.Page(
        p['file'],
        title=p['title'],
        url_path=p['url'],
        default=bool(p.get('default')),
    )
    groups.setdefault(p['group'], []).append(page_obj)


# ──────────────────────────────────────────────────
# 渲染分组导航
# ──────────────────────────────────────────────────
selected = st.navigation(groups, position='sidebar', expanded=True)

# 侧边栏底部：用户身份 + 登出按钮
render_user_badge()

selected.run()
