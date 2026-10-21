from fastapi.testclient import TestClient

from shoppingbench_foundry.app import app, get_store
from shoppingbench_foundry.store import InMemoryProductStore

PRODUCT = {
    "product_id": "p1",
    "shop_id": "s1",
    "title": "Orange Basic Calculator",
    "price": 200.0,
    "sold_count": 5,
    "service": ["COD"],
    "sku_options": {"1": {"color": "orange"}},
    "attributes": {"calculator_type": ["basic"]},
    "description": "Battery calculator",
    "short_description": "",
}


def test_original_tool_contracts_and_grader_endpoint(monkeypatch):
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    app.dependency_overrides[get_store] = lambda: InMemoryProductStore([PRODUCT])
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}

    search = client.get(
        "/find_product",
        params={"q": "calculator", "page": 1},
        headers=headers,
    )
    assert search.status_code == 200
    assert search.json()[0]["product_id"] == "p1"

    details = client.get(
        "/view_product_information",
        params={"product_ids": "p1"},
        headers=headers,
    )
    assert details.status_code == 200
    assert details.json()[0]["attributes"]["calculator_type"] == ["basic"]

    grade = client.post(
        "/grade/v3",
        json={
            "sample": {
                "output_tools": [
                    {
                        "function": {
                            "name": "find_product",
                            "arguments": {"q": "calculator", "page": 1},
                        }
                    },
                    {
                        "function": {
                            "name": "view_product_information",
                            "arguments": {"product_ids": "p1"},
                        }
                    },
                    {"function": {"name": "recommend_product", "arguments": {"product_ids": "p1"}}},
                    {"function": {"name": "terminate", "arguments": {}}},
                ],
                "assistant_text": "I recommend p1 because it matches the request.",
            },
            "item": {"task": "product", "reward": {"product_id": "p1"}},
        },
        headers=headers,
    )
    assert grade.status_code == 200
    assert grade.json() == {"score": 1.0}

    web_v5_grade = client.post(
        "/grade/web/v5",
        json={
            "sample": {
                "output_tools": [
                    {
                        "function": {
                            "name": "find_product",
                            "arguments": {"q": "Jason Statham mug", "page": 1},
                        }
                    },
                    {
                        "function": {
                            "name": "view_product_information",
                            "arguments": {"product_ids": "1234567890"},
                        }
                    },
                    {
                        "function": {
                            "name": "recommend_product",
                            "arguments": {"product_ids": "1234567890"},
                        }
                    },
                    {
                        "function": {
                            "name": "terminate",
                            "arguments": {
                                "product_ids": "1234567890",
                                "resolved_clue": "Jason Statham",
                                "final_answer": (
                                    "The clue resolves to Jason Statham. Product "
                                    "1234567890 is a ceramic coffee mug matching the clue."
                                ),
                            },
                        }
                    },
                ]
            },
            "item": {
                "task": "web",
                "reward": {
                    "product_id": "1234567890",
                    "title": "Jason Statham Ceramic Coffee Mug",
                },
                "knowledge_attribute": "Jason Statham",
            },
        },
        headers=headers,
    )
    assert web_v5_grade.status_code == 200
    assert web_v5_grade.json() == {"score": 1.0}

    rft_tool = client.post(
        "/rft/tools/find_product",
        json={
            "type": "function_call",
            "id": "fc-1",
            "call_id": "call-1",
            "name": "find_product",
            "arguments": '{"q":"calculator","page":1}',
            "trace_id": "trace-1",
            "item": {},
        },
        headers=headers,
    )
    assert rft_tool.status_code == 200
    assert rft_tool.json()["type"] == "function_call_output"
    assert rft_tool.json()["call_id"] == "call-1"
    assert rft_tool.json()["id"] == "fc-1"

    monkeypatch.setattr(
        "shoppingbench_foundry.app.search_web",
        lambda query: {"answer": f"Resolved: {query}", "sources": ["https://example.com"]},
    )
    web_search = client.post(
        "/rft/tools/web_search",
        json={
            "type": "function_call",
            "id": "fc-2",
            "call_id": "call-2",
            "name": "web_search",
            "arguments": '{"query":"Who voiced Sergeant Waters?"}',
            "trace_id": "trace-2",
            "item": {},
        },
        headers=headers,
    )
    assert web_search.status_code == 200
    assert "Resolved: Who voiced Sergeant Waters?" in web_search.json()["output"]

    app.dependency_overrides.clear()


def test_tool_api_fails_closed_without_token(monkeypatch):
    monkeypatch.delenv("SHOPPINGBENCH_API_TOKEN", raising=False)
    monkeypatch.delenv("SHOPPINGBENCH_ALLOW_ANONYMOUS", raising=False)
    client = TestClient(app)

    response = client.get("/find_product", params={"q": "calculator", "page": 1})

    assert response.status_code == 503
    assert response.json() == {"detail": "Bearer authentication is not configured"}


def test_swagger_exposes_one_bearer_authorization_flow(monkeypatch):
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    schema = TestClient(app).get("/openapi.json").json()

    assert schema["components"]["securitySchemes"] == {
        "HTTPBearer": {
            "type": "http",
            "description": "Paste the ShoppingBench API token. Swagger adds the Bearer prefix.",
            "scheme": "bearer",
        }
    }
    assert schema["paths"]["/find_product"]["get"]["security"] == [{"HTTPBearer": []}]
    assert not any(
        parameter["name"].lower() == "authorization"
        for parameter in schema["paths"]["/find_product"]["get"].get("parameters", [])
    )


def test_web_search_grade_route_retries_only_unreachable_judge(monkeypatch):
    import httpx
    import pytest
    from openai import APIConnectionError

    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    monkeypatch.setenv("RFT_GRADER_TRANSPORT_BACKOFF_SECONDS", "0")
    calls = []
    outcomes = []

    def judge(payload):
        calls.append(payload)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def unreachable():
        return APIConnectionError(request=httpx.Request("POST", "https://judge.invalid"))

    monkeypatch.setattr("shoppingbench_foundry.app.web_search_v6_endpoint_grade", judge)
    client = TestClient(app, raise_server_exceptions=True)
    headers = {"Authorization": "Bearer test-token"}
    body = {"sample": {}, "item": {}}

    def throttled():
        from openai import RateLimitError

        request = httpx.Request("POST", "https://judge.invalid")
        response = httpx.Response(429, headers={"retry-after-ms": "0"}, request=request)
        return RateLimitError("rate limited", response=response, body=None)

    outcomes[:] = [unreachable(), throttled(), {"score": 0.9}]
    response = client.post("/grade/web-search/v6", json=body, headers=headers)
    assert (response.status_code, response.json(), len(calls)) == (200, {"score": 0.9}, 3)

    calls.clear()
    monkeypatch.setenv("RFT_GRADER_TRANSPORT_ATTEMPTS", "2")
    outcomes[:] = [unreachable(), unreachable()]
    response = client.post("/grade/web-search/v6", json=body, headers=headers)
    assert (response.status_code, len(calls)) == (503, 2)

    calls.clear()
    outcomes[:] = [ValueError("malformed sample")]
    with pytest.raises(ValueError):
        client.post("/grade/web-search/v6", json=body, headers=headers)
    assert len(calls) == 1
