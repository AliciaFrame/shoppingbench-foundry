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
        "/grade",
        json={
            "sample": {
                "output_tools": [
                    {"function": {"name": "recommend_product", "arguments": {"product_ids": "p1"}}},
                    {"function": {"name": "terminate", "arguments": {}}},
                ]
            },
            "item": {"task": "product", "reward": {"product_id": "p1"}},
        },
        headers=headers,
    )
    assert grade.status_code == 200
    assert grade.json() == {"score": 1.0}

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
    app.dependency_overrides.clear()


def test_tool_api_fails_closed_without_token(monkeypatch):
    monkeypatch.delenv("SHOPPINGBENCH_API_TOKEN", raising=False)
    monkeypatch.delenv("SHOPPINGBENCH_ALLOW_ANONYMOUS", raising=False)
    client = TestClient(app)

    response = client.get("/find_product", params={"q": "calculator", "page": 1})

    assert response.status_code == 503
    assert response.json() == {"detail": "Bearer authentication is not configured"}
