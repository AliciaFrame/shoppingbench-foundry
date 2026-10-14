from __future__ import annotations

import json
from typing import Any

from .trajectory import (
    ids,
    meaningful_search,
    normalized,
    recommended_ids,
    terminal_arguments,
    tool_calls,
)
from .web_rft_grading_v5 import grade_with_details as web_v5_details
from .web_search_rft_grading_v2 import _calls

VERSION = "web-search-rft-v3"


def grade_with_details(sample: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Score the catalog continuation of the optimized live-web workflow.

    The fixed web resolver is evaluated end-to-end, not rewarded as a model action.
    Inputs must contain its actual evidence, never the answer label as a substitute.
    """
    sample = {**sample, "output_tools": _calls(sample)}
    calls = tool_calls(sample)
    names = [name for name, _ in calls]
    expected = [str(item["reward"]["product_id"])]
    actual = recommended_ids(calls)
    terminal = terminal_arguments(calls)
    reference = web_v5_details(sample, item)
    single_recommendation = names.count("recommend_product") == 1
    correct_recommendation = single_recommendation and actual == expected
    correct_terminal = (
        correct_recommendation
        and names.count("terminate") == 1
        and ids(terminal.get("product_ids")) == expected
    )
    components = {
        "exact_recommendation": correct_recommendation,
        "exact_terminal": correct_terminal,
        "correct_clue": reference["correct_clue"],
        "meaningful_search": meaningful_search(calls, item),
        "viewed_selected": reference["viewed_selected"],
        "single_recommendation": single_recommendation,
        "completed_protocol": correct_terminal and reference["completed_protocol"],
        "grounded_final_answer": correct_terminal
        and reference["correct_clue"]
        and bool(terminal.get("final_answer"))
        and reference["grounded_final_answer"],
    }
    weights = {
        "exact_recommendation": 0.55,
        "exact_terminal": 0.15,
        "correct_clue": 0.05,
        "meaningful_search": 0.025,
        "viewed_selected": 0.025,
        "single_recommendation": 0.025,
        "completed_protocol": 0.025,
        "grounded_final_answer": 0.15,
    }
    quality = sum(weights[name] * float(value) for name, value in components.items())

    searches = []
    for name, arguments in calls:
        if name != "find_product":
            continue
        canonical = dict(arguments)
        canonical["q"] = normalized(canonical.get("q"))
        canonical.setdefault("page", 1)
        for field in ("shop_id", "price", "service"):
            if not canonical.get(field):
                canonical.pop(field, None)
        if canonical.get("sort") in (None, "", "default"):
            canonical.pop("sort", None)
        searches.append(json.dumps(canonical, sort_keys=True))
    repeats = len(searches) - len(set(searches))
    # Bounded costs keep an accurate longer search preferable to a fast wrong answer.
    search_penalty = min(0.25, 0.05 * max(0, len(searches) - 2) + 0.025 * repeats)
    score = max(0.0, quality - search_penalty)
    return {
        "version": VERSION,
        "score": round(score, 6),
        "quality_score": round(quality, 6),
        "search_penalty": round(search_penalty, 6),
        "catalog_searches": len(searches),
        "repeated_searches": repeats,
        "components": components,
        "failed_components": [name for name, value in components.items() if not value],
    }


def grade(sample: dict[str, Any], item: dict[str, Any]) -> float:
    return float(grade_with_details(sample, item)["score"])
