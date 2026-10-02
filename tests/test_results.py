import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
RAW = ROOT / "optimization" / "results" / "raw"
RFT_RAW = ROOT / "rft" / "results" / "raw"
SUMMARY = json.loads(
    (ROOT / "optimization" / "results" / "summary.json").read_text(encoding="utf-8")
)
RFT_SUMMARY = json.loads(
    (ROOT / "rft" / "results" / "web-rft3-holdout-comparison.json").read_text(
        encoding="utf-8"
    )
)
VOUCHER_RFT_SUMMARY = json.loads(
    (ROOT / "rft" / "results" / "voucher-rft1-holdout-comparison.json").read_text(
        encoding="utf-8"
    )
)
POST_RFT_SUMMARY = json.loads(
    (
        ROOT
        / "optimization"
        / "results"
        / "web-post-rft-step10-comparison.json"
    ).read_text(encoding="utf-8")
)


def _metrics(name: str) -> dict[str, float | int]:
    rows = [
        json.loads(line)
        for line in (RAW / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scores = [float(row["grade"]["score"]) for row in rows]
    return {
        "cases": len(rows),
        "mean": round(sum(scores) / len(scores), 6),
        "terminated": sum(bool(row["sample"].get("terminated")) for row in rows),
    }


def test_documented_canonical_results_match_raw_receipts():
    evidence = {
        "product": (
            _metrics("product-baseline-heldout.jsonl"),
            _metrics("product-round2-candidate-heldout.jsonl"),
        ),
        "shop": (
            _metrics("shop-round2-baseline-heldout.jsonl"),
            _metrics("shop-round2-candidate-heldout.jsonl"),
        ),
        "voucher": (
            _metrics("voucher-baseline-heldout.jsonl"),
            _metrics_from(RFT_RAW / "voucher-rft1-step12-holdout.jsonl"),
        ),
        "web": (
            _metrics("web-round2-baseline-heldout.jsonl"),
            _metrics_from(RFT_RAW / "web-rft3-step10-holdout.jsonl"),
        ),
    }

    for task, (baseline, final) in evidence.items():
        documented = SUMMARY["tasks"][task]
        assert baseline["cases"] == final["cases"] == 50
        assert baseline["mean"] == documented["baseline"]["canonical_mean_score"]
        if task in {"voucher", "web"}:
            assert final["mean"] == documented["rft"]["canonical_mean_score"]
        else:
            final_round = "round2" if task in {"product", "shop"} else "round1"
            assert final["mean"] == documented[final_round]["canonical_mean_score"]

    final_mean = sum(final["mean"] for _, final in evidence.values()) / 4
    assert abs(final_mean - SUMMARY["final_canonical_mean_across_tasks"]) < 1e-9


def test_documented_rft_checkpoint_results_match_raw_receipts():
    expected = {
        "step10": (0.901, 47),
        "step15": (0.836, 46),
        "final": (0.795, 40),
    }
    for label, (mean, terminated) in expected.items():
        metrics = _metrics_from(RFT_RAW / f"web-rft3-{label}-holdout.jsonl")
        documented = RFT_SUMMARY["candidates"][label]["metrics"]
        assert metrics["cases"] == documented["cases"] == 50
        assert metrics["mean"] == documented["mean_score"] == mean
        assert metrics["terminated"] == documented["terminated"] == terminated
    assert RFT_SUMMARY["selected_checkpoint"] == "step10"


def test_documented_voucher_rft_results_match_raw_receipts():
    expected = {
        "step3": (0.96375, 49),
        "step12": (0.994667, 49),
    }
    for label, (mean, terminated) in expected.items():
        metrics = _metrics_from(RFT_RAW / f"voucher-rft1-{label}-holdout.jsonl")
        documented = VOUCHER_RFT_SUMMARY["candidates"][label]["metrics"]
        assert metrics["cases"] == documented["cases"] == 50
        assert metrics["mean"] == documented["mean_score"] == mean
        assert metrics["terminated"] == documented["terminated"] == terminated
    assert VOUCHER_RFT_SUMMARY["selected_checkpoint"] == "step12"


def test_post_rft_optimizer_candidate_is_rejected():
    metrics = _metrics_from(
        RFT_RAW / "web-rft3-step10-post-opt-candidate1-holdout.jsonl"
    )
    documented = POST_RFT_SUMMARY["candidates"]["candidate1"]["metrics"]
    assert metrics["cases"] == documented["cases"] == 50
    assert metrics["mean"] == documented["mean_score"] == 0.871
    assert metrics["terminated"] == documented["terminated"] == 49
    assert documented["exact_product"] == 41
    assert POST_RFT_SUMMARY["retained_artifact"] == "baseline"
    assert POST_RFT_SUMMARY["decision"] == "rejected"


def _metrics_from(path: Path) -> dict[str, float | int]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scores = [float(row["grade"]["score"]) for row in rows]
    return {
        "cases": len(rows),
        "mean": round(sum(scores) / len(scores), 6),
        "terminated": sum(bool(row["sample"].get("terminated")) for row in rows),
    }
