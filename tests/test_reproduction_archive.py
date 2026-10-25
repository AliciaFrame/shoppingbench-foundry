import json
from pathlib import Path

import pytest

from shoppingbench_foundry.artifacts import text_file_sha256

ROOT = Path(__file__).parents[1]
ARCHIVE = ROOT / "evals/results/reproductions/mtas-20261024"
TASKS = ("product", "shop", "voucher", "web", "web-live")


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path):
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def test_archive_inventory_matches_every_evidence_file():
    manifest = _json(ARCHIVE / "manifest.json")
    listed = {entry["path"] for entry in manifest["files"]}
    actual = {
        str(path.relative_to(ARCHIVE))
        for path in ARCHIVE.rglob("*")
        if path.suffix in {".json", ".jsonl"} and path.name != "manifest.json"
    }
    assert manifest["hash_line_endings"] == "LF"
    assert len(listed) == len(manifest["files"])
    assert listed == actual
    for entry in manifest["files"]:
        assert text_file_sha256(ARCHIVE / entry["path"]) == entry["sha256"]


@pytest.mark.parametrize("task", TASKS)
def test_archived_task_has_expected_coverage_metrics_and_contract(task):
    summary = _json(ARCHIVE / "summary.json")
    recorded = next(entry for entry in summary["tasks"] if entry["task"] == task)
    baseline = recorded["qwen_baseline"]
    rows = _rows(ARCHIVE / baseline["receipt"])
    dataset = ROOT / "evals/datasets/development" / (
        "web.jsonl" if task == "web-live" else f"{task}.jsonl"
    )
    cases = _rows(dataset)
    expected = {
        f"{case['name']}-rollout-{rollout}" for case in cases for rollout in (1, 2, 3)
    }
    assert len(cases) == 30
    assert len(rows) == baseline["episodes"] == baseline["expected"] == 90
    assert {row["name"] for row in rows} == expected
    assert baseline["mean_score"] == pytest.approx(
        sum(row["grade"]["score"] for row in rows) / 90
    )
    assert baseline["mean_latency_seconds"] == pytest.approx(
        sum(row["latency_seconds"] for row in rows) / 90
    )
    assert baseline["strict_success_episodes"] == sum(
        row["grade"].get("success") is True for row in rows
    )
    assert baseline["runtime_errors"] == sum(bool(row.get("error")) for row in rows)
    assert baseline["usage_incomplete"] == sum(
        not row["usage_complete"] for row in rows
    )
    for row in rows:
        if row.get("error"):
            assert row["grade"]["score"] == 0
    assert text_file_sha256(ARCHIVE / baseline["receipt"]) == recorded["receipt_sha256"]
    manifest = _json(ARCHIVE / "baseline" / f"{task}.manifest.json")
    assert manifest["contract"]["dataset_sha256"] == text_file_sha256(dataset)
    assert not manifest["archive"]["resume_allowed"]
    assert "model_base_url" not in manifest["contract"]
    assert not manifest["contract"].get("catalog_backend", "").startswith("https://")


def test_archive_preserves_incomplete_stages_and_separate_historical_metrics():
    summary = _json(ARCHIVE / "summary.json")
    assert summary["completed_episodes"] == summary["expected_episodes"] == 450
    assert summary["usage_incomplete_episodes"] == 450
    assert summary["native_optimizer"]["http_status"] == 400
    assert summary["native_optimizer"]["job_id"] is None
    assert summary["training_submissions"] == 0
    assert not summary["final_test_started"]
    assert summary["runtime_failures"] == [{
        "task": "web",
        "name": "web-132-rollout-2",
        "error": "ValueError: Unknown tool: web_search",
        "score": 0.0,
    }]
    hosted = _json(ARCHIVE / summary["references"]["previous_hosted"])
    assert [entry["successes"] for entry in hosted["references"]] == [146, 171]
    assert all(entry["cases"] == 250 for entry in hosted["references"])
    assert len({entry["dataset_sha256"] for entry in hosted["references"]}) == 1
    for path in ARCHIVE.rglob("*"):
        if path.suffix in {".json", ".jsonl"}:
            text = path.read_text(encoding="utf-8")
            for private in (
                "/Users/meng/", "Bearer ", "SHOPPINGBENCH_API_TOKEN",
                "Authorization", "api_key", "/subscriptions/",
                "metang-ft-eastus2-resource",
            ):
                assert private not in text
