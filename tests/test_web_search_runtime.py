from __future__ import annotations

import importlib
import json
import sys
from types import SimpleNamespace

import httpx


def test_web_search_runtime_extracts_url_citations(monkeypatch):
    monkeypatch.setenv("MAI_OPENAI_BASE_URL", "https://example.test/openai/v1")
    monkeypatch.setenv("MAI_API_KEY", "test-key")
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_URL", "https://tools.example.test")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    monkeypatch.setenv("MAI_MODEL_DEPLOYMENT_NAME", "test-model")
    monkeypatch.setenv("OPTIMIZATION_LOCAL_DIR", "web_search/baseline")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    sys.modules.pop("agents.shared.web_search_runtime", None)
    runtime = importlib.import_module("agents.shared.web_search_runtime")
    response = SimpleNamespace(
        model_dump=lambda mode: {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "title": "Source",
                                    "url": "https://example.test/source",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )

    assert runtime._web_sources(response) == [
        {"title": "Source", "url": "https://example.test/source"}
    ]


def test_web_search_runtime_records_web_search_first(monkeypatch):
    monkeypatch.setenv("MAI_OPENAI_BASE_URL", "https://example.test/openai/v1")
    monkeypatch.setenv("MAI_API_KEY", "test-key")
    monkeypatch.setenv("WEB_SEARCH_OPENAI_BASE_URL", "https://search.example.test/openai/v1")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "search-key")
    monkeypatch.setenv("WEB_SEARCH_MODEL_DEPLOYMENT_NAME", "search-model")
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_URL", "https://tools.example.test")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    monkeypatch.setenv("MAI_MODEL_DEPLOYMENT_NAME", "selection-model")
    monkeypatch.setenv("WEB_SEARCH_SOURCE_LIMIT", "2")
    monkeypatch.setenv("OPTIMIZATION_LOCAL_DIR", "web_search/baseline")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    sys.modules.pop("agents.shared.web_search_runtime", None)
    runtime = importlib.import_module("agents.shared.web_search_runtime")

    web_response = SimpleNamespace(
        output_text="The resolved clue is Violin.",
        output=[],
        usage=None,
        model_dump=lambda mode: {"output": []},
    )
    recommendation = SimpleNamespace(
        id="response-1",
        output=[
            SimpleNamespace(
                type="function_call",
                name="recommend_product",
                arguments='{"product_ids":"123"}',
                call_id="call-1",
            )
        ],
        usage=None,
    )
    termination = SimpleNamespace(
        id="response-2",
        output=[
            SimpleNamespace(
                type="function_call",
                name="terminate",
                arguments=(
                    '{"product_ids":"123","resolved_clue":"Violin",'
                    '"final_answer":"Violin; product_id 123."}'
                ),
                call_id="call-2",
            )
        ],
        output_text="",
        usage=None,
    )
    responses = iter([web_response, recommendation, termination])
    web_requests = []
    selection_requests = []
    monkeypatch.setattr(
        runtime.web_search_client.responses,
        "create",
        lambda **kwargs: web_requests.append(kwargs) or next(responses),
    )
    monkeypatch.setattr(
        runtime.selection_client.responses,
        "create",
        lambda **kwargs: selection_requests.append(kwargs) or next(responses),
    )
    monkeypatch.setattr(
        runtime,
        "_execute_tool",
        lambda name, arguments: {"ok": True},
    )

    output_text, _ = runtime._run_episode("Which instrument did Alec Aitken play?")
    sample = __import__("json").loads(output_text)

    assert sample["output_tools"][0] == {
        "function": {
            "name": "web_search",
            "arguments": {"query": "Which instrument did Alec Aitken play?"},
        }
    }
    assert web_requests[0]["model"] == "search-model"
    assert web_requests[0]["max_output_tokens"] == 256
    assert selection_requests[0]["model"] == "selection-model"
    assert "Grounded web evidence:\nThe resolved clue is Violin." in selection_requests[0][
        "input"
    ]


def test_web_search_runtime_retries_mismatched_termination(monkeypatch):
    monkeypatch.setenv("MAI_OPENAI_BASE_URL", "https://example.test/openai/v1")
    monkeypatch.setenv("MAI_API_KEY", "test-key")
    monkeypatch.setenv("WEB_SEARCH_OPENAI_BASE_URL", "https://search.example.test/openai/v1")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "search-key")
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_URL", "https://tools.example.test")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    monkeypatch.setenv("MAI_MODEL_DEPLOYMENT_NAME", "selection-model")
    monkeypatch.setenv("OPTIMIZATION_LOCAL_DIR", "web_search/baseline")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    sys.modules.pop("agents.shared.web_search_runtime", None)
    runtime = importlib.import_module("agents.shared.web_search_runtime")

    web_response = SimpleNamespace(
        output_text="The resolved clue is Violin.",
        output=[],
        usage=None,
        model_dump=lambda mode: {"output": []},
    )
    recommendation = SimpleNamespace(
        id="response-1",
        output=[
            SimpleNamespace(
                type="function_call",
                name="recommend_product",
                arguments='{"product_ids":"123"}',
                call_id="call-1",
            )
        ],
        usage=None,
    )
    mismatched_termination = SimpleNamespace(
        id="response-2",
        output=[
            SimpleNamespace(
                type="function_call",
                name="terminate",
                arguments=(
                    '{"product_ids":"456","resolved_clue":"Violin",'
                    '"final_answer":"Violin; product_id 456."}'
                ),
                call_id="call-2",
            )
        ],
        output_text="",
        usage=None,
    )
    corrected_termination = SimpleNamespace(
        id="response-3",
        output=[
            SimpleNamespace(
                type="function_call",
                name="terminate",
                arguments=(
                    '{"product_ids":"123","resolved_clue":"Violin",'
                    '"final_answer":"Violin; product_id 123."}'
                ),
                call_id="call-3",
            )
        ],
        output_text="",
        usage=None,
    )
    web_responses = iter([web_response])
    selection_responses = iter(
        [recommendation, mismatched_termination, corrected_termination]
    )
    monkeypatch.setattr(
        runtime.web_search_client.responses,
        "create",
        lambda **kwargs: next(web_responses),
    )
    requests = []
    monkeypatch.setattr(
        runtime.selection_client.responses,
        "create",
        lambda **kwargs: requests.append(kwargs) or next(selection_responses),
    )
    monkeypatch.setattr(runtime, "_execute_tool", lambda name, arguments: {"ok": True})

    output_text, _ = runtime._run_episode("Which instrument did Alec Aitken play?")
    sample = __import__("json").loads(output_text)

    assert sample["terminated"] is True
    assert sample["recommended_product_ids"] == ["123"]
    assert [
        call["function"]["arguments"]["product_ids"]
        for call in sample["output_tools"]
        if call["function"]["name"] == "terminate"
    ] == ["123"]
    assert "required_product_ids" in requests[-1]["input"][0]["output"]


def test_web_search_runtime_limits_sources_and_catalog_results(monkeypatch):
    monkeypatch.setenv("MAI_OPENAI_BASE_URL", "https://example.test/openai/v1")
    monkeypatch.setenv("MAI_API_KEY", "test-key")
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_URL", "https://tools.example.test")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    monkeypatch.setenv("MAI_MODEL_DEPLOYMENT_NAME", "test-model")
    monkeypatch.setenv("CATALOG_SEARCH_RESULT_LIMIT", "2")
    monkeypatch.setenv("OPTIMIZATION_LOCAL_DIR", "web_search/baseline")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    sys.modules.pop("agents.shared.web_search_runtime", None)
    runtime = importlib.import_module("agents.shared.web_search_runtime")
    request = httpx.Request("GET", "https://tools.example.test/find_product")
    response = httpx.Response(
        200,
        request=request,
        json=[
            {"product_id": "1"},
            {"product_id": "2"},
            {"product_id": "3"},
        ],
    )
    monkeypatch.setattr(runtime.tool_client, "get", lambda *args, **kwargs: response)

    result = runtime._execute_tool("find_product", {"q": "violin", "page": 1})

    assert result == [{"product_id": "1"}, {"product_id": "2"}]


def test_web_search_runtime_removes_exhausted_tools(monkeypatch):
    monkeypatch.setenv("MAI_OPENAI_BASE_URL", "https://example.test/openai/v1")
    monkeypatch.setenv("MAI_API_KEY", "test-key")
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_URL", "https://tools.example.test")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    monkeypatch.setenv("MAI_MODEL_DEPLOYMENT_NAME", "test-model")
    monkeypatch.setenv("MAX_CATALOG_SEARCHES", "2")
    monkeypatch.setenv("MAX_PRODUCT_VIEWS", "2")
    monkeypatch.setenv("OPTIMIZATION_LOCAL_DIR", "web_search/baseline")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    sys.modules.pop("agents.shared.web_search_runtime", None)
    runtime = importlib.import_module("agents.shared.web_search_runtime")

    assert [tool["name"] for tool in runtime._selection_tools(1, 1)] == [
        "find_product",
        "view_product_information",
        "recommend_product",
    ]
    assert [tool["name"] for tool in runtime._selection_tools(2, 1)] == [
        "view_product_information",
        "recommend_product",
    ]
    assert [tool["name"] for tool in runtime._selection_tools(2, 2)] == [
        "recommend_product"
    ]


def test_web_search_runtime_uses_local_catalog(monkeypatch, tmp_path):
    documents = tmp_path / "documents.jsonl"
    documents.write_text(
        json.dumps(
            {
                "product": {
                    "product_id": "123",
                    "title": "Violin bow",
                    "price": 100,
                    "shop_id": "shop-1",
                    "service": [],
                    "sold_count": 1,
                    "short_description": "A bow",
                    "description": "A violin bow",
                    "sku_options": [],
                    "attributes": {},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAI_OPENAI_BASE_URL", "https://example.test/openai/v1")
    monkeypatch.setenv("MAI_API_KEY", "test-key")
    monkeypatch.setenv("MAI_MODEL_DEPLOYMENT_NAME", "test-model")
    monkeypatch.setenv("SHOPPINGBENCH_DOCUMENTS_PATH", str(documents))
    monkeypatch.setenv("OPTIMIZATION_LOCAL_DIR", "web_search/baseline")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    sys.modules.pop("agents.shared.web_search_runtime", None)
    runtime = importlib.import_module("agents.shared.web_search_runtime")

    search_result = runtime._execute_tool("find_product", {"q": "violin", "page": 1})
    view_result = runtime._execute_tool(
        "view_product_information",
        {"product_ids": "123"},
    )

    assert search_result[0]["product_id"] == "123"
    assert view_result == [
        {
            "product_id": "123",
            "short_description": "A bow",
            "description": "A violin bow",
            "sku_options": [],
            "attributes": {},
        }
    ]


def test_web_search_runtime_authenticates_catalog_requests(monkeypatch):
    monkeypatch.setenv("MAI_OPENAI_BASE_URL", "https://example.test/openai/v1")
    monkeypatch.setenv("MAI_API_KEY", "test-key")
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_URL", "https://tools.example.test")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    monkeypatch.setenv("MAI_MODEL_DEPLOYMENT_NAME", "test-model")
    monkeypatch.setenv("OPTIMIZATION_LOCAL_DIR", "web_search/baseline")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    sys.modules.pop("agents.shared.web_search_runtime", None)
    runtime = importlib.import_module("agents.shared.web_search_runtime")
    request = httpx.Request(
        "GET",
        "https://tools.example.test/find_product",
        headers={"Authorization": "Bearer test-token"},
    )
    response = httpx.Response(200, request=request, json={"products": []})
    calls = []
    monkeypatch.setattr(
        runtime.tool_client,
        "get",
        lambda *args, **kwargs: calls.append((args, kwargs)) or response,
    )

    runtime._execute_tool("find_product", {"q": "violin", "page": 1})

    assert calls[0][1]["headers"] == {"Authorization": "Bearer test-token"}
