"""Alternative grader: copy to grader.py for semantic tasks; use endpoint RFT."""

import json
import math
import os
from pathlib import Path

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI

REQUIRES_NETWORK = True


def grade_with_usage(sample, item):
    endpoint = os.environ["MAI_JUDGE_BASE_URL"]
    model = os.environ["MAI_JUDGE_DEPLOYMENT_NAME"]
    scope = (
        "https://cognitiveservices.azure.com/.default"
        if ".openai.azure.com" in endpoint
        else "https://ai.azure.com/.default"
    )
    with DefaultAzureCredential(process_timeout=60) as credential:
        key = os.getenv("MAI_JUDGE_API_KEY") or get_bearer_token_provider(credential, scope)
        with OpenAI(
            base_url=endpoint.rstrip("/") + "/", api_key=key, max_retries=0, timeout=60
        ) as api:
            response = api.responses.create(
                model=model,
                max_output_tokens=512,
                instructions=(
                    Path(__file__).with_name("judge-rubric.md").read_text(encoding="utf-8")
                    + '\nReturn only JSON: {"score": <number in [0,1]>, "reason": "<reason>"}'
                ),
                input=json.dumps({"reference": item, "candidate": sample}),
            )
    incomplete = getattr(response, "incomplete_details", None)
    call = {
        "model": model,
        "returned_model": getattr(response, "model", None),
        "response_status": getattr(response, "status", None),
        "incomplete_details": incomplete.model_dump() if incomplete else None,
        "purpose": "evaluation",
        "pricing_key": os.environ["MAI_JUDGE_PRICING_KEY"],
        "raw_usage": response.usage.model_dump() if response.usage else None,
    }
    try:
        if getattr(response, "status", None) not in (None, "completed") or incomplete:
            raise ValueError("Judge generation did not complete")
        result = json.loads(response.output_text)
        value = result["score"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Judge score must be numeric")
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Judge score must be in [0,1]")
    except (ValueError, KeyError, TypeError) as error:
        error.calls = [call]
        raise
    return {
        "score": value,
        "calls": [call],
    }


def grade(sample, item):
    return grade_with_usage(sample, item)["score"]
