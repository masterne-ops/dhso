from __future__ import annotations

import hashlib
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse

from .auth_store import (
    authenticate,
    ensure_auth_schema,
    write_audit,
)
from .catalog import load_catalog, visible_resource, visible_resources
from .config import settings
from .data_access import (
    QueryRejected,
    describe_object,
    execute_readonly,
    list_visible_objects,
    open_data_db,
    result_to_csv,
)
from .fixed_api import execute_aggregate, execute_search
from .models import (
    LoginRequest,
    QueryRequest,
    ResolveRequest,
    ResourceAggregateRequest,
    ResourceSearchRequest,
    TokenResponse,
)
from .resolver import ResolverUnavailable, resolve_request
from .security import (
    check_login_allowed,
    clear_login_failures,
    create_access_token,
    current_user,
    ensure_secret_file,
    login_key,
    record_login_failure,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_auth_schema()
    if not settings.jwt_secret_file.exists():
        ensure_secret_file()
    yield


app = FastAPI(
    title=settings.api_title,
    version="1.0.0",
    description=(
        "Authenticated, audited, read-only access to the SO production SQLite database. "
        "All data-changing SQL and security tables are denied."
    ),
    lifespan=lifespan,
)


def _request_id(request: Request) -> str:
    return request.headers.get("x-request-id") or uuid.uuid4().hex


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else None)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = _request_id(request)
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/healthz", tags=["system"])
def health() -> dict:
    try:
        conn = open_data_db()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        stat = settings.data_db.stat()
        return {
            "status": "ok",
            "database_readable": True,
            "database_size_bytes": stat.st_size,
            "database_mtime": int(stat.st_mtime),
        }
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "database_unavailable", "message": "Database is unavailable"},
        )


@app.post("/v1/auth/login", response_model=TokenResponse, tags=["auth"])
def login(payload: LoginRequest, request: Request) -> TokenResponse:
    key = login_key(request, payload.username)
    check_login_allowed(key)
    user = authenticate(payload.username, payload.password)
    if not user:
        record_login_failure(key)
        write_audit(
            request_id=request.state.request_id,
            username=payload.username,
            client_ip=_client_ip(request),
            endpoint="/v1/auth/login",
            status_code=401,
            error_code="bad_credentials",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "bad_credentials", "message": "Invalid username or password"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    clear_login_failures(key)
    token, expires_in = create_access_token(user)
    write_audit(
        request_id=request.state.request_id,
        username=user["username"],
        client_ip=_client_ip(request),
        endpoint="/v1/auth/login",
        status_code=200,
    )
    return TokenResponse(access_token=token, expires_in=expires_in)


@app.get("/v1/me", tags=["auth"])
def me(user: dict = Depends(current_user)) -> dict:
    return {
        "username": user["username"],
        "full_name": user["full_name"],
        "max_rows": min(int(user["max_rows"]), settings.hard_max_rows),
        "table_patterns": user["table_patterns"],
    }


@app.get("/v1/schema/tables", tags=["schema"])
def tables(user: dict = Depends(current_user)) -> dict:
    objects = list_visible_objects(user["table_patterns"])
    return {"count": len(objects), "items": objects}


@app.get("/v1/schema/tables/{name}", tags=["schema"])
def table_schema(name: str, user: dict = Depends(current_user)) -> dict:
    try:
        return describe_object(name, user["table_patterns"])
    except QueryRejected as exc:
        code = 404 if exc.code == "table_not_found" else 403
        raise HTTPException(
            status_code=code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@app.get("/v1/catalog", tags=["fixed-data-api"])
def catalog(user: dict = Depends(current_user)) -> dict:
    source = load_catalog()
    resources = visible_resources(user["table_patterns"])
    return {
        "catalog_version": source["catalog_version"],
        "count": len(resources),
        "filter_operators": source["filter_operators"],
        "aggregate_functions": source["aggregate_functions"],
        "resources": [
            {
                "name": item["name"],
                "object_type": item["object_type"],
                "domain": item["domain"],
                "description": item["description"],
                "interfaces": item["interfaces"],
            }
            for item in resources
        ],
    }


@app.get("/v1/catalog/resources/{name}", tags=["fixed-data-api"])
def catalog_resource(name: str, user: dict = Depends(current_user)) -> dict:
    resource = visible_resource(name, user["table_patterns"])
    if not resource:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "resource_not_found",
                "message": "Resource does not exist or is not authorized",
            },
        )
    return resource


def _fixed_query_error(exc: QueryRejected) -> HTTPException:
    status_code = 403 if exc.code in {
        "query_forbidden",
        "table_forbidden",
        "no_data_permission",
    } else 400
    if exc.code == "query_timeout":
        status_code = 408
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def _fixed_result(result, request: Request, resource_name: str) -> dict:
    return {
        "request_id": request.state.request_id,
        "resource": resource_name,
        "columns": result.columns,
        "rows": result.rows,
        "row_count": len(result.rows),
        "truncated": result.truncated,
        "elapsed_ms": result.elapsed_ms,
    }


@app.post("/v1/data/{name}/search", tags=["fixed-data-api"])
def resource_search(
    name: str,
    payload: ResourceSearchRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    resource = visible_resource(name, user["table_patterns"])
    if not resource:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "resource_not_found",
                "message": "Resource does not exist or is not authorized",
            },
        )
    started = time.monotonic()
    try:
        result = execute_search(
            resource,
            payload,
            patterns=user["table_patterns"],
            max_rows=min(int(user["max_rows"]), settings.hard_max_rows),
        )
    except QueryRejected as exc:
        error = _fixed_query_error(exc)
        write_audit(
            request_id=request.state.request_id,
            username=user["username"],
            client_ip=_client_ip(request),
            endpoint=f"/v1/data/{name}/search",
            status_code=error.status_code,
            referenced_tables=[name],
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_code=exc.code,
        )
        raise error from exc
    write_audit(
        request_id=request.state.request_id,
        username=user["username"],
        client_ip=_client_ip(request),
        endpoint=f"/v1/data/{name}/search",
        status_code=200,
        query_sha256=result.query_sha256,
        referenced_tables=[name],
        rows_returned=len(result.rows),
        elapsed_ms=result.elapsed_ms,
    )
    return _fixed_result(result, request, name)


@app.post("/v1/data/{name}/aggregate", tags=["fixed-data-api"])
def resource_aggregate(
    name: str,
    payload: ResourceAggregateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    resource = visible_resource(name, user["table_patterns"])
    if not resource:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "resource_not_found",
                "message": "Resource does not exist or is not authorized",
            },
        )
    started = time.monotonic()
    try:
        result = execute_aggregate(
            resource,
            payload,
            patterns=user["table_patterns"],
            max_rows=min(int(user["max_rows"]), settings.hard_max_rows),
        )
    except QueryRejected as exc:
        error = _fixed_query_error(exc)
        write_audit(
            request_id=request.state.request_id,
            username=user["username"],
            client_ip=_client_ip(request),
            endpoint=f"/v1/data/{name}/aggregate",
            status_code=error.status_code,
            referenced_tables=[name],
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_code=exc.code,
        )
        raise error from exc
    write_audit(
        request_id=request.state.request_id,
        username=user["username"],
        client_ip=_client_ip(request),
        endpoint=f"/v1/data/{name}/aggregate",
        status_code=200,
        query_sha256=result.query_sha256,
        referenced_tables=[name],
        rows_returned=len(result.rows),
        elapsed_ms=result.elapsed_ms,
    )
    return _fixed_result(result, request, name)


@app.get("/v1/resolver/status", tags=["natural-language-entry"])
def resolver_status(user: dict = Depends(current_user)) -> dict:
    configured = bool(
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
    )
    return {
        "claude_binary_available": settings.claude_binary.exists(),
        "llm_auth_configured": configured,
        "model": settings.resolver_model,
        "skill": "resolve-data-api",
        "catalog_version": load_catalog()["catalog_version"],
    }


@app.post("/v1/resolve", tags=["natural-language-entry"])
def resolve(
    payload: ResolveRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    if not user["table_patterns"]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "no_data_permission",
                "message": "Account has no data permission",
            },
        )
    started = time.monotonic()
    request_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()
    try:
        result = resolve_request(
            payload.question,
            payload.context,
            patterns=user["table_patterns"],
        )
    except ResolverUnavailable as exc:
        write_audit(
            request_id=request.state.request_id,
            username=user["username"],
            client_ip=_client_ip(request),
            endpoint="/v1/resolve",
            status_code=503,
            query_sha256=request_hash,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_code="resolver_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "resolver_unavailable", "message": str(exc)},
        ) from exc
    write_audit(
        request_id=request.state.request_id,
        username=user["username"],
        client_ip=_client_ip(request),
        endpoint="/v1/resolve",
        status_code=200,
        query_sha256=request_hash,
        referenced_tables=result.get("candidate_resources", []),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
    result["request_id"] = request.state.request_id
    return result


def _run_query(payload: QueryRequest, user: dict):
    max_allowed = min(int(user["max_rows"]), settings.hard_max_rows)
    requested = payload.max_rows or min(settings.default_max_rows, max_allowed)
    max_rows = min(requested, max_allowed)
    return execute_readonly(
        payload.sql,
        payload.params,
        patterns=user["table_patterns"],
        max_rows=max_rows,
    )


@app.post("/v1/query", tags=["query"])
def query(payload: QueryRequest, request: Request, user: dict = Depends(current_user)) -> dict:
    started = time.monotonic()
    query_hash = hashlib.sha256(payload.sql.encode("utf-8")).hexdigest()
    try:
        result = _run_query(payload, user)
    except QueryRejected as exc:
        status_code = 403 if exc.code in {
            "query_forbidden", "table_forbidden", "no_data_permission"
        } else 400
        if exc.code == "query_timeout":
            status_code = 408
        write_audit(
            request_id=request.state.request_id,
            username=user["username"],
            client_ip=_client_ip(request),
            endpoint="/v1/query",
            status_code=status_code,
            query_sha256=query_hash,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_code=exc.code,
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    write_audit(
        request_id=request.state.request_id,
        username=user["username"],
        client_ip=_client_ip(request),
        endpoint="/v1/query",
        status_code=200,
        query_sha256=result.query_sha256,
        referenced_tables=result.referenced_tables,
        rows_returned=len(result.rows),
        elapsed_ms=result.elapsed_ms,
    )
    return {
        "request_id": request.state.request_id,
        "columns": result.columns,
        "rows": result.rows,
        "row_count": len(result.rows),
        "truncated": result.truncated,
        "elapsed_ms": result.elapsed_ms,
        "referenced_tables": sorted(result.referenced_tables),
    }


@app.post("/v1/query.csv", response_class=PlainTextResponse, tags=["query"])
def query_csv(
    payload: QueryRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> Response:
    try:
        result = _run_query(payload, user)
    except QueryRejected as exc:
        status_code = 403 if exc.code in {
            "query_forbidden", "table_forbidden", "no_data_permission"
        } else 400
        if exc.code == "query_timeout":
            status_code = 408
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    write_audit(
        request_id=request.state.request_id,
        username=user["username"],
        client_ip=_client_ip(request),
        endpoint="/v1/query.csv",
        status_code=200,
        query_sha256=result.query_sha256,
        referenced_tables=result.referenced_tables,
        rows_returned=len(result.rows),
        elapsed_ms=result.elapsed_ms,
    )
    headers = {
        "Content-Disposition": 'attachment; filename="query-result.csv"',
        "X-Result-Truncated": str(result.truncated).lower(),
        "X-Result-Rows": str(len(result.rows)),
    }
    return Response(
        content=result_to_csv(result),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )
