"""Responses-compatible hosted agent and directly callable evaluation adapter."""

import asyncio
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from azure.ai.agentserver.optimization import OptimizationConfig, Skill, load_config
from azure.ai.agentserver.responses import ResponseEventStream, ResponsesAgentServerHost
from azure.ai.agentserver.responses.models import (
    ResponseUsage,
    ResponseUsageInputTokensDetails,
    ResponseUsageOutputTokensDetails,
)
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from domain import TOOL_DEFINITIONS, execute_tool
from evaluation_auth import authorize_capture, redact_trace
from openai import OpenAI
from workload import decode_input, model_messages, skill_catalog

ROOT = Path(__file__).resolve().parent


@contextmanager
def client():
    endpoint = os.environ["MAI_OPENAI_BASE_URL"].rstrip("/") + "/"
    scope = (
        "https://cognitiveservices.azure.com/.default"
        if ".openai.azure.com" in endpoint
        else "https://ai.azure.com/.default"
    )
    with DefaultAzureCredential(process_timeout=60) as credential:
        key = os.getenv("MAI_API_KEY") or get_bearer_token_provider(credential, scope)
        with OpenAI(base_url=endpoint, api_key=key, max_retries=0, timeout=120) as api:
            yield api


def configuration():
    with DefaultAzureCredential(process_timeout=60) as credential:
        configured = load_config(
            config_dir=os.getenv("OPTIMIZATION_LOCAL_DIR", str(ROOT)),
            credential=credential,
        )
    candidate = os.getenv("OPTIMIZATION_CANDIDATE_ID")
    if (
        candidate
        and not os.getenv("OPTIMIZATION_CONFIG")
        and (configured is None or configured.candidate_id != candidate)
    ):
        raise RuntimeError(f"Requested optimization candidate {candidate} was not loaded")
    return configured or OptimizationConfig(
        instructions=(ROOT / "instructions.md").read_text(encoding="utf-8"),
        model=os.environ["MAI_MODEL_DEPLOYMENT_NAME"],
        tool_definitions=json.loads((ROOT / "tools.json").read_text(encoding="utf-8")),
        skills=[Skill(**item) for item in skill_catalog(ROOT)],
    )


def response_tools(definitions):
    return [
        {"type": "function", **tool["function"]}
        if tool.get("type") == "function" and "function" in tool
        else tool
        for tool in definitions
    ]


def policy_contract():
    config = configuration()
    explicit = os.getenv("MAI_MODEL_OVERRIDE")
    configured = config.model
    model = explicit or configured or os.getenv("MAI_MODEL_DEPLOYMENT_NAME")
    if not model:
        raise ValueError("A resolved model deployment is required")
    instructions = config.compose_instructions()
    for skill in config.skills or []:
        instructions += f"\n\n## Skill: {skill.name}\n{skill.body}"
    if config.skills_dir:
        for path in sorted(Path(config.skills_dir).rglob("SKILL.md")):
            instructions += "\n\n" + path.read_text(encoding="utf-8")
    return {
        "model": model,
        "model_override": explicit,
        "endpoint": os.environ["MAI_OPENAI_BASE_URL"],
        "instructions": instructions,
        "tools": response_tools(
            config.tool_definitions if config.tool_definitions is not None else TOOL_DEFINITIONS
        ),
        "temperature": config.temperature,
        "max_tool_steps": int(os.getenv("MAI_MAX_TOOL_STEPS", "6")),
        "max_output_tokens": int(os.getenv("MAI_MAX_OUTPUT_TOKENS", "1024")),
        "domain_sha256": hashlib.sha256((ROOT / "domain.py").read_bytes()).hexdigest(),
    }


class EpisodeFailure(RuntimeError):
    def __init__(self, error, calls):
        super().__init__(f"{type(error).__name__}: {error}")
        self.calls = calls


def run_episode(item):
    calls = []
    try:
        with client() as api:
            return _run_episode(item, calls, api)
    except Exception as error:
        raise EpisodeFailure(error, calls) from error


def _run_episode(item, calls, api):
    policy = policy_contract()
    instructions, model = policy["instructions"], policy["model"]
    inputs = list(item["messages"])
    if inputs[0]["role"] == "system" and inputs[0]["content"].strip() == instructions.strip():
        inputs = inputs[1:]
    if any(message["role"] not in ("user", "assistant") for message in inputs):
        raise ValueError("Only the server-configured policy may supply system instructions")
    expected_policy = item.get("expected_policy_sha256")
    if (
        expected_policy
        and expected_policy != hashlib.sha256(instructions.strip().encode("utf-8")).hexdigest()
    ):
        raise ValueError("Workload policy does not match the deployed policy")
    inputs = model_messages({**item, "messages": inputs})
    trace = []
    max_steps = policy["max_tool_steps"]
    if max_steps < 1:
        raise ValueError("MAI_MAX_TOOL_STEPS must be positive")
    for _ in range(max_steps):
        response = api.responses.create(
            model=model,
            instructions=instructions,
            input=inputs,
            tools=policy["tools"],
            max_output_tokens=policy["max_output_tokens"],
            **({"temperature": policy["temperature"]} if policy["temperature"] is not None else {}),
        )
        raw_usage = response.usage.model_dump() if response.usage else None
        incomplete = getattr(response, "incomplete_details", None)
        calls.append(
            {
                "model": model,
                "returned_model": getattr(response, "model", None),
                "response_status": getattr(response, "status", None),
                "incomplete_details": incomplete.model_dump() if incomplete else None,
                "purpose": "inference",
                "raw_usage": raw_usage,
                "cache_write_mode": os.getenv("MAI_CACHE_WRITE_MODE", "none"),
            }
        )
        if getattr(response, "status", None) not in (None, "completed") or incomplete:
            raise RuntimeError("Provider generation did not complete")
        inputs.extend(message.model_dump(exclude_none=True) for message in response.output)
        tool_calls = [message for message in response.output if message.type == "function_call"]
        if not tool_calls:
            if not response.output_text.strip():
                raise RuntimeError("Empty final response")
            return {
                "output_text": response.output_text,
                "output_tools": trace,
                "calls": calls,
                "policy_contract": policy,
            }
        for call in tool_calls:
            arguments = json.loads(call.arguments)
            result = execute_tool(call.name, arguments)
            trace.append({"name": call.name, "arguments": arguments, "output": result})
            inputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result),
                }
            )
    raise RuntimeError("Tool-step limit exceeded")


async def handler(request, context, _cancellation_signal):
    inputs = await context.get_input_text() or ""
    capture = authorize_capture(inputs, request.get("metadata") or {})
    item = (
        decode_input(inputs)
        if os.getenv("HILLCLIMB_ENABLE_EVALUATION") == "1"
        else {"messages": [{"role": "user", "content": inputs}]}
    )
    try:
        result = await asyncio.to_thread(run_episode, item)
    except EpisodeFailure as error:
        if not capture:
            raise
        stream = ResponseEventStream(response_id=context.response_id, request=request)
        failure = json.dumps(
            {
                "hillclimb_trace_version": 1,
                "status": "failed",
                "calls": redact_trace(error.calls),
            }
        )
        usage = response_usage(error.calls)

        async def failed_events():
            yield stream.emit_created()
            yield stream.emit_in_progress()
            yield stream.emit_failed(message=failure, usage=usage)

        return failed_events()
    output_text = result["output_text"]
    if capture:
        trace = {"hillclimb_trace_version": 1, **result}
        trace["output_tools"] = redact_trace(result["output_tools"])
        trace["calls"] = redact_trace(result["calls"])
        output_text = json.dumps(trace)
    usage = response_usage(result["calls"])
    stream = ResponseEventStream(response_id=context.response_id, request=request)

    async def events():
        yield stream.emit_created()
        yield stream.emit_in_progress()
        message = stream.add_output_item_message()
        yield message.emit_added()
        text = message.add_text_content()
        yield text.emit_added()
        yield text.emit_delta(output_text)
        yield text.emit_text_done(output_text)
        yield text.emit_done()
        yield message.emit_done()
        yield stream.emit_completed(usage=usage)

    return events()


def response_usage(calls):
    usage_rows = [call["raw_usage"] for call in calls]
    usage = None
    if usage_rows and all(row is not None for row in usage_rows):
        cached = sum(
            (row.get("input_tokens_details") or {}).get("cached_tokens", 0) for row in usage_rows
        )
        reasoning = sum(
            (row.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
            for row in usage_rows
        )
        usage = ResponseUsage(
            input_tokens=sum(row["input_tokens"] for row in usage_rows),
            output_tokens=sum(row["output_tokens"] for row in usage_rows),
            total_tokens=sum(row["total_tokens"] for row in usage_rows),
            input_tokens_details=ResponseUsageInputTokensDetails(cached_tokens=cached),
            output_tokens_details=ResponseUsageOutputTokensDetails(reasoning_tokens=reasoning),
        )
    return usage


def create_app():
    app = ResponsesAgentServerHost()
    app.response_handler(handler)
    return app


if __name__ == "__main__":
    create_app().run()
