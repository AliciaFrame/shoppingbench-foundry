from __future__ import annotations

import argparse
import json
from pathlib import Path

from shoppingbench_foundry.benchmark_subset import (
    build_benchmark_subset,
    write_documents,
    write_eval_datasets,
)
from shoppingbench_foundry.datasets import validate_public_dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--source", type=Path, default=repo_root / "data" / "source")
    parser.add_argument(
        "--documents",
        type=Path,
        default=repo_root / "data" / "prepared" / "search-documents.jsonl",
    )
    parser.add_argument(
        "--evals",
        type=Path,
        default=repo_root / "evaluations" / "datasets",
    )
    args = parser.parse_args()

    counts = validate_public_dataset(args.source)
    products = build_benchmark_subset(args.source)
    write_documents(products, args.documents)
    splits = write_eval_datasets(args.source, args.evals)
    print(
        json.dumps(
            {
                "source_cases": counts,
                "search_documents": len(products),
                "evaluation_splits": splits,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
