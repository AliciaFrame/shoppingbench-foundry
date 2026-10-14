import json
from argparse import Namespace
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from evals.scripts import evaluate_agent as evaluator
from evals.scripts import score_live_web as scorer


class FakeClient:
    base_url = "https://example.test/openai/v1"

    def __init__(self, create):
        self.create = create
        self.responses = SimpleNamespace(create=create)

    def with_options(self, **kwargs):
        assert kwargs == {"max_retries": 0}
        return FakeClient(self.create)


def response():
    return SimpleNamespace(
        model="test-model",
        usage=SimpleNamespace(
            model_dump=lambda **kwargs: {
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
            },
        ),
    )


def test_failed_episode_keeps_usage_elapsed_time_and_is_not_resampled(monkeypatch):
    calls = []
    client = FakeClient(lambda **kwargs: response())
    runtime = SimpleNamespace(model_client=client)

    def episode(query):
        calls.append(query)
        runtime.model_client.responses.create(model="test-model", input=query)
        raise RuntimeError("Tool sequence did not complete")

    runtime._run_episode = episode
    monkeypatch.setattr(evaluator, "runtime", runtime)
    evaluator._instrument_runtime()
    result = evaluator._evaluate({"name": "case", "query": "request", "item": {}}, None)

    assert calls == ["request"]
    assert result["grade"]["score"] == 0
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    assert result["usage_complete"]
    assert result["latency_seconds"] > 0
    assert "Tool sequence did not complete" in result["error"]


def test_transport_retry_retries_only_the_request_not_the_episode(monkeypatch):
    requests = []
    episodes = []

    def create(**kwargs):
        requests.append(kwargs["input"])
        if len(requests) == 2:
            raise APIConnectionError(request=httpx.Request("POST", "https://example.test"))
        return response()

    runtime = SimpleNamespace(model_client=FakeClient(create))

    def episode(query):
        episodes.append(query)
        runtime.model_client.responses.create(input="first")
        runtime.model_client.responses.create(input="second")
        return "{}", {}

    runtime._run_episode = episode
    monkeypatch.setattr(evaluator, "runtime", runtime)
    monkeypatch.setattr(evaluator.time, "sleep", lambda delay: None)
    monkeypatch.setattr(evaluator, "grade_sample", lambda *args: {"score": 1})
    evaluator._instrument_runtime()
    result = evaluator._evaluate({"name": "case", "query": "request", "item": {}}, None)

    assert episodes == ["request"]
    assert requests == ["first", "second", "second"]
    assert result["usage"]["total_tokens"] == 24
    assert result["grade"]["score"] == 1
    assert not result["usage_complete"]
    assert len(result["transport_retries"]) == 1


def test_resume_rejects_changed_contract_duplicates_and_legacy_outputs(tmp_path):
    output = tmp_path / "results.jsonl"
    contract = {"model": "first", "dataset": "frozen"}
    assert evaluator._prepare_resume(output, contract, {"case"}) == {}
    output.write_text(
        json.dumps({"name": "case", "error": "RuntimeError", "grade": {"score": 0}}) + "\n",
        encoding="utf-8",
    )
    assert evaluator._prepare_resume(output, contract, {"case"})["case"]["error"] == "RuntimeError"
    with pytest.raises(ValueError, match="different model"):
        evaluator._prepare_resume(output, {**contract, "model": "second"}, {"case"})
    output.write_text(output.read_text() * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        evaluator._prepare_resume(output, contract, {"case"})
    output.with_suffix(".manifest.json").unlink()
    with pytest.raises(ValueError, match="legacy"):
        evaluator._prepare_resume(output, contract, {"case"})


@pytest.mark.parametrize("changed", ["model", "instructions", "dataset", "task"])
def test_resume_binds_actual_model_prompt_and_dataset(monkeypatch, tmp_path, changed):
    dataset = tmp_path / "dataset.jsonl"
    documents = tmp_path / "documents.jsonl"
    module = tmp_path / "runtime.py"
    dataset.write_text("{}\n", encoding="utf-8")
    documents.write_text("{}\n", encoding="utf-8")
    module.write_text("# runtime\n", encoding="utf-8")
    runtime = SimpleNamespace(
        __file__=str(module),
        model="first",
        TASK="product",
        instructions="first prompt",
        TOOLS=[],
        MAX_TOOL_STEPS=12,
        model_client=FakeClient(lambda **kwargs: response()),
    )
    monkeypatch.setattr(evaluator, "runtime", runtime)
    monkeypatch.setenv("EVALUATION_IN_PROCESS_TOOLS", "true")
    args = Namespace(dataset=dataset, documents=documents, runtime_module="test", rollouts=3)
    output = tmp_path / "results.jsonl"
    original = evaluator._run_contract(args, 1)
    evaluator._prepare_resume(output, original, {"case"})
    if changed == "dataset":
        dataset.write_text('{"changed":true}\n', encoding="utf-8")
    elif changed == "model":
        runtime.model = "second"
    elif changed == "task":
        runtime.TASK = "voucher"
    else:
        runtime.instructions = "changed prompt"
    with pytest.raises(ValueError, match="different model"):
        evaluator._prepare_resume(output, evaluator._run_contract(args, 1), {"case"})


def test_evaluator_default_retains_failures_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    dataset = tmp_path / "dataset.jsonl"
    documents = tmp_path / "documents.jsonl"
    output = tmp_path / "results.jsonl"
    dataset.write_text(
        json.dumps({"name": "case", "query": "request", "item": {}}) + "\n",
        encoding="utf-8",
    )
    documents.write_text('{"product": {}}\n', encoding="utf-8")
    runtime = SimpleNamespace(
        model_client=FakeClient(lambda **kwargs: response()),
        config=SimpleNamespace(source="test", candidate_id=None),
    )

    def episode(query):
        runtime.model_client.responses.create(input=query)
        raise RuntimeError("Incomplete tool protocol")

    runtime._run_episode = episode
    monkeypatch.setattr(evaluator, "_load_runtime", lambda name: runtime)
    monkeypatch.setattr(evaluator, "_run_contract", lambda *args: {"model": "test-model"})
    monkeypatch.setattr(evaluator, "InMemoryProductStore", lambda products: None)
    monkeypatch.delenv("EVALUATION_IN_PROCESS_TOOLS", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_agent",
            "--dataset",
            str(dataset),
            "--documents",
            str(documents),
            "--output",
            str(output),
            "--workers",
            "1",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        evaluator.main_cli()
    assert exc.value.code == 1
    recorded = json.loads(output.read_text())
    assert recorded["grade"]["score"] == 0
    assert recorded["usage"]["total_tokens"] == 12
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary["completed"] == summary["episodes"] == 1
    assert summary["mean_score"] == 0
    assert len(summary["errors"]) == 1


def write_scoring_inputs(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    results = tmp_path / "results.jsonl"
    dataset.write_text('{"name":"case","query":"request","item":{}}\n', encoding="utf-8")
    row = {
        "name": "case-rollout-1",
        "case_name": "case",
        "query": "request",
        "error": "RuntimeError",
        "grade": {"score": 0},
        "responses": [],
        "tool_results": [],
        "latency_seconds": 1.0,
    }
    results.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return results, dataset


def test_missing_scored_baseline_is_rejected_before_any_judge_call(monkeypatch, tmp_path):
    results, dataset = write_scoring_inputs(tmp_path)
    output = tmp_path / "scored.jsonl"
    called = []
    monkeypatch.setattr(scorer, "score", lambda *args: called.append(args))
    monkeypatch.setattr(
        "sys.argv",
        [
            "score_live_web",
            "--results",
            str(results),
            "--dataset",
            str(dataset),
            "--output",
            str(output),
            "--baseline-details",
            str(tmp_path / "missing.jsonl"),
        ],
    )
    with pytest.raises(ValueError, match="Score the baseline first"):
        scorer.main()
    assert not called
    assert not output.exists()


def test_invalid_later_result_is_rejected_before_scoring_valid_results(monkeypatch, tmp_path):
    results, dataset = write_scoring_inputs(tmp_path)
    valid = json.loads(results.read_text())
    invalid = {**valid, "name": "unknown-rollout-1", "case_name": "unknown"}
    results.write_text(
        "\n".join(json.dumps(row) for row in (valid, invalid)) + "\n",
        encoding="utf-8",
    )
    called = []
    monkeypatch.setattr(scorer.grader, "grade_with_details", lambda *args: called.append(args))
    with pytest.raises(ValueError, match="does not match"):
        scorer.score(results, dataset)
    assert not called
