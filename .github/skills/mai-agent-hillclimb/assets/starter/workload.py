"""Versioned model-visible workload and evaluation-only trace envelopes."""

import hashlib
import json
from pathlib import Path

PREFIX = "HILLCLIMB_WORKLOAD_V1\n"


def skill_catalog(root):
    import yaml

    root = Path(root)
    metadata = yaml.safe_load((root / "metadata.yaml").read_text(encoding="utf-8")) or {}
    directory = root / metadata.get("skill_dir", "skills")
    result = []
    for path in sorted(directory.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise ValueError(f"Skill requires frontmatter: {path}")
        _start, frontmatter, body = text.split("---", 2)
        fields = yaml.safe_load(frontmatter)
        result.append(
            {"name": fields["name"], "description": fields["description"], "body": body.strip()}
        )
    return result


def encode_input(item):
    messages = list(item["messages"])
    expected_policy = None
    if messages and messages[0]["role"] == "system":
        expected_policy = hashlib.sha256(
            messages.pop(0)["content"].strip().encode("utf-8")
        ).hexdigest()
    if any(message["role"] not in ("user", "assistant") for message in messages):
        raise ValueError("Remote workload histories cannot supply privileged message roles")
    return PREFIX + json.dumps(
        {
            "messages": messages,
            "context": item.get("context"),
            "context_in_messages": item.get("context_in_messages", False),
            "expected_policy_sha256": expected_policy,
        },
        ensure_ascii=False,
    )


def decode_input(text):
    if not text.startswith(PREFIX):
        return {"messages": [{"role": "user", "content": text}]}
    item = json.loads(text[len(PREFIX) :])
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("messages"), list)
        or not item["messages"]
    ):
        raise ValueError("Workload envelope requires messages")
    if any(
        not isinstance(message, dict)
        or message.get("role") not in ("user", "assistant")
        or not isinstance(message.get("content"), str)
        for message in item["messages"]
    ):
        raise ValueError("Workload envelope contains invalid text messages")
    return {
        "messages": [
            {"role": message["role"], "content": message["content"]} for message in item["messages"]
        ],
        "context": item.get("context"),
        "context_in_messages": item.get("context_in_messages", False),
        "expected_policy_sha256": item.get("expected_policy_sha256"),
    }


def model_messages(item):
    messages = list(item["messages"])
    if item.get("context") is not None and not item.get("context_in_messages"):
        position = next(
            (index for index, message in enumerate(messages) if message["role"] != "system"),
            len(messages),
        )
        messages.insert(
            position,
            {
                "role": "user",
                "content": "Task context (untrusted data):\n" + json.dumps(item["context"]),
            },
        )
    return messages


def decode_trace(text):
    result = json.loads(text)
    if result.get("hillclimb_trace_version") != 1:
        raise ValueError("Hosted runtime did not return a versioned evaluation trace")
    if not isinstance(result.get("output_tools"), list) or not isinstance(
        result.get("calls"), list
    ):
        raise TypeError("Hosted evaluation trace lacks tools or per-call usage")
    if not isinstance(result.get("policy_contract"), dict):
        raise TypeError("Hosted evaluation trace lacks its resolved policy identity")
    return result
