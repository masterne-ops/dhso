from __future__ import annotations

import json
import os

import httpx


def main() -> int:
    base = os.environ["SO_DATA_API_URL"].rstrip("/")
    username = os.environ["SO_DATA_API_USERNAME"]
    password = os.environ["SO_DATA_API_PASSWORD"]
    ca = os.environ["SO_DATA_API_CA_CERT"]

    with httpx.Client(base_url=base, verify=ca, timeout=120) as client:
        login = client.post(
            "/v1/auth/login",
            json={"username": username, "password": password},
        )
        login.raise_for_status()
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}"
        }

        status = client.get("/v1/resolver/status", headers=headers)
        status.raise_for_status()
        assert status.json()["llm_auth_configured"] is True
        print(
            "resolver configured: ok "
            f"(model={status.json().get('model')})"
        )

        resolved = client.post(
            "/v1/resolve",
            headers=headers,
            json={
                "question": "按客户城市统计签约服务商数量，返回数量最多的前5个城市"
            },
        )
        resolved.raise_for_status()
        plan = resolved.json()
        assert plan["status"] == "resolved", json.dumps(plan, ensure_ascii=False)
        assert plan["calls"], json.dumps(plan, ensure_ascii=False)
        assert all("/v1/data/" in call["path"] for call in plan["calls"])
        assert "sql" not in json.dumps(plan, ensure_ascii=False).lower()
        print(
            "natural-language resolution: ok "
            f"({len(plan['calls'])} registered calls)"
        )

        results = {}
        for call in plan["calls"]:
            assert all(dep in results for dep in call["depends_on"])
            response = client.request(
                call["method"],
                call["path"],
                headers=headers,
                json=call["body"],
            )
            response.raise_for_status()
            results[call["call_id"]] = response.json()
        assert any(result.get("row_count", 0) > 0 for result in results.values())
        print("resolved fixed API execution: ok")
        print(json.dumps({
            "understanding": plan["understanding"],
            "calls": [
                {
                    "method": call["method"],
                    "path": call["path"],
                    "purpose": call["purpose"],
                }
                for call in plan["calls"]
            ],
            "result_rows": {
                call_id: value.get("row_count")
                for call_id, value in results.items()
            },
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
