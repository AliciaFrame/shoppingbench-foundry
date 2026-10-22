import asyncio
import importlib.util
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

STARTER = (
    Path(__file__).parents[1] / ".github" / "skills" / "mai-agent-hillclimb" / "assets" / "starter"
)
USAGE = {
    "input_tokens": 12,
    "output_tokens": 3,
    "total_tokens": 15,
    "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 0},
    "output_tokens_details": {"reasoning_tokens": 1},
}


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.syspath_prepend(str(STARTER))
    for name in ("domain", "workload", "evaluation_auth"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("MAI_CACHE_WRITE_MODE", "none")
    monkeypatch.setenv("HILLCLIMB_EVALUATION_KEY", "synthetic-signing-key-at-least-32-characters")
    monkeypatch.delenv("HILLCLIMB_EVALUATION_KEY_SECRET_URI", raising=False)

    def load(name):
        spec = importlib.util.spec_from_file_location(
            f"runtime_test_{name}", STARTER / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return SimpleNamespace(agent=load("agent"), hosted=load("hosted_eval"))


def incomplete_response():
    from openai.types.responses import Response

    return Response.model_validate(
        {
            "id": "resp_synthetic",
            "object": "response",
            "created_at": 0,
            "model": "provider-model-revision",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [
                {
                    "id": "msg_synthetic",
                    "type": "message",
                    "role": "assistant",
                    "status": "incomplete",
                    "content": [
                        {"type": "output_text", "text": "Partial answer", "annotations": []}
                    ],
                }
            ],
            "usage": USAGE,
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
        }
    )


@pytest.mark.parametrize("effort", [None, "medium"])
def test_incomplete_nonempty_generation_preserves_usage_and_provenance(
    runtime, monkeypatch, effort
):
    agent = runtime.agent
    monkeypatch.setattr(
        agent,
        "policy_contract",
        lambda: {
            "instructions": "Synthetic policy",
            "model": "requested-deployment",
            "tools": [],
            "temperature": None,
            "max_tool_steps": 1,
            "max_output_tokens": 3,
            "reasoning_effort": effort,
        },
    )
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return incomplete_response()

    api = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(agent, "client", lambda: nullcontext(api))
    with pytest.raises(agent.EpisodeFailure, match="did not complete") as failure:
        agent.run_episode({"messages": [{"role": "user", "content": "Synthetic input"}]})
    assert failure.value.calls == [
        {
            "model": "requested-deployment",
            "returned_model": "provider-model-revision",
            "response_status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "purpose": "inference",
            "raw_usage": USAGE,
            "cache_write_mode": "none",
        }
    ]
    assert requests[0].get("reasoning") == ({"effort": effort} if effort else None)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_runtime_rejects_invalid_request_timeout(runtime, monkeypatch, value):
    monkeypatch.setenv("MAI_REQUEST_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="finite and positive"):
        runtime.agent.request_timeout()


def test_runtime_binds_reasoning_effort_and_request_deadline(runtime, monkeypatch):
    agent = runtime.agent
    monkeypatch.setenv("MAI_OPENAI_BASE_URL", "https://provider.example/openai/v1")
    monkeypatch.setenv("MAI_API_KEY", "synthetic-key")
    monkeypatch.setenv("MAI_REQUEST_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("MAI_REASONING_EFFORT", "medium")
    monkeypatch.setattr(
        agent,
        "configuration",
        lambda: SimpleNamespace(
            model="synthetic-model",
            temperature=None,
            skills=[],
            skills_dir=None,
            tool_definitions=[],
            compose_instructions=lambda: "Synthetic policy",
        ),
    )
    options = {}
    monkeypatch.setattr(agent, "DefaultAzureCredential", lambda **_: nullcontext(object()))
    monkeypatch.setattr(
        agent, "OpenAI", lambda **kwargs: options.update(kwargs) or nullcontext(None)
    )
    policy = agent.policy_contract()
    assert policy["reasoning_effort"] == "medium" and policy["request_timeout_seconds"] == 300
    with agent.client():
        pass
    assert options["timeout"] == 300
    monkeypatch.setenv("MAI_REASONING_EFFORT", "unrecognized")
    with pytest.raises(ValueError, match="recognized effort"):
        agent.reasoning_effort()


def test_rft_calibration_uses_materialized_user_policy_without_system_instructions(
    runtime,
    monkeypatch,
):
    agent = runtime.agent
    policy = {
        "instructions": "Synthetic selected policy",
        "model": "requested-deployment",
        "tools": [],
        "temperature": None,
        "max_tool_steps": 1,
        "max_output_tokens": 10,
        "reasoning_effort": "low",
        "rft_calibration_mode": True,
    }
    monkeypatch.setattr(agent, "policy_contract", lambda: policy)
    requests = []
    response = SimpleNamespace(
        usage=SimpleNamespace(model_dump=lambda: USAGE),
        incomplete_details=None,
        status="completed",
        model="provider",
        output=[],
        output_text='{"ok":true}',
    )
    monkeypatch.setattr(
        agent,
        "client",
        lambda: nullcontext(
            SimpleNamespace(
                responses=SimpleNamespace(
                    create=lambda **kwargs: requests.append(kwargs) or response
                ),
            )
        ),
    )
    materialized = agent.TRAINING_POLICY_PREFIX + policy["instructions"]
    result = agent.run_episode(
        {
            "messages": [
                {"role": "user", "content": materialized},
                {"role": "user", "content": "Synthetic task"},
            ]
        }
    )
    assert result["output_text"] == '{"ok":true}'
    assert "instructions" not in requests[0]
    assert requests[0]["input"][0]["content"] == materialized
    with pytest.raises(agent.EpisodeFailure, match="does not contain"):
        agent.run_episode({"messages": [{"role": "user", "content": "Missing policy"}]})


def hosted_project(runtime, monkeypatch, create_response, events, *, diagnostic=None, cleanup=None):
    hosted = runtime.hosted
    monkeypatch.setenv("MAI_HOSTED_AGENT_NAME", "synthetic-agent")
    monkeypatch.setenv("MAI_HOSTED_AGENT_VERSION", "1")
    monkeypatch.setenv("MAI_PROJECT_ENDPOINT", "https://project.example")
    session = SimpleNamespace(status="active", agent_session_id="synthetic-session")

    def get_session(**kwargs):
        events.append("diagnostics")
        assert kwargs["read_timeout"] == kwargs["connection_timeout"] == 5
        assert kwargs["retry_total"] == 0
        if isinstance(diagnostic, Exception):
            raise diagnostic
        return diagnostic or session

    def stop_session(**_):
        events.append("cleanup")
        if cleanup:
            raise cleanup

    def get_session_log_stream(**kwargs):
        events.append("logs")
        assert kwargs["read_timeout"] == kwargs["connection_timeout"] == 5
        assert kwargs["retry_total"] == 0
        if isinstance(diagnostic, Exception):
            raise diagnostic
        pipeline = SimpleNamespace(
            http_response=SimpleNamespace(close=lambda: events.append("logs_closed"))
        )
        return kwargs["cls"](
            pipeline,
            [b"Bearer synthetic-token timeout client_secret=synthetic-secret"],
            {},
        )

    project = SimpleNamespace(
        agents=SimpleNamespace(
            get_version=lambda **_: SimpleNamespace(
                status="active",
                definition=SimpleNamespace(
                    environment_variables={"HILLCLIMB_ENABLE_EVALUATION": "1"}
                ),
            ),
            create_session=lambda **_: session,
            get_session=get_session,
            get_session_log_stream=get_session_log_stream,
            stop_session=stop_session,
        ),
        get_openai_client=lambda **_: nullcontext(
            SimpleNamespace(responses=SimpleNamespace(create=create_response))
        ),
    )
    monkeypatch.setattr(hosted, "AIProjectClient", lambda **_: nullcontext(project))
    monkeypatch.setattr(hosted, "DefaultAzureCredential", lambda **_: nullcontext(None))
    return hosted


@pytest.mark.parametrize("diagnostic_fails", [False, True])
def test_original_failure_survives_safe_diagnostics_and_cleanup(
    runtime, monkeypatch, diagnostic_fails
):
    events = []
    original = ValueError("Original synthetic failure")
    secret = "Bearer synthetic-token client_secret=synthetic-secret"

    def fail(**_):
        events.append("invoke")
        raise original

    diagnostic = (
        RuntimeError(secret)
        if diagnostic_fails
        else SimpleNamespace(status=secret, error=secret, logs=secret)
    )
    hosted = hosted_project(
        runtime,
        monkeypatch,
        fail,
        events,
        diagnostic=diagnostic,
        cleanup=RuntimeError(secret),
    )
    with pytest.raises(ValueError) as failure:
        hosted.run_episode({"messages": [{"role": "user", "content": "Synthetic input"}]})
    assert failure.value is original
    assert events == (
        ["invoke", "diagnostics", "logs", "cleanup"]
        if diagnostic_fails
        else ["invoke", "diagnostics", "logs", "logs_closed", "cleanup"]
    )
    safe_context = json.dumps(original.hosted_diagnostics) + str(original.__notes__)
    assert "synthetic-token" not in safe_context
    assert "synthetic-secret" not in safe_context
    assert "cleanup failed" in safe_context


async def invoke(agent, metadata):
    from azure.ai.agentserver.responses import CreateResponse

    async def get_input_text():
        return "Synthetic input"

    context = SimpleNamespace(response_id="resp_synthetic", get_input_text=get_input_text)
    stream = await agent.handler(
        CreateResponse(input="Synthetic input", metadata=metadata), context, None
    )
    return [event async for event in stream]


def test_failure_details_exclude_opaque_messages_and_invalid_metadata(runtime):
    from urllib.error import HTTPError

    details = runtime.agent.failure_details
    assert details(HTTPError("https://private.example", 429, "synthetic-secret", None, None)) == {
        "type": "HTTPError",
        "http_status": 429,
    }
    assert details({"type": ["synthetic-secret"], "http_status": "synthetic-secret"}) == {
        "type": "unknown",
        "http_status": None,
    }
    assert details({"type": "HTTPError", "http_status": 500, "message": "synthetic-secret"}) == {
        "type": "HTTPError",
        "http_status": 500,
    }


@pytest.mark.parametrize("unknown_tail", [False, True])
def test_authorized_failure_uses_real_failed_event_and_remote_usage(
    runtime, monkeypatch, unknown_tail
):
    agent = runtime.agent
    monkeypatch.setenv("HILLCLIMB_ENABLE_EVALUATION", "1")
    calls = [{"model": "requested", "returned_model": "provider", "raw_usage": USAGE}]
    if unknown_tail:
        calls.append({"model": "requested", "raw_usage": None})

    def fail(_):
        raise agent.EpisodeFailure(RuntimeError("private failure message"), calls)

    monkeypatch.setattr(agent, "run_episode", fail)
    sign = agent.authorize_capture.__globals__["evaluation_metadata"]
    events = asyncio.run(invoke(agent, sign("Synthetic input")))
    assert [event["type"] for event in events] == [
        "response.created",
        "response.in_progress",
        "response.failed",
    ]
    terminal = events[-1]["response"]
    assert terminal["status"] == "failed"
    if unknown_tail:
        assert terminal.get("usage") is None
    else:
        assert terminal["usage"]["input_tokens"] == 12
    assert terminal["output"] == []
    assert "private failure message" not in terminal["error"]["message"]
    assert json.loads(terminal["error"]["message"])["error"] == {
        "type": "RuntimeError",
        "http_status": None,
    }
    import httpx
    from openai import OpenAI

    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=terminal))
    with OpenAI(
        api_key="synthetic-key",
        base_url="https://provider.example",
        http_client=httpx.Client(transport=transport),
    ) as api:
        response = api.responses.create(model="synthetic", input="Synthetic input")
    history = []
    hosted = hosted_project(runtime, monkeypatch, lambda **_: response, history)
    with pytest.raises(hosted.HostedEpisodeFailure) as failure:
        hosted.run_episode({"messages": [{"role": "user", "content": "Synthetic input"}]})
    assert failure.value.calls == calls
    assert failure.value.failure_details == {"type": "RuntimeError", "http_status": None}
    assert history == ["diagnostics", "logs", "logs_closed", "cleanup"]


@pytest.mark.parametrize("evaluation_enabled", [False, True])
def test_unsigned_failure_cannot_export_rich_trace(runtime, monkeypatch, evaluation_enabled):
    agent = runtime.agent
    monkeypatch.setenv("HILLCLIMB_ENABLE_EVALUATION", "1" if evaluation_enabled else "0")
    error = agent.EpisodeFailure(
        RuntimeError("synthetic failure"), [{"model": "private-model", "raw_usage": USAGE}]
    )

    def fail(_):
        raise error

    monkeypatch.setattr(agent, "run_episode", fail)
    with pytest.raises(agent.EpisodeFailure) as failure:
        asyncio.run(invoke(agent, {}))
    assert failure.value is error
    with pytest.raises(PermissionError):
        asyncio.run(invoke(agent, {"hillclimb_capture": "v1"}))


def test_unavailable_remote_trace_keeps_usage_unknown(runtime, monkeypatch):
    history = []
    hosted = hosted_project(
        runtime,
        monkeypatch,
        lambda **_: SimpleNamespace(
            status="failed",
            output_text="Nonempty partial answer",
            error=SimpleNamespace(message="Opaque remote error"),
        ),
        history,
    )
    with pytest.raises(hosted.HostedEpisodeFailure) as failure:
        hosted.run_episode({"messages": [{"role": "user", "content": "Synthetic input"}]})
    assert failure.value.calls == []
    assert history == ["diagnostics", "logs", "logs_closed", "cleanup"]


def test_log_capture_is_bounded_and_closed(runtime):
    closed = []
    chunks_read = []

    def chunks():
        for _ in range(100):
            chunks_read.append(True)
            yield b"synthetic-private-token" * 1000

    pipeline = SimpleNamespace(http_response=SimpleNamespace(close=lambda: closed.append(True)))
    result = runtime.hosted.summarize_logs(pipeline, chunks(), {})
    assert result == {"bytes_observed": 8192, "signals": []}
    assert len(chunks_read) == 1
    assert closed == [True]


def test_log_read_failure_still_closes_stream(runtime):
    closed = []

    def chunks():
        yield b"Bearer synthetic-private-token"
        raise RuntimeError("synthetic-private-secret")

    pipeline = SimpleNamespace(http_response=SimpleNamespace(close=lambda: closed.append(True)))
    with pytest.raises(RuntimeError):
        runtime.hosted.summarize_logs(pipeline, chunks(), {})
    assert closed == [True]
