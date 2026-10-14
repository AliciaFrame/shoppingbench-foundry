from __future__ import annotations

import asyncio
import json
import logging
import os
from functools import lru_cache
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

from shoppingbench_foundry.store import INFORMATION_FIELDS, InMemoryProductStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shoppingbench-web-search-agent")

AGENTS_ROOT = Path(__file__).resolve().parents[1]
configured_dir = Path(os.getenv("OPTIMIZATION_LOCAL_DIR", "web_search/baseline"))
CONFIG_DIR = configured_dir if configured_dir.is_absolute() else AGENTS_ROOT / configured_dir
SELECTION_MODEL = os.getenv(
    "MAI_MODEL_DEPLOYMENT_NAME",
    "shoppingbench-eval-gpt-5-4-mini",
)
WEB_SEARCH_MODEL = os.getenv("WEB_SEARCH_MODEL_DEPLOYMENT_NAME", SELECTION_MODEL)
MAX_TOOL_STEPS = int(os.getenv("MAX_TOOL_STEPS", "8"))
WEB_SEARCH_MAX_OUTPUT_TOKENS = int(os.getenv("WEB_SEARCH_MAX_OUTPUT_TOKENS", "256"))
WEB_SEARCH_MAX_ATTEMPTS = int(os.getenv("WEB_SEARCH_MAX_ATTEMPTS", "3"))
SELECTION_MAX_OUTPUT_TOKENS = int(os.getenv("SELECTION_MAX_OUTPUT_TOKENS", "4096"))
SELECTION_REASONING_EFFORT = os.getenv("SELECTION_REASONING_EFFORT", "").strip()
WEB_SEARCH_SOURCE_LIMIT = int(os.getenv("WEB_SEARCH_SOURCE_LIMIT", "2"))
CATALOG_SEARCH_RESULT_LIMIT = int(os.getenv("CATALOG_SEARCH_RESULT_LIMIT", "10"))
MAX_CATALOG_SEARCHES = int(os.getenv("MAX_CATALOG_SEARCHES", "2"))
MAX_PRODUCT_VIEWS = int(os.getenv("MAX_PRODUCT_VIEWS", "2"))
ENFORCE_SELECTION_TOOL_LIMITS = os.getenv(
    "ENFORCE_SELECTION_TOOL_LIMITS",
    "",
).lower() in {"1", "true", "yes"}
MODE = os.getenv("AGENT_MODE", "baseline")


def _load_config() -> OptimizationConfig:
    instructions_path = CONFIG_DIR / "instructions.md"
    tools_path = CONFIG_DIR / "tools.json"
    if not instructions_path.is_file() or not tools_path.is_file():
        raise FileNotFoundError(f"Incomplete Web-search configuration: {CONFIG_DIR}")
    return OptimizationConfig(
        instructions=instructions_path.read_text(encoding="utf-8").strip(),
        model=SELECTION_MODEL,
        tool_definitions=json.loads(tools_path.read_text(encoding="utf-8")),
        source=f"web-search-{MODE}",
    )


config = _load_config()
instructions = config.compose_instructions()
efficiency_instructions = os.getenv("RFT_EFFICIENCY_INSTRUCTIONS", "").strip()
if efficiency_instructions:
    instructions = f"{instructions}\n\n{efficiency_instructions}"


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
SELECTION_TOOLS_BY_NAME = {tool["name"]: tool for tool in SELECTION_TOOLS}
if len(TERMINATE_TOOLS) != 1:
    raise ValueError("Exactly one terminate tool definition is required")


def _credential_scope(endpoint: str) -> str:
    if ".openai.azure.com" in endpoint:
        return "https://cognitiveservices.azure.com/.default"
    return "https://ai.azure.com/.default"


def _create_client(base_url_name: str, api_key_name: str) -> OpenAI:
    endpoint = os.environ[base_url_name].rstrip("/") + "/"
    api_key: str | Any = os.getenv(api_key_name, "")
    if not api_key:
        api_key = get_bearer_token_provider(
            DefaultAzureCredential(),
            _credential_scope(endpoint),
        )
    return OpenAI(base_url=endpoint, api_key=api_key)


selection_client = _create_client("MAI_OPENAI_BASE_URL", "MAI_API_KEY")
if os.getenv("WEB_SEARCH_OPENAI_BASE_URL"):
    web_search_client = _create_client(
        "WEB_SEARCH_OPENAI_BASE_URL",
        "WEB_SEARCH_API_KEY",
    )
else:
    web_search_client = selection_client
tool_client = httpx.Client(timeout=120)

app = ResponsesAgentServerHost(
    options=ResponsesServerOptions(default_fetch_history_count=1),
)


@lru_cache(maxsize=1)
def _local_store() -> InMemoryProductStore | None:
    documents_path = os.getenv("SHOPPINGBENCH_DOCUMENTS_PATH", "").strip()
    if not documents_path:
        return None
    products = []
    with Path(documents_path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                products.append(json.loads(line)["product"])
    return InMemoryProductStore(products)


def _execute_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "recommend_product":
        return {"recommended": arguments.get("product_ids", "")}
    if name == "terminate":
        return {"terminated": True, **arguments}
    store = _local_store()
    if store is not None:
        if name == "find_product":
            return store.search(
                arguments["q"],
                arguments["page"],
                arguments.get("shop_id"),
                arguments.get("price"),
                arguments.get("sort"),
                arguments.get("service"),
            )[:CATALOG_SEARCH_RESULT_LIMIT]
        if name == "view_product_information":
            product_ids = [
                item.strip()
                for item in arguments.get("product_ids", "").split(",")
                if item.strip()
            ]
            return [
                {field: product.get(field) for field in INFORMATION_FIELDS}
                for product in store.get_products(product_ids)
            ]
    tool_url = os.environ["SHOPPINGBENCH_TOOL_URL"].rstrip("/")
    token = os.getenv("SHOPPINGBENCH_API_TOKEN", "")
    headers = {"Authorization": "Bearer " + token} if token else {}
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
    else:
        raise ValueError(f"Unknown tool: {name}")
    response.raise_for_status()
    payload = response.json()
    if name == "find_product" and isinstance(payload, list):
        return payload[:CATALOG_SEARCH_RESULT_LIMIT]
    return payload


def _add_usage(total: dict[str, int], response: Any) -> None:
    if not response.usage:
        return
    total["input_tokens"] += response.usage.input_tokens or 0
    total["output_tokens"] += response.usage.output_tokens or 0
    total["total_tokens"] += response.usage.total_tokens or 0


def _selection_tools(catalog_searches: int, product_views: int) -> list[dict[str, Any]]:
    names = ["recommend_product"]
    if catalog_searches < MAX_CATALOG_SEARCHES:
        names.insert(0, "find_product")
    if product_views < MAX_PRODUCT_VIEWS:
        names.insert(-1, "view_product_information")
    return [SELECTION_TOOLS_BY_NAME[name] for name in names]


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


def _search_web(query: str, usage: dict[str, int]) -> tuple[str, list[dict[str, str]]]:
    for _ in range(WEB_SEARCH_MAX_ATTEMPTS):
        response = web_search_client.responses.create(
            model=WEB_SEARCH_MODEL,
            instructions=(
                "Use web search to resolve the factual clue in the shopping request. "
                "Return only the shortest precise factual answer needed for product "
                "selection and include source citations."
            ),
            input=query,
            tools=[{"type": "web_search"}],
            max_output_tokens=WEB_SEARCH_MAX_OUTPUT_TOKENS,
        )
        _add_usage(usage, response)
        evidence_text = (response.output_text or "").strip()
        if evidence_text:
            return evidence_text, _web_sources(response)[:WEB_SEARCH_SOURCE_LIMIT]
    raise RuntimeError("Web search returned no grounded clue resolution")


def _run_episode(query: str) -> tuple[str, dict[str, int]]:
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    evidence_text, sources = _search_web(query, usage)
    source_text = "\n".join(
        f"- {source['title'] or 'Source'}: {source['url']}" for source in sources
    )

    selection_input = (
        f"Original request:\n{query}\n\n"
        f"Grounded web evidence:\n{evidence_text}\n\n"
        f"Sources:\n{source_text or '- No source URL returned'}\n\n"
        "Use the catalog tools to select the product. Treat the web evidence as the "
        "authoritative clue resolution."
    )
    request: dict[str, Any] = {
        "model": SELECTION_MODEL,
        "instructions": instructions,
        "input": selection_input,
        "tools": SELECTION_TOOLS,
        "max_output_tokens": SELECTION_MAX_OUTPUT_TOKENS,
    }
    if SELECTION_REASONING_EFFORT:
        request["reasoning"] = {"effort": SELECTION_REASONING_EFFORT}
    if MODE != "baseline":
        request["tool_choice"] = "required"
        request["parallel_tool_calls"] = False
    response = selection_client.responses.create(**request)
    _add_usage(usage, response)

    trace: list[dict[str, Any]] = [
        {"function": {"name": "web_search", "arguments": {"query": query}}}
    ]
    recommended_ids: list[str] = []
    terminal: dict[str, Any] = {}
    catalog_searches = 0
    product_views = 0
    for _ in range(MAX_TOOL_STEPS):
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            break
        outputs = []
        for call in calls:
            arguments = json.loads(call.arguments)
            if call.name == "terminate" and not recommended_ids:
                raise RuntimeError("The model called terminate before recommend_product")
            if call.name == "terminate":
                attempted_ids = [
                    item.strip()
                    for item in str(arguments.get("product_ids", "")).split(",")
                    if item.strip()
                ]
                if attempted_ids != recommended_ids:
                    outputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": json.dumps(
                                {
                                    "terminated": False,
                                    "error": (
                                        "terminate product_ids must exactly match the prior "
                                        "recommend_product call"
                                    ),
                                    "required_product_ids": recommended_ids,
                                }
                            ),
                        }
                    )
                    continue
            result = _execute_tool(call.name, arguments)
            trace.append({"function": {"name": call.name, "arguments": arguments}})
            if call.name == "find_product":
                catalog_searches += 1
            elif call.name == "view_product_information":
                product_views += 1
            elif call.name == "recommend_product":
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
            "model": SELECTION_MODEL,
            "previous_response_id": response.id,
            "input": outputs,
            "tools": (
                TERMINATE_TOOLS
                if recommended_ids
                else (
                    _selection_tools(catalog_searches, product_views)
                    if ENFORCE_SELECTION_TOOL_LIMITS
                    else SELECTION_TOOLS
                )
            ),
            "max_output_tokens": SELECTION_MAX_OUTPUT_TOKENS,
        }
        if SELECTION_REASONING_EFFORT:
            next_request["reasoning"] = {"effort": SELECTION_REASONING_EFFORT}
        if MODE != "baseline" or recommended_ids:
            next_request["tool_choice"] = "required"
            next_request["parallel_tool_calls"] = False
        response = selection_client.responses.create(**next_request)
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
