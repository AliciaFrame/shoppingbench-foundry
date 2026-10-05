from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .datasets import TASK_FILES, load_task_rows
from .splits import group_disjoint_splits


class DisjointSet:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self._parent.setdefault(item, item)
        if self._parent[item] != item:
            self._parent[item] = self.find(self._parent[item])
        return self._parent[item]

    def union(self, items: list[str]) -> None:
        if not items:
            return
        root = self.find(items[0])
        for item in items[1:]:
            self._parent[self.find(item)] = root


def _reward_list(row: dict[str, Any]) -> list[dict[str, Any]]:
    reward = row["reward"]
    return reward if isinstance(reward, list) else [reward]


def _price_bounds(reward: dict[str, Any]) -> tuple[float | None, float | None]:
    lower: float | None = None
    upper: float | None = None
    price = reward.get("price", [])
    if isinstance(price, (int, float)):
        return float(price), float(price)
    for constraint in price:
        for mode, bounds in constraint.items():
            constraint_lower, constraint_upper = bounds
            if mode == "greater than" and constraint_lower is not None:
                lower = max(lower or float("-inf"), float(constraint_lower))
            elif mode == "less than" and constraint_upper is not None:
                upper = min(upper or float("inf"), float(constraint_upper))
            elif mode == "between":
                if constraint_lower is not None:
                    lower = max(lower or float("-inf"), float(constraint_lower))
                if constraint_upper is not None:
                    upper = min(upper or float("inf"), float(constraint_upper))
    return lower, upper


def _choose_price(rewards: list[dict[str, Any]]) -> float | None:
    lowers: list[float] = []
    uppers: list[float] = []
    for reward in rewards:
        lower, upper = _price_bounds(reward)
        if lower is not None:
            lowers.append(lower)
        if upper is not None:
            uppers.append(upper)
    if not lowers and not uppers:
        return None
    lower = max(lowers) if lowers else None
    upper = min(uppers) if uppers else None
    if lower is not None and upper is not None:
        return round((lower + upper) / 2, 2)
    if lower is not None:
        return round(lower + max(1, abs(lower) * 0.05), 2)
    return round(max(0.01, upper - max(1, abs(upper) * 0.05)), 2)


def _voucher_total(voucher: dict[str, Any]) -> float:
    after = float(voucher.get("price_after_voucher", voucher["budget"]))
    if voucher["discount_type"] == "fixed":
        return round(after + float(voucher["face_value"]), 2)
    discount = float(voucher["discount"])
    cap = float(voucher["cap"])
    candidates = [after / (1 - discount), after + cap]
    for total in candidates:
        discounted = max(total * (1 - discount), total - cap)
        if abs(discounted - after) < 0.02 and total >= float(voucher["threshold"]):
            return round(total, 2)
    return round(max(candidates), 2)


def _merge_attributes(target: dict[str, list[str]], reward: dict[str, Any]) -> None:
    attributes = reward.get("attributes", [])
    if isinstance(attributes, dict):
        attributes = [attributes]
    for attribute in attributes:
        for name, values in attribute.items():
            existing = target.setdefault(name, [])
            for value in values:
                if value not in existing:
                    existing.append(value)


def _merge_skus(target: dict[str, Any], reward: dict[str, Any]) -> None:
    sku_options = reward.get("sku_options")
    if not sku_options or isinstance(sku_options, dict):
        return
    variant: dict[str, Any] = {}
    for option in sku_options:
        variant.update(option)
    if any(
        all(existing.get(key) == value for key, value in variant.items())
        for existing in target.values()
    ):
        return
    numeric_keys = [int(key) for key in target if str(key).isdigit()]
    target[str(max(numeric_keys, default=0) + 1)] = variant


def _product_text(product: dict[str, Any]) -> str:
    values = [product["title"]]
    for name, items in product.get("attributes", {}).items():
        values.append(f"{name}: {', '.join(map(str, items))}")
    for variant in product.get("sku_options", {}).values():
        values.extend(f"{name}: {value}" for name, value in variant.items())
    return ". ".join(values)


def build_benchmark_subset(data_dir: Path) -> list[dict[str, Any]]:
    rows_by_task = {task: load_task_rows(data_dir, task) for task in TASK_FILES}
    occurrences: dict[str, list[tuple[str, int, int, dict[str, Any], dict[str, Any]]]] = (
        defaultdict(list)
    )
    shops = DisjointSet()

    for task, rows in rows_by_task.items():
        for row_index, row in enumerate(rows):
            rewards = _reward_list(row)
            product_ids = [str(reward["product_id"]) for reward in rewards]
            if task == "shop" or (task == "voucher" and row["voucher"]["voucher_type"] == "shop"):
                shops.union(product_ids)
            for reward_index, reward in enumerate(rewards):
                occurrences[str(reward["product_id"])].append(
                    (task, row_index, reward_index, row, reward)
                )

    component_names: dict[str, str] = {}
    prices = {
        product_id: _choose_price([item[4] for item in items])
        for product_id, items in occurrences.items()
    }

    for row_index, row in enumerate(rows_by_task["voucher"]):
        rewards = _reward_list(row)
        product_ids = [str(reward["product_id"]) for reward in rewards]
        desired_total = _voucher_total(row["voucher"])
        fixed_total = sum(prices[product_id] or 0 for product_id in product_ids)
        unpriced = [product_id for product_id in product_ids if prices[product_id] is None]
        if unpriced:
            remaining = max(len(unpriced) * 0.01, desired_total - fixed_total)
            share = round(remaining / len(unpriced), 2)
            for product_id in unpriced[:-1]:
                prices[product_id] = share
            final_product_id = unpriced[-1]
            prices[final_product_id] = round(
                desired_total
                - sum(
                    prices[product_id] or 0
                    for product_id in product_ids
                    if product_id != final_product_id
                ),
                2,
            )

    products: list[dict[str, Any]] = []
    for product_id, items in occurrences.items():
        full_reward = next(
            (reward for task, _, _, _, reward in items if task == "web"),
            None,
        )
        product = dict(full_reward) if full_reward else {}
        first_reward = items[0][4]
        title = first_reward.get("title", product.get("title", f"Product {product_id}"))
        if isinstance(title, list):
            title = title[0]
        root = shops.find(product_id)
        component_names.setdefault(root, f"benchmark-shop-{len(component_names) + 1}")
        product.update(
            {
                "product_id": product_id,
                "shop_id": product.get("shop_id") or component_names[root],
                "title": title,
                "price": float(product.get("price") or prices[product_id] or 100),
                "sold_count": int(product.get("sold_count", 0)),
                "service": list(product.get("service", [])),
                "sku_options": dict(product.get("sku_options", {})),
                "attributes": dict(product.get("attributes", {})),
            }
        )
        for task, _, _, row, reward in items:
            for service in reward.get("service", []):
                if service not in product["service"]:
                    product["service"].append(service)
            _merge_attributes(product["attributes"], reward)
            _merge_skus(product["sku_options"], reward)
            if task == "web":
                knowledge = str(row["knowledge_attribute"])
                if knowledge.casefold() not in str(product.get("description", "")).casefold():
                    product["description"] = f"{product.get('description', '')} {knowledge}".strip()
        product.setdefault("short_description", _product_text(product))
        product.setdefault("description", _product_text(product))
        products.append(product)
        products.append(_make_decoy(product, items))

    return products


def _make_decoy(
    product: dict[str, Any],
    items: list[tuple[str, int, int, dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    decoy = json.loads(json.dumps(product))
    decoy["product_id"] = f"decoy-{product['product_id']}"
    decoy["shop_id"] = f"decoy-{product['shop_id']}"
    decoy["title"] = f"Alternative {product['title']}"
    decoy["price"] = round(float(product["price"]) * 1.8 + 10, 2)
    decoy["sold_count"] = max(0, int(product.get("sold_count", 0)) - 1)

    reward = items[0][4]
    if reward.get("sku_options") and decoy.get("sku_options"):
        first_variant = next(iter(decoy["sku_options"].values()))
        first_key = next(iter(first_variant))
        first_variant[first_key] = f"alternative-{first_variant[first_key]}"
    elif reward.get("attributes") and decoy.get("attributes"):
        first_key = next(iter(decoy["attributes"]))
        decoy["attributes"][first_key] = ["alternative"]
    elif reward.get("service"):
        decoy["service"] = [item for item in decoy["service"] if item != reward["service"][0]]

    web_item = next((item for item in items if item[0] == "web"), None)
    if web_item:
        knowledge = str(web_item[3]["knowledge_attribute"])
        pattern = re.compile(re.escape(knowledge), re.IGNORECASE)
        decoy["title"] = pattern.sub("Accessory", decoy["title"])
        decoy["description"] = pattern.sub(
            "accessory",
            str(decoy.get("description", "")),
        )
    return decoy


def write_documents(products: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for product in products:
            handle.write(json.dumps({"product": product}, ensure_ascii=False) + "\n")


def _criteria(task: str, row: dict[str, Any]) -> list[dict[str, str]]:
    product_ids = [str(reward["product_id"]) for reward in _reward_list(row)]
    criteria = [
        {
            "name": "returns_required_products",
            "instruction": (
                "The JSON response must contain recommended_product_ids with "
                f"these IDs in this order: {', '.join(product_ids)}."
            ),
        },
        {
            "name": "uses_shopping_tools",
            "instruction": (
                "The tool_trace must show product search and product detail inspection "
                "before recommendation."
            ),
        },
        {
            "name": "terminates_episode",
            "instruction": "The JSON response must set terminated to true.",
        },
    ]
    if task == "shop":
        criteria.append(
            {
                "name": "same_shop",
                "instruction": "All recommended products must come from one shop.",
            }
        )
    elif task == "voucher":
        criteria.append(
            {
                "name": "voucher_budget",
                "instruction": (
                    "The selected products must satisfy the stated voucher threshold, "
                    "discount, shop restriction, and final budget."
                ),
            }
        )
    elif task == "web":
        criteria.append(
            {
                "name": "knowledge_grounding",
                "instruction": (
                    "The recommendation must match the product requested after resolving "
                    f"the knowledge clue to: {row['knowledge_attribute']}."
                ),
            }
        )
    return criteria


def write_eval_datasets(
    data_dir: Path,
    output_dir: Path,
    optimize_count: int = 20,
    holdout_count: int = 50,
    round2_optimize_count: int = 40,
) -> dict[str, dict[str, int]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, dict[str, int]] = {}
    for task in TASK_FILES:
        rows = load_task_rows(data_dir, task)
        indexes = list(range(len(rows)))
        random.Random(f"shoppingbench-{task}-v1").shuffle(indexes)
        optimize_indexes = indexes[:optimize_count]
        task_holdout_count = min(holdout_count, len(rows) - optimize_count)
        holdout_indexes = indexes[optimize_count : optimize_count + task_holdout_count]
        round2_start = optimize_count + task_holdout_count
        task_round2_count = min(
            round2_optimize_count,
            len(rows) - round2_start,
        )
        round2_indexes = indexes[round2_start : round2_start + task_round2_count]
        for split, selected in (
            ("optimize", optimize_indexes),
            ("holdout", holdout_indexes),
            ("optimize-round2", round2_indexes),
        ):
            path = output_dir / f"{task}-{split}.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for index in selected:
                    row = rows[index]
                    handle.write(
                        json.dumps(
                            {
                                "name": f"{task}-{index:03d}",
                                "query": row["query"],
                                "criteria": _criteria(task, row),
                                "item": row,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        counts[task] = {
            "optimize": len(optimize_indexes),
            "holdout": len(holdout_indexes),
            "optimize-round2": len(round2_indexes),
        }
    return counts


def write_eval_datasets_v2(
    data_dir: Path,
    output_dir: Path,
    development_count: int = 30,
    final_test_count: int = 50,
    optimize_count: int = 20,
    round2_optimize_count: int = 40,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "version": "v2",
        "policy": (
            "Connected groups sharing target product IDs, and for Web normalized knowledge "
            "answers, are assigned wholly to training-pool, development, or final-test."
        ),
        "tasks": {},
    }
    for task in TASK_FILES:
        rows = load_task_rows(data_dir, task)
        splits, task_manifest = group_disjoint_splits(
            task,
            rows,
            development_count=development_count,
            final_test_count=final_test_count,
        )
        training_indexes = list(splits["training-pool"])
        random.Random(f"shoppingbench-{task}-v2-optimizer").shuffle(training_indexes)
        derived = {
            "optimize": training_indexes[:optimize_count],
            "optimize-round2": training_indexes[
                optimize_count : optimize_count + round2_optimize_count
            ],
        }
        for split, selected in {**splits, **derived}.items():
            path = output_dir / f"{task}-{split}.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for index in selected:
                    row = rows[index]
                    handle.write(
                        json.dumps(
                            {
                                "name": f"{task}-{index:03d}",
                                "query": row["query"],
                                "criteria": _criteria(task, row),
                                "item": row,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        task_manifest["derived_training_subsets"] = {
            split: [f"{task}-{index:03d}" for index in indexes]
            for split, indexes in derived.items()
        }
        manifest["tasks"][task] = task_manifest
    manifest_path = output_dir / "split-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--data-dir", type=Path, default=repo_root / "data" / "source")
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--eval-dir", type=Path)
    parser.add_argument("--split-version", choices=("v1", "v2"), default="v1")
    args = parser.parse_args()

    products = build_benchmark_subset(args.data_dir)
    write_documents(products, args.documents)
    result: dict[str, Any] = {"documents": len(products), "output": str(args.documents)}
    if args.eval_dir:
        if args.split_version == "v2":
            manifest = write_eval_datasets_v2(args.data_dir, args.eval_dir)
            result["evals"] = {
                "version": "v2",
                "manifest": str(args.eval_dir / "split-manifest.json"),
                "counts": {
                    task: task_manifest["counts"]
                    for task, task_manifest in manifest["tasks"].items()
                },
            }
        else:
            result["evals"] = write_eval_datasets(args.data_dir, args.eval_dir)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
