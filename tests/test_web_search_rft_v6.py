import pytest
from test_web_search_rft_v5 import FULL, ITEM, sample

from shoppingbench_foundry import web_search_rft_grading_v5 as v5
from shoppingbench_foundry import web_search_rft_grading_v6 as v6


def details(value, assessment=FULL):
    return v6.grade_with_details(value, ITEM, assessment=assessment)


def with_searches(count):
    value = sample()
    for page in range(2, count + 1):
        value["output_tools"].insert(
            1, {"name": "find_product", "arguments": {"q": "27 x 40 wall frame", "page": page}},
        )
    return value


def test_v6_reuses_v5_judge_and_quality():
    assert v6.RUBRIC == v5.RUBRIC
    assert v6.assess_grounding is v5.assess_grounding
    assert details(sample())["score"] == details(sample())["v5_score"] == 1


def test_each_call_beyond_minimal_path_costs_more_than_v5():
    scores = [details(with_searches(n))["score"] for n in (1, 2, 3, 4)]
    assert scores == [1, 0.92, 0.84, 0.76]
    assert scores[1] > v6.PASS_THRESHOLD > scores[2]


def test_verbose_answers_are_penalized_but_never_below_failed_quality():
    value = sample()
    terminal = value["output_tools"][-1]["arguments"]
    terminal["final_answer"] += " Extra detail." * 20
    result = details(value)
    assert result["answer_words"] == 51
    assert result["answer_verbosity_cost"] == pytest.approx(0.105)
    assert result["score"] == pytest.approx(0.895)
    terminal["final_answer"] += " More padding words here." * 100
    costly = with_searches(4)
    costly["output_tools"][-1] = value["output_tools"][-1]
    result = details(costly)
    assert result["quality_pass"]
    assert result["answer_verbosity_cost"] == v6.MAX_ANSWER_COST
    assert result["score"] == pytest.approx(0.64)
    partial = FULL.model_copy(update={"product_faithfulness": "partial"})
    assert result["score"] > details(sample(), partial)["score"]


def test_v6_route_is_registered(monkeypatch):
    from fastapi.testclient import TestClient

    from shoppingbench_foundry.app import app

    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    monkeypatch.setattr(
        "shoppingbench_foundry.app.web_search_v6_endpoint_grade", lambda payload: {"score": 0.5},
    )
    response = TestClient(app).post(
        "/grade/web-search/v6", json={"sample": {}, "item": {}},
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.json() == {"score": 0.5}
