from __future__ import annotations

import argparse
import copy
import importlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from azure.ai.agentserver.responses import ResponsesAgentServerHost
from openai import RateLimitError

from shoppingbench_foundry.grading import grade_sample
from shoppingbench_foundry.store import InMemoryProductStore

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

runtime: Any = None


def _load_runtime(module_name: str) -> Any:
    ResponsesAgentServerHost.run = lambda self: None
    return importlib.import_module(module_name)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _load_products(path: Path) -> list[dict[str, Any]]:
    return [row["product"] for row in _read_jsonl(path)]


def _expand_rollouts(
    rows: list[dict[str, Any]],
    rollouts: int,
) -> list[dict[str, Any]]:
    if rollouts == 1:
        return rows
    expanded = []
    for row in rows:
        for rollout in range(1, rollouts + 1):
            duplicate = copy.deepcopy(row)
            duplicate["name"] = f"{row['name']}-rollout-{rollout}"
            duplicate["item"]["case_name"] = row["name"]
            expanded.append(duplicate)
    return expanded


def _evaluate(
    row: dict[str, Any],
    store: InMemoryProductStore,
) -> dict[str, Any]:
    started_at = time.perf_counter()
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
        "latency_seconds": round(time.perf_counter() - started_at, 6),
    }


def main_cli() -> None:
    global runtime
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rollouts", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--record-errors-as-zero",
        action="store_true",
        help="Persist failed episodes as explicit zero-score results for calibration.",
    )
    parser.add_argument(
        "--runtime-module",
        default="agents.shared.runtime",
        help="Python module exposing _run_episode and config.",
    )
    args = parser.parse_args()
    if args.rollouts < 1:
        raise ValueError("--rollouts must be at least 1")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1")
    runtime = _load_runtime(args.runtime_module)

    rows = _read_jsonl(args.dataset)
    if args.limit is not None:
        rows = rows[: args.limit]
    rows = _expand_rollouts(rows, args.rollouts)
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

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with (
        args.output.open("a", encoding="utf-8") as checkpoint,
        ThreadPoolExecutor(max_workers=args.workers) as executor,
    ):
        futures = {
            executor.submit(_evaluate, row, store): index
            for index, row in enumerate(rows)
            if results[index] is None
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
                results[index] = result
                checkpoint.write(json.dumps(result, ensure_ascii=False) + "\n")
                checkpoint.flush()
            except Exception as exc:  # noqa: BLE001
                errors.append({"name": rows[index]["name"], "error": repr(exc)})
                if args.record_errors_as_zero:
                    result = {
                        "name": rows[index]["name"],
                        "query": rows[index]["query"],
                        "sample": {"output_tools": []},
                        "grade": {
                            "score": 0.0,
                            "success": False,
                            "valid_final_answer": False,
                        },
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                        },
                        "latency_seconds": 0.0,
                        "error": repr(exc),
                    }
                    results[index] = result
                    checkpoint.write(json.dumps(result, ensure_ascii=False) + "\n")
                    checkpoint.flush()

    with args.output.open("w", encoding="utf-8") as handle:
        for result in results:
            if result is not None:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    completed = [result for result in results if result is not None]
    scores = [float(result["grade"]["score"]) for result in completed]
    exact = [
        float(
            result["grade"].get(
                "ground_truth",
                result["grade"].get("exact_selection", 0),
            )
        )
        for result in completed
    ]
    total_tokens = [int(result["usage"]["total_tokens"]) for result in completed]
    latencies = [float(result["latency_seconds"]) for result in completed]
    print(
        json.dumps(
            {
                "config_source": runtime.config.source,
                "candidate_id": runtime.config.candidate_id,
                "cases": len(rows) // args.rollouts,
                "rollouts": args.rollouts,
                "episodes": len(rows),
                "completed": len(completed),
                "errors": errors,
                "mean_score": sum(scores) / len(scores) if scores else 0,
                "perfect": sum(score == 1 for score in scores),
                "exact": int(sum(exact)),
                "success": sum(bool(result["grade"].get("success")) for result in completed),
                "valid_final_answer": sum(
                    bool(result["grade"].get("valid_final_answer"))
                    for result in completed
                ),
                "mean_tokens": sum(total_tokens) / len(total_tokens) if total_tokens else 0,
                "mean_latency_seconds": (
                    sum(latencies) / len(latencies) if latencies else 0
                ),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    if errors and not args.record_errors_as_zero:
        raise SystemExit(1)


if __name__ == "__main__":
    main_cli()
