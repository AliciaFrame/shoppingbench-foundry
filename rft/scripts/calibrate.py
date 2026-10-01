from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluations.graders.rft_grader import grade


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def calibrate(results_path: Path, dataset_path: Path) -> dict[str, Any]:
    items = {row["name"]: row["item"] for row in _load_jsonl(dataset_path)}
    scored = []
    for result in _load_jsonl(results_path):
        if result["name"] not in items:
            continue
        scored.append(grade(result["sample"], items[result["name"]]))
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
    return {
        "samples": len(scored),
        "mean_score": round(sum(scored) / len(scored), 6),
        "recommended_pass_threshold": selected[2],
        "base_failure_rate": round(selected[3], 6),
        "score_distribution": {str(score): scored.count(score) for score in sorted(set(scored))},
        "sufficient_signal": 0.25 <= selected[3] <= 0.50,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = calibrate(args.results, args.dataset)
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
