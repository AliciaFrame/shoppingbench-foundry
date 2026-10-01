from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from shoppingbench_foundry.rft_grading_v2 import (
    WEB_V2_PASS_THRESHOLD,
    WEB_V2_VERSION,
    grade_with_details,
)


def _sample_from_output_item(output_item: dict[str, Any]) -> dict[str, Any]:
    output_tools = []
    for output in output_item.get("sample", {}).get("output", []) or []:
        output_tools.extend(output.get("tool_calls", []) or [])
    return {"output_tools": output_tools}


def recalibrate(path: Path) -> dict[str, Any]:
    output_items = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for output_item in output_items:
        details = grade_with_details(
            _sample_from_output_item(output_item),
            output_item["datasource_item"],
        )
        cases.append(
            {
                "name": output_item["datasource_item"]["case_name"],
                "platform_score": output_item["results"][0]["score"],
                **details,
            }
        )

    scores = [float(case["score"]) for case in cases]
    threshold_results = {}
    for threshold in (0.8, 0.85, 0.9, 0.95):
        failed = sum(score < threshold for score in scores)
        threshold_results[str(threshold)] = {
            "passed": len(scores) - failed,
            "failed": failed,
            "failure_rate": round(failed / len(scores), 6),
        }

    selected = threshold_results[str(WEB_V2_PASS_THRESHOLD)]
    return {
        "grader_version": WEB_V2_VERSION,
        "source_eval_id": output_items[0]["eval_id"],
        "source_run_id": output_items[0]["run_id"],
        "samples": len(cases),
        "mean_score": round(sum(scores) / len(scores), 6),
        "recommended_pass_threshold": WEB_V2_PASS_THRESHOLD,
        "base_failure_rate": selected["failure_rate"],
        "sufficient_signal": 0.25 <= selected["failure_rate"] <= 0.50,
        "score_distribution": {
            str(score): count for score, count in sorted(Counter(scores).items())
        },
        "threshold_results": threshold_results,
        "dimension_rates": {
            dimension: round(
                sum(bool(case[dimension]) for case in cases) / len(cases),
                6,
            )
            for dimension in (
                "exact_product",
                "viewed_product",
                "single_recommendation",
                "completed_protocol",
                "early_knowledge",
                "efficient_searches",
            )
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step0", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = recalibrate(args.step0)
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
