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
        "label": "Web (static)",
        "base": "baseline/web.jsonl",
        "optimized": "optimized/web.jsonl",
        "rft": "checkpoints/web-v5/final.jsonl",
        "selected_stage": "optimized",
        "alternative_stage": "rft",
        "alternative_reason": "Lower cost and latency for a small score trade-off",
    },
    "live_web_search": {
        "label": "Web (live)",
        "base": "baseline/web-search.jsonl",
        "optimized": "optimized/web-search.jsonl",
        "rft": "checkpoints/web-search-v6/final.jsonl",
        "selected_stage": "rft",
        "final_test": "final/web-search.jsonl",
    },
}
# USD per million tokens (input, output) at list price, uncached input.
PRICES = {"selection": (0.20, 1.20), "web": (0.75, 4.50)}
PRICE_NOTE = (
    "MAI-Code-1.1-Flash $0.20 input / $1.20 output per 1M tokens; the Web (live) "
    "web-search lookup step uses GPT-5.4-mini at $0.75 / $4.50. All input is priced uncached."
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _cost(row: dict[str, Any]) -> float:
    if "responses" in row:
        parts = [
            (PRICES["web" if response["stage"] == "web" else "selection"], response["usage"] or {})
            for response in row["responses"]
        ]
    else:
        parts = [(PRICES["selection"], row["usage"])]
    return sum(
        usage.get("input_tokens", 0) * price[0] + usage.get("output_tokens", 0) * price[1]
        for price, usage in parts
    ) / 1e6


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
        "cost_per_1k_episodes_usd": round(1000 * statistics.fmean(map(_cost, rows)), 4),
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
            "rft_status": "complete" if "rft" in stages else "not_run",
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
        if "alternative_stage" in config:
            tasks[name]["alternative_stage"] = config["alternative_stage"]
            tasks[name]["alternative_reason"] = config["alternative_reason"]
        if "final_test" in config:
            tasks[name]["final_test"] = _metrics(RESULTS_ROOT / config["final_test"])
    return {
        "methodology": {
            "cases": 30,
            "rollouts_per_case": 3,
            "episodes_per_stage": 90,
            "score": "canonical deterministic development score",
            "cost": PRICE_NOTE,
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
