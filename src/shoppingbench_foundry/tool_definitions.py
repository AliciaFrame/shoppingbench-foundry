from __future__ import annotations

from copy import deepcopy
from typing import Any

RESPONSES_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "find_product",
        "description": "Search products and return up to ten results.",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "page": {"type": "integer", "minimum": 1, "maximum": 5},
                "shop_id": {"type": "string"},
                "price": {"type": "string"},
                "sort": {"type": "string", "enum": ["default", "order", "priceasc", "pricedesc"]},
                "service": {"type": "string"},
            },
            "required": ["q", "page"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "view_product_information",
        "description": "Fetch detailed information for comma-separated product IDs.",
        "parameters": {
            "type": "object",
            "properties": {"product_ids": {"type": "string"}},
            "required": ["product_ids"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "recommend_product",
        "description": "Recommend ordered comma-separated product IDs. Use once.",
        "parameters": {
            "type": "object",
            "properties": {"product_ids": {"type": "string"}},
            "required": ["product_ids"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "terminate",
        "description": "End the shopping episode.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

WEB_TERMINATE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_ids": {
            "type": "string",
            "description": "The same ordered product IDs submitted to recommend_product.",
        },
        "resolved_clue": {
            "type": "string",
            "description": "The factual answer to the user's knowledge clue.",
        },
        "final_answer": {
            "type": "string",
            "description": (
                "The complete user-facing answer naming the resolved clue and recommended "
                "product ID."
            ),
        },
    },
    "required": ["product_ids", "resolved_clue", "final_answer"],
    "additionalProperties": False,
}


def responses_tools(task: str | None = None) -> list[dict[str, Any]]:
    tools = deepcopy(RESPONSES_TOOLS)
    if task == "web":
        terminate = next(tool for tool in tools if tool["name"] == "terminate")
        terminate["description"] = (
            "End the Web shopping episode with the resolved clue and complete final answer."
        )
        terminate["parameters"] = deepcopy(WEB_TERMINATE_PARAMETERS)
    return tools


def chat_completions_tools(task: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                key: deepcopy(tool[key])
                for key in ("name", "description", "parameters")
            },
        }
        for tool in responses_tools(task)
    ]
