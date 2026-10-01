from __future__ import annotations

import json
from typing import Any

from .rft_grading import (
    _efficient_searches,
    _expected_ids,
    _normalized,
    _recommend_then_terminate,
    _recommended_ids,
    _tool_calls,
    _viewed_before_recommendation,
)

WEB_V2_PASS_THRESHOLD = 0.9
WEB_V2_VERSION = "web-v2"


def _searches(calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [arguments for name, arguments in calls if name == "find_product"]


def grade_with_details(sample: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    if item.get("task") != "web":
        raise ValueError("The v2 grader currently supports only the Web task")

    calls = _tool_calls(sample)
    expected = _expected_ids(item)
    actual = _recommended_ids(calls)
    searches = _searches(calls)
    search_queries = [_normalized(arguments.get("q")) for arguments in searches]
    knowledge = _normalized(item.get("knowledge_attribute"))
    max_search_calls = int(item.get("max_search_calls", 3))

    exact_product = actual == expected and len(actual) == 1
    viewed_product = _viewed_before_recommendation(calls, actual)
    single_recommendation = sum(name == "recommend_product" for name, _ in calls) == 1
    completed_protocol = _recommend_then_terminate(calls)
    early_knowledge = bool(
        knowledge and any(knowledge in query for query in search_queries[:2])
    )
    efficient_searches = _efficient_searches(calls, max_search_calls)

    serialized_searches = [json.dumps(arguments, sort_keys=True) for arguments in searches]
    excess_searches = max(0, len(searches) - max_search_calls)
    duplicate_searches = len(serialized_searches) - len(set(serialized_searches))

    score = 0.65 if exact_product else 0.0
    score += 0.10 if viewed_product else 0.0
    score += 0.05 if single_recommendation else 0.0
    score += 0.05 if completed_protocol else 0.0
    score += 0.10 if early_knowledge else 0.0
    score += 0.05 if efficient_searches else 0.0
    score -= 0.03 * excess_searches
    score -= 0.05 * duplicate_searches
    score = round(max(0.0, min(1.0, score)), 6)

    return {
        "score": score,
        "version": WEB_V2_VERSION,
        "exact_product": exact_product,
        "viewed_product": viewed_product,
        "single_recommendation": single_recommendation,
        "completed_protocol": completed_protocol,
        "early_knowledge": early_knowledge,
        "efficient_searches": efficient_searches,
        "search_count": len(searches),
        "excess_searches": excess_searches,
        "duplicate_searches": duplicate_searches,
    }


def grade(sample: dict[str, Any], item: dict[str, Any]) -> float:
    return float(grade_with_details(sample, item)["score"])


def endpoint_grade(payload: dict[str, Any]) -> dict[str, float]:
    return {"score": grade(payload.get("sample", {}), payload["item"])}
