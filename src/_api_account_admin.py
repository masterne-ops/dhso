"""SO 生产数据 API 账号管理。

仅供 Streamlit 的 admin 页面在服务器本机调用。所有写操作都通过
``python -m app.cli`` 执行，避免主应用直接打开 API 的认证数据库。
密码只通过子进程 stdin 传递，不进入命令行参数或日志。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable


API_WORKDIR = Path(os.getenv("SO_DATA_API_ADMIN_WORKDIR", "/opt/so-data-api"))
API_PYTHON = Path(
    os.getenv("SO_DATA_API_ADMIN_PYTHON", "/opt/so-data-api/venv/bin/python")
)
API_RUN_AS = os.getenv("SO_DATA_API_ADMIN_RUN_AS", "so-data-api").strip()
API_CATALOG = Path(
    os.getenv(
        "SO_DATA_API_ADMIN_CATALOG",
        "/opt/so-data-api/knowledge/api-catalog.json",
    )
)
COMMAND_TIMEOUT_SECONDS = 30
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ApiAccountAdminError(RuntimeError):
    """API 账号管理命令失败。"""


def _command(args: list[str]) -> list[str]:
    base = [str(API_PYTHON), "-m", "app.cli", *args]
    if os.geteuid() == 0 and API_RUN_AS:
        return ["/usr/sbin/runuser", "-u", API_RUN_AS, "--", *base]
    return base


def _run_cli(args: list[str], *, password: str | None = None) -> str:
    if not API_WORKDIR.is_dir() or not API_PYTHON.is_file():
        raise ApiAccountAdminError("数据接口服务未安装或管理程序路径不可用")
    stdin_text = f"{password}\n" if password is not None else None
    try:
        result = subprocess.run(
            _command(args),
            cwd=str(API_WORKDIR),
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ApiAccountAdminError("账号管理操作超时，请检查数据接口服务") from exc
    except OSError as exc:
        raise ApiAccountAdminError(f"无法执行账号管理程序：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "未知错误").strip().splitlines()[-1]
        raise ApiAccountAdminError(f"账号管理失败：{detail}")
    return result.stdout.strip()


def validate_username(username: str) -> str:
    value = (username or "").strip()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("用户名仅支持1—64位字母、数字、点、下划线和短横线")
    return value


def validate_password(password: str) -> None:
    if len(password or "") < 12:
        raise ValueError("密码至少需要12位")


def list_api_users() -> list[dict]:
    output = _run_cli(["user-list"])
    try:
        value = json.loads(output or "[]")
    except json.JSONDecodeError as exc:
        raise ApiAccountAdminError("账号管理程序返回了无法识别的数据") from exc
    if not isinstance(value, list):
        raise ApiAccountAdminError("账号管理程序返回格式错误")
    return value


def list_catalog_resources() -> list[dict]:
    if not API_CATALOG.is_file():
        raise ApiAccountAdminError("固定接口注册目录不存在")
    try:
        catalog = json.loads(API_CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiAccountAdminError("固定接口注册目录无法读取") from exc
    resources = catalog.get("resources", [])
    if not isinstance(resources, list):
        raise ApiAccountAdminError("固定接口注册目录格式错误")
    return sorted(
        [item for item in resources if isinstance(item, dict) and item.get("name")],
        key=lambda item: (str(item.get("domain", "")), str(item["name"])),
    )


def create_api_user(
    username: str,
    password: str,
    *,
    full_name: str = "",
    max_rows: int = 5000,
) -> None:
    username = validate_username(username)
    validate_password(password)
    max_rows = int(max_rows)
    if not 1 <= max_rows <= 20000:
        raise ValueError("单次最大行数必须在1—20000之间")
    if any(item["username"] == username for item in list_api_users()):
        raise ValueError(f"用户名 {username} 已存在")
    args = [
        "user-add",
        username,
        "--full-name",
        (full_name or "").strip(),
        "--max-rows",
        str(max_rows),
        "--password-stdin",
    ]
    _run_cli(args, password=password)


def reset_api_password(username: str, password: str) -> None:
    username = validate_username(username)
    validate_password(password)
    _run_cli(["passwd", username, "--password-stdin"], password=password)


def replace_api_permissions(username: str, patterns: Iterable[str]) -> int:
    username = validate_username(username)
    cleaned = sorted({str(value).strip() for value in patterns if str(value).strip()})
    if not cleaned:
        raise ValueError("至少选择一个数据资源；如需停用账号，请使用停用功能")
    if any("," in value for value in cleaned):
        raise ValueError("权限名称不能包含逗号")
    _run_cli(["grant", username, "--tables", ",".join(cleaned)])
    return len(cleaned)


def set_api_user_enabled(username: str, enabled: bool) -> None:
    username = validate_username(username)
    _run_cli(["enable" if enabled else "disable", username])


def revoke_api_tokens(username: str) -> None:
    username = validate_username(username)
    _run_cli(["revoke", username])


def delete_api_user(username: str) -> None:
    username = validate_username(username)
    _run_cli(["user-delete", username])
