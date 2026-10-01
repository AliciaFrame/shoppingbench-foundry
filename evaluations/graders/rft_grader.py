from __future__ import annotations

import json
from typing import Any


def _expected_ids(item: dict[str, Any]) -> list[str]:
    reward = item.get("reward", {})
    rewards = reward if isinstance(reward, list) else [reward]
    return [str(entry["product_id"]) for entry in rewards if entry.get("product_id") is not None]


def _tool_calls(sample: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for raw in sample.get("output_tools", []) or []:
        function = raw.get("function", raw)
        name = function.get("name")
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if name and isinstance(arguments, dict):
            calls.append((name, arguments))
    return calls


def _recommended_ids(calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    recommendations = [
        arguments.get("product_ids", "") for name, arguments in calls if name == "recommend_product"
    ]
    if not recommendations:
        return []
    return [value.strip() for value in str(recommendations[-1]).split(",") if value.strip()]


def _ids(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _recommendation_index(calls: list[tuple[str, dict[str, Any]]]) -> int | None:
    return next(
        (index for index, (name, _) in enumerate(calls) if name == "recommend_product"), None
    )


def _viewed_before_recommendation(
    calls: list[tuple[str, dict[str, Any]]], actual: list[str]
) -> bool:
    recommendation_index = _recommendation_index(calls)
    if recommendation_index is None:
        return False
    viewed = {
        product_id
        for name, arguments in calls[:recommendation_index]
        if name == "view_product_information"
        for product_id in _ids(arguments.get("product_ids"))
    }
    return bool(actual) and set(actual).issubset(viewed)


def _recommend_then_terminate(calls: list[tuple[str, dict[str, Any]]]) -> bool:
    recommendation_index = _recommendation_index(calls)
    return (
        recommendation_index is not None
        and recommendation_index + 1 == len(calls) - 1
        and calls[-1][0] == "terminate"
    )


def _efficient_searches(calls: list[tuple[str, dict[str, Any]]], limit: int) -> bool:
    searches = [
        json.dumps(arguments, sort_keys=True) for name, arguments in calls if name == "find_product"
    ]
    return 0 < len(searches) <= limit and len(searches) == len(set(searches))


def _grade_product(
    calls: list[tuple[str, dict[str, Any]]],
    expected: list[str],
    actual: list[str],
    item: dict[str, Any],
) -> float:
    score = 0.60 if actual == expected and len(actual) == 1 else 0.0
    score += 0.15 if _viewed_before_recommendation(calls, actual) else 0.0
    recommendation_index = _recommendation_index(calls)
    if recommendation_index is not None:
        prefix_names = [name for name, _ in calls[:recommendation_index]]
        score += 0.05 if "find_product" in prefix_names else 0.0
    score += 0.05 if sum(name == "recommend_product" for name, _ in calls) == 1 else 0.0
    score += 0.10 if _recommend_then_terminate(calls) else 0.0
    score += 0.05 if _efficient_searches(calls, int(item.get("max_search_calls", 3))) else 0.0
    return round(score, 6)


def _grade_web(
    calls: list[tuple[str, dict[str, Any]]],
    expected: list[str],
    actual: list[str],
    item: dict[str, Any],
) -> float:
    score = 0.55 if actual == expected and len(actual) == 1 else 0.0
    knowledge = _normalized(item.get("knowledge_attribute"))
    search_queries = [
        _normalized(arguments.get("q")) for name, arguments in calls if name == "find_product"
    ]
    score += 0.15 if knowledge and any(knowledge in query for query in search_queries) else 0.0
    score += 0.10 if _viewed_before_recommendation(calls, actual) else 0.0
    score += 0.05 if sum(name == "recommend_product" for name, _ in calls) == 1 else 0.0
    score += 0.05 if _recommend_then_terminate(calls) else 0.0
    score += 0.10 if _efficient_searches(calls, int(item.get("max_search_calls", 3))) else 0.0
    return round(score, 6)


def _grade_generic(
    calls: list[tuple[str, dict[str, Any]]],
    expected: list[str],
    actual: list[str],
    item: dict[str, Any],
) -> float:
    expected = _expected_ids(item)
    if not expected:
        return 0.0

    if actual == expected:
        selection_score = 0.65
    else:
        matched_positions = sum(
            1
            for index, product_id in enumerate(actual[: len(expected)])
            if product_id == expected[index]
        )
        selection_score = 0.45 * matched_positions / len(expected)

    names = [name for name, _ in calls]
    process_score = 0.0
    process_score += 0.05 if "find_product" in names else 0.0
    process_score += 0.10 if "view_product_information" in names else 0.0
    process_score += 0.05 if names.count("recommend_product") == 1 else 0.0
    process_score += 0.10 if names.count("terminate") == 1 and names[-1:] == ["terminate"] else 0.0

    max_search_calls = int(item.get("max_search_calls", max(3, 2 * len(expected) + 2)))
    search_calls = names.count("find_product")
    if 0 < search_calls <= max_search_calls:
        process_score += 0.025
    if len(actual) == len(set(actual)):
        process_score += 0.025

    return round(min(1.0, selection_score + process_score), 6)


def grade(sample: dict[str, Any], item: dict[str, Any]) -> float:
    """Score task correctness and learnable tool-use behavior."""
    expected = _expected_ids(item)
    calls = _tool_calls(sample)
    actual = _recommended_ids(calls)
    if item.get("task") == "product":
        return _grade_product(calls, expected, actual, item)
    if item.get("task") == "web":
        return _grade_web(calls, expected, actual, item)
    return _grade_generic(calls, expected, actual, item)
