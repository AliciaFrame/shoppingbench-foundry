from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]

from evals.scripts.build_release_summary import _cost, _read_jsonl
from shoppingbench_foundry.artifacts import text_file_sha256

COMPARISON_ROOT = REPO_ROOT / "evals" / "results" / "model-comparison"
MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
START_MARKER = "<!-- model-cost-comparison:start -->"
END_MARKER = "<!-- model-cost-comparison:end -->"
BEST_SCORE = "\U0001f3c6"
CHEAPEST = "\U0001f4b0"


def read_reference(reference: dict[str, str], root: Path) -> tuple[Path, list[dict[str, Any]]]:
    path = root.joinpath(*reference["path"].split("/"))
    if text_file_sha256(path) != reference["sha256"]:
        raise ValueError(f"Receipt or dataset hash mismatch: {path}")
    return path, _read_jsonl(path)


def validate_usage(usage: dict[str, Any]) -> None:
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage[field]
        if type(value) is not int or value < 0:
            raise ValueError(f"Invalid token count: {field}={value!r}")
    if usage["input_tokens"] + usage["output_tokens"] != usage["total_tokens"]:
        raise ValueError("Input and output token counts do not match total_tokens")


def summarize_run(
    rows: list[dict[str, Any]],
    prices: dict[str, tuple[float, float]],
    expected_names: set[str],
) -> dict[str, Any]:
    names = [row["name"] for row in rows]
    if len(names) != len(set(names)) or set(names) != expected_names:
        raise ValueError("Run must contain exactly one result for every expected episode")
    missing_usage = 0
    costs = []
    for row in rows:
        score = row["grade"]["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            raise ValueError(f"Invalid canonical score for {row['name']}: {score!r}")
        if row.get("error") and score != 0:
            raise ValueError(f"Failed episode must have zero score: {row['name']}")
        usage = row["usage"]
        if usage is not None:
            validate_usage(usage)
        complete = usage is not None and usage["total_tokens"] > 0
        complete = complete and row.get("usage_complete", True)
        if "responses" in row:
            responses = row["responses"]
            totals = dict.fromkeys(("input_tokens", "output_tokens", "total_tokens"), 0)
            complete = complete and bool(responses)
            for response in responses:
                if response["stage"] not in {"selection", "web"}:
                    raise ValueError(f"Unknown response stage: {response['stage']}")
                if response["usage"] is None:
                    complete = False
                    continue
                validate_usage(response["usage"])
                for field in totals:
                    totals[field] += response["usage"][field]
            if usage is not None and any(usage[field] != totals[field] for field in totals):
                raise ValueError(f"Per-call usage does not match episode usage: {row['name']}")
        missing_usage += not complete
        costs.append(_cost({**row, "usage": usage or {}}, prices))
    return {
        "episodes": len(rows),
        "mean_score": round(statistics.fmean(row["grade"]["score"] for row in rows), 9),
        "errors": sum(bool(row.get("error")) for row in rows),
        "input_tokens": sum((row["usage"] or {}).get("input_tokens", 0) for row in rows),
        "output_tokens": sum((row["usage"] or {}).get("output_tokens", 0) for row in rows),
        "total_tokens": sum((row["usage"] or {}).get("total_tokens", 0) for row in rows),
        "cost_per_1k_episodes_usd": round(1000 * statistics.fmean(costs), 9),
        "episodes_with_missing_usage": missing_usage,
        "cost_is_lower_bound": bool(missing_usage),
    }


def select_best(stages: dict[str, dict[str, Any]]) -> str:
    if not stages:
        raise ValueError("No complete stages available")
    return max(stages, key=lambda stage: stages[stage]["mean_score"])


def lowest_recorded_cost_models(metrics: dict[str, dict[str, Any]]) -> set[str]:
    lowest = min(run["cost_per_1k_episodes_usd"] for run in metrics.values())
    return {model for model, run in metrics.items() if run["cost_per_1k_episodes_usd"] == lowest}


def build_comparison(manifest_path: Path, root: Path = REPO_ROOT) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["schema_version"] != 1 or manifest["hash_line_endings"] != "LF":
        raise ValueError("Unsupported comparison manifest schema or hash line endings")
    for field in ("cases", "rollouts_per_case"):
        if type(manifest[field]) is not int or manifest[field] < 1:
            raise ValueError(f"Invalid {field}: {manifest[field]!r}")
    rates = manifest["prices_usd_per_million_tokens"]
    for model in ("mai", *MODELS, "web_lookup"):
        for field in ("input", "output"):
            value = rates[model][field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"Invalid {model} {field} price: {value!r}")
    prices = {
        model: {
            "selection": (rates[model]["input"], rates[model]["output"]),
            "web": (rates["web_lookup"]["input"], rates["web_lookup"]["output"]),
        }
        for model in ("mai", *MODELS)
    }
    scenarios = {}
    for scenario in manifest["scenarios"]:
        _, dataset = read_reference(scenario["dataset"], root)
        if len(dataset) != manifest["cases"]:
            raise ValueError(f"Unexpected case count for {scenario['id']}")
        expected = {
            f"{case['name']}-rollout-{rollout}"
            for case in dataset
            for rollout in range(1, manifest["rollouts_per_case"] + 1)
        }
        if len(expected) != manifest["cases"] * manifest["rollouts_per_case"]:
            raise ValueError(f"Duplicate case names for {scenario['id']}")
        entry: dict[str, Any] = {"label": scenario["label"], "mai_runs": [], "gpt_runs": {}}
        for run in scenario["mai_runs"]:
            _, rows = read_reference(run["receipt"], root)
            entry["mai_runs"].append(
                {
                    "label": run["label"],
                    "receipt": run["receipt"]["path"],
                    **summarize_run(rows, prices["mai"], expected),
                }
            )
        for model in MODELS:
            stages = {}
            for stage, reference in scenario["gpt_runs"][model].items():
                if stage not in {"base", "optimized"}:
                    raise ValueError(f"Unknown GPT stage: {stage}")
                _, rows = read_reference(reference, root)
                stages[stage] = {
                    "receipt": reference["path"],
                    **summarize_run(rows, prices[model], expected),
                }
            entry["gpt_runs"][model] = {
                "stages": stages,
                "selected_stage": select_best(stages),
            }
        scenarios[scenario["id"]] = entry
    return {
        "methodology": {
            "cost": "Recorded API token usage at uncached list prices; unavailable usage excluded.",
            "hash_line_endings": manifest["hash_line_endings"],
            "cases": manifest["cases"],
            "rollouts_per_case": manifest["rollouts_per_case"],
            "prices_usd_per_million_tokens": rates,
            "excluded_incomplete_runs": manifest["excluded_incomplete_runs"],
        },
        "scenarios": scenarios,
    }


def render_section(report: dict[str, Any]) -> str:
    cases = report["methodology"]["cases"]
    rollouts = report["methodology"]["rollouts_per_case"]
    lines = [
        "## Cost comparison with GPT-5.6",
        "",
        "We compare the published MAI-Code-1.1-Flash choices against each GPT-5.6",
        "model's highest observed canonical score among available complete vanilla",
        f"and agent-optimized runs. Each run uses the same {cases} development cases",
        f"and {rollouts} rollouts ({cases * rollouts} episodes); failed episodes remain zero-scored.",
        "The two Web (static) rows show the accuracy choice and the RFT token alternative.",
        "Each model cell shows **score / recorded token cost (USD per 1,000 episodes)**.",
        "",
        "| MAI run | MAI Flash | Luna | Terra | Sol |",
        "|---|---:|---:|---:|---:|",
    ]
    for scenario in report["scenarios"].values():
        selected = {
            model: config["stages"][config["selected_stage"]]
            for model, config in scenario["gpt_runs"].items()
        }
        for mai in scenario["mai_runs"]:
            metrics = {"mai": mai, **selected}
            highest_score = max(run["mean_score"] for run in metrics.values())
            cheapest = lowest_recorded_cost_models(metrics)
            label = scenario["label"].replace("(", "").replace(")", "")
            stage = "opt." if mai["label"] == "optimized" else mai["label"].split(",")[0]
            cells = [f"{label} ({stage})".replace(" ", "&nbsp;")]
            for model, run in metrics.items():
                score = str(
                    Decimal(str(run["mean_score"])).quantize(
                        Decimal("0.001"),
                        rounding=ROUND_HALF_UP,
                    ),
                )
                if model != "mai" and scenario["gpt_runs"][model]["selected_stage"] == "optimized":
                    score += "\\*"
                if run["mean_score"] == highest_score:
                    score = f"**{score} {BEST_SCORE}**"
                cost = f"${run['cost_per_1k_episodes_usd']:.2f}"
                if model in cheapest:
                    cost = f"**{cost} {CHEAPEST}**"
                cells.append(f"{score} / {cost}".replace(" ", "&nbsp;"))
            lines.append("| " + " | ".join(cells) + " |")
    rates = report["methodology"]["prices_usd_per_million_tokens"]
    excluded = [
        f"{run['model'].removeprefix('gpt-5.6-').title()} "
        f"{report['scenarios'][run['scenario']]['label']} {run['stage']} "
        f"({run['episodes']}/{report['methodology']['cases'] * report['methodology']['rollouts_per_case']})"
        for run in report["methodology"]["excluded_incomplete_runs"]
    ]
    lines.extend(
        [
            "",
            f"**{BEST_SCORE}** marks the highest measured score in each row (not a claim of",
            f"statistical significance). **{CHEAPEST}** marks the lowest recorded token cost",
            "among that row's runs. **\\*** marks a selected agent-optimized GPT run; unmarked GPT",
            "results are vanilla. These GPT runs use the MAI-tuned configuration, not",
            "independent GPT-specific tuning. Selection is among available complete runs,",
            "not every possible optimized configuration.",
            "MAI RFT checkpoints are Voucher step 10, Web (static) v5 final, and Web (live) v6 final.",
            *(["Excluded incomplete candidates: " + "; ".join(excluded) + "."] if excluded else []),
            "",
            "Recorded token costs are **USD per 1,000 episodes**, calculated from API-reported",
            "input and output tokens, including reasoning output, with all input priced",
            "uncached. Rates per 1M tokens (input / output):",
            f"MAI ${rates['mai']['input']:.2f} / ${rates['mai']['output']:.2f};",
            (
                f"Luna ${rates['gpt-5.6-luna']['input']:.2f} / "
                f"${rates['gpt-5.6-luna']['output']:.2f};"
            ),
            (
                f"Terra ${rates['gpt-5.6-terra']['input']:.2f} / "
                f"${rates['gpt-5.6-terra']['output']:.2f};"
            ),
            (f"Sol ${rates['gpt-5.6-sol']['input']:.2f} / ${rates['gpt-5.6-sol']['output']:.2f}."),
            "Web (live) also includes the GPT-5.4-mini lookup at",
            f"${rates['web_lookup']['input']:.2f} / ${rates['web_lookup']['output']:.2f}.",
            "Training, hosting, and any separately billed search/tool fees are excluded.",
            "",
            "Costs exclude unavailable token usage from failed episodes.",
            "",
            "Recalculate the table offline, without Azure credentials or inference:",
            "",
            "```powershell",
            "python evals\\scripts\\compare_model_costs.py --write-readme",
            "python evals\\scripts\\compare_model_costs.py --check",
            "```",
            "",
            "The [manifest](evals/results/model-comparison/manifest.json) contains prices,",
            "all eligible stage inputs, excluded-run counts, and SHA-256 provenance.",
            "Receipt and dataset hashes normalize CRLF to LF for cross-platform checks.",
            "Committed GPT receipts retain per-episode scores, token usage, and failure",
            "flags; MAI runs reference the existing full receipts. The",
            "[generated summary](evals/results/model-comparison/summary.json) includes",
            "every candidate's score, total tokens, failure count, and missing-usage count.",
            "This reproduces the arithmetic and stage selection, not a new inference run.",
        ]
    )
    return "\n".join(lines) + "\n"


def readme_parts(text: str) -> tuple[str, str, str]:
    if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise ValueError("README must contain exactly one model-cost-comparison marker pair")
    before, remainder = text.split(START_MARKER)
    section, after = remainder.split(END_MARKER)
    return before, section, after


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reproduce token-cost comparison and README table."
    )
    parser.add_argument("--manifest", type=Path, default=COMPARISON_ROOT / "manifest.json")
    parser.add_argument("--output", type=Path, default=COMPARISON_ROOT / "summary.json")
    parser.add_argument("--write-readme", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check and args.write_readme:
        parser.error("--check and --write-readme are mutually exclusive")
    report = build_comparison(args.manifest)
    section = render_section(report)
    readme = REPO_ROOT / "README.md"
    if args.check:
        if json.loads(args.output.read_text(encoding="utf-8")) != report:
            raise SystemExit("Comparison summary is stale; rerun with --write-readme")
        if readme_parts(readme.read_text(encoding="utf-8"))[1].strip() != section.strip():
            raise SystemExit("README comparison is stale; rerun with --write-readme")
        print("Comparison summary and README are up to date.")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.write_readme:
        before, _, after = readme_parts(readme.read_text(encoding="utf-8"))
        readme.write_text(
            before + START_MARKER + "\n" + section + END_MARKER + after,
            encoding="utf-8",
        )
    print(section)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
