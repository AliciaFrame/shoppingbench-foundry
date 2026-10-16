import hashlib
import io
import json
import subprocess
import sys
import traceback
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_mai_hillclimb_journey import (
    SKILL,
    Context,
    cloud,
    journey,
)

hosted_ops = cloud.hosted_ops
native = cloud.native_results


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    journey.scaffold(root)
    return root


@pytest.fixture
def prepared(project):
    path = project / "prepared"
    journey.prepare(
        project / "cases.example.jsonl", path, project / "tools.json", project / "instructions.md"
    )
    return path


def builtin_config(prepared):
    config = journey.read_json(SKILL / "examples" / "cloud.example.json")
    config.update(agent_name="synthetic-agent", agent_version="1", enable_evaluation=False)
    config["optimizer"].update(
        evaluator_type="builtin",
        evaluator="builtin.task_adherence",
        dataset=str(prepared / "optimizer.jsonl"),
    )
    return config


def test_builtin_plan_needs_no_rubric_registration_or_trace_key(project, prepared):
    config = builtin_config(prepared)
    (project / "evaluator.md").unlink()
    plan = cloud.optimizer_plan(project, config)
    evaluation = plan["optimization_configuration"]["evaluation_configuration"]
    assert evaluation["evaluators"] == [{"name": "builtin.task_adherence"}]
    assert evaluation["training_set"]["source"]["type"] == "dataset_reference"
    data = cloud.builtin_optimizer_data(project, config)
    assert all(not row["query"].startswith("HILLCLIMB_WORKLOAD") for row in data["rows"])
    assert all(row["criteria"] and row["ground_truth"] for row in data["rows"])
    assert plan["optimization_configuration"]["agent_optimization_space"] == {
        "target_attributes": ["instructions"]
    }
    with pytest.raises(ValueError, match="registration"):
        cloud.register_evaluator(project, config, project / "not-created.json")
    assert not (project / "not-created.json").exists()


def test_builtin_submission_registers_once_and_keeps_criteria(project, prepared, monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError

    config = builtin_config(prepared)
    plan = cloud.optimizer_plan(project, config)
    events = []

    def upload(**kwargs):
        rows = journey.read_jsonl(kwargs["file_path"])
        assert all(row["criteria"] for row in rows)
        events.append("upload")

    def create(body, **_kwargs):
        events.append("submit")
        assert body["optimization_configuration"]["evaluation_configuration"]["evaluators"] == [
            {"name": "builtin.task_adherence"}
        ]
        return SimpleNamespace(details={"job_id": "opt_synthetic"})

    def missing(**_kwargs):
        raise ResourceNotFoundError("missing")

    api = SimpleNamespace(
        datasets=SimpleNamespace(get=missing, upload_file=upload),
        agents=SimpleNamespace(
            begin_create_optimization_job=create,
            get_optimization_job=lambda _id: SimpleNamespace(
                status="in_progress", as_dict=lambda: {"status": "in_progress"}
            ),
        ),
    )
    monkeypatch.setattr(cloud, "project_client", lambda _endpoint: Context(api))
    path = project / "builtin-submit.json"
    result = cloud.submit_optimizer(config, plan, path, project=project)
    assert events == ["upload", "submit"]
    assert len(result["case_map"]) == 3
    assert result["dataset"]["registered"]
    with pytest.raises(FileExistsError):
        cloud.submit_optimizer(config, plan, path, project=project)
    assert events == ["upload", "submit"]


def test_builtin_history_is_not_silently_flattened(project, prepared):
    config = builtin_config(prepared)
    path = prepared / "optimizer.jsonl"
    rows = journey.read_jsonl(path)
    rows[0]["item"]["context"] = {"document": "context"}
    journey.write_jsonl(path, rows)
    with pytest.raises(ValueError, match="History/context"):
        cloud.optimizer_plan(project, config)


def test_builtin_accepts_official_single_turn_rows_without_rubric(project, prepared):
    config = builtin_config(prepared)
    data = project / "official-search.jsonl"
    journey.write_jsonl(
        data,
        [
            {
                "name": "case-a",
                "query": "A synthetic task",
                "criteria": [{"name": "complete", "instruction": "Answer the task"}],
            }
        ],
    )
    config["optimizer"]["dataset"] = str(data)
    compiled = cloud.builtin_optimizer_data(project, config)
    assert compiled["rows"][0]["query"] == "A synthetic task"
    assert compiled["rows"][0]["ground_truth"] == ""
    row = journey.read_jsonl(data)[0]
    row["split"] = "final"
    journey.write_jsonl(data, [row])
    with pytest.raises(ValueError, match="optimizer-only"):
        cloud.optimizer_plan(project, config)


def code_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "metadata.yaml", "instruction_file: instructions.md\ntools_file: tools.json\n"
        )
        archive.writestr("instructions.md", "Old policy\r\n")
        archive.writestr("tools.json", "[]")
        archive.writestr("agent.py", "print('synthetic runtime')")
    return buffer.getvalue()


def profile():
    return {
        "instructions": "Improved synthetic policy",
        "model": "synthetic-policy-v2",
        "tool_definitions": [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Improved description",
                    "parameters": {"type": "object"},
                },
            }
        ],
        "skills": [
            {
                "name": "Validate evidence",
                "description": "Check evidence",
                "body": "Compare claims to the retrieved evidence.",
            }
        ],
    }


def test_sdk_archive_preserves_code_and_materializes_all_mutations(tmp_path):
    import yaml

    original, chosen = code_zip(), profile()
    result = hosted_ops.candidate_archive(original, chosen)
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert archive.read("agent.py") == b"print('synthetic runtime')"
        assert archive.read("instructions.md").decode() == chosen["instructions"]
        assert json.loads(archive.read("tools.json")) == chosen["tool_definitions"]
        metadata = yaml.safe_load(archive.read("metadata.yaml"))
        assert metadata["model"] == chosen["model"]
        body = archive.read(f"{metadata['skill_dir']}/skill-000/SKILL.md").decode()
        assert chosen["skills"][0]["body"] in body
        archive.extractall(tmp_path)
    # Verify the actual optimization loader contract, not only the generated YAML.
    from azure.ai.agentserver.optimization import load_config

    loaded = load_config(config_dir=str(tmp_path))
    assert loaded.instructions == chosen["instructions"]
    assert loaded.model == chosen["model"]
    assert loaded.tool_definitions == chosen["tool_definitions"]
    assert (
        chosen["skills"][0]["body"]
        in (Path(loaded.skills_dir) / "skill-000" / "SKILL.md").read_text()
    )


def test_archive_rejects_metadata_path_escape():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("metadata.yaml", "instruction_file: ../outside.md\n")
    with pytest.raises(ValueError, match="inside"):
        hosted_ops.candidate_archive(data.getvalue(), profile())


class DeploymentAPI:
    def __init__(self, original, auto_promote=False, latest="1"):
        from azure.ai.projects.models import VersionSelector

        self.original, self.created, self.auto_promote = original, 0, auto_promote
        self.latest = latest
        self.previous = {
            "version_selection_rules": [
                {"type": "FixedRatio", "agent_version": "@latest", "traffic_percentage": 100}
            ]
        }
        self.selector = VersionSelector(self.previous)
        self.baseline = SimpleNamespace(
            status="active",
            definition=SimpleNamespace(
                environment_variables={"MAI_MAX_TOOL_STEPS": "4"},
                cpu="1",
                memory="2Gi",
                code_configuration=SimpleNamespace(
                    content_hash=hashlib.sha256(original).hexdigest(),
                    runtime="python_3_13",
                    entry_point=["python", "agent.py"],
                    dependency_resolution="remote_build",
                ),
            ),
            metadata={},
        )
        self.agents = self

    def get_version(self, **_kwargs):
        return self.baseline

    def download_code(self, **_kwargs):
        return iter([self.original])

    def get(self, **_kwargs):
        return SimpleNamespace(
            agent_endpoint=SimpleNamespace(version_selector=self.selector),
            versions=SimpleNamespace(latest=SimpleNamespace(version=self.latest)),
        )

    def update_details(self, *, agent_endpoint, **_kwargs):
        self.selector = agent_endpoint.version_selector

    def create_version_from_code(self, **kwargs):
        from azure.ai.projects.models import VersionSelector

        assert all(
            rule.agent_version != "@latest" for rule in self.selector.version_selection_rules
        )
        self.created += 1
        code = kwargs["code"].read()
        assert hashlib.sha256(code).hexdigest() == kwargs["code_zip_sha256"]
        assert (
            kwargs["definition"].environment_variables
            == self.baseline.definition.environment_variables
        )
        self.uploaded = code
        if self.auto_promote:
            self.selector = VersionSelector(self.previous)
        return SimpleNamespace(status="active", version="2")


def deployment_state(project, chosen):
    path = project / "candidate-deploy.json"
    optimization = {"job": {"target_configuration": {"name": "synthetic-agent", "version": "1"}}}
    config = {"agent_name": "synthetic-agent", "agent_version": "1"}
    state = {"status": "submitting", "config": {"profile_sha256": journey.value_digest(chosen)}}
    return config, optimization, state, path


def test_sdk_candidate_creation_preserves_default_traffic_without_cli(project):
    api, chosen = DeploymentAPI(code_zip()), profile()
    config, optimization, state, path = deployment_state(project, chosen)
    result = hosted_ops.deploy(api, config, optimization, chosen, "cand_synthetic", state, path, 30)
    assert result["version"] == "2" and result["isolated"]
    assert api.created == 1
    assert api.selector.version_selection_rules[0].agent_version == "1"
    assert (
        result["previous_serving_selector"]["version_selection_rules"][0]["agent_version"]
        == "@latest"
    )


def test_unexpected_auto_promotion_is_restored_and_not_reported_success(project):
    api, chosen = DeploymentAPI(code_zip(), auto_promote=True), profile()
    config, optimization, state, path = deployment_state(project, chosen)
    with pytest.raises(RuntimeError, match="Serving routes"):
        hosted_ops.deploy(api, config, optimization, chosen, "cand_synthetic", state, path, 30)
    assert api.created == 1
    assert api.selector.version_selection_rules[0].agent_version == "1"


def test_sdk_submitted_job_applies_without_any_cli_job_state(project, prepared, monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError

    config = builtin_config(prepared)
    plan = cloud.optimizer_plan(project, config)
    api = DeploymentAPI(code_zip())

    def missing(**_kwargs):
        raise ResourceNotFoundError("missing")

    api.datasets = SimpleNamespace(get=missing, upload_file=lambda **_kwargs: None)
    api.begin_create_optimization_job = lambda *_args, **_kwargs: SimpleNamespace(
        details={"job_id": "opt_synthetic"}
    )
    api.get_optimization_job = lambda _id: SimpleNamespace(
        status="succeeded", as_dict=lambda: {**plan, "status": "succeeded"}
    )
    api.get_optimization_candidate = lambda *_args: SimpleNamespace(
        as_dict=lambda: {
            "status": "completed",
            "output": {"mutations": [{"type": "instructions", "value": "New policy"}]},
        }
    )
    monkeypatch.setattr(cloud, "project_client", lambda _endpoint: Context(api))

    def no_cli(*_args, **_kwargs):
        raise AssertionError("SDK workflow must not depend on azd environment state")

    monkeypatch.setattr(cloud, "command", no_cli)
    submitted = project / "sdk-job.json"
    cloud.submit_optimizer(config, plan, submitted, project=project)
    deployed = cloud.deploy_candidate(
        config, submitted, "cand_synthetic", project / "sdk-deploy.json", timeout=30
    )
    assert deployed["version"] == "2" and deployed["isolated"]
    assert api.created == 1
    with pytest.raises(FileExistsError):
        cloud.deploy_candidate(config, submitted, "cand_synthetic", project / "sdk-deploy.json")
    assert api.created == 1


def test_plain_smoke_requires_no_capture_secret(project):
    events = []
    session = SimpleNamespace(status="active", agent_session_id="synthetic-session")
    response = SimpleNamespace(
        id="synthetic-response",
        status="completed",
        model="returned-model",
        output_text="A normal response",
        usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 1}),
    )

    def invoke(**kwargs):
        assert "metadata" not in kwargs
        events.append("invoke")
        return response

    api = SimpleNamespace(
        agents=SimpleNamespace(
            get_version=lambda **_kwargs: SimpleNamespace(status="active"),
            create_session=lambda **_kwargs: session,
            stop_session=lambda **_kwargs: events.append("stop"),
        ),
        get_openai_client=lambda **_kwargs: Context(
            SimpleNamespace(responses=SimpleNamespace(create=invoke))
        ),
    )
    result = hosted_ops.smoke(
        api,
        {"agent_name": "synthetic", "agent_version": "1"},
        "Hello",
        {},
        project / "smoke.json",
        30,
    )
    assert events == ["invoke", "stop"]
    assert result["returned_model"] == "returned-model"


def test_monitor_retries_reads_and_never_recreates_job(project, monkeypatch):
    from azure.identity import CredentialUnavailableError

    path = project / "monitor.json"
    journey.write_json(path, {"operation_id": "opt_existing", "status": "in_progress"})
    attempts = []

    def get(job_id):
        attempts.append(job_id)
        if len(attempts) == 1:
            raise CredentialUnavailableError("synthetic secret should not be persisted")
        return SimpleNamespace(status="succeeded", as_dict=lambda: {"status": "succeeded"})

    monkeypatch.setattr(
        cloud,
        "project_client",
        lambda _endpoint: Context(
            SimpleNamespace(agents=SimpleNamespace(get_optimization_job=get))
        ),
    )
    monkeypatch.setattr(cloud.time, "sleep", lambda _delay: None)
    result = cloud.monitor_optimizer({"project_endpoint": "https://project.example"}, path, 30)
    assert attempts == ["opt_existing", "opt_existing"]
    assert result["monitor"]["status"] == "finished"
    assert "synthetic secret" not in path.read_text()


def test_monitor_disconnect_preserves_last_remote_status(project, monkeypatch):
    from azure.identity import CredentialUnavailableError

    path = project / "disconnected.json"
    journey.write_json(path, {"operation_id": "opt_existing", "status": "in_progress"})

    def fail(_job_id):
        raise CredentialUnavailableError("synthetic token")

    monkeypatch.setattr(
        cloud,
        "project_client",
        lambda _endpoint: Context(
            SimpleNamespace(agents=SimpleNamespace(get_optimization_job=fail))
        ),
    )
    monkeypatch.setattr(cloud.time, "sleep", lambda _delay: None)
    with pytest.raises(RuntimeError, match="disconnected"):
        cloud.monitor_optimizer({"project_endpoint": "https://project.example"}, path, 30)
    result = journey.read_json(path)
    assert result["status"] == "in_progress" and result["monitor"]["status"] == "disconnected"
    assert "synthetic token" not in path.read_text()


def test_native_subsets_match_fingerprints_not_run_row_numbers():
    rows = [
        {"name": "case-a", "query": "First task", "ground_truth": '{"expected":1}'},
        {"name": "case-b", "query": "Second task", "ground_truth": '{"expected":2}'},
    ]
    state = {"case_map": native.case_map(rows)}
    candidates, runs, outputs = [], {}, {}
    for name, selected in (("baseline", rows), ("candidate", rows[1:])):
        candidates.append(
            {
                "candidate_id": name,
                "name": name,
                "evaluation": {
                    "eval_run_id": name,
                    "score": 0.9,
                    "evaluator_scores": {"task_adherence": 1.0},
                },
            }
        )
        runs[name] = {
            "status": "completed",
            "result_counts": {"total": len(selected)},
            "data_source": {"source": {"content": [{"item": r} for r in selected]}},
        }
        outputs[name] = [
            {
                "datasource_item_id": index,
                "status": "completed",
                "datasource_item": {**row, "sample.output_text": "answer"},
                "results": [],
            }
            for index, row in enumerate(selected)
        ]
    report = native.build_report(state, candidates, runs, outputs)
    assert report["different_case_subsets"] and not report["matched_serving_comparison"]
    assert report["candidates"][1]["returned_case_ids"] == ["case-b"]
    assert report["candidates"][1]["unobserved_scheduled_case_ids"] == []
    assert report["candidates"][1]["optimizer_composite_score"] == 0.9
    assert report["candidates"][1]["shared_evaluator_scores"] == {"task_adherence": 1.0}


def test_semantic_judge_incomplete_json_is_not_accepted(project, monkeypatch):
    monkeypatch.setenv("MAI_JUDGE_BASE_URL", "https://judge.example/v1")
    monkeypatch.setenv("MAI_JUDGE_DEPLOYMENT_NAME", "synthetic-judge")
    monkeypatch.setenv("MAI_JUDGE_PRICING_KEY", "judge")
    module_grade = journey.load_hook(project / "semantic_grader.py", "grade_with_usage")
    namespace = module_grade.__globals__
    response = SimpleNamespace(
        status="incomplete",
        model="returned-judge",
        incomplete_details=SimpleNamespace(model_dump=lambda: {"reason": "max_output_tokens"}),
        output_text='{"score":1,"reason":"synthetic"}',
        usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 10, "output_tokens": 5}),
    )
    monkeypatch.setitem(namespace, "DefaultAzureCredential", lambda **_kwargs: Context("identity"))
    monkeypatch.setitem(namespace, "get_bearer_token_provider", lambda *_args: "synthetic-key")
    monkeypatch.setitem(
        namespace,
        "OpenAI",
        lambda **_kwargs: Context(
            SimpleNamespace(responses=SimpleNamespace(create=lambda **_args: response))
        ),
    )
    with pytest.raises(ValueError, match="did not complete") as failure:
        module_grade({"output_text": "answer"}, {"criteria": []})
    assert failure.value.calls[0]["returned_model"] == "returned-judge"
    assert failure.value.calls[0]["purpose"] == "evaluation"
    assert failure.value.calls[0]["raw_usage"]["output_tokens"] == 5


def test_sdk_deploy_freezes_current_latest_not_an_older_search_baseline(project):
    api, chosen = DeploymentAPI(code_zip(), latest="3"), profile()
    config, optimization, state, path = deployment_state(project, chosen)
    hosted_ops.deploy(api, config, optimization, chosen, "cand_synthetic", state, path, 30)
    assert api.selector.version_selection_rules[0].agent_version == "3"


def test_builtin_upload_errors_do_not_expose_storage_credentials(project, prepared, monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError, ServiceRequestError

    config = builtin_config(prepared)

    def missing(**_kwargs):
        raise ResourceNotFoundError("missing")

    def fail_upload(**_kwargs):
        raise ServiceRequestError("https://blob.example/file?sig=synthetic-secret")

    api = SimpleNamespace(datasets=SimpleNamespace(get=missing, upload_file=fail_upload))
    monkeypatch.setattr(cloud, "project_client", lambda _endpoint: Context(api))
    path = project / "upload-failed.json"
    with pytest.raises(RuntimeError, match="withheld") as error:
        cloud.submit_optimizer(config, cloud.optimizer_plan(project, config), path, project=project)
    assert "synthetic-secret" not in str(error.value)
    assert "synthetic-secret" not in path.read_text()
    assert journey.read_json(path)["status"] == "needs_reconciliation"


def test_archive_preserves_unrelated_executable_permissions():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("metadata.yaml", "instruction_file: instructions.md\n")
        executable = zipfile.ZipInfo("helper.sh")
        executable.external_attr = 0o100755 << 16
        archive.writestr(executable, "echo synthetic")
    with zipfile.ZipFile(
        io.BytesIO(hosted_ops.candidate_archive(data.getvalue(), profile()))
    ) as result:
        assert result.getinfo("helper.sh").external_attr == executable.external_attr


def test_plain_smoke_dry_run_needs_no_sdk_or_signing_key(project):
    config = project / "smoke-config.json"
    journey.write_json(config, {"agent_name": "synthetic-agent", "agent_version": "1"})
    receipt = project / "not-created.json"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(SKILL / "scripts" / "cloud.py"),
            "smoke",
            "--project",
            str(project),
            "--config",
            str(config),
            "--receipt",
            str(receipt),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["action"] == "smoke"
    assert not receipt.exists()


def test_rft_submission_errors_cannot_echo_endpoint_credentials(project, prepared, monkeypatch):
    import httpx
    from openai import BadRequestError

    secret = "synthetic-endpoint-secret"
    monkeypatch.setenv("HILLCLIMB_ENDPOINT_TOKEN", secret)
    config = {
        "endpoint_token_env": "HILLCLIMB_ENDPOINT_TOKEN",
        "rft": {"base_url": "https://resource.example/openai/v1", "grader_type": "endpoint"},
    }
    plan = {
        "model": "synthetic-model",
        "suffix": "synthetic",
        "trainingType": "developerTier",
        "method": {
            "type": "reinforcement",
            "reinforcement": {
                "grader": {"type": "endpoint", "url": "https://grader.example/grade"},
            },
        },
    }

    def reject(**kwargs):
        assert kwargs["method"]["reinforcement"]["grader"]["headers"]["Authorization"].endswith(
            secret
        )
        raise BadRequestError(
            "Server echoed " + secret,
            response=httpx.Response(400, request=httpx.Request("POST", "https://resource.example")),
            body={"message": secret},
        )

    client = SimpleNamespace(
        files=SimpleNamespace(
            create=lambda **_: SimpleNamespace(id="file-synthetic"),
            retrieve=lambda _: SimpleNamespace(status="processed"),
        ),
        fine_tuning=SimpleNamespace(jobs=SimpleNamespace(create=reject)),
    )
    monkeypatch.setattr(cloud, "sdk_client", lambda _: client)
    path = project / "rft-rejected.json"
    with pytest.raises(RuntimeError, match="credential-bearing details withheld") as failure:
        cloud.submit_rft(project, config, prepared, plan, path)
    assert secret not in "".join(traceback.format_exception(failure.value))
    assert secret not in path.read_text()
    assert failure.value.__suppress_context__
    state = journey.read_json(path)
    assert state["status"] == "needs_reconciliation"
    assert state["phase"] == "create_training_job"
    assert state["train_file_id"] == "file-synthetic"
    assert state["validation_file_id"] == "file-synthetic"


def test_cancel_and_monitor_only_the_bound_job_without_duplicate_cancellation(project, monkeypatch):
    config = {"rft": {"base_url": "https://resource.example/openai/v1", "model": "synthetic-model"}}
    path = project / "rft.json"
    journey.write_json(
        path,
        {
            "job_id": "ftjob-synthetic",
            "status": "running",
            "config": {"base_url": config["rft"]["base_url"], "plan": {"model": "synthetic-model"}},
        },
    )
    cancelled, dump_options = [], []
    job = SimpleNamespace(id="ftjob-synthetic", status="running", model="synthetic-model")

    def dump(**kwargs):
        dump_options.append(kwargs)
        return {
            "id": job.id,
            "status": job.status,
            "headers": {"Authorization": "synthetic-secret"},
        }

    def cancel(job_id):
        cancelled.append(job_id)
        job.status = "cancelled"
        return job

    job.model_dump = dump
    client = SimpleNamespace(
        fine_tuning=SimpleNamespace(
            jobs=SimpleNamespace(retrieve=lambda _: job, cancel=cancel),
        )
    )
    monkeypatch.setattr(cloud, "sdk_client", lambda _: client)
    assert cloud.cancel_rft(config, path)["cancellation"]["status"] == "confirmed"
    assert cloud.cancel_rft(config, path)["status"] == "cancelled"
    assert cloud.monitor_rft(config, path)["status"] == "cancelled"
    assert cancelled == ["ftjob-synthetic"]
    assert all(options == {"mode": "json", "warnings": False} for options in dump_options)
    assert "synthetic-secret" not in path.read_text()


def test_rft_cancel_refuses_unbound_endpoint_before_accessing_cloud(project, monkeypatch):
    path = project / "rft.json"
    journey.write_json(
        path,
        {
            "job_id": "ftjob-synthetic",
            "config": {"base_url": "https://old.example/openai/v1", "plan": {"model": "synthetic"}},
        },
    )
    monkeypatch.setattr(
        cloud, "sdk_client", lambda _: pytest.fail("Must reject before cloud access")
    )
    with pytest.raises(ValueError, match="Cancellation endpoint/model differs"):
        cloud.cancel_rft(
            {"rft": {"base_url": "https://new.example/openai/v1", "model": "synthetic"}}, path
        )


@pytest.mark.parametrize("api_error", [False, True])
def test_endpoint_parity_checks_foundry_transport_and_rejects_error_shaped_zero(
    project, monkeypatch, api_error
):
    config = journey.read_json(SKILL / "examples" / "cloud.example.json")
    monkeypatch.setenv(config["endpoint_token_env"], "synthetic-token")
    grade = journey.load_hook(project / "grader.py", "grade")
    execute = journey.load_hook(project / "domain.py", "execute_tool")
    labels = journey.read_jsonl(project / "labels.example.jsonl")
    labels_path = project / "api-labels.jsonl"
    journey.write_jsonl(labels_path, [labels[1]] if api_error else labels)

    def http(url, body, _token):
        if url.endswith("/grade"):
            return {"score": grade(body["sample"], body["item"])}
        return {
            "call_id": body["call_id"],
            "output": json.dumps(execute(url.rsplit("/", 1)[-1], body["arguments"])),
        }

    calls = []

    def api_run(**kwargs):
        calls.append(kwargs["model_sample"])
        result = grade({"output_text": kwargs["model_sample"]}, kwargs["item"])
        return SimpleNamespace(
            reward=result,
            model_dump=lambda **_: {
                "metadata": {"errors": {"endpoint_grader_server_error": api_error}}
            },
        )

    client = SimpleNamespace(
        fine_tuning=SimpleNamespace(
            alpha=SimpleNamespace(
                graders=SimpleNamespace(validate=lambda **_: None, run=api_run),
            )
        )
    )
    monkeypatch.setattr(cloud, "sdk_client", lambda _: client)
    monkeypatch.setattr(cloud, "http_post", http)
    output = project / "api-parity.json"
    if api_error:
        with pytest.raises(RuntimeError, match="execution errors, not a valid zero"):
            cloud.endpoint_parity(
                project,
                config,
                labels_path,
                project / "tool-probes.example.jsonl",
                output,
            )
        assert journey.read_json(output)["passed"] is False
    else:
        report = cloud.endpoint_parity(
            project,
            config,
            labels_path,
            project / "tool-probes.example.jsonl",
            output,
        )
        assert report["passed"] and report["rft_api_verified"]
        assert len(report["rft_api_samples"]) == len(calls) == 3
        assert report["rft_grader_sha256"] == journey.value_digest(
            cloud.endpoint_grader_spec(config)
        )
        assert "synthetic-token" not in output.read_text()


@pytest.mark.parametrize("response", [{"reward": 1}, {"score": 1, "extra": 0}, {"score": True}])
def test_endpoint_parity_requires_the_exact_score_only_wire_contract(
    project, monkeypatch, response
):
    monkeypatch.setenv("TEST_ENDPOINT_TOKEN", "synthetic-token")
    monkeypatch.setattr(cloud, "http_post", lambda *_: response)
    config = {
        "service_url": "https://endpoint.example",
        "endpoint_token_env": "TEST_ENDPOINT_TOKEN",
    }
    with pytest.raises(ValueError, match="score field only|invalid reward"):
        cloud.endpoint_parity(
            project,
            config,
            project / "labels.example.jsonl",
            project / "tool-probes.example.jsonl",
            project / "bad-parity.json",
        )
