"""Explicit cloud operations with dry-run plans and durable submission receipts."""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import hosted_ops
import optimizer_results as native_results
from evaluator_adapter import code_source
from journey import (
    SKILL,
    digest,
    implementation_digest,
    load_hook,
    read_json,
    read_jsonl,
    score,
    validate_data,
    value_digest,
    write_json,
    write_jsonl,
)


def sanitized_text(text, redact_values=()):
    for token in (*redact_values, os.getenv("HILLCLIMB_ENDPOINT_TOKEN")):
        if token:
            text = text.replace(token, "<redacted>")
    return text


def command(argv, cwd, redact_values=()):
    label = Path(argv[0]).name
    if argv[0] == "az" and "--subscription" in argv:
        index = argv.index("--subscription")
        argv = argv[:index] + argv[index + 2 :] + argv[index : index + 2]
    executable = shutil.which(argv[0])
    if executable is None:
        raise FileNotFoundError(f"Required CLI is not installed: {argv[0]}")
    if os.name == "nt" and argv[0] == "az" and executable.lower().endswith(".cmd"):
        # Azure's Windows MSI ships a Python launcher, not a native az executable.
        python = Path(executable).resolve().parents[1] / "python.exe"
        if not python.is_file():
            raise FileNotFoundError("Azure CLI MSI Python launcher is missing")
        argv = [str(python), "-X", "utf8", "-IBm", "azure.cli", *argv[1:]]
    else:
        argv = [executable, *argv[1:]]
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=1200,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"{label} timed out; command arguments and output were withheld. "
            "Reconcile operation state before retrying."
        ) from None
    except (OSError, UnicodeError):
        raise RuntimeError(
            f"{label} could not execute or decode its output; details were withheld"
        ) from None
    if result.returncode:
        error = sanitized_text(result.stderr or result.stdout, redact_values)
        raise RuntimeError(f"{argv[0]} failed ({result.returncode}): {error}")
    return sanitized_text(result.stdout, redact_values)


def redact_credentials(value):
    if isinstance(value, dict):
        return {
            key: "<redacted>"
            if key.lower()
            in {
                "headers",
                "authorization",
                "api_key",
                "api-key",
                "password",
                "secret",
                "token",
            }
            else redact_credentials(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    return value


def sdk_client(base_url):
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import OpenAI

    scope = (
        "https://cognitiveservices.azure.com/.default"
        if ".openai.azure.com" in base_url
        else "https://ai.azure.com/.default"
    )
    key = os.getenv("MAI_API_KEY") or get_bearer_token_provider(
        DefaultAzureCredential(process_timeout=60), scope
    )
    # A timed-out POST is ambiguous; do not retry billable creates automatically.
    return OpenAI(base_url=base_url.rstrip("/") + "/", api_key=key, max_retries=0, timeout=120)


@contextmanager
def project_client(endpoint):
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    with (
        DefaultAzureCredential(process_timeout=60) as credential,
        AIProjectClient(
            endpoint=endpoint, credential=credential, allow_preview=True, retry_total=0
        ) as client,
    ):
        yield client


def register_evaluator(project, config, receipt):
    if config["optimizer"].get("evaluator_type") == "builtin":
        raise ValueError("Built-in evaluators do not need registration")
    from azure.ai.projects.models import (
        CodeBasedEvaluatorDefinition,
        EvaluatorMetric,
        EvaluatorVersion,
        PromptBasedEvaluatorDefinition,
    )

    path = claim(
        receipt,
        "register-evaluator",
        evaluator_fingerprint(project, config),
    )
    mapping = evaluator_mapping(config)
    schema = {
        "type": "object",
        "properties": {key: runtime_field_schema(key) for key in mapping},
        "required": list(mapping),
    }
    metrics = {
        "quality": EvaluatorMetric(
            type="continuous",
            desirable_direction="increase",
            min_value=0,
            max_value=1,
            threshold=0.75,
        )
    }
    if config["optimizer"].get("evaluator_type", "prompt") == "code":
        definition = CodeBasedEvaluatorDefinition(
            code_text=code_source(evaluator_source(project, config).read_text(encoding="utf-8")),
            data_schema=schema,
            metrics=metrics,
        )
    elif config["optimizer"].get("evaluator_type", "prompt") == "prompt":
        definition = PromptBasedEvaluatorDefinition(
            prompt_text=evaluator_source(project, config).read_text(encoding="utf-8"),
            init_parameters={
                "type": "object",
                "properties": {"model": {"type": "string"}, "threshold": {"type": "number"}},
                "required": ["model", "threshold"],
            },
            data_schema=schema,
            metrics=metrics,
        )
    else:
        raise ValueError("Optimizer evaluator_type must be prompt or code")
    with project_client(config["project_endpoint"]) as client:
        evaluator = client.evaluators.create_version(
            name=config["optimizer"]["evaluator"],
            evaluator_version=EvaluatorVersion(
                evaluator_type="custom",
                categories=["quality"],
                definition=definition,
            ),
            headers={"Foundry-Features": "Evaluations=V1Preview"},
        )
        state = {
            "status": "completed",
            "evaluator": evaluator.as_dict(),
            **evaluator_fingerprint(project, config),
        }
        write_json(path, state)
        return state


def claim(receipt, action, config):
    path = Path(receipt)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump({"action": action, "status": "submitting", "config": config}, handle, indent=2)
    return path


def http_post(url, body, token):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def endpoint_parity(project, config, labels, tool_probes, output):
    project = Path(project)
    grader = load_hook(project / "grader.py", "grade")
    execute_tool = load_hook(project / "domain.py", "execute_tool")
    token = os.environ[config["endpoint_token_env"]]
    endpoint = config["service_url"].rstrip("/")
    if not endpoint.startswith("https://"):
        raise ValueError("Training service must use HTTPS")
    reviewed = read_jsonl(labels)
    probes = read_jsonl(tool_probes)
    tolerance = config.get("rft", {}).get("parity_tolerance", 0)
    if not isinstance(tolerance, (float, int)) or not 0 <= tolerance <= 1:
        raise ValueError("Endpoint parity tolerance must be in [0,1]")
    differences = []
    for row in reviewed:
        expected = score(grader, row["sample"], row["item"])
        actual = http_post(
            endpoint + "/grade", {"sample": row["sample"], "item": row["item"]}, token
        )["reward"]
        if (
            not isinstance(actual, (int, float))
            or not math.isfinite(actual)
            or not 0 <= actual <= 1
        ):
            raise ValueError(f"Endpoint returned an invalid reward for {row['id']}")
        differences.append(abs(actual - expected))
        if differences[-1] > tolerance + 1e-9:
            raise ValueError(f"Endpoint grader parity failed for {row['id']}")
    for index, probe in enumerate(probes):
        local = execute_tool(probe["name"], probe["arguments"])
        remote = http_post(
            endpoint + "/tools",
            {
                **probe,
                "call_id": f"parity-{index}",
            },
            token,
        )
        if remote["call_id"] != f"parity-{index}" or json.loads(remote["output"]) != local:
            raise ValueError(f"Tool parity failed for {probe['name']}")
    report = {
        "passed": True,
        "grader_sha256": digest(project / "grader.py"),
        "domain_sha256": digest(project / "domain.py"),
        "labels_sha256": digest(labels),
        "tool_probes_sha256": digest(tool_probes),
        "service_url": endpoint,
        "graded_samples": len(reviewed),
        "tool_names": sorted({probe["name"] for probe in probes}),
        "parity_tolerance": tolerance,
        "max_score_difference": max(differences),
    }
    write_json(output, report)
    return report


def evaluator_source(project, config):
    return Path(project) / (
        "grader.py" if config["optimizer"].get("evaluator_type") == "code" else "evaluator.md"
    )


def evaluator_fingerprint(project, config):
    if config["optimizer"].get("evaluator_type") == "builtin":
        return {"builtin_evaluator": config["optimizer"]["evaluator"]}
    result = {"evaluator_sha256": digest(evaluator_source(project, config))}
    if config["optimizer"].get("evaluator_type") == "code":
        result["transport_adapter_sha256"] = digest(
            Path(__file__).with_name("evaluator_adapter.py")
        )
    return result


def evaluator_mapping(config):
    mapping = config["optimizer"].get(
        "data_mapping",
        {
            "query": "query",
            "response": "response",
            "ground_truth": "ground_truth",
        },
    )
    allowed = {"query", "response", "ground_truth", "tool_calls", "tool_definitions"}
    if not mapping or any(value not in allowed or key != value for key, value in mapping.items()):
        raise ValueError("Evaluator mappings must use supported optimizer runtime fields")
    return mapping


def runtime_field_schema(name):
    return (
        {"type": "array", "items": {"type": "object"}}
        if name in ("tool_calls", "tool_definitions")
        else {"type": "string"}
    )


def evaluator_data(label, config=None):
    item = label["item"]
    encode = load_hook(SKILL / "assets" / "starter" / "workload.py", "encode_input")
    return {
        "query": encode(item),
        "response": (
            json.dumps([{"type": "output_text", "text": label["sample"]["output_text"]}])
            if config and config["optimizer"].get("evaluator_type") == "code"
            else label["sample"]["output_text"]
        ),
        "ground_truth": json.dumps(
            {
                key: value
                for key, value in item.items()
                if key not in ("messages", "context", "tools")
            },
            sort_keys=True,
        ),
        "tool_calls": label["sample"].get("output_tools", []),
        "tool_definitions": [
            {"type": "function", **tool["function"]} if "function" in tool else tool
            for tool in item.get("tools", [])
        ],
    }


def validate_evaluator(project, config, labels, receipt, timeout=600):
    if config["optimizer"].get("evaluator_type") == "builtin":
        raise ValueError("Built-ins do not require custom-rubric validation receipts")
    path = Path(receipt)
    fingerprints = {
        "labels_sha256": digest(labels),
        **evaluator_fingerprint(project, config),
    }
    resuming = path.exists()
    if resuming:
        state = read_json(path)
        if state["action"] != "validate-evaluator" or state["config"] != fingerprints:
            raise ValueError("Evaluator validation inputs changed")
        if "eval_id" not in state or "eval_run_id" not in state:
            raise ValueError("Validation outcome unknown: reconcile evaluations/runs before create")
    else:
        claim(path, "validate-evaluator", fingerprints)
    state = read_json(path)
    reviewed = read_jsonl(labels)
    settings = config["optimizer"]
    mapping = evaluator_mapping(config)
    headers = {"Foundry-Features": "Evaluations=V1Preview"}
    criterion = {
        "type": "azure_ai_evaluator",
        "name": settings["evaluator"],
        "evaluator_name": settings["evaluator"],
        "evaluator_version": settings["evaluator_version"],
        "initialization_parameters": {
            "model": settings["eval_model"],
            "threshold": settings["pass_threshold"],
        },
        "data_mapping": {
            key: "{{item." + value + "}}" for key, value in evaluator_mapping(config).items()
        },
    }
    with (
        project_client(config["project_endpoint"]) as project_api,
        project_api.get_openai_client(max_retries=0) as api,
    ):
        if resuming:
            evaluation = api.evals.retrieve(state["eval_id"], extra_headers=headers)
            criteria = evaluation.model_dump(mode="json", warnings=False)["testing_criteria"]
            if not any(
                row.get("evaluator_version") == criterion["evaluator_version"]
                and row.get("evaluator_name") == criterion["evaluator_name"]
                and row.get("initialization_parameters") == criterion["initialization_parameters"]
                and row.get("data_mapping") == criterion["data_mapping"]
                for row in criteria
            ):
                raise ValueError("Evaluator runtime settings differ from the persisted evaluation")
            run = api.evals.runs.retrieve(
                state["eval_run_id"], eval_id=evaluation.id, extra_headers=headers
            )
        else:
            evaluation = api.evals.create(
                name=config["run_id"] + "-grader-validation",
                data_source_config={
                    "type": "custom",
                    "item_schema": {
                        "type": "object",
                        "properties": {key: runtime_field_schema(key) for key in mapping.values()},
                        "required": list(mapping.values()),
                    },
                },
                testing_criteria=[criterion],
                extra_headers=headers,
            )
            state["eval_id"] = evaluation.id
            write_json(path, state)
            run = api.evals.runs.create(
                eval_id=evaluation.id,
                name=config["run_id"] + "-reviewed-labels",
                data_source={
                    "type": "jsonl",
                    "source": {
                        "type": "file_content",
                        "content": [
                            {
                                "item": {
                                    field: evaluator_data(label, config)[field]
                                    for field in mapping.values()
                                }
                            }
                            for label in reviewed
                        ],
                    },
                },
                extra_headers=headers,
            )
            state["eval_run_id"] = run.id
            write_json(path, state)
        deadline = time.monotonic() + timeout
        while run.status not in ("completed", "failed", "canceled"):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Evaluator validation still {run.status}; retain its IDs")
            time.sleep(10)
            run = api.evals.runs.retrieve(run.id, eval_id=evaluation.id, extra_headers=headers)
        outputs = list(
            api.evals.runs.output_items.list(run.id, eval_id=evaluation.id, extra_headers=headers)
        )
        state["outputs"] = [row.model_dump(mode="json") for row in outputs]
        state["status"] = run.status
        write_json(path, state)
        if run.status != "completed" or len(outputs) != len(reviewed):
            raise RuntimeError("Evaluator validation did not complete every reviewed label")
        errors = []
        for output in outputs:
            label = reviewed[int(output.datasource_item_id)]
            results = [result for result in output.results if result.name == settings["evaluator"]]
            if len(results) != 1 or results[0].score is None:
                raise RuntimeError(f"Evaluator returned an error or no score: {output.id}")
            value = results[0].score
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Evaluator score is outside the canonical range")
            errors.append(abs(value - label["human_score"]))
        state["label_mae"] = sum(errors) / len(errors)
        state["evaluator_settings"] = criterion
        state["project_endpoint"] = config["project_endpoint"]
        state["passed"] = state["label_mae"] <= settings["max_label_mae"]
        write_json(path, state)
        if not state["passed"]:
            raise ValueError("Optimizer evaluator disagrees with reviewed calibration labels")
        return state


def builtin_optimizer_data(project, config):
    spec = optimizer_config(project, config)
    rows = read_jsonl(spec["dataset"]["local_uri"])
    native = []
    for row in rows:
        item = row.get("item", row)
        messages = item.get("messages")
        simple = (
            isinstance(messages, list)
            and len(messages) == 1
            and messages[0].get("role") == "user"
            and not item.get("context")
        )
        if messages is None:
            query = row["query"]
            if (
                isinstance(query, str)
                and query.startswith("HILLCLIMB_WORKLOAD")
                and config.get("enable_evaluation") is not True
            ):
                raise ValueError("Workload envelopes require isolated evaluation mode")
        elif simple:
            query = messages[0]["content"]
        else:
            if config.get("enable_evaluation") is not True:
                raise ValueError("History/context envelopes require isolated evaluation mode")
            query = row["query"]
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Built-in optimizer queries must be nonempty text")
        if not isinstance(row.get("ground_truth", ""), str):
            raise TypeError("Optimizer ground truth must be a string when supplied")
        criteria = row.get("criteria")
        criteria = [] if criteria is None else criteria
        if not isinstance(criteria, list) or any(
            not isinstance(criterion, dict)
            or any(
                not isinstance(criterion.get(key), str) or not criterion[key].strip()
                for key in ("name", "instruction")
            )
            for criterion in criteria
        ):
            raise ValueError("Optimizer criteria require nonempty names and instructions")
        native.append(
            {
                "name": row.get("name", row.get("id")),
                "query": query,
                "ground_truth": row.get("ground_truth", ""),
                "criteria": criteria,
            }
        )
    if any(not isinstance(row["name"], str) or not row["name"].strip() for row in native) or len(
        {row["name"] for row in native}
    ) != len(native):
        raise ValueError("Optimizer examples require unique stable names")
    sha = value_digest(native)
    label = re.sub("[^a-z0-9-]", "-", config["run_id"].lower()).strip("-")[:35]
    if not label:
        raise ValueError("Run ID must contain an alphanumeric dataset label")
    return {"name": f"hc-{label}-{sha[:16]}", "version": "1", "sha256": sha, "rows": native}


def optimizer_plan(project, config, validation_receipt=None):
    for key, value in {
        "max_candidates": config["optimizer"]["max_candidates"],
        "max_concurrent_agent_runs": config["optimizer"].get("max_concurrent_agent_runs", 1),
    }.items():
        if type(value) is not int or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    kind = config["optimizer"].get("evaluator_type", "prompt")
    if kind not in ("prompt", "builtin"):
        raise ValueError(
            "This optimizer helper accepts built-in or calibrated prompt-rubric evaluators. "
            "Code evaluators are validated for ordinary evaluation, not established as "
            "supported by Agent Optimizer. Use evaluator_type=builtin for "
            "evaluators such as builtin.task_adherence; retain the canonical grader "
            "for independent acceptance."
        )
    if kind == "builtin":
        name = config["optimizer"]["evaluator"]
        if (
            not isinstance(name, str)
            or not name.startswith("builtin.")
            or len(name) <= len("builtin.")
        ):
            raise ValueError("Built-in evaluator names must start with builtin.")
        data = builtin_optimizer_data(project, config)
    elif config.get("enable_evaluation") is not True:
        raise ValueError(
            "Workload-envelope optimization requires an isolated evaluation deployment"
        )
    if kind == "prompt" and not validation_receipt:
        raise ValueError("Custom prompt rubrics require --evaluator-validation")
    if kind == "builtin" and validation_receipt is not None:
        raise ValueError("Built-ins do not use custom-rubric validation receipts")
    validated = read_json(validation_receipt) if validation_receipt else None
    if validated is not None:
        validate_optimizer_evidence(project, config, validated)
    spec = optimizer_config(project, config)
    rows = read_jsonl(spec["dataset"]["local_uri"])
    settings = config["optimizer"]
    source = (
        {"type": "dataset_reference", "name": data["name"], "version": data["version"]}
        if kind == "builtin"
        else {
            "type": "inline",
            "test_cases": [
                {"query": row["query"], "ground_truth": row["ground_truth"]} for row in rows
            ],
        }
    )
    evaluator = {"name": settings["evaluator"]}
    if kind == "prompt":
        evaluator.update(
            version=settings["evaluator_version"],
            initialization_parameters={
                "model": settings["eval_model"],
                "threshold": settings["pass_threshold"],
            },
        )
    attributes = settings.get("target_attributes")
    if attributes is None:
        attributes = (
            ["instructions"]
            if kind == "builtin"
            else (["instructions", "tools"] + (["skills"] if optimizer_skills(project) else []))
        )
    if not attributes or any(
        value not in ("instructions", "tools", "skills") for value in attributes
    ):
        raise ValueError("Unsupported agent optimization target attributes")
    return {
        "display_name": config["run_id"],
        "target_configuration": {
            "type": "foundry_agent",
            "name": config["agent_name"],
            "version": config["agent_version"],
        },
        "optimization_model_configuration": {"model": settings["optimize_model"]},
        "optimization_configuration": {
            "type": "agent_optimization",
            "goal": "improve_quality",
            "evaluation_configuration": {
                "training_set": {"type": "target_completion", "source": source},
                "evaluators": [evaluator],
                "evaluation_model": {"model": settings["eval_model"]},
                "max_concurrent_agent_runs": settings.get("max_concurrent_agent_runs", 1),
            },
            "candidate_search_configuration": {"max_candidates": settings["max_candidates"]},
            "baseline_agent_configuration": {
                "system_prompt": (Path(project) / "instructions.md").read_text(encoding="utf-8"),
                "current_model": config.get("model_override") or config["model"]["deployment"],
                "skills": optimizer_skills(project),
                "tools": read_json(Path(project) / "tools.json"),
            },
            "agent_optimization_space": {"target_attributes": attributes},
        },
    }


def validate_optimizer_evidence(project, config, validated):
    if not validated.get("passed"):
        raise ValueError("Optimizer requires successful known-response evaluator validation")
    if any(
        validated["config"].get(key) != value
        for key, value in evaluator_fingerprint(project, config).items()
    ):
        raise ValueError("Optimizer evaluator changed after validation")
    settings = config["optimizer"]
    expected = validated["evaluator_settings"]
    if (
        expected.get("evaluator_name") != settings["evaluator"]
        or expected["evaluator_version"] != settings["evaluator_version"]
        or expected["initialization_parameters"]
        != {
            "model": settings["eval_model"],
            "threshold": settings["pass_threshold"],
        }
        or validated["project_endpoint"] != config["project_endpoint"]
        or expected.get("data_mapping")
        != {key: "{{item." + value + "}}" for key, value in evaluator_mapping(config).items()}
    ):
        raise ValueError(
            "Optimizer evaluator model/version/threshold/project changed after validation"
        )


def optimizer_skills(project):
    catalog = load_hook(SKILL / "assets" / "starter" / "workload.py", "skill_catalog")
    return catalog(project)


def submit_optimizer(config, plan, receipt, project=None):
    path = claim(receipt, "optimize", {"plan": plan})
    state = read_json(path)
    with project_client(config["project_endpoint"]) as api:
        if config["optimizer"].get("evaluator_type") == "builtin":
            from azure.core.exceptions import AzureError, ResourceNotFoundError

            if project is None:
                raise ValueError("Built-in submission requires the source project directory")
            data = builtin_optimizer_data(project, config)
            expected = plan["optimization_configuration"]["evaluation_configuration"][
                "training_set"
            ]["source"]
            if expected != {
                "type": "dataset_reference",
                "name": data["name"],
                "version": data["version"],
            }:
                raise ValueError("Optimizer dataset changed after planning")
            staged = Path(receipt).with_name(Path(receipt).stem + "-optimizer.jsonl")
            if staged.exists():
                raise FileExistsError(
                    "Staged optimizer data already exists; reconcile before retrying"
                )
            write_jsonl(staged, data["rows"])
            state.update(
                phase="register_dataset",
                dataset={
                    "name": data["name"],
                    "version": data["version"],
                    "sha256": data["sha256"],
                    "local_uri": str(staged),
                },
            )
            state["case_map"] = native_results.case_map(data["rows"])
            write_json(path, state)
            try:
                api.datasets.get(name=data["name"], version=data["version"])
            except ResourceNotFoundError:
                pass
            else:
                raise FileExistsError("Optimizer dataset exists; reconcile its registration")
            try:
                api.datasets.upload_file(
                    name=data["name"], version=data["version"], file_path=str(staged)
                )
            except AzureError as error:
                state.update(
                    status="needs_reconciliation",
                    error={"type": type(error).__name__, "phase": "register_dataset"},
                )
                write_json(path, state)
                raise RuntimeError(
                    "Dataset registration failed; details may contain credentials and were withheld. "
                    "Reconcile the recorded dataset before retrying."
                ) from None
            state["dataset"]["registered"] = True
            write_json(path, state)
        else:
            state["case_map"] = native_results.case_map(
                plan["optimization_configuration"]["evaluation_configuration"]["training_set"][
                    "source"
                ]["test_cases"]
            )
        state["phase"] = "create_optimization_job"
        write_json(path, state)
        poller = api.agents.begin_create_optimization_job(
            plan,
            polling=False,
            headers={"Foundry-Features": "Evaluations=V1Preview"},
        )
        state["operation_id"] = poller.details.get("job_id")
        if not isinstance(state["operation_id"], str) or not state["operation_id"]:
            raise RuntimeError("Optimizer submission returned no job ID; reconcile before retrying")
        write_json(path, state)
        job = api.agents.get_optimization_job(state["operation_id"])
        state.update(status=job.status, job=job.as_dict())
        write_json(path, state)
        return state


def optimizer_results(config, optimization_receipt, output):
    if Path(output).exists():
        raise FileExistsError("Native results report already exists")
    state = read_json(optimization_receipt)
    if state["status"] != "succeeded":
        raise ValueError("Native results require a completed optimization job")
    with project_client(config["project_endpoint"]) as project:
        candidates = [
            row.as_dict()
            for row in project.agents.list_optimization_candidates(job_id=state["operation_id"])
        ]
        runs, outputs = {}, {}
        with project.get_openai_client(max_retries=0) as api:
            headers = {"Foundry-Features": "Evaluations=V1Preview"}
            for candidate in candidates:
                evaluation = candidate.get("evaluation") or {}
                if not evaluation.get("eval_run_id") or not evaluation.get("eval_id"):
                    raise ValueError("Candidate did not produce a native evaluation")
                run_id, eval_id = evaluation["eval_run_id"], evaluation["eval_id"]
                runs[run_id] = api.evals.runs.retrieve(
                    run_id, eval_id=eval_id, extra_headers=headers
                ).model_dump(mode="json", warnings=False)
                outputs[run_id] = [
                    row.model_dump(mode="json")
                    for row in api.evals.runs.output_items.list(
                        run_id, eval_id=eval_id, extra_headers=headers
                    )
                ]
    report = redact_credentials(native_results.build_report(state, candidates, runs, outputs))
    write_json(output, report)
    return report


def reconcile_optimizer(config, receipt, operation_id):
    path = Path(receipt)
    state = read_json(path)
    if state.get("operation_id") not in (None, operation_id):
        raise ValueError("Receipt already belongs to a different optimizer job")
    plan = state["config"]["plan"]
    with project_client(config["project_endpoint"]) as api:
        job = api.agents.get_optimization_job(operation_id)
        details = job.as_dict()
        if (
            details["target_configuration"] != plan["target_configuration"]
            or details.get("display_name") != plan["display_name"]
        ):
            raise ValueError("Optimizer job does not match the recorded target and run name")
        state.update(operation_id=operation_id, status=job.status, job=details)
        write_json(path, state)
        return state


def candidate_profile(api, state, candidate_id):
    if state["status"] != "succeeded":
        raise ValueError("Export requires a completed optimizer job")
    baseline = state["job"]["optimization_configuration"]["baseline_agent_configuration"]
    profile = {
        "instructions": baseline["system_prompt"],
        "model": baseline["current_model"],
        "tool_definitions": baseline.get("tools") or [],
        "skills": baseline.get("skills") or [],
    }
    candidate = api.agents.get_optimization_candidate(state["operation_id"], candidate_id)
    details = candidate.as_dict()
    if details["status"] != "completed":
        raise ValueError("Candidate did not finish evaluation")
    mapping = {
        "instructions": "instructions",
        "tools": "tool_definitions",
        "skills": "skills",
        "model": "model",
    }
    for mutation in details.get("output", {}).get("mutations", []):
        if mutation["type"] not in mapping:
            raise ValueError(f"Unsupported candidate mutation: {mutation['type']}")
        profile[mapping[mutation["type"]]] = mutation["value"]
    return profile, details.get("evaluation")


def export_candidate(config, optimization_receipt, candidate_id, output):
    if Path(output).exists():
        raise FileExistsError("Candidate configuration already exported")
    state = read_json(optimization_receipt)
    with project_client(config["project_endpoint"]) as api:
        profile, evaluation = candidate_profile(api, state, candidate_id)
    write_json(output, profile)
    return {
        "candidate_id": candidate_id,
        "output": str(output),
        "sha256": digest(output),
        "evaluation": evaluation,
    }


def deploy_candidate(config, optimization_receipt, candidate_id, receipt, timeout=600):
    from azure.core.exceptions import AzureError

    optimization = read_json(optimization_receipt)
    with project_client(config["project_endpoint"]) as api:
        profile, evaluation = candidate_profile(api, optimization, candidate_id)
        path = claim(
            receipt,
            "deploy-candidate",
            {
                "optimization_receipt_sha256": digest(optimization_receipt),
                "profile_sha256": value_digest(profile),
                "candidate_id": candidate_id,
            },
        )
        state = read_json(path)
        state["evaluation"] = evaluation
        try:
            return hosted_ops.deploy(
                api, config, optimization, profile, candidate_id, state, path, timeout
            )
        except (AzureError, RuntimeError, ValueError, TypeError, OSError) as error:
            state.update(
                status="needs_reconciliation",
                error={
                    "type": type(error).__name__,
                    "phase": state.get("phase", "prepare_candidate"),
                },
            )
            write_json(path, state)
            raise RuntimeError(
                "Candidate application failed; inspect its receipt and existing version before retrying"
            ) from None


def smoke_hosted(config, text, receipt, timeout=300):
    from azure.core.exceptions import AzureError
    from openai import APIError

    if timeout <= 0 or not isinstance(text, str) or not text.strip():
        raise ValueError("Smoke requires positive timeout and nonempty input")
    path = claim(
        receipt,
        "smoke",
        {
            "agent_name": config["agent_name"],
            "agent_version": config["agent_version"],
            "input_sha256": value_digest(text),
        },
    )
    state = read_json(path)
    try:
        with project_client(config["project_endpoint"]) as api:
            return hosted_ops.smoke(api, config, text, state, path, timeout)
    except (AzureError, APIError, RuntimeError, ValueError, TypeError, OSError) as error:
        state.update(status="failed", error={"type": type(error).__name__})
        write_json(path, state)
        raise RuntimeError("Hosted smoke failed; inspect its receipt before retrying") from None


def rft_plan(project, config, prepared, calibration_path, receipts_path, labels_path, parity_path):
    project, prepared = Path(project), Path(prepared)
    calibration = read_json(calibration_path)
    if not calibration["eligible"]:
        raise ValueError(f"RFT blocked by calibration: {calibration['reasons']}")
    if calibration["receipts_sha256"] != digest(receipts_path):
        raise ValueError("Calibration receipts changed")
    if calibration["labels_sha256"] != digest(labels_path):
        raise ValueError("Reviewed calibration labels changed")
    if calibration["grader_sha256"] != digest(project / "grader.py"):
        raise ValueError("Grader changed after calibration")
    adapter = project / config["rft"].get("calibration_adapter", "agent.py")
    current_policy = load_hook(adapter, "policy_contract")()
    if (
        calibration.get("policy_sha256") != value_digest(current_policy)
        or calibration.get("implementation_sha256") != implementation_digest(adapter)
        or calibration.get("adapter_sha256") != [digest(adapter)]
    ):
        raise ValueError(
            "Model, policy, tools, runtime or implementation changed after calibration"
        )
    if current_policy["model"] != config["rft"]["source_deployment"]:
        raise ValueError("RFT source deployment differs from the calibrated inference model")
    manifest = read_json(prepared / "manifest.json")
    all_rows = []
    for split, expected_hash in manifest["files"].items():
        path = prepared / f"{split}.jsonl"
        if digest(path) != expected_hash:
            raise ValueError("Prepared dataset changed")
        if manifest["counts"][split]:
            all_rows.extend(read_jsonl(path))
    validate_data(all_rows)
    if not {manifest["files"]["train"], manifest["files"]["validation"]}.issubset(
        set(calibration["data_sha256"])
    ):
        raise ValueError("Calibration must cover the submitted training and validation data")
    train = read_jsonl(prepared / "train.jsonl")
    validation = read_jsonl(prepared / "validation.jsonl")
    instructions = current_policy["instructions"].strip()
    for row in train + validation:
        if (
            row["messages"][0]["role"] != "system"
            or row["messages"][0]["content"].strip() != instructions
        ):
            raise ValueError("Training examples must contain the selected policy instructions")
    if len(train) < config["rft"]["min_train_examples"] or len(validation) < 1:
        raise ValueError("Too few training/validation examples for selected service")
    capability = config["rft"]["capability_evidence"]
    if not capability.get("source") or not capability.get("checked_at"):
        raise ValueError("Current model/region/SKU access evidence is required")
    age = (datetime.now(UTC).date() - date.fromisoformat(capability["checked_at"][:10])).days
    if not 0 <= age <= 7:
        raise ValueError("RFT capability evidence must be checked within the last seven days")
    target = {
        "model": config["rft"]["model"],
        "region": config["region"],
        "training_type": config["rft"]["training_type"],
        "source_deployment": current_policy["model"],
    }
    if any(capability.get(key) != value for key, value in target.items()):
        raise ValueError(
            "Capability evidence does not cover this exact RFT model/region/SKU/policy"
        )
    if not capability.get("reinforcement_supported"):
        raise ValueError("Selected model does not have confirmed reinforcement support")
    base_url = config["rft"]["base_url"]
    if not base_url.startswith("https://"):
        raise ValueError("RFT endpoint must use HTTPS")
    threshold = config["rft"]["pass_threshold"]
    if not isinstance(threshold, (int, float)) or not 0 < threshold < 1:
        raise ValueError("Pass threshold must be strictly between 0 and 1")
    tools = config["rft"].get("tools", [])
    grader = {
        "type": "python",
        "name": "project_reward",
        "source": (project / "grader.py").read_text(encoding="utf-8"),
    }
    if config["rft"]["grader_type"] == "endpoint":
        if parity_path is None:
            raise ValueError("Endpoint grader requires a parity receipt")
        parity = read_json(parity_path)
        if not parity["passed"] or parity["grader_sha256"] != digest(project / "grader.py"):
            raise ValueError("Endpoint grader parity is stale or failed")
        if parity["domain_sha256"] != digest(project / "domain.py"):
            raise ValueError("Training tool implementation changed after parity")
        if parity["service_url"] != config["service_url"].rstrip("/"):
            raise ValueError("Endpoint changed after parity")
        if parity["labels_sha256"] != digest(labels_path):
            raise ValueError("Endpoint parity must cover reviewed calibration labels")
        if parity.get("parity_tolerance", 0) != config["rft"].get("parity_tolerance", 0):
            raise ValueError("Endpoint parity tolerance changed after validation")
        if {tool["name"] for tool in tools} - set(parity["tool_names"]):
            raise ValueError("Every training tool must have a parity probe")
        grader = {
            "type": "endpoint",
            "name": "project_reward",
            "url": config["service_url"].rstrip("/") + "/grade",
            "rate_limit": config["rft"]["grader_rate_limit"],
            "pass_threshold": threshold,
        }
    elif config["rft"]["grader_type"] != "python":
        raise ValueError("Bundled RFT submission supports python or endpoint graders")
    elif load_hook(project / "grader.py", "grade").__globals__.get("REQUIRES_NETWORK"):
        raise ValueError("Network/semantic graders require endpoint grading, not Python RFT")
    if tools and config["rft"]["grader_type"] != "endpoint":
        raise ValueError("Tool-calling RFT requires endpoint parity in this starter")
    if tools and not capability.get("agentic_supported"):
        raise ValueError("Agentic RFT access is not confirmed")
    expected_names = {tool["name"] for tool in tools}
    project_schemas = read_json(project / "tools.json")
    if {tool["function"]["name"] for tool in project_schemas} != expected_names:
        raise ValueError("Training tools must match the selected inference tool contract")
    for row in train + validation:
        schemas = row.get("tools", [])
        names = {tool["function"]["name"] for tool in schemas}
        if names != expected_names:
            raise ValueError("Dataset tool schemas and job tools differ")
        if schemas != project_schemas:
            raise ValueError("Training tool schemas differ from the selected inference schemas")
        if any(
            tool.get("type") != "function"
            or not isinstance(tool["function"].get("parameters"), dict)
            for tool in schemas
        ):
            raise ValueError("RFT examples require Chat Completions function schemas")
    reinforcement = {
        "grader": grader,
        "pass_threshold": threshold,
        "hyperparameters": config["rft"]["hyperparameters"],
    }
    if tools:
        reinforcement["tools"] = [
            {"name": tool["name"], "server_url": config["service_url"].rstrip("/") + "/tools"}
            for tool in tools
        ]
        reinforcement["max_episode_steps"] = config["rft"]["max_episode_steps"]
    return {
        "model": config["rft"]["model"],
        "suffix": config["rft"]["suffix"],
        "trainingType": config["rft"]["training_type"],
        "method": {"type": "reinforcement", "reinforcement": reinforcement},
    }


def submit_rft(project, config, prepared, plan, receipt):
    path = claim(
        receipt,
        "rft-submit",
        {
            "plan": plan,
            "train_sha256": digest(Path(prepared) / "train.jsonl"),
            "validation_sha256": digest(Path(prepared) / "validation.jsonl"),
            "project": str(Path(project).resolve()),
        },
    )
    state = read_json(path)
    client = sdk_client(config["rft"]["base_url"])
    for split in ("train", "validation"):
        with (Path(prepared) / f"{split}.jsonl").open("rb") as handle:
            uploaded = client.files.create(file=handle, purpose="fine-tune")
        state[f"{split}_file_id"] = uploaded.id
        write_json(path, state)
        deadline = time.monotonic() + 600
        while True:
            resource = client.files.retrieve(uploaded.id)
            if resource.status in ("processed", "completed"):
                break
            if resource.status in ("error", "failed") or time.monotonic() >= deadline:
                raise RuntimeError(f"File {uploaded.id} not processed: {resource.status}")
            time.sleep(5)
    body = json.loads(json.dumps(plan))
    reinforcement = body["method"]["reinforcement"]
    if config["rft"]["grader_type"] == "endpoint":
        headers = {"Authorization": f"Bearer {os.environ[config['endpoint_token_env']]}"}
        reinforcement["grader"]["headers"] = headers
        for tool in reinforcement.get("tools", []):
            tool["headers"] = headers
    body.update(training_file=state["train_file_id"], validation_file=state["validation_file_id"])
    training_type = body.pop("trainingType")
    job = client.fine_tuning.jobs.create(**body, extra_body={"trainingType": training_type})
    state.update(job_id=job.id, status=job.status)
    write_json(path, state)
    return {"job_id": job.id, "status": job.status}


def monitor_rft(config, receipt, timeout=3600):
    path = Path(receipt)
    state = read_json(path)
    if "job_id" not in state:
        raise ValueError("Submission outcome unknown: reconcile jobs/files before any new create")
    client = sdk_client(config["rft"]["base_url"])
    deadline = time.monotonic() + timeout
    while True:
        job = client.fine_tuning.jobs.retrieve(state["job_id"])
        state["status"] = job.status
        state["job"] = redact_credentials(job.model_dump(mode="json"))
        state["checkpoints"] = (
            [
                row.model_dump(mode="json")
                for row in client.fine_tuning.jobs.checkpoints.list(state["job_id"])
            ]
            if job.status == "succeeded"
            else []
        )
        write_json(path, state)
        if job.status == "succeeded":
            return state
        if job.status in ("failed", "cancelled"):
            raise RuntimeError(f"Training ended {job.status}: {job.error}")
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Still {job.status}; resume monitoring job {job.id}, not submission"
            )
        time.sleep(30)


def monitor_optimizer(config, receipt, timeout=3600):
    from azure.core.exceptions import (
        HttpResponseError,
        ServiceRequestError,
        ServiceResponseError,
    )
    from azure.identity import CredentialUnavailableError

    path = Path(receipt)
    state = read_json(path)
    operation_id = state.get("operation_id")
    if not operation_id:
        match = re.search(r"Job ID:\s*(\S+)", "\n".join(state.get("outputs", [])))
        if match:
            operation_id = match.group(1)
            state["operation_id"] = operation_id
            write_json(path, state)
    if not operation_id:
        raise ValueError("Optimizer outcome unknown: reconcile jobs before any new submission")
    if timeout <= 0:
        raise ValueError("Monitoring timeout must be positive")
    deadline = time.monotonic() + timeout
    heartbeat, previous = 0, None
    with project_client(config["project_endpoint"]) as client:
        while True:
            for attempt in range(3):
                try:
                    job = client.agents.get_optimization_job(operation_id)
                    break
                except (
                    CredentialUnavailableError,
                    ServiceRequestError,
                    ServiceResponseError,
                    HttpResponseError,
                ) as error:
                    transient = (
                        isinstance(error, CredentialUnavailableError)
                        or not isinstance(error, HttpResponseError)
                        or error.status_code in (408, 429, 500, 502, 503, 504)
                    )
                    retry = transient and attempt < 2 and time.monotonic() < deadline
                    state["monitor"] = {
                        "status": "retrying_read" if retry else "disconnected",
                        "attempt": attempt + 1,
                        "error_type": type(error).__name__,
                        "http_status": getattr(error, "status_code", None),
                        "time": datetime.now(UTC).isoformat(),
                    }
                    write_json(path, state)
                    print(
                        json.dumps({"operation_id": operation_id, **state["monitor"]}), flush=True
                    )
                    if not retry:
                        raise RuntimeError(
                            f"Monitor disconnected for {operation_id}; repair access and resume "
                            "this receipt. The cloud job was not marked failed or resubmitted."
                        ) from None
                    time.sleep(5)
            state["job"] = job.as_dict()
            state["status"] = job.status
            state["monitor"] = {
                "status": "connected",
                "last_successful_poll": datetime.now(UTC).isoformat(),
            }
            write_json(path, state)
            progress = {
                "operation_id": operation_id,
                "status": job.status,
                "summary": (state["job"].get("result") or {}).get("candidate_summary"),
            }
            if progress != previous or time.monotonic() >= heartbeat:
                print(json.dumps(progress), flush=True)
                previous, heartbeat = progress, time.monotonic() + 120
            if job.status == "succeeded":
                state["monitor"]["status"] = "finished"
                write_json(path, state)
                return state
            if job.status in ("failed", "cancelled"):
                state["monitor"]["status"] = "finished"
                write_json(path, state)
                raise RuntimeError(f"Optimizer {operation_id} ended {job.status}: {job.error}")
            if time.monotonic() >= deadline:
                state["monitor"]["status"] = "timeout"
                write_json(path, state)
                raise TimeoutError(
                    f"Optimizer {operation_id} still {job.status}; resume monitoring"
                )
            time.sleep(15)


def cli_plan(action, project, config):
    az = ["az", "--subscription", config["subscription"]]
    rg, account = config["resource_group"], config["account"]
    if action == "provision":
        return [
            az
            + [
                "deployment",
                "group",
                "create",
                "-g",
                rg,
                "--name",
                config["run_id"],
                "--template-file",
                str(SKILL / "assets" / "project.bicep"),
                "--parameters",
                f"accountName={account}",
                f"projectName={config['project_name']}",
                f"location={config['region']}",
                "-o",
                "json",
            ]
        ]
    if action == "deploy-model":
        model = config["model"]
        return [
            az
            + [
                "cognitiveservices",
                "account",
                "deployment",
                "create",
                "-g",
                rg,
                "-n",
                account,
                "--deployment-name",
                model["deployment"],
                "--model-name",
                model["name"],
                "--model-version",
                model["version"],
                "--model-format",
                model["format"],
                "--sku-name",
                model["sku"],
                "--sku-capacity",
                str(model["capacity"]),
                "-o",
                "json",
            ]
        ]
    if action == "grant-inference":
        principal = config["inference_principal_id"]
        scope = (
            f"/subscriptions/{config['subscription']}/resourceGroups/{rg}"
            f"/providers/Microsoft.CognitiveServices/accounts/{account}"
        )
        assignment = str(uuid.uuid5(uuid.NAMESPACE_URL, config["run_id"] + principal + scope))
        return [
            az
            + [
                "role",
                "assignment",
                "create",
                "--name",
                assignment,
                "--assignee-object-id",
                principal,
                "--assignee-principal-type",
                config["inference_principal_type"],
                "--role",
                config["inference_role_definition_id"],
                "--scope",
                scope,
                "-o",
                "json",
            ]
        ]
    if action == "grant-project-access":
        principal = config["developer_principal_id"]
        scope = config["project_id"]
        assignment = str(uuid.uuid5(uuid.NAMESPACE_URL, principal + scope + "project-manager"))
        return [
            az
            + [
                "role",
                "assignment",
                "create",
                "--name",
                assignment,
                "--assignee-object-id",
                principal,
                "--assignee-principal-type",
                config["developer_principal_type"],
                "--role",
                "eadc314b-1a2d-4efa-be10-5d325db5065e",
                "--scope",
                scope,
                "-o",
                "json",
            ]
        ]
    if action == "init-hosted":
        if not (Path(project) / "azure.yaml").is_file():
            raise ValueError("A project-local azure.yaml is required to avoid modifying a parent")
        return [
            [
                "azd",
                "ai",
                "agent",
                "init",
                "--src",
                str(Path(project).resolve()),
                "--kind",
                "hosted",
                "--agent-name",
                config["agent_name"],
                "--project-id",
                config["project_id"],
                "--model-deployment",
                config["model"]["deployment"],
                "--deploy-mode",
                "code",
                "--runtime",
                config["runtime"],
                "--entry-point",
                "agent.py",
                "--protocol",
                "responses",
                "--no-prompt",
            ]
        ]
    if action == "deploy-hosted":
        return [["azd", "deploy", "--no-prompt", "-o", "json"]]
    if action == "deploy-endpoints":
        name = config["service_name"]
        endpoint_env = config.get("endpoint_environment", {})
        if any(
            key != "MAI_JUDGE_PRICING_KEY"
            and ("KEY" in key.upper() or "TOKEN" in key.upper() or "SECRET" in key.upper())
            for key in endpoint_env
        ):
            raise ValueError("Endpoint environment configuration must not contain credentials")
        source = (
            ["--image", config["service_image"], "--registry-server", config["service_registry"]]
            if config.get("service_image")
            else ["--source", str(Path(project).resolve())]
        )
        token_reference = config.get("endpoint_token_secret_uri")
        if token_reference:
            load_hook(SKILL / "assets" / "starter" / "evaluation_auth.py", "validate_secret_uri")(
                token_reference
            )
        secret_value = (
            f"keyvaultref:{token_reference},identityref:system"
            if token_reference
            else "<environment-secret>"
        )
        return [
            az
            + [
                "containerapp",
                "up",
                "--name",
                name,
                "-g",
                rg,
                "--location",
                config["region"],
                "--environment",
                config["service_environment"],
                *source,
                "--ingress",
                "external",
                "--target-port",
                "8080",
            ],
            az
            + [
                "containerapp",
                "identity",
                "assign",
                "--name",
                name,
                "-g",
                rg,
                "--system-assigned",
                "-o",
                "json",
            ],
            az
            + [
                "containerapp",
                "secret",
                "set",
                "--name",
                name,
                "-g",
                rg,
                "--secrets",
                f"endpoint-token={secret_value}",
                "-o",
                "none",
            ],
            az
            + [
                "containerapp",
                "update",
                "--name",
                name,
                "-g",
                rg,
                "--set-env-vars",
                "HILLCLIMB_ENDPOINT_TOKEN=secretref:endpoint-token",
                *[f"{key}={value}" for key, value in endpoint_env.items()],
                "--min-replicas",
                "2",
                "--max-replicas",
                "5",
                "-o",
                "json",
            ],
        ]
    if action == "optimizer-status":
        return [
            [
                "azd",
                "ai",
                "agent",
                "optimize",
                "status",
                config["optimizer"]["operation_id"],
                "--project-endpoint",
                config["project_endpoint"],
                "-o",
                "json",
            ]
        ]
    raise ValueError(f"Unknown cloud action: {action}")


def optimizer_config(project, config):
    project = Path(project).resolve()
    settings = config["optimizer"]
    dataset = Path(settings["dataset"])
    if not dataset.is_absolute():
        dataset = project / dataset
    if not dataset.is_file():
        raise FileNotFoundError(f"Optimizer dataset is missing: {dataset}")
    rows = read_jsonl(dataset)
    if not rows or any(
        not row.get("query")
        or (
            row.get("split") not in (None, "optimizer")
            if settings.get("evaluator_type") == "builtin"
            else row.get("split") != "optimizer"
        )
        for row in rows
    ):
        raise ValueError("Optimizer requires query-shaped data from the optimizer-only split")
    return {
        "name": config["run_id"],
        "agent": {
            "name": config["agent_name"],
            "kind": "hosted",
            "version": config["agent_version"],
            "model": config["model"]["deployment"],
            "config": str(project / "metadata.yaml"),
        },
        "dataset": {"local_uri": str(dataset)},
        "evaluators": [settings["evaluator"]],
        "options": {
            "eval_model": settings["eval_model"],
            "optimization_model": settings["optimize_model"],
            "max_candidates": settings["max_candidates"],
        },
    }


def wire_hosted(project, config):
    import yaml

    path = Path(project) / "azure.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    matches = [
        service
        for service in manifest["services"].values()
        if service.get("host") == "azure.ai.agent"
    ]
    if len(matches) != 1:
        raise ValueError("Starter wiring requires exactly one hosted agent service")
    matches[0].setdefault("env", {}).update(
        {
            "MAI_OPENAI_BASE_URL": config["model_base_url"],
            "MAI_MODEL_DEPLOYMENT_NAME": config["model"]["deployment"],
        }
    )
    evaluation_enabled = config.get("enable_evaluation", False)
    if not isinstance(evaluation_enabled, bool):
        raise TypeError("enable_evaluation must be an explicit boolean")
    environment = matches[0]["env"]
    environment.pop("HILLCLIMB_EVALUATION_KEY", None)
    environment["HILLCLIMB_ENABLE_EVALUATION"] = "1" if evaluation_enabled else "0"
    if evaluation_enabled:
        secret_uri = config.get("evaluation_key_secret_uri")
        if not secret_uri:
            raise ValueError("Hosted evaluation requires a signing-key Key Vault secret reference")
        load_hook(SKILL / "assets" / "starter" / "evaluation_auth.py", "validate_secret_uri")(
            secret_uri
        )
        environment["HILLCLIMB_EVALUATION_KEY_SECRET_URI"] = secret_uri
    else:
        environment.pop("HILLCLIMB_EVALUATION_KEY_SECRET_URI", None)
    if "model_override" in config:
        if config["model_override"]:
            matches[0]["env"]["MAI_MODEL_OVERRIDE"] = config["model_override"]
        else:
            matches[0]["env"].pop("MAI_MODEL_OVERRIDE", None)
    matches[0]["sessionConfiguration"] = {"idleTimeoutSeconds": 120}
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    metadata_path = Path(project) / "metadata.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    metadata.update(name=config["agent_name"], model=config["model"]["deployment"])
    metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "provision",
            "deploy-model",
            "init-hosted",
            "deploy-hosted",
            "deploy-endpoints",
            "optimize",
            "optimizer-status",
            "deploy-candidate",
            "endpoint-parity",
            "rft-submit",
            "rft-monitor",
            "register-evaluator",
            "deploy-checkpoint",
            "grant-inference",
            "grant-project-access",
            "optimizer-monitor",
            "validate-evaluator",
            "optimizer-reconcile",
            "export-candidate",
            "smoke",
            "optimizer-results",
        ),
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approve-billable", action="store_true")
    parser.add_argument("--prepared")
    parser.add_argument("--calibration")
    parser.add_argument("--receipts")
    parser.add_argument("--labels")
    parser.add_argument("--tool-probes")
    parser.add_argument("--parity")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--input", default="Hello, what can you help me with?")
    parser.add_argument("--training-receipt")
    parser.add_argument("--evaluator-validation")
    parser.add_argument("--operation-id")
    parser.add_argument("--optimization-receipt")
    parser.add_argument("--candidate-id")
    parser.add_argument("--output")
    args = parser.parse_args()
    config = read_json(args.config)
    timeout = (
        args.timeout
        if args.timeout is not None
        else (300 if args.action == "smoke" else 600 if args.action == "deploy-candidate" else 3600)
    )
    if args.action == "smoke":
        print(
            json.dumps(
                {
                    "action": "smoke",
                    "agent": config["agent_name"],
                    "version": config["agent_version"],
                    "input": args.input,
                }
            )
        )
        if args.execute:
            if not args.approve_billable:
                parser.error("Smoke inference requires --approve-billable")
            print(json.dumps(smoke_hosted(config, args.input, args.receipt, timeout), indent=2))
        return
    if args.action == "optimizer-results":
        if not args.optimization_receipt or not args.output:
            parser.error("Native reporting requires --optimization-receipt and --output")
        if args.execute:
            path = claim(
                args.receipt,
                "optimizer-results",
                {
                    "optimization_receipt_sha256": digest(args.optimization_receipt),
                },
            )
            report = optimizer_results(config, args.optimization_receipt, args.output)
            result = {
                "status": "completed",
                "output": args.output,
                "sha256": digest(args.output),
                "candidates": len(report["candidates"]),
            }
            write_json(path, result)
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps({"action": args.action, "output": args.output}))
        return
    if args.action == "deploy-candidate":
        candidate_id = args.candidate_id or config.get("optimizer", {}).get("candidate_id")
        if not args.optimization_receipt or not candidate_id:
            parser.error("SDK deployment requires --optimization-receipt and --candidate-id")
        print(
            json.dumps(
                {
                    "action": args.action,
                    "candidate_id": candidate_id,
                    "isolation": "freeze current serving routes; create evaluation version",
                }
            )
        )
        if args.execute:
            if not args.approve_billable:
                parser.error("Candidate deployment requires --approve-billable")
            print(
                json.dumps(
                    deploy_candidate(
                        config, args.optimization_receipt, candidate_id, args.receipt, timeout
                    ),
                    indent=2,
                )
            )
        return
    if args.action == "export-candidate":
        if not args.execute:
            print(json.dumps({"action": args.action, "candidate_id": args.candidate_id}))
            return
        result = export_candidate(config, args.optimization_receipt, args.candidate_id, args.output)
        write_json(args.receipt, result)
        print(json.dumps(result, indent=2))
        return
    if args.action == "optimizer-reconcile":
        if not args.execute:
            print(json.dumps({"action": args.action, "operation_id": args.operation_id}))
            return
        print(json.dumps(reconcile_optimizer(config, args.receipt, args.operation_id), indent=2))
        return
    if args.action == "validate-evaluator":
        if not args.execute:
            print(json.dumps({"action": args.action, "labels": args.labels}))
            return
        if not args.approve_billable:
            parser.error("Remote evaluator validation is billable; approval is required")
        print(
            json.dumps(
                validate_evaluator(args.project, config, args.labels, args.receipt, timeout),
                indent=2,
            )
        )
        return
    if args.action == "endpoint-parity":
        if not args.execute:
            print(json.dumps({"action": args.action, "service_url": config["service_url"]}))
            return
        print(
            json.dumps(
                endpoint_parity(args.project, config, args.labels, args.tool_probes, args.receipt),
                indent=2,
            )
        )
        return
    if args.action in ("rft-monitor", "optimizer-monitor"):
        if not args.execute:
            print(json.dumps({"action": args.action, "receipt": args.receipt}))
            return
        monitor = monitor_rft if args.action == "rft-monitor" else monitor_optimizer
        print(json.dumps(monitor(config, args.receipt, timeout), indent=2))
        return
    if args.action == "optimize":
        plan = optimizer_plan(args.project, config, args.evaluator_validation)
    elif args.action == "rft-submit":
        plan = rft_plan(
            args.project,
            config,
            args.prepared,
            args.calibration,
            args.receipts,
            args.labels,
            args.parity,
        )
    elif args.action == "register-evaluator":
        if config["optimizer"].get("evaluator_type") == "builtin":
            parser.error("Built-in evaluators do not need registration; use optimize directly")
        plan = {
            "name": config["optimizer"]["evaluator"],
            "source_sha256": digest(evaluator_source(args.project, config)),
        }
    elif args.action == "deploy-checkpoint":
        training = read_json(args.training_receipt)
        if training["status"] != "succeeded":
            raise ValueError("Checkpoint deployment requires a successful training receipt")
        models = {row["fine_tuned_model_checkpoint"] for row in training["checkpoints"]}
        models.add(training["job"]["fine_tuned_model"])
        if config["model"]["name"] not in models:
            raise ValueError("Selected checkpoint is not from the recorded training job")
        plan = cli_plan("deploy-model", args.project, config)
    else:
        plan = cli_plan(args.action, args.project, config)
    print(json.dumps({"action": args.action, "plan": plan}, indent=2))
    if not args.execute:
        return
    if args.action != "optimizer-status" and not args.approve_billable:
        parser.error("Cloud mutation requires --approve-billable for this reviewed operation")
    if args.action == "rft-submit":
        print(json.dumps(submit_rft(args.project, config, args.prepared, plan, args.receipt)))
        return
    if args.action == "register-evaluator":
        print(json.dumps(register_evaluator(args.project, config, args.receipt), indent=2))
        return
    if args.action == "optimize":
        print(
            json.dumps(submit_optimizer(config, plan, args.receipt, project=args.project), indent=2)
        )
        return
    path = claim(args.receipt, args.action, {"sha256": digest(args.config), "plan": plan})
    if args.action == "deploy-hosted":
        wire_hosted(args.project, config)
    state = read_json(path)
    state["outputs"] = []
    redact_values = []
    for argv in plan:
        if args.action == "deploy-endpoints" and "<environment-secret>" in " ".join(argv):
            token = os.environ[config["endpoint_token_env"]]
            redact_values.append(token)
            argv = [value.replace("<environment-secret>", token) for value in argv]
        try:
            output = command(argv, args.project, redact_values)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            state["status"] = "needs_reconciliation"
            state["error"] = {
                "type": type(error).__name__,
                "message": sanitized_text(str(error), redact_values),
            }
            write_json(path, state)
            raise
        state["outputs"].append(output)
        write_json(path, state)
    if args.action == "init-hosted":
        wire_hosted(args.project, config)
    state["status"] = "completed"
    write_json(path, state)


if __name__ == "__main__":
    main()
