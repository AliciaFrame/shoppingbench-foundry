import json

import pytest

from rft.scripts.prepare_live_web_data import capture_sha256, prepare, read_jsonl, sha256
from shoppingbench_foundry.tool_definitions import chat_completions_tools


@pytest.fixture
def inputs(tmp_path):
    data = tmp_path / "data"
    config = tmp_path / "config"
    output = tmp_path / "output"
    data.mkdir()
    config.mkdir()
    (config / "instructions.md").write_text("Optimized prompt.", encoding="utf-8")
    tools = chat_completions_tools("web")
    tools[0]["function"]["description"] = "Optimized tool guidance."
    (config / "tools.json").write_text(json.dumps(tools), encoding="utf-8")
    captures = []
    for split in ("train", "validation"):
        query = f"The original {split} shopping query"
        row = {
            "case_name": split, "case_group": split, "query": query, "task": "web",
            "reward": {"product_id": f"secret-{split}"}, "knowledge_attribute": f"gold-{split}",
            "max_search_calls": 4,
        }
        (data / f"web-{split}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        captures.append({
            "case_name": split, "name": f"{split}-rollout-1", "query": query,
            "started_at": "2026-10-12T00:00:00Z",
            "sample": {
                "web_evidence": "Observed evidence, not the answer label.",
                "web_sources": [{"title": "Source", "url": "https://example.test/source"}],
            },
            "responses": [{
                "stage": "web", "input": query, "model": "web-model", "status": "completed",
                "evidence": "Observed evidence, not the answer label.",
                "native_search_calls": [{"type": "web_search_call"}],
            }],
        })
    evidence = tmp_path / "evidence.jsonl"
    evidence.write_text("".join(json.dumps(row) + "\n" for row in captures), encoding="utf-8")
    return evidence, data, config, output


def test_preparation_uses_observed_evidence_and_exact_optimized_contract(inputs):
    evidence, data, config, output = inputs
    report = prepare(evidence, data, config, output)
    train = read_jsonl(output / "web-train.jsonl")[0]
    assert train["messages"][0]["content"] == "Optimized prompt."
    assert train["tools"] == json.loads((config / "tools.json").read_text(encoding="utf-8"))
    text = train["messages"][1]["content"]
    assert "Observed evidence, not the answer label." in text
    assert "https://example.test/source" in text
    assert "gold-train" not in text
    assert "secret-train" not in text
    assert "max_search_calls" not in train
    assert report["status"] == "prepared-not-approved-for-submission"


@pytest.mark.parametrize(
    "invalid", ["no-search", "different-query", "duplicate", "error", "incomplete", "uncited"],
)
def test_preparation_rejects_unverified_or_ambiguous_captures(inputs, invalid):
    evidence, data, config, output = inputs
    captures = read_jsonl(evidence)
    if invalid == "no-search":
        captures[0]["responses"][0]["native_search_calls"] = []
    elif invalid == "different-query":
        captures[0]["responses"][0]["input"] = "A different query"
    elif invalid == "duplicate":
        captures.append(captures[0])
    elif invalid == "incomplete":
        captures[0]["responses"][0]["status"] = "incomplete"
    elif invalid == "uncited":
        captures[0]["sample"]["web_sources"] = []
    else:
        captures[0]["error"] = "Upstream failure"
    evidence.write_text(
        "".join(json.dumps(row) + "\n" for row in captures), encoding="utf-8",
    )
    with pytest.raises(ValueError):
        prepare(evidence, data, config, output)


def reviewed_inputs(inputs):
    evidence, data, _, _ = inputs
    captures = read_jsonl(evidence)
    for capture in captures:
        text = f"Observed gold-{capture['case_name']} answer with a source."
        capture["sample"]["web_evidence"] = text
        capture["responses"][0]["evidence"] = text
    evidence.write_text(
        "".join(json.dumps(row) + "\n" for row in captures), encoding="utf-8",
    )
    review = {
        "status": "approved-for-data-preparation",
        "source_sha256": {
            f"web-{split}.jsonl": sha256(data / f"web-{split}.jsonl")
            for split in ("train", "validation")
        },
        "validation_groups": ["validation"],
        "cases": {
            capture["case_name"]: {
                "decision": "include", "reason": "Reviewed factual evidence.",
                "capture_sha256": capture_sha256(capture),
            } for capture in captures
        },
    }
    path = evidence.parent / "review.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    return path, review


def test_review_selects_an_explicit_capture_without_rewriting_its_evidence(inputs):
    review_path, _ = reviewed_inputs(inputs)
    evidence, data, config, output = inputs
    captures = read_jsonl(evidence)
    failed = json.loads(json.dumps(captures[0]))
    failed["error"] = "Earlier failed retrieval"
    evidence.write_text(
        "".join(json.dumps(row) + "\n" for row in [failed, *captures]), encoding="utf-8",
    )
    report = prepare(evidence, data, config, output, review_path)
    train = read_jsonl(output / "web-train.jsonl")[0]
    assert train["live_web_context"]["evidence"] == captures[0]["sample"]["web_evidence"]
    assert report["semantic_review"]["excluded_cases"] == {}
    assert len(report["remaining_gates"]) == 2


@pytest.mark.parametrize(
    "invalid", ["stale-capture", "stale-source", "missing-case", "alias-overlap", "unapproved"],
)
def test_semantic_review_fails_closed(inputs, invalid):
    review_path, review = reviewed_inputs(inputs)
    evidence, data, config, output = inputs
    if invalid == "stale-capture":
        review["cases"]["train"]["capture_sha256"] = "stale"
    elif invalid == "stale-source":
        review["source_sha256"]["web-train.jsonl"] = "stale"
    elif invalid == "missing-case":
        del review["cases"]["train"]
    elif invalid == "alias-overlap":
        for decision in review["cases"].values():
            decision["knowledge_aliases"] = ["same entity"]
    else:
        review["status"] = "candidate"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError):
        prepare(evidence, data, config, output, review_path)
    assert not output.exists()


def test_explicit_alias_repairs_agreement_without_changing_the_gold_label(inputs):
    review_path, review = reviewed_inputs(inputs)
    evidence, data, config, output = inputs
    train_path = data / "web-train.jsonl"
    train = read_jsonl(train_path)[0]
    train["knowledge_attribute"] = "Mane"
    train_path.write_text(json.dumps(train) + "\n", encoding="utf-8")
    review["source_sha256"]["web-train.jsonl"] = sha256(train_path)
    captures = read_jsonl(evidence)
    captures[0]["sample"]["web_evidence"] = "Man\u00e9"
    captures[0]["responses"][0]["evidence"] = "Man\u00e9"
    evidence.write_text(
        "".join(json.dumps(row) + "\n" for row in captures), encoding="utf-8",
    )
    review["cases"]["train"]["capture_sha256"] = capture_sha256(captures[0])
    review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="contradicts or omits"):
        prepare(evidence, data, config, output, review_path)
    review["cases"]["train"]["knowledge_aliases"] = ["Man\u00e9"]
    review_path.write_text(json.dumps(review), encoding="utf-8")
    prepare(evidence, data, config, output, review_path)
    prepared = read_jsonl(output / "web-train.jsonl")[0]
    assert prepared["knowledge_attribute"] == "Mane"
    assert prepared["live_web_context"]["evidence"] == "Man\u00e9"


def test_review_cannot_quarantine_only_part_of_a_connected_group(inputs):
    review_path, review = reviewed_inputs(inputs)
    evidence, data, config, output = inputs
    for split in ("train", "validation"):
        path = data / f"web-{split}.jsonl"
        row = read_jsonl(path)[0]
        row["case_group"] = "shared"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        review["source_sha256"][path.name] = sha256(path)
    review["cases"]["train"] = {"decision": "exclude", "reason": "Unverified evidence"}
    review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="entire connected case group"):
        prepare(evidence, data, config, output, review_path)
