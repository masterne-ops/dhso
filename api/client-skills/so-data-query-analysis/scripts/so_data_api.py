#!/usr/bin/env python3
"""Deterministic client for the SO production data API.

Uses only the Python standard library. Credentials are read from environment
variables and are never written to output.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_URL = "https://121.196.152.24"
ALLOWED_PLAN_PREFIXES = (
    "/v1/data/",
    "/v1/catalog/resources/",
)


class ClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        payload: Any = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload

    def as_dict(self) -> dict:
        return {
            "message": str(self),
            "status": self.status,
            "code": self.code,
        }


def _skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _environment() -> dict:
    ca_default = _skill_root() / "assets" / "so-data-api-ca.crt"
    values = {
        "url": os.getenv("SO_DATA_API_URL", DEFAULT_URL).rstrip("/"),
        "username": os.getenv("SO_DATA_API_USERNAME", ""),
        "password": os.getenv("SO_DATA_API_PASSWORD", ""),
        "ca": os.getenv("SO_DATA_API_CA_CERT", str(ca_default)),
        "timeout": int(os.getenv("SO_DATA_API_TIMEOUT", "120")),
    }
    missing = [
        name
        for name in ("username", "password")
        if not values[name]
    ]
    if missing:
        raise ClientError(
            "Missing required environment variables: "
            + ", ".join(f"SO_DATA_API_{name.upper()}" for name in missing),
            code="missing_configuration",
        )
    if not Path(values["ca"]).is_file():
        raise ClientError(
            f"CA certificate does not exist: {values['ca']}",
            code="missing_ca_certificate",
        )
    if not values["url"].startswith("https://"):
        raise ClientError(
            "SO_DATA_API_URL must use HTTPS",
            code="insecure_url",
        )
    return values


class SODataClient:
    def __init__(self) -> None:
        config = _environment()
        self.base_url = config["url"]
        self.username = config["username"]
        self.password = config["password"]
        self.timeout = config["timeout"]
        self.ssl_context = ssl.create_default_context(cafile=config["ca"])
        self.token: str | None = None

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        authenticated: bool = True,
        retries: int = 2,
    ) -> dict:
        if not path.startswith("/"):
            raise ClientError("API path must start with /", code="invalid_path")
        if "://" in path or not path.startswith("/v1/") and path != "/healthz":
            raise ClientError("External or unregistered URL is forbidden", code="invalid_path")
        if authenticated and not self.token:
            self.login()
        headers = {
            "Accept": "application/json",
            "User-Agent": "OpenClaw-SO-Data-Skill/1.0",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        for attempt in range(retries + 1):
            # urllib may mutate a Request while proxying. Recreate it for every
            # retry so the request target remains an origin-form API path.
            request = urllib.request.Request(
                self.base_url + path,
                data=data,
                headers=headers,
                method=method,
            )
            try:
                with urllib.request.urlopen(
                    request,
                    context=self.ssl_context,
                    timeout=self.timeout,
                ) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"detail": raw[:500]}
                detail = payload.get("detail", payload)
                if isinstance(detail, dict):
                    code = detail.get("code")
                    message = detail.get("message") or str(detail)
                else:
                    code = None
                    message = str(detail)
                if exc.code in {429, 503} and attempt < retries:
                    retry_after = exc.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 2 ** attempt
                    time.sleep(min(delay, 10))
                    continue
                raise ClientError(
                    message,
                    status=exc.code,
                    code=code,
                    payload=payload,
                ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise ClientError(
                    f"API connection failed: {exc}",
                    code="connection_failed",
                ) from exc
        raise ClientError("API request failed", code="request_failed")

    def login(self) -> dict:
        result = self._request(
            "POST",
            "/v1/auth/login",
            {"username": self.username, "password": self.password},
            authenticated=False,
            retries=0,
        )
        token = result.get("access_token")
        if not token:
            raise ClientError("Login returned no access token", code="login_failed")
        self.token = token
        return {
            "token_type": result.get("token_type"),
            "expires_in": result.get("expires_in"),
        }

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)

    def resolve(self, question: str, context: str | None = None) -> dict:
        payload = {"question": question}
        if context:
            payload["context"] = context
        return self.post("/v1/resolve", payload)

    @staticmethod
    def _validate_call(call: dict) -> None:
        method = call.get("method")
        path = call.get("path", "")
        if method not in {"GET", "POST"}:
            raise ClientError(
                f"Resolver returned forbidden method: {method}",
                code="unsafe_plan",
            )
        if not any(path.startswith(prefix) for prefix in ALLOWED_PLAN_PREFIXES):
            raise ClientError(
                f"Resolver returned unregistered path: {path}",
                code="unsafe_plan",
            )
        if "://" in path or ".." in path:
            raise ClientError("Resolver returned unsafe path", code="unsafe_plan")
        if method == "GET" and call.get("body") is not None:
            raise ClientError("GET call must have a null body", code="unsafe_plan")
        if method == "POST" and not isinstance(call.get("body"), dict):
            raise ClientError("POST call must have an object body", code="unsafe_plan")

    def execute_plan(self, plan: dict) -> tuple[dict, list[str]]:
        if plan.get("status") != "resolved":
            return {}, []
        pending: dict[str, dict] = {}
        for call in plan.get("calls", []):
            self._validate_call(call)
            call_id = call.get("call_id")
            if not call_id or call_id in pending:
                raise ClientError(
                    "Resolver returned an empty or duplicate call_id",
                    code="invalid_dependencies",
                )
            pending[call_id] = call
        results: dict[str, dict] = {}
        warnings: list[str] = []
        while pending:
            progressed = False
            for call_id, call in list(pending.items()):
                dependencies = call.get("depends_on") or []
                if not isinstance(dependencies, list):
                    raise ClientError(
                        f"Invalid depends_on for {call_id}",
                        code="invalid_dependencies",
                    )
                if not all(dependency in results for dependency in dependencies):
                    continue
                if call["method"] == "GET":
                    result = self.get(call["path"])
                else:
                    result = self.post(call["path"], call["body"])
                results[call_id] = {
                    "call": {
                        "method": call["method"],
                        "path": call["path"],
                        "purpose": call.get("purpose"),
                        "depends_on": dependencies,
                    },
                    "data": result,
                }
                if result.get("truncated") is True:
                    warnings.append(
                        f"{call_id} returned truncated=true; do not treat it as full data"
                    )
                pending.pop(call_id)
                progressed = True
            if not progressed:
                unresolved = {
                    call_id: call.get("depends_on", [])
                    for call_id, call in pending.items()
                }
                raise ClientError(
                    f"Unresolvable call dependencies: {unresolved}",
                    code="invalid_dependencies",
                )
        return results, warnings


def _emit(payload: dict, output: str | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        summary = {
            "saved_to": str(target),
            "ok": payload.get("ok"),
            "plan_status": payload.get("plan", {}).get("status"),
            "result_calls": list(payload.get("results", {})),
            "warnings": payload.get("warnings", []),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve natural-language SO data needs and execute fixed APIs"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight", help="check API, login, permission, and resolver status")
    sub.add_parser("catalog", help="list authorized registered resources")

    describe = sub.add_parser("describe", help="show one registered resource")
    describe.add_argument("resource")

    resolve = sub.add_parser("resolve", help="resolve a question without fetching data")
    resolve_question = resolve.add_mutually_exclusive_group(required=True)
    resolve_question.add_argument("--question")
    resolve_question.add_argument("--question-file")
    resolve.add_argument("--context")
    resolve.add_argument("--context-file")
    resolve.add_argument("--output")

    run = sub.add_parser("run", help="resolve and execute the registered calls")
    run_question = run.add_mutually_exclusive_group(required=True)
    run_question.add_argument("--question")
    run_question.add_argument("--question-file")
    run.add_argument("--context")
    run.add_argument("--context-file")
    run.add_argument("--output")
    return parser


def _text_argument(args, name: str) -> str | None:
    direct = getattr(args, name, None)
    file_name = getattr(args, f"{name}_file", None)
    if direct is not None and file_name:
        raise ClientError(
            f"Use either --{name.replace('_', '-')} or --{name.replace('_', '-')}-file",
            code="invalid_arguments",
        )
    if file_name:
        path = Path(file_name).expanduser().resolve()
        if not path.is_file():
            raise ClientError(f"Input file does not exist: {path}", code="missing_input")
        value = path.read_text(encoding="utf-8").strip()
    else:
        value = direct
    if name == "question" and not value:
        raise ClientError("Question must not be empty", code="invalid_arguments")
    return value


def main() -> int:
    args = _parser().parse_args()
    try:
        client = SODataClient()
        if args.command == "preflight":
            health = client._request(
                "GET",
                "/healthz",
                authenticated=False,
            )
            login = client.login()
            result = {
                "ok": True,
                "health": health,
                "login": login,
                "account": client.get("/v1/me"),
                "resolver": client.get("/v1/resolver/status"),
            }
            _emit(result)
            return 0
        if args.command == "catalog":
            _emit({"ok": True, "catalog": client.get("/v1/catalog")})
            return 0
        if args.command == "describe":
            if not args.resource.replace("_", "").isalnum():
                raise ClientError("Invalid resource name", code="invalid_resource")
            _emit({
                "ok": True,
                "resource": client.get(f"/v1/catalog/resources/{args.resource}"),
            })
            return 0
        if args.command == "resolve":
            question = _text_argument(args, "question")
            context = _text_argument(args, "context")
            plan = client.resolve(question, context)
            _emit({
                "ok": plan.get("status") == "resolved",
                "plan": plan,
            }, args.output)
            return 0
        if args.command == "run":
            question = _text_argument(args, "question")
            context = _text_argument(args, "context")
            plan = client.resolve(question, context)
            results, warnings = client.execute_plan(plan)
            _emit({
                "ok": plan.get("status") == "resolved",
                "question": question,
                "plan": plan,
                "results": results,
                "warnings": warnings,
                "executed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, args.output)
            return 0
        raise ClientError("Unknown command", code="invalid_command")
    except ClientError as exc:
        print(json.dumps({
            "ok": False,
            "error": exc.as_dict(),
        }, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
