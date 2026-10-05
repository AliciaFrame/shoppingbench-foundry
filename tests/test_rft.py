import json
from pathlib import Path

from evaluations.graders.rft_grader import grade
from evaluations.graders.rft_grader_v2 import grade as grade_v2
from evaluations.graders.rft_grader_v2 import grade_with_details as grade_v2_with_details
from evaluations.graders.rft_grader_v3 import grade as grade_v3
from evaluations.graders.rft_grader_v3 import grade_with_details as grade_v3_with_details
from rft.scripts.calibrate import calibrate
from rft.scripts.monitor import (
    COGNITIVE_SERVICES_TOKEN_SCOPE,
    FOUNDRY_TOKEN_SCOPE,
    _token_scope,
)
from rft.scripts.prepare_data import _generated_files, _portable_path, prepare_task
from rft.scripts.submit import (
    _endpoint_grader,
    _private_preview_payload,
    _private_preview_url,
    _recipe_from_job,
    _validate_agentic_dataset,
)
from shoppingbench_foundry.benchmark_subset import write_eval_datasets_v2


def test_monitor_uses_foundry_token_scope():
    assert (
        _token_scope(
            "https://resource.services.ai.azure.com/api/projects/project/openai/v1/"
        )
        == FOUNDRY_TOKEN_SCOPE
    )
    assert (
        _token_scope("https://resource.openai.azure.com/openai/v1/")
        == COGNITIVE_SERVICES_TOKEN_SCOPE
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


def test_web_rft_grader_v2_rewards_correct_efficient_trajectory_without_keyword_stuffing():
    sample = {
        "output_tools": [
            {
                "function": {
                    "name": "find_product",
                    "arguments": {"q": "matching replacement part", "page": 1},
                }
            },
            {
                "function": {
                    "name": "view_product_information",
                    "arguments": {"product_ids": "p1"},
                }
            },
            {"function": {"name": "recommend_product", "arguments": {"product_ids": "p1"}}},
            {"function": {"name": "terminate", "arguments": {}}},
        ]
    }
    item = {
        "task": "web",
        "reward": {"product_id": "p1"},
        "knowledge_attribute": "specific fact",
        "max_search_calls": 3,
    }

    assert grade_v2(sample, item) == 0.9

    early_grounded = json.loads(json.dumps(sample))
    early_grounded["output_tools"][0]["function"]["arguments"]["q"] = (
        "specific fact matching replacement part"
    )
    assert grade_v2(early_grounded, item) == 1.0


def test_web_rft_grader_v2_penalizes_search_bloat_and_keyword_stuffing():
    searches = [
        {
            "function": {
                "name": "find_product",
                "arguments": {"q": f"broad search {index}", "page": 1},
            }
        }
        for index in range(5)
    ]
    searches[-1]["function"]["arguments"]["q"] = "specific fact"
    sample = {
        "output_tools": searches
        + [
            {
                "function": {
                    "name": "view_product_information",
                    "arguments": {"product_ids": "p1"},
                }
            },
            {"function": {"name": "recommend_product", "arguments": {"product_ids": "p1"}}},
            {"function": {"name": "terminate", "arguments": {}}},
        ]
    }
    item = {
        "task": "web",
        "reward": {"product_id": "p1"},
        "knowledge_attribute": "specific fact",
        "max_search_calls": 3,
    }

    details = grade_v2_with_details(sample, item)
    assert details["early_knowledge"] is False
    assert details["excess_searches"] == 2
    assert details["score"] == 0.79

    wrong_product = json.loads(json.dumps(sample))
    wrong_product["output_tools"][-2]["function"]["arguments"]["product_ids"] = "wrong"
    assert grade_v2(wrong_product, item) < 0.25


def test_rft_v3_requires_bound_views_and_final_answer():
    item = {
        "task": "voucher",
        "reward": [{"product_id": "p1"}, {"product_id": "p2"}],
        "voucher": {
            "voucher_type": "shop",
            "threshold": 600,
            "discount_type": "fixed",
            "face_value": 100,
            "price_after_voucher": 600,
        },
        "max_search_calls": 6,
    }
    sample = {
        "output_tools": [
            {"function": {"name": "find_product", "arguments": {"q": "p1 p2", "page": 1}}},
            {
                "function": {
                    "name": "view_product_information",
                    "arguments": {"product_ids": "p1,p2"},
                }
            },
            {"function": {"name": "recommend_product", "arguments": {"product_ids": "p1,p2"}}},
            {"function": {"name": "terminate", "arguments": {}}},
        ],
        "assistant_text": (
            "p1 and p2 are from one shop. Subtotal 700 meets the 600 threshold. "
            "The voucher discount gives 600 within budget."
        ),
    }
    assert grade_v3(sample, item) == 1.0

    dummy_view = json.loads(json.dumps(sample))
    dummy_view["output_tools"][1]["function"]["arguments"]["product_ids"] = "wrong"
    assert grade_v3(dummy_view, item) == 0.92

    empty_answer = json.loads(json.dumps(sample))
    empty_answer["assistant_text"] = ""
    assert grade_v3(empty_answer, item) == 0.9
    assert grade_v3_with_details(empty_answer, item)["valid_final_answer"] is False


def test_web_rft_v3_requires_grounded_explanation():
    item = {
        "task": "web",
        "reward": {"product_id": "p1"},
        "knowledge_attribute": "violin",
        "max_search_calls": 3,
    }
    sample = {
        "output_tools": [
            {
                "function": {
                    "name": "find_product",
                    "arguments": {"q": "violin bow", "page": 1},
                }
            },
            {
                "function": {
                    "name": "view_product_information",
                    "arguments": {"product_ids": "p1"},
                }
            },
            {"function": {"name": "recommend_product", "arguments": {"product_ids": "p1"}}},
            {"function": {"name": "terminate", "arguments": {}}},
        ],
        "assistant_text": "The clue resolves to violin, so I recommend p1.",
    }
    assert grade_v3(sample, item) == 1.0

    ungrounded = json.loads(json.dumps(sample))
    ungrounded["assistant_text"] = "I recommend p1."
    assert grade_v3(ungrounded, item) == 0.9

    wrong = json.loads(json.dumps(sample))
    wrong["output_tools"][1]["function"]["arguments"]["product_ids"] = "wrong"
    wrong["output_tools"][2]["function"]["arguments"]["product_ids"] = "wrong"
    assert grade_v3(wrong, item) <= 0.3


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


def test_prepare_task_uses_group_disjoint_v2_manifest(tmp_path: Path):
    repo_root = Path(__file__).parents[1]
    data_dir = repo_root / "data" / "source"
    evals_dir = tmp_path / "evals"
    output_dir = tmp_path / "rft"
    manifest = write_eval_datasets_v2(data_dir, evals_dir)

    summary = prepare_task(
        data_dir,
        evals_dir,
        repo_root / "agents",
        output_dir,
        "web",
        evals_dir / "split-manifest.json",
    )

    with (output_dir / "web-train.jsonl").open(encoding="utf-8") as handle:
        train = {json.loads(line)["case_name"] for line in handle if line.strip()}
    with (output_dir / "web-validation.jsonl").open(encoding="utf-8") as handle:
        validation = {json.loads(line)["case_name"] for line in handle if line.strip()}
    final_test = set(manifest["tasks"]["web"]["cases"]["final-test"])
    case_groups = manifest["tasks"]["web"]["case_groups"]

    assert train.isdisjoint(validation)
    assert train.isdisjoint(final_test)
    assert validation.isdisjoint(final_test)
    assert {case_groups[name] for name in train}.isdisjoint(
        {case_groups[name] for name in validation}
    )
    assert summary["calibration_rollouts"] == summary["validation"] * 5


def test_calibration_requires_repeated_case_rollouts(tmp_path: Path):
    dataset = tmp_path / "dataset.jsonl"
    results = tmp_path / "results.jsonl"
    dataset_rows = []
    result_rows = []
    for case in range(20):
        for rollout in range(3):
            name = f"case-{case}-rollout-{rollout}"
            dataset_rows.append(
                {
                    "name": name,
                    "item": {"case_name": f"case-{case}"},
                }
            )
            result_rows.append(
                {
                    "name": name,
                    "sample": {"score": 0.8 if case < 7 else 1.0},
                }
            )
    dataset.write_text(
        "".join(json.dumps(row) + "\n" for row in dataset_rows),
        encoding="utf-8",
    )
    results.write_text(
        "".join(json.dumps(row) + "\n" for row in result_rows),
        encoding="utf-8",
    )

    summary = calibrate(
        results,
        dataset,
        grader=lambda sample, item: float(sample["score"]),
    )

    assert summary["samples"] == 60
    assert summary["unique_cases"] == 20
    assert summary["rollouts_per_case"]["min"] == 3
    assert summary["sufficient_samples"] is True
    assert len(summary["base_failure_rate_bootstrap_95"]) == 2


def test_preparation_manifest_uses_only_selected_task_outputs(tmp_path: Path):
    output_dir = tmp_path / "rft" / "data"
    output_dir.mkdir(parents=True)
    for suffix in ("train", "validation", "validation-eval", "calibration-eval"):
        (output_dir / f"web-{suffix}.jsonl").write_text("{}\n", encoding="utf-8")
    (output_dir / "stale.jsonl").write_text("{}\n", encoding="utf-8")

    generated = _generated_files(output_dir, ["web"])

    assert [path.name for path in generated] == [
        "web-calibration-eval.jsonl",
        "web-train.jsonl",
        "web-validation-eval.jsonl",
        "web-validation.jsonl",
    ]
    assert _portable_path(output_dir / "web-train.jsonl", tmp_path) == (
        "rft/data/web-train.jsonl"
    )


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
        n_epochs=3,
        batch_size=4,
        learning_rate_multiplier=0.5,
        eval_interval=7,
        eval_samples=2,
        max_episode_steps=9,
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
    assert reinforcement["max_episode_steps"] == 9
    assert reinforcement["hyperparameters"] == {
        "eval_interval": 7,
        "eval_samples": 2,
        "compute_multiplier": 1.0,
        "learning_rate_multiplier": 0.5,
        "reasoning_effort": "medium",
        "number_of_epochs": 3,
        "batch_size": 4,
    }
    assert payload["execution_config"] == {
        "type": "blossom",
        "blossom": {
            "recipe": {
                "name": "mai-code-1-flash",
                "version": 11,
            }
        },
    }


def test_v2_grader_submission_uses_isolated_route_and_suffix(monkeypatch):
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_BASE_URL", "https://tools.example")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")

    grader = _endpoint_grader("web", 0.9, "v2")
    payload = _private_preview_payload(
        model="MAI-Code-1.1-Flash",
        training_file_id="file-train",
        validation_file_id="file-validation",
        task="web",
        training_type="GlobalStandard",
        threshold=0.9,
        grader_version="v2",
    )

    assert grader["url"] == "https://tools.example/grade/v2"
    assert grader["name"] == "shoppingbench_web_v2"
    assert payload["fineTuningJobCreation"]["suffix"] == "mai-sb-web-rft3"


def test_v3_grader_submission_uses_hardened_route_and_new_suffix(monkeypatch):
    monkeypatch.setenv("SHOPPINGBENCH_TOOL_BASE_URL", "https://tools.example")
    monkeypatch.setenv("SHOPPINGBENCH_API_TOKEN", "test-token")

    grader = _endpoint_grader("voucher", 0.95, "v3")
    payload = _private_preview_payload(
        model="MAI-Code-1.1-Flash",
        training_file_id="file-train",
        validation_file_id="file-validation",
        task="voucher",
        training_type="GlobalStandard",
        threshold=0.95,
        grader_version="v3",
    )

    assert grader["url"] == "https://tools.example/grade/v3"
    assert grader["name"] == "shoppingbench_voucher_v3"
    assert payload["fineTuningJobCreation"]["suffix"] == "mai-sb-vouch-rft2"
    assert payload["fineTuningJobCreation"]["method"]["reinforcement"]["hyperparameters"][
        "eval_samples"
    ] == 10


def test_private_preview_url_and_recipe_confirmation_helpers(monkeypatch):
    monkeypatch.setenv(
        "RFT_PRIVATE_PREVIEW_JOBS_URL",
        "https://resource.openai.azure.com/openai/1p/jobs?api-version=2025-04-01-preview",
    )
    assert _private_preview_url().endswith("api-version=2025-04-01-preview")
    assert _recipe_from_job(
        {"fineTuningJob": {"execution": {"recipe": {"name": "recipe", "version": 11}}}}
    ) == {"name": "recipe", "version": 11}
