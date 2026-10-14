from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _case_name(name: str) -> str:
    return name.rsplit("-rollout-", 1)[0]


def _metric(result: dict[str, Any], name: str) -> float:
    grade = result["grade"]
    if name == "score":
        return float(grade["score"])
    if name == "exact":
        return float(grade.get("ground_truth", grade.get("exact_selection", 0)))
    if name == "constraints":
        return float(grade.get("constraint_score", grade.get("knowledge", 0)))
    if name == "viewed_selection":
        return float(grade.get("process_components", {}).get("viewed_selection", False))
    if name == "protocol":
        return float(grade.get("process_components", {}).get("completed_protocol", False))
    if name == "valid_final_answer":
        return float(grade.get("valid_final_answer", False))
    if name == "success":
        return float(grade.get("success", False))
    if name == "tokens":
        return float(result["usage"]["total_tokens"])
    if name == "latency_seconds":
        return float(result["latency_seconds"])
    raise KeyError(name)


def _mean(rows: list[dict[str, Any]], metric: str) -> float:
    return statistics.fmean(_metric(row, metric) for row in rows)


def _clustered_interval(
    baseline: dict[str, list[dict[str, Any]]],
    candidate: dict[str, list[dict[str, Any]]],
    metric: str,
    *,
    samples: int = 10_000,
    seed: int = 20261002,
) -> list[float]:
    cases = sorted(set(baseline) & set(candidate))
    deltas = [
        statistics.fmean(_metric(row, metric) for row in candidate[case])
        - statistics.fmean(_metric(row, metric) for row in baseline[case])
        for case in cases
    ]
    rng = random.Random(seed)
    bootstrapped = sorted(
        statistics.fmean(rng.choice(deltas) for _ in deltas) for _ in range(samples)
    )
    return [
        round(bootstrapped[int(samples * 0.025)], 6),
        round(bootstrapped[int(samples * 0.975)], 6),
    ]


def _group(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_case_name(row["name"])].append(row)
    return dict(grouped)


def summarize(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline = _group(baseline_rows)
    candidate = _group(candidate_rows)
    cases = sorted(set(baseline) & set(candidate))
    if set(baseline) != set(candidate):
        raise ValueError("Baseline and candidate case sets differ")
    if any(len(baseline[case]) != len(candidate[case]) for case in cases):
        raise ValueError("Baseline and candidate rollout counts differ")

    metrics = (
        "score",
        "exact",
        "constraints",
        "viewed_selection",
        "protocol",
        "valid_final_answer",
        "success",
        "tokens",
        "latency_seconds",
    )
    aggregates = {}
    for metric in metrics:
        baseline_mean = _mean(baseline_rows, metric)
        candidate_mean = _mean(candidate_rows, metric)
        aggregates[metric] = {
            "baseline": round(baseline_mean, 6),
            "candidate": round(candidate_mean, 6),
            "delta": round(candidate_mean - baseline_mean, 6),
        }
        if metric in {"score", "exact", "success"}:
            aggregates[metric]["delta_clustered_95_ci"] = _clustered_interval(
                baseline,
                candidate,
                metric,
            )

    paired = {"candidate_wins": 0, "baseline_wins": 0, "ties": 0}
    for case in cases:
        baseline_score = statistics.fmean(_metric(row, "score") for row in baseline[case])
        candidate_score = statistics.fmean(_metric(row, "score") for row in candidate[case])
        if candidate_score > baseline_score:
            paired["candidate_wins"] += 1
        elif baseline_score > candidate_score:
            paired["baseline_wins"] += 1
        else:
            paired["ties"] += 1

    def variance_summary(
        grouped: dict[str, list[dict[str, Any]]],
        metric_fn: Callable[[dict[str, Any]], float],
    ) -> dict[str, Any]:
        variances = [
            statistics.pvariance(metric_fn(row) for row in grouped[case])
            for case in cases
        ]
        return {
            "mean_case_variance": round(statistics.fmean(variances), 6),
            "unstable_cases": sum(variance > 0 for variance in variances),
        }

    return {
        "cases": len(cases),
        "rollouts_per_case": len(baseline[cases[0]]) if cases else 0,
        "metrics": aggregates,
        "paired_case_outcomes": paired,
        "rollout_variance": {
            "score": {
                "baseline": variance_summary(
                    baseline,
                    lambda row: _metric(row, "score"),
                ),
                "candidate": variance_summary(
                    candidate,
                    lambda row: _metric(row, "score"),
                ),
            },
            "exact": {
                "baseline": variance_summary(
                    baseline,
                    lambda row: _metric(row, "exact"),
                ),
                "candidate": variance_summary(
                    candidate,
                    lambda row: _metric(row, "exact"),
                ),
            },
        },
    }


def main_cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    summary = summarize(_read_jsonl(args.baseline), _read_jsonl(args.candidate))
    rendered = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main_cli()
