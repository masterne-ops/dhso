"""
登录会话（签名 cookie）。

为什么不再用 HTTP Basic 做浏览器登录：Basic 没有服务端会话，浏览器把凭证
缓存到进程退出为止，而且会在每个请求上自动重发。于是"登出"只能靠各种
清缓存偏方（假凭证覆盖、换 realm），实测都不稳：要么第二次登出失效，
要么换 realm 把密码管理器里存的条目弄丢，要么点重新登录时浏览器直接
用旧凭证静默登进去、根本不给重新输入的机会。

改成表单登录后这些问题一次性消失：
  登出   = 删 cookie（服务端说了算，可反复）
  重登   = 表单永远可编辑，随时换账号
  记住密码 = 标准 <form> + autocomplete，交给浏览器密码管理器

token 是无状态签名串，不落库：

    v1.<base64url(name|exp)>.<base64url(HMAC-SHA256)>

密钥放 `/etc/so-funnel/session.key`（0600，首次自动生成）。密钥持久化是必须的，
否则每次 `systemctl restart` 都会把所有人踢下线。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Optional

COOKIE_NAME = "funnel_session"
# 30 天：内部工具，登一次用一个月，够久但不至于永久有效
TTL_SECONDS = 30 * 86400

KEY_PATH = Path(os.environ.get(
    "SO_FUNNEL_SESSION_KEY_FILE", "/etc/so-funnel/session.key"))

_KEY: Optional[bytes] = None


def _load_key() -> bytes:
    """
    读密钥，没有就生成。写不进去（本地开发、只读 /etc）时退化成内存随机密钥——
    此时重启会掉线，但不至于让服务起不来。
    """
    global _KEY
    if _KEY is not None:
        return _KEY
    env = os.environ.get("SO_FUNNEL_SESSION_KEY")
    if env:
        _KEY = env.encode("utf-8")
        return _KEY
    try:
        if KEY_PATH.exists():
            raw = KEY_PATH.read_bytes().strip()
            if len(raw) >= 32:
                _KEY = raw
                return _KEY
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        raw = base64.urlsafe_b64encode(secrets.token_bytes(48))
        # 先 0600 建文件再写，避免密钥有一瞬间是 0644
        fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        _KEY = raw
    except OSError:
        _KEY = secrets.token_bytes(48)
    return _KEY


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: str) -> str:
    mac = hmac.new(_load_key(), payload.encode("utf-8"), hashlib.sha256)
    return _b64e(mac.digest())


def issue(name: str, ttl: int = TTL_SECONDS) -> str:
    body = _b64e(f"{name}|{int(time.time()) + ttl}".encode("utf-8"))
    return f"v1.{body}.{_sign(body)}"


def verify(token: str) -> Optional[str]:
    """校验 token，返回用户名；无效/过期返回 None。"""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    _, body, sig = parts
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        name, _, exp = _b64d(body).decode("utf-8").rpartition("|")
        if not name or time.time() > int(exp):
            return None
    except (ValueError, UnicodeDecodeError):
        return None
    return name
