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
) -> dict:
    suffix = "" if round_number == 1 else "-round2"
    seed = "baseline" if round_number == 1 else "round2-start"
    return {
        "name": f"shoppingbench-{task}-optimization{suffix}",
        "agent": {
            "name": AGENTS[task],
            "kind": "hosted",
            "version": agent_version,
            "config": str(repo_root / "optimization" / task / seed / "metadata.yaml"),
        },
        "dataset": {
            "local_uri": str(
                repo_root / "evaluations" / "datasets" / f"{task}-optimize{suffix}.jsonl"
            )
        },
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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    work_dir = repo_root / "optimization" / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    config_path = work_dir / f"{args.task}-round{args.round}.json"
    config_path.write_text(
        json.dumps(
            build_config(
                repo_root,
                args.task,
                args.round,
                args.agent_version,
                args.eval_model,
                args.optimization_model,
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
