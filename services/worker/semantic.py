from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any

from database import Database


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    common = left.keys() & right.keys()
    dot = sum(left[tag] * right[tag] for tag in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def assign_semantic_groups(database: Database, project_id: str, threshold: float = 0.86) -> int:
    rows = database.fetch_all(
        """SELECT a.id,s.result_json FROM assets a JOIN stage_results s ON s.id=(
            SELECT s2.id FROM stage_results s2 WHERE s2.asset_id=a.id AND s2.stage='wd14' AND s2.status='succeeded'
            ORDER BY s2.updated_at DESC LIMIT 1)
        WHERE a.project_id=? AND a.eligible=1""",
        (project_id,),
    )
    vectors: dict[str, dict[str, float]] = {}
    for row in rows:
        result = json.loads(row["result_json"])
        source = result.get("vector") or result.get("general") or []
        vectors[row["id"]] = {str(item["tag"]): float(item["confidence"]) for item in source[:128]}
    parent = {asset_id: asset_id for asset_id in vectors}
    strongest = {asset_id: 0.0 for asset_id in vectors}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    postings: dict[str, list[str]] = defaultdict(list)
    seen_pairs: set[tuple[str, str]] = set()
    for asset_id, vector in vectors.items():
        candidates: set[str] = set()
        for tag, _score in sorted(vector.items(), key=lambda item: item[1], reverse=True)[:12]:
            candidates.update(postings[tag][-200:])
        for other in candidates:
            pair = tuple(sorted((asset_id, other)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            similarity = _cosine(vector, vectors[other])
            if similarity >= threshold:
                union(asset_id, other)
                strongest[asset_id] = max(strongest[asset_id], similarity)
                strongest[other] = max(strongest[other], similarity)
        for tag, _score in sorted(vector.items(), key=lambda item: item[1], reverse=True)[:12]:
            postings[tag].append(asset_id)
    groups: dict[str, list[str]] = defaultdict(list)
    for asset_id in vectors:
        groups[find(asset_id)].append(asset_id)
    grouped = [sorted(members) for members in groups.values() if len(members) > 1]
    with database.transaction() as connection:
        connection.execute("DELETE FROM semantic_groups WHERE project_id=?", (project_id,))
        for members in grouped:
            group_id = "semantic-" + hashlib.sha1("\n".join(members).encode()).hexdigest()[:12]
            for asset_id in members:
                connection.execute(
                    "INSERT INTO semantic_groups(asset_id,project_id,group_id,similarity) VALUES(?,?,?,?)",
                    (asset_id, project_id, group_id, strongest[asset_id]),
                )
    return len(grouped)
