from __future__ import annotations

import importlib
import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def make_client(tmp_path: Path):
    data_db = tmp_path / "business.db"
    conn = sqlite3.connect(data_db)
    conn.executescript(
        """
        CREATE TABLE provider_contract (客户编码 TEXT, 客户城市 TEXT, 金额 REAL);
        INSERT INTO provider_contract VALUES ('P1', '杭州市', 100);
        INSERT INTO provider_contract VALUES ('P2', '宁波市', 200);
        CREATE TABLE app_user (username TEXT, password_hash TEXT);
        INSERT INTO app_user VALUES ('secret', 'hash');
        """
    )
    conn.commit()
    conn.close()
    database_doc = tmp_path / "数据库说明.md"
    database_doc.write_text(
        """
# 数据库说明
## 域 2：服务商
### provider_contract
- **用途**：服务商签约主数据。
""".strip(),
        encoding="utf-8",
    )

    os.environ["SO_DATA_API_DB_PATH"] = str(data_db)
    os.environ["SO_DATA_API_AUTH_DB"] = str(tmp_path / "auth.db")
    os.environ["SO_DATA_API_JWT_SECRET_FILE"] = str(tmp_path / "jwt-secret")
    os.environ["SO_DATA_API_DATABASE_DOC"] = str(database_doc)
    os.environ["SO_DATA_API_CATALOG_FILE"] = str(tmp_path / "api-catalog.json")
    os.environ["SO_DATA_API_QUERY_TIMEOUT_MS"] = "2000"

    import app.config
    import app.auth_store
    import app.security
    import app.data_access
    import app.catalog
    import app.fixed_api
    import app.resolver
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.auth_store)
    importlib.reload(app.security)
    importlib.reload(app.data_access)
    importlib.reload(app.catalog)
    importlib.reload(app.fixed_api)
    importlib.reload(app.resolver)
    importlib.reload(app.main)

    app.auth_store.ensure_auth_schema()
    app.security.ensure_secret_file()
    app.auth_store.upsert_user("operator", "correct-horse-battery", max_rows=10)
    app.auth_store.set_table_patterns("operator", ["*"])
    return TestClient(app.main.app)


def login(client: TestClient) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"username": "operator", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_login_schema_and_select(tmp_path):
    client = make_client(tmp_path)
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}
    tables = client.get("/v1/schema/tables", headers=headers)
    assert tables.status_code == 200
    assert [x["name"] for x in tables.json()["items"]] == ["provider_contract"]

    result = client.post(
        "/v1/query",
        headers=headers,
        json={
            "sql": "SELECT 客户编码, 金额 FROM provider_contract "
                   "WHERE 客户城市 = :city ORDER BY 客户编码",
            "params": {"city": "杭州市"},
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["rows"] == [["P1", 100.0]]


def test_security_table_and_write_are_denied(tmp_path):
    client = make_client(tmp_path)
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}

    secret = client.post(
        "/v1/query",
        headers=headers,
        json={"sql": "SELECT * FROM app_user"},
    )
    assert secret.status_code == 403

    write = client.post(
        "/v1/query",
        headers=headers,
        json={"sql": "DELETE FROM provider_contract"},
    )
    assert write.status_code == 400

    conn = sqlite3.connect(os.environ["SO_DATA_API_DB_PATH"])
    assert conn.execute("SELECT COUNT(*) FROM provider_contract").fetchone()[0] == 2
    conn.close()


def test_row_cap_and_token_revocation(tmp_path):
    client = make_client(tmp_path)
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}
    result = client.post(
        "/v1/query",
        headers=headers,
        json={"sql": "SELECT * FROM provider_contract ORDER BY 客户编码", "max_rows": 1},
    )
    assert result.status_code == 200
    assert result.json()["row_count"] == 1
    assert result.json()["truncated"] is True

    import app.auth_store
    app.auth_store.revoke_tokens("operator")
    assert client.get("/v1/me", headers=headers).status_code == 401


def test_fixed_catalog_search_and_aggregate(tmp_path):
    client = make_client(tmp_path)
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}

    catalog = client.get("/v1/catalog", headers=headers)
    assert catalog.status_code == 200, catalog.text
    assert [item["name"] for item in catalog.json()["resources"]] == [
        "provider_contract"
    ]

    detail = client.get(
        "/v1/catalog/resources/provider_contract",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["description"] == "服务商签约主数据。"

    search = client.post(
        "/v1/data/provider_contract/search",
        headers=headers,
        json={
            "fields": ["客户编码", "客户城市", "金额"],
            "filters": [{"field": "客户城市", "operator": "eq", "value": "杭州市"}],
            "order_by": [{"field": "客户编码", "direction": "asc"}],
            "limit": 10,
        },
    )
    assert search.status_code == 200, search.text
    assert search.json()["rows"] == [["P1", "杭州市", 100.0]]

    aggregate = client.post(
        "/v1/data/provider_contract/aggregate",
        headers=headers,
        json={
            "group_by": ["客户城市"],
            "metrics": [
                {"function": "sum", "field": "金额", "alias": "金额合计"},
                {"function": "count", "field": None, "alias": "服务商数"},
            ],
            "order_by": [{"field": "金额合计", "direction": "desc"}],
            "limit": 10,
        },
    )
    assert aggregate.status_code == 200, aggregate.text
    assert aggregate.json()["rows"] == [
        ["宁波市", 200.0, 1],
        ["杭州市", 100.0, 1],
    ]

    unknown = client.post(
        "/v1/data/provider_contract/search",
        headers=headers,
        json={"fields": ['客户编码" FROM app_user --']},
    )
    assert unknown.status_code == 400
    assert unknown.json()["detail"]["code"] == "unknown_field"


def test_resolver_plan_is_revalidated(tmp_path, monkeypatch):
    client = make_client(tmp_path)
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}

    import app.main

    def fake_resolve(question, context, *, patterns):
        return {
            "status": "resolved",
            "understanding": question,
            "matched_definitions": [],
            "calls": [
                {
                    "call_id": "providers",
                    "method": "POST",
                    "path": "/v1/data/provider_contract/search",
                    "purpose": "查询服务商",
                    "body": {"fields": ["客户编码"], "limit": 10},
                    "depends_on": [],
                }
            ],
            "clarifications": [],
            "confidence": 0.9,
            "catalog_version": "1.0",
            "candidate_resources": ["provider_contract"],
        }

    monkeypatch.setattr(app.main, "resolve_request", fake_resolve)
    response = client.post(
        "/v1/resolve",
        headers=headers,
        json={"question": "查询杭州服务商"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["calls"][0]["path"] == (
        "/v1/data/provider_contract/search"
    )

    import app.resolver
    invalid = app.resolver._validate_calls(
        {
            "status": "resolved",
            "calls": [
                {
                    "call_id": "bad",
                    "method": "POST",
                    "path": "/v1/query",
                    "purpose": "绕过固定接口",
                    "body": {"sql": "SELECT * FROM app_user"},
                    "depends_on": [],
                }
            ],
            "clarifications": [],
            "confidence": 1,
        },
        {"provider_contract": {"name": "provider_contract"}},
    )
    assert invalid["status"] == "unsupported"
    assert invalid["calls"] == []


def test_resolver_keeps_each_requested_business_domain(tmp_path):
    make_client(tmp_path)
    import app.resolver

    names = [
        "provider_contract_v",
        "product_flow_v",
        "visit_record_v",
        "marketing_region_snapshot",
        "district_base",
        "provider_profile",
        "provider_tier_v",
        "provider_tags_v",
        "provider_target",
        "district_si_target",
        "dealer_sandbox",
        "city_snapshot_p3",
    ]
    resources = [
        {
            "name": name,
            "description": f"{name} 业务数据",
            "domain": "测试",
            "columns": [{"name": "客户编码"}],
        }
        for name in names
    ]
    selected = app.resolver._score_resources(
        "分析杭州服务商签约、官方激活、SO、跑动和营销ROI，分别按区县统计",
        resources,
    )
    selected_names = {item["name"] for item in selected}
    assert {
        "provider_contract_v",
        "product_flow_v",
        "visit_record_v",
        "marketing_region_snapshot",
        "district_base",
    } <= selected_names
    assert len(selected) <= app.resolver.settings.resolver_max_resources


def test_resolver_normalizes_safe_operator_aliases(tmp_path):
    make_client(tmp_path)
    import app.resolver

    call = {
        "body": {
            "filters": [
                {"field": "金额", "operator": ">=", "value": 1},
                {"field": "客户编码", "operator": "is_not_null", "value": None},
            ],
            "order_by": [{"field": "金额", "direction": "DESC"}],
            "metrics": [
                {
                    "function": "distinct_count",
                    "field": "客户编码",
                    "alias": "客户数",
                }
            ],
        }
    }
    app.resolver._normalize_call_body(call)
    assert [item["operator"] for item in call["body"]["filters"]] == [
        "gte",
        "not_null",
    ]
    assert call["body"]["order_by"][0]["direction"] == "desc"
    assert call["body"]["metrics"][0]["function"] == "count_distinct"
