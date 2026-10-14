import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
RESULTS = ROOT / "evals" / "results"


def _rows(path: Path) -> list[dict]:
    # Iterate file lines: product text can contain Unicode line separators.
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _mean_score(path: Path) -> float:
    rows = _rows(path)
    assert len(rows) == 90
    return round(sum(float(row["grade"]["score"]) for row in rows) / len(rows), 6)


def _comparison(name: str) -> dict:
    return json.loads(
        (RESULTS / "comparisons" / name).read_text(encoding="utf-8")
    )


def test_canonical_baseline_to_optimized_evidence_matches_receipts():
    expected = {
        "product": (0.752870, 0.940833),
        "shop": (0.795694, 0.937083),
        "voucher": (0.854722, 0.860625),
        "web": (0.691111, 0.728611),
    }

    for task, (baseline_mean, optimized_mean) in expected.items():
        baseline = _mean_score(RESULTS / "baseline" / f"{task}.jsonl")
        optimized = _mean_score(RESULTS / "optimized" / f"{task}.jsonl")
        comparison = _comparison(f"{task}-baseline-to-optimized.json")

        assert baseline == baseline_mean
        assert optimized == optimized_mean
        assert comparison["cases"] == 30
        assert comparison["rollouts_per_case"] == 3
        assert comparison["metrics"]["score"]["baseline"] == baseline
        assert comparison["metrics"]["score"]["candidate"] == optimized
        assert comparison["metrics"]["score"]["delta"] == round(
            optimized - baseline, 6
        )


def test_voucher_step10_is_selected_and_final_checkpoint_is_rejected():
    optimized = _mean_score(RESULTS / "optimized" / "voucher.jsonl")
    step10 = _mean_score(RESULTS / "checkpoints" / "voucher" / "step10.jsonl")
    final = _mean_score(RESULTS / "checkpoints" / "voucher" / "final.jsonl")
    rft_comparison = _comparison("voucher-optimized-to-rft.json")
    final_comparison = _comparison("voucher-step10-vs-final.json")

    assert (optimized, step10, final) == (0.860625, 0.924757, 0.925278)
    assert rft_comparison["metrics"]["score"]["delta"] == 0.064132
    assert rft_comparison["metrics"]["success"]["delta"] == 0.422222
    assert final_comparison["metrics"]["success"]["delta"] == -0.077778
    assert final_comparison["paired_case_outcomes"]["baseline_wins"] > (
        final_comparison["paired_case_outcomes"]["candidate_wins"]
    )


def test_hardened_web_v5_receipts_and_selected_checkpoint_are_present():
    optimized = _mean_score(RESULTS / "optimized" / "web.jsonl")
    checkpoint_means = {
        name: _mean_score(RESULTS / "checkpoints" / "web-v5" / f"{name}.jsonl")
        for name in ("step6", "step8", "final")
    }
    comparison = _comparison("web-v5-optimized-to-rft.json")

    assert optimized == 0.728611
    assert checkpoint_means == {
        "step6": 0.6825,
        "step8": 0.679444,
        "final": 0.7175,
    }
    assert checkpoint_means["final"] == max(checkpoint_means.values())
    assert comparison["metrics"]["score"]["baseline"] == optimized
    assert comparison["metrics"]["score"]["candidate"] == checkpoint_means["final"]
    assert comparison["metrics"]["tokens"]["delta"] < 0
    assert comparison["metrics"]["latency_seconds"]["delta"] < 0


def test_live_web_climbs_from_base_to_optimized_to_rft():
    baseline = _mean_score(RESULTS / "baseline" / "web-search.jsonl")
    optimized = _mean_score(RESULTS / "optimized" / "web-search.jsonl")
    rft = _mean_score(RESULTS / "checkpoints" / "web-search-v6" / "final.jsonl")
    assert (baseline, optimized, rft) == (0.249722, 0.829167, 0.9025)
    for name in ("web-search-baseline-to-optimized.json", "web-search-optimized-to-rft.json"):
        assert _comparison(name)["metrics"]["score"]["delta_clustered_95_ci"][0] > 0
    for stage, reward in (("baseline", 0.18), ("optimized", 0.739), ("rft", 0.82)):
        summary = json.loads(
            (RESULTS / "scored" / f"web-search-{stage}.summary.json").read_text(encoding="utf-8"),
        )
        assert round(summary["rft_reward"], 3) == reward
        if stage != "baseline":
            assert summary["paired_vs_baseline"]["rft_reward"]["case_clustered_95_ci"][0] > 0
    manifest = json.loads(
        (RESULTS / "baseline" / "web-search.manifest.json").read_text(encoding="utf-8"),
    )
    assert manifest["contract"]["selection_deployment"].startswith("mai-code-1.1-flash")


def test_release_summary_matches_published_receipts():
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))

    assert summary["methodology"]["episodes_per_stage"] == 90
    assert summary["tasks"]["voucher"]["selected_stage"] == "rft"
    assert summary["tasks"]["catalog_web"]["stages"]["rft"]["mean_score"] == 0.7175
    assert summary["tasks"]["catalog_web"]["selected_stage"] == "optimized"
    assert summary["tasks"]["catalog_web"]["alternative_stage"] == "rft"
    assert summary["tasks"]["live_web_search"]["selected_stage"] == "rft"
    assert summary["tasks"]["live_web_search"]["final_test"]["episodes"] == 50


def test_final_test_receipts_are_present_for_all_tasks():
    for task in ("product", "shop", "voucher", "web", "web-search"):
        rows = _rows(RESULTS / "final" / f"{task}.jsonl")
        assert len(rows) == 50
