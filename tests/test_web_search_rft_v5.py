import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from shoppingbench_foundry import web_search_rft_grading_v4 as v4
from shoppingbench_foundry import web_search_rft_grading_v5 as grader
from shoppingbench_foundry.app import app

ITEM = {
    "task": "web", "query": "Find a frame matching the artwork's dimensions.",
    "knowledge_attribute": "27 x 40",
    "reward": {"product_id": "2402370482", "title": "27 x 40 inch wall frame"},
    "live_web_context": {"evidence": "The artwork measures 27 x 40 inches.", "sources": []},
}
FULL = grader.GroundingAssessment(
    issues=[], clue_faithfulness="full", product_faithfulness="full", answer_completeness="full",
    clue_correctness="full",
)


def sample(clue="27 x 40"):
    return {"output_tools": [
        {"name": "find_product", "arguments": {"q": "27 x 40 wall frame", "page": 1}},
        {"name": "view_product_information", "arguments": {"product_ids": "2402370482"}},
        {"name": "recommend_product", "arguments": {"product_ids": "2402370482"}},
        {"name": "terminate", "arguments": {
            "product_ids": "2402370482", "resolved_clue": clue,
            "final_answer": f"The artwork measures {clue} inches. Wall frame 2402370482 matches.",
        }},
    ]}


def details(value, assessment=FULL):
    return grader.grade_with_details(value, ITEM, assessment=assessment)


@pytest.mark.parametrize("notation", ["27 \u00d7 40", "27x40", "27 X 40", "27 by 40"])
def test_semantic_equivalence_is_not_overruled_by_literal_matching(notation):
    value = sample(notation)
    before = copy.deepcopy(value)
    assert details(value)["score"] == 1
    assert value == before
    old_assessment = v4.GroundingAssessment(
        **FULL.model_dump(exclude={"clue_correctness"}),
    )
    expected_old = 1.0 if notation == "27 X 40" else 0.79
    assert v4.grade_with_details(value, ITEM, assessment=old_assessment)["score"] == expected_old


def test_faithfully_repeating_wrong_evidence_does_not_override_gold_correctness():
    faithful_but_wrong = FULL.model_copy(update={"clue_correctness": "none"})
    result = details(sample("27 x 41"), faithful_but_wrong)
    assert not result["quality_pass"]
    assert result["failed_gates"] == ["clue_correctness"]
    assert result["score"] == 0.55


@pytest.mark.parametrize("format_name", ["responses", "chat"])
def test_dimension_normalization_supports_serialized_tool_formats(format_name):
    value = sample("27\u00d740")
    if format_name == "responses":
        value = {"output": [
            {"type": "function_call", "name": call["name"],
             "arguments": json.dumps(call["arguments"])} for call in value["output_tools"]
        ]}
    else:
        value = {"messages": [{"role": "assistant", "tool_calls": [
            {"function": call} for call in value["output_tools"]
        ]}]}
    assert details(value)["score"] == 1


def test_recovery_search_passes_but_costly_correct_answers_still_outrank_bad_answers():
    value = sample()
    scores = [details(value)["score"]]
    for page in range(2, 6):
        value["output_tools"].insert(
            1, {"name": "find_product", "arguments": {"q": "27 x 40 wall frame", "page": page}},
        )
        scores.append(details(value)["score"])
    assert scores == [1, 0.96, 0.84, 0.72, 0.64]
    assert scores[1] > grader.PASS_THRESHOLD > scores[2]
    assert details(value)["efficiency_only_failure"]
    partial = FULL.model_copy(update={"product_faithfulness": "partial"})
    assert scores[-1] > details(sample(), partial)["score"]
    incomplete = sample()
    incomplete["output_tools"] = incomplete["output_tools"][:2]
    assert details(incomplete)["score"] == 0.0825


@pytest.mark.parametrize("change", ["reorder", "page", "filter", "new_constraint"])
def test_repeats_respect_pagination_filters_and_new_constraints(change):
    value = sample()
    arguments = {"q": "frame wall 40 x 27", "page": 1, "sort": "default", "price": ""}
    if change == "page":
        arguments["page"] = 2
    elif change == "filter":
        arguments["price"] = "100-200"
    elif change == "new_constraint":
        arguments["q"] += " metal"
    value["output_tools"].insert(1, {"name": "find_product", "arguments": arguments})
    result = details(value)
    assert result["repeated_searches"] == (change == "reorder")
    assert result["score"] == (0.8 if change == "reorder" else 0.96)


def test_partial_overlap_view_is_not_wholly_redundant():
    value = sample()
    value["output_tools"].insert(
        2, {"name": "view_product_information", "arguments": {"product_ids": "2402370482,9876543210"}},
    )
    assert details(value)["repeated_views"] == 0
    assert details(value)["score"] == 0.97
    value["output_tools"][2]["arguments"]["product_ids"] = "2402370482"
    assert details(value)["repeated_views"] == 1
    assert details(value)["score"] == 0.81


@pytest.mark.parametrize("mutation", ["wrong_id", "malformed", "no_inspection", "late_search"])
def test_full_judge_cannot_override_structural_failures(mutation):
    value = sample()
    if mutation == "wrong_id":
        value["output_tools"][-2]["arguments"]["product_ids"] = "9876543210"
    elif mutation == "malformed":
        value["output_tools"].append({"name": "terminate", "arguments": "{bad"})
    elif mutation == "no_inspection":
        value["output_tools"].pop(1)
    else:
        value["output_tools"].append(copy.deepcopy(value["output_tools"][0]))
    result = details(value)
    assert not result["quality_pass"]
    assert result["score"] <= grader.FAILED_QUALITY_CAP


def test_published_endpoint_uses_judge_not_candidate_assessments(monkeypatch):
    from shoppingbench_foundry import web_search_rft_grading_v6 as v6

    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    partial = FULL.model_copy(update={"product_faithfulness": "partial"})
    monkeypatch.setattr(grader, "assess_grounding", lambda sample, item: partial)
    value = sample()
    value["grounding"] = FULL.model_dump()
    client = TestClient(app)
    payload = {"sample": value, "item": ITEM}
    assert client.post("/grade/web-search/v6", json=payload).status_code == 401
    response = client.post(
        "/grade/web-search/v6", json=payload,
        headers={"Authorization": "Bearer test-token"},
    )
    expected = v6.grade_with_details(value, ITEM, assessment=partial)["score"]
    assert response.json() == {"score": expected}


def test_v5_judge_uses_original_trusted_evidence_and_surfaces_failures(monkeypatch):
    requests = []

    def parse(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(status="completed", output_parsed=FULL)

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    client.with_options = lambda **kwargs: client
    monkeypatch.setattr(grader, "get_web_search_client", lambda: client)
    monkeypatch.setenv("RFT_GRADER_MODEL_DEPLOYMENT_NAME", "test-judge")
    value = sample("27 \u00d7 40")
    value["web_evidence"] = "untrusted replacement evidence"
    assert grader.grade(value, ITEM) == 1
    packet = json.loads(requests[0]["input"])
    assert packet["web_evidence"] == ITEM["live_web_context"]["evidence"]
    assert packet["candidate"]["resolved_clue"] == "27 \u00d7 40"
    assert requests[0]["instructions"] == grader.RUBRIC
    client.responses.parse = lambda **kwargs: SimpleNamespace(
        status="incomplete", output_parsed=None,
    )
    with pytest.raises(RuntimeError, match="no complete assessment"):
        grader.grade(value, ITEM)


def test_calibration_separates_quality_and_efficiency_failures(tmp_path, monkeypatch):
    from rft.scripts.recalibrate_live_web import recalibrate
    from shoppingbench_foundry import web_search_rft_grading_v6 as v6

    monkeypatch.setenv("RFT_GRADER_MODEL_DEPLOYMENT_NAME", "test-judge")
    monkeypatch.setenv("WEB_SEARCH_OPENAI_BASE_URL", "https://example")
    invocations = []

    def assess(value, item):
        invocations.append(value)
        return FULL

    monkeypatch.setattr(grader, "assess_grounding", assess)
    item = {**ITEM, "case_name": "case", "case_group": "group"}
    data = tmp_path / "data.jsonl"
    data.write_text(json.dumps(item) + "\n", encoding="utf-8")
    values = []
    for index in range(1, 4):
        value = sample("27 \u00d7 40")
        if index == 3:
            value["output_tools"].insert(1, copy.deepcopy(value["output_tools"][0]))
        values.append({
            "name": f"case-rollout-{index}", "case_name": "case", "sample": value,
            "usage": {"total_tokens": 100}, "latency_seconds": 1,
        })
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in values), encoding="utf-8")
    report = recalibrate(results, data, tmp_path / "v6", 1)
    assert report["quality_passes"] == 3
    assert report["score_passes"] == 2
    assert report["efficiency_only_failures"] == 1
    assert report["grader_sha256"] == v6.grader_sha256()
    assert recalibrate(results, data, tmp_path / "v6", 1) == report
    assert len(invocations) == 3


def test_recalibration_reuses_bound_assessments_without_new_judge_calls(tmp_path, monkeypatch):
    from rft.scripts.recalibrate_live_web import recalibrate

    monkeypatch.setenv("RFT_GRADER_MODEL_DEPLOYMENT_NAME", "test-judge")
    monkeypatch.setenv("WEB_SEARCH_OPENAI_BASE_URL", "https://example.openai.azure.com/openai/v1")
    calls = []

    def assess(value, item):
        calls.append(value)
        return FULL

    monkeypatch.setattr(grader, "assess_grounding", assess)
    item = {**ITEM, "case_name": "case", "case_group": "group"}
    data = tmp_path / "data.jsonl"
    data.write_text(json.dumps(item) + "\n", encoding="utf-8")
    results = tmp_path / "results.jsonl"
    rows = [
        {"name": f"case-rollout-{index}", "case_name": "case", "sample": sample(),
         "usage": {"total_tokens": 100}, "latency_seconds": 1}
        for index in range(1, 4)
    ]
    results.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    first = recalibrate(results, data, tmp_path / "report", workers=1)
    assert first["quality_passes"] == 3
    assert len(calls) == 3
    second = recalibrate(results, data, tmp_path / "report", workers=1)
    assert first == second
    assert len(calls) == 3
    rows.pop()
    results.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly three"):
        recalibrate(results, data, tmp_path / "report", workers=1)


def test_recalibration_recovers_throttling_without_changing_the_reward(tmp_path, monkeypatch):
    import httpx
    from openai import RateLimitError

    from rft.scripts.recalibrate_live_web import recalibrate

    monkeypatch.setenv("RFT_GRADER_MODEL_DEPLOYMENT_NAME", "test-judge")
    monkeypatch.setenv("WEB_SEARCH_OPENAI_BASE_URL", "https://example.openai.azure.com/openai/v1")
    attempts = []
    delays = []

    def assess(value, item):
        attempts.append(value)
        if len(attempts) == 1:
            raise RateLimitError(
                "throttled",
                response=httpx.Response(
                    429, headers={"retry-after": "30"}, request=httpx.Request("POST", "https://test"),
                ),
                body=None,
            )
        return FULL

    monkeypatch.setattr(grader, "assess_grounding", assess)
    monkeypatch.setattr("rft.scripts.recalibrate_live_web.time.sleep", delays.append)
    data = tmp_path / "data.jsonl"
    data.write_text(
        json.dumps({**ITEM, "case_name": "case", "case_group": "group"}) + "\n", encoding="utf-8",
    )
    results = tmp_path / "results.jsonl"
    rows = [
        {"name": f"case-rollout-{index}", "case_name": "case", "sample": sample(),
         "usage": {"total_tokens": 100}, "latency_seconds": 1}
        for index in range(1, 4)
    ]
    results.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    report = recalibrate(results, data, tmp_path / "report", workers=1)
    assert delays == [30]
    assert report["rate_limit_retries_this_run"] == 1
    assert report["quality_passes"] == 3
