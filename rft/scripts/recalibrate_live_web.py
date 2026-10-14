from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from openai import OpenAIError, RateLimitError

from rft.scripts.calibrate import calibrate
from rft.scripts.prepare_live_web_data import read_jsonl, sha256
from shoppingbench_foundry import web_search_rft_grading_v6 as grader
from shoppingbench_foundry.web_search import _retry_delay
from shoppingbench_foundry.web_search_rft_grading_v3 import grade as previous_grade

logger = logging.getLogger(__name__)


def recalibrate(
    results_path: Path, dataset_path: Path, output_dir: Path, workers: int = 2,
) -> dict[str, Any]:
    grader_version = "web-search-v6"
    fingerprint = grader.grader_sha256()
    rows = read_jsonl(dataset_path)
    items = {row["case_name"]: row for row in rows}
    if len(items) != len(rows):
        raise ValueError("Duplicate cases in prepared dataset")
    results = read_jsonl(results_path)
    expected = {f"{case}-rollout-{index}" for case in items for index in range(1, 4)}
    names = [result["name"] for result in results]
    if len(set(names)) != len(names) or set(names) != expected:
        raise ValueError("Expected exactly three recorded rollouts per prepared case")
    if any(result.get("error") or result["case_name"] not in items for result in results):
        raise ValueError("Resolve infrastructure errors and case mismatches before regrading")
    for result in results:
        if not result["name"].startswith(result["case_name"] + "-rollout-"):
            raise ValueError("Result case and rollout name disagree")
    if workers < 1:
        raise ValueError("Workers must be positive")
    model = os.environ["RFT_GRADER_MODEL_DEPLOYMENT_NAME"]
    base_url = os.environ["WEB_SEARCH_OPENAI_BASE_URL"]
    output_dir.mkdir(parents=True, exist_ok=True)
    journal_path = output_dir / "assessments.jsonl"
    cached = {
        row["key"]: row["grounding"] for row in read_jsonl(journal_path)
    } if journal_path.exists() else {}

    def score(result: dict[str, Any]) -> dict[str, Any]:
        item = items[result["case_name"]]
        key = hashlib.sha256(json.dumps(
            {"sample": result["sample"], "item": item, "rubric": grader.RUBRIC,
             "model": model, "base_url": base_url, "grader_sha256": fingerprint},
            sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        assessment = cached.get(key)
        parsed = (
            grader.GroundingAssessment.model_validate(assessment) if assessment is not None else None
        )
        for attempt in range(5):
            try:
                details = grader.grade_with_details(result["sample"], item, assessment=parsed)
                break
            except RateLimitError as exc:
                if attempt == 4:
                    raise
                delay = max(10.0, _retry_delay(exc, attempt))
                logger.warning(
                    "Rate limited grading %s; retry %d/4 in %.1fs",
                    result["name"], attempt + 1, delay,
                )
                time.sleep(delay)
        return {
            "key": key, "name": result["name"], "case_name": result["case_name"],
            "case_group": item["case_group"], "previous_score": previous_grade(result["sample"], item),
            "observed_usage": result["usage"], "observed_latency_seconds": result["latency_seconds"],
            "rate_limit_retries_this_run": attempt,
            **details,
        }

    scored = {}
    with (
        journal_path.open("a", encoding="utf-8") as journal,
        ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        futures = {pool.submit(score, result): result["name"] for result in results}
        for future in as_completed(futures):
            try:
                result = future.result()
            except (OpenAIError, ValueError, RuntimeError) as exc:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(f"Grading failed for {futures[future]}") from exc
            scored[result["name"]] = result
            journal.write(json.dumps(result, ensure_ascii=False) + "\n")
            journal.flush()
            if len(scored) % 10 == 0 or len(scored) == len(results):
                print(f"Graded {len(scored)}/{len(results)}", flush=True)

    evaluation = [
        {"name": result["name"], "item": items[result["case_name"]]} for result in results
    ]
    score_inputs = [
        {"name": name, "sample": {"score": scored[name]["score"]}} for name in names
    ]
    evaluation_path = output_dir / "evaluation.jsonl"
    score_path = output_dir / "scores.jsonl"
    for path, values in (
        (evaluation_path, evaluation), (score_path, score_inputs),
        (output_dir / "details.jsonl", [scored[name] for name in names]),
    ):
        path.write_text(
            "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values), encoding="utf-8",
        )
    if grader.grader_sha256() != fingerprint:
        raise RuntimeError("Grader changed during calibration")
    calibration = calibrate(
        score_path, evaluation_path, grader=lambda sample, item: sample["score"],
        pass_threshold=grader.PASS_THRESHOLD,
    )
    old_perfect = [row for row in scored.values() if row["previous_score"] == 1]
    calibration.update({
        "grader_version": grader_version,
        "grader_sha256": fingerprint,
        "judge_model_deployment": model,
        "judge_reasoning_effort": grader.JUDGE_REASONING_EFFORT,
        "judge_base_url": base_url,
        "source_dataset_sha256": sha256(dataset_path),
        "source_results_sha256": sha256(results_path),
        "scope": "Frozen-input regrading; no new agent inference or live-web benchmark",
        "previous_perfect_audit": {
            "count": len(old_perfect),
            "still_perfect": sum(row["score"] == 1 for row in old_perfect),
            "quality_failures": sum(not row["quality_pass"] for row in old_perfect),
            "efficiency_only_reductions": sum(
                row["quality_pass"] and row["score"] < 1 for row in old_perfect
            ),
        },
        "failed_gates": dict(Counter(
            gate for row in scored.values() for gate in row["failed_gates"]
        )),
        "quality_passes": sum(row["quality_pass"] for row in scored.values()),
        "rate_limit_retries_this_run": sum(
            row["rate_limit_retries_this_run"] for row in scored.values()
        ),
        "score_passes": sum(row["score"] > grader.PASS_THRESHOLD for row in scored.values()),
        "efficiency_only_failures": sum(
            row["quality_pass"] and row["score"] <= grader.PASS_THRESHOLD for row in scored.values()
        ),
        "passes_identical_under_strict_and_inclusive": all(
            row["score"] != grader.PASS_THRESHOLD for row in scored.values()
        ),
    })
    (output_dir / "calibration.json").write_text(
        json.dumps(calibration, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    return calibration


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    result = recalibrate(args.results, args.dataset, args.output_dir, args.workers)
    print(json.dumps({key: value for key, value in result.items() if key != "threshold_sweep"},
                     indent=2))


if __name__ == "__main__":
    main()
