import json

from shoppingbench_foundry.web_search_rft_grading_v2 import calculate_reward, endpoint_grade


def _call(name: str, arguments: dict) -> dict:
    return {"type": "function_call", "name": name, "arguments": json.dumps(arguments)}


def _sample(*, searches: list[str] | None = None, product_id: str = "p1") -> dict:
    queries = searches or ["Jason Statham casual watch"]
    output = [_call("web_search", {"query": "Who voiced Sergeant Waters?", "answer": "Jason Statham"})]
    output.extend(_call("find_product", {"q": query, "page": 1}) for query in queries)
    output.extend(
        [
            _call("view_product_information", {"product_ids": product_id}),
            _call("recommend_product", {"product_ids": product_id}),
            _call(
                "terminate",
                {
                    "product_ids": product_id,
                    "resolved_clue": "Jason Statham",
                    "final_answer": (
                        f"The clue resolves to Jason Statham; product {product_id} matches."
                    ),
                },
            ),
        ]
    )
    return {"output": output}


ITEM = {"reward": {"product_id": "p1", "knowledge_attribute": "Jason Statham"}}


def test_web_search_rft_v2_rewards_ideal_trajectory():
    assert calculate_reward(_sample(), ITEM) == 1.0
    assert endpoint_grade({"sample": _sample(), "item": ITEM}) == {"score": 1.0}


def test_web_search_rft_v2_fails_wrong_product_and_missing_gates():
    assert calculate_reward(_sample(product_id="wrong"), ITEM) < 0.5

    missing_inspection = _sample()
    missing_inspection["output"].pop(-3)
    assert calculate_reward(missing_inspection, ITEM) < 0.9


def test_web_search_rft_v2_fails_repeated_or_over_budget_searches():
    repeated = _sample(
        searches=[
            "Jason Statham casual watch",
            "casual watch Jason Statham",
        ]
    )
    assert calculate_reward(repeated, ITEM) < 0.9

    over_budget = _sample(
        searches=[
            "Jason Statham watch",
            "actor casual wristwatch",
            "Sergeant Waters fashionable watch",
        ]
    )
    assert calculate_reward(over_budget, ITEM) < 0.9
