from __future__ import annotations

import json
import re
from typing import Any

from .trajectory import (
    final_answer_text,
    ids,
    normalized,
    recommend_then_terminate,
    recommended_ids,
    resolved_clue,
    terminal_arguments,
    tool_calls,
    viewed_before_recommendation,
)

WEB_RFT_V5_VERSION = "web-rft-v5"
MAX_CATALOG_SEARCHES = 2
MAX_TOTAL_CALLS = 8
FAILED_GATE_CAP = 0.48

_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "first": "1",
    "two": "2",
    "second": "2",
    "three": "3",
    "third": "3",
    "four": "4",
    "fourth": "4",
    "five": "5",
    "fifth": "5",
    "six": "6",
    "sixth": "6",
    "seven": "7",
    "seventh": "7",
    "eight": "8",
    "eighth": "8",
    "nine": "9",
    "ninth": "9",
    "ten": "10",
    "tenth": "10",
    "eleven": "11",
    "eleventh": "11",
    "twelve": "12",
    "twelfth": "12",
    "thirteen": "13",
    "thirteenth": "13",
    "fourteen": "14",
    "fourteenth": "14",
    "fifteen": "15",
    "fifteenth": "15",
    "sixteen": "16",
    "sixteenth": "16",
    "seventeen": "17",
    "seventeenth": "17",
    "eighteen": "18",
    "eighteenth": "18",
    "nineteen": "19",
    "nineteenth": "19",
    "twenty": "20",
    "twentieth": "20",
}

_IGNORED_GROUNDING_TERMS = {
    "and",
    "because",
    "buy",
    "for",
    "from",
    "item",
    "match",
    "matches",
    "product",
    "recommend",
    "recommended",
    "request",
    "that",
    "the",
    "this",
    "with",
}


def _expected_ids(item: dict[str, Any]) -> list[str]:
    reward = item.get("reward", {})
    rewards = reward if isinstance(reward, list) else [reward]
    return [str(entry["product_id"]) for entry in rewards if entry.get("product_id") is not None]


def _normalize_numbers(value: Any) -> str:
    text = normalized(value)
    text = re.sub(
        r"\b(" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\b",
        lambda match: _NUMBER_WORDS[match.group()],
        text,
    )
    return re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", r"\1", text)


def _accepted_clues(item: dict[str, Any]) -> set[str]:
    values = [item.get("knowledge_attribute", "")]
    aliases = item.get("knowledge_aliases", [])
    if isinstance(aliases, str):
        aliases = [aliases]
    values.extend(aliases)
    return {_normalize_numbers(value) for value in values if _normalize_numbers(value)}


def _contains_accepted_clue(value: str, accepted: set[str]) -> bool:
    candidate = _normalize_numbers(value)
    if not candidate:
        return False
    return any(
        re.search(rf"(?<!\w){re.escape(clue)}(?!\w)", candidate) is not None
        for clue in accepted
    )


def _product_grounding_terms(
    item: dict[str, Any],
    accepted_clues: set[str],
) -> set[str]:
    reward = item.get("reward", {})
    rewards = reward if isinstance(reward, list) else [reward]
    values: list[str] = []
    for entry in rewards:
        for key in (
            "brand",
            "category",
            "title",
            "short_description",
            "description",
            "attributes",
            "sku_options",
            "service",
        ):
            values.append(json.dumps(entry.get(key, ""), ensure_ascii=False))
    clue_tokens = {
        token
        for clue in accepted_clues
        for token in re.findall(r"[\w-]+", clue)
    }
    return {
        token
        for token in re.findall(r"[\w-]+", normalized(" ".join(values)))
        if len(token) >= 3
        and token not in _IGNORED_GROUNDING_TERMS
        and token not in clue_tokens
        and not token.isdigit()
    }


def _grounded_final_answer(
    sample: dict[str, Any],
    actual_ids: list[str],
    accepted_clues: set[str],
    grounding_terms: set[str],
) -> bool:
    text = final_answer_text(sample)
    normalized_text = normalized(text)
    if not text or not actual_ids:
        return False
    if any(
        re.search(rf"(?<!\w){re.escape(product_id)}(?!\w)", text, re.IGNORECASE) is None
        for product_id in actual_ids
    ):
        return False
    if not _contains_accepted_clue(text, accepted_clues):
        return False
    if grounding_terms and not grounding_terms.intersection(
        re.findall(r"[\w-]+", normalized_text)
    ):
        return False
    mentioned_ids = set(re.findall(r"(?<!\d)\d{8,12}(?!\d)", text))
    expected_numeric = {product_id for product_id in actual_ids if product_id.isdigit()}
    return not (mentioned_ids - expected_numeric)


def grade_with_details(sample: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    calls = tool_calls(sample)
    names = [name for name, _ in calls]
    expected = _expected_ids(item)
    actual = recommended_ids(calls)
    terminal_ids = ids(terminal_arguments(calls).get("product_ids"))
    accepted_clues = _accepted_clues(item)
    grounding_terms = _product_grounding_terms(item, accepted_clues)

    exact_product = bool(expected) and actual == expected and terminal_ids == expected
    correct_clue = _contains_accepted_clue(resolved_clue(sample), accepted_clues)
    viewed_selected = viewed_before_recommendation(calls, actual)
    grounded_final = _grounded_final_answer(
        sample,
        actual,
        accepted_clues,
        grounding_terms,
    )
    completed_protocol = (
        recommend_then_terminate(calls)
        and names.count("recommend_product") == 1
        and names.count("terminate") == 1
    )

    searches = [
        json.dumps(arguments, sort_keys=True)
        for name, arguments in calls
        if name == "find_product"
    ]
    efficient = (
        0 < len(searches) <= MAX_CATALOG_SEARCHES
        and len(calls) <= MAX_TOTAL_CALLS
        and len(searches) == len(set(searches))
    )

    score = 0.60 * float(exact_product)
    score += 0.10 * float(correct_clue)
    score += 0.05 * float(viewed_selected)
    score += 0.15 * float(grounded_final)
    score += 0.05 * float(completed_protocol)
    score += 0.05 * float(efficient)

    caps: list[float] = []
    failed_gates: list[str] = []
    if not exact_product:
        caps.append(0.20)
        failed_gates.append("exact_product")
    if not correct_clue:
        caps.append(FAILED_GATE_CAP)
        failed_gates.append("correct_clue")
    if not grounded_final:
        caps.append(FAILED_GATE_CAP)
        failed_gates.append("grounded_final_answer")
    if not completed_protocol:
        caps.append(FAILED_GATE_CAP)
        failed_gates.append("completed_protocol")
    if caps:
        score = min(score, *caps)

    return {
        "score": round(score, 6),
        "version": WEB_RFT_V5_VERSION,
        "exact_product": exact_product,
        "correct_clue": correct_clue,
        "viewed_selected": viewed_selected,
        "grounded_final_answer": grounded_final,
        "completed_protocol": completed_protocol,
        "efficient": efficient,
        "catalog_searches": len(searches),
        "total_calls": len(calls),
        "failed_gates": failed_gates,
        "applied_cap": min(caps) if caps else None,
    }


def grade(sample: dict[str, Any], item: dict[str, Any]) -> float:
    return float(grade_with_details(sample, item)["score"])


def endpoint_grade(payload: dict[str, Any]) -> dict[str, float]:
    return {"score": grade(payload.get("sample", {}), payload["item"])}
