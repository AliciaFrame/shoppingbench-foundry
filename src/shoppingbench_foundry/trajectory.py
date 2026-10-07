from __future__ import annotations

import json
import re
from typing import Any


def normalized(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def ids(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def tool_calls(sample: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
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


def recommended_ids(calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    recommendations = [
        arguments.get("product_ids", "")
        for name, arguments in calls
        if name == "recommend_product"
    ]
    return ids(recommendations[-1]) if recommendations else []


def terminal_arguments(calls: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    terminations = [arguments for name, arguments in calls if name == "terminate"]
    return terminations[-1] if terminations else {}


def recommendation_index(calls: list[tuple[str, dict[str, Any]]]) -> int | None:
    return next(
        (index for index, (name, _) in enumerate(calls) if name == "recommend_product"),
        None,
    )


def viewed_before_recommendation(
    calls: list[tuple[str, dict[str, Any]]],
    actual: list[str],
) -> bool:
    index = recommendation_index(calls)
    if index is None:
        return False
    viewed = {
        product_id
        for name, arguments in calls[:index]
        if name == "view_product_information"
        for product_id in ids(arguments.get("product_ids"))
    }
    return bool(actual) and set(actual).issubset(viewed)


def recommend_then_terminate(calls: list[tuple[str, dict[str, Any]]]) -> bool:
    index = recommendation_index(calls)
    return (
        index is not None
        and index + 1 == len(calls) - 1
        and calls[-1][0] == "terminate"
    )


def efficient_searches(calls: list[tuple[str, dict[str, Any]]], limit: int) -> bool:
    searches = [
        json.dumps(arguments, sort_keys=True)
        for name, arguments in calls
        if name == "find_product"
    ]
    return 0 < len(searches) <= limit and len(searches) == len(set(searches))


def search_queries_before_recommendation(
    calls: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    index = recommendation_index(calls)
    prefix = calls[:index] if index is not None else calls
    return [
        normalized(arguments.get("q"))
        for name, arguments in prefix
        if name == "find_product"
    ]


def assistant_text(sample: dict[str, Any]) -> str:
    for key in ("assistant_text", "output_text", "text"):
        value = sample.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolved_clue(sample: dict[str, Any]) -> str:
    calls = tool_calls(sample)
    value = terminal_arguments(calls).get("resolved_clue")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def final_answer_text(sample: dict[str, Any]) -> str:
    calls = tool_calls(sample)
    value = terminal_arguments(calls).get("final_answer")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return assistant_text(sample)


def contains_amount(text: str, amount: Any) -> bool:
    try:
        expected = float(amount)
    except (TypeError, ValueError):
        return False
    values = [
        float(match.replace(",", ""))
        for match in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", text)
    ]
    return any(abs(value - expected) < 0.01 for value in values)


def expected_voucher_subtotal(voucher: dict[str, Any]) -> float | None:
    try:
        final = float(voucher["price_after_voucher"])
        threshold = float(voucher["threshold"])
    except (KeyError, TypeError, ValueError):
        return None
    if voucher.get("discount_type") == "fixed":
        return round(final + float(voucher["face_value"]), 2)
    if voucher.get("discount_type") == "percentage":
        discount = float(voucher["discount"])
        cap = float(voucher["cap"])
        for subtotal in (final / (1 - discount), final + cap):
            discounted = max(subtotal * (1 - discount), subtotal - cap)
            if abs(discounted - final) < 0.02 and subtotal >= threshold:
                return round(subtotal, 2)
    return None


def reward_search_terms(item: dict[str, Any]) -> set[str]:
    reward = item.get("reward", {})
    rewards = reward if isinstance(reward, list) else [reward]
    values: list[str] = []
    for entry in rewards:
        for key in ("title", "attributes", "sku_options", "service"):
            values.append(json.dumps(entry.get(key, ""), ensure_ascii=False))
    query = item.get("query")
    if not query:
        query = next(
            (
                message.get("content", "")
                for message in reversed(item.get("messages", []))
                if message.get("role") == "user"
            ),
            "",
        )
    values.append(str(query))
    if item.get("task") == "web":
        values.append(str(item.get("knowledge_attribute", "")))
    ignored = {
        "and",
        "the",
        "with",
        "for",
        "from",
        "that",
        "this",
        "product",
        "item",
        "none",
        "null",
    }
    return {
        token
        for token in re.findall(r"[\w-]+", normalized(" ".join(values)))
        if len(token) >= 3 and token not in ignored
    }


def meaningful_search(calls: list[tuple[str, dict[str, Any]]], item: dict[str, Any]) -> bool:
    terms = reward_search_terms(item)
    queries = search_queries_before_recommendation(calls)
    if item.get("task") == "web":
        knowledge = normalized(item.get("knowledge_attribute"))
        if knowledge and any(knowledge in query for query in queries):
            return True
    if not terms:
        return any(len(query) >= 3 for query in queries)
    return any(
        terms.intersection(re.findall(r"[\w-]+", query))
        for query in queries
    )


def grounded_final_answer(
    sample: dict[str, Any],
    item: dict[str, Any],
    actual: list[str],
) -> bool:
    text = final_answer_text(sample)
    if not text:
        return False
    normalized_text = normalized(text)
    if not actual or any(
        not re.search(rf"(?<!\w){re.escape(product_id)}(?!\w)", text, re.IGNORECASE)
        for product_id in actual
    ):
        return False
    numeric_id_matches = re.finditer(r"(?<!\d)\d{8,12}(?!\d)", text)
    mentioned_numeric_ids = {
        match.group()
        for match in numeric_id_matches
        if not (
            match.start() >= 2
            and text[match.start() - 1] == "."
            and text[match.start() - 2].isdigit()
        )
        and not (
            match.end() + 1 < len(text)
            and text[match.end()] == "."
            and text[match.end() + 1].isdigit()
        )
    }
    actual_numeric_ids = {product_id for product_id in actual if product_id.isdigit()}
    if mentioned_numeric_ids - actual_numeric_ids:
        return False
    task = item.get("task")
    if task == "web":
        knowledge = normalized(item.get("knowledge_attribute"))
        return bool(knowledge and knowledge in normalized_text)
    if task == "voucher":
        voucher = item.get("voucher", {})
        expected_final = voucher.get("price_after_voucher")
        expected_subtotal = expected_voucher_subtotal(voucher)
        required_words = {"budget", "threshold"}
        required_words.add("voucher" if "voucher" in normalized_text else "discount")
        if voucher.get("voucher_type") == "shop":
            required_words.add("shop")
        return (
            expected_final is not None
            and expected_subtotal is not None
            and contains_amount(text, expected_subtotal)
            and contains_amount(text, expected_final)
            and all(word in normalized_text for word in required_words)
        )
    return True
