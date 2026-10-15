"""Adapt Foundry completion transport frames to the unchanged local quality grader."""

import inspect
import json


def _hillclimb_response_text(value):
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return value
    if (
        isinstance(decoded, list)
        and decoded
        and all(
            isinstance(block, dict)
            and block.get("type") in ("output_text", "text")
            and isinstance(block.get("text"), str)
            for block in decoded
        )
    ):
        return "".join(block["text"] for block in decoded)
    return value if isinstance(value, str) else json.dumps(value)


def _hillclimb_catalog_entry(sample, item):
    if (
        (sample is not None and not isinstance(sample, dict))
        or "response" not in item
        or "ground_truth" not in item
    ):
        raise ValueError("Code evaluation requires mapped response and ground_truth")
    reference = json.loads(item["ground_truth"])
    normalized = {
        "output_text": _hillclimb_response_text(item["response"]),
        "output_tools": item.get("tool_calls", []),
        "tool_definitions": item.get("tool_definitions", []),
    }
    # The generated source binds this name before replacing the grade entry point.
    return _hillclimb_canonical_grade(normalized, reference)  # noqa: F821


def code_source(canonical_source):
    return (
        canonical_source
        + "\n\nimport json\n\n_hillclimb_canonical_grade = grade\n\n"
        + inspect.getsource(_hillclimb_response_text)
        + "\n\n"
        + inspect.getsource(_hillclimb_catalog_entry).replace(
            "def _hillclimb_catalog_entry(", "def grade(", 1
        )
    )
