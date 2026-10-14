from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shoppingbench_foundry.artifacts import text_file_sha256
from shoppingbench_foundry.rft_grading import grade
from shoppingbench_foundry.web_rft_grading_v5 import grade as grade_web_v5


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
    grader: Callable[[dict[str, Any], dict[str, Any]], float] = grade,
    minimum_pass_threshold: float | None = None,
    pass_threshold: float | None = None,
) -> dict[str, Any]:
    for threshold in (minimum_pass_threshold, pass_threshold):
        if threshold is not None and (not math.isfinite(threshold) or not 0 < threshold < 1):
            raise ValueError("Pass thresholds must be finite and strictly between 0 and 1")
    dataset_rows = _load_jsonl(dataset_path)
    items = {row["name"]: row["item"] for row in dataset_rows}
    if len(items) != len(dataset_rows):
        raise ValueError("Calibration dataset contains duplicate names")
    scored = []
    seen: set[str] = set()
    error_count = 0
    scores_by_case: dict[str, list[float]] = defaultdict(list)
    scores_by_example: dict[str, list[float]] = defaultdict(list)
    for result in _load_jsonl(results_path):
        if result["name"] not in items:
            raise ValueError(f"Unexpected calibration result: {result['name']}")
        if result["name"] in seen:
            raise ValueError(f"Duplicate calibration result: {result['name']}")
        seen.add(result["name"])
        item = items[result["name"]]
        error_count += bool(result.get("error"))
        score = 0.0 if result.get("error") else grader(result["sample"], item)
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError(f"Invalid calibration score for {result['name']}: {score}")
        scored.append(score)
        scores_by_case[
            str(item.get("case_group", item.get("case_name", result["name"])))
        ].append(score)
        scores_by_example[
            str(item.get("case_name", item.get("case_group", result["name"])))
        ].append(score)
    if not scored:
        raise ValueError("No result names matched the calibration dataset")

    levels = sorted({0.0, *scored, 1.0})
    midpoints = [(left + right) / 2 for left, right in itertools.pairwise(levels)]
    candidates = sorted(
        (set(midpoints) | {index / 100 for index in range(1, 100)})
        - set(scored)
    )
    candidates = [threshold for threshold in candidates if 0 < threshold < 1]
    if minimum_pass_threshold is not None:
        candidates = [
            threshold
            for threshold in candidates
            if threshold >= minimum_pass_threshold
        ]
    if pass_threshold is not None:
        if pass_threshold in scored:
            raise ValueError("Choose a pass threshold between observed scores, not on a score")
        if minimum_pass_threshold is not None and pass_threshold < minimum_pass_threshold:
            raise ValueError("Pass threshold is below the required quality minimum")
        candidates = sorted({*candidates, pass_threshold})
    if not candidates:
        raise ValueError("No comparator-independent threshold satisfies the quality minimum")
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
    if pass_threshold is not None:
        selected = (
            0.0, -pass_threshold, pass_threshold,
            sum(score < pass_threshold for score in scored) / len(scored),
        )
    rollout_counts = [len(scores) for scores in scores_by_case.values()]
    failure_interval = _bootstrap_failure_interval(scores_by_case, selected[2])
    sufficient_samples = (
        len(scored) >= 60
        and len(scores_by_case) >= 20
        and min(rollout_counts, default=0) >= 3
    )
    complete_coverage = seen == items.keys()
    varying_examples = sum(len(set(scores)) > 1 for scores in scores_by_example.values())
    in_target_band = 0.25 <= selected[3] <= 0.50
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
        "threshold_selection": "fixed_quality_boundary" if pass_threshold is not None else "sweep",
        "threshold_comparator": "Local >=; selected cutoff gives identical > and >= counts",
        "observations_equal_to_threshold": scored.count(selected[2]),
        "base_failure_rate": round(selected[3], 6),
        "base_failure_rate_bootstrap_95": failure_interval,
        "score_distribution": {str(score): scored.count(score) for score in sorted(set(scored))},
        "threshold_sweep": [
            {
                "threshold": threshold,
                "passes": sum(score >= threshold for score in scored),
                "failures": sum(score < threshold for score in scored),
                "failure_rate": round(sum(score < threshold for score in scored) / len(scored), 6),
            }
            for threshold in candidates
        ],
        "complete_coverage": complete_coverage,
        "missing_results": sorted(items.keys() - seen),
        "runtime_errors": error_count,
        "hash_line_endings": "LF",
        "dataset_sha256": text_file_sha256(dataset_path),
        "results_sha256": text_file_sha256(results_path),
        "sufficient_samples": sufficient_samples,
        "failure_rate_in_target_band": in_target_band,
        "reward_variation": {
            "examples": len(scores_by_example),
            "varying_examples": varying_examples,
            "mean_within_example_variance": statistics.mean(
                statistics.pvariance(scores) for scores in scores_by_example.values()
            ),
        },
        "sufficient_signal": (
            sufficient_samples and complete_coverage and not error_count
            and varying_examples > 0
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--grader-version",
        choices=("v3", "web-v5"),
        default="v3",
    )
    args = parser.parse_args()
    graders = {
        "v3": grade,
        "web-v5": grade_web_v5,
    }
    grader = graders[args.grader_version]
    minimum_thresholds = {"web-v5": 0.49}
    result = calibrate(
        args.results,
        args.dataset,
        grader=grader,
        minimum_pass_threshold=minimum_thresholds.get(args.grader_version),
    )
    result["grader_version"] = args.grader_version
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
