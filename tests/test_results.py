import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
RAW = ROOT / "optimization" / "results" / "raw"
SUMMARY = json.loads(
    (ROOT / "optimization" / "results" / "summary.json").read_text(encoding="utf-8")
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
            _metrics("voucher-candidate-heldout.jsonl"),
        ),
        "web": (
            _metrics("web-round2-baseline-heldout.jsonl"),
            _metrics("web-round2-baseline-heldout.jsonl"),
        ),
    }

    for task, (baseline, final) in evidence.items():
        documented = SUMMARY["tasks"][task]
        assert baseline["cases"] == final["cases"] == 50
        assert baseline["mean"] == documented["baseline"]["canonical_mean_score"]
        final_round = "round2" if task in {"product", "shop"} else "round1"
        if task == "web":
            assert final["mean"] == documented["baseline"]["canonical_mean_score"]
        else:
            assert final["mean"] == documented[final_round]["canonical_mean_score"]

    final_mean = sum(final["mean"] for _, final in evidence.values()) / 4
    assert round(final_mean, 6) == SUMMARY["final_canonical_mean_across_tasks"]
