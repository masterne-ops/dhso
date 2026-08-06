"""登录鉴权 + 数据权限 scope

机制：
  - **DB 优先**：app_user 表有用户 → 走 DB（推荐，可在 page 00 管理）
  - **JSON 兜底**：DB 无用户但 ~/.so_data_analytics/auth.json 有 → 兜底走 JSON
  - 两个都没 → 开发模式，直接放行

数据权限（scope）：
  - admin / manager：放行所有数据
  - salesperson：只能看 app_user_scope 表里允许的 城市/区县/业务员/代理商
  - 业务员调 get_current_scope() 拿到自己的 scope dict

用法：
  在 app.py 和每个 pages/*.py 顶部 set_page_config 之后立即调：
      from _auth import require_auth
      require_auth()

  数据查询时：
      from _auth import get_current_scope, is_admin
      if not is_admin():
          scope = get_current_scope()
          if scope.get('city'):
              df = df[df['上线城市'].isin(scope['city'])]
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


AUTH_FILE = Path.home() / '.so_data_analytics' / 'auth.json'

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 持久化登录：服务端 token 文件 + 浏览器 Cookie
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# 设计：
#   1. 登录成功 → 生成 32 字节随机 token，写到服务端 SESSION_FILE
#   2. 通过 components.html 在浏览器主文档设置 cookie（max-age = 90 天）
#   3. 后续访问 → st.context.cookies 读 token → 服务端文件比对 → 还原 session_state
#   4. 用户点登出 → 撤销 token + 清 cookie
#
# 这样浏览器只要不清 cookie，就一直保持登录；服务器重启 / WebSocket 断都不影响

SESSION_FILE = Path.home() / '.so_data_analytics' / 'sessions.json'
SESSION_COOKIE_NAME = '_so_da_token'
SESSION_TTL_DAYS = 90  # cookie 有效期；服务端永不过期（除非显式撤销 / 文件被删）


def _load_sessions() -> dict:
    if not SESSION_FILE.exists():
        return {}
    try:
        with open(SESSION_FILE, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_sessions(d: dict):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_FILE.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SESSION_FILE)
    try:
        os.chmod(SESSION_FILE, 0o600)
    except OSError:
        pass


def _create_session_token(user: str) -> str:
    """登录成功后调用 — 写服务端 session 记录 + 返回 token"""
    token = secrets.token_urlsafe(32)
    d = _load_sessions()
    d[token] = {
        'user': user,
        'created_at': int(time.time()),
        'last_seen': int(time.time()),
    }
    _save_sessions(d)
    return token


def _validate_session_token(token: str) -> str | None:
    """token → user (or None if invalid). 同时更新 last_seen"""
    if not token:
        return None
    d = _load_sessions()
    info = d.get(token)
    if not info:
        return None
    user = info.get('user')
    if not user:
        return None
    # 顺便确认该用户没被禁用
    try:
        from _ai_log import list_users
        users_df = list_users()
        if not users_df.empty:
            row = users_df[users_df['username'] == user]
            if row.empty or not bool(row.iloc[0].get('enabled', 1)):
                # 用户已禁用 / 删除 → 清掉 token
                _revoke_session_token(token)
                return None
    except Exception:
        pass
    # 更新 last_seen（按天粒度防止频繁写）
    today = int(time.time()) // 86400
    last_seen_day = (info.get('last_seen') or 0) // 86400
    if today != last_seen_day:
        info['last_seen'] = int(time.time())
        _save_sessions(d)
    return user


def _revoke_session_token(token: str):
    if not token:
        return
    d = _load_sessions()
    if token in d:
        del d[token]
        _save_sessions(d)


def revoke_all_sessions_for_user(username: str) -> int:
    """撤销某用户的所有 active session（强制所有设备下线）— 比如改了密码"""
    d = _load_sessions()
    to_delete = [t for t, info in d.items() if info.get('user') == username]
    for t in to_delete:
        del d[t]
    if to_delete:
        _save_sessions(d)
    return len(to_delete)


def list_active_sessions(*, username: str = None) -> list[dict]:
    """列出 active sessions（admin 用）"""
    d = _load_sessions()
    out = []
    for token, info in d.items():
        if username and info.get('user') != username:
            continue
        out.append({
            'token_prefix': (token or '')[:8] + '...',
            'user': info.get('user'),
            'created_at': info.get('created_at'),
            'last_seen': info.get('last_seen'),
        })
    return out


def _set_browser_cookie(token: str):
    """通过 components.html 在主文档设 cookie"""
    js = (
        "<script>"
        "try {"
        f"  window.parent.document.cookie = '{SESSION_COOKIE_NAME}=' + "
        f"  encodeURIComponent({json.dumps(token)}) + "
        f"  '; max-age={SESSION_TTL_DAYS * 86400}; path=/; SameSite=Lax';"
        "} catch(e) { console.warn('cookie set failed', e); }"
        "</script>"
    )
    components.html(js, height=0)


def _clear_browser_cookie():
    """让浏览器删除 cookie（max-age=0）"""
    js = (
        "<script>"
        "try {"
        f"  window.parent.document.cookie = '{SESSION_COOKIE_NAME}=; max-age=0; path=/';"
        "} catch(e) { console.warn('cookie clear failed', e); }"
        "</script>"
    )
    components.html(js, height=0)


def _read_browser_cookie() -> str | None:
    """从 streamlit 拿 cookie（1.32+ 支持 st.context.cookies）"""
    try:
        cookies = st.context.cookies
        if not cookies:
            return None
        return cookies.get(SESSION_COOKIE_NAME)
    except Exception:
        return None


def _load_users() -> dict[str, str]:
    """读 auth.json。文件不存在 → 返回空 dict（= 开发模式）"""
    if not AUTH_FILE.exists():
        return {}
    try:
        with open(AUTH_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f) or {}
        users = data.get('users') or {}
        return {str(k): str(v) for k, v in users.items()}
    except (json.JSONDecodeError, OSError):
        return {}


def _verify(username: str, password: str, users: dict) -> bool:
    """用 hmac.compare_digest 防时序攻击"""
    expected = users.get(username, '')
    if not expected:
        return False
    return hmac.compare_digest(expected, password)


def _db_has_users() -> bool:
    """检查 DB 里是否有用户（决定走 DB 还是走 JSON 兜底）"""
    try:
        from _ai_log import list_users
        return not list_users().empty
    except Exception:
        return False


def is_auth_required() -> bool:
    """DB 有用户 或 auth.json 有用户 → 需要登录"""
    return _db_has_users() or bool(_load_users())


def _try_restore_from_cookie() -> bool:
    """页面 entry 时调用 — 用 cookie token 还原 session_state（如有）

    Returns: True if restored, False otherwise
    """
    if st.session_state.get('_auth_user'):
        return False  # 已经有 session_state，不用还原
    if st.session_state.get('_auth_restore_tried'):
        return False  # 本会话已尝试过（cookie 无效，不要每次 rerun 都查文件）
    st.session_state['_auth_restore_tried'] = True
    token = _read_browser_cookie()
    if not token:
        return False
    user = _validate_session_token(token)
    if not user:
        return False
    st.session_state['_auth_user'] = user
    st.session_state['_auth_token'] = token
    return True


def is_logged_in() -> bool:
    if st.session_state.get('_auth_user'):
        return True
    return _try_restore_from_cookie()


def current_user() -> str:
    return st.session_state.get('_auth_user', '')


def get_current_role() -> str:
    """当前用户角色（每次 DB 实时查，避免管理员改后还得让用户重登）"""
    if not is_logged_in():
        return ''
    try:
        from _ai_log import get_user_role
        return get_user_role(current_user()) or ''
    except Exception:
        return ''


def get_current_scope() -> dict[str, list[str]]:
    """当前用户的数据权限范围 {scope_type: [scope_values]}（实时 DB 查）

    admin：放空 dict（表示"全放行"）
    manager / salesperson / guest / product_manager：从 app_user_scope 表查白名单
    （product_manager 的 scope 还含 scope_type='page' 页面白名单，由 app.py 侧边栏用；
     filter_by_scope 只取 city/district/salesperson/dealer，不受 page 影响）
    """
    if not is_logged_in() or is_admin():
        return {}
    try:
        from _ai_log import get_user_scope
        return get_user_scope(current_user()) or {}
    except Exception:
        return {}


def is_admin() -> bool:
    """只有 admin 自动绕过 scope；manager/salesperson/guest 都按 scope 过滤"""
    return get_current_role() == 'admin'


def is_privileged() -> bool:
    """admin 或 manager 都算"特权角色"（可分配任务、可访问用户管理等）"""
    return get_current_role() in ('admin', 'manager')


def is_product_manager() -> bool:
    """产品经理：数据范围 = 页面分配(scope_type='page') + 区域分配(city/district/dealer)，
    页面看白名单、数据按区域 scope 过滤（受限，非全放行）"""
    return get_current_role() == 'product_manager'


def filter_by_scope(df, *, city_col: str = None, district_col: str = None,
                    salesperson_col: str = None, dealer_col: str = None):
    """便捷工具：按当前用户 scope 过滤 DataFrame。

    admin/manager 直接返回原 df。其他人按 scope 过滤。
    传入需要过滤的列名即可（None 表示该维度不存在/不过滤）。
    """
    if is_admin():
        return df
    scope = get_current_scope()
    if not scope:
        return df
    if city_col and 'city' in scope and city_col in df.columns:
        df = df[df[city_col].isin(scope['city'])]
    if district_col and 'district' in scope and district_col in df.columns:
        df = df[df[district_col].isin(scope['district'])]
    if salesperson_col and 'salesperson' in scope and salesperson_col in df.columns:
        df = df[df[salesperson_col].isin(scope['salesperson'])]
    if dealer_col and 'dealer' in scope and dealer_col in df.columns:
        df = df[df[dealer_col].isin(scope['dealer'])]
    return df


def logout():
    # 撤销服务端 token
    token = st.session_state.get('_auth_token')
    if token:
        _revoke_session_token(token)
    st.session_state.pop('_auth_user', None)
    st.session_state.pop('_auth_token', None)
    st.session_state.pop('_auth_login_attempt', None)
    st.session_state.pop('_auth_restore_tried', None)
    # 清浏览器 cookie
    _clear_browser_cookie()


def require_auth():
    """页面门禁。未登录则展示登录表单并 st.stop()。

    顺序：① DB 用户 → ② JSON 兜底 → ③ 都没就开发模式放行
    持久化：① session_state → ② 浏览器 cookie + 服务端 token 文件
    """
    db_active = _db_has_users()
    json_users = _load_users()
    if not db_active and not json_users:
        # 开发模式：直接放行
        return

    # 先尝试从 cookie 还原（is_logged_in 已包含此逻辑）
    if is_logged_in():
        return

    # === 渲染登录表单 ===
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { display: none !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns([1, 1, 1])
    with cols[1]:
        st.markdown("### 🔒 SO 数据分析 · 登录")
        st.caption("内部系统，仅授权人员访问")

        with st.form('_login_form', clear_on_submit=False):
            username = st.text_input('用户名', key='_login_user')
            password = st.text_input('密码', type='password', key='_login_pwd')
            submitted = st.form_submit_button('登录', use_container_width=True, type='primary')

        if submitted:
            ok = False
            user_in = username.strip()
            if db_active:
                try:
                    from _ai_log import verify_password, update_last_login
                    if verify_password(user_in, password):
                        ok = True
                        update_last_login(user_in)
                except Exception:
                    pass
            if not ok and json_users:
                ok = _verify(user_in, password, json_users)

            if ok:
                st.session_state['_auth_user'] = user_in
                # 清密码缓存
                st.session_state.pop('_login_pwd', None)
                # 生成持久化 session token + 写浏览器 cookie
                try:
                    token = _create_session_token(user_in)
                    st.session_state['_auth_token'] = token
                    _set_browser_cookie(token)
                except Exception as e:
                    st.warning(f"⚠️ 持久登录 cookie 设置失败：{e}（本次仍可使用，下次需要重新登录）")
                # 让 cookie 设置脚本有机会执行后再 rerun
                import time
                time.sleep(0.3)
                st.rerun()
            else:
                st.error('❌ 用户名或密码错误')

    st.stop()


def render_user_badge():
    """侧栏底部展示登录用户名 + 登出按钮（可选调用）"""
    if not is_logged_in():
        return
    with st.sidebar:
        st.markdown('---')
        cols = st.columns([3, 1])
        cols[0].caption(f'👤 {current_user()}')
        if cols[1].button('登出', key='_auth_logout_btn'):
            logout()
            # 给 cookie 清除脚本 0.3 秒执行时间
            import time
            time.sleep(0.3)
            st.rerun()


# ══════════════════════════════════════════════
# 工具函数：服务器端用来初始化/更新 auth.json
# ══════════════════════════════════════════════

def init_auth_file(users: dict[str, str]) -> Path:
    """写一份 auth.json（chmod 600）。给运维脚本用，不在页面里调。"""
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AUTH_FILE, 'w', encoding='utf-8') as f:
        json.dump({'users': users}, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(AUTH_FILE, 0o600)
    except OSError:
        pass
    return AUTH_FILE


if __name__ == '__main__':
    # 命令行用：python _auth.py set <user> <pwd>
    import sys
    if len(sys.argv) >= 4 and sys.argv[1] == 'set':
        user, pwd = sys.argv[2], sys.argv[3]
        users = _load_users()
        users[user] = pwd
        path = init_auth_file(users)
        print(f"✅ 写入 {path}")
        print(f"   用户: {user}")
    else:
        users = _load_users()
        print(f"auth.json: {AUTH_FILE}")
        print(f"用户数: {len(users)}")
        for u in users:
            print(f"  - {u}")
