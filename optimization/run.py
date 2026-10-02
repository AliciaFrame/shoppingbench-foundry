from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

AGENTS = {
    "product": "shoppingbench-product-agent",
    "shop": "shoppingbench-shop-agent",
    "voucher": "shoppingbench-voucher-agent",
    "web": "shoppingbench-web-agent",
}


def build_config(
    repo_root: Path,
    task: str,
    round_number: int,
    agent_version: str,
    eval_model: str,
    optimization_model: str,
    seed_dir: Path | None = None,
    dataset_path: Path | None = None,
    name_suffix: str = "",
) -> dict:
    suffix = "" if round_number == 1 else "-round2"
    seed = "baseline" if round_number == 1 else "round2-start"
    resolved_seed_dir = seed_dir or repo_root / "optimization" / task / seed
    resolved_dataset_path = dataset_path or (
        repo_root / "evaluations" / "datasets" / f"{task}-optimize{suffix}.jsonl"
    )
    metadata_path = resolved_seed_dir / "metadata.yaml"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Optimizer metadata not found: {metadata_path}")
    if not resolved_dataset_path.is_file():
        raise FileNotFoundError(f"Optimizer dataset not found: {resolved_dataset_path}")
    model_lines = [
        line.split(":", 1)[1].strip()
        for line in metadata_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("model:")
    ]
    if len(model_lines) != 1 or not model_lines[0]:
        raise ValueError(f"Optimizer metadata must define exactly one model: {metadata_path}")
    model = model_lines[0]
    experiment_suffix = f"-{name_suffix}" if name_suffix else ""
    return {
        "name": f"shoppingbench-{task}-optimization{suffix}{experiment_suffix}",
        "agent": {
            "name": AGENTS[task],
            "kind": "hosted",
            "version": agent_version,
            "model": model,
            "config": str(metadata_path),
        },
        "dataset": {"local_uri": str(resolved_dataset_path)},
        "evaluators": ["builtin.task_adherence"],
        "options": {
            "eval_model": eval_model,
            "optimization_model": optimization_model,
            "max_candidates": 4,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=AGENTS)
    parser.add_argument("--round", type=int, choices=(1, 2), default=1)
    parser.add_argument("--agent-version", required=True)
    parser.add_argument("--eval-model", required=True)
    parser.add_argument("--optimization-model", required=True)
    parser.add_argument("--seed-dir", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--name-suffix", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    seed_dir = args.seed_dir
    if seed_dir is not None and not seed_dir.is_absolute():
        seed_dir = repo_root / seed_dir
    dataset_path = args.dataset
    if dataset_path is not None and not dataset_path.is_absolute():
        dataset_path = repo_root / dataset_path
    work_dir = repo_root / "optimization" / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    experiment_suffix = f"-{args.name_suffix}" if args.name_suffix else ""
    config_path = work_dir / f"{args.task}-round{args.round}{experiment_suffix}.json"
    config_path.write_text(
        json.dumps(
            build_config(
                repo_root,
                args.task,
                args.round,
                args.agent_version,
                args.eval_model,
                args.optimization_model,
                seed_dir,
                dataset_path,
                args.name_suffix,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    command = [
        "azd",
        "ai",
        "agent",
        "optimize",
        "--agent",
        AGENTS[args.task],
        "--config",
        str(config_path),
    ]
    if args.dry_run:
        print(json.dumps({"command": command, "config": str(config_path)}, indent=2))
        return
    subprocess.run(command, cwd=repo_root / "agents", check=True)


if __name__ == "__main__":
    main()
