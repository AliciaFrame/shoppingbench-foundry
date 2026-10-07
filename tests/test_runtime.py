from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MAI_OPENAI_BASE_URL", "https://example.test/openai/v1")
os.environ.setdefault("MAI_API_KEY", "test-key")
os.environ.setdefault("SHOPPINGBENCH_TOOL_URL", "https://tools.example.test")
os.environ.setdefault("SHOPPINGBENCH_API_TOKEN", "test-token")
os.environ.setdefault("SHOPPINGBENCH_TASK", "product")
os.environ.setdefault("MAI_MODEL_DEPLOYMENT_NAME", "test-model-override")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")


def test_hosted_manifest_uses_resolvable_task_config_and_bounded_steps():
    manifest = (
        Path(__file__).resolve().parents[1] / "agents" / "azure.yaml"
    ).read_text(encoding="utf-8")

    assert manifest.count('MAX_TOOL_STEPS: "12"') == 4
    assert "OPTIMIZATION_LOCAL_DIR: product/optimized" in manifest
    assert "OPTIMIZATION_LOCAL_DIR: shop/optimized" in manifest
    assert "OPTIMIZATION_LOCAL_DIR: voucher/optimized" in manifest
    assert "OPTIMIZATION_LOCAL_DIR: web/optimized" in manifest
    assert "OPTIMIZATION_LOCAL_DIR: web_search/optimized" in manifest
    assert "WEB_SEARCH_MODEL_DEPLOYMENT_NAME" in manifest


def _response(output, output_text="", response_id="response"):
    return SimpleNamespace(
        id=response_id,
        output=output,
        output_text=output_text,
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
    )


def _call(name: str, arguments: str, call_id: str):
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=arguments,
        call_id=call_id,
    )


class FakeResponses:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return next(self._responses)


def test_termination_requests_final_user_facing_response(monkeypatch):
    runtime = importlib.import_module("agents.shared.runtime")
    assert runtime.model == "test-model-override"
    assert runtime.MAX_TOOL_STEPS == 12
    assert runtime.CONFIG_DIR == runtime.AGENTS_ROOT / "product" / "optimized"
    assert (
        runtime._credential_scope("https://example.openai.azure.com/openai/v1/")
        == "https://cognitiveservices.azure.com/.default"
    )
    assert (
        runtime._credential_scope("https://example.services.ai.azure.com/api/projects/p/openai/v1/")
        == "https://ai.azure.com/.default"
    )
    responses = FakeResponses(
        [
            _response(
                [
                    _call(
                        "recommend_product",
                        '{"product_ids":"p1"}',
                        "recommend",
                    ),
                ],
                response_id="recommend-response",
            ),
            _response([_call("terminate", "{}", "terminate")], response_id="tool-response"),
            _response([], "I recommend p1 because it matches the request.", "final-response"),
        ]
    )
    monkeypatch.setattr(runtime, "model_client", SimpleNamespace(responses=responses))
    monkeypatch.setattr(runtime, "_execute_tool", lambda name, arguments: {"ok": True})

    output, usage = runtime._run_episode("Find the product")

    assert '"assistant_text": "I recommend p1 because it matches the request."' in output
    assert usage == {"input_tokens": 3, "output_tokens": 3, "total_tokens": 6}
    assert responses.requests[1]["previous_response_id"] == "recommend-response"
    assert responses.requests[1]["tools"] == runtime.TERMINATE_TOOLS
    assert responses.requests[1]["tool_choice"] == "required"
    assert responses.requests[1]["parallel_tool_calls"] is False
    assert responses.requests[2]["previous_response_id"] == "tool-response"
    assert responses.requests[2]["instructions"] == runtime.FINAL_RESPONSE_INSTRUCTION
    assert "tools" not in responses.requests[2]
    assert responses.requests[0]["tool_choice"] == "required"
    assert responses.requests[0]["parallel_tool_calls"] is False
    assert all(
        tool["name"] != "terminate" for tool in responses.requests[0]["tools"]
    )


def test_terminate_before_recommendation_fails(monkeypatch):
    runtime = importlib.import_module("agents.shared.runtime")
    responses = FakeResponses(
        [_response([_call("terminate", "{}", "terminate")], response_id="tool-response")]
    )
    monkeypatch.setattr(runtime, "model_client", SimpleNamespace(responses=responses))
    monkeypatch.setattr(runtime, "_execute_tool", lambda name, arguments: {"ok": True})

    try:
        runtime._run_episode("Find the product")
    except RuntimeError as exc:
        assert "before recommend_product" in str(exc)
    else:
        raise AssertionError("Termination without a recommendation must fail")


def test_termination_without_final_text_fails(monkeypatch):
    runtime = importlib.import_module("agents.shared.runtime")
    responses = FakeResponses(
        [
            _response(
                [_call("recommend_product", '{"product_ids":"p1"}', "recommend")],
                response_id="recommend-response",
            ),
            _response([_call("terminate", "{}", "terminate")], response_id="tool-response"),
            _response([], "", "final-response"),
        ]
    )
    monkeypatch.setattr(runtime, "model_client", SimpleNamespace(responses=responses))
    monkeypatch.setattr(runtime, "_execute_tool", lambda name, arguments: {"ok": True})

    try:
        runtime._run_episode("Find the product")
    except RuntimeError as exc:
        assert "final user-facing response" in str(exc)
    else:
        raise AssertionError("An empty final answer must fail explicitly")


def test_final_text_without_terminate_fails(monkeypatch):
    runtime = importlib.import_module("agents.shared.runtime")
    responses = FakeResponses(
        [
            _response(
                [_call("recommend_product", '{"product_ids":"p1"}', "recommend")],
                response_id="recommend-response",
            ),
            _response([], "I recommend p1.", "unprotocolled-final"),
        ]
    )
    monkeypatch.setattr(runtime, "model_client", SimpleNamespace(responses=responses))
    monkeypatch.setattr(runtime, "_execute_tool", lambda name, arguments: {"ok": True})

    try:
        runtime._run_episode("Find the product")
    except RuntimeError as exc:
        assert "without calling terminate" in str(exc)
    else:
        raise AssertionError("Final text without terminate must fail explicitly")
    assert responses.requests[1]["tool_choice"] == "required"
    assert responses.requests[1]["tools"] == runtime.TERMINATE_TOOLS
    assert responses.requests[1]["parallel_tool_calls"] is False


def test_recommendation_on_final_tool_step_fails_explicitly(monkeypatch):
    runtime = importlib.import_module("agents.shared.runtime")
    responses = FakeResponses(
        [
            _response(
                [_call("recommend_product", '{"product_ids":"p1"}', "recommend")],
                response_id="recommend-response",
            ),
            _response([_call("terminate", "{}", "terminate")], response_id="tool-response"),
        ]
    )
    monkeypatch.setattr(runtime, "MAX_TOOL_STEPS", 1)
    monkeypatch.setattr(runtime, "model_client", SimpleNamespace(responses=responses))
    monkeypatch.setattr(runtime, "_execute_tool", lambda name, arguments: {"ok": True})

    try:
        runtime._run_episode("Find the product")
    except RuntimeError as exc:
        assert "within 1 tool steps" in str(exc)
    else:
        raise AssertionError("Step exhaustion must fail instead of returning partial output")


def test_web_terminal_tool_supplies_final_answer_without_extra_model_call(monkeypatch):
    monkeypatch.setenv("SHOPPINGBENCH_TASK", "web")
    monkeypatch.setenv("OPTIMIZATION_LOCAL_DIR", "web/optimized")
    import sys

    sys.modules.pop("agents.shared.runtime", None)
    runtime = importlib.import_module("agents.shared.runtime")
    responses = FakeResponses(
        [
            _response(
                [_call("recommend_product", '{"product_ids":"1234567890"}', "recommend")],
                response_id="recommend-response",
            ),
            _response(
                [
                    _call(
                        "terminate",
                        (
                            '{"product_ids":"1234567890",'
                            '"resolved_clue":"Jason Statham",'
                            '"final_answer":"Jason Statham is the clue; '
                            'I recommend 1234567890."}'
                        ),
                        "terminate",
                    )
                ],
                response_id="terminal-response",
            ),
        ]
    )
    monkeypatch.setattr(runtime, "model_client", SimpleNamespace(responses=responses))
    monkeypatch.setattr(runtime, "_execute_tool", lambda name, arguments: {"ok": True})

    output, _usage = runtime._run_episode("Find the product")
    sample = __import__("json").loads(output)

    assert sample["assistant_text"] == (
        "Jason Statham is the clue; I recommend 1234567890."
    )
    assert len(responses.requests) == 2
    assert responses.requests[1]["tools"] == runtime.TERMINATE_TOOLS
