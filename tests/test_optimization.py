from pathlib import Path

from optimization.run import build_config


def test_build_config_supports_isolated_post_rft_seed(tmp_path: Path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "metadata.yaml").write_text(
        "model: mai-sb-rft3-step10\ninstruction_file: instructions.md\n",
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
        name_suffix="post-rft-step10",
    )

    assert config["name"] == "shoppingbench-web-optimization-round2-post-rft-step10"
    assert config["agent"] == {
        "name": "shoppingbench-web-agent",
        "kind": "hosted",
        "version": "3",
        "model": "mai-sb-rft3-step10",
        "config": str(seed / "metadata.yaml"),
    }
    assert config["dataset"]["local_uri"] == str(dataset)
