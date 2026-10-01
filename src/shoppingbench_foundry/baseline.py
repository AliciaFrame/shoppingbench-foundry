from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI

from .datasets import load_task_rows
from .tool_definitions import RESPONSES_TOOLS

SYSTEM_PROMPT = """You are a shopping agent. Use the provided tools to satisfy every constraint.
Search broadly, inspect product details before recommending, preserve requested product order,
call recommend_product exactly once, and call terminate when finished."""

TOOLS = RESPONSES_TOOLS


def create_client(base_url: str) -> OpenAI:
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    if not api_key:
        api_key = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
    return OpenAI(base_url=base_url.rstrip("/") + "/", api_key=api_key)


def execute_tool(tool_url: str, token: str | None, name: str, arguments: dict[str, Any]) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if name == "find_product":
        response = httpx.get(
            f"{tool_url}/find_product", params=arguments, headers=headers, timeout=120
        )
    elif name == "view_product_information":
        response = httpx.get(
            f"{tool_url}/view_product_information",
            params=arguments,
            headers=headers,
            timeout=120,
        )
    elif name in {"recommend_product", "terminate"}:
        return {"ok": True}
    else:
        raise ValueError(f"Unknown tool: {name}")
    response.raise_for_status()
    return response.json()


def run_episode(
    client: OpenAI,
    model: str,
    query: str,
    tool_url: str,
    token: str | None,
    max_steps: int,
) -> dict[str, Any]:
    response = client.responses.create(
        model=model,
        instructions=SYSTEM_PROMPT,
        input=query,
        tools=TOOLS,
    )
    tool_trace: list[dict[str, Any]] = []
    for _ in range(max_steps):
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            break
        outputs = []
        for call in calls:
            arguments = json.loads(call.arguments)
            result = execute_tool(tool_url, token, call.name, arguments)
            tool_trace.append(
                {"function": {"name": call.name, "arguments": call.arguments}, "output": result}
            )
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                }
            )
        if any(call.name == "terminate" for call in calls):
            break
        response = client.responses.create(
            model=model,
            previous_response_id=response.id,
            input=outputs,
            tools=TOOLS,
        )
    return {
        "response_id": response.id,
        "output_text": response.output_text,
        "output_tools": tool_trace,
        "usage": response.usage.model_dump() if response.usage else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--base-url", default=os.getenv("AZURE_OPENAI_BASE_URL"))
    parser.add_argument("--model", default=os.getenv("MODEL_DEPLOYMENT_NAME"))
    parser.add_argument(
        "--tool-url", default=os.getenv("SHOPPINGBENCH_TOOL_URL", "http://localhost:8000")
    )
    parser.add_argument("--tool-token", default=os.getenv("SHOPPINGBENCH_API_TOKEN"))
    parser.add_argument("--data-dir", type=Path, default=repo_root / "data" / "source")
    parser.add_argument("--task", choices=["product", "shop", "voucher", "web"], default="product")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.base_url or not args.model:
        raise SystemExit("AZURE_OPENAI_BASE_URL and MODEL_DEPLOYMENT_NAME are required")
    client = create_client(args.base_url)
    rows = load_task_rows(args.data_dir, args.task)[: args.limit]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            result = run_episode(
                client, args.model, row["query"], args.tool_url, args.tool_token, args.max_steps
            )
            handle.write(json.dumps({"item": row, "sample": result}, ensure_ascii=False) + "\n")
            handle.flush()


if __name__ == "__main__":
    main()
