"""Report generic recorded-token costs without network or provider dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

BUCKETS = ("uncached_input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens")
CALL_PURPOSES = {"inference", "evaluation", "optimizer"}
CHARGE_PURPOSES = {"serving", "evaluation", "optimizer", "training", "hosting"}
BASES = {"azure_list", "copilot_reference", "customer_quote", "synthetic"}


def amount(value: Any, label: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError(f"{label} must be a numeric amount or null")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{label} is not a numeric amount: {value!r}") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return number


def token_count(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer or null")
    if value < 0:
        raise ValueError(f"{label} must be nonnegative")
    return value


def text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be text")
    if not value.strip():
        raise ValueError(f"{label} cannot be empty")
    return value


def validate_prices(book: dict[str, Any]) -> None:
    if book["schema_version"] != 1:
        raise ValueError("Unsupported pricebook schema")
    currency = text(book["currency"], "currency")
    if len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
        raise ValueError("Use an ISO-style uppercase currency code")
    if not book["prices"]:
        raise ValueError("At least one price entry is required")
    for key, price in book["prices"].items():
        for field in ("model", "provider", "region", "sku", "tier", "source_url"):
            text(price[field], f"{key}.{field}")
        date.fromisoformat(price["retrieved_at"])
        if price["basis"] not in BASES:
            raise ValueError(f"Unknown pricing basis for {key}")
        if price["unit"] != "per_million_tokens":
            raise ValueError(f"{key}: request multipliers/credits/hours are not token rates")
        if price.get("currency", currency) != currency:
            raise ValueError(f"Mixed currencies for {key}")
        lower = token_count(price.get("min_context_tokens", 0), f"{key}.min_context_tokens")
        upper = token_count(price.get("max_context_tokens"), f"{key}.max_context_tokens")
        if lower is None or (upper is not None and upper < lower):
            raise ValueError(f"Invalid context range for {key}")
        if price.get("cache_write_mode", "none") not in {"none", "replacement", "additional"}:
            raise ValueError(f"Unknown cache-write mode for {key}")
        for bucket in BUCKETS:
            amount(price["rates"].get(bucket), f"{key}.{bucket}")


def call_cost(
    call: dict[str, Any],
    book: dict[str, Any],
) -> tuple[Decimal, bool, list[str]]:
    key = text(call["pricing_key"], "pricing_key")
    if key not in book["prices"]:
        raise ValueError(f"Missing price entry: {key}")
    price = book["prices"][key]
    lower, upper = price.get("min_context_tokens", 0), price.get("max_context_tokens")
    context = token_count(call.get("context_input_tokens"), "context_input_tokens")
    if (lower > 0 or upper is not None) and context is None:
        raise ValueError(f"{key}: context tokens are needed to verify this tier")
    if context is not None and (context < lower or (upper is not None and context > upper)):
        raise ValueError(f"{key}: pricing tier does not cover this call's context")
    known = Decimal(0)
    missing = []
    usage = call["usage"]
    if (
        call.get("cache_write_mode") is not None
        and call["cache_write_mode"] != price.get("cache_write_mode", "none")
        and usage.get("cache_write_tokens") not in (None, 0)
    ):
        raise ValueError(f"{key}: recorded and priced cache-write modes disagree")
    for bucket in BUCKETS:
        count = token_count(usage.get(bucket), bucket)
        rate = amount(price["rates"].get(bucket), f"{key}.{bucket}")
        if count == 0:
            continue
        if (
            bucket == "cache_write_tokens"
            and count
            and price.get("cache_write_mode", "none") == "none"
        ):
            raise ValueError(f"{key}: cache writes require a verified replacement/additional mode")
        if count is None or rate is None:
            missing.append(bucket)
            continue
        known += Decimal(count) * rate / Decimal(1_000_000)
    return known, not missing, missing


def numeric(value: Decimal | None) -> float | None:
    return float(round(value, 10)) if value is not None else None


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def operation_charges(
    operations: list[dict[str, Any]], book: dict[str, Any]
) -> list[dict[str, Any]]:
    charges = []
    seen = set()
    for operation in operations:
        stage = operation["stage"]
        identity = (stage, operation["receipt_sha256"])
        if identity in seen:
            raise ValueError("Duplicate operation receipt would double-count experiment cost")
        seen.add(identity)
        receipt = operation["receipt"]
        job = receipt.get("job", {})
        if operation["kind"] == "optimizer":
            usage = job.get("result", {}).get("token_usage")
            if not usage:
                charges.append(
                    {
                        "stage": stage,
                        "purpose": "optimizer",
                        "amount": None,
                        "currency": book["currency"],
                        "label": "Missing optimizer usage",
                        "source": operation["receipt_sha256"],
                    }
                )
            for row in usage or []:
                purpose = "evaluation" if row["stage"] == "evaluation" else "optimizer"
                model = (
                    row.get("model")
                    or job["optimization_configuration"]["baseline_agent_configuration"][
                        "current_model"
                    ]
                )
                key = operation.get("model_pricing_keys", {}).get(model)
                known, complete = Decimal(0), False
                if key:
                    call = {
                        "pricing_key": key,
                        "context_input_tokens": None,
                        "usage": {
                            "uncached_input_tokens": (
                                row["input_tokens"] - row["cached_tokens"]
                                if row.get("input_tokens") is not None
                                and row.get("cached_tokens") is not None
                                else None
                            ),
                            "cached_input_tokens": row.get("cached_tokens"),
                            "cache_write_tokens": row.get("cache_write_tokens", 0),
                            "output_tokens": row.get("output_tokens"),
                        },
                    }
                    # Aggregates cannot prove per-call context tiers or cache-write semantics.
                    price = book["prices"][key]
                    if (
                        price.get("min_context_tokens", 0) == 0
                        and price.get("max_context_tokens") is None
                        and not row.get("cache_write_tokens")
                    ):
                        known, complete, _missing = call_cost(call, book)
                charges.append(
                    {
                        "stage": stage,
                        "purpose": purpose,
                        "amount": numeric(known) if complete else None,
                        "known_amount": numeric(known),
                        "currency": book["currency"],
                        "label": f"Optimizer {row['stage']} usage for {model}",
                        "source": operation["receipt_sha256"],
                    }
                )
        elif operation["kind"] == "calibration":
            for call in receipt.get("calls", []):
                known, complete, _missing = call_cost(call, book)
                charges.append(
                    {
                        "stage": stage,
                        "purpose": "evaluation",
                        "amount": numeric(known) if complete else None,
                        "known_amount": numeric(known),
                        "currency": book["currency"],
                        "label": "Recorded calibration judge call",
                        "source": operation["receipt_sha256"],
                    }
                )
        elif operation["kind"] == "rft":
            if not any(charge["purpose"] == "training" for charge in operation.get("charges", [])):
                charges.append(
                    {
                        "stage": stage,
                        "purpose": "training",
                        "amount": None,
                        "currency": book["currency"],
                        "label": "Unpriced RFT job",
                        "source": operation["receipt_sha256"],
                    }
                )
            grader = (
                receipt.get("config", {})
                .get("plan", {})
                .get("method", {})
                .get("reinforcement", {})
                .get("grader", {})
            )
            if grader.get("type") == "endpoint" and not any(
                charge["purpose"] == "evaluation" for charge in operation.get("charges", [])
            ):
                charges.append(
                    {
                        "stage": stage,
                        "purpose": "evaluation",
                        "amount": None,
                        "currency": book["currency"],
                        "label": "Unpriced external endpoint grader",
                        "source": operation["receipt_sha256"],
                    }
                )
        else:
            raise ValueError("Operation kind must be optimizer, calibration or rft")
        for charge in operation.get("charges", []):
            charges.append({**charge, "stage": stage})
    return charges


def report(
    episodes: list[dict[str, Any]],
    book: dict[str, Any],
    charges: list[dict[str, Any]] | None = None,
    operations: list[dict[str, Any]] | None = None,
    ledger_complete: bool = False,
) -> dict[str, Any]:
    validate_prices(book)
    if not episodes:
        raise ValueError("No episodes to report")
    stages: dict[str, Any] = {}
    seen = set()
    for episode in episodes:
        stage = text(episode["stage"], "stage")
        episode_id = text(episode["episode_id"], "episode_id")
        if (stage, episode_id) in seen:
            raise ValueError(f"Duplicate episode in {stage}: {episode_id}")
        seen.add((stage, episode_id))
        case = text(episode["case_id"], "case_id")
        if episode["status"] not in {"success", "failed"}:
            raise ValueError(f"Invalid episode status: {episode_id}")
        if type(episode["usage_complete"]) is not bool:
            raise TypeError("usage_complete must be explicit true or false")
        data = stages.setdefault(
            stage,
            {
                "episodes": 0,
                "failed_episodes": 0,
                "rollouts": Counter(),
                "latencies": [],
                "missing_latency_episodes": 0,
                "metrics": {},
                "purposes": {},
                "charge_tokens": {},
                "bases": set(),
                "missing_calls": [],
                "incomplete_usage_episodes": 0,
                "unpriced_charges": [],
                "serving_complete": True,
                "experiment_complete": True,
            },
        )
        data["episodes"] += 1
        data["failed_episodes"] += episode["status"] == "failed"
        data["rollouts"][case] += 1
        latency = amount(episode.get("latency_seconds"), "latency_seconds")
        if latency is None:
            data["missing_latency_episodes"] += 1
        else:
            data["latencies"].append(latency)
        for key, value in episode.get("metrics", {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"Metric {key} must be numeric")
            if not math.isfinite(value):
                raise ValueError(f"Metric {key} must be finite")
            data["metrics"].setdefault(key, []).append(value)
        if not isinstance(episode["calls"], list):
            raise TypeError("calls must be a list")
        for index, call in enumerate(episode["calls"]):
            purpose = call["purpose"]
            if purpose not in CALL_PURPOSES:
                raise ValueError(f"Unknown call purpose: {purpose}")
            cost, complete, missing = call_cost(call, book)
            token_totals = data["charge_tokens"].setdefault(purpose, Counter())
            for bucket in BUCKETS:
                count = call["usage"].get(bucket)
                if count is not None:
                    token_totals[bucket] += count
            data["purposes"][purpose] = data["purposes"].get(purpose, Decimal(0)) + cost
            data["bases"].add(book["prices"][call["pricing_key"]]["basis"])
            if not complete:
                data["experiment_complete"] = False
                if purpose == "inference":
                    data["serving_complete"] = False
                data["missing_calls"].append(
                    {
                        "episode_id": episode_id,
                        "call_index": index,
                        "pricing_key": call["pricing_key"],
                        "missing_buckets": missing,
                        "purpose": purpose,
                    }
                )
        if not episode["usage_complete"]:
            data["incomplete_usage_episodes"] += 1
            data["serving_complete"] = data["experiment_complete"] = False
    all_charges = (charges or []) + operation_charges(operations or [], book)
    for charge in all_charges:
        stage = charge["stage"]
        if stage not in stages:
            raise ValueError(f"Charge references unknown stage: {stage}")
        purpose = charge["purpose"]
        if purpose not in CHARGE_PURPOSES:
            raise ValueError(f"Unknown charge purpose: {purpose}")
        if charge["currency"] != book["currency"]:
            raise ValueError("Cannot add a charge in another currency")
        text(charge["label"], "charge label")
        text(charge["source"], "charge source")
        if purpose == "serving" and charge.get("allocation") != "this_run":
            raise ValueError("Serving charges need an explicit this_run allocation")
        cost = amount(charge["amount"], "charge amount")
        data = stages[stage]
        if cost is None:
            known = amount(charge.get("known_amount", 0), "known charge amount")
            if known:
                data["purposes"][purpose] = data["purposes"].get(purpose, Decimal(0)) + known
            data["unpriced_charges"].append({"purpose": purpose, "label": charge["label"]})
            data["experiment_complete"] = False
            if purpose == "serving":
                data["serving_complete"] = False
        else:
            data["purposes"][purpose] = data["purposes"].get(purpose, Decimal(0)) + cost
    output = {}
    for stage, data in stages.items():
        serving = data["purposes"].get("inference", Decimal(0)) + data["purposes"].get(
            "serving",
            Decimal(0),
        )
        experiment = sum(data["purposes"].values(), Decimal(0))
        output[stage] = {
            "episodes": data["episodes"],
            "receipts_sha256": fingerprint([row for row in episodes if row["stage"] == stage]),
            "failed_episodes": data["failed_episodes"],
            "rollouts_per_case": dict(data["rollouts"]),
            "metrics": {
                key: {"mean": sum(values) / len(values), "observations": len(values)}
                for key, values in data["metrics"].items()
            },
            "mean_latency_seconds": (
                numeric(sum(data["latencies"]) / len(data["latencies"]))
                if data["latencies"]
                else None
            ),
            "missing_latency_episodes": data["missing_latency_episodes"],
            "known_cost_by_purpose": {
                key: numeric(value) for key, value in data["purposes"].items()
            },
            "known_charge_tokens_by_purpose": {
                purpose: dict(tokens) for purpose, tokens in data["charge_tokens"].items()
            },
            "known_serving_cost": numeric(serving),
            "serving_cost": numeric(serving) if data["serving_complete"] else None,
            "known_serving_cost_per_1k_episodes": numeric(serving * 1000 / data["episodes"]),
            "serving_cost_per_1k_episodes": (
                numeric(serving * 1000 / data["episodes"]) if data["serving_complete"] else None
            ),
            "known_experiment_cost": numeric(experiment),
            "experiment_cost": numeric(experiment) if data["experiment_complete"] else None,
            "pricing_bases": sorted(data["bases"]),
            "serving_cost_complete": data["serving_complete"],
            "experiment_cost_complete": data["experiment_complete"],
            "nonserving_cost_complete": not data["unpriced_charges"]
            and not any(
                call["pricing_key"]
                for call in data["missing_calls"]
                if call.get("purpose") != "inference"
            ),
            "incomplete_usage_episodes": data["incomplete_usage_episodes"],
            "unpriced_call_count": len(data["missing_calls"]),
            "incomplete_calls": data["missing_calls"],
            "unpriced_charges": data["unpriced_charges"],
        }
    return {
        "schema_version": 1,
        "currency": book["currency"],
        "cost_type": "recorded-usage estimate, not an invoice",
        "price_snapshot": book,
        "receipts_sha256": fingerprint(episodes),
        "charges": charges or [],
        "operations": operations or [],
        "experiment_ledger_complete": ledger_complete,
        "stages": output,
    }


def verify_report(episodes: list[dict[str, Any]], recorded: dict[str, Any]) -> None:
    stages = {row["stage"] for row in episodes}
    regenerated = report(
        episodes,
        recorded["price_snapshot"],
        [row for row in recorded.get("charges", []) if row["stage"] in stages],
        [row for row in recorded.get("operations", []) if row["stage"] in stages],
        recorded.get("experiment_ledger_complete", False),
    )
    for stage in stages:
        if (
            recorded["stages"][stage].get("receipts_sha256")
            != regenerated["stages"][stage]["receipts_sha256"]
        ):
            raise ValueError("Cost report does not belong to these exact evaluation receipts")
        if regenerated["stages"][stage] != recorded["stages"][stage]:
            # Savings adds two derived success metrics, verified by the savings command itself.
            original = {
                key: value
                for key, value in recorded["stages"][stage].items()
                if key not in ("successful_tasks", "cost_per_successful_task")
            }
            if regenerated["stages"][stage] != original:
                raise ValueError(
                    "Cost report metrics differ from recomputed receipt/pricing evidence"
                )


def savings_report(
    episodes,
    book,
    baseline_stage,
    success_threshold=0.75,
    volume=1000,
    charges=None,
    operations=None,
    ledger_complete=False,
):
    if not 0 <= success_threshold <= 1 or volume < 1:
        raise ValueError("Success threshold must be in [0,1] and forecast volume positive")
    result = report(episodes, book, charges, operations, ledger_complete)
    by_stage = {}
    for row in episodes:
        by_stage.setdefault(row["stage"], []).append(row)
    if baseline_stage not in by_stage or len(by_stage) < 2:
        raise ValueError("Savings comparison requires baseline and at least one candidate")

    def index(rows):
        indexed = {}
        for row in rows:
            if row.get("split") != "development":
                raise ValueError("Savings requires development evaluation receipts")
            key = (row["case_id"], row["rollout"])
            if key in indexed:
                raise ValueError("Duplicate matched episode")
            indexed[key] = row
        return indexed

    baseline = index(by_stage[baseline_stage])
    comparisons = {}
    for stage, rows in by_stage.items():
        indexed = index(rows)
        if indexed.keys() != baseline.keys():
            raise ValueError("Cost savings requires identical scheduled cases and rollouts")
        for key, row in indexed.items():
            if any(
                row.get(field) != baseline[key].get(field)
                for field in ("data_sha256", "grader_sha256", "adapter_sha256", "group")
            ):
                raise ValueError("Cost savings requires matched data, grader, adapter and groups")
        current = result["stages"][stage]
        successes = sum(
            row["status"] == "success" and row["metrics"]["quality"] >= success_threshold
            for row in rows
        )
        current["successful_tasks"] = successes
        current["cost_per_successful_task"] = (
            current["serving_cost"] / successes
            if successes and current["serving_cost_complete"]
            else None
        )
        if stage == baseline_stage:
            continue
        old = result["stages"][baseline_stage]
        old_cost, new_cost = old["serving_cost"], current["serving_cost"]
        per_task_amount = (
            (amount(old_cost, "baseline serving cost") - amount(new_cost, "candidate serving cost"))
            / Decimal(len(rows))
            if old_cost is not None and new_cost is not None
            else None
        )
        per_task = float(per_task_amount) if per_task_amount is not None else None
        investments_complete = (
            ledger_complete
            and old["nonserving_cost_complete"]
            and current["nonserving_cost_complete"]
        )
        overhead = lambda entry: sum(
            amount(value, "overhead")
            for purpose, value in entry["known_cost_by_purpose"].items()
            if purpose not in ("inference", "serving")
        )
        investment = (
            max(overhead(current) - overhead(old), Decimal(0)) if investments_complete else None
        )
        comparisons[stage] = {
            "quality_delta": current["metrics"]["quality"]["mean"]
            - old["metrics"]["quality"]["mean"],
            "latency_ratio": (
                current["mean_latency_seconds"] / old["mean_latency_seconds"]
                if old["mean_latency_seconds"] and current["mean_latency_seconds"] is not None
                else None
            ),
            "serving_savings_per_task": per_task,
            "serving_savings_per_1k": per_task * 1000 if per_task is not None else None,
            "serving_savings_percent": (
                (old_cost - new_cost) / old_cost * 100
                if old_cost and new_cost is not None
                else None
            ),
            "incremental_experiment_investment": numeric(investment),
            "break_even_tasks": (
                math.ceil(investment / per_task_amount)
                if investment is not None and per_task is not None and per_task > 0
                else None
            ),
            "forecast_tasks": volume,
            "forecast_net_savings": (
                per_task * volume - float(investment)
                if per_task is not None and investment is not None
                else None
            ),
            "cost_basis": "recorded price snapshot; reference estimates are not invoice savings",
        }
    result.update(
        baseline_stage=baseline_stage, comparisons=comparisons, success_threshold=success_threshold
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", type=Path, nargs="+", required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--charges", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--operations", type=Path)
    parser.add_argument("--baseline-stage")
    parser.add_argument("--success-threshold", type=float, default=0.75)
    parser.add_argument("--forecast-tasks", type=int, default=1000)
    parser.add_argument(
        "--ledger-complete",
        action="store_true",
        help="Attest that all experiment charges are inventoried, including explicit unknowns",
    )
    args = parser.parse_args()
    episodes = []
    for path in args.receipts:
        with path.open(encoding="utf-8") as handle:
            episodes.extend(json.loads(line) for line in handle if line.strip())
    book = json.loads(args.prices.read_text(encoding="utf-8"))
    charges = json.loads(args.charges.read_text(encoding="utf-8")) if args.charges else None
    operations = []
    if args.operations:
        for operation in json.loads(args.operations.read_text(encoding="utf-8")):
            path = Path(operation.pop("path"))
            if not path.is_absolute():
                path = args.operations.parent / path
            operations.append(
                {
                    **operation,
                    "receipt": json.loads(path.read_text(encoding="utf-8")),
                    "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    result = (
        savings_report(
            episodes,
            book,
            args.baseline_stage,
            args.success_threshold,
            args.forecast_tasks,
            charges,
            operations,
            args.ledger_complete,
        )
        if args.baseline_stage
        else report(episodes, book, charges, operations, args.ledger_complete)
    )
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
