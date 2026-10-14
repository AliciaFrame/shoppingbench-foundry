from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from azure.ai.agentserver.optimization import OptimizationConfig
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shoppingbench-vanilla-agent")

TASK = os.getenv("SHOPPINGBENCH_TASK", "product")
AGENTS_ROOT = Path(__file__).resolve().parents[1]
configured_dir = Path(os.environ["OPTIMIZATION_LOCAL_DIR"])
CONFIG_DIR = configured_dir if configured_dir.is_absolute() else AGENTS_ROOT / configured_dir
MODEL = os.getenv("MAI_MODEL_DEPLOYMENT_NAME", "mai-code-1-1-flash-base")
MAX_TOOL_STEPS = int(os.getenv("MAX_TOOL_STEPS", "30"))


def _load_config() -> OptimizationConfig:
    instructions_path = CONFIG_DIR / "instructions.md"
    tools_path = CONFIG_DIR / "tools.json"
    if not instructions_path.is_file() or not tools_path.is_file():
        raise FileNotFoundError(f"Incomplete vanilla configuration: {CONFIG_DIR}")
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
        source="off-the-shelf",
    )


config = _load_config()
instructions = config.compose_instructions()
model = MODEL


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
    "Loaded off-the-shelf task=%s model=%s tools=%d",
    TASK,
    model,
    len(TOOLS),
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
        tools=TOOLS,
    )
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    _add_usage(usage, response)
    trace: list[dict[str, Any]] = []
    recommended_ids: list[str] = []
    terminated = False

    for _ in range(MAX_TOOL_STEPS):
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            break
        outputs = []
        for call in calls:
            arguments = json.loads(call.arguments)
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
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                }
            )
        if terminated:
            break
        response = model_client.responses.create(
            model=model,
            previous_response_id=response.id,
            input=outputs,
            tools=TOOLS,
        )
        _add_usage(usage, response)

    output = {
        "task": TASK,
        "recommended_product_ids": recommended_ids,
        "terminated": terminated,
        "output_tools": trace,
        "assistant_text": response.output_text or "",
    }
    return json.dumps(output, ensure_ascii=False), usage
