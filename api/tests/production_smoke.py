from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    base = os.environ["SO_DATA_API_URL"].rstrip("/")
    username = os.environ["SO_DATA_API_USERNAME"]
    password = os.environ["SO_DATA_API_PASSWORD"]
    ca = os.environ["SO_DATA_API_CA_CERT"]

    with httpx.Client(base_url=base, verify=ca, timeout=15) as client:
        login = client.post(
            "/v1/auth/login",
            json={"username": username, "password": password},
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        me = client.get("/v1/me", headers=headers)
        me.raise_for_status()
        assert me.json()["username"] == username
        print("login: ok")

        schema = client.get("/v1/schema/tables", headers=headers)
        schema.raise_for_status()
        names = {item["name"] for item in schema.json()["items"]}
        assert names == {"provider_contract", "product_flow_v"}
        print(f"schema permission: ok ({len(names)} objects)")

        catalog = client.get("/v1/catalog", headers=headers)
        catalog.raise_for_status()
        catalog_names = {
            item["name"] for item in catalog.json()["resources"]
        }
        assert catalog_names == {"provider_contract", "product_flow_v"}
        print("fixed API catalog: ok")

        search = client.post(
            "/v1/data/provider_contract/search",
            headers=headers,
            json={
                "fields": ["客户编码", "客户名称", "客户城市"],
                "filters": [
                    {"field": "客户城市", "operator": "eq", "value": "杭州市"}
                ],
                "limit": 2,
            },
        )
        search.raise_for_status()
        assert search.json()["row_count"] > 0
        print("fixed search API: ok")

        aggregate = client.post(
            "/v1/data/provider_contract/aggregate",
            headers=headers,
            json={
                "group_by": ["客户城市"],
                "metrics": [
                    {
                        "function": "count_distinct",
                        "field": "客户编码",
                        "alias": "服务商数",
                    }
                ],
                "order_by": [{"field": "服务商数", "direction": "desc"}],
                "limit": 20,
            },
        )
        aggregate.raise_for_status()
        assert aggregate.json()["row_count"] > 0
        print("fixed aggregate API: ok")

        resolver = client.get("/v1/resolver/status", headers=headers)
        resolver.raise_for_status()
        print(
            "resolver status: ok "
            f"(auth={resolver.json()['llm_auth_configured']})"
        )

        query = client.post(
            "/v1/query",
            headers=headers,
            json={
                "sql": "SELECT 客户城市, COUNT(*) AS n "
                       "FROM provider_contract GROUP BY 客户城市 ORDER BY n DESC",
                "max_rows": 20,
            },
        )
        query.raise_for_status()
        body = query.json()
        assert body["row_count"] > 0
        assert body["referenced_tables"] == ["provider_contract"]
        print(f"production query: ok ({body['row_count']} rows)")

        forbidden = client.post(
            "/v1/query",
            headers=headers,
            json={"sql": "SELECT * FROM app_user"},
        )
        assert forbidden.status_code == 403, forbidden.text
        assert forbidden.json()["detail"]["code"] == "query_forbidden"
        print("security table denial: ok")

        write = client.post(
            "/v1/query",
            headers=headers,
            json={"sql": "DELETE FROM provider_contract"},
        )
        assert write.status_code == 400, write.text
        assert write.json()["detail"]["code"] == "select_only"
        print("write denial: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
