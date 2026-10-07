from pathlib import Path

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
