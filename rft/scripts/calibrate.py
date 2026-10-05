from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evaluations.graders.rft_grader import grade as grade_v1
from evaluations.graders.rft_grader_v2 import grade as grade_v2
from evaluations.graders.rft_grader_v3 import grade as grade_v3


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _bootstrap_failure_interval(
    scores_by_case: dict[str, list[float]],
    threshold: float,
    samples: int = 5_000,
) -> list[float]:
    case_names = sorted(scores_by_case)
    randomizer = random.Random(20261002)
    estimates = []
    for _ in range(samples):
        selected = [case_names[randomizer.randrange(len(case_names))] for _ in case_names]
        scores = [score for name in selected for score in scores_by_case[name]]
        estimates.append(sum(score < threshold for score in scores) / len(scores))
    estimates.sort()
    return [
        round(estimates[int(0.025 * samples)], 6),
        round(estimates[int(0.975 * samples)], 6),
    ]


def calibrate(
    results_path: Path,
    dataset_path: Path,
    grader: Callable[[dict[str, Any], dict[str, Any]], float] = grade_v3,
) -> dict[str, Any]:
    items = {row["name"]: row["item"] for row in _load_jsonl(dataset_path)}
    scored = []
    scores_by_case: dict[str, list[float]] = defaultdict(list)
    for result in _load_jsonl(results_path):
        if result["name"] not in items:
            continue
        item = items[result["name"]]
        score = grader(result["sample"], item)
        scored.append(score)
        scores_by_case[str(item.get("case_name", result["name"]))].append(score)
    if not scored:
        raise ValueError("No result names matched the calibration dataset")

    candidates = sorted(set(scored + [round(index / 100, 2) for index in range(50, 101)]))
    target_failure_rate = 0.375
    viable = []
    for threshold in candidates:
        failure_rate = sum(score < threshold for score in scored) / len(scored)
        if 0.25 <= failure_rate <= 0.50:
            viable.append(
                (abs(failure_rate - target_failure_rate), -threshold, threshold, failure_rate)
            )
    selected = min(
        viable
        or [
            (
                abs(sum(score < threshold for score in scored) / len(scored) - target_failure_rate),
                -threshold,
                threshold,
                sum(score < threshold for score in scored) / len(scored),
            )
            for threshold in candidates
        ]
    )
    rollout_counts = [len(scores) for scores in scores_by_case.values()]
    failure_interval = _bootstrap_failure_interval(scores_by_case, selected[2])
    sufficient_samples = (
        len(scored) >= 60
        and len(scores_by_case) >= 20
        and min(rollout_counts, default=0) >= 3
    )
    return {
        "samples": len(scored),
        "unique_cases": len(scores_by_case),
        "rollouts_per_case": {
            "min": min(rollout_counts),
            "max": max(rollout_counts),
            "mean": round(sum(rollout_counts) / len(rollout_counts), 3),
        },
        "mean_score": round(sum(scored) / len(scored), 6),
        "recommended_pass_threshold": selected[2],
        "base_failure_rate": round(selected[3], 6),
        "base_failure_rate_bootstrap_95": failure_interval,
        "score_distribution": {str(score): scored.count(score) for score in sorted(set(scored))},
        "sufficient_samples": sufficient_samples,
        "sufficient_signal": sufficient_samples and 0.25 <= selected[3] <= 0.50,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--grader-version", choices=("v1", "v2", "v3"), default="v3")
    args = parser.parse_args()
    graders = {"v1": grade_v1, "v2": grade_v2, "v3": grade_v3}
    result = calibrate(args.results, args.dataset, graders[args.grader_version])
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
