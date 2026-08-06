"""
HTTP Basic auth gate + 登录用户识别。

两种模式，按存在与否自动选：

1. **多账号**（`/etc/so-funnel/users.json` 存在且非空）—— 管理员 + 11 个地市账号，
   每个账号带地区 scope。认证通过后把 `User` 挂到 `request.state.user`，
   路由层用 `require_geo()` 做地区级授权。
2. **单账号**（只设了 `FUNNEL_USER`/`FUNNEL_PASS`）—— 老行为，等同管理员。
   本地开发两者都不设时中间件是 no-op。

⚠️ 仍是**明文 HTTP**（8443 端口无 TLS）。Basic 凭证在链路上可被嗅探，
这一点没变，地区授权解决的是"登录后能改谁的数据"，不是传输安全。
要对外扩大范围仍需先上 nginx + TLS。
"""
from __future__ import annotations

import base64
import binascii
import os
import secrets
import time
from typing import Dict, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from .users import User, hash_password, load_users, verify_password

# realm must stay ASCII — HTTP headers are latin-1 encoded, so a Chinese realm
# raises UnicodeEncodeError when the response is built.
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


def _deny() -> Response:
    return JSONResponse({"detail": "Unauthorized"}, status_code=401,
                        headers=CHALLENGE)


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


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """
    单账号模式（向后兼容）。认证通过即视为管理员，不做地区限制。
    """

    def __init__(self, app, user: str, password: str):
        super().__init__(app)
        self._user = user
        self._password = password
        self._admin = User(user, "*", user, {})

    async def dispatch(self, request, call_next):
        creds = _parse_basic(request.headers.get("authorization", ""))
        if creds is None:
            return _deny()
        user, password = creds
        # compare_digest on both halves, and evaluate both before returning,
        # so the check is not short-circuited on a wrong username.
        user_ok = secrets.compare_digest(user, self._user)
        pass_ok = secrets.compare_digest(password, self._password)
        if not (user_ok and pass_ok):
            return _deny()
        request.state.user = self._admin
        return await call_next(request)


class MultiUserAuthMiddleware(BaseHTTPMiddleware):
    """
    多账号模式。认证通过后把 User 挂到 request.state.user，供路由做地区授权。

    用户表**每次请求都重读吗？** 不。启动时读一次，缓存在内存里；改了
    users.json 要 `systemctl restart so-funnel` 生效。这样每个请求少一次磁盘 IO，
    代价是加账号要重启——加账号是低频运维动作，划算。
    """

    def __init__(self, app, users: Dict[str, User]):
        super().__init__(app)
        self._users = users
        # 用户名不存在时也要走一遍 KDF，否则"响应快 = 用户名不存在"，
        # 等于白送一个用户名枚举口子
        self._dummy = hash_password("x" * 24, iters=1000)

    async def dispatch(self, request, call_next):
        creds = _parse_basic(request.headers.get("authorization", ""))
        if creds is None:
            return _deny()
        name, password = creds
        u = self._users.get(name)
        if u is None:
            verify_password(password, self._dummy)   # 拖平时序
            return _deny()
        if not (_cache_ok(name, password) or verify_password(password, u.rec)):
            return _deny()
        _cache_put(name, password)
        request.state.user = u
        return await call_next(request)


def install_auth(app) -> bool:
    """
    挂上鉴权。多账号优先，其次单账号，都没有则不挂（本地开发）。
    返回是否启用了鉴权。
    """
    users = load_users()
    if users:
        app.add_middleware(MultiUserAuthMiddleware, users=users)
        return True
    user = os.environ.get("FUNNEL_USER", "")
    password = os.environ.get("FUNNEL_PASS", "")
    if not (user and password):
        return False
    app.add_middleware(BasicAuthMiddleware, user=user, password=password)
    return True


def current_user(request) -> Optional[User]:
    """当前登录用户。无鉴权（本地开发）时为 None，此时视为不受限。"""
    return getattr(request.state, "user", None)
