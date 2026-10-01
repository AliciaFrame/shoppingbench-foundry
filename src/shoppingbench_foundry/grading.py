from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from .store import ProductStore


def _normalize(value: Any) -> str:
    return " ".join(str(value).casefold().split())


def _title_match(product: dict[str, Any], reward: dict[str, Any]) -> float:
    expected = reward.get("title", [])
    if isinstance(expected, str):
        expected = [expected]
    actual = _normalize(product.get("title", ""))
    return float(any(_normalize(title) == actual for title in expected)) if expected else 1.0


def _price_match(product: dict[str, Any], reward: dict[str, Any]) -> tuple[int, int]:
    hits = 0
    total = 0
    price = float(product.get("price", 0))
    for constraint in reward.get("price", []):
        for mode, bounds in constraint.items():
            lower, upper = bounds
            total += 1
            if (
                mode == "less than"
                and upper is not None
                and price <= upper
                or mode == "greater than"
                and lower is not None
                and price >= lower
                or mode == "between"
                and lower is not None
                and upper is not None
                and lower <= price <= upper
            ):
                hits += 1
    return hits, total


def _service_match(product: dict[str, Any], reward: dict[str, Any]) -> tuple[int, int]:
    expected = reward.get("service", [])
    actual = set(product.get("service", []))
    return sum(item in actual for item in expected), len(expected)


def _sku_attribute_match(product: dict[str, Any], reward: dict[str, Any]) -> tuple[int, int]:
    attributes = {
        (key, _normalize(value))
        for key, values in product.get("attributes", {}).items()
        for value in values
    }
    sku_options = product.get("sku_options", {})
    variants = list(sku_options.values()) if isinstance(sku_options, dict) else sku_options
    variants = variants or [{}]
    expected_skus = [
        (key, _normalize(value))
        for option in reward.get("sku_options", [])
        for key, value in option.items()
    ]
    expected_attributes = [
        (key, _normalize(value))
        for attribute in reward.get("attributes", [])
        for key, values in attribute.items()
        for value in values
    ]
    total = len(expected_skus) + len(expected_attributes)
    best = 0
    for variant in variants:
        variant_values = {(key, _normalize(value)) for key, value in variant.items()}
        available = attributes | variant_values
        best = max(best, sum(item in available for item in expected_skus + expected_attributes))
    return best, total


@dataclass
class ProductScore:
    ground_truth: float
    title: float
    price: float
    service: float
    sku_attributes: float
    rule: float


def score_product(product: dict[str, Any] | None, reward: dict[str, Any]) -> ProductScore:
    if not product:
        return ProductScore(0, 0, 0, 0, 0, 0)
    ground_truth = float(str(product.get("product_id")) == str(reward.get("product_id")))
    if ground_truth:
        return ProductScore(1, 1, 1, 1, 1, 1)
    title = _title_match(product, reward)
    price_hits, price_total = _price_match(product, reward)
    service_hits, service_total = _service_match(product, reward)
    sku_hits, sku_total = _sku_attribute_match(product, reward)
    dimensions = [
        (int(title), 1 if reward.get("title") else 0),
        (price_hits, price_total),
        (service_hits, service_total),
        (sku_hits, sku_total),
    ]
    hits = sum(hit for hit, _ in dimensions)
    total = sum(count for _, count in dimensions)
    return ProductScore(
        ground_truth=0,
        title=title if reward.get("title") else 1,
        price=price_hits / price_total if price_total else 1,
        service=service_hits / service_total if service_total else 1,
        sku_attributes=sku_hits / sku_total if sku_total else 1,
        rule=hits / total if total else 0,
    )


def _extract_recommendation(sample: dict[str, Any]) -> tuple[list[str], list[str], bool]:
    product_ids: list[str] = []
    tool_names: list[str] = []
    for tool in sample.get("output_tools", []) or []:
        function = tool.get("function", tool)
        name = function.get("name", "")
        tool_names.append(name)
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if name == "recommend_product":
            raw = arguments.get("product_ids", "")
            product_ids = [item.strip() for item in raw.split(",") if item.strip()]
    output_text = sample.get("output_text", "") or ""
    if not product_ids:
        match = re.search(r'"?product_ids"?\s*[:=]\s*"([^"]+)"', output_text)
        if match:
            product_ids = [item.strip() for item in match.group(1).split(",") if item.strip()]
    terminated = "terminate" in tool_names
    return product_ids, tool_names, terminated


def _average_product_scores(
    products: list[dict[str, Any]], rewards: list[dict[str, Any]]
) -> dict[str, float]:
    scores = [
        score_product(products[index] if index < len(products) else None, reward)
        for index, reward in enumerate(rewards)
    ]
    fields = ProductScore.__dataclass_fields__
    return {field: sum(getattr(score, field) for score in scores) / len(scores) for field in fields}


def _voucher_match(products: list[dict[str, Any]], voucher: dict[str, Any]) -> float:
    if not products:
        return 0
    total_price = sum(float(product.get("price", 0)) for product in products)
    if total_price <= voucher["budget"]:
        return 1
    same_shop = len({str(product.get("shop_id")) for product in products}) == 1
    if voucher["voucher_type"] == "shop" and not same_shop:
        return 0
    if total_price < voucher["threshold"]:
        return 0
    if voucher["discount_type"] == "fixed":
        discounted = total_price - voucher["face_value"]
    elif voucher["discount_type"] == "percentage":
        discounted = max(total_price * (1 - voucher["discount"]), total_price - voucher["cap"])
    else:
        return 0
    return float(discounted <= voucher["budget"])


def grade_sample(
    sample: dict[str, Any], item: dict[str, Any], store: ProductStore
) -> dict[str, Any]:
    task = item["task"]
    product_ids, _tool_names, terminated = _extract_recommendation(sample)
    products = store.get_products(product_ids)
    recommendation_score = float(bool(product_ids))
    termination_score = float(terminated)
    process_score = 0.5 * recommendation_score + 0.5 * termination_score

    if task == "web":
        reward = item["reward"]
        product = products[0] if products else None
        exact = float(
            bool(product) and str(product.get("product_id")) == str(reward.get("product_id"))
        )
        key_attribute = _normalize(item["knowledge_attribute"])
        searchable = (
            _normalize(
                " ".join([str(product.get("title", "")), str(product.get("description", ""))])
            )
            if product
            else ""
        )
        knowledge = float(key_attribute in searchable)
        score = 0.7 * max(exact, knowledge) + 0.3 * process_score
        return {
            "score": round(score, 6),
            "task": task,
            "ground_truth": exact,
            "knowledge": knowledge,
            "process": process_score,
            "product_ids": product_ids,
        }

    rewards = item["reward"] if isinstance(item["reward"], list) else [item["reward"]]
    product_metrics = _average_product_scores(products, rewards)
    task_invariant = 1.0
    if task == "shop":
        task_invariant = float(
            len(products) == len(rewards)
            and len({str(product.get("shop_id")) for product in products}) == 1
        )
    elif task == "voucher":
        task_invariant = (
            _voucher_match(products, item["voucher"]) if len(products) == len(rewards) else 0
        )
    invariant_weight = 0.25 if task in {"shop", "voucher"} else 0
    rule_weight = 0.9 - invariant_weight
    score = (
        rule_weight * product_metrics["rule"]
        + invariant_weight * task_invariant
        + 0.1 * process_score
    )
    success = product_metrics["rule"] == 1 and task_invariant == 1
    return {
        "score": round(score, 6),
        "task": task,
        "success": success,
        "process": process_score,
        "task_invariant": task_invariant,
        "product_ids": product_ids,
        "product_metrics": product_metrics,
    }


def endpoint_grade(payload: dict[str, Any], store: ProductStore) -> dict[str, float]:
    result = grade_sample(payload.get("sample", {}), payload["item"], store)
    return {"score": result["score"]}


def as_json(result: ProductScore) -> dict[str, float]:
    return asdict(result)
