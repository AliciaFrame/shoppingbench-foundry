"""Portable fingerprints for text artifacts."""

import hashlib
from pathlib import Path


def normalized_text_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def text_file_sha256(path: Path) -> str:
    return hashlib.sha256(normalized_text_bytes(path)).hexdigest()


def matches_text_file_sha256(path: Path, expected: str | None) -> bool:
    content = normalized_text_bytes(path)
    return expected in {
        hashlib.sha256(content).hexdigest(),
        hashlib.sha256(content.replace(b"\n", b"\r\n")).hexdigest(),
    }
