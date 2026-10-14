from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from azure.ai.agentserver.responses import ResponsesAgentServerHost
from httpx import HTTPError
from openai import APIConnectionError, APITimeoutError, OpenAIError, RateLimitError

from shoppingbench_foundry.artifacts import text_file_sha256
from shoppingbench_foundry.grading import grade_sample
from shoppingbench_foundry.store import INFORMATION_FIELDS, InMemoryProductStore

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

runtime: Any = None
state = threading.local()
logger = logging.getLogger(__name__)
TRANSPORT_ATTEMPTS = 6


def _load_runtime(module_name: str) -> Any:
    ResponsesAgentServerHost.run = lambda self: None
    return importlib.import_module(module_name)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _load_products(path: Path) -> list[dict[str, Any]]:
    return [row["product"] for row in _read_jsonl(path)]


def _use_in_process_tools(store: InMemoryProductStore) -> None:
    def execute(name: str, arguments: dict[str, Any]) -> Any:
        if name == "find_product":
            return store.search(
                arguments["q"],
                arguments["page"],
                arguments.get("shop_id"),
                arguments.get("price"),
                arguments.get("sort"),
                arguments.get("service"),
            )
        if name == "view_product_information":
            product_ids = [
                item.strip()
                for item in arguments.get("product_ids", "").split(",")
                if item.strip()
            ]
            return [
                {field: product.get(field) for field in INFORMATION_FIELDS}
                for product in store.get_products(product_ids)
            ]
        if name == "recommend_product":
            return {"recommended": arguments.get("product_ids", "")}
        if name == "terminate":
            return {"terminated": True, **arguments}
        raise ValueError(f"Unknown tool: {name}")

    runtime._execute_tool = execute


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


def _run_contract(args: argparse.Namespace, cases: int) -> dict[str, Any]:
    return {
        "hash_line_endings": "LF",
        "dataset_sha256": text_file_sha256(args.dataset),
        "documents_sha256": text_file_sha256(args.documents),
        "evaluator_sha256": text_file_sha256(Path(__file__)),
        "runtime_sha256": text_file_sha256(Path(runtime.__file__)),
        "grader_sha256": text_file_sha256(
            REPO_ROOT / "src" / "shoppingbench_foundry" / "grading.py",
        ),
        "store_sha256": text_file_sha256(
            REPO_ROOT / "src" / "shoppingbench_foundry" / "store.py",
        ),
        "trajectory_sha256": text_file_sha256(
            REPO_ROOT / "src" / "shoppingbench_foundry" / "trajectory.py",
        ),
        "instructions_sha256": hashlib.sha256(runtime.instructions.encode("utf-8")).hexdigest(),
        "tools_sha256": hashlib.sha256(
            json.dumps(runtime.TOOLS, sort_keys=True, ensure_ascii=False).encode("utf-8"),
        ).hexdigest(),
        "runtime_module": args.runtime_module,
        "task": runtime.TASK,
        "model_deployment": runtime.model,
        "model_base_url": str(runtime.model_client.base_url),
        "max_tool_steps": runtime.MAX_TOOL_STEPS,
        "catalog_backend": (
            "in-process" if _in_process_tools() else os.environ["SHOPPINGBENCH_TOOL_URL"].rstrip("/")
        ),
        "sdk_versions": {
            "openai": version("openai"),
            "agentserver_responses": version("azure-ai-agentserver-responses"),
        },
        "cases": cases,
        "rollouts": args.rollouts,
    }


def _in_process_tools() -> bool:
    return os.getenv("EVALUATION_IN_PROCESS_TOOLS", "").lower() in {"1", "true", "yes"}


def _prepare_resume(
    output: Path, contract: dict[str, Any], names: set[str],
) -> dict[str, dict[str, Any]]:
    manifest_path = output.with_suffix(".manifest.json")
    if output.exists() and not manifest_path.exists():
        raise ValueError("Cannot resume legacy results without a manifest; use a new output path")
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous["contract"] != contract:
            raise ValueError("Cannot resume results under a different model, config, or dataset")
    existing = _read_jsonl(output) if output.exists() else []
    existing_names = [result["name"] for result in existing]
    if len(existing_names) != len(set(existing_names)) or set(existing_names) - names:
        raise ValueError("Existing receipts contain duplicate or unexpected episode names")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not manifest_path.exists():
        manifest_path.write_text(
            json.dumps(
                {"started_at": datetime.now(UTC).isoformat(), "contract": contract}, indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    return {result["name"]: result for result in existing}


def _instrument_runtime() -> None:
    runtime.model_client = runtime.model_client.with_options(max_retries=0)
    create = runtime.model_client.responses.create

    def recorded_create(**kwargs: Any) -> Any:
        for attempt in range(TRANSPORT_ATTEMPTS):
            try:
                response = create(**kwargs)
                break
            except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
                if isinstance(exc, APIConnectionError):
                    state.uncertain_usage = True
                if attempt == TRANSPORT_ATTEMPTS - 1:
                    raise
                delay = min(60, 10 * 2**attempt)
                logger.warning("Retrying transport for %s in %ss: %s", state.name, delay, exc)
                state.retries.append({"retry_delay_seconds": delay, "error_type": type(exc).__name__})
                started = time.perf_counter()
                time.sleep(delay)
                state.wait += time.perf_counter() - started
        state.responses.append({
            "stage": "selection",
            "model": response.model,
            "usage": response.usage.model_dump(mode="json") if response.usage else None,
        })
        return response

    runtime.model_client.responses.create = recorded_create


def _evaluate(
    row: dict[str, Any],
    store: InMemoryProductStore,
) -> dict[str, Any]:
    state.name = row["name"]
    state.responses, state.retries, state.wait, state.uncertain_usage = [], [], 0.0, False
    started_at = time.perf_counter()
    result: dict[str, Any] = {
        "name": row["name"],
        "query": row["query"],
    }
    try:
        output_text, _ = runtime._run_episode(row["query"])
        sample = json.loads(output_text)
        result.update(sample=sample, grade=grade_sample(sample, row["item"], store))
    except (OpenAIError, HTTPError, RuntimeError, ValueError) as exc:
        logger.exception("Episode failed: %s", row["name"])
        state.uncertain_usage |= isinstance(exc, OpenAIError)
        result.update(
            error=f"{type(exc).__name__}: {exc}",
            sample={"output_tools": []},
            grade={"score": 0.0, "success": False, "valid_final_answer": False},
        )
    result.update(
        responses=state.responses,
        usage={
            field: sum((response["usage"] or {}).get(field, 0) for response in state.responses)
            for field in ("input_tokens", "output_tokens", "total_tokens")
        },
        usage_complete=bool(state.responses) and not state.uncertain_usage and all(
            response["usage"] is not None for response in state.responses
        ),
        latency_seconds=round(time.perf_counter() - started_at - state.wait, 6),
    )
    if state.retries:
        result["transport_retries"] = state.retries
        result["transport_backoff_seconds"] = state.wait
    return result


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
        help="Allow a zero exit status when failed episodes have been recorded as zero scores.",
    )
    parser.add_argument(
        "--runtime-module",
        default="agents.shared.runtime",
        help="Python module exposing _run_episode and config.",
    )
    args = parser.parse_args()
    if min(args.rollouts, args.workers) < 1:
        raise ValueError("--rollouts and --workers must be at least 1")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1")
    logging.basicConfig(level=logging.WARNING)
    runtime = _load_runtime(args.runtime_module)

    rows = _read_jsonl(args.dataset)
    if args.limit is not None:
        rows = rows[: args.limit]
    contract = _run_contract(args, len(rows))
    rows = _expand_rollouts(rows, args.rollouts)
    names = {row["name"] for row in rows}
    if len(names) != len(rows) or not rows:
        raise ValueError("Dataset must contain nonempty, unique episode names")
    existing = _prepare_resume(args.output, contract, names)
    store = InMemoryProductStore(_load_products(args.documents))
    if _in_process_tools():
        _use_in_process_tools(store)
    _instrument_runtime()
    results: list[dict[str, Any] | None] = [None] * len(rows)
    errors: list[dict[str, str]] = []
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
            result = future.result()
            results[index] = result
            checkpoint.write(json.dumps(result, ensure_ascii=False) + "\n")
            checkpoint.flush()
            print(json.dumps({
                "completed": sum(result is not None for result in results),
                "name": result["name"], "score": result["grade"]["score"],
                "error": result.get("error"),
            }), flush=True)

    with args.output.open("w", encoding="utf-8") as handle:
        for result in results:
            if result is not None:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    completed = [result for result in results if result is not None]
    errors = [
        {"name": result["name"], "error": result["error"]}
        for result in completed if result.get("error")
    ]
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
    sys.stdout.reconfigure(encoding="utf-8")
    main_cli()
