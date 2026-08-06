from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError

from .auth_store import get_user
from .config import settings


bearer = HTTPBearer(auto_error=False)
_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_lock = Lock()
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5


def ensure_secret_file(path: Path | None = None) -> Path:
    target = path or settings.jwt_secret_file
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(secrets.token_hex(64), encoding="ascii")
        target.chmod(0o600)
    return target


def _secret() -> str:
    path = settings.jwt_secret_file
    if not path.exists():
        raise RuntimeError(f"JWT secret file is missing: {path}")
    value = path.read_text(encoding="ascii").strip()
    if len(value) < 64:
        raise RuntimeError("JWT secret must contain at least 64 characters")
    return value


def create_access_token(user: dict) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.token_minutes)
    payload = {
        "sub": user["username"],
        "ver": int(user["token_version"]),
        "iat": now,
        "nbf": now,
        "exp": expires,
        "jti": secrets.token_urlsafe(16),
        "aud": "so-data-api",
        "iss": "so-data-api",
    }
    token = jwt.encode(payload, _secret(), algorithm="HS256")
    return token, settings.token_minutes * 60


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            audience="so-data-api",
            issuer="so-data-api",
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": "Token is invalid or expired"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "missing_token", "message": "Bearer token is required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(credentials.credentials)
    username = payload.get("sub")
    user = get_user(str(username)) if username else None
    if (
        not user
        or not bool(user["enabled"])
        or int(payload.get("ver", -1)) != int(user["token_version"])
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "revoked_token", "message": "Account or token was revoked"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def login_key(request: Request, username: str) -> str:
    client = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not client and request.client:
        client = request.client.host
    return f"{client or 'unknown'}:{username.lower()}"


def check_login_allowed(key: str) -> None:
    now = time.monotonic()
    with _login_lock:
        q = _login_attempts[key]
        while q and now - q[0] > LOGIN_WINDOW_SECONDS:
            q.popleft()
        if len(q) >= LOGIN_MAX_FAILURES:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "login_rate_limited",
                    "message": "Too many failed login attempts; retry later",
                },
            )


def record_login_failure(key: str) -> None:
    with _login_lock:
        _login_attempts[key].append(time.monotonic())


def clear_login_failures(key: str) -> None:
    with _login_lock:
        _login_attempts.pop(key, None)

