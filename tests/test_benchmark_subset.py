import json
from pathlib import Path

from shoppingbench_foundry.benchmark_subset import (
    build_benchmark_subset,
    write_eval_datasets,
    write_eval_datasets_v2,
)
from shoppingbench_foundry.datasets import TASK_FILES, load_task_rows
from shoppingbench_foundry.grading import grade_sample
from shoppingbench_foundry.store import InMemoryProductStore
from shoppingbench_foundry.trajectory import expected_voucher_subtotal

DATA_DIR = Path(__file__).parents[1] / "data" / "source"


def test_generated_gold_products_pass_all_frozen_tasks():
    products = build_benchmark_subset(DATA_DIR)
    store = InMemoryProductStore(products)

    for task in TASK_FILES:
        for row in load_task_rows(DATA_DIR, task):
            rewards = row["reward"] if isinstance(row["reward"], list) else [row["reward"]]
            product_ids = ",".join(str(reward["product_id"]) for reward in rewards)
            search_query = (
                row["knowledge_attribute"]
                if task == "web"
                else row["query"]
            )
            if task == "web":
                assistant_text = (
                    f"The clue resolves to {row['knowledge_attribute']}; "
                    f"I recommend {product_ids}."
                )
            elif task == "voucher":
                voucher = row["voucher"]
                subtotal = expected_voucher_subtotal(voucher)
                shop_text = " same shop" if voucher["voucher_type"] == "shop" else ""
                assistant_text = (
                    f"I recommend {product_ids}.{shop_text} The subtotal is {subtotal} and satisfies the "
                    f"{voucher['threshold']} threshold, and the voucher discount gives "
                    f"a final payable of {voucher['price_after_voucher']} within budget."
                )
            else:
                assistant_text = f"I recommend {product_ids} because it matches the request."
            sample = {
                "output_tools": [
                    {
                        "function": {
                            "name": "find_product",
                            "arguments": {"q": search_query, "page": 1},
                        }
                    },
                    {
                        "function": {
                            "name": "view_product_information",
                            "arguments": {"product_ids": product_ids},
                        }
                    },
                    {
                        "function": {
                            "name": "recommend_product",
                            "arguments": {"product_ids": product_ids},
                        }
                    },
                    {"function": {"name": "terminate", "arguments": {}}},
                ],
                "assistant_text": assistant_text,
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


def test_v2_splits_are_group_disjoint(tmp_path):
    manifest = write_eval_datasets_v2(DATA_DIR, tmp_path)

    for task in TASK_FILES:
        task_manifest = manifest["tasks"][task]
        assert all(not values for values in task_manifest["overlap"].values())
        split_groups = {
            split: {
                task_manifest["case_groups"][name]
                for name in task_manifest["cases"][split]
            }
            for split in ("training-pool", "development", "final-test")
        }
        assert split_groups["training-pool"].isdisjoint(split_groups["development"])
        assert split_groups["training-pool"].isdisjoint(split_groups["final-test"])
        assert split_groups["development"].isdisjoint(split_groups["final-test"])

    web_manifest = manifest["tasks"]["web"]
    assert web_manifest["counts"]["final-test"] >= 50
    assert web_manifest["counts"]["development"] >= 30
