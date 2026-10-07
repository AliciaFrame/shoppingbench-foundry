from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import NotFoundError, OpenAI

from shoppingbench_foundry.tool_definitions import chat_completions_tools

SUFFIXES = {
    "product": "mai-sb-product-rft",
    "shop": "mai-sb-shop-rft",
    "voucher": "mai-sb-voucher-rft",
    "web": "mai-sb-web-rft",
}
TOOL_NAMES = ["find_product", "view_product_information", "recommend_product", "terminate"]
WEB_SEARCH_TOOL_NAMES = ["web_search", *TOOL_NAMES]


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _client() -> OpenAI:
    base_url = _required_env("MAI_RFT_BASE_URL").rstrip("/") + "/"
    api_key: str | Any = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    if not api_key:
        scope = (
            "https://ai.azure.com/.default"
            if ".services.ai.azure.com/" in base_url
            else "https://cognitiveservices.azure.com/.default"
        )
        api_key = get_bearer_token_provider(
            DefaultAzureCredential(),
            scope,
        )
    return OpenAI(api_key=api_key, base_url=base_url)


def _model() -> str:
    return os.getenv("MAI_RFT_MODEL", "mai-code-1.1-flash").strip()


def _validate_model(client: OpenAI, model_id: str) -> None:
    try:
        models = client.models.list().data
    except NotFoundError:
        # Project-scoped Foundry endpoints expose files/jobs but not /models.
        return
    model = next((entry for entry in models if entry.id == model_id), None)
    if model is None:
        raise RuntimeError(f"RFT model is not available on this resource: {model_id}")
    capabilities = getattr(model, "capabilities", None) or {}
    if hasattr(capabilities, "model_dump"):
        capabilities = capabilities.model_dump()
    if not capabilities.get("global_fine_tune") and not capabilities.get("devtier_fine_tune"):
        raise RuntimeError(f"Model does not advertise a fine-tuning SKU: {model_id}")


def _wait_for_file(client: OpenAI, file_id: str) -> None:
    while True:
        file = client.files.retrieve(file_id)
        if file.status in {"processed", "completed"}:
            return
        if file.status in {"error", "failed"}:
            raise RuntimeError(f"File processing failed for {file_id}: {file.status_details}")
        time.sleep(2)


def _validate_agentic_dataset(path: Path, task: str = "web") -> list[str]:
    with path.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if not rows:
        raise ValueError(f"{path} is empty")
    expected_names: list[str] | None = None
    for index, row in enumerate(rows, start=1):
        if not isinstance(row.get("messages"), list) or not row["messages"]:
            raise ValueError(f"{path}:{index} must contain non-empty messages")
        tools = row.get("tools")
        if not isinstance(tools, list):
            raise TypeError(
                f"{path}:{index} is missing per-example tools required by agentic RFT"
            )
        names = [
            tool.get("function", {}).get("name")
            for tool in tools
            if isinstance(tool, dict)
        ]
        if names not in (TOOL_NAMES, WEB_SEARCH_TOOL_NAMES):
            raise ValueError(
                f"{path}:{index} tool names must match a supported job config"
            )
        if expected_names is None:
            expected_names = names
        elif names != expected_names:
            raise ValueError(f"{path}:{index} tool names differ from earlier rows")
        if any(
            tool.get("type") != "function"
            or not isinstance(tool.get("function", {}).get("parameters"), dict)
            for tool in tools
        ):
            raise ValueError(f"{path}:{index} contains an invalid function tool schema")
        expected_tools = chat_completions_tools(task)
        if names == TOOL_NAMES and tools != expected_tools:
            raise ValueError(
                f"{path}:{index} tool schemas do not match the canonical {task} contract"
            )
    return expected_names or []


def _tools(tool_names: list[str]) -> list[dict[str, Any]]:
    base_url = _required_env("SHOPPINGBENCH_TOOL_BASE_URL").rstrip("/")
    token = _required_env("SHOPPINGBENCH_API_TOKEN")
    return [
        {
            "name": name,
            "server_url": f"{base_url}/rft/tools/{name}",
            "headers": {"Authorization": f"Bearer {token}"},
        }
        for name in tool_names
    ]


def _suffix(task: str, grader_version: str = "v3") -> str:
    if grader_version == "v3":
        return SUFFIXES[task]
    if task == "web" and grader_version == "web-v5":
        return "mai-sb-web-rft-v5"
    raise ValueError(f"Unsupported grader version for {task}: {grader_version}")


def _endpoint_grader(
    task: str,
    threshold: float,
    grader_version: str = "v3",
) -> dict[str, Any]:
    base_url = _required_env("SHOPPINGBENCH_TOOL_BASE_URL").rstrip("/")
    token = _required_env("SHOPPINGBENCH_API_TOKEN")
    if grader_version not in {"v3", "web-v5"}:
        raise ValueError(f"Unsupported grader version: {grader_version}")
    if grader_version == "web-v5" and task != "web":
        raise ValueError("The Web v5 grader is Web-specific")
    route = {
        "v3": "/grade/v3",
        "web-v5": "/grade/web/v5",
    }[grader_version]
    return {
        "type": "endpoint",
        "name": f"shoppingbench_{task}_{grader_version}",
        "url": f"{base_url}{route}",
        "headers": {"Authorization": f"Bearer {token}"},
        "rate_limit": 10,
        "pass_threshold": threshold,
    }


def _reinforcement_config(
    task: str,
    threshold: float,
    grader_version: str,
    n_epochs: int = 2,
    batch_size: int = 8,
    learning_rate_multiplier: float = 1.0,
    eval_interval: int = 5,
    eval_samples: int = 10,
    max_episode_steps: int = 12,
    minimal_payload: bool = False,
    tool_names: list[str] | None = None,
) -> dict[str, Any]:
    reinforcement: dict[str, Any] = {
        "grader": _endpoint_grader(task, threshold, grader_version),
        "tools": _tools(tool_names or TOOL_NAMES),
        "max_episode_steps": max_episode_steps,
    }
    if not minimal_payload:
        reinforcement["pass_threshold"] = threshold
        reinforcement["hyperparameters"] = {
            "n_epochs": n_epochs,
            "batch_size": batch_size,
            "learning_rate_multiplier": learning_rate_multiplier,
            "eval_interval": eval_interval,
            "eval_samples": eval_samples,
            "compute_multiplier": 1.0,
            "reasoning_effort": "medium",
        }
    return reinforcement


def submit(
    task: str,
    data_dir: Path,
    threshold: float,
    minimal_payload: bool = False,
    grader_version: str = "v3",
    n_epochs: int = 2,
    batch_size: int = 8,
    learning_rate_multiplier: float = 1.0,
    eval_interval: int = 5,
    eval_samples: int = 10,
    max_episode_steps: int = 12,
) -> dict[str, Any]:
    client = _client()
    model_id = _model()
    _validate_model(client, model_id)
    train_path = data_dir / f"{task}-train.jsonl"
    validation_path = data_dir / f"{task}-validation.jsonl"
    tool_names = _validate_agentic_dataset(train_path, task)
    validation_tool_names = _validate_agentic_dataset(validation_path, task)
    if validation_tool_names != tool_names:
        raise ValueError("Training and validation datasets use different tool profiles")
    with train_path.open("rb") as handle:
        train_file = client.files.create(file=handle, purpose="fine-tune")
    with validation_path.open("rb") as handle:
        validation_file = client.files.create(file=handle, purpose="fine-tune")
    _wait_for_file(client, train_file.id)
    _wait_for_file(client, validation_file.id)

    training_type = os.getenv("MAI_RFT_TRAINING_TYPE", "").strip() or None
    reinforcement = _reinforcement_config(
        task,
        threshold,
        grader_version,
        n_epochs,
        batch_size,
        learning_rate_multiplier,
        eval_interval,
        eval_samples,
        max_episode_steps,
        minimal_payload,
        tool_names,
    )
    create_kwargs: dict[str, Any] = {
        "model": model_id,
        "training_file": train_file.id,
        "validation_file": validation_file.id,
        "suffix": _suffix(task, grader_version),
        "method": {
            "type": "reinforcement",
            "reinforcement": reinforcement,
        },
    }
    if training_type is not None:
        create_kwargs["extra_body"] = {"trainingType": training_type}
    job = client.fine_tuning.jobs.create(
        **create_kwargs,
    )
    return {
        "task": task,
        "model": model_id,
        "suffix": _suffix(task, grader_version),
        "grader_version": grader_version,
        "training_type": training_type,
        "job_id": job.id,
        "status": job.status,
        "training_file": train_file.id,
        "validation_file": validation_file.id,
        "pass_threshold": threshold,
        "submission_mode": "public-v1",
        "max_episode_steps": max_episode_steps,
        "hyperparameters": reinforcement.get("hyperparameters"),
        "tools": tool_names,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument("task", choices=SUFFIXES)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=repo_root / "rft" / "data")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-low-signal", action="store_true")
    parser.add_argument("--minimal-payload", action="store_true")
    parser.add_argument(
        "--grader-version",
        choices=("v3", "web-v5"),
        default="v3",
    )
    parser.add_argument("--pass-threshold", type=float)
    parser.add_argument("--n-epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate-multiplier", type=float, default=1.0)
    parser.add_argument("--eval-interval", type=int, default=5)
    parser.add_argument("--eval-samples", type=int, default=10)
    parser.add_argument("--max-episode-steps", type=int, default=12)
    parser.add_argument("--confirm-submit", action="store_true")
    args = parser.parse_args()
    if not args.confirm_submit:
        raise SystemExit("Submission is disabled unless --confirm-submit is supplied")
    if min(
        args.n_epochs,
        args.batch_size,
        args.eval_interval,
        args.eval_samples,
        args.max_episode_steps,
    ) < 1:
        raise SystemExit("Epochs, batch size, evaluation counts, and episode steps must be positive")
    if args.learning_rate_multiplier <= 0:
        raise SystemExit("Learning-rate multiplier must be positive")
    calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
    if not calibration.get("sufficient_signal") and not args.allow_low_signal:
        raise SystemExit(
            "Calibration did not produce a 25-50% base failure rate; "
            "harden the distribution or pass --allow-low-signal explicitly"
        )
    result = submit(
        args.task,
        args.data_dir,
        (
            args.pass_threshold
            if args.pass_threshold is not None
            else float(calibration["recommended_pass_threshold"])
        ),
        minimal_payload=args.minimal_payload,
        grader_version=args.grader_version,
        n_epochs=args.n_epochs,
        batch_size=args.batch_size,
        learning_rate_multiplier=args.learning_rate_multiplier,
        eval_interval=args.eval_interval,
        eval_samples=args.eval_samples,
        max_episode_steps=args.max_episode_steps,
    )
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
