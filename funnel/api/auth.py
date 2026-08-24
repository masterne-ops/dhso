"""
登录闸门 + 登录用户识别。

两种账号来源，按存在与否自动选：

1. **多账号**（`/etc/so-funnel/users.json` 存在且非空）—— 管理员 + 11 个地市账号，
   每个账号带地区 scope。认证通过后把 `User` 挂到 `request.state.user`，
   路由层用 `require_geo()` 做地区级授权。
2. **单账号**（只设了 `FUNNEL_USER`/`FUNNEL_PASS`）—— 老行为，等同管理员。
   本地开发两者都不设时不挂闸门。

## 两条认证通道

**浏览器走表单 + 会话 cookie**（`session.py`）。以前用 HTTP Basic，登出做不干净：
浏览器缓存凭证并自动重发，「登出后点重新登录」会被静默登回旧账号，而靠换 realm
逼出登录框又会让密码管理器丢掉已保存的条目。表单登录把会话生命周期交回服务端，
登出/重登/记住密码/换账号四件事互不打架。

**脚本走 HTTP Basic**（curl、部署健康检查、`_verify_*.py`）。只对非浏览器请求
生效——浏览器一律带 `Sec-Fetch-*` 头，据此识别。这条限制是必须的：浏览器里
可能还残留着旧的 Basic 缓存，若照单全收，登出又会被它顶回来。

⚠️ 仍是**明文 HTTP**（8443 端口无 TLS），cookie 和 Basic 凭证在链路上都可被嗅探。
要对外扩大范围仍需先上 nginx + TLS（届时给 cookie 加 Secure）。
"""
from __future__ import annotations

import base64
import binascii
import os
import secrets
import time
from typing import Dict, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import session
from .users import User, hash_password, load_users, verify_password

LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"
_PUBLIC_PATHS = frozenset({LOGIN_PATH, LOGOUT_PATH, "/favicon.ico"})

CHALLENGE = {"WWW-Authenticate": 'Basic realm="SO Funnel", charset="UTF-8"'}

# PBKDF2 24 万次迭代 ≈ 60~100ms。页面一次加载会并发 5+ 个接口，每个都重算一遍
# KDF 会把首屏拖慢半秒以上。缓存 (user, password) → 已验证，60 秒过期。
# 缓存的是**校验结果**而不是密码本身：key 里的密码摘要用随机 pepper 做 HMAC，
# 内存 dump 里也拿不回明文。
_VERIFY_TTL = 60.0
_PEPPER = secrets.token_bytes(32)
_VERIFY_CACHE: Dict[bytes, float] = {}


def _cache_key(user: str, password: str) -> bytes:
    import hashlib
    import hmac as _hmac
    return _hmac.new(_PEPPER, f"{user}\0{password}".encode("utf-8"),
                     hashlib.sha256).digest()


def _cache_ok(user: str, password: str) -> bool:
    k = _cache_key(user, password)
    exp = _VERIFY_CACHE.get(k)
    if exp is None:
        return False
    if exp < time.monotonic():
        _VERIFY_CACHE.pop(k, None)
        return False
    return True


def _cache_put(user: str, password: str) -> None:
    # 上限兜底：别让缓存变成无界字典（爆破尝试会塞满它）
    if len(_VERIFY_CACHE) > 512:
        now = time.monotonic()
        for k, exp in list(_VERIFY_CACHE.items()):
            if exp < now:
                _VERIFY_CACHE.pop(k, None)
        if len(_VERIFY_CACHE) > 512:
            _VERIFY_CACHE.clear()
    _VERIFY_CACHE[_cache_key(user, password)] = time.monotonic() + _VERIFY_TTL


def _parse_basic(header: str) -> Optional[Tuple[str, str]]:
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic" or not token:
        return None
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    user, sep, password = decoded.partition(":")
    return (user, password) if sep else None


class AuthGate:
    """账号表 + 校验。多账号与单账号两种来源在这里被抹平成同一套接口。"""

    def __init__(self, users: Optional[Dict[str, User]] = None,
                 single: Optional[Tuple[str, str]] = None):
        self._users = users or {}
        self._single = single
        if single:
            name, _ = single
            self._users = {name: User(name, "*", name, {})}
        # 用户名不存在时也要走一遍 KDF，否则"响应快 = 用户名不存在"，
        # 等于白送一个用户名枚举口子
        self._dummy = hash_password("x" * 24, iters=1000)

    def get(self, name: str) -> Optional[User]:
        return self._users.get(name)

    def authenticate(self, name: str, password: str) -> Optional[User]:
        if self._single:
            want_name, want_pw = self._single
            name_ok = secrets.compare_digest(name, want_name)
            pw_ok = secrets.compare_digest(password, want_pw)
            return self._users[want_name] if (name_ok and pw_ok) else None
        u = self._users.get(name)
        if u is None:
            verify_password(password, self._dummy)   # 拖平时序
            return None
        if not (_cache_ok(name, password) or verify_password(password, u.rec)):
            return None
        _cache_put(name, password)
        return u


GATE: Optional[AuthGate] = None


def gate() -> Optional[AuthGate]:
    return GATE


# ── cookie ───────────────────────────────────────────────

def set_session_cookie(response: Response, name: str) -> None:
    response.set_cookie(
        session.COOKIE_NAME,
        session.issue(name),
        max_age=session.TTL_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(session.COOKIE_NAME, path="/")
    # 早期版本残留的 cookie，一并清掉免得干扰
    for stale in ("funnel_reauth", "funnel_realm"):
        response.delete_cookie(stale, path="/")


# ── 请求分类 ──────────────────────────────────────────────

def _is_browser(request: Request) -> bool:
    """浏览器一律带 Sec-Fetch-*；curl / urllib / TestClient 不带。"""
    return any(h in request.headers for h in
               ("sec-fetch-mode", "sec-fetch-site", "sec-fetch-dest"))


def _wants_html(request: Request) -> bool:
    if request.method not in ("GET", "HEAD"):
        return False
    if request.headers.get("sec-fetch-mode") == "navigate":
        return True
    if request.url.path.startswith("/api/"):
        return False
    return "text/html" in request.headers.get("accept", "")


def _login_redirect(request: Request) -> Response:
    nxt = request.url.path
    if request.url.query:
        nxt = f"{nxt}?{request.url.query}"
    from urllib.parse import quote
    resp = RedirectResponse(f"{LOGIN_PATH}?next={quote(nxt, safe='')}",
                            status_code=303)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _deny(request: Request) -> Response:
    """
    未登录。浏览器请求**绝不能**带 WWW-Authenticate —— 那会弹出原生 Basic 弹窗，
    正是我们要摆脱的东西。脚本请求带上，方便 curl。
    """
    headers = {"Cache-Control": "no-store"}
    if not _is_browser(request):
        headers.update(CHALLENGE)
    return JSONResponse({"detail": "Unauthorized", "login": LOGIN_PATH},
                        status_code=401, headers=headers)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, gate: AuthGate):
        super().__init__(app)
        self._gate = gate

    def _resolve(self, request: Request) -> Optional[User]:
        name = session.verify(request.cookies.get(session.COOKIE_NAME, ""))
        if name:
            u = self._gate.get(name)
            if u is not None:
                return u
        # Basic 只服务脚本：浏览器里的陈旧 Basic 缓存不能顶掉登出
        if not _is_browser(request):
            creds = _parse_basic(request.headers.get("authorization", ""))
            if creds:
                return self._gate.authenticate(*creds)
        return None

    async def dispatch(self, request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        user = self._resolve(request)
        if user is None:
            return (_login_redirect(request) if _wants_html(request)
                    else _deny(request))
        request.state.user = user
        return await call_next(request)


def install_auth(app) -> bool:
    """
    挂上闸门。多账号优先，其次单账号，都没有则不挂（本地开发）。
    返回是否启用了鉴权。
    """
    global GATE
    users = load_users()
    if users:
        GATE = AuthGate(users=users)
    else:
        name = os.environ.get("FUNNEL_USER", "")
        password = os.environ.get("FUNNEL_PASS", "")
        if not (name and password):
            GATE = None
            return False
        GATE = AuthGate(single=(name, password))
    app.add_middleware(AuthMiddleware, gate=GATE)
    return True


def current_user(request) -> Optional[User]:
    """当前登录用户。无鉴权（本地开发）时为 None，此时视为不受限。"""
    return getattr(request.state, "user", None)
