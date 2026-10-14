import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from optimize.run import build_config


def test_build_config_supports_explicit_seed(tmp_path: Path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "metadata.yaml").write_text(
        "model: fine-tuned-checkpoint-step10\ninstruction_file: instructions.md\n",
        encoding="utf-8",
    )
    dataset = tmp_path / "web-optimize.jsonl"
    dataset.write_text('{"name":"web-001"}\n', encoding="utf-8")

    config = build_config(
        tmp_path,
        "web",
        2,
        "3",
        "eval-model",
        "optimization-model",
        seed_dir=seed,
        dataset_path=dataset,
        name_suffix="candidate",
    )

    assert config["name"] == "shoppingbench-web-optimization-round2-candidate"
    assert config["agent"] == {
        "name": "shoppingbench-web-agent",
        "kind": "hosted",
        "version": "3",
        "model": "fine-tuned-checkpoint-step10",
        "config": str(seed / "metadata.yaml"),
    }
    assert config["dataset"]["local_uri"] == str(dataset)


def test_build_config_maps_web_search_to_web_dataset(tmp_path: Path):
    seed = tmp_path / "agents" / "web_search" / "baseline"
    seed.mkdir(parents=True)
    (seed / "metadata.yaml").write_text(
        "model: shoppingbench-eval-gpt-5-4-mini\n",
        encoding="utf-8",
    )
    dataset = tmp_path / "evals" / "datasets" / "optimization" / "web-round1.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text('{"name":"web-001"}\n', encoding="utf-8")

    config = build_config(
        tmp_path,
        "web-search",
        1,
        "1",
        "eval-model",
        "optimization-model",
    )

    assert config["agent"]["name"] == "shoppingbench-web-search-agent"
    assert config["agent"]["config"] == str(seed / "metadata.yaml")
    assert config["dataset"]["local_uri"] == str(dataset)


def test_prepackage_refreshes_changed_and_removed_files_without_nesting(tmp_path):
    manifest = (Path(__file__).parents[1] / "agents" / "azure.yaml").read_text(encoding="utf-8")
    if os.name == "nt":
        executable = shutil.which("pwsh")
        if executable is None:
            pytest.skip("PowerShell 7 is required for the Windows azd packaging hook")
        hook = manifest.split("run: |", 1)[1].split("        posix:", 1)[0]
        command = [executable, "-NoProfile", "-NonInteractive", "-Command", textwrap.dedent(hook)]
    else:
        hook = manifest.split("posix:", 1)[1].split("run:", 1)[1].split("services:", 1)[0].strip()
        command = ["sh", "-ec", hook]
    agents = tmp_path / "agents"
    source = tmp_path / "src" / "shoppingbench_foundry"
    source.mkdir(parents=True)
    agents.mkdir()
    (source / "version.py").write_text("old", encoding="utf-8")
    (source / "removed.py").write_text("obsolete", encoding="utf-8")
    subprocess.run(command, cwd=agents, check=True, capture_output=True)
    (source / "version.py").write_text("new", encoding="utf-8")
    (source / "removed.py").unlink()
    subprocess.run(command, cwd=agents, check=True, capture_output=True)
    vendored = agents / "shoppingbench_foundry"
    assert (vendored / "version.py").read_text() == "new"
    assert not (vendored / "removed.py").exists()
    assert not (vendored / "shoppingbench_foundry").exists()
