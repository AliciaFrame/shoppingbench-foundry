"""Responses-compatible hosted agent and directly callable evaluation adapter."""

import asyncio
import json
import os
from contextlib import contextmanager
from pathlib import Path

from azure.ai.agentserver.optimization import OptimizationConfig, load_config
from azure.ai.agentserver.responses import ResponseEventStream, ResponsesAgentServerHost
from azure.ai.agentserver.responses.models import (
    ResponseUsage,
    ResponseUsageInputTokensDetails,
    ResponseUsageOutputTokensDetails,
)
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from domain import TOOL_DEFINITIONS, execute_tool
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
app = ResponsesAgentServerHost()


@contextmanager
def client():
    endpoint = os.environ["MAI_OPENAI_BASE_URL"].rstrip("/") + "/"
    scope = (
        "https://cognitiveservices.azure.com/.default"
        if ".openai.azure.com" in endpoint
        else "https://ai.azure.com/.default"
    )
    with DefaultAzureCredential() as credential:
        key = os.getenv("MAI_API_KEY") or get_bearer_token_provider(credential, scope)
        with OpenAI(base_url=endpoint, api_key=key, max_retries=0, timeout=120) as api:
            yield api


def configuration():
    configured = load_config(
        config_dir=os.getenv("OPTIMIZATION_LOCAL_DIR", str(ROOT)),
        credential=DefaultAzureCredential(),
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
    )


def response_tools(definitions):
    return [
        {"type": "function", **tool["function"]}
        if tool.get("type") == "function" and "function" in tool
        else tool
        for tool in definitions
    ]


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
    config = configuration()
    instructions = config.compose_instructions()
    model = os.getenv("MAI_MODEL_DEPLOYMENT_NAME") or config.model
    if not model:
        raise ValueError("A model deployment is required")
    inputs = list(item["messages"])
    if inputs[0]["role"] == "system" and inputs[0]["content"].strip() == instructions.strip():
        inputs = inputs[1:]
    trace = []
    max_steps = int(os.getenv("MAI_MAX_TOOL_STEPS", "6"))
    if max_steps < 1:
        raise ValueError("MAI_MAX_TOOL_STEPS must be positive")
    for _ in range(max_steps):
        response = api.responses.create(
            model=model,
            instructions=instructions,
            input=inputs,
            tools=response_tools(
                config.tool_definitions if config.tool_definitions is not None else TOOL_DEFINITIONS
            ),
            max_output_tokens=int(os.getenv("MAI_MAX_OUTPUT_TOKENS", "1024")),
            **({"temperature": config.temperature} if config.temperature is not None else {}),
        )
        raw_usage = response.usage.model_dump() if response.usage else None
        calls.append(
            {
                "model": model,
                "purpose": "inference",
                "raw_usage": raw_usage,
                "cache_write_mode": os.getenv("MAI_CACHE_WRITE_MODE", "none"),
            }
        )
        inputs.extend(message.model_dump(exclude_none=True) for message in response.output)
        tool_calls = [message for message in response.output if message.type == "function_call"]
        if not tool_calls:
            if not response.output_text.strip():
                raise RuntimeError("Empty final response")
            return {
                "output_text": response.output_text,
                "output_tools": trace,
                "calls": calls,
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


@app.response_handler
async def handler(request, context, _cancellation_signal):
    inputs = await context.get_input_text()
    result = await asyncio.to_thread(
        run_episode, {"messages": [{"role": "user", "content": inputs}]}
    )
    usage_rows = [call["raw_usage"] for call in result["calls"]]
    usage = None
    if all(row is not None for row in usage_rows):
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
    stream = ResponseEventStream(response_id=context.response_id, request=request)

    async def events():
        yield stream.emit_created()
        yield stream.emit_in_progress()
        message = stream.add_output_item_message()
        yield message.emit_added()
        text = message.add_text_content()
        yield text.emit_added()
        yield text.emit_delta(result["output_text"])
        yield text.emit_text_done(result["output_text"])
        yield text.emit_done()
        yield message.emit_done()
        yield stream.emit_completed(usage=usage)

    return events()


if __name__ == "__main__":
    app.run()
