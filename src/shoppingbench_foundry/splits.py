from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from typing import Any

from .trajectory import normalized


class DisjointSet:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        if self._parent[item] != item:
            self._parent[item] = self.find(self._parent[item])
        return self._parent[item]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def leakage_keys(task: str, row: dict[str, Any]) -> list[str]:
    reward = row["reward"]
    rewards = reward if isinstance(reward, list) else [reward]
    keys = [f"product:{entry['product_id']}" for entry in rewards]
    if task == "web":
        keys.append(f"knowledge:{normalized(row['knowledge_attribute'])}")
    return sorted(set(keys))


def grouped_row_indexes(task: str, rows: list[dict[str, Any]]) -> list[list[int]]:
    disjoint = DisjointSet(len(rows))
    owners: dict[str, int] = {}
    for index, row in enumerate(rows):
        for key in leakage_keys(task, row):
            if key in owners:
                disjoint.union(index, owners[key])
            else:
                owners[key] = index
    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rows)):
        groups[disjoint.find(index)].append(index)
    return list(groups.values())


def _group_id(task: str, rows: list[dict[str, Any]], indexes: list[int]) -> str:
    keys = sorted(
        {
            key
            for index in indexes
            for key in leakage_keys(task, rows[index])
        }
    )
    digest = hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]
    return f"{task}-{digest}"


def group_disjoint_splits(
    task: str,
    rows: list[dict[str, Any]],
    development_count: int = 30,
    final_test_count: int = 50,
) -> tuple[dict[str, list[int]], dict[str, Any]]:
    groups = grouped_row_indexes(task, rows)
    random.Random(f"shoppingbench-{task}-v2-groups").shuffle(groups)
    splits: dict[str, list[int]] = {
        "final-test": [],
        "development": [],
        "training-pool": [],
    }
    for split, target in (
        ("final-test", final_test_count),
        ("development", development_count),
    ):
        while groups and len(splits[split]) < target:
            splits[split].extend(groups.pop())
    splits["training-pool"] = [index for group in groups for index in group]
    for indexes in splits.values():
        indexes.sort()

    case_groups: dict[str, str] = {}
    case_keys: dict[str, list[str]] = {}
    for indexes in grouped_row_indexes(task, rows):
        group_id = _group_id(task, rows, indexes)
        for index in indexes:
            name = f"{task}-{index:03d}"
            case_groups[name] = group_id
            case_keys[name] = leakage_keys(task, rows[index])

    key_sets = {
        split: {
            key
            for index in indexes
            for key in leakage_keys(task, rows[index])
        }
        for split, indexes in splits.items()
    }
    overlap = {
        f"{left}|{right}": sorted(key_sets[left] & key_sets[right])
        for left, right in (
            ("training-pool", "development"),
            ("training-pool", "final-test"),
            ("development", "final-test"),
        )
    }
    if any(overlap.values()):
        raise RuntimeError(f"Leakage keys crossed v2 splits for {task}: {overlap}")

    manifest = {
        "counts": {split: len(indexes) for split, indexes in splits.items()},
        "cases": {
            split: [f"{task}-{index:03d}" for index in indexes]
            for split, indexes in splits.items()
        },
        "case_groups": case_groups,
        "case_leakage_keys": case_keys,
        "overlap": overlap,
    }
    return splits, manifest
