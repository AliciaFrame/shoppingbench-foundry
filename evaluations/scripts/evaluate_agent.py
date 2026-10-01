from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from azure.ai.agentserver.responses import ResponsesAgentServerHost
from openai import RateLimitError

from shoppingbench_foundry.grading import grade_sample
from shoppingbench_foundry.store import InMemoryProductStore

ResponsesAgentServerHost.run = lambda self: None

from agents.shared import runtime


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _load_products(path: Path) -> list[dict[str, Any]]:
    return [row["product"] for row in _read_jsonl(path)]


def _evaluate(
    row: dict[str, Any],
    store: InMemoryProductStore,
) -> dict[str, Any]:
    for attempt in range(6):
        try:
            output_text, usage = runtime._run_episode(row["query"])
            break
        except RateLimitError:
            if attempt == 5:
                raise
            time.sleep(min(60, 10 * 2**attempt))
    sample = json.loads(output_text)
    grade = grade_sample(sample, row["item"], store)
    return {
        "name": row["name"],
        "query": row["query"],
        "sample": sample,
        "grade": grade,
        "usage": usage,
    }


def main_cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    rows = _read_jsonl(args.dataset)
    store = InMemoryProductStore(_load_products(args.documents))
    results: list[dict[str, Any] | None] = [None] * len(rows)
    errors: list[dict[str, str]] = []
    existing = (
        {result["name"]: result for result in _read_jsonl(args.output)}
        if args.output.is_file()
        else {}
    )
    for index, row in enumerate(rows):
        results[index] = existing.get(row["name"])

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_evaluate, row, store): index
            for index, row in enumerate(rows)
            if results[index] is None
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                errors.append({"name": rows[index]["name"], "error": repr(exc)})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for result in results:
            if result is not None:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    completed = [result for result in results if result is not None]
    scores = [float(result["grade"]["score"]) for result in completed]
    print(
        json.dumps(
            {
                "config_source": runtime.config.source,
                "candidate_id": runtime.config.candidate_id,
                "cases": len(rows),
                "completed": len(completed),
                "errors": errors,
                "mean_score": sum(scores) / len(scores) if scores else 0,
                "perfect": sum(score == 1 for score in scores),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main_cli()
