"""Live-web RFT grader v6: v5 semantic quality with a succinctness cost.

The trainer sends the grader only the trajectory, not token usage, so v6 prices
the observable drivers of cost: every tool call beyond the minimal
search -> view -> recommend -> terminate path (each turn replays the growing
context and incurs more reasoning) and verbose final answers.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import web_search_rft_grading_v5 as v5
from .artifacts import normalized_text_bytes
from .trajectory import terminal_arguments, tool_calls
from .web_search_rft_grading_v2 import _calls

VERSION = "web-search-rft-v6"
PASS_THRESHOLD = 0.85
JUDGE_REASONING_EFFORT = v5.JUDGE_REASONING_EFFORT
RUBRIC = v5.RUBRIC
GroundingAssessment = v5.GroundingAssessment
assess_grounding = v5.assess_grounding

MINIMAL_CALLS = 4
EXTRA_CALL_COST = 0.08
REPEAT_COST = 0.16
ANSWER_WORD_ALLOWANCE = 30
ANSWER_WORD_COST = 0.005
MAX_ANSWER_COST = 0.12
# Keeps every fully correct answer (>= .60) above the failed-quality cap (.55).
MAX_COST = 0.40


def grader_sha256() -> str:
    return hashlib.sha256(
        normalized_text_bytes(Path(__file__)) + v5.grader_sha256().encode("ascii"),
    ).hexdigest()


def _succinctness(sample: dict[str, Any], efficiency: dict[str, Any]) -> dict[str, Any]:
    calls = tool_calls({**sample, "output_tools": _calls(sample)})
    answer = terminal_arguments(calls).get("final_answer", "")
    words = len(answer.split()) if isinstance(answer, str) else 0
    extra_calls = max(0, len(calls) - MINIMAL_CALLS)
    repeats = efficiency["repeated_searches"] + efficiency["repeated_views"]
    answer_cost = min(
        MAX_ANSWER_COST, ANSWER_WORD_COST * max(0, words - ANSWER_WORD_ALLOWANCE),
    )
    cost = min(MAX_COST, EXTRA_CALL_COST * extra_calls + REPEAT_COST * repeats + answer_cost)
    return {
        "answer_words": words,
        "extra_calls_beyond_minimal": extra_calls,
        "answer_verbosity_cost": round(answer_cost, 6),
        "succinctness_cost": round(cost, 6),
    }


def grade_with_details(
    sample: dict[str, Any], item: dict[str, Any], *,
    assessment: GroundingAssessment | None = None,
) -> dict[str, Any]:
    details = v5.grade_with_details(sample, item, assessment=assessment)
    succinct = _succinctness(sample, details)
    score = round(details["quality_score"] * (1 - succinct["succinctness_cost"]), 6)
    return {
        **details, **succinct, "version": VERSION,
        "v5_score": details["score"], "score": score,
        "score_pass": score > PASS_THRESHOLD,
        "efficiency_only_failure": details["quality_pass"] and score <= PASS_THRESHOLD,
    }


def grade(sample: dict[str, Any], item: dict[str, Any]) -> float:
    return float(grade_with_details(sample, item)["score"])


def endpoint_grade(payload: dict[str, Any]) -> dict[str, float]:
    return {"score": grade(payload["sample"], payload["item"])}
