from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from . import web_search_rft_grading_v4 as v4
from .trajectory import ids, normalized, terminal_arguments, tool_calls
from .web_search import _required_env, get_web_search_client
from .web_search_rft_grading_v2 import _calls

VERSION = "web-search-rft-v5"
PASS_THRESHOLD = 0.85
FAILED_QUALITY_CAP = 0.55
MAX_EFFICIENCY_COST = 0.36
JUDGE_REASONING_EFFORT = v4.JUDGE_REASONING_EFFORT


class GroundingAssessment(v4.GroundingAssessment):
    clue_correctness: Literal["full", "partial", "none"]

RUBRIC = v4.RUBRIC + """
ADDITIONAL REQUIRED DIMENSION: clue_correctness
Compare BOTH candidate.resolved_clue and the factual resolution in final_answer
with accepted_clue and accepted_aliases IN THE CONTEXT OF THE USER QUESTION.
Judge meaning, not spelling or literal substrings. Full means the same factual
answer: accept equivalent dimensional notation, exact unit conversions, written
numbers, ordinal forms, accents, names/aliases, abbreviations and paraphrases
when they preserve the requested fact. Partial means material ambiguity or an
incomplete resolution; none means a different quantity/entity/date, contradictory
units, negation of the answer, or omission of the resolution. A matching keyword
inside a negation or contradictory answer is not correct.
The reference answer is not an exhaustive wording template. Additional supported
specificity, subtitles, parenthetical credits, or disambiguation do not make the
same answer incorrect or ambiguous. Require an actual contradiction or a change
to the requested fact for a deduction, not merely more detail than the reference.
This is separate from clue_faithfulness, which checks captured evidence.
A candidate can faithfully repeat WRONG web evidence and still have NONE for
clue_correctness. Do not override the reference answer because evidence disagrees.
Conversely, matching the reference does not excuse contradicting the evidence.
All four dimensions must be full to pass quality. Do not penalize equivalent
wording in any dimension; the literal accepted-clue text need not appear.

MATERIALITY RULE FOR THESE RATINGS:
Deduct for an unsupported assertion only when it materially changes the factual
resolution, product identity, compatibility, origin, affiliation, capabilities,
availability, or purchase promise. Do not deduct for harmless wording:
- Treat supplied product metadata as one evidence record. Saying "description"
  when the fact appears in the title or specification is not an unsupported
  product fact. Do not demand a particular field when another supports it.
- A children's picture book explicitly about the identified person can be
  described as a picture-book biography. Do not require a separate genre field.
  This does not support inventing events from the person's life or book contents.
- An incidental team nickname accompanying the correct explicit university/team
  name is not itself a claim of official licensing or a different product.
  Deduct for a contradictory identity or unsupported official affiliation, not
  merely for an additional label absent from sparse metadata.
- Conservative catalog-evidence caveats such as "the catalog does not list a
  specifically named variant" describe the evidence available to the agent,
  not global inventory. Do not penalize these limitations because this grading
  packet omits other search results. Do not reinterpret "not listed/specified"
  as "unavailable/sold out." Explicit stock, exclusivity, delivery or global
  availability assertions still require evidence and are not such caveats.
These rules do NOT excuse material overclaims: a country/style word does not
establish manufacture in that country; a shared name does not establish the same
person; a generation number does not establish a geographic design theme.
Use partial for a material unsupported secondary claim, none for a fabricated or
contradictory central connection. For a deduction identify that material claim,
not merely a missing metadata field. Full permits harmless wording as above.
"""


def grader_sha256() -> str:
    return hashlib.sha256(
        Path(__file__).read_bytes() + v4.grader_sha256().encode("ascii"),
    ).hexdigest()


def assess_grounding(sample: dict[str, Any], item: dict[str, Any]) -> GroundingAssessment:
    evidence = item.get("live_web_context", {}).get("evidence", "")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("The v5 grader requires trusted captured evidence in item.live_web_context")
    terminal = terminal_arguments(tool_calls({**sample, "output_tools": _calls(sample)}))
    fields = (
        "product_id", "title", "brand", "category", "short_description", "description",
        "specification", "attributes", "sku_options", "price", "service",
    )
    payload = {
        "query": item["query"],
        "web_evidence": evidence,
        "sources": item["live_web_context"].get("sources", []),
        "accepted_clue": item["knowledge_attribute"],
        "accepted_aliases": item.get("knowledge_aliases", []),
        "reference_product": {key: item["reward"][key] for key in fields if key in item["reward"]},
        "candidate": {key: terminal.get(key, "") for key in ("resolved_clue", "final_answer")},
    }
    response = get_web_search_client().with_options(timeout=60, max_retries=2).responses.parse(
        model=_required_env("RFT_GRADER_MODEL_DEPLOYMENT_NAME"),
        instructions=RUBRIC,
        input=json.dumps(payload, ensure_ascii=False),
        text_format=GroundingAssessment,
        reasoning={"effort": JUDGE_REASONING_EFFORT},
        max_output_tokens=4096,
        store=False,
    )
    if response.status != "completed" or response.output_parsed is None:
        raise RuntimeError(f"Grounding grader returned no complete assessment: {response.status}")
    return response.output_parsed


def _search_signature(arguments: dict[str, Any]) -> str:
    canonical = dict(arguments)
    query = normalized(canonical.get("q", ""))
    terms = query.split()
    if re.fullmatch(r"[\w\s-]+", query) and not {"not", "no", "without", "vs"} & set(terms):
        query = " ".join(sorted(terms))
    canonical["q"] = query
    canonical.setdefault("page", 1)
    for key in ("shop_id", "price", "service"):
        if not canonical.get(key):
            canonical.pop(key, None)
    if canonical.get("sort") in (None, "", "default"):
        canonical.pop("sort", None)
    return json.dumps(canonical, sort_keys=True)


def _efficiency(calls: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    searches = [_search_signature(args) for name, args in calls if name == "find_product"]
    repeats = len(searches) - len(set(searches))
    seen: set[str] = set()
    repeated_views = 0
    for name, arguments in calls:
        if name == "view_product_information":
            requested = set(ids(arguments.get("product_ids")))
            # Inspecting a batch with any new item is not a wholly redundant view.
            repeated_views += bool(requested) and requested <= seen
            seen.update(requested)
    extra_searches = max(0, len(searches) - 1)
    extra_other_calls = max(0, len(calls) - len(searches) - 3)
    cost = min(
        MAX_EFFICIENCY_COST,
        0.04 * min(extra_searches, 1)
        + 0.12 * max(0, extra_searches - 1)
        + 0.03 * extra_other_calls
        + 0.16 * (repeats + repeated_views),
    )
    return {
        "total_calls": len(calls),
        "catalog_searches": len(searches),
        "extra_calls": max(0, len(calls) - 4),
        "extra_other_calls": extra_other_calls,
        "repeated_searches": repeats,
        "repeated_views": repeated_views,
        "efficiency_cost": round(cost, 6),
    }


def grade_with_details(
    sample: dict[str, Any], item: dict[str, Any], *,
    assessment: GroundingAssessment | None = None,
) -> dict[str, Any]:
    # Reuse frozen structural checks without invoking v4's semantic judge.
    full = v4.GroundingAssessment(
        issues=[], clue_faithfulness="full", product_faithfulness="full",
        answer_completeness="full",
    )
    structural = v4.grade_with_details(sample, item, assessment=full)
    calls = tool_calls({**sample, "output_tools": _calls(sample)})
    terminal = terminal_arguments(calls)
    semantic_fields = (
        "clue_correctness", "clue_faithfulness", "product_faithfulness", "answer_completeness",
    )
    if structural["grounding"] is not None:
        assessment = assessment or assess_grounding(sample, item)
        ratings = {
            field: {"full": 1.0, "partial": 0.5, "none": 0.0}[getattr(assessment, field)]
            for field in semantic_fields
        }
    else:
        assessment = None
        ratings = dict.fromkeys(semantic_fields, 0.0)
    failed = [
        name for name in structural["failed_gates"]
        if name not in {"correct_clue", "grounded_answer_structure", *semantic_fields}
    ]
    answer = terminal.get("final_answer", "")
    expected = str(item["reward"]["product_id"])
    if (
        not isinstance(answer, str)
        or re.search(rf"(?<!\w){re.escape(expected)}(?!\w)", answer) is None
        or set(re.findall(r"(?<!\d)\d{8,12}(?!\d)", answer)) - {expected}
    ):
        failed.append("answer_product_ids")
    failed.extend(name for name, rating in ratings.items() if rating != 1)
    weights = {
        "exact_recommendation": 0.40, "exact_terminal": 0.10, "viewed_expected": 0.10,
        "meaningful_search": 0.05, "completed_protocol": 0.05,
    }
    quality = sum(weights[name] * value for name, value in structural["components"].items())
    quality += 0.10 * sum(ratings[name] for name in semantic_fields if name != "clue_correctness")
    if failed:
        quality *= FAILED_QUALITY_CAP
    if not structural["components"]["exact_recommendation"]:
        quality = min(quality, 0.20)
    efficiency = _efficiency(calls)
    score = round(quality * (1 - efficiency["efficiency_cost"]), 6)
    return {
        **structural, **efficiency, "version": VERSION,
        "quality_score": round(quality, 6), "score": score,
        "quality_pass": not failed, "failed_gates": failed,
        "grounding": assessment.model_dump() if assessment is not None else None,
        "score_pass": score > PASS_THRESHOLD,
        "efficiency_only_failure": not failed and score <= PASS_THRESHOLD,
    }


def grade(sample: dict[str, Any], item: dict[str, Any]) -> float:
    return float(grade_with_details(sample, item)["score"])


def endpoint_grade(payload: dict[str, Any]) -> dict[str, float]:
    return {"score": grade(payload["sample"], payload["item"])}
