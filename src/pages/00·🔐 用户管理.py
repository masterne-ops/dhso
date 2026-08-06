#!/usr/bin/env python3
"""🔐 用户管理（admin only）

功能：
  - 用户列表 + 创建/重置密码/启停用/删除
  - 数据权限（scope）配置：地市/区县/业务员/代理商
  - 一键从 salesperson_scope 表同步业务员 scope
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, current_user, is_admin, get_current_role  # noqa: E402
from _loaders import DB_PATH  # noqa: E402
from _ai_log import (  # noqa: E402
    upsert_user, delete_user, list_users,
    set_user_scope, get_user_scope, get_user_role,
    list_data_salespeople, list_unbound_salesperson_users,
    set_bound_salesperson,
)
from _monthly_city_report import list_cities  # noqa: E402
from _page_registry import PAGES  # noqa: E402

require_auth()

st.markdown("### 🔐 用户管理")

# 仅 admin 可访问；如果还没初始化用户（DB 空）允许 bootstrap 创建首个 admin
users_df = list_users()
need_bootstrap = users_df.empty

if not need_bootstrap:
    if not is_admin():
        st.error(f"⛔ 仅管理员可访问。当前用户: `{current_user()}`，角色: `{get_current_role()}`")
        st.stop()
else:
    st.warning("📦 系统首次初始化：还没有任何用户。请先创建第一个 admin 账号。")

# ──────────────────────────────────────────────
# Bootstrap：首个 admin
# ──────────────────────────────────────────────
if need_bootstrap:
    st.markdown("#### 🌱 创建首个 admin")
    with st.form('_bootstrap_form'):
        col1, col2 = st.columns(2)
        with col1:
            boot_user = st.text_input('用户名', value='admin')
            boot_pwd = st.text_input('初始密码', type='password')
        with col2:
            boot_pwd2 = st.text_input('再次输入密码', type='password')
            boot_name = st.text_input('显示名（可空）', value='超级管理员')
        if st.form_submit_button('🌱 创建', type='primary'):
            if not boot_user or not boot_pwd:
                st.error('用户名和密码必填')
            elif boot_pwd != boot_pwd2:
                st.error('两次密码不一致')
            else:
                upsert_user(username=boot_user, password=boot_pwd, role='admin',
                            full_name=boot_name, enabled=True)
                st.success(f'✅ 已创建 admin `{boot_user}`，请重新登录后再分配其他用户。')
                st.info('提示：现在退出登录，再用新账号登录。')
                from _auth import logout
                logout()
                import time
                time.sleep(0.3)
                st.rerun()
    st.stop()


# ──────────────────────────────────────────────
# 用户列表
# ──────────────────────────────────────────────
tab_list, tab_new, tab_scope, tab_api = st.tabs([
    "👥 用户列表",
    "➕ 新建用户",
    "🔑 权限配置",
    "🔌 数据接口账号",
])

with tab_list:
    # ── 顶部健康度警示：未绑定业务员的 salesperson 账号 ──
    unbound = list_unbound_salesperson_users()
    if not unbound.empty:
        st.error(
            f"⚠️ **{len(unbound)} 个 salesperson 账号未绑定业务员**，"
            f"这些账号在「跑动任务管理 / 我的任务」里看不到自己的客户："
            f"`{', '.join(unbound['username'].tolist())}`"
        )
        st.caption("👉 在「单用户操作」里给他们设「绑定业务员」字段。")

    st.markdown("#### 当前所有用户")
    show_users = users_df[['username', 'role', 'full_name', 'bound_salesperson',
                            'enabled', 'created_at', 'last_login', '备注']].copy()
    # 用 emoji 让绑定状态一目了然
    def _bind_status(row):
        if row['role'] in ('admin', 'manager', 'guest'):
            return '—'  # 不需要绑定
        bs = row.get('bound_salesperson')
        if bs:
            return f"✅ {bs}"
        return "❌ 未绑定"
    show_users['绑定业务员'] = show_users.apply(_bind_status, axis=1)
    show_users = show_users[['username', 'role', 'full_name', '绑定业务员',
                              'enabled', 'created_at', 'last_login', '备注']]
    st.dataframe(show_users, use_container_width=True, hide_index=True)

    st.markdown("##### 单用户操作")
    sel = st.selectbox("选用户", users_df['username'].tolist(), key='ops_user')
    if sel:
        u = users_df[users_df['username'] == sel].iloc[0]
        # 🔑 widget key 必须包含 sel — 否则切换用户时 streamlit 会用 session_state 里
        #    上一个用户的值覆盖我们 pass 的 index/value 默认值
        k = f"__{sel}"
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _roles = ['admin', 'manager', 'product_manager', 'salesperson', 'guest']
            new_role = st.selectbox('角色', _roles,
                                    index=_roles.index(u['role']) if u['role'] in _roles
                                    else _roles.index('salesperson'),
                                    key=f'ops_role{k}')
        with c2:
            new_enabled = st.checkbox('启用', value=bool(u['enabled']), key=f'ops_enabled{k}')
        with c3:
            new_name = st.text_input('显示名', value=u['full_name'] or '', key=f'ops_name{k}')
        with c4:
            new_remark = st.text_input('备注', value=u['备注'] or '', key=f'ops_remark{k}')

        # ── 绑定业务员 ──
        data_sps = list_data_salespeople()
        cur_bs = (u.get('bound_salesperson') or '')
        # 当前绑定值可能不在 data_sps 里（如业务员离职被移除）— 显式插入
        sp_options = ['（不绑定）'] + data_sps
        if cur_bs and cur_bs not in data_sps:
            sp_options = ['（不绑定）', f'{cur_bs} ⚠️ 此名在 salesperson_scope 里找不到'] + data_sps
        if cur_bs:
            cur_label = next((o for o in sp_options if o.startswith(cur_bs)), '（不绑定）')
            idx_default = sp_options.index(cur_label)
        else:
            idx_default = 0
        _bind_required_hint = {
            'salesperson': '必填',
            'manager':     '可选（自己也跑客户时建议填）',
            'admin':       '可选',
            'guest':       '通常不填',
        }.get(new_role, '可选')
        new_bs = st.selectbox(
            f"🧑‍💼 绑定业务员（{_bind_required_hint}）  · 当前 `{cur_bs or '未绑定'}`",
            sp_options,
            index=idx_default,
            key=f'ops_bind_sp{k}',
            help=(
                "把这个账号绑定到业务数据里的「业务员」字段，绑定后：\n"
                "• salesperson 才能在「跑动任务管理 / 我的任务」看到自己客户\n"
                "• manager 绑定后也能用「我的任务」查自己的客户（如果 manager 同时也跑客户）\n"
                "• admin / guest 通常不需要绑定"
            ),
        )
        new_bs_val = '' if new_bs.startswith('（不绑定）') else new_bs.split(' ⚠️')[0]

        c5, c6, c7 = st.columns(3)
        with c5:
            if st.button('💾 保存修改', key=f'ops_save{k}', type='primary'):
                if new_role == 'salesperson' and not new_bs_val:
                    st.warning(
                        f"⚠️ salesperson 角色建议绑定业务员，否则该用户"
                        f"在任务系统里看不到自己的客户。仍保存？"
                    )
                upsert_user(username=sel, role=new_role, full_name=new_name,
                            enabled=new_enabled, 备注=new_remark,
                            bound_salesperson=new_bs_val)
                st.success('✅ 已保存')
                st.rerun()
        with c6:
            with st.popover('🔑 重置密码'):
                new_pwd = st.text_input('新密码', type='password', key=f'ops_newpwd{k}')
                if st.button('重置', key=f'ops_resetpwd_btn{k}',
                             disabled=not new_pwd):
                    upsert_user(username=sel, password=new_pwd, role=new_role,
                                full_name=new_name, enabled=new_enabled,
                                bound_salesperson=new_bs_val)
                    # 改密码 → 强制踢掉所有设备
                    from _auth import revoke_all_sessions_for_user
                    n_revoked = revoke_all_sessions_for_user(sel)
                    st.success(
                        f'✅ {sel} 密码已重置'
                        + (f'，并踢掉 {n_revoked} 个登录设备' if n_revoked else '')
                    )
        with c7:
            with st.popover('🗑️ 删除用户'):
                if sel == current_user():
                    st.error('不能删除自己')
                else:
                    confirm = st.text_input(f'输入用户名 `{sel}` 确认删除',
                                            key=f'ops_del_confirm{k}')
                    if st.button('确认删除', key=f'ops_del_btn{k}',
                                 disabled=(confirm != sel), type='primary'):
                        delete_user(sel)
                        from _auth import revoke_all_sessions_for_user
                        revoke_all_sessions_for_user(sel)
                        st.success(f'✅ 已删除 {sel}')
                        st.rerun()


# ──────────────────────────────────────────────
# 新建用户
# ──────────────────────────────────────────────
with tab_new:
    st.markdown("#### 创建新用户")
    data_sps = list_data_salespeople()
    with st.form('_new_user_form', clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            new_user = st.text_input('用户名（登录用）', placeholder='如 sun_lujiang')
            new_pwd = st.text_input('初始密码', type='password')
            new_pwd2 = st.text_input('再次输入密码', type='password')
        with col2:
            new_role = st.selectbox('角色',
                                    ['salesperson', 'manager', 'product_manager', 'admin', 'guest'])
            new_name = st.text_input('显示名（推荐填业务员中文名）', placeholder='如 孙鲁江')
            new_remark = st.text_input('备注（可空）')

        # 绑定业务员
        new_bs_opts = ['（不绑定）'] + data_sps
        new_bs = st.selectbox(
            '🧑‍💼 绑定业务员（任何角色都可绑 · salesperson 必填）',
            new_bs_opts,
            index=0,
            help=(
                "把这个账号绑定到业务数据里的「业务员」字段：\n"
                "• salesperson：必填，否则他在「我的任务」看不到自己客户\n"
                "• manager：自己同时跑客户时建议绑（也能用「我的任务」）\n"
                "• admin / guest：通常不绑"
            ),
        )
        new_bs_val = '' if new_bs.startswith('（不绑定）') else new_bs

        submitted = st.form_submit_button('➕ 创建', type='primary')
        if submitted:
            if not new_user or not new_pwd:
                st.error('用户名和密码必填')
            elif new_pwd != new_pwd2:
                st.error('两次密码不一致')
            elif new_user in users_df['username'].tolist():
                st.error(f'用户名 {new_user} 已存在')
            elif new_role == 'salesperson' and not new_bs_val:
                st.error('❌ salesperson 必须绑定业务员（否则任务系统识别不了）')
            else:
                upsert_user(username=new_user, password=new_pwd, role=new_role,
                            full_name=new_name, 备注=new_remark,
                            bound_salesperson=new_bs_val)
                msg = f'✅ 已创建用户 `{new_user}`'
                if new_bs_val:
                    msg += f' · 绑定业务员 `{new_bs_val}`'
                msg += '。下一步去「权限配置」Tab 给他配 scope。'
                st.success(msg)
                st.rerun()


# ──────────────────────────────────────────────
# 权限配置（scope）
# ──────────────────────────────────────────────
with tab_scope:
    st.markdown("#### 数据权限（scope）配置")
    st.caption(
        "**白名单 scope**：勾哪些 城市/区县/业务员/代理商，用户只能看到这些范围的数据。"
        "admin/manager 角色自动绕过所有 scope 限制。"
    )

    with st.expander("📖 配置规则说明（点击展开）"):
        st.markdown("""
**勾的越少 = 看的越多；每多勾一个维度 = 范围越窄（多维度是 AND 关系）**

| 配置 | 看到的数据 |
|------|----------|
| 只勾 **地市=杭州市** | 杭州市全市所有数据（所有区县/业务员/代理商）|
| 地市=杭州市 + 区县=滨江区 | 只看杭州滨江区 |
| 地市=杭州市 + 业务员=孙鲁江 | 杭州市 + 业务员是孙鲁江 |
| 地市=杭州市、宁波市 | 杭州 + 宁波 两市数据 |
| 全留空 | 全国全部数据（不推荐，等同 admin）|

⚠️ 注意：admin/manager 角色**自动跳过 scope**，scope 配置仅对 `salesperson`/`guest` 生效。
""")

    sel_u = st.selectbox("选用户", users_df['username'].tolist(), key='scope_user')
    if sel_u:
        u_row = users_df[users_df['username'] == sel_u].iloc[0]
        st.caption(f"角色：**{u_row['role']}** | 显示名：**{u_row['full_name'] or '-'}**")

        if u_row['role'] in ('admin', 'manager'):
            st.info("ℹ️ admin/manager 不需要 scope（默认看全部）")
        elif u_row['role'] == 'product_manager':
            st.info("ℹ️ 产品经理：先在下方「📄 页面分配」勾选可见页面（只能分普通页），"
                    "再按需配「区域分配」过滤页面内数据。**未分配任何页面 = 只能看到主页**。")

        current = get_user_scope(sel_u)
        st.write(f"当前 scope：{current or '（空，无限制）'}")

        # 从 salesperson_scope 表自动同步
        st.markdown("##### 🔄 自动同步（推荐）")
        st.caption(
            "根据当前账号绑定的业务员名匹配 `salesperson_scope` 表，"
            "自动把该业务员负责的 城市/区县/代理商 填进 scope。"
            "**前提：上方「绑定业务员」字段已填**。"
        )
        # 优先用 bound_salesperson；fallback 到 full_name
        sync_sp_name = u_row.get('bound_salesperson') or u_row['full_name']
        if not sync_sp_name:
            st.info("请先在上方设置「绑定业务员」或「显示名」")
        if st.button(
            f"🔄 从 salesperson_scope 同步「{sync_sp_name}」",
            disabled=not sync_sp_name,
        ):
            conn = sqlite3.connect(str(DB_PATH))
            try:
                df = pd.read_sql(
                    "SELECT 市, 区县, 代理商 FROM salesperson_scope WHERE 业务员 = ?",
                    conn, params=(sync_sp_name,),
                )
            finally:
                conn.close()

            if df.empty:
                st.warning(f"salesperson_scope 表里没找到业务员「{sync_sp_name}」")
            else:
                scopes = []
                for city in df['市'].dropna().unique():
                    scopes.append(('city', city))
                for d in df['区县'].dropna().unique():
                    scopes.append(('district', d))
                for dealer in df['代理商'].dropna().unique():
                    scopes.append(('dealer', dealer))
                # 业务员名本身也加入 scope（向后兼容旧逻辑）
                scopes.append(('salesperson', sync_sp_name))
                n = set_user_scope(sel_u, scopes)
                # 如果还未绑定 bound_salesperson，顺手补上
                if not u_row.get('bound_salesperson'):
                    set_bound_salesperson(sel_u, sync_sp_name)
                st.success(
                    f"✅ 已同步 {n} 条 scope + 已绑定业务员「{sync_sp_name}」"
                    f"（{df['市'].nunique()} 城市 / {df['区县'].nunique()} 区县 / "
                    f"{df['代理商'].nunique()} 代理商）"
                )
                st.rerun()

        # 手动配置
        st.markdown("---")
        st.markdown("##### ✏️ 手动配置")

        # 产品经理：页面分配（只能分普通页 role!=admin；admin 专属页不可分配）
        is_pm = (u_row['role'] == 'product_manager')
        _normal_pages = [p for p in PAGES if p.get('role') != 'admin']
        _url2title = {p['url']: f"{p.get('icon', '')} {p['title']}".strip()
                      for p in _normal_pages}

        try:
            cities = list_cities()
        except Exception:
            cities = []
        conn = sqlite3.connect(str(DB_PATH))
        try:
            all_districts = pd.read_sql(
                "SELECT DISTINCT 上线区县 FROM product_flow_v WHERE 上线区县 IS NOT NULL ORDER BY 上线区县",
                conn,
            )['上线区县'].tolist()
            all_sps = pd.read_sql(
                "SELECT DISTINCT 业务员 FROM salesperson_scope ORDER BY 业务员",
                conn,
            )['业务员'].tolist()
            all_dealers = pd.read_sql(
                "SELECT DISTINCT 代理商 FROM salesperson_scope ORDER BY 代理商",
                conn,
            )['代理商'].tolist()
        finally:
            conn.close()

        with st.form('_scope_form'):
            sel_pages = []
            if is_pm:
                st.markdown("**📄 页面分配** — 产品经理侧边栏只显示这里勾选的页面"
                            "（只能分普通页，admin 专属页不可分配；主页「智能搜索」始终可见）")
                sel_pages = st.multiselect(
                    "分配页面",
                    [p['url'] for p in _normal_pages],
                    default=[u for u in current.get('page', []) if u in _url2title],
                    format_func=lambda u: _url2title.get(u, u),
                )
                st.markdown("**📍 区域分配** — 页面内数据再按下面的区域 scope 过滤"
                            "（留空 = 该角色数据不按区域收窄）")
            sel_cities = st.multiselect("📍 允许的地市", cities,
                                        default=current.get('city', []))
            sel_dist = st.multiselect("🏘 允许的区县", all_districts,
                                      default=current.get('district', []))
            sel_sp = st.multiselect("👤 业务员（限定能看哪些业务员的数据/任务）", all_sps,
                                    default=current.get('salesperson', []))
            sel_dealer = st.multiselect("🏢 代理商", all_dealers,
                                        default=current.get('dealer', []))

            if st.form_submit_button("💾 保存 scope", type='primary'):
                scopes = []
                if is_pm:
                    scopes += [('page', u) for u in sel_pages]
                scopes += [('city', c) for c in sel_cities]
                scopes += [('district', d) for d in sel_dist]
                scopes += [('salesperson', s) for s in sel_sp]
                scopes += [('dealer', d) for d in sel_dealer]
                n = set_user_scope(sel_u, scopes)
                st.success(f"✅ 已保存 {n} 条 scope")
                st.rerun()


# ──────────────────────────────────────────────
# 生产数据 API 账号（独立于本系统登录账号）
# ──────────────────────────────────────────────
with tab_api:
    from _api_account_admin import (  # noqa: E402
        ApiAccountAdminError,
        create_api_user,
        delete_api_user,
        list_api_users,
        list_catalog_resources,
        replace_api_permissions,
        reset_api_password,
        revoke_api_tokens,
        set_api_user_enabled,
    )

    st.markdown("#### 生产数据 API 账号")
    st.caption(
        "供 Codex、Claude Code、OpenClaw 等 Agent 调用只读生产数据接口。"
        "这里的账号独立于本系统登录账号；所有操作仅 admin 可执行。"
    )
    st.info(
        "接口账号只能读取已注册业务资源，系统安全表永久禁止访问。"
        "密码至少12位；创建后系统不会显示或保存明文密码，请由管理员安全交付。"
    )

    try:
        api_users = list_api_users()
        api_resources = list_catalog_resources()
        api_ready = True
    except ApiAccountAdminError as exc:
        api_users = []
        api_resources = []
        api_ready = False
        st.error(f"⛔ 无法连接数据接口账号服务：{exc}")

    if api_ready:
        resource_names = [item["name"] for item in api_resources]
        resource_labels = {
            item["name"]: (
                f"{item.get('domain') or '未分类'} · {item['name']}"
                f" — {item.get('description') or '无说明'}"
            )
            for item in api_resources
        }

        if api_users:
            show_api_users = pd.DataFrame(api_users)
            show_api_users["状态"] = show_api_users["enabled"].map(
                lambda value: "✅ 启用" if bool(value) else "⛔ 停用"
            )
            show_api_users["数据权限"] = show_api_users["table_patterns"].map(
                lambda values: (
                    "全部业务资源"
                    if "*" in (values or [])
                    else f"{len(values or [])} 项"
                )
            )
            show_api_users = show_api_users.rename(columns={
                "username": "用户名",
                "full_name": "使用人",
                "max_rows": "单次最大行数",
                "last_login": "最近登录",
                "created_at": "创建时间",
            })
            st.dataframe(
                show_api_users[[
                    "用户名", "使用人", "状态", "数据权限",
                    "单次最大行数", "最近登录", "创建时间",
                ]],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.warning("当前还没有数据接口账号。")

        with st.expander("➕ 创建数据接口账号", expanded=not api_users):
            with st.form("_api_create_user", clear_on_submit=True):
                create_col1, create_col2 = st.columns(2)
                with create_col1:
                    api_new_username = st.text_input(
                        "用户名",
                        placeholder="如 cory",
                        key="api_new_username",
                    )
                    api_new_password = st.text_input(
                        "初始密码（至少12位）",
                        type="password",
                        key="api_new_password",
                    )
                    api_new_password2 = st.text_input(
                        "再次输入密码",
                        type="password",
                        key="api_new_password2",
                    )
                with create_col2:
                    api_new_full_name = st.text_input(
                        "使用人/备注",
                        placeholder="如 运营人员",
                        key="api_new_full_name",
                    )
                    api_new_max_rows = st.number_input(
                        "单次最大返回行数",
                        min_value=100,
                        max_value=20000,
                        value=5000,
                        step=100,
                        key="api_new_max_rows",
                    )
                    api_new_permission_mode = st.radio(
                        "初始数据权限",
                        ["全部业务资源", "选择指定资源"],
                        horizontal=True,
                        key="api_new_permission_mode",
                    )
                api_new_selected = []
                if api_new_permission_mode == "选择指定资源":
                    api_new_selected = st.multiselect(
                        "允许读取的资源",
                        resource_names,
                        format_func=lambda name: resource_labels.get(name, name),
                        key="api_new_selected",
                    )
                api_create_submitted = st.form_submit_button(
                    "创建账号",
                    type="primary",
                )

            if api_create_submitted:
                try:
                    if api_new_password != api_new_password2:
                        raise ValueError("两次输入的密码不一致")
                    initial_patterns = (
                        ["*"]
                        if api_new_permission_mode == "全部业务资源"
                        else api_new_selected
                    )
                    if not initial_patterns:
                        raise ValueError("请至少选择一个数据资源")
                    create_api_user(
                        api_new_username,
                        api_new_password,
                        full_name=api_new_full_name,
                        max_rows=int(api_new_max_rows),
                    )
                    try:
                        replace_api_permissions(api_new_username, initial_patterns)
                    except Exception:
                        # 避免创建出无权限的半成品账号。
                        delete_api_user(api_new_username)
                        raise
                    st.success(
                        f"✅ 数据接口账号 `{api_new_username}` 已创建并完成授权。"
                        "请立即把密码安全交付给使用人。"
                    )
                    st.rerun()
                except (ValueError, ApiAccountAdminError) as exc:
                    st.error(f"创建失败：{exc}")

        if api_users:
            st.markdown("##### 管理已有账号")
            api_usernames = [item["username"] for item in api_users]
            api_selected_username = st.selectbox(
                "选择账号",
                api_usernames,
                key="api_selected_username",
            )
            selected_api_user = next(
                item for item in api_users
                if item["username"] == api_selected_username
            )
            current_patterns = selected_api_user.get("table_patterns") or []

            status_col1, status_col2, status_col3 = st.columns(3)
            with status_col1:
                if bool(selected_api_user["enabled"]):
                    if st.button(
                        "⛔ 停用账号",
                        key=f"api_disable_{api_selected_username}",
                    ):
                        try:
                            set_api_user_enabled(api_selected_username, False)
                            st.success("账号已停用，现有 Token 同时失效。")
                            st.rerun()
                        except ApiAccountAdminError as exc:
                            st.error(str(exc))
                else:
                    if st.button(
                        "✅ 启用账号",
                        key=f"api_enable_{api_selected_username}",
                        type="primary",
                    ):
                        try:
                            set_api_user_enabled(api_selected_username, True)
                            st.success("账号已启用。")
                            st.rerun()
                        except ApiAccountAdminError as exc:
                            st.error(str(exc))
            with status_col2:
                if st.button(
                    "🔄 强制所有 Token 失效",
                    key=f"api_revoke_{api_selected_username}",
                ):
                    try:
                        revoke_api_tokens(api_selected_username)
                        st.success("该账号现有 Token 已全部失效。")
                    except ApiAccountAdminError as exc:
                        st.error(str(exc))
            with status_col3:
                st.write(
                    f"当前权限：**{'全部业务资源' if '*' in current_patterns else f'{len(current_patterns)} 项'}**"
                )

            permission_mode_default = (
                "全部业务资源" if "*" in current_patterns else "选择指定资源"
            )
            with st.form(f"_api_permissions_{api_selected_username}"):
                api_permission_mode = st.radio(
                    "数据权限",
                    ["全部业务资源", "选择指定资源"],
                    index=0 if permission_mode_default == "全部业务资源" else 1,
                    horizontal=True,
                    key=f"api_permission_mode_{api_selected_username}",
                )
                exact_current = [
                    name for name in current_patterns if name in resource_names
                ]
                api_permission_selected = []
                if api_permission_mode == "选择指定资源":
                    api_permission_selected = st.multiselect(
                        "允许读取的资源",
                        resource_names,
                        default=exact_current,
                        format_func=lambda name: resource_labels.get(name, name),
                        key=f"api_permission_selected_{api_selected_username}",
                    )
                    unknown_patterns = [
                        pattern for pattern in current_patterns
                        if pattern not in resource_names and pattern != "*"
                    ]
                    if unknown_patterns:
                        st.warning(
                            "当前账号含高级通配权限："
                            f"`{', '.join(unknown_patterns)}`。"
                            "保存后将改为上方选择的精确资源。"
                        )
                if st.form_submit_button("💾 保存数据权限", type="primary"):
                    try:
                        target_patterns = (
                            ["*"]
                            if api_permission_mode == "全部业务资源"
                            else api_permission_selected
                        )
                        count = replace_api_permissions(
                            api_selected_username,
                            target_patterns,
                        )
                        st.success(f"✅ 已保存 {count} 条权限，现有 Token 已失效。")
                        st.rerun()
                    except (ValueError, ApiAccountAdminError) as exc:
                        st.error(f"保存失败：{exc}")

            reset_col, delete_col = st.columns(2)
            with reset_col:
                with st.expander("🔑 重置接口密码"):
                    with st.form(f"_api_reset_password_{api_selected_username}"):
                        api_reset_password = st.text_input(
                            "新密码（至少12位）",
                            type="password",
                            key=f"api_reset_password_{api_selected_username}",
                        )
                        api_reset_password2 = st.text_input(
                            "再次输入新密码",
                            type="password",
                            key=f"api_reset_password2_{api_selected_username}",
                        )
                        api_reset_submit = st.form_submit_button("重置密码")
                    if api_reset_submit:
                        try:
                            if api_reset_password != api_reset_password2:
                                raise ValueError("两次输入的密码不一致")
                            reset_api_password(
                                api_selected_username,
                                api_reset_password,
                            )
                            st.success("✅ 密码已重置，现有 Token 已全部失效。")
                        except (ValueError, ApiAccountAdminError) as exc:
                            st.error(f"重置失败：{exc}")
            with delete_col:
                with st.expander("🗑️ 删除接口账号"):
                    api_delete_confirm = st.text_input(
                        f"输入 `{api_selected_username}` 确认删除",
                        key=f"api_delete_confirm_{api_selected_username}",
                    )
                    if st.button(
                        "确认删除",
                        key=f"api_delete_{api_selected_username}",
                        disabled=(api_delete_confirm != api_selected_username),
                    ):
                        try:
                            delete_api_user(api_selected_username)
                            st.success("✅ 接口账号已删除。")
                            st.rerun()
                        except ApiAccountAdminError as exc:
                            st.error(f"删除失败：{exc}")
