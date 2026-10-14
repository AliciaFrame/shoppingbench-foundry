from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from rft.scripts.submit import _validate_agentic_dataset
from shoppingbench_foundry.artifacts import (
    matches_text_file_sha256,
)
from shoppingbench_foundry.artifacts import (
    text_file_sha256 as sha256,
)
from shoppingbench_foundry.web_rft_grading_v5 import _accepted_clues, _contains_accepted_clue


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def capture_sha256(capture: dict[str, Any]) -> str:
    payload = json.dumps(capture, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def selection_input(query: str, evidence: str, sources: list[dict[str, str]]) -> str:
    source_text = "\n".join(
        f"- {source['title'] or 'Source'}: {source['url']}" for source in sources
    )
    return (
        f"Original request:\n{query}\n\n"
        f"Grounded web evidence:\n{evidence}\n\n"
        f"Sources:\n{source_text or '- No source URL returned'}\n\n"
        "Use the catalog tools to select the product. Treat the web evidence as the "
        "authoritative clue resolution."
    )


def prepare(
    evidence_path: Path,
    data_dir: Path,
    config_dir: Path,
    output_dir: Path,
    review_path: Path | None = None,
) -> dict[str, Any]:
    captures = read_jsonl(evidence_path)
    source_splits = {
        split: read_jsonl(data_dir / f"web-{split}.jsonl") for split in ("train", "validation")
    }
    source_rows = [row for rows in source_splits.values() for row in rows]
    source_names = {row["case_name"] for row in source_rows}
    if len(source_names) != len(source_rows):
        raise ValueError("Source data contains duplicate case names")
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path else None
    if review is not None:
        if review.get("status") != "approved-for-data-preparation":
            raise ValueError("Semantic review is not approved for data preparation")
        if set(review["cases"]) != source_names:
            raise ValueError("Review must explicitly cover every source case")
        for split in source_splits:
            filename = f"web-{split}.jsonl"
            if not matches_text_file_sha256(
                data_dir / filename, review["source_sha256"].get(filename),
            ):
                raise ValueError(f"Review does not match source data: {filename}")
        excluded_groups = {
            row["case_group"] for row in source_rows
            if review["cases"][row["case_name"]]["decision"] == "exclude"
        }
        for row in source_rows:
            decision = review["cases"][row["case_name"]]
            if decision["decision"] not in {"include", "exclude"} or not decision.get("reason"):
                raise ValueError(f"Missing explicit review decision for {row['case_name']}")
            if row["case_group"] in excluded_groups and decision["decision"] != "exclude":
                raise ValueError("Quarantine must exclude the entire connected case group")

    by_case: dict[str, list[dict[str, Any]]] = {}
    for capture in captures:
        name = capture["case_name"]
        if name in by_case and review is None:
            raise ValueError(f"Multiple evidence snapshots for {name}; select one explicitly")
        by_case.setdefault(name, []).append(capture)
    if set(by_case) - source_names:
        raise ValueError("Evidence contains cases outside the source training pool")
    instructions_path = config_dir / "instructions.md"
    tools_path = config_dir / "tools.json"
    instructions = instructions_path.read_text(encoding="utf-8").strip()
    tools = json.loads(tools_path.read_text(encoding="utf-8"))
    prepared: dict[str, list[dict[str, Any]]] = {}
    for split, split_rows in source_splits.items():
        output_rows = []
        for row in split_rows:
            name = row["case_name"]
            row = dict(row)
            candidates = by_case.get(name, [])
            if review is not None:
                decision = review["cases"][name]
                if decision["decision"] == "exclude":
                    continue
                aliases = decision.get("knowledge_aliases", [])
                if not isinstance(aliases, list) or any(
                    not isinstance(alias, str) or not alias.strip() for alias in aliases
                ):
                    raise ValueError(f"Invalid reviewed aliases for {name}")
                row["knowledge_aliases"] = list(dict.fromkeys([
                    *row.get("knowledge_aliases", []), *aliases,
                ]))
                row["case_group"] = decision.get("case_group", row["case_group"])
                candidates = [
                    capture for capture in candidates
                    if capture_sha256(capture) == decision.get("capture_sha256")
                ]
            if len(candidates) != 1:
                raise ValueError(f"Review must select exactly one unchanged capture for {name}")
            capture = candidates[0]
            if not capture or capture.get("error") or capture["query"] != row["query"]:
                raise ValueError(f"Missing or failed matching live web capture for {name}")
            web_responses = [
                response for response in capture["responses"] if response["stage"] == "web"
            ]
            evidence = capture["sample"]["web_evidence"]
            sources = capture["sample"]["web_sources"]
            if (
                not web_responses
                or web_responses[-1].get("status") != "completed"
                or not web_responses[-1].get("native_search_calls")
                or not evidence.strip()
                or not sources
                or web_responses[-1]["input"] != row["query"]
                or web_responses[-1]["evidence"].strip() != evidence.strip()
            ):
                raise ValueError(f"Unverified native web evidence for {name}")
            if review is not None and not _contains_accepted_clue(evidence, _accepted_clues(row)):
                raise ValueError(f"Reviewed evidence contradicts or omits the clue for {name}")
            item = dict(row)
            item.pop("max_search_calls", None)
            item["messages"] = [
                {"role": "developer", "content": instructions},
                {
                    "role": "user",
                    "content": selection_input(row["query"], evidence, sources),
                },
            ]
            item["tools"] = tools
            item["parallel_tool_calls"] = False
            item["live_web_context"] = {
                "evidence": evidence,
                "sources": sources,
                "captured_at": capture["started_at"],
                "capture_name": capture["name"],
                "model": web_responses[-1]["model"],
                "native_search_observed": True,
            }
            output_rows.append(item)
        prepared[split] = output_rows
    if review is not None:
        rows = [row for values in prepared.values() for row in values]
        validation_groups = set(review["validation_groups"])
        if not validation_groups <= {row["case_group"] for row in rows}:
            raise ValueError("Reviewed validation groups include excluded or unknown groups")
        prepared = {
            "train": [row for row in rows if row["case_group"] not in validation_groups],
            "validation": [row for row in rows if row["case_group"] in validation_groups],
        }
    if not all(prepared.values()):
        raise ValueError("Training and validation must both retain examples")
    train_groups = {row["case_group"] for row in prepared["train"]}
    validation_groups = {row["case_group"] for row in prepared["validation"]}
    if train_groups & validation_groups:
        raise ValueError("Training and validation contain overlapping case groups")
    for field in ("product_id", "knowledge_attribute"):
        values = {}
        for split, rows in prepared.items():
            values[split] = (
                {str(row["reward"][field]) for row in rows}
                if field == "product_id"
                else {clue for row in rows for clue in _accepted_clues(row)}
            )
        if values["train"] & values["validation"]:
            raise ValueError(f"Training and validation contain overlapping {field} values")
    used_cases = {row["case_name"] for rows in prepared.values() for row in rows}
    if review is None and used_cases != by_case.keys():
        raise ValueError("Evidence captures do not exactly cover the training pool")

    output_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for split, rows in prepared.items():
        path = output_dir / f"web-{split}.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
            newline="\n",
        )
        _validate_agentic_dataset(path, "web", expected_tools=tools)
        files[path.name] = {"sha256": sha256(path), "rows": len(rows)}
    manifest = {
        "hash_line_endings": "LF",
        "status": "prepared-not-approved-for-submission",
        "architecture": "fixed native live-web resolver followed by optimized catalog agent",
        "training_target": "catalog continuation; live web resolver weights are unchanged",
        "evidence_sha256": sha256(evidence_path),
        "instructions_sha256": sha256(instructions_path),
        "tools_sha256": sha256(tools_path),
        "training_groups": len(train_groups),
        "validation_groups": len(validation_groups),
        "files": files,
        "remaining_gates": [
            *([] if review is not None else [
                "Review evidence-label agreement and dataset semantics",
            ]),
            "Calibrate finalized inputs using the training-compatible tool loop",
            "Verify hosted grader parity and tool availability with the training service",
        ],
    }
    if review_path is not None:
        manifest["semantic_review"] = {
            "sha256": sha256(review_path),
            "included_cases": sorted(used_cases),
            "excluded_cases": {
                name: decision["reason"] for name, decision in review["cases"].items()
                if decision["decision"] == "exclude"
            },
            "note": "Curated training inputs, not an unbiased end-to-end evaluation cohort",
        }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
        newline="\n",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("rft") / "data")
    parser.add_argument(
        "--config-dir", type=Path, default=Path("agents") / "web_search" / "optimized",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(
        args.evidence, args.data_dir, args.config_dir, args.output_dir, args.review,
    ), indent=2))


if __name__ == "__main__":
    main()
