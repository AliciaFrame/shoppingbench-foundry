import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

SKILL = Path(__file__).parents[1] / ".github" / "skills" / "mai-agent-hillclimb"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    import cloud
    import journey
finally:
    sys.path.pop(0)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    journey.scaffold(root)
    return root


@pytest.fixture
def prepared(project):
    target = project / "prepared"
    journey.prepare(
        project / "cases.example.jsonl", target, project / "tools.json", project / "instructions.md"
    )
    return target


def write_adapter(project):
    path = project / "fake_adapter.py"
    path.write_text(
        "import json\n"
        "def run_episode(item):\n"
        "    if item.get('fail'): raise RuntimeError('tool unavailable')\n"
        "    return {'output_text': json.dumps(item['expected']), 'calls': [\n"
        "        {'model': 'policy', 'purpose': 'inference', 'raw_usage': {\n"
        "            'input_tokens': 100, 'output_tokens': 20,\n"
        "            'input_tokens_details': {'cached_tokens': 25}}}]}\n",
        encoding="utf-8",
    )
    return path


def make_receipts(project, prepared, split, adapter):
    output = project / f"{split}-receipts.jsonl"
    journey.evaluate(
        prepared / f"{split}.jsonl", adapter, project / "grader.py", output, "base", rollouts=2
    )
    return output


def calibration_fixture(project, prepared):
    adapter = write_adapter(project)
    receipts = []
    for split in ("train", "validation"):
        path = make_receipts(project, prepared, split, adapter)
        rows = journey.read_jsonl(path)
        for row in rows:
            if row["rollout"] == 1:
                row["sample"]["output_text"] = "{}"
                row["metrics"]["quality"] = 0
        receipts.extend(rows)
    combined = project / "calibration-receipts.jsonl"
    journey.write_jsonl(combined, receipts)
    output = project / "calibration.json"
    report = journey.calibrate(
        combined, project / "grader.py", project / "labels.example.jsonl", output, min_cases=3
    )
    assert report["eligible"]
    return combined, output


def test_copied_skill_scaffolds_and_prepares_without_site_packages(tmp_path):
    copied = tmp_path / "skill"
    shutil.copytree(SKILL, copied, ignore=shutil.ignore_patterns("__pycache__"))
    root = tmp_path / "new-project"
    subprocess.run(
        [
            sys.executable,
            "-S",
            str(copied / "scripts" / "journey.py"),
            "scaffold",
            "--output",
            str(root),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-S",
            str(copied / "scripts" / "journey.py"),
            "prepare",
            "--data",
            str(root / "cases.example.jsonl"),
            "--tools",
            str(root / "tools.json"),
            "--output",
            str(root / "prepared"),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert journey.read_json(root / "prepared" / "manifest.json")["counts"]["train"] == 3
    assert journey.read_jsonl(root / "prepared" / "train.jsonl")[0]["tools"][0]["function"]["name"]
    with pytest.raises(FileExistsError):
        journey.scaffold(root)


def test_cold_copied_skill_completes_evaluation_cost_selection_and_sealed_final(tmp_path):
    copied = tmp_path / "skill"
    shutil.copytree(SKILL, copied, ignore=shutil.ignore_patterns("__pycache__"))
    script = copied / "scripts" / "journey.py"
    root = tmp_path / "project"

    def run(*args):
        result = subprocess.run(
            [sys.executable, "-S", str(script), *map(str, args)],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    run("scaffold", "--output", root)
    adapter = root / "offline_adapter.py"
    adapter.write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_episode(item):\n"
        "    chosen = 'selected-test-policy' in Path(__file__).with_name('instructions.md').read_text()\n"
        "    return {'output_text': json.dumps(item['expected'] if chosen else {}),\n"
        "            'calls': [{'model':'policy','purpose':'inference','raw_usage':{\n"
        "                'input_tokens':100,'output_tokens':20,\n"
        "                'input_tokens_details':{'cached_tokens':0}}}]}\n",
        encoding="utf-8",
    )
    prepared = root / "prepared"
    run("prepare", "--data", root / "cases.example.jsonl", "--output", prepared)
    common = [
        "--data",
        prepared / "development.jsonl",
        "--adapter",
        adapter,
        "--grader",
        root / "grader.py",
        "--rollouts",
        "2",
        "--price-key",
        "policy-default",
    ]
    baseline, candidate = root / "baseline.jsonl", root / "candidate.jsonl"
    assert run("evaluate", *common, "--stage", "baseline", "--output", baseline)["quality"] == 0
    (root / "instructions.md").write_text("selected-test-policy\n", encoding="utf-8")
    assert run("evaluate", *common, "--stage", "candidate", "--output", candidate)["quality"] == 1
    result = run(
        "compare",
        "--baseline",
        baseline,
        "--candidate",
        candidate,
        "--output",
        root / "comparison.json",
    )
    assert result["quality_gain_mean"] == 1
    cost_report = root / "cost.json"
    subprocess.run(
        [
            sys.executable,
            "-S",
            str(copied / "scripts" / "cost_report.py"),
            "--receipts",
            str(candidate),
            "--prices",
            str(copied / "examples" / "prices.synthetic.json"),
            "--output",
            str(cost_report),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    approval = root / "approval.json"
    journey.write_json(
        approval,
        {
            "candidate_sha256": journey.digest(candidate),
            "cost_report_sha256": journey.digest(cost_report),
            "quality": True,
            "safety": True,
            "latency": True,
            "cost": True,
            "rationale": "Synthetic cold-folder workflow verification, not a live model result",
        },
    )
    selected = root / "selected.json"
    run(
        "select",
        "--candidate",
        candidate,
        "--approval",
        approval,
        "--cost-report",
        cost_report,
        "--adapter",
        adapter,
        "--grader",
        root / "grader.py",
        "--final-data",
        prepared / "final.jsonl",
        "--output",
        selected,
    )
    final = run(
        "evaluate",
        "--data",
        prepared / "final.jsonl",
        "--adapter",
        adapter,
        "--grader",
        root / "grader.py",
        "--stage",
        "candidate",
        "--output",
        root / "final.jsonl",
        "--final-state",
        selected,
    )
    assert final["quality"] == 1 and journey.read_json(selected)["final_test_started"]


def test_connected_group_leakage_and_duplicate_rows_are_rejected(project):
    rows = journey.read_jsonl(project / "cases.example.jsonl")
    rows[-1]["group"] = rows[0]["group"]
    with pytest.raises(ValueError, match="leak"):
        journey.validate_data(rows)
    rows[-1]["group"] = "unique"
    rows.append(rows[0])
    with pytest.raises(ValueError, match="Duplicate"):
        journey.validate_data(rows)


def test_identical_prompts_cannot_be_hidden_by_renaming_groups(project):
    rows = journey.read_jsonl(project / "cases.example.jsonl")
    rows[-1]["messages"] = copy.deepcopy(rows[0]["messages"])
    with pytest.raises(ValueError, match="Identical prompt"):
        journey.validate_data(rows)


def test_training_instructions_are_bound_without_leaking_into_selection_data(project, prepared):
    train = journey.read_jsonl(prepared / "train.jsonl")
    dev = journey.read_jsonl(prepared / "development.jsonl")
    assert train[0]["messages"][0]["role"] == "system"
    assert dev[0]["messages"][0]["role"] == "user"
    rows = journey.read_jsonl(project / "cases.example.jsonl")
    rows[3]["messages"].insert(0, {"role": "system", "content": "conflicting policy"})
    source = project / "conflicting.jsonl"
    journey.write_jsonl(source, rows)
    with pytest.raises(ValueError, match="differs from selected policy"):
        journey.prepare(
            source, project / "bad-prepared", project / "tools.json", project / "instructions.md"
        )


def test_evaluation_preserves_failure_denominator_and_feeds_existing_cost_tool(project, prepared):
    rows = journey.read_jsonl(prepared / "development.jsonl")
    rows[1]["fail"] = True
    journey.write_jsonl(prepared / "development.jsonl", rows)
    adapter = write_adapter(project)
    output = project / "receipts.jsonl"
    summary = journey.evaluate(
        prepared / "development.jsonl",
        adapter,
        project / "grader.py",
        output,
        "base",
        price_key="policy-default",
    )
    assert summary["episodes"] == 3 and summary["failed"] == 1
    receipts = journey.read_jsonl(output)
    assert not receipts[1]["usage_complete"] and receipts[1]["metrics"]["quality"] == 0
    spec = importlib.util.spec_from_file_location("journey_costs", SCRIPTS / "cost_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    costs = module.report(receipts, journey.read_json(SKILL / "examples" / "prices.synthetic.json"))
    assert costs["stages"]["base"]["serving_cost"] is None
    assert costs["stages"]["base"]["known_serving_cost"] > 0


def test_unknown_usage_and_aggregated_context_are_not_invented():
    calls, complete = journey.normalize_calls([{"raw_usage": None}], "policy")
    assert not complete and calls[0]["usage"]["output_tokens"] is None
    calls, complete = journey.normalize_calls(
        [
            {
                "aggregated": True,
                "raw_usage": {
                    "input_tokens": 200,
                    "output_tokens": 10,
                    "input_tokens_details": {"cached_tokens": 100},
                },
            }
        ],
        "policy",
    )
    assert complete and calls[0]["context_input_tokens"] is None
    with pytest.raises(ValueError, match="exceed"):
        journey.normalize_calls(
            [
                {
                    "raw_usage": {
                        "input_tokens": 1,
                        "output_tokens": 2,
                        "input_tokens_details": {"cached_tokens": 3},
                    }
                }
            ],
            "policy",
        )


def test_cache_writes_require_explicit_billing_mode_and_observed_counters():
    raw = {
        "input_tokens": 100,
        "output_tokens": 10,
        "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 30},
    }
    calls, complete = journey.normalize_calls(
        [{"raw_usage": raw, "cache_write_mode": "replacement"}], "policy"
    )
    assert complete and calls[0]["usage"]["uncached_input_tokens"] == 50
    assert calls[0]["usage"]["cache_write_tokens"] == 30
    with pytest.raises(ValueError, match="verified billing mode"):
        journey.normalize_calls([{"raw_usage": raw}], "policy")
    raw["input_tokens_details"]["cache_write_tokens"] = None
    _, complete = journey.normalize_calls(
        [{"raw_usage": raw, "cache_write_mode": "additional"}], "policy"
    )
    assert not complete


def test_calibration_distinguishes_within_example_from_between_example_variation(project, prepared):
    adapter = write_adapter(project)
    path = make_receipts(project, prepared, "train", adapter)
    report = journey.calibrate(
        path,
        project / "grader.py",
        project / "labels.example.jsonl",
        project / "constant.json",
        min_cases=3,
    )
    assert not report["eligible"] and report["within_example_variable_fraction"] == 0
    path.rename(project / "constant-receipts.jsonl")
    combined, output = calibration_fixture(project, prepared)
    assert journey.read_json(output)["within_example_variable_fraction"] == 1
    labels = journey.read_jsonl(project / "labels.example.jsonl")
    labels[2]["human_score"] = 1
    journey.write_jsonl(project / "labels.example.jsonl", labels)
    report = journey.calibrate(
        combined,
        project / "grader.py",
        project / "labels.example.jsonl",
        project / "disagreement.json",
        min_cases=3,
    )
    assert not report["eligible"] and report["label_mae"] > 0.1


def test_comparison_requires_matched_complete_data_and_never_auto_selects(project, prepared):
    adapter = write_adapter(project)
    baseline = make_receipts(project, prepared, "development", adapter)
    rows = journey.read_jsonl(baseline)
    for row in rows:
        row["metrics"]["quality"] = 0.5
    journey.write_jsonl(baseline, rows)
    candidate = project / "candidate.jsonl"
    for row in rows:
        row["metrics"]["quality"] = 1
    journey.write_jsonl(candidate, rows)
    result = journey.compare(baseline, candidate, project / "comparison.json")
    assert result["quality_latency_gate"] and not result["eligible"]
    journey.write_jsonl(candidate, rows[:-1])
    with pytest.raises(ValueError, match="every scheduled"):
        journey.compare(baseline, candidate, project / "bad.json")


def test_final_selection_is_persisted_and_never_reopened(project, prepared):
    adapter = write_adapter(project)
    candidate = make_receipts(project, prepared, "development", adapter)
    approval = project / "approval.json"
    cost_report = project / "costs.json"
    journey.write_json(
        cost_report,
        {
            "stages": {
                "base": {
                    "episodes": 6,
                    "serving_cost_complete": True,
                }
            }
        },
    )
    journey.write_json(
        approval,
        {
            "candidate_sha256": journey.digest(candidate),
            "quality": True,
            "safety": True,
            "latency": True,
            "cost": True,
            "rationale": "Reviewed matched development evidence",
            "cost_report_sha256": journey.digest(cost_report),
        },
    )
    selected = project / "selected.json"
    journey.select(
        candidate,
        approval,
        adapter,
        project / "grader.py",
        prepared / "final.jsonl",
        selected,
        cost_report,
    )
    journey.evaluate(
        prepared / "final.jsonl",
        adapter,
        project / "grader.py",
        project / "final.jsonl",
        "base",
        final_state=selected,
    )
    assert journey.read_json(selected)["final_test_started"]
    with pytest.raises(ValueError, match="already opened"):
        journey.evaluate(
            prepared / "final.jsonl",
            adapter,
            project / "grader.py",
            project / "final-again.jsonl",
            "base",
            final_state=selected,
        )


def test_selection_rejects_changed_domain_and_unapproved_tradeoffs(project, prepared):
    adapter = write_adapter(project)
    candidate = make_receipts(project, prepared, "development", adapter)
    approval = project / "approval.json"
    cost_report = project / "costs.json"
    journey.write_json(
        cost_report,
        {
            "stages": {
                "base": {
                    "episodes": 6,
                    "serving_cost_complete": True,
                }
            }
        },
    )
    body = {
        "candidate_sha256": journey.digest(candidate),
        "quality": True,
        "safety": True,
        "latency": True,
        "cost": False,
        "rationale": "test",
        "cost_report_sha256": journey.digest(cost_report),
    }
    journey.write_json(approval, body)
    with pytest.raises(ValueError, match="acceptance"):
        journey.select(
            candidate,
            approval,
            adapter,
            project / "grader.py",
            prepared / "final.jsonl",
            project / "selection.json",
            cost_report,
        )
    body["cost"] = True
    journey.write_json(approval, body)
    with (project / "domain.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# Domain semantics changed\n")
    with pytest.raises(ValueError, match="implementation changed"):
        journey.select(
            candidate,
            approval,
            adapter,
            project / "grader.py",
            prepared / "final.jsonl",
            project / "selection.json",
            cost_report,
        )


def test_endpoints_require_auth_and_preserve_local_reward_and_tool_parity(project, monkeypatch):
    monkeypatch.setenv("HILLCLIMB_ENDPOINT_TOKEN", "test-token-not-a-secret")
    sys.path.insert(0, str(project))
    try:
        spec = importlib.util.spec_from_file_location("starter_endpoints", project / "service.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    client = TestClient(module.app)
    label = journey.read_jsonl(project / "labels.example.jsonl")[0]
    body = {"sample": label["sample"], "item": label["item"]}
    assert client.post("/grade", json=body).status_code == 401
    headers = {"Authorization": "Bearer test-token-not-a-secret"}
    assert client.post("/grade", json=body, headers=headers).json()["reward"] == 1
    response = client.post(
        "/tools",
        headers=headers,
        json={
            "name": "lookup_policy",
            "arguments": '{"category":"outage"}',
            "call_id": "call-1",
        },
    )
    assert response.json()["call_id"] == "call-1"
    assert json.loads(response.json()["output"])["team"] == "operations"
    assert (
        client.post(
            "/tools",
            headers=headers,
            json={
                "name": "not-a-tool",
                "arguments": {},
                "call_id": "call-2",
            },
        ).status_code
        == 422
    )
    assert client.post("/grade", headers=headers, content="x" * 1_000_001).status_code == 413
    assert client.post("/grade", headers=headers, json={}).status_code == 422


def test_rft_plan_gates_calibration_data_and_endpoint_parity(project, prepared):
    combined, calibration = calibration_fixture(project, prepared)
    config = journey.read_json(SKILL / "examples" / "cloud.example.json")
    config["rft"]["min_train_examples"] = 3
    config["rft"]["capability_evidence"].update(
        source="verified-test-capability",
        checked_at="2026-10-14",
        reinforcement_supported=True,
        agentic_supported=True,
    )
    parity = project / "parity.json"
    journey.write_json(
        parity,
        {
            "passed": True,
            "grader_sha256": journey.digest(project / "grader.py"),
            "domain_sha256": journey.digest(project / "domain.py"),
            "labels_sha256": journey.digest(project / "labels.example.jsonl"),
            "service_url": config["service_url"],
            "tool_names": ["lookup_policy"],
        },
    )
    plan = cloud.rft_plan(
        project, config, prepared, calibration, combined, project / "labels.example.jsonl", parity
    )
    assert plan["method"]["reinforcement"]["tools"][0]["name"] == "lookup_policy"
    assert "headers" not in plan["method"]["reinforcement"]["grader"]
    config["rft"]["capability_evidence"]["agentic_supported"] = False
    with pytest.raises(ValueError, match="access"):
        cloud.rft_plan(
            project,
            config,
            prepared,
            calibration,
            combined,
            project / "labels.example.jsonl",
            parity,
        )
    config["rft"]["capability_evidence"]["agentic_supported"] = True
    with (prepared / "train.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="dataset changed"):
        cloud.rft_plan(
            project,
            config,
            prepared,
            calibration,
            combined,
            project / "labels.example.jsonl",
            parity,
        )


def test_ambiguous_rft_submission_persists_file_ids_and_cannot_be_repeated(
    project, prepared, monkeypatch
):
    config = journey.read_json(SKILL / "examples" / "cloud.example.json")
    config["rft"]["grader_type"] = "python"
    created = []

    def upload(**_kwargs):
        created.append("upload")
        return SimpleNamespace(id=f"file-{len(created)}")

    def submit(**_kwargs):
        raise TimeoutError("outcome unknown")

    client = SimpleNamespace(
        files=SimpleNamespace(
            create=upload, retrieve=lambda _id: SimpleNamespace(status="processed")
        ),
        fine_tuning=SimpleNamespace(jobs=SimpleNamespace(create=submit)),
    )
    monkeypatch.setattr(cloud, "sdk_client", lambda _url: client)
    plan = {
        "model": "policy",
        "trainingType": "developerTier",
        "method": {"type": "reinforcement", "reinforcement": {"grader": {"type": "python"}}},
    }
    receipt = project / "submission.json"
    with pytest.raises(TimeoutError, match="unknown"):
        cloud.submit_rft(project, config, prepared, plan, receipt)
    state = journey.read_json(receipt)
    assert state["train_file_id"] == "file-1" and state["validation_file_id"] == "file-2"
    assert state["status"] == "submitting" and "job_id" not in state
    with pytest.raises(FileExistsError):
        cloud.submit_rft(project, config, prepared, plan, receipt)
    assert created == ["upload", "upload"]


def test_cloud_dry_run_never_creates_resources(project, monkeypatch):
    config = project / "cloud.json"
    journey.write_json(config, journey.read_json(SKILL / "examples" / "cloud.example.json"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cloud.py",
            "deploy-hosted",
            "--project",
            str(project),
            "--config",
            str(config),
            "--receipt",
            str(project / "deploy.json"),
        ],
    )
    monkeypatch.setattr(cloud, "command", lambda *_args: pytest.fail("dry run executed a command"))
    cloud.main()
    assert not (project / "deploy.json").exists()


def test_optimizer_spec_binds_instructions_version_and_query_shaped_data(project, prepared):
    config = journey.read_json(SKILL / "examples" / "cloud.example.json")
    config["optimizer"]["dataset"] = str(prepared / "optimizer.jsonl")
    config["agent_version"] = "4"
    spec = cloud.optimizer_config(project, config)
    assert spec["agent"]["version"] == "4"
    assert spec["agent"]["config"] == str(project / "metadata.yaml")
    config["optimizer"]["dataset"] = str(prepared / "development.jsonl")
    with pytest.raises(ValueError, match="optimizer-only"):
        cloud.optimizer_config(project, config)
    (project / "azure.yaml").unlink()
    with pytest.raises(ValueError, match="project-local"):
        cloud.cli_plan("init-hosted", project, config)


def test_cli_errors_preserve_stdout_and_redact_custom_secret(project, monkeypatch):
    monkeypatch.setattr(cloud.shutil, "which", lambda _name: str(project / "az.exe"))
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs)
        seen["argv"] = argv
        return SimpleNamespace(returncode=1, stderr="", stdout="rejected private-token-value")

    monkeypatch.setattr(cloud.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="rejected <redacted>"):
        cloud.command(
            ["az", "--subscription", "subscription-id", "account", "show"],
            project,
            ["private-token-value"],
        )
    assert seen["encoding"] == "utf-8"
    assert seen["argv"][-2:] == ["--subscription", "subscription-id"]


class Context:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


def test_remote_evaluator_binds_judge_and_resumes_without_recreating(project, monkeypatch):
    config = journey.read_json(SKILL / "examples" / "cloud.example.json")
    labels = journey.read_jsonl(project / "labels.example.jsonl")
    calls = []
    criteria = []

    def create_eval(**kwargs):
        calls.append("eval")
        criteria.extend(kwargs["testing_criteria"])
        return SimpleNamespace(id="eval_test")

    def create_run(**_kwargs):
        calls.append("run")
        return SimpleNamespace(id="run_test", status="completed")

    def outputs(*_args, **_kwargs):
        return [
            SimpleNamespace(
                id=str(index),
                datasource_item_id=index,
                results=[
                    SimpleNamespace(
                        name=config["optimizer"]["evaluator"], score=label["human_score"]
                    )
                ],
                model_dump=lambda **_kwargs: {"status": "completed"},
            )
            for index, label in enumerate(labels)
        ]

    run_api = SimpleNamespace(
        create=create_run,
        retrieve=lambda *_args, **_kwargs: SimpleNamespace(id="run_test", status="completed"),
        output_items=SimpleNamespace(list=outputs),
    )
    eval_api = SimpleNamespace(
        create=create_eval,
        retrieve=lambda *_args, **_kwargs: SimpleNamespace(
            id="eval_test", model_dump=lambda **_kwargs: {"testing_criteria": criteria}
        ),
        runs=run_api,
    )
    api = SimpleNamespace(evals=eval_api)
    project_api = SimpleNamespace(get_openai_client=lambda **_kwargs: Context(api))
    monkeypatch.setattr(cloud, "project_client", lambda _endpoint: Context(project_api))
    receipt = project / "remote-validation.json"
    result = cloud.validate_evaluator(project, config, project / "labels.example.jsonl", receipt)
    assert result["passed"] and result["label_mae"] == 0
    assert criteria[0]["initialization_parameters"] == {
        "model": config["optimizer"]["eval_model"],
        "threshold": 0.75,
    }
    cloud.validate_evaluator(project, config, project / "labels.example.jsonl", receipt)
    assert calls == ["eval", "run"]
    config["optimizer"]["eval_model"] = "different-judge"
    with pytest.raises(ValueError, match="runtime settings"):
        cloud.validate_evaluator(project, config, project / "labels.example.jsonl", receipt)


def test_optimizer_sdk_requires_calibrated_frozen_judge(project, prepared, monkeypatch):
    config = journey.read_json(SKILL / "examples" / "cloud.example.json")
    config["optimizer"]["dataset"] = str(prepared / "optimizer.jsonl")
    validation = project / "judge.json"
    body = {
        "passed": True,
        "config": {"evaluator_sha256": journey.digest(project / "evaluator.md")},
        "project_endpoint": config["project_endpoint"],
        "evaluator_settings": {
            "evaluator_version": config["optimizer"]["evaluator_version"],
            "initialization_parameters": {
                "model": config["optimizer"]["eval_model"],
                "threshold": 0.75,
            },
        },
    }
    journey.write_json(validation, body)
    plan = cloud.optimizer_plan(project, config, validation)
    evaluator = plan["optimization_configuration"]["evaluation_configuration"]["evaluators"][0]
    assert evaluator["initialization_parameters"]["model"] == config["optimizer"]["eval_model"]
    calls = []

    def create(request, **kwargs):
        calls.append((request, kwargs))
        job = SimpleNamespace(
            id="opt_test",
            status="queued",
            as_dict=lambda: {"id": "opt_test", "status": "queued"},
        )
        return SimpleNamespace(details={"job_id": job.id})

    api = SimpleNamespace(
        agents=SimpleNamespace(
            begin_create_optimization_job=create,
            get_optimization_job=lambda _id: SimpleNamespace(
                status="queued",
                as_dict=lambda: {"id": "opt_test", "status": "queued"},
            ),
        )
    )
    monkeypatch.setattr(cloud, "project_client", lambda _endpoint: Context(api))
    receipt = project / "optimizer.json"
    result = cloud.submit_optimizer(config, plan, receipt)
    assert result["operation_id"] == "opt_test" and calls[0][1]["polling"] is False
    with pytest.raises(FileExistsError):
        cloud.submit_optimizer(config, plan, receipt)
    config["optimizer"]["pass_threshold"] = 0.9
    with pytest.raises(ValueError, match="changed after validation"):
        cloud.optimizer_plan(project, config, validation)


def test_successful_rft_monitor_uses_sdk_pages_and_redacts_echoed_credentials(
    project, prepared, monkeypatch
):
    config = journey.read_json(SKILL / "examples" / "cloud.example.json")
    config["rft"]["grader_type"] = "python"
    submitted = []
    file_ids = iter(["file_train", "file_validation"])
    job = SimpleNamespace(
        id="ft_test",
        status="succeeded",
        error=None,
        model_dump=lambda **_kwargs: {
            "id": "ft_test",
            "status": "succeeded",
            "fine_tuned_model": "ft_model",
            "method": {
                "reinforcement": {"grader": {"headers": {"Authorization": "not-a-real-key"}}}
            },
        },
    )

    def create(**kwargs):
        submitted.append(kwargs)
        return job

    api = SimpleNamespace(
        files=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(id=next(file_ids)),
            retrieve=lambda _id: SimpleNamespace(status="processed"),
        ),
        fine_tuning=SimpleNamespace(
            jobs=SimpleNamespace(
                create=create,
                retrieve=lambda _id: job,
                checkpoints=SimpleNamespace(
                    list=lambda _id: [
                        SimpleNamespace(
                            model_dump=lambda **_kwargs: {
                                "fine_tuned_model_checkpoint": "ft_checkpoint"
                            }
                        )
                    ]
                ),
            )
        ),
    )
    monkeypatch.setattr(cloud, "sdk_client", lambda _url: api)
    plan = {
        "model": "policy",
        "trainingType": "developerTier",
        "method": {"type": "reinforcement", "reinforcement": {"grader": {"type": "python"}}},
    }
    receipt = project / "rft-success.json"
    cloud.submit_rft(project, config, prepared, plan, receipt)
    assert submitted[0]["training_file"] == "file_train"
    assert submitted[0]["extra_body"] == {"trainingType": "developerTier"}
    result = cloud.monitor_rft(config, receipt)
    assert result["checkpoints"][0]["fine_tuned_model_checkpoint"] == "ft_checkpoint"
    assert result["job"]["method"]["reinforcement"]["grader"]["headers"] == "<redacted>"
    assert "not-a-real-key" not in receipt.read_text(encoding="utf-8")


def test_failed_tool_episode_retains_observed_inference_usage(project, prepared, monkeypatch):
    raw = {
        "model": "policy",
        "purpose": "inference",
        "raw_usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "input_tokens_details": {"cached_tokens": 0},
        },
    }

    def fail(_item):
        error = RuntimeError("external tool failed after inference")
        error.calls = [raw]
        raise error

    original = journey.load_hook
    monkeypatch.setattr(
        journey,
        "load_hook",
        lambda path, name: fail if name == "run_episode" else original(path, name),
    )
    adapter = write_adapter(project)
    output = project / "failed-observed.jsonl"
    journey.evaluate(
        prepared / "development.jsonl", adapter, project / "grader.py", output, "failed"
    )
    receipt = journey.read_jsonl(output)[0]
    assert receipt["calls"][0]["usage"]["uncached_input_tokens"] == 10
    assert receipt["status"] == "failed" and not receipt["usage_complete"]


def test_endpoint_grader_invariants_dominate_partial_credit(project):
    grader = journey.load_hook(project / "grader.py", "grade")
    item = {
        "expected": {"team": "operations", "severity": "critical"},
        "invariants": {"safe": True},
    }
    assert (
        grader({"output_text": '{"team":"operations","severity":"critical","safe":false}'}, item)
        == 0
    )
    assert grader({"output_text": '{"team":"operations","safe":true}'}, item) == 0.5
    assert grader({"output_text": "not json"}, item) == 0
    invalid = copy.deepcopy(item)
    invalid["weights"] = {"team": float("nan"), "severity": 1}
    with pytest.raises(ValueError, match="finite"):
        grader({"output_text": "{}"}, invalid)
