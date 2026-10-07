from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace


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


def test_vanilla_runtime_preserves_unassisted_tool_loop(monkeypatch):
    monkeypatch.setenv("MAI_OPENAI_BASE_URL", "https://example.test/openai/v1")
    monkeypatch.setenv("MAI_API_KEY", "test-key")
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_URL", "https://tools.example.test")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    monkeypatch.setenv("SHOPPINGBENCH_TASK", "product")
    monkeypatch.setenv("MAI_MODEL_DEPLOYMENT_NAME", "test-model")
    monkeypatch.setenv(
        "OPTIMIZATION_LOCAL_DIR",
        "product/baseline",
    )
    sys.modules.pop("agents.shared.baseline_runtime", None)
    runtime = importlib.import_module("agents.shared.baseline_runtime")
    assert runtime.CONFIG_DIR == runtime.AGENTS_ROOT / "product" / "baseline"
    responses = FakeResponses(
        [
            _response(
                [_call("recommend_product", '{"product_ids":"p1"}', "recommend")],
                response_id="recommend-response",
            ),
            _response([], "I recommend p1.", "final-response"),
        ]
    )
    monkeypatch.setattr(runtime, "model_client", SimpleNamespace(responses=responses))
    monkeypatch.setattr(runtime, "_execute_tool", lambda name, arguments: {"ok": True})

    output, usage = runtime._run_episode("Find the product")

    assert '"recommended_product_ids": ["p1"]' in output
    assert '"terminated": false' in output
    assert usage == {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4}
    assert "tool_choice" not in responses.requests[0]
    assert "parallel_tool_calls" not in responses.requests[0]
    assert "temperature" not in responses.requests[0]
    assert responses.requests[1]["tools"] == runtime.TOOLS
