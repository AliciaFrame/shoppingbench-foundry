from __future__ import annotations

import json
import re
from typing import Any

MAX_TOTAL_CALLS = 6
MAX_CATALOG_SEARCHES = 2
PASS_THRESHOLD = 0.90


def _arguments(call: dict[str, Any]) -> dict[str, Any]:
    raw = call.get("arguments", {})
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _trajectory(sample: dict[str, Any]) -> list[dict[str, Any]]:
    raw = sample.get("output", sample.get("messages", []))
    if isinstance(raw, dict):
        raw = raw.get("messages", raw.get("output", []))
    return raw if isinstance(raw, list) else []


def _calls(sample: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for raw in sample.get("output_tools", []) or []:
        function = raw.get("function", raw)
        if isinstance(function, dict) and function.get("name"):
            calls.append(
                {
                    "name": function["name"],
                    "arguments": function.get("arguments", {}),
                }
            )
    if calls:
        return calls
    for entry in _trajectory(sample):
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "function_call":
            calls.append(entry)
            continue
        for call in entry.get("tool_calls", []):
            function = call.get("function", {}) if isinstance(call, dict) else {}
            calls.append(
                {
                    "name": function.get("name", ""),
                    "arguments": function.get("arguments", {}),
                }
            )
    return calls


def _normalized_terms(query: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", query.lower()))


def _near_duplicate_searches(calls: list[dict[str, Any]]) -> bool:
    queries = [
        str(_arguments(call).get("q", "")).strip()
        for call in calls
        if call.get("name") == "find_product"
    ]
    terms = [_normalized_terms(query) for query in queries if query]
    for index, left in enumerate(terms):
        for right in terms[index + 1 :]:
            union = left | right
            if union and len(left & right) / len(union) >= 0.85:
                return True
    return False


def calculate_reward(sample: dict[str, Any], item: dict[str, Any]) -> float:
    calls = _calls(sample)
    names = [str(call.get("name", "")) for call in calls]
    expected_product_id = str(item.get("reward", {}).get("product_id", ""))
    expected_clue = str(
        item.get("knowledge_attribute")
        or item.get("reward", {}).get("knowledge_attribute", "")
    ).strip().lower()

    recommended_ids: list[str] = []
    terminated_ids: list[str] = []
    viewed_ids: list[str] = []
    resolved_clues: list[str] = []
    final_answers: list[str] = []
    search_count = names.count("find_product")

    for call in calls:
        name = call.get("name")
        arguments = _arguments(call)
        if name == "recommend_product":
            recommended_ids.extend(
                part.strip()
                for part in str(arguments.get("product_ids", "")).split(",")
                if part.strip()
            )
        elif name == "terminate":
            terminated_ids.extend(
                part.strip()
                for part in str(arguments.get("product_ids", "")).split(",")
                if part.strip()
            )
            resolved_clues.append(str(arguments.get("resolved_clue", "")).strip().lower())
            final_answers.append(str(arguments.get("final_answer", "")).strip().lower())
        elif name == "view_product_information":
            viewed_ids.extend(
                part.strip()
                for part in str(arguments.get("product_ids", "")).split(",")
                if part.strip()
            )

    web_index = names.index("web_search") if "web_search" in names else -1
    catalog_index = names.index("find_product") if "find_product" in names else -1
    web_first = names.count("web_search") == 1 and web_index == 0 and catalog_index > web_index
    evidence = str(sample.get("web_evidence", "")).strip().lower()
    clue_grounded = bool(expected_clue) and (
        any(expected_clue in clue or clue in expected_clue for clue in resolved_clues if clue)
        or expected_clue in evidence
    )
    exact_product = (
        bool(expected_product_id)
        and recommended_ids == [expected_product_id]
        and terminated_ids == [expected_product_id]
    )
    inspected_product = expected_product_id in viewed_ids
    grounded_answer = clue_grounded and any(
        expected_product_id in answer and expected_clue in answer for answer in final_answers
    )
    protocol_complete = (
        bool(recommended_ids)
        and recommended_ids == terminated_ids
        and names[-2:] == ["recommend_product", "terminate"]
    )
    repeated_search = _near_duplicate_searches(calls)
    within_budget = (
        len(calls) <= MAX_TOTAL_CALLS
        and search_count <= MAX_CATALOG_SEARCHES
        and not repeated_search
    )
    ideal_efficiency = len(calls) == 5 and search_count == 1 and within_budget

    reward = (
        0.45 * exact_product
        + 0.15 * clue_grounded
        + 0.10 * web_first
        + 0.10 * inspected_product
        + 0.10 * grounded_answer
        + 0.05 * protocol_complete
        + 0.05 * ideal_efficiency
    )

    if not exact_product:
        reward = min(reward, 0.49)
    if not web_first or not within_budget:
        reward = min(reward, PASS_THRESHOLD - 0.01)
    if not clue_grounded or not inspected_product or not grounded_answer or not protocol_complete:
        reward = min(reward, PASS_THRESHOLD - 0.01)
    return round(float(reward), 6)


def endpoint_grade(payload: dict[str, Any]) -> dict[str, float]:
    return {"score": calculate_reward(payload.get("sample", {}), payload.get("item", {}))}
