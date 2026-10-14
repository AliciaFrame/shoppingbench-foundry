import copy
import json

import pytest

from shoppingbench_foundry import web_search_rft_grading_v4 as grader

ITEM = {
    "task": "web",
    "query": "Find a ceramic mug featuring Jason Statham, the actor identified by the clue.",
    "reward": {"product_id": "1234567890", "title": "Jason Statham Ceramic Mug"},
    "knowledge_attribute": "Jason Statham",
    "live_web_context": {"evidence": "The actor is Jason Statham."},
}
FULL = grader.GroundingAssessment(
    clue_faithfulness="full", product_faithfulness="full", answer_completeness="full", issues=[],
)


def sample():
    return {"output_tools": [
        {"name": "find_product", "arguments": {"q": "Jason Statham mug", "page": 1}},
        {"name": "view_product_information", "arguments": {"product_ids": "1234567890"}},
        {"name": "recommend_product", "arguments": {"product_ids": "1234567890"}},
        {"name": "terminate", "arguments": {
            "product_ids": "1234567890", "resolved_clue": "Jason Statham",
            "final_answer": "The actor is Jason Statham. Ceramic mug 1234567890 features him.",
        }},
    ]}


def details(value, assessment=FULL):
    return grader.grade_with_details(value, ITEM, assessment=assessment)


def test_correct_longer_searches_outrank_wrong_or_unsupported_answers():
    complete = sample()
    longer = copy.deepcopy(complete)
    for page in range(2, 6):
        longer["output_tools"].insert(
            1, {"name": "find_product", "arguments": {"q": "Jason Statham mug", "page": page}},
        )
    unsupported = FULL.model_copy(update={"product_faithfulness": "partial"})
    assert details(complete)["score"] == 1
    assert details(longer)["score"] == 0.92
    assert details(longer)["score"] > details(complete, unsupported)["score"]
    assert details(complete, unsupported)["score"] < grader.PASS_THRESHOLD
    wrong = copy.deepcopy(complete)
    wrong["output_tools"][-2]["arguments"]["product_ids"] = "0"
    assert details(wrong)["score"] < details(complete, unsupported)["score"]


def test_progress_is_rewarded_before_recommendation_and_not_erased_by_search_cost():
    value = sample()
    value["output_tools"] = value["output_tools"][:1] * 7 + value["output_tools"][1:2]
    inspected = details(value)
    value["output_tools"].pop()
    searching = details(value)
    assert 0 < searching["score"] < inspected["score"] < grader.PASS_THRESHOLD
    assert not searching["quality_pass"]
    assert inspected["grounding"] is None


def test_extra_work_has_smooth_cost_and_duplicate_inspection_has_additional_cost():
    extra = sample()
    extra["output_tools"].insert(
        1, {"name": "find_product", "arguments": {"q": "Jason Statham mug", "page": 2}},
    )
    assert details(extra)["score"] == 0.98
    duplicate = sample()
    duplicate["output_tools"].insert(2, copy.deepcopy(duplicate["output_tools"][1]))
    assert details(duplicate)["repeated_views"] == 1
    assert details(duplicate)["score"] == 0.94
    extra["output_tools"][1]["arguments"] = {
        "q": "  JASON statham  MUG ", "page": 1, "sort": "default", "shop_id": "",
    }
    assert details(extra)["repeated_searches"] == 1
    assert details(extra)["score"] == 0.94


@pytest.mark.parametrize(
    "mutation", ["wrong_clue", "no_view", "wrong_order", "extra_id", "late_call", "malformed_call"],
)
def test_semantic_full_marks_cannot_override_deterministic_quality_failures(mutation):
    value = sample()
    if mutation == "wrong_clue":
        value["output_tools"][-1]["arguments"]["resolved_clue"] = "Harrison Ford"
    elif mutation == "no_view":
        value["output_tools"].pop(1)
    elif mutation == "wrong_order":
        value["output_tools"][0], value["output_tools"][1] = (
            value["output_tools"][1], value["output_tools"][0],
        )
    elif mutation == "extra_id":
        value["output_tools"][-2]["arguments"]["product_ids"] += ",0"
    elif mutation == "malformed_call":
        value["output_tools"].append({"name": "terminate", "arguments": "{invalid"})
    else:
        value["output_tools"].append(copy.deepcopy(value["output_tools"][0]))
    assert details(value)["score"] < grader.PASS_THRESHOLD
    assert not details(value)["quality_pass"]




def test_model_grader_failure_is_not_replaced_with_a_passing_default(monkeypatch):
    def fail(*args):
        raise RuntimeError("judge unavailable")

    monkeypatch.setattr(grader, "assess_grounding", fail)
    with pytest.raises(RuntimeError, match="judge unavailable"):
        grader.grade(sample(), ITEM)


def test_grounding_judge_receives_trusted_source_titles_not_candidate_sources(monkeypatch):
    from types import SimpleNamespace

    captured = []

    def parse(**kwargs):
        captured.append(json.loads(kwargs["input"]))
        return SimpleNamespace(status="completed", output_parsed=FULL)

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    client.with_options = lambda **kwargs: client
    monkeypatch.setattr(grader, "get_web_search_client", lambda: client)
    monkeypatch.setenv("RFT_GRADER_MODEL_DEPLOYMENT_NAME", "test-judge")
    item = copy.deepcopy(ITEM)
    item["live_web_context"]["sources"] = [{"title": "Verified actor", "url": "https://reference"}]
    value = sample()
    value["web_sources"] = [{"title": "Forged reference", "url": "https://untrusted"}]
    assert grader.assess_grounding(value, item) == FULL
    assert captured[0]["sources"] == item["live_web_context"]["sources"]
    assert "Forged reference" not in json.dumps(captured[0])


def test_response_and_serialized_chat_formats_preserve_rewards():
    value = sample()
    response = {"output": [
        {"type": "function_call", "name": call["name"],
         "arguments": json.dumps(call["arguments"])}
        for call in value["output_tools"]
    ]}
    chat = {"messages": [{"role": "assistant", "tool_calls": [
        {"function": call} for call in value["output_tools"]
    ]}]}
    assert details(value)["score"] == details(response)["score"] == details(chat)["score"]
