from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = REPO_ROOT / "evals" / "results"

RUNS = {
    "product": {
        "label": "Product",
        "base": "baseline/product.jsonl",
        "optimized": "optimized/product.jsonl",
        "selected_stage": "optimized",
    },
    "shop": {
        "label": "Shop",
        "base": "baseline/shop.jsonl",
        "optimized": "optimized/shop.jsonl",
        "selected_stage": "optimized",
    },
    "voucher": {
        "label": "Voucher",
        "base": "baseline/voucher.jsonl",
        "optimized": "optimized/voucher.jsonl",
        "rft": "checkpoints/voucher/step10.jsonl",
        "selected_stage": "rft",
    },
    "catalog_web": {
        "label": "Catalog Web",
        "base": "baseline/web.jsonl",
        "optimized": "optimized/web.jsonl",
        "rft": "checkpoints/web-v5/final.jsonl",
        "selected_stage": "rft",
    },
    "live_web_search": {
        "label": "Live Web Search",
        "base": "baseline/web-search.jsonl",
        "optimized": "optimized/web-search.jsonl",
        "selected_stage": "optimized",
        "rft_status": "wip",
    },
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _metrics(path: Path) -> dict[str, float | int]:
    rows = _read_jsonl(path)
    return {
        "episodes": len(rows),
        "mean_score": round(
            statistics.fmean(float(row["grade"]["score"]) for row in rows),
            6,
        ),
        "mean_latency_seconds": round(
            statistics.fmean(float(row["latency_seconds"]) for row in rows),
            6,
        ),
        "mean_total_tokens": round(
            statistics.fmean(int(row["usage"]["total_tokens"]) for row in rows),
            3,
        ),
    }


def _relative_delta(candidate: float, baseline: float) -> float:
    return round(candidate / baseline - 1, 6)


def build_summary() -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    for name, config in RUNS.items():
        stages = {
            stage: _metrics(RESULTS_ROOT / config[path_key])
            for stage, path_key in (("base", "base"), ("optimized", "optimized"), ("rft", "rft"))
            if path_key in config
        }
        selected_stage = str(config["selected_stage"])
        base = stages["base"]
        selected = stages[selected_stage]
        tasks[name] = {
            "label": config["label"],
            "stages": stages,
            "selected_stage": selected_stage,
            "rft_status": config.get("rft_status", "complete" if "rft" in stages else "not_run"),
            "selected_gain_vs_base": {
                "mean_score": round(
                    float(selected["mean_score"]) - float(base["mean_score"]),
                    6,
                ),
                "latency_fraction": _relative_delta(
                    float(selected["mean_latency_seconds"]),
                    float(base["mean_latency_seconds"]),
                ),
                "tokens_fraction": _relative_delta(
                    float(selected["mean_total_tokens"]),
                    float(base["mean_total_tokens"]),
                ),
            },
        }
    return {
        "methodology": {
            "cases": 30,
            "rollouts_per_case": 3,
            "episodes_per_stage": 90,
            "score": "canonical deterministic development score",
        },
        "tasks": tasks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=RESULTS_ROOT / "summary.json",
    )
    args = parser.parse_args()
    summary = build_summary()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
