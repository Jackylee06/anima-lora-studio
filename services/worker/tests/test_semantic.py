from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

from database import Database
from semantic import assign_semantic_groups
from util import utc_now


class SemanticGroupingTests(unittest.TestCase):
    def test_groups_similar_wd_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "project.sqlite3")
            database.initialize()
            now = utc_now()
            database.execute(
                "INSERT INTO projects VALUES('p','p',?,'source','pixiv/{id}','character','abc','{}',?,?)",
                (temporary, now, now),
            )
            vectors = {
                "a": [("red_hair", .95), ("blue_eyes", .9), ("solo", .8)],
                "b": [("red_hair", .93), ("blue_eyes", .88), ("solo", .79)],
                "c": [("landscape", .98), ("mountain", .9), ("no_humans", .8)],
            }
            for asset_id, vector in vectors.items():
                database.execute(
                    """INSERT INTO assets(id,project_id,source_path,relative_path,kind,eligible,review_state,size_bytes,
                    mtime_ns,metadata_json,created_at,updated_at) VALUES(?,?,?,?, 'image',1,'kept',1,1,'{}',?,?)""",
                    (asset_id, "p", f"source/{asset_id}.png", f"{asset_id}.png", now, now),
                )
                result = {"vector": [{"tag": tag, "confidence": score} for tag, score in vector]}
                database.execute(
                    """INSERT INTO stage_results(id,project_id,asset_id,stage,model_id,config_hash,status,result_json,
                    created_at,updated_at) VALUES(?,?,?,'wd14','mock','hash','succeeded',?,?,?)""",
                    (f"s-{asset_id}", "p", asset_id, json.dumps(result), now, now),
                )
            self.assertEqual(assign_semantic_groups(database, "p"), 1)
            grouped = database.fetch_all("SELECT asset_id,group_id FROM semantic_groups ORDER BY asset_id")
            self.assertEqual([row["asset_id"] for row in grouped], ["a", "b"])
            self.assertEqual(grouped[0]["group_id"], grouped[1]["group_id"])


if __name__ == "__main__":
    unittest.main()
