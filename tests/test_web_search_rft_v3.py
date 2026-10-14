import copy
import json

import pytest

from shoppingbench_foundry.web_search_rft_grading_v3 import grade, grade_with_details

ITEM = {
    "task": "web",
    "query": "I want a ceramic mug featuring the actor who voiced Sergeant Waters.",
    "reward": {"product_id": "1234567890", "title": "Jason Statham Ceramic Mug"},
    "knowledge_attribute": "Jason Statham",
}


def sample(searches=1, product_id="1234567890"):
    calls = [
        {"name": "find_product", "arguments": {"q": f"Jason Statham mug {i}", "page": 1}}
        for i in range(searches)
    ]
    calls += [
        {"name": "view_product_information", "arguments": {"product_ids": product_id}},
        {"name": "recommend_product", "arguments": {"product_ids": product_id}},
        {
            "name": "terminate",
            "arguments": {
                "product_ids": product_id,
                "resolved_clue": "Jason Statham",
                "final_answer": f"Jason Statham is the actor. Ceramic mug {product_id} matches.",
            },
        },
    ]
    return {"output_tools": [{"function": call} for call in calls]}


def test_preserves_accuracy_priority_without_an_efficiency_cliff():
    assert grade(sample(), ITEM) == 1.0
    assert grade(sample(2), ITEM) == 1.0
    assert grade(sample(3), ITEM) == 0.95
    assert grade(sample(7), ITEM) == 0.75
    assert grade(sample(7), ITEM) > grade(sample(product_id="placeholder"), ITEM)


def test_partial_progress_and_bad_terminal_ids_receive_distinct_rewards():
    complete = sample()
    missing_view = copy.deepcopy(complete)
    missing_view["output_tools"].pop(1)
    missing_terminal = copy.deepcopy(complete)
    missing_terminal["output_tools"].pop()
    wrong_terminal = copy.deepcopy(complete)
    wrong_terminal["output_tools"][-1]["function"]["arguments"]["product_ids"] = "0"
    assert grade(complete, ITEM) > grade(missing_view, ITEM) > grade(wrong_terminal, ITEM)
    assert grade(wrong_terminal, ITEM) > grade(missing_terminal, ITEM)
    assert grade(missing_terminal, ITEM) > grade(sample(product_id="0"), ITEM)


def test_repeated_queries_normalize_defaults_but_do_not_penalize_pagination():
    duplicate = sample(2)
    duplicate["output_tools"][0]["function"]["arguments"] = {
        "q": "Jason Statham mug", "page": 1,
    }
    duplicate["output_tools"][1]["function"]["arguments"] = {
        "q": "  jason   statham MUG ", "page": 1, "sort": "default", "shop_id": "",
    }
    assert grade_with_details(duplicate, ITEM)["repeated_searches"] == 1
    assert grade(duplicate, ITEM) == 0.975
    duplicate["output_tools"][1]["function"]["arguments"]["page"] = 2
    assert grade_with_details(duplicate, ITEM)["repeated_searches"] == 0
    assert grade(duplicate, ITEM) == 1.0


@pytest.mark.parametrize("product_id", ["0", "placeholder", "1234567890,0"])
def test_wrong_or_extra_product_ids_cannot_pass(product_id):
    assert grade(sample(product_id=product_id), ITEM) <= 0.30


def test_does_not_trust_self_reported_web_evidence_or_short_substring_clues():
    value = sample()
    value["web_evidence"] = "Jason Statham"
    terminal = value["output_tools"][-1]["function"]["arguments"]
    terminal["resolved_clue"] = "Jason"
    terminal["final_answer"] = "Jason appears on ceramic mug 1234567890."
    details = grade_with_details(value, ITEM)
    assert not details["components"]["correct_clue"]
    assert not details["components"]["grounded_final_answer"]
    assert details["score"] == 0.8


def test_wrong_resolved_clue_cannot_be_hidden_in_an_otherwise_correct_final_answer():
    value = sample()
    value["output_tools"][-1]["function"]["arguments"]["resolved_clue"] = "Harrison Ford"
    details = grade_with_details(value, ITEM)
    assert not details["components"]["grounded_final_answer"]
    assert details["score"] == 0.8


def test_accepts_serialized_arguments_without_changing_scores():
    value = sample()
    serialized = copy.deepcopy(value)
    for call in serialized["output_tools"]:
        function = call["function"]
        function["arguments"] = json.dumps(function["arguments"])
    assert grade(value, ITEM) == grade(serialized, ITEM)


def test_accepts_foundry_response_and_chat_trajectory_formats():
    value = sample()
    response = {
        "output": [
            {
                "type": "function_call",
                "name": call["function"]["name"],
                "arguments": json.dumps(call["function"]["arguments"]),
            }
            for call in value["output_tools"]
        ],
    }
    chat = {
        "messages": [
            {"role": "assistant", "tool_calls": [call]} for call in value["output_tools"]
        ],
    }
    assert grade(value, ITEM) == grade(response, ITEM) == grade(chat, ITEM) == 1.0


def test_second_recommendation_is_not_hidden_by_a_correct_final_choice():
    value = sample()
    value["output_tools"].insert(
        2, {"function": {"name": "recommend_product", "arguments": {"product_ids": "0"}}},
    )
    assert grade(value, ITEM) < 0.30
