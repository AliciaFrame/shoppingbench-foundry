"""Evaluate the Live Web agent end to end and write per-episode receipts.

Each episode runs the fixed native web-search lookup, then the catalog agent
against the hosted ShoppingBench tool API. Receipts keep every model response's
usage, the captured web evidence, and every executed tool call so that results
can be rescored (``score_live_web.py``) and priced without rerunning inference.

The defaults below are the agent settings behind the published Web (live)
results. Every setting is recorded in the run contract written next to the
receipts, so a resumed run cannot silently mix settings. Worker count changes
only throughput and latency contention; published runs used 2-3 workers.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import logging
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

PUBLISHED_SETTINGS = {
    "MAX_TOOL_STEPS": "8",
    "SELECTION_REASONING_EFFORT": "medium",
    "SELECTION_MAX_OUTPUT_TOKENS": "4096",
    "WEB_SEARCH_MAX_OUTPUT_TOKENS": "256",
    "WEB_SEARCH_MAX_ATTEMPTS": "3",
    "WEB_SEARCH_SOURCE_LIMIT": "2",
    "CATALOG_SEARCH_RESULT_LIMIT": "10",
    "MAX_CATALOG_SEARCHES": "2",
    "MAX_PRODUCT_VIEWS": "2",
    "ENFORCE_SELECTION_TOOL_LIMITS": "false",
}
TRANSPORT_ATTEMPTS = 8
logger = logging.getLogger(__name__)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agent-mode", choices=("baseline", "optimized"), default="optimized")
    parser.add_argument("--rollouts", type=int, default=3)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if min(args.rollouts, args.workers) < 1:
        raise ValueError("Rollouts and workers must be positive")
    for name, value in PUBLISHED_SETTINGS.items():
        os.environ.setdefault(name, value)
    os.environ["AGENT_MODE"] = args.agent_mode
    os.environ.setdefault(
        "OPTIMIZATION_LOCAL_DIR", str(REPO_ROOT / "agents" / "web_search" / args.agent_mode),
    )
    if os.getenv("SHOPPINGBENCH_DOCUMENTS_PATH", "").strip():
        raise SystemExit("Unset SHOPPINGBENCH_DOCUMENTS_PATH: published runs use the hosted catalog")
    for name in ("MAI_OPENAI_BASE_URL", "MAI_MODEL_DEPLOYMENT_NAME", "WEB_SEARCH_OPENAI_BASE_URL",
                 "WEB_SEARCH_MODEL_DEPLOYMENT_NAME", "SHOPPINGBENCH_TOOL_URL",
                 "SHOPPINGBENCH_API_TOKEN"):
        if not os.getenv(name):
            raise SystemExit(f"Set {name}; see docs/reproducibility.md")

    from openai import APIConnectionError, RateLimitError

    from shoppingbench_foundry.grading import grade_sample
    from shoppingbench_foundry.store import InMemoryProductStore

    runtime = importlib.import_module("agents.shared.web_search_runtime")
    logging.basicConfig(level=logging.WARNING)
    rows = read_jsonl(args.dataset)[: args.limit]
    documents = REPO_ROOT / "data" / "prepared" / "search-documents.jsonl"
    store = InMemoryProductStore(row["product"] for row in read_jsonl(documents))
    contract = {
        "dataset_sha256": sha256(args.dataset),
        "documents_sha256": sha256(documents),
        "agent_mode": runtime.MODE,
        "instructions_sha256": sha256(runtime.CONFIG_DIR / "instructions.md"),
        "tools_sha256": sha256(runtime.CONFIG_DIR / "tools.json"),
        "runtime_sha256": sha256(REPO_ROOT / "agents" / "shared" / "web_search_runtime.py"),
        "selection_deployment": runtime.SELECTION_MODEL,
        "web_deployment": runtime.WEB_SEARCH_MODEL,
        "selection_reasoning_effort": runtime.SELECTION_REASONING_EFFORT or "provider-default",
        "selection_max_output_tokens": runtime.SELECTION_MAX_OUTPUT_TOKENS,
        "web_max_output_tokens": runtime.WEB_SEARCH_MAX_OUTPUT_TOKENS,
        "web_source_limit": runtime.WEB_SEARCH_SOURCE_LIMIT,
        "web_max_attempts": runtime.WEB_SEARCH_MAX_ATTEMPTS,
        "max_tool_steps": runtime.MAX_TOOL_STEPS,
        "enforce_selection_tool_limits": runtime.ENFORCE_SELECTION_TOOL_LIMITS,
        "catalog_search_result_limit": runtime.CATALOG_SEARCH_RESULT_LIMIT,
        "catalog_backend": "hosted-production-azure-search",
        "cases": len(rows),
        "rollouts": args.rollouts,
    }
    manifest_path = args.output.with_suffix(".manifest.json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding="utf-8"))["contract"] != contract:
            raise ValueError("Cannot resume results under a different contract")
    else:
        manifest_path.write_text(json.dumps(
            {"started_at": datetime.now(UTC).isoformat(), "contract": contract}, indent=2,
        ) + "\n", encoding="utf-8")

    state = threading.local()
    execute_tool = runtime._execute_tool

    def recorded_tool(name: str, arguments: dict[str, Any]) -> Any:
        started = time.perf_counter()
        result = execute_tool(name, arguments)
        state.tools.append({"name": name, "arguments": arguments, "output": result,
                            "latency_seconds": time.perf_counter() - started})
        return result

    runtime._execute_tool = recorded_tool

    def instrument(client: Any, stage: str) -> None:
        create = client.responses.create

        def recorded_create(**kwargs: Any) -> Any:
            started = time.perf_counter()
            for attempt in range(TRANSPORT_ATTEMPTS):
                try:
                    response = create(**kwargs)
                    break
                except (APIConnectionError, RateLimitError):
                    # No model output was returned, so waiting cannot resample an answer.
                    if attempt == TRANSPORT_ATTEMPTS - 1:
                        raise
                    delay = 15 * (attempt + 1)
                    state.retries.append({"stage": stage, "retry_delay_seconds": delay})
                    time.sleep(delay)
                    state.wait += delay
            entry = {
                "stage": stage,
                "model": response.model,
                "status": response.status,
                "latency_seconds": time.perf_counter() - started,
                "usage": response.usage.model_dump(mode="json") if response.usage else None,
                "output_types": [item.type for item in response.output],
            }
            if stage == "web":
                entry.update(
                    input=kwargs["input"], evidence=response.output_text,
                    sources=runtime._web_sources(response),
                    native_search_calls=[
                        item.model_dump(mode="json") for item in response.output
                        if item.type == "web_search_call"
                    ],
                )
            state.responses.append(entry)
            return response

        client.responses.create = recorded_create

    if runtime.selection_client is runtime.web_search_client:
        raise ValueError("Configure separate web-search and agent endpoints for accounting")
    instrument(runtime.web_search_client, "web")
    instrument(runtime.selection_client, "selection")

    def evaluate(row: dict[str, Any], rollout: int) -> dict[str, Any]:
        state.responses, state.tools, state.retries, state.wait = [], [], [], 0.0
        started = time.perf_counter()
        result: dict[str, Any] = {
            "name": f"{row['name']}-rollout-{rollout}", "case_name": row["name"],
            "case_group": row["item"].get("case_group", row["name"]), "query": row["query"],
        }
        try:
            text, _ = runtime._run_episode(row["query"])
            sample = json.loads(text)
            result.update(sample=sample, grade=grade_sample(sample, row["item"], store))
        except Exception as exc:
            # Failed episodes stay in the denominator as zeroes.
            result.update(error=f"{type(exc).__name__}: {exc}", sample={"output_tools": []},
                          grade={"score": 0.0, "ground_truth": 0.0, "success": False})
            logger.exception("Episode failed: %s", result["name"])
        # Transport backoff is not model latency.
        result["latency_seconds"] = round(time.perf_counter() - started - state.wait, 6)
        if state.retries:
            result["transport_retries"] = state.retries
        result["responses"], result["tool_results"] = state.responses, state.tools
        result["usage"] = {
            field: sum((r["usage"] or {}).get(field, 0) for r in state.responses)
            for field in ("input_tokens", "output_tokens", "total_tokens")
        }
        result["usage_complete"] = not result.get("error") and all(
            r["usage"] is not None for r in state.responses
        )
        return result

    existing = read_jsonl(args.output) if args.output.exists() else []
    done = {row["name"] for row in existing}
    results = list(existing)
    with args.output.open("a", encoding="utf-8") as handle, ThreadPoolExecutor(args.workers) as pool:
        futures = [
            pool.submit(evaluate, row, rollout)
            for row in rows for rollout in range(1, args.rollouts + 1)
            if f"{row['name']}-rollout-{rollout}" not in done
        ]
        for future in as_completed(futures):
            result = future.result()
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            handle.flush()
            results.append(result)
            print(json.dumps({"completed": len(results), "name": result["name"],
                              "score": result["grade"]["score"], "error": result.get("error")}),
                  flush=True)
    summary = {
        "episodes": len(results),
        "errors": sum(bool(row.get("error")) for row in results),
        "mean_score": statistics.mean(row["grade"]["score"] for row in results),
        "exact": sum(row["grade"].get("ground_truth", 0) for row in results),
        "mean_total_tokens": statistics.mean(row["usage"]["total_tokens"] for row in results),
        "mean_latency_seconds": statistics.mean(row["latency_seconds"] for row in results),
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
