"""Report generic recorded-token costs without network or provider dependencies."""

from __future__ import annotations

import argparse
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


def report(
    episodes: list[dict[str, Any]],
    book: dict[str, Any],
    charges: list[dict[str, Any]] | None = None,
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
                    }
                )
        if not episode["usage_complete"]:
            data["incomplete_usage_episodes"] += 1
            data["serving_complete"] = data["experiment_complete"] = False
    for charge in charges or []:
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
        "stages": output,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--charges", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.receipts.open(encoding="utf-8") as handle:
        episodes = [json.loads(line) for line in handle if line.strip()]
    book = json.loads(args.prices.read_text(encoding="utf-8"))
    charges = json.loads(args.charges.read_text(encoding="utf-8")) if args.charges else None
    result = report(episodes, book, charges)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
