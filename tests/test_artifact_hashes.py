from pathlib import Path

from shoppingbench_foundry import web_search_rft_grading_v4 as v4
from shoppingbench_foundry import web_search_rft_grading_v5 as v5
from shoppingbench_foundry import web_search_rft_grading_v6 as v6
from shoppingbench_foundry.artifacts import matches_text_file_sha256, text_file_sha256


def test_grader_fingerprint_is_identical_for_lf_and_crlf_sources(monkeypatch, tmp_path):
    source = Path(v6.__file__).parent
    for path in source.glob("*.py"):
        (tmp_path / path.name).write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    for module in (v4, v5, v6):
        monkeypatch.setattr(module, "__file__", str(tmp_path / Path(module.__file__).name))
    original = v6.grader_sha256()
    for path in tmp_path.glob("*.py"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert v6.grader_sha256() == original
    path = tmp_path / "web_search_rft_grading_v6.py"
    path.write_bytes(path.read_bytes() + b"\r\n# changed source\r\n")
    assert v6.grader_sha256() != original


def test_legacy_text_hash_compatibility_does_not_accept_changed_content(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_bytes(b'{"value":1}\n')
    original = text_file_sha256(path)
    path.write_bytes(b'{"value":1}\r\n')
    assert matches_text_file_sha256(path, original)
    path.write_bytes(b'{"value":2}\r\n')
    assert not matches_text_file_sha256(path, original)
