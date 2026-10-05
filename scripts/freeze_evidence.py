from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

EVIDENCE_PREFIXES = (
    "README.md",
    "docs/results.md",
    "optimization/results/",
    "rft/README.md",
    "rft/data/",
    "rft/results/",
    "src/shoppingbench_foundry/benchmark_subset.py",
    "src/shoppingbench_foundry/grading.py",
    "src/shoppingbench_foundry/rft_grading.py",
    "src/shoppingbench_foundry/rft_grading_v2.py",
    "evaluations/datasets/",
    "evaluations/scripts/evaluate_agent.py",
)


def _git(repo: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", *arguments], cwd=repo)


def freeze(repo: Path, commit: str) -> dict[str, object]:
    resolved = _git(repo, "rev-parse", commit).decode().strip()
    paths = _git(repo, "ls-tree", "-r", "--name-only", resolved).decode().splitlines()
    evidence_paths = sorted(
        path
        for path in paths
        if any(path == prefix or path.startswith(prefix) for prefix in EVIDENCE_PREFIXES)
    )
    files = []
    for path in evidence_paths:
        content = _git(repo, "show", f"{resolved}:{path}")
        files.append(
            {
                "path": path,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return {
        "version": "shoppingbench-v1",
        "source_commit": resolved,
        "file_count": len(files),
        "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", default="2922cdc")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/audits/v1-evidence-manifest.json"),
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    result = freeze(repo, args.commit)
    output = args.output if args.output.is_absolute() else repo / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "files": result["file_count"]}))


if __name__ == "__main__":
    main()
