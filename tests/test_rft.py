import json
from pathlib import Path

from evaluations.graders.rft_grader import grade
from rft.scripts.prepare_data import prepare_task
from rft.scripts.submit import (
    _private_preview_payload,
    _private_preview_url,
    _recipe_from_job,
    _validate_agentic_dataset,
)


def test_rft_grader_rewards_exact_order_and_complete_process():
    sample = {
        "output_tools": [
            {"function": {"name": "find_product", "arguments": {"q": "item", "page": 1}}},
            {
                "function": {
                    "name": "view_product_information",
                    "arguments": {"product_ids": "p1,p2"},
                }
            },
            {"function": {"name": "recommend_product", "arguments": {"product_ids": "p1,p2"}}},
            {"function": {"name": "terminate", "arguments": {}}},
        ]
    }
    item = {"reward": [{"product_id": "p1"}, {"product_id": "p2"}], "max_search_calls": 6}
    assert grade(sample, item) == 1.0

    reversed_sample = json.loads(json.dumps(sample))
    reversed_sample["output_tools"][2]["function"]["arguments"]["product_ids"] = "p2,p1"
    assert grade(reversed_sample, item) < 0.5

    missing_termination = json.loads(json.dumps(sample))
    missing_termination["output_tools"].pop()
    assert grade(missing_termination, item) == 0.9


def test_prepare_task_excludes_holdout_and_uses_developer_message(tmp_path: Path):
    data_dir = tmp_path / "data"
    evals_dir = tmp_path / "evals"
    config_root = tmp_path / "configs"
    output_dir = tmp_path / "output"
    data_dir.mkdir()
    evals_dir.mkdir()
    config = config_root / "web" / "config"
    (config / "skills" / "knowledge-shopping").mkdir(parents=True)
    (config / "instructions.md").write_text("Base instructions", encoding="utf-8")
    (config / "skills" / "knowledge-shopping" / "SKILL.md").write_text(
        "Skill instructions", encoding="utf-8"
    )
    rows = [
        {
            "query": f"query-{index}",
            "reward": {"product_id": f"p{index}"},
            "Knowledge_Attribute": f"fact-{index}",
        }
        for index in range(71)
    ]
    (data_dir / "web.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    (evals_dir / "web-holdout.jsonl").write_text(
        json.dumps({"name": "web-000"}) + "\n",
        encoding="utf-8",
    )

    summary = prepare_task(data_dir, evals_dir, config_root, output_dir, "web")
    assert summary["validation"] == 20
    assert summary["train"] == 50
    first = json.loads((output_dir / "web-train.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert [message["role"] for message in first["messages"]] == ["developer", "user"]
    assert "Skill instructions" in first["messages"][0]["content"]
    assert first["messages"][-1]["content"] != "query-0"
    assert [tool["function"]["name"] for tool in first["tools"]] == [
        "find_product",
        "view_product_information",
        "recommend_product",
        "terminate",
    ]
    _validate_agentic_dataset(output_dir / "web-train.jsonl")


def test_agentic_dataset_validation_rejects_missing_tool_schemas(tmp_path: Path):
    path = tmp_path / "invalid.jsonl"
    path.write_text(
        json.dumps({"messages": [{"role": "user", "content": "Find an item"}]}) + "\n",
        encoding="utf-8",
    )

    try:
        _validate_agentic_dataset(path)
    except TypeError as exc:
        assert "missing per-example tools" in str(exc)
    else:
        raise AssertionError("Dataset without tool schemas should be rejected")


def test_private_preview_payload_matches_blossom_contract(monkeypatch):
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_BASE_URL", "https://tools.example")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")
    payload = _private_preview_payload(
        model="MAI-Code-1.1-Flash",
        training_file_id="file-train",
        validation_file_id="file-validation",
        task="web",
        training_type="GlobalStandard",
        threshold=1.0,
    )

    creation = payload["fineTuningJobCreation"]
    reinforcement = creation["method"]["reinforcement"]
    assert payload["fineTuningJobType"] == "fineTuning"
    assert creation["model"] == "MAI-Code-1.1-Flash"
    assert creation["trainingType"] == "GlobalStandard"
    assert creation["suffix"] == "mai-sb-web-rft2"
    assert reinforcement["grader"]["type"] == "endpoint"
    assert reinforcement["grader"]["pass_threshold"] == 1.0
    assert [tool["name"] for tool in reinforcement["tools"]] == [
        "find_product",
        "view_product_information",
        "recommend_product",
        "terminate",
    ]
    assert "pass_threshold" not in reinforcement
    assert reinforcement["hyperparameters"]["number_of_epochs"] == 1
    assert payload["execution_config"] == {
        "type": "blossom",
        "blossom": {
            "recipe": {
                "name": "mai-code-1-flash",
                "version": 11,
            }
        },
    }


def test_private_preview_url_and_recipe_confirmation_helpers(monkeypatch):
    monkeypatch.setenv(
        "RFT_PRIVATE_PREVIEW_JOBS_URL",
        "https://resource.openai.azure.com/openai/1p/jobs?api-version=2025-04-01-preview",
    )
    assert _private_preview_url().endswith("api-version=2025-04-01-preview")
    assert _recipe_from_job(
        {"fineTuningJob": {"execution": {"recipe": {"name": "recipe", "version": 11}}}}
    ) == {"name": "recipe", "version": 11}
