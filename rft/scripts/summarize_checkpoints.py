from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction - 1e-9)))
    return ordered[index]


def _tool_count(row: dict[str, Any], name: str) -> int:
    return sum(
        tool.get("function", tool).get("name") == name
        for tool in row["sample"].get("output_tools", [])
    )


def _ground_truth_score(row: dict[str, Any]) -> float:
    grade = row["grade"]
    return float(
        grade.get(
            "ground_truth",
            grade.get("product_metrics", {}).get("ground_truth", 0),
        )
    )


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [float(row["grade"]["score"]) for row in rows]
    searches = [_tool_count(row, "find_product") for row in rows]
    views = [_tool_count(row, "view_product_information") for row in rows]
    total_tokens = [
        int(row["usage"]["total_tokens"])
        for row in rows
        if row.get("usage", {}).get("total_tokens") is not None
    ]
    latencies = [
        float(row["latency_seconds"])
        for row in rows
        if row.get("latency_seconds") is not None
    ]
    exact = sum(_ground_truth_score(row) == 1 for row in rows)
    return {
        "cases": len(rows),
        "mean_score": round(statistics.fmean(scores), 6),
        "perfect": sum(score == 1 for score in scores),
        "exact_product": exact,
        "terminated": sum(bool(row["sample"].get("terminated")) for row in rows),
        "mean_searches": round(statistics.fmean(searches), 6),
        "mean_views": round(statistics.fmean(views), 6),
        "mean_total_tokens": (
            round(statistics.fmean(total_tokens), 6) if total_tokens else None
        ),
        "median_total_tokens": statistics.median(total_tokens) if total_tokens else None,
        "mean_latency_seconds": (
            round(statistics.fmean(latencies), 6) if latencies else None
        ),
        "median_latency_seconds": statistics.median(latencies) if latencies else None,
        "p95_latency_seconds": _percentile(latencies, 0.95),
    }


def _compare(
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    wins: list[str] = []
    ties: list[str] = []
    losses: list[str] = []
    for name in sorted(baseline):
        delta = float(candidate[name]["grade"]["score"]) - float(
            baseline[name]["grade"]["score"]
        )
        if delta > 0:
            wins.append(name)
        elif delta < 0:
            losses.append(name)
        else:
            ties.append(name)
    return {
        "wins": len(wins),
        "ties": len(ties),
        "losses": len(losses),
        "improved_cases": wins,
        "regressed_cases": losses,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="web")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        action="append",
        nargs=2,
        metavar=("LABEL", "PATH"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline_rows = _read_jsonl(args.baseline)
    baseline = {row["name"]: row for row in baseline_rows}
    candidates: dict[str, Any] = {}
    for label, value in args.candidate:
        rows = _read_jsonl(Path(value))
        by_name = {row["name"]: row for row in rows}
        if set(by_name) != set(baseline):
            raise ValueError(f"{label} cases do not match the baseline")
        candidates[label] = {
            "metrics": _metrics(rows),
            "versus_baseline": _compare(baseline, by_name),
        }

    result = {
        "task": args.task,
        "holdout_cases": len(baseline),
        "baseline": _metrics(baseline_rows),
        "candidates": candidates,
        "selected_checkpoint": max(
            candidates,
            key=lambda label: candidates[label]["metrics"]["mean_score"],
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
