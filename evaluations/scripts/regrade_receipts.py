from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shoppingbench_foundry.grading import grade_sample
from shoppingbench_foundry.store import InMemoryProductStore


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _load_store(path: Path) -> InMemoryProductStore:
    return InMemoryProductStore([row["product"] for row in _read_jsonl(path)])


def _case_name(name: str) -> str:
    return name.rsplit("-rollout-", 1)[0]


def regraded_rows(
    receipt: Path,
    dataset: Path,
    store: InMemoryProductStore,
) -> list[dict[str, Any]]:
    items = {row["name"]: row["item"] for row in _read_jsonl(dataset)}
    results = _read_jsonl(receipt)
    regraded = []
    for result in results:
        item = items.get(result["name"]) or items.get(_case_name(result["name"]))
        if item is None:
            continue
        updated = dict(result)
        updated["grade"] = grade_sample(result["sample"], item, store)
        regraded.append(updated)
    return regraded


def regrade(
    receipt: Path,
    dataset: Path,
    store: InMemoryProductStore,
) -> dict[str, Any]:
    grades = [result["grade"] for result in regraded_rows(receipt, dataset, store)]
    exact = [
        float(grade.get("ground_truth", grade.get("exact_selection", 0)))
        for grade in grades
    ]
    return {
        "receipt": str(receipt),
        "cases": len(grades),
        "mean_score": round(sum(grade["score"] for grade in grades) / len(grades), 6),
        "exact": int(sum(exact)),
        "success": sum(bool(grade.get("success")) for grade in grades),
        "valid_final_answer": sum(bool(grade.get("valid_final_answer")) for grade in grades),
        "viewed_selection": sum(
            bool(grade.get("process_components", {}).get("viewed_selection"))
            for grade in grades
        ),
        "completed_protocol": sum(
            bool(grade.get("process_components", {}).get("completed_protocol"))
            for grade in grades
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--receipt",
        action="append",
        required=True,
        help="LABEL=PATH; may be specified more than once",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--regraded-dir", type=Path)
    args = parser.parse_args()
    store = _load_store(args.documents)
    summaries = {}
    for value in args.receipt:
        label, separator, raw_path = value.partition("=")
        if not separator:
            raise ValueError("--receipt must use LABEL=PATH")
        receipt = Path(raw_path)
        rows = regraded_rows(receipt, args.dataset, store)
        summaries[label] = regrade(receipt, args.dataset, store)
        if args.regraded_dir:
            args.regraded_dir.mkdir(parents=True, exist_ok=True)
            output_path = args.regraded_dir / f"{label}.jsonl"
            with output_path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    rendered = json.dumps(summaries, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
