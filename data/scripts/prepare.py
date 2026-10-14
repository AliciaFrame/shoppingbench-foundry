from __future__ import annotations

import argparse
import json
from pathlib import Path

from shoppingbench_foundry.benchmark_subset import (
    build_benchmark_subset,
    write_documents,
    write_lifecycle_datasets,
)
from shoppingbench_foundry.datasets import validate_public_dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--source", type=Path, default=repo_root / "data" / "source")
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--evals", type=Path)
    args = parser.parse_args()
    documents = args.documents or repo_root / "data" / "prepared" / "search-documents.jsonl"
    evals = args.evals or repo_root / "evals" / "datasets"

    counts = validate_public_dataset(args.source)
    products = build_benchmark_subset(args.source)
    write_documents(products, documents)
    manifest = write_lifecycle_datasets(args.source, evals)
    splits = {
        task: task_manifest["counts"]
        for task, task_manifest in manifest["tasks"].items()
    }
    print(
        json.dumps(
            {
                "source_cases": counts,
                "search_documents": len(products),
                "split_strategy": manifest["split_strategy"],
                "documents": str(documents),
                "evals": str(evals),
                "evaluation_splits": splits,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
