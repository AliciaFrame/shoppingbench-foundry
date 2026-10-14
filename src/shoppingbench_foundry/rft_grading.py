from __future__ import annotations

from typing import Any

from .trajectory import (
    efficient_searches,
    grounded_final_answer,
    meaningful_search,
    recommend_then_terminate,
    recommended_ids,
    tool_calls,
    viewed_before_recommendation,
)

RFT_V3_PASS_THRESHOLD = 0.95
RFT_V3_VERSION = "rft-v3"


def _expected_ids(item: dict[str, Any]) -> list[str]:
    reward = item.get("reward", {})
    rewards = reward if isinstance(reward, list) else [reward]
    return [str(entry["product_id"]) for entry in rewards if entry.get("product_id") is not None]


def grade_with_details(sample: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    expected = _expected_ids(item)
    calls = tool_calls(sample)
    actual = recommended_ids(calls)
    exact_selection = bool(expected) and actual == expected
    viewed_selection = viewed_before_recommendation(calls, actual)
    searched_for_constraints = meaningful_search(calls, item)
    single_recommendation = sum(name == "recommend_product" for name, _ in calls) == 1
    completed_protocol = recommend_then_terminate(calls)
    valid_final_answer = grounded_final_answer(sample, item, actual)
    efficient = efficient_searches(
        calls,
        int(item.get("max_search_calls", max(3, 2 * len(expected) + 2))),
    )

    if exact_selection:
        selection_score = 0.70
    else:
        matched_positions = sum(
            1
            for index, product_id in enumerate(actual[: len(expected)])
            if product_id == expected[index]
        )
        selection_score = 0.30 * matched_positions / len(expected) if expected else 0.0

    score = selection_score
    score += 0.08 if viewed_selection else 0.0
    score += 0.04 if searched_for_constraints else 0.0
    score += 0.02 if single_recommendation else 0.0
    score += 0.04 if completed_protocol else 0.0
    score += 0.10 if valid_final_answer else 0.0
    score += 0.02 if exact_selection and efficient else 0.0
    score = round(min(1.0, score), 6)

    return {
        "score": score,
        "version": RFT_V3_VERSION,
        "exact_selection": exact_selection,
        "viewed_selection": viewed_selection,
        "searched_for_constraints": searched_for_constraints,
        "single_recommendation": single_recommendation,
        "completed_protocol": completed_protocol,
        "valid_final_answer": valid_final_answer,
        "efficient_searches": efficient,
    }


def grade(sample: dict[str, Any], item: dict[str, Any]) -> float:
    return float(grade_with_details(sample, item)["score"])


def endpoint_grade(payload: dict[str, Any]) -> dict[str, float]:
    return {"score": grade(payload.get("sample", {}), payload["item"])}
