from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI

FOUNDRY_TOKEN_SCOPE = "https://ai.azure.com/.default"
COGNITIVE_SERVICES_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _job_summary(job: Any) -> dict[str, Any]:
    value = job.model_dump() if hasattr(job, "model_dump") else dict(job)
    return {
        "id": value.get("id"),
        "status": value.get("status"),
        "model": value.get("model"),
        "training_type": value.get("trainingType") or value.get("training_type"),
        "fine_tuned_model": value.get("fine_tuned_model"),
        "trained_tokens": value.get("trained_tokens"),
        "finished_at": value.get("finished_at"),
        "error": value.get("error"),
    }


def _token_scope(base_url: str) -> str:
    if ".services.ai.azure." in base_url:
        return FOUNDRY_TOKEN_SCOPE
    if ".openai.azure." in base_url:
        return COGNITIVE_SERVICES_TOKEN_SCOPE
    raise RuntimeError(f"Unsupported RFT endpoint for Entra authentication: {base_url}")


def _client() -> OpenAI:
    base_url = _required_env("MAI_RFT_BASE_URL").rstrip("/") + "/"
    api_key: str | Any = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    if not api_key:
        api_key = get_bearer_token_provider(
            DefaultAzureCredential(),
            _token_scope(base_url),
        )
    return OpenAI(api_key=api_key, base_url=base_url)


def _write_result(path: Path | None, result: dict[str, Any]) -> None:
    rendered = json.dumps(result, indent=2)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    if args.interval < 1:
        raise SystemExit("Polling interval must be positive")

    client = _client()
    terminal = {"succeeded", "failed", "cancelled"}
    while True:
        result = _job_summary(client.fine_tuning.jobs.retrieve(args.job_id))
        _write_result(args.output, result)
        if not args.watch or result["status"] in terminal:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
