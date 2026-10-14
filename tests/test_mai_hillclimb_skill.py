import copy
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).parents[1] / ".github" / "skills" / "mai-agent-hillclimb"
SCRIPT = SKILL / "scripts" / "cost_report.py"
SPEC = importlib.util.spec_from_file_location("mai_hillclimb_cost_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
costs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(costs)


@pytest.fixture
def book():
    return json.loads((SKILL / "examples" / "prices.synthetic.json").read_text(encoding="utf-8"))


@pytest.fixture
def episodes():
    with (SKILL / "examples" / "episodes.synthetic.jsonl").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def test_cached_buckets_failed_episodes_and_per_1k_denominator(book, episodes):
    report = costs.report(episodes, book)
    base = report["stages"]["baseline"]
    optimized = report["stages"]["agent-optimized"]
    assert base["episodes"] == 2 and base["failed_episodes"] == 1
    assert base["metrics"]["quality"]["mean"] == pytest.approx(0.4)
    assert base["serving_cost"] == pytest.approx(0.00186)
    assert base["serving_cost_per_1k_episodes"] == pytest.approx(0.93)
    assert base["known_charge_tokens_by_purpose"]["inference"]["uncached_input_tokens"] == 1200
    assert base["known_charge_tokens_by_purpose"]["inference"]["output_tokens"] == 250
    assert optimized["serving_cost_per_1k_episodes"] == pytest.approx(0.71)
    assert optimized["pricing_bases"] == ["synthetic"]


def test_judges_training_and_hosting_are_separate_from_serving_cost(book, episodes):
    sample = copy.deepcopy(episodes[:1])
    sample[0]["calls"].append({**sample[0]["calls"][0], "purpose": "evaluation"})
    charges = [
        {
            "stage": "baseline",
            "purpose": purpose,
            "label": purpose,
            "amount": 2,
            "currency": "USD",
            "source": "synthetic approved charge",
        }
        for purpose in ("training", "hosting")
    ]
    stage = costs.report(sample, book, charges)["stages"]["baseline"]
    assert stage["serving_cost"] == pytest.approx(0.00136)
    assert stage["experiment_cost"] == pytest.approx(4.00272)
    assert stage["known_cost_by_purpose"]["evaluation"] == pytest.approx(0.00136)
    charges[0].update(purpose="serving", allocation="this_run")
    stage = costs.report(sample, book, charges)["stages"]["baseline"]
    assert stage["serving_cost"] == pytest.approx(2.00136)


@pytest.mark.parametrize("missing", ["usage", "rate", "uncertain-request", "hosting"])
def test_unknown_usage_or_rates_are_not_reported_as_exact_free_cost(book, episodes, missing):
    sample = copy.deepcopy(episodes[:1])
    charges = None
    if missing == "usage":
        sample[0]["calls"][0]["usage"]["output_tokens"] = None
    elif missing == "rate":
        book["prices"]["policy-default"]["rates"]["output_tokens"] = None
    elif missing == "uncertain-request":
        sample[0]["usage_complete"] = False
    else:
        charges = [
            {
                "stage": "baseline",
                "purpose": "hosting",
                "label": "unpriced hosting",
                "amount": None,
                "currency": "USD",
                "source": "pending quote",
            }
        ]
    stage = costs.report(sample, book, charges)["stages"]["baseline"]
    assert stage["experiment_cost"] is None
    if missing == "hosting":
        assert stage["serving_cost"] == pytest.approx(0.00136)
        assert stage["unpriced_charges"]
    else:
        assert stage["serving_cost"] is None
        assert stage["known_serving_cost"] > 0


def test_context_tier_is_checked_per_call_not_episode_or_uncached_tokens(book, episodes):
    price = book["prices"]["policy-default"]
    price["max_context_tokens"] = 900
    with pytest.raises(ValueError, match="does not cover"):
        costs.report(episodes[:1], book)
    price["max_context_tokens"] = 1000
    assert costs.report(episodes[:1], book)["stages"]["baseline"]["serving_cost_complete"]


@pytest.mark.parametrize(
    "invalid", ["unit", "placeholder-rate", "duplicates", "cache-mode", "currency"]
)
def test_ambiguous_or_invalid_billing_inputs_fail_explicitly(book, episodes, invalid):
    if invalid == "unit":
        book["prices"]["policy-default"]["unit"] = "premium_request_multiplier"
    elif invalid == "placeholder-rate":
        book["prices"]["policy-default"]["rates"]["output_tokens"] = "$-"
    elif invalid == "duplicates":
        episodes.append(episodes[0])
    elif invalid == "cache-mode":
        book["prices"]["policy-default"]["cache_write_mode"] = "none"
    else:
        book["prices"]["policy-default"]["currency"] = "EUR"
    with pytest.raises(ValueError):
        costs.report(episodes, book)


def test_skill_runs_after_copying_only_its_folder_without_repository_or_packages(tmp_path):
    copied = tmp_path / "installed-skill"
    shutil.copytree(SKILL, copied)
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(copied / "scripts" / "cost_report.py"),
            "--receipts",
            str(copied / "examples" / "episodes.synthetic.jsonl"),
            "--prices",
            str(copied / "examples" / "prices.synthetic.json"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["stages"]["agent-optimized"]["serving_cost_per_1k_episodes"] == pytest.approx(
        0.71
    )
    entry = (copied / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = entry.split("---", 2)[1]
    assert re.search(r"^name: mai-agent-hillclimb$", frontmatter, re.MULTILINE)
    assert re.search(r"^description:", frontmatter, re.MULTILINE)
    for path in copied.rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if not target.startswith(("https://", "#")):
                assert (path.parent / target).is_file(), (path, target)
