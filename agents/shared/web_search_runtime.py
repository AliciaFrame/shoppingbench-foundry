from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from azure.ai.agentserver.optimization import OptimizationConfig
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
logger = logging.getLogger("shoppingbench-web-search-agent")

AGENTS_ROOT = Path(__file__).resolve().parents[1]
configured_dir = Path(os.getenv("OPTIMIZATION_LOCAL_DIR", "web_search/baseline"))
CONFIG_DIR = configured_dir if configured_dir.is_absolute() else AGENTS_ROOT / configured_dir
MODEL = os.getenv("MAI_MODEL_DEPLOYMENT_NAME", "shoppingbench-eval-gpt-5-4-mini")
MAX_TOOL_STEPS = int(os.getenv("MAX_TOOL_STEPS", "8"))
MODE = os.getenv("AGENT_MODE", "baseline")


def _load_config() -> OptimizationConfig:
    instructions_path = CONFIG_DIR / "instructions.md"
    tools_path = CONFIG_DIR / "tools.json"
    if not instructions_path.is_file() or not tools_path.is_file():
        raise FileNotFoundError(f"Incomplete Web-search configuration: {CONFIG_DIR}")
    return OptimizationConfig(
        instructions=instructions_path.read_text(encoding="utf-8").strip(),
        model=MODEL,
        tool_definitions=json.loads(tools_path.read_text(encoding="utf-8")),
        source=f"web-search-{MODE}",
    )


config = _load_config()
instructions = config.compose_instructions()


def _load_tools() -> list[dict[str, Any]]:
    tools = []
    for definition in config.tool_definitions or []:
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


TOOLS = _load_tools()
TERMINATE_TOOLS = [tool for tool in TOOLS if tool["name"] == "terminate"]
SELECTION_TOOLS = [tool for tool in TOOLS if tool["name"] != "terminate"]
if len(TERMINATE_TOOLS) != 1:
    raise ValueError("Exactly one terminate tool definition is required")


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

app = ResponsesAgentServerHost(
    options=ResponsesServerOptions(default_fetch_history_count=1),
)


def _execute_tool(name: str, arguments: dict[str, Any]) -> Any:
    tool_url = os.environ["SHOPPINGBENCH_TOOL_URL"].rstrip("/")
    token = os.environ["SHOPPINGBENCH_API_TOKEN"]
    headers = {"Authorization": "Bearer " + token}
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
        return {"terminated": True, **arguments}
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


def _web_sources(response: Any) -> list[dict[str, str]]:
    payload = response.model_dump(mode="json")
    sources: list[dict[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            citation = value.get("url_citation")
            if isinstance(citation, dict) and citation.get("url"):
                source = {
                    "title": str(citation.get("title", "")),
                    "url": str(citation["url"]),
                }
                if source not in sources:
                    sources.append(source)
            if value.get("type") == "url_citation" and value.get("url"):
                source = {
                    "title": str(value.get("title", "")),
                    "url": str(value["url"]),
                }
                if source not in sources:
                    sources.append(source)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return sources


def _run_episode(query: str) -> tuple[str, dict[str, int]]:
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    web_response = model_client.responses.create(
        model=MODEL,
        instructions=(
            "Use web search to resolve the factual clue in the shopping request. "
            "Return the shortest precise factual answer and include source citations."
        ),
        input=query,
        tools=[{"type": "web_search"}],
    )
    _add_usage(usage, web_response)
    evidence_text = (web_response.output_text or "").strip()
    if not evidence_text:
        raise RuntimeError("Web search returned no grounded clue resolution")
    sources = _web_sources(web_response)

    selection_input = (
        f"Original request:\n{query}\n\n"
        f"Grounded web evidence:\n{evidence_text}\n\n"
        "Use the catalog tools to select the product. Treat the web evidence as the "
        "authoritative clue resolution."
    )
    request: dict[str, Any] = {
        "model": MODEL,
        "instructions": instructions,
        "input": selection_input,
        "tools": SELECTION_TOOLS,
    }
    if MODE != "baseline":
        request["tool_choice"] = "required"
        request["parallel_tool_calls"] = False
    response = model_client.responses.create(**request)
    _add_usage(usage, response)

    trace: list[dict[str, Any]] = [
        {"function": {"name": "web_search", "arguments": {"query": query}}}
    ]
    recommended_ids: list[str] = []
    terminal: dict[str, Any] = {}
    for _ in range(MAX_TOOL_STEPS):
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            break
        outputs = []
        for call in calls:
            arguments = json.loads(call.arguments)
            if call.name == "terminate" and not recommended_ids:
                raise RuntimeError("The model called terminate before recommend_product")
            result = _execute_tool(call.name, arguments)
            trace.append({"function": {"name": call.name, "arguments": arguments}})
            if call.name == "recommend_product":
                recommended_ids = [
                    item.strip()
                    for item in arguments.get("product_ids", "").split(",")
                    if item.strip()
                ]
            elif call.name == "terminate":
                terminal = arguments
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                }
            )
        if terminal:
            break
        next_request: dict[str, Any] = {
            "model": MODEL,
            "previous_response_id": response.id,
            "input": outputs,
            "tools": TERMINATE_TOOLS if recommended_ids else SELECTION_TOOLS,
        }
        if MODE != "baseline" or recommended_ids:
            next_request["tool_choice"] = "required"
            next_request["parallel_tool_calls"] = False
        response = model_client.responses.create(**next_request)
        _add_usage(usage, response)

    terminal_ids = [
        item.strip()
        for item in str(terminal.get("product_ids", "")).split(",")
        if item.strip()
    ]
    if terminal and terminal_ids != recommended_ids:
        raise RuntimeError("The terminate product_ids must match recommend_product")

    output = {
        "task": "web",
        "recommended_product_ids": recommended_ids,
        "terminated": bool(terminal),
        "output_tools": trace,
        "assistant_text": terminal.get("final_answer", response.output_text or ""),
        "resolved_clue": terminal.get("resolved_clue", ""),
        "web_evidence": evidence_text,
        "web_sources": sources,
    }
    return json.dumps(output, ensure_ascii=False), usage


@app.response_handler
async def handler(
    request: CreateResponse,
    context: ResponseContext,
    _cancellation_signal: asyncio.Event,
):
    query = await context.get_input_text() or ""
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
