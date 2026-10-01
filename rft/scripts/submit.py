from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import NotFoundError, OpenAI

SUFFIXES = {
    "product": "mai-sb-prod-rft1",
    "shop": "mai-sb-shop-rft1",
    "voucher": "mai-sb-vouch-rft1",
    "web": "mai-sb-web-rft2",
}
TOOL_NAMES = ["find_product", "view_product_information", "recommend_product", "terminate"]


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


def _validate_agentic_dataset(path: Path) -> None:
    with path.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if not rows:
        raise ValueError(f"{path} is empty")
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
        if names != TOOL_NAMES:
            raise ValueError(
                f"{path}:{index} tool names must exactly match the job config: {TOOL_NAMES}"
            )
        if any(
            tool.get("type") != "function"
            or not isinstance(tool.get("function", {}).get("parameters"), dict)
            for tool in tools
        ):
            raise ValueError(f"{path}:{index} contains an invalid function tool schema")


def _tools() -> list[dict[str, Any]]:
    base_url = _required_env("SHOPPINGBENCH_TOOL_BASE_URL").rstrip("/")
    token = _required_env("SHOPPINGBENCH_API_TOKEN")
    return [
        {
            "name": name,
            "server_url": f"{base_url}/rft/tools/{name}",
            "headers": {"Authorization": f"Bearer {token}"},
        }
        for name in TOOL_NAMES
    ]


def _private_preview_url() -> str:
    value = _required_env("RFT_PRIVATE_PREVIEW_JOBS_URL")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not parsed.path.endswith("/openai/1p/jobs")
    ):
        raise RuntimeError("RFT_PRIVATE_PREVIEW_JOBS_URL must be an HTTPS /openai/1p/jobs URL")
    return value


def _endpoint_grader(task: str, threshold: float) -> dict[str, Any]:
    base_url = _required_env("SHOPPINGBENCH_TOOL_BASE_URL").rstrip("/")
    token = _required_env("SHOPPINGBENCH_API_TOKEN")
    return {
        "type": "endpoint",
        "name": f"shoppingbench_{task}_canonical",
        "url": f"{base_url}/grade",
        "headers": {"Authorization": f"Bearer {token}"},
        "rate_limit": 10,
        "pass_threshold": threshold,
    }


def _private_preview_payload(
    *,
    model: str,
    training_file_id: str,
    validation_file_id: str,
    task: str,
    training_type: str,
    threshold: float,
) -> dict[str, Any]:
    return {
        "fineTuningJobType": "fineTuning",
        "fineTuningJobCreation": {
            "model": model,
            "training_file": training_file_id,
            "validation_file": validation_file_id,
            "trainingType": training_type,
            "suffix": SUFFIXES[task],
            "method": {
                "type": "reinforcement",
                "reinforcement": {
                    "grader": _endpoint_grader(task, threshold),
                    "tools": _tools(),
                    "max_episode_steps": 12,
                    "hyperparameters": {
                        "eval_interval": 5,
                        "eval_samples": 5,
                        "compute_multiplier": 1.0,
                        "learning_rate_multiplier": 1.0,
                        "reasoning_effort": "medium",
                        "number_of_epochs": 1,
                    },
                },
            },
        },
        "execution_config": {
            "type": "blossom",
            "blossom": {
                "recipe": {
                    "name": os.getenv("MAI_RFT_RECIPE_NAME", "mai-code-1-flash"),
                    "version": int(os.getenv("MAI_RFT_RECIPE_VERSION", "11")),
                }
            },
        },
    }


def _recipe_from_job(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        recipe = value.get("recipe")
        if isinstance(recipe, dict) and "name" in recipe and "version" in recipe:
            return {"name": recipe["name"], "version": recipe["version"]}
        for nested in value.values():
            found = _recipe_from_job(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _recipe_from_job(nested)
            if found is not None:
                return found
    return None


def _post_private_preview(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = _required_env("AZURE_OPENAI_API_KEY")
    request = Request(
        _private_preview_url(),
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={"api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Private-preview submission failed with HTTP {error.code}: {detail}"
        ) from error
    if not isinstance(result, dict):
        raise TypeError("Private-preview job response must be a JSON object")
    return result


def submit(
    task: str,
    data_dir: Path,
    threshold: float,
    minimal_payload: bool = False,
    private_preview: bool = False,
) -> dict[str, Any]:
    client = _client()
    model_id = _model()
    if not private_preview:
        _validate_model(client, model_id)
    train_path = data_dir / f"{task}-train.jsonl"
    validation_path = data_dir / f"{task}-validation.jsonl"
    _validate_agentic_dataset(train_path)
    _validate_agentic_dataset(validation_path)
    with train_path.open("rb") as handle:
        train_file = client.files.create(file=handle, purpose="fine-tune")
    with validation_path.open("rb") as handle:
        validation_file = client.files.create(file=handle, purpose="fine-tune")
    _wait_for_file(client, train_file.id)
    _wait_for_file(client, validation_file.id)

    server_recipe = None
    requested_recipe = None
    if private_preview:
        training_type = os.getenv("MAI_RFT_TRAINING_TYPE", "GlobalStandard")
        payload = _private_preview_payload(
            model=model_id,
            training_file_id=train_file.id,
            validation_file_id=validation_file.id,
            task=task,
            training_type=training_type,
            threshold=threshold,
        )
        requested_recipe = payload["execution_config"]["blossom"]["recipe"]
        job_data = _post_private_preview(payload)
        server_recipe = _recipe_from_job(job_data)
        job_id = str(job_data.get("id", ""))
        status = str(job_data.get("status", ""))
        if not job_id:
            raise RuntimeError("Private-preview submission returned no job ID")
    else:
        reinforcement: dict[str, Any] = {
            "grader": _endpoint_grader(task, threshold),
            "tools": _tools(),
            "max_episode_steps": 12,
        }
        if not minimal_payload:
            reinforcement.update(
                {
                    "pass_threshold": threshold,
                    "hyperparameters": {
                        "n_epochs": 2,
                        "batch_size": 8,
                        "learning_rate_multiplier": 1.0,
                        "eval_interval": 5,
                        "eval_samples": 1,
                        "compute_multiplier": 1.0,
                        "reasoning_effort": "medium",
                    },
                }
            )
        job = client.fine_tuning.jobs.create(
            model=model_id,
            training_file=train_file.id,
            validation_file=validation_file.id,
            suffix=SUFFIXES[task],
            extra_body={"trainingType": os.getenv("MAI_RFT_TRAINING_TYPE", "globalStandard")},
            method={
                "type": "reinforcement",
                "reinforcement": reinforcement,
            },
        )
        job_id = job.id
        status = job.status
    return {
        "task": task,
        "model": model_id,
        "suffix": SUFFIXES[task],
        "job_id": job_id,
        "status": status,
        "training_file": train_file.id,
        "validation_file": validation_file.id,
        "pass_threshold": threshold,
        "submission_mode": "exact-recipe-preview" if private_preview else "public-v1",
        "requested_recipe": requested_recipe,
        "server_returned_recipe": server_recipe,
        "recipe_confirmed": (None if server_recipe is None else server_recipe == requested_recipe),
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
    parser.add_argument("--private-preview", action="store_true")
    parser.add_argument("--pass-threshold", type=float)
    parser.add_argument("--confirm-submit", action="store_true")
    args = parser.parse_args()
    if not args.confirm_submit:
        raise SystemExit("Submission is disabled unless --confirm-submit is supplied")
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
        private_preview=args.private_preview,
    )
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if result["recipe_confirmed"] is False:
        raise SystemExit(
            "The submitted job did not confirm the requested Blossom recipe; "
            "inspect the saved job record before continuing"
        )


if __name__ == "__main__":
    main()
