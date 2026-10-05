"""그룹 분할 (스펙 12.1).

분할 단위는 문서 계열이다. 같은 문서의 문단, 겹치는 segment, 개정본, 거의 같은 템플릿은
한 계열로 묶여 같은 partition에 들어간다. 한 번 배정된 계열은 다시 배정하지 않는다.
"""
from __future__ import annotations

import hashlib

from ..runtime import utc_now
from ..storage import Database

PARTITIONS = ("train", "validation", "test")


def assign_partitions(database: Database, groups: dict[str, dict[str, int]], ratios: list[float],
                      seed: int, split_tag: str) -> dict[str, str]:
    """새 그룹을 목표 비율에 가장 모자란 partition에 넣는다. 기존 배정은 바꾸지 않는다.

    groups: {family_id: {"n": 라벨 수, "pos": YES 수}}
    """
    existing = {row["group_id"]: row["partition"] for row in database.query("SELECT group_id, partition FROM split_assignments")}
    totals = {name: {"n": 0, "pos": 0} for name in PARTITIONS}
    assignment: dict[str, str] = {}
    for group, stats in groups.items():
        if group in existing:
            assignment[group] = existing[group]
            totals[existing[group]]["n"] += stats["n"]
            totals[existing[group]]["pos"] += stats["pos"]

    def order_key(group: str) -> tuple[int, int, str]:
        digest = hashlib.sha256(f"{seed}:{group}".encode("utf-8")).hexdigest()
        return (-groups[group]["pos"], -groups[group]["n"], digest)

    target = dict(zip(PARTITIONS, ratios))
    new_assignments: list[tuple[str, str]] = []
    for group in sorted((g for g in groups if g not in existing), key=order_key):
        stats = groups[group]
        key = "pos" if stats["pos"] > 0 else "n"
        grand = sum(totals[name][key] for name in PARTITIONS) + stats[key]
        # 목표 대비 부족분이 가장 큰 partition. 동률이면 train → validation → test 순.
        choice = max(PARTITIONS, key=lambda name: (target[name] * grand - totals[name][key], -PARTITIONS.index(name)))
        assignment[group] = choice
        totals[choice]["n"] += stats["n"]
        totals[choice]["pos"] += stats["pos"]
        new_assignments.append((group, choice))
    if new_assignments:
        now = utc_now()
        with database.transaction() as connection:
            connection.executemany(
                "INSERT INTO split_assignments (group_id, partition, assigned_at, split_tag) VALUES (?, ?, ?, ?)",
                [(group, partition, now, split_tag) for group, partition in new_assignments],
            )
    return assignment
