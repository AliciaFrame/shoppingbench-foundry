from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from azure.ai.agentserver.optimization import OptimizationConfig, load_config
from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponseEventStream,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
)
from azure.ai.agentserver.responses.models import (
    ResponseUsage,
    ResponseUsageInputTokensDetails,
    ResponseUsageOutputTokensDetails,
)
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shoppingbench-agent")

TASK = os.getenv("SHOPPINGBENCH_TASK", "product")
AGENTS_ROOT = Path(__file__).resolve().parents[1]
configured_dir = Path(os.getenv("OPTIMIZATION_LOCAL_DIR", str(Path(TASK) / "optimized")))
CONFIG_DIR = configured_dir if configured_dir.is_absolute() else AGENTS_ROOT / configured_dir
MODEL_OVERRIDE = os.getenv("MAI_MODEL_DEPLOYMENT_NAME")
MODEL = MODEL_OVERRIDE or "mai-code-1-1-flash-base"
MAX_TOOL_STEPS = int(os.getenv("MAX_TOOL_STEPS", "12"))

FALLBACK_INSTRUCTIONS = {
    "product": "Find one product that satisfies the user's constraints.",
    "shop": "Find all requested products from one shop.",
    "voucher": "Find the requested products within the voucher-adjusted budget.",
    "web": "Resolve the knowledge clue and find the requested product.",
}
FINAL_RESPONSE_INSTRUCTION = (
    "The shopping tools are complete. Return a concise user-facing answer now. "
    "State the recommended product IDs and briefly explain why they satisfy the request. "
    "Mention every recommended product ID and do not mention or suggest any other product IDs. "
)
if TASK == "voucher":
    FINAL_RESPONSE_INSTRUCTION += (
        "Show shop consistency, subtotal, voucher threshold, discount or cap, "
        "final payable, and budget fit."
    )


def _load_optimization_config() -> OptimizationConfig:
    config = load_config(config_dir=CONFIG_DIR)
    if config is not None:
        return config
    instructions_path = CONFIG_DIR / "instructions.md"
    tools_path = CONFIG_DIR / "tools.json"
    if instructions_path.is_file() and tools_path.is_file():
        sections = [instructions_path.read_text(encoding="utf-8").strip()]
        skills_dir = CONFIG_DIR / "skills"
        if skills_dir.is_dir():
            sections.extend(
                path.read_text(encoding="utf-8").strip()
                for path in sorted(skills_dir.glob("*/SKILL.md"))
            )
        return OptimizationConfig(
            instructions="\n\n".join(section for section in sections if section),
            model=MODEL,
            tool_definitions=json.loads(tools_path.read_text(encoding="utf-8")),
            source="local",
        )
    return OptimizationConfig(
        instructions=FALLBACK_INSTRUCTIONS[TASK],
        model=MODEL,
        source="defaults",
    )


config = _load_optimization_config()
instructions = (
    config.compose_instructions()
    + "\n\nDuring the tool phase, do not return user-facing prose. "
    "When the final selection is ready, call recommend_product. The runtime "
    "will then expose only terminate; call it on the next step. The runtime "
    "will request the user-facing answer only after terminate succeeds."
)
model = MODEL_OVERRIDE or config.model or MODEL


def _load_tool_definitions() -> list[dict[str, Any]]:
    definitions = config.tool_definitions
    if not definitions:
        fallback_paths = [
            CONFIG_DIR / "tools.json",
        ]
        tools_path = next((path for path in fallback_paths if path.is_file()), None)
        if tools_path is None:
            raise FileNotFoundError(f"No tool definitions found for task {TASK}: {fallback_paths}")
        definitions = json.loads(tools_path.read_text(encoding="utf-8"))
    tools = []
    for definition in definitions:
        function = definition.get("function", definition)
        tools.append(
            {
                "type": "function",
                "name": function["name"],
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {}),
            }
        )
    return tools


TOOLS = _load_tool_definitions()
TERMINATE_TOOLS = [tool for tool in TOOLS if tool["name"] == "terminate"]
if len(TERMINATE_TOOLS) != 1:
    raise ValueError("Exactly one terminate tool definition is required")
SELECTION_TOOLS = [tool for tool in TOOLS if tool["name"] != "terminate"]


def _credential_scope(endpoint: str) -> str:
    if ".openai.azure.com" in endpoint:
        return "https://cognitiveservices.azure.com/.default"
    return "https://ai.azure.com/.default"


base_url = os.environ["MAI_OPENAI_BASE_URL"].rstrip("/") + "/"
api_key: str | Any = os.getenv("MAI_API_KEY", "")
if not api_key:
    api_key = get_bearer_token_provider(
        DefaultAzureCredential(),
        _credential_scope(base_url),
    )
model_client = OpenAI(base_url=base_url, api_key=api_key)
tool_client = httpx.Client(timeout=120)

logger.info(
    "Loaded task=%s config_source=%s model=%s tools=%d",
    TASK,
    config.source,
    model,
    len(TOOLS),
)

app = ResponsesAgentServerHost(
    options=ResponsesServerOptions(default_fetch_history_count=1),
)


def _execute_tool(name: str, arguments: dict[str, Any]) -> Any:
    tool_url = os.environ["SHOPPINGBENCH_TOOL_URL"].rstrip("/")
    token = os.environ["SHOPPINGBENCH_API_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    if name == "find_product":
        response = tool_client.get(
            f"{tool_url}/find_product",
            params=arguments,
            headers=headers,
        )
    elif name == "view_product_information":
        response = tool_client.get(
            f"{tool_url}/view_product_information",
            params=arguments,
            headers=headers,
        )
    elif name == "recommend_product":
        return {"recommended": arguments.get("product_ids", "")}
    elif name == "terminate":
        return {"terminated": True}
    else:
        raise ValueError(f"Unknown tool: {name}")
    response.raise_for_status()
    return response.json()


def _add_usage(total: dict[str, int], response: Any) -> None:
    if not response.usage:
        return
    total["input_tokens"] += response.usage.input_tokens or 0
    total["output_tokens"] += response.usage.output_tokens or 0
    total["total_tokens"] += response.usage.total_tokens or 0


def _run_episode(query: str) -> tuple[str, dict[str, int]]:
    response = model_client.responses.create(
        model=model,
        instructions=instructions,
        input=query,
        tools=SELECTION_TOOLS,
        tool_choice="required",
        parallel_tool_calls=False,
    )
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    _add_usage(usage, response)
    trace: list[dict[str, Any]] = []
    recommended_ids: list[str] = []
    terminated = False

    for _ in range(MAX_TOOL_STEPS):
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            if recommended_ids:
                raise RuntimeError(
                    "The model returned a final answer without calling terminate"
                )
            raise RuntimeError(
                "The model stopped before recommending products and calling terminate"
            )
        outputs = []
        for call in calls:
            arguments = json.loads(call.arguments)
            if call.name == "terminate" and not recommended_ids:
                raise RuntimeError("The model called terminate before recommend_product")
            result = _execute_tool(call.name, arguments)
            trace.append(
                {
                    "function": {
                        "name": call.name,
                        "arguments": arguments,
                    }
                }
            )
            if call.name == "recommend_product":
                recommended_ids = [
                    item.strip()
                    for item in arguments.get("product_ids", "").split(",")
                    if item.strip()
                ]
            elif call.name == "terminate":
                terminated = True
                if TASK == "web":
                    terminal_ids = [
                        item.strip()
                        for item in arguments.get("product_ids", "").split(",")
                        if item.strip()
                    ]
                    if terminal_ids != recommended_ids:
                        raise RuntimeError(
                            "The Web terminate product_ids must match recommend_product"
                        )
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                }
            )
        request: dict[str, Any] = {
            "model": model,
            "previous_response_id": response.id,
            "input": outputs,
        }
        if terminated:
            if TASK == "web":
                break
            request["instructions"] = FINAL_RESPONSE_INSTRUCTION
        else:
            request["tools"] = TERMINATE_TOOLS if recommended_ids else SELECTION_TOOLS
            request["tool_choice"] = "required"
            request["parallel_tool_calls"] = False
        response = model_client.responses.create(**request)
        _add_usage(usage, response)
        if terminated:
            break

    if not terminated:
        raise RuntimeError(
            f"The model did not complete recommend_product then terminate "
            f"within {MAX_TOOL_STEPS} tool steps"
        )

    if TASK == "web":
        terminal = trace[-1]["function"]["arguments"]
        assistant_text = terminal.get("final_answer", "")
    else:
        assistant_text = response.output_text or ""
    if not assistant_text.strip():
        raise RuntimeError("The model terminated without a final user-facing response")

    output = {
        "task": TASK,
        "recommended_product_ids": recommended_ids,
        "terminated": terminated,
        "output_tools": trace,
        "assistant_text": assistant_text,
    }
    return json.dumps(output, ensure_ascii=False), usage


@app.response_handler
async def handler(
    request: CreateResponse,
    context: ResponseContext,
    _cancellation_signal: asyncio.Event,
):
    query = await context.get_input_text() or ""
    logger.info("Processing response_id=%s task=%s", context.response_id, TASK)
    output_text, usage_values = await asyncio.get_running_loop().run_in_executor(
        None,
        _run_episode,
        query,
    )
    usage = ResponseUsage(
        input_tokens=usage_values["input_tokens"],
        output_tokens=usage_values["output_tokens"],
        total_tokens=usage_values["total_tokens"],
        input_tokens_details=ResponseUsageInputTokensDetails(cached_tokens=0),
        output_tokens_details=ResponseUsageOutputTokensDetails(reasoning_tokens=0),
    )
    stream = ResponseEventStream(response_id=context.response_id, request=request)

    async def events():
        yield stream.emit_created()
        yield stream.emit_in_progress()
        message = stream.add_output_item_message()
        yield message.emit_added()
        text_content = message.add_text_content()
        yield text_content.emit_added()
        yield text_content.emit_delta(output_text)
        yield text_content.emit_text_done(output_text)
        yield text_content.emit_done()
        yield message.emit_done()
        yield stream.emit_completed(usage=usage)

    return events()


def run() -> None:
    app.run()


if __name__ == "__main__":
    run()
