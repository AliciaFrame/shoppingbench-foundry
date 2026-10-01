from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

TASK_FILES = {
    "product": "product.jsonl",
    "shop": "shop.jsonl",
    "voucher": "voucher.jsonl",
    "web": "web.jsonl",
}
EXPECTED_COUNTS = {"product": 250, "shop": 250, "voucher": 250, "web": 150}


def load_task_rows(data_dir: Path, task: str) -> list[dict[str, Any]]:
    path = data_dir / TASK_FILES[task]
    with path.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    for row in rows:
        if "query" not in row or "reward" not in row:
            raise ValueError(f"{path} contains a row without query/reward")
        row["task"] = task
        if task == "web":
            row["knowledge_attribute"] = row.pop("Knowledge_Attribute")
    return rows


def validate_public_dataset(data_dir: Path) -> dict[str, int]:
    counts = {task: len(load_task_rows(data_dir, task)) for task in TASK_FILES}
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"Unexpected public dataset counts: {counts}")
    return counts


def export_rft_dataset(data_dir: Path, output: Path, tasks: list[str]) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for task in tasks:
            for row in load_task_rows(data_dir, task):
                item = {
                    "messages": [{"role": "user", "content": row["query"]}],
                    "task": task,
                    "reward": row["reward"],
                }
                if task == "voucher":
                    item["voucher"] = row["voucher"]
                if task == "web":
                    item["knowledge_attribute"] = row["knowledge_attribute"]
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--data-dir", type=Path, default=repo_root / "data" / "source")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tasks", nargs="+", choices=TASK_FILES, default=list(TASK_FILES))
    args = parser.parse_args()
    counts = validate_public_dataset(args.data_dir)
    print(json.dumps({"validated": counts}))
    if args.output:
        count = export_rft_dataset(args.data_dir, args.output, args.tasks)
        print(json.dumps({"exported": count, "output": str(args.output)}))


if __name__ == "__main__":
    main()
