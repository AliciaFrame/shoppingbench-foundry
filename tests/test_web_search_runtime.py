from __future__ import annotations

import importlib
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
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_URL", "https://tools.example.test")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    monkeypatch.setenv("MAI_MODEL_DEPLOYMENT_NAME", "test-model")
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
    monkeypatch.setattr(
        runtime.model_client.responses,
        "create",
        lambda **kwargs: next(responses),
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
