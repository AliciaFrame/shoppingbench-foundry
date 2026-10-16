"""Replace this example rubric with the project's quality and safety definition."""

import json
import math


def grade(sample, item):
    trace = []
    for raw in sample.get("output_tools", []):
        function = raw.get("function", raw)
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return 0.0
        trace.append(
            {"name": function.get("name"), "arguments": arguments, "output": raw.get("output")}
        )
    names = [call["name"] for call in trace]
    if any(name in names for name in item.get("forbidden_tools", [])):
        return 0.0
    required = item.get("required_tool_calls", [])
    cursor = 0
    for call in trace:
        if cursor < len(required) and all(
            call.get(key) == value for key, value in required[cursor].items()
        ):
            cursor += 1
    if cursor != len(required):
        return 0.0
    expected = item["expected"]
    weights = item.get("weights", {key: 1 for key in expected})
    if not expected or set(weights) != set(expected):
        raise ValueError("Expected fields and positive weights must match")
    if any(not math.isfinite(w) or w <= 0 for w in weights.values()):
        raise ValueError("Weights must be finite and positive")
    try:
        actual = json.loads(sample["output_text"])
    except (json.JSONDecodeError, TypeError):
        return 0.0
    if not isinstance(actual, dict):
        return 0.0
    if any(actual.get(key) != value for key, value in item.get("invariants", {}).items()):
        return 0.0
    earned = sum(weights[key] for key in expected if actual.get(key) == expected[key])
    return earned / sum(weights.values())
