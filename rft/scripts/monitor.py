from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    client = OpenAI(
        api_key=_required_env("AZURE_OPENAI_API_KEY"),
        base_url=_required_env("MAI_RFT_BASE_URL").rstrip("/") + "/",
    )
    result = _job_summary(client.fine_tuning.jobs.retrieve(args.job_id))
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
