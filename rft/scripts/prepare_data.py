from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from shoppingbench_foundry.datasets import TASK_FILES, load_task_rows
from shoppingbench_foundry.tool_definitions import chat_completions_tools

VALIDATION_COUNTS = {"product": 20, "shop": 20, "voucher": 20, "web": 20}
CALIBRATION_ROLLOUTS = 5
OUTPUT_SUFFIXES = (
    "train",
    "validation",
    "validation-eval",
    "calibration-eval",
)


def _case_name(task: str, index: int) -> str:
    return f"{task}-{index:03d}"


def _holdout_names(evals_dir: Path, task: str) -> set[str]:
    path = evals_dir / f"{task}-holdout.jsonl"
    with path.open(encoding="utf-8") as handle:
        return {json.loads(line)["name"] for line in handle if line.strip()}


def _compose_developer_message(config_root: Path, task: str) -> str:
    config_dir = config_root / task / "config"
    sections = [(config_dir / "instructions.md").read_text(encoding="utf-8").strip()]
    skills_dir = config_dir / "skills"
    if skills_dir.exists():
        for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
            sections.append(skill_path.read_text(encoding="utf-8").strip())
    return "\n\n".join(section for section in sections if section)


def _rft_item(
    task: str,
    case_name: str,
    row: dict[str, Any],
    developer_message: str,
) -> dict[str, Any]:
    reward = row["reward"]
    reward_count = len(reward) if isinstance(reward, list) else 1
    item = {
        "messages": [
            {"role": "developer", "content": developer_message},
            {"role": "user", "content": row["query"]},
        ],
        "tools": chat_completions_tools(),
        "case_name": case_name,
        "query": row["query"],
        "task": task,
        "reward": reward,
        "max_search_calls": max(3, 2 * reward_count + 2),
    }
    if task == "voucher":
        item["voucher"] = row["voucher"]
    if task == "web":
        item["knowledge_attribute"] = row["knowledge_attribute"]
    return item


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _portable_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _generated_files(output_dir: Path, tasks: list[str]) -> list[Path]:
    paths = [
        output_dir / f"{task}-{suffix}.jsonl"
        for task in tasks
        for suffix in OUTPUT_SUFFIXES
    ]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Expected generated files are missing: {missing}")
    return sorted(paths)


def prepare_task(
    data_dir: Path,
    evals_dir: Path,
    config_root: Path,
    output_dir: Path,
    task: str,
    split_manifest: Path | None = None,
) -> dict[str, Any]:
    public_rows = load_task_rows(data_dir, task)
    rows_by_name = {
        _case_name(task, index): row for index, row in enumerate(public_rows)
    }
    case_groups: dict[str, str] = {}
    if split_manifest is not None:
        manifest = json.loads(split_manifest.read_text(encoding="utf-8"))
        task_manifest = manifest["tasks"][task]
        available = [
            (name, rows_by_name[name])
            for name in task_manifest["cases"]["training-pool"]
        ]
        case_groups = task_manifest["case_groups"]
        grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for entry in available:
            grouped.setdefault(case_groups[entry[0]], []).append(entry)
        groups = list(grouped.values())
        random.Random(f"shoppingbench-rft-{task}-v2").shuffle(groups)
        validation: list[tuple[str, dict[str, Any]]] = []
        while groups and len(validation) < VALIDATION_COUNTS[task]:
            validation.extend(groups.pop())
        training = [entry for group in groups for entry in group]
        holdout_names = set(task_manifest["cases"]["final-test"])
    else:
        holdout_names = _holdout_names(evals_dir, task)
        available = [
            (_case_name(task, index), row)
            for index, row in enumerate(public_rows)
            if _case_name(task, index) not in holdout_names
        ]
        random.Random(f"shoppingbench-rft-{task}-v1").shuffle(available)
        validation_count = VALIDATION_COUNTS[task]
        validation = available[:validation_count]
        training = available[validation_count:]
    developer_message = _compose_developer_message(config_root, task)

    train_rows = [_rft_item(task, name, row, developer_message) for name, row in training]
    validation_rows = [_rft_item(task, name, row, developer_message) for name, row in validation]
    validation_eval_rows = [
        {
            "name": name,
            "query": row["query"],
            "item": _rft_item(task, name, row, developer_message),
        }
        for name, row in validation
    ]
    calibration_eval_rows = [
        {
            "name": f"{name}-rollout-{rollout}",
            "query": row["query"],
            "item": _rft_item(task, name, row, developer_message),
        }
        for name, row in validation
        for rollout in range(1, CALIBRATION_ROLLOUTS + 1)
    ]
    _write_jsonl(output_dir / f"{task}-train.jsonl", train_rows)
    _write_jsonl(output_dir / f"{task}-validation.jsonl", validation_rows)
    _write_jsonl(output_dir / f"{task}-validation-eval.jsonl", validation_eval_rows)
    _write_jsonl(output_dir / f"{task}-calibration-eval.jsonl", calibration_eval_rows)

    return {
        "task": task,
        "train": len(train_rows),
        "validation": len(validation_rows),
        "calibration_rollouts": len(calibration_eval_rows),
        "holdout_excluded": len(holdout_names),
        "split_manifest": split_manifest.name if split_manifest else None,
        "validation_groups": len({case_groups.get(name, name) for name, _ in validation}),
        "training_groups": len({case_groups.get(name, name) for name, _ in training}),
        "developer_message_sha256": hashlib.sha256(developer_message.encode()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--task", choices=TASK_FILES, action="append")
    parser.add_argument("--data-dir", type=Path, default=repo_root / "data" / "source")
    parser.add_argument(
        "--evals-dir",
        type=Path,
        default=repo_root / "evaluations" / "datasets",
    )
    parser.add_argument(
        "--config-root",
        type=Path,
        default=repo_root / "agents",
    )
    parser.add_argument("--output-dir", type=Path, default=repo_root / "rft" / "data")
    parser.add_argument(
        "--split-manifest",
        type=Path,
        default=repo_root / "evaluations" / "datasets" / "v2" / "split-manifest.json",
    )
    args = parser.parse_args()

    tasks = args.task or list(TASK_FILES)
    summaries = [
        prepare_task(
            args.data_dir,
            args.evals_dir,
            args.config_root,
            args.output_dir,
            task,
            args.split_manifest if args.split_manifest.is_file() else None,
        )
        for task in tasks
    ]
    generated_files = _generated_files(args.output_dir, tasks)
    manifest = {
        "version": "v2" if args.split_manifest.is_file() else "v1",
        "split_manifest": (
            {
                "path": _portable_path(args.split_manifest, repo_root),
                "sha256": hashlib.sha256(args.split_manifest.read_bytes()).hexdigest(),
            }
            if args.split_manifest.is_file()
            else None
        ),
        "tasks": summaries,
        "files": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in generated_files
        },
    }
    manifest_path = args.output_dir / "preparation-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"manifest": _portable_path(manifest_path, repo_root), **manifest},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
