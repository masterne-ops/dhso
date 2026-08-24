"""FastAPI application entry point for the funnel dashboard."""
import time
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Dict, List
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, HTMLResponse, RedirectResponse,
                               Response)

from .auth import (LOGIN_PATH, clear_session_cookie, gate, install_auth,
                   set_session_cookie)
from .db import init_schema, seed_factor_defs
from .factors import SEED_DEFS
from .routers.funnel import router as funnel_router

STATIC_DIR = Path(__file__).parent.parent
PAGE = STATIC_DIR / "funnel_v2.html"
OVERVIEW_PAGE = STATIC_DIR / "overview.html"

# Ensure the schema exists as soon as the module is imported, so the app works
# no matter how it is started (uvicorn, TestClient, or an embedded import).
init_schema()
seed_factor_defs(SEED_DEFS)

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>登录 · 服务商转化漏斗</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:flex;align-items:center;
  justify-content:center;background:#f0f2f5;color:#141414;
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
.card{{width:100%;max-width:360px;background:#fff;border-radius:10px;padding:32px 28px;
  box-shadow:0 2px 12px rgba(0,0,0,.08)}}
h1{{margin:0 0 4px;font-size:19px}}
.sub{{margin:0 0 22px;font-size:13px;color:#8c8c8c}}
label{{display:block;font-size:13px;color:#595959;margin:0 0 6px}}
input{{width:100%;padding:9px 11px;font-size:14px;border:1px solid #d9d9d9;
  border-radius:6px;margin-bottom:16px;font-family:inherit}}
input:focus{{outline:none;border-color:#1677ff;box-shadow:0 0 0 2px rgba(22,119,255,.12)}}
button{{width:100%;padding:10px;font-size:14px;font-weight:600;color:#fff;
  background:#1677ff;border:0;border-radius:6px;cursor:pointer}}
button:hover{{background:#0958d9}}
.msg{{padding:9px 11px;border-radius:6px;font-size:13px;margin-bottom:16px}}
.err{{background:#fff2f0;border:1px solid #ffccc7;color:#cf1322}}
.ok{{background:#f6ffed;border:1px solid #b7eb8f;color:#389e0d}}
.tip{{margin:18px 0 0;font-size:12px;color:#bfbfbf;text-align:center}}
</style></head>
<body>
<div class="card">
  <h1>🔻 服务商转化漏斗</h1>
  <p class="sub">浙江 · SO 经营分析</p>
  {message}
  <form method="post" action="{login_path}" autocomplete="on">
    <input type="hidden" name="next" value="{next}">
    <label for="username">账号</label>
    <input id="username" name="username" type="text" autocomplete="username"
           value="{username}" required autofocus>
    <label for="password">密码</label>
    <input id="password" name="password" type="password"
           autocomplete="current-password" required>
    <button type="submit">登录</button>
  </form>
  <p class="tip">admin 为全省账号，其余为地市账号</p>
</div>
</body></html>
"""

# 登录暴力破解兜底：同一 IP 连续失败过多就先冷却一会儿。
# 进程内内存计数即可 —— 单实例部署，重启清零也无所谓。
_FAIL_WINDOW = 300.0
_FAIL_LIMIT = 10
_FAILS: Dict[str, List[float]] = {}


async def _read_form(request: Request) -> Dict[str, str]:
    """
    解析 urlencoded 表单。不走 `request.form()`：新版 Starlette 即使只解析
    urlencoded 也硬依赖 python-multipart，为一个登录框给生产加依赖不划算。
    """
    raw = (await request.body())[:8192]
    parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items() if v}


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _throttled(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _FAILS.get(ip, []) if now - t < _FAIL_WINDOW]
    _FAILS[ip] = hits
    return len(hits) >= _FAIL_LIMIT


def _record_fail(ip: str) -> None:
    _FAILS.setdefault(ip, []).append(time.monotonic())
    if len(_FAILS) > 1024:
        _FAILS.clear()


def _safe_next(raw: str) -> str:
    """只允许站内绝对路径，挡掉 //evil.com 这类开放重定向。"""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    if raw.startswith(LOGIN_PATH):
        return "/"
    return raw


def _login_page(*, next_to: str, username: str = "", error: str = "",
                notice: str = "", status: int = 200) -> HTMLResponse:
    if error:
        message = f'<div class="msg err">{escape(error)}</div>'
    elif notice:
        message = f'<div class="msg ok">{escape(notice)}</div>'
    else:
        message = ""
    html = _LOGIN_HTML.format(
        message=message,
        login_path=LOGIN_PATH,
        next=escape(next_to, quote=True),
        username=escape(username, quote=True),
    )
    return HTMLResponse(html, status_code=status,
                        headers={"Cache-Control": "no-store"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()
    seed_factor_defs(SEED_DEFS)
    yield


app = FastAPI(title="SO漏斗分析 API", version="1.0.0", lifespan=lifespan)
app.include_router(funnel_router)
AUTH_ON = install_auth(app)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness probe. Sits behind auth too, so deploy checks send credentials."""
    return {"ok": True, "auth": AUTH_ON}


@app.get(LOGIN_PATH, include_in_schema=False)
def login_form(request: Request):
    next_to = _safe_next(request.query_params.get("next", "/"))
    if not AUTH_ON:
        return RedirectResponse(next_to, status_code=303)
    notice = "已登出，可换个账号登录。" if request.query_params.get("bye") else ""
    return _login_page(next_to=next_to, notice=notice)


@app.post(LOGIN_PATH, include_in_schema=False)
async def login_submit(request: Request):
    form = await _read_form(request)
    username = form.get("username", "").strip()
    password = form.get("password", "")
    next_to = _safe_next(form.get("next", "/"))

    g = gate()
    if g is None:
        return RedirectResponse(next_to, status_code=303)

    ip = _client_ip(request)
    if _throttled(ip):
        return _login_page(next_to=next_to, username=username, status=429,
                           error="失败次数过多，请 5 分钟后再试。")

    user = g.authenticate(username, password)
    if user is None:
        _record_fail(ip)
        return _login_page(next_to=next_to, username=username, status=401,
                           error="账号或密码不正确。")

    resp = RedirectResponse(next_to, status_code=303)
    resp.headers["Cache-Control"] = "no-store"
    set_session_cookie(resp, user.name)
    return resp


@app.api_route("/logout", methods=["GET", "POST"], include_in_schema=False)
def logout() -> Response:
    """删会话 cookie。服务端说了算，可反复登出，随后能换任意账号登录。"""
    resp = RedirectResponse(f"{LOGIN_PATH}?bye=1", status_code=303)
    resp.headers["Cache-Control"] = "no-store"
    clear_session_cookie(resp)
    return resp


@app.get("/", include_in_schema=False)
def index():
    """Serve the funnel page. no-store keeps browsers off a stale build."""
    return FileResponse(PAGE, media_type="text/html",
                        headers={"Cache-Control": "no-store"})


@app.get("/overview", include_in_schema=False)
def overview_page():
    """管辖总览。no-store 与漏斗页同一套，避免浏览器吃到旧壳。"""
    return FileResponse(OVERVIEW_PAGE, media_type="text/html",
                        headers={"Cache-Control": "no-store"})


@app.get("/md.js", include_in_schema=False)
def md_js():
    """思考框 Markdown 渲染。登录后才能拉，和页面同一套闸门。"""
    return FileResponse(STATIC_DIR / "md.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-store"})
