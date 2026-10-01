import json
from pathlib import Path

from shoppingbench_foundry.benchmark_subset import (
    build_benchmark_subset,
    write_eval_datasets,
)
from shoppingbench_foundry.datasets import TASK_FILES, load_task_rows
from shoppingbench_foundry.grading import grade_sample
from shoppingbench_foundry.store import InMemoryProductStore

DATA_DIR = Path(__file__).parents[1] / "data" / "source"


def test_generated_gold_products_pass_all_frozen_tasks():
    products = build_benchmark_subset(DATA_DIR)
    store = InMemoryProductStore(products)

    for task in TASK_FILES:
        for row in load_task_rows(DATA_DIR, task):
            rewards = row["reward"] if isinstance(row["reward"], list) else [row["reward"]]
            product_ids = ",".join(str(reward["product_id"]) for reward in rewards)
            sample = {
                "output_tools": [
                    {
                        "function": {
                            "name": "recommend_product",
                            "arguments": {"product_ids": product_ids},
                        }
                    },
                    {"function": {"name": "terminate", "arguments": {}}},
                ]
            }
            result = grade_sample(sample, row, store)
            assert result["score"] == 1, (task, row["query"], result)


def test_generated_subset_includes_one_decoy_per_gold_product():
    products = build_benchmark_subset(DATA_DIR)
    gold = [product for product in products if not str(product["product_id"]).startswith("decoy-")]
    decoys = [product for product in products if str(product["product_id"]).startswith("decoy-")]

    assert len(gold) == len(decoys)
    assert len(gold) > 1_800


def test_round2_optimization_split_is_disjoint(tmp_path):
    counts = write_eval_datasets(DATA_DIR, tmp_path)

    for task in TASK_FILES:
        splits = {}
        for split in ("optimize", "holdout", "optimize-round2"):
            path = tmp_path / f"{task}-{split}.jsonl"
            with path.open(encoding="utf-8") as handle:
                splits[split] = {json.loads(line)["name"] for line in handle if line.strip()}

        assert counts[task]["optimize"] == 20
        assert counts[task]["holdout"] == 50
        assert counts[task]["optimize-round2"] == 40
        assert splits["optimize"].isdisjoint(splits["holdout"])
        assert splits["optimize"].isdisjoint(splits["optimize-round2"])
        assert splits["holdout"].isdisjoint(splits["optimize-round2"])
