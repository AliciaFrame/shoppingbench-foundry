"""Score Live Web receipts with the published RFT grader and summarize them.

``evaluate_live_web.py`` records the fixed web-search lookup as the first
trajectory event. The RFT grader scores only the catalog agent, so each
receipt is adapted by removing exactly that verified lookup and binding the
evidence the agent actually received as trusted context. Runtime errors score
zero. Optionally compares against another scored run with case-clustered
95% confidence intervals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from openai import APIConnectionError, RateLimitError

from evals.scripts.summarize_development import _clustered_interval, _group
from shoppingbench_foundry import web_search_rft_grading_v6 as grader
from shoppingbench_foundry.trajectory import tool_calls


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def adapt(result: dict[str, Any], row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sample = result["sample"]
    calls = tool_calls(sample)
    if not calls or calls[0] != ("web_search", {"query": result["query"]}):
        raise ValueError(f"No web-search lookup event: {result['name']}")
    executed = [(call["name"], call["arguments"]) for call in result["tool_results"]]
    if calls[1:] != executed:
        raise ValueError(f"Trajectory differs from executed tool receipts: {result['name']}")
    item = {
        **row["item"], "case_name": row["name"], "case_group": result["case_group"],
        "live_web_context": {"evidence": sample["web_evidence"], "sources": sample["web_sources"]},
    }
    return {**sample, "output_tools": sample["output_tools"][1:]}, item


def stage_usage(result: dict[str, Any], stage: str, field: str) -> int:
    return sum(
        (response["usage"] or {}).get(field, 0)
        for response in result["responses"] if response["stage"] == stage
    )


def score(results_path: Path, dataset_path: Path) -> list[dict[str, Any]]:
    rows = {row["name"]: row for row in read_jsonl(dataset_path)}
    scored = []
    for result in read_jsonl(results_path):
        if result.get("error"):
            details = {"score": 0.0, "quality_pass": False, "failed_gates": ["runtime_error"]}
        else:
            sample, item = adapt(result, rows[result["case_name"]])
            for attempt in range(8):
                try:
                    details = grader.grade_with_details(sample, item)
                    break
                except (APIConnectionError, RateLimitError):
                    if attempt == 7:
                        raise
                    time.sleep(15 * (attempt + 1))
        scored.append({
            "name": result["name"], "case_name": result["case_name"],
            "canonical": result["grade"], "details": details, "error": result.get("error"),
            "latency_seconds": result["latency_seconds"],
            "catalog_calls": len(result["tool_results"]),
            "usage": {
                stage: {field: stage_usage(result, stage, field)
                        for field in ("input_tokens", "output_tokens")}
                for stage in ("selection", "web")
            },
        })
    return scored


def summarize(scored: list[dict[str, Any]], results_sha256: str | None = None) -> dict[str, Any]:
    def mean(get: Any) -> float:
        return statistics.mean(get(row) for row in scored)

    return {
        "grader_sha256": grader.grader_sha256(),
        "results_sha256": results_sha256,
        "episodes": len(scored),
        "rft_reward": mean(lambda row: row["details"]["score"]),
        "rft_passes": sum(row["details"]["score"] > grader.PASS_THRESHOLD for row in scored),
        "quality_passes": sum(row["details"]["quality_pass"] for row in scored),
        "canonical_score": mean(lambda row: row["canonical"]["score"]),
        "exact": sum(row["canonical"].get("ground_truth", 0) for row in scored),
        "mean_latency_seconds": mean(lambda row: row["latency_seconds"]),
        "mean_catalog_calls": mean(lambda row: row["catalog_calls"]),
        "mean_tokens": {
            f"{stage}_{field}": mean(lambda row, s=stage, f=field: row["usage"][s][f])
            for stage in ("selection", "web") for field in ("input_tokens", "output_tokens")
        },
        "runtime_errors": sum(bool(row["error"]) for row in scored),
    }


def compare(candidate: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for name, get in (
        ("rft_reward", lambda row: row["details"]["score"]),
        ("canonical_score", lambda row: row["canonical"]["score"]),
    ):
        left = [{"name": row["name"], "grade": {"score": get(row)}} for row in baseline]
        right = [{"name": row["name"], "grade": {"score": get(row)}} for row in candidate]
        out[name] = {
            "delta": statistics.mean(map(get, candidate)) - statistics.mean(map(get, baseline)),
            "case_clustered_95_ci": _clustered_interval(_group(left), _group(right), "score"),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Scored details JSONL")
    parser.add_argument("--baseline-details", type=Path, help="Scored details to compare against")
    args = parser.parse_args()
    results_sha256 = hashlib.sha256(args.results.read_bytes()).hexdigest()
    summary_path = args.output.with_suffix(".summary.json")
    previous = (
        json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    )
    # Reuse scores (and their judge calls) only for the same receipts and grader.
    reusable = args.output.exists() and previous.get("results_sha256") == results_sha256 and (
        previous.get("grader_sha256") == grader.grader_sha256()
    )
    scored = read_jsonl(args.output) if reusable else score(args.results, args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in scored), encoding="utf-8",
    )
    report = summarize(scored, results_sha256)
    if args.baseline_details:
        report["paired_vs_baseline"] = {
            "baseline": args.baseline_details.stem,
            **compare(scored, read_jsonl(args.baseline_details)),
        }
    summary_path.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
