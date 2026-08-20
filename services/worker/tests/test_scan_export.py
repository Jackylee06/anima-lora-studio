from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

from database import Database
from exporter import export_dataset
from naming import DEFAULT_TEMPLATE, compile_template
from scanner import scan_project
from util import new_id, utc_now


def digest_tree(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}


class ScanExportTests(unittest.TestCase):
    def test_incremental_scan_preserves_review_and_excludes_non_static_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "pixiv"
            image_dir = source / "AI" / "R-18" / "作者-9"
            image_dir.mkdir(parents=True)
            valid = image_dir / "200_p0-lowres.png"
            broken = image_dir / "201_p0-broken.png"
            animated = image_dir / "202_p0-animation.gif"
            novel = image_dir / "203-story.txt"
            Image.new("RGB", (128, 192), "red").save(valid)
            broken.write_bytes(b"not an image")
            frames = [Image.new("RGB", (64, 64), color) for color in ("red", "blue")]
            frames[0].save(animated, save_all=True, append_images=frames[1:], duration=50, loop=0)
            novel.write_text("novel", encoding="utf-8")
            workspace = root / "incremental.alora"
            database = Database(workspace / "project.sqlite3")
            database.initialize()
            project = {
                "id": new_id("project"), "name": "incremental", "workspacePath": str(workspace),
                "sourceRoot": str(source), "pathTemplate": DEFAULT_TEMPLATE, "profile": "character", "trigger": "charx002",
            }
            now = utc_now()
            database.execute(
                "INSERT INTO projects(id,name,workspace_path,source_root,path_template,profile,trigger,settings_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'{}',?,?)",
                (project["id"], project["name"], project["workspacePath"], project["sourceRoot"], project["pathTemplate"], project["profile"], project["trigger"], now, now),
            )
            first = scan_project(database, project, compile_template(DEFAULT_TEMPLATE))
            self.assertEqual(first["counts"]["image"], 1)
            self.assertEqual(first["counts"]["unsupported"], 1)
            self.assertEqual(first["counts"]["animated"], 1)
            self.assertEqual(first["counts"]["novel"], 1)
            row = database.fetch_one("SELECT * FROM assets WHERE source_path=?", (str(valid.resolve()),))
            self.assertIsNotNone(row)
            database.execute("UPDATE assets SET review_state='kept' WHERE id=?", (row["id"],))
            thumbnail_mtime = Path(row["thumbnail_path"]).stat().st_mtime_ns
            second = scan_project(database, project, compile_template(DEFAULT_TEMPLATE))
            updated = database.fetch_one("SELECT * FROM assets WHERE id=?", (row["id"],))
            self.assertEqual(second["scanned"], 4)
            self.assertEqual(updated["review_state"], "kept")
            self.assertEqual(Path(updated["thumbnail_path"]).stat().st_mtime_ns, thumbnail_mtime)

    def test_scan_and_export_never_mutate_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "pixiv"
            image_dir = source / "AI" / "All Ages" / "测试作者-123"
            image_dir.mkdir(parents=True)
            first = image_dir / "100_p0-第一张.png"
            second = image_dir / "100_p1-第二张.png"
            Image.new("RGB", (800, 1000), (220, 80, 140)).save(first)
            Image.new("RGB", (800, 1000), (220, 80, 140)).save(second)
            before = digest_tree(source)
            workspace = root / "demo.alora"
            database = Database(workspace / "project.sqlite3")
            database.initialize()
            project = {
                "id": new_id("project"), "name": "demo", "workspacePath": str(workspace),
                "sourceRoot": str(source), "pathTemplate": DEFAULT_TEMPLATE, "profile": "character", "trigger": "charx001",
            }
            now = utc_now()
            database.execute(
                "INSERT INTO projects(id,name,workspace_path,source_root,path_template,profile,trigger,settings_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'{}',?,?)",
                (project["id"], project["name"], project["workspacePath"], project["sourceRoot"], project["pathTemplate"], project["profile"], project["trigger"], now, now),
            )
            result = scan_project(database, project, compile_template(DEFAULT_TEMPLATE))
            self.assertEqual(result["scanned"], 2)
            self.assertGreaterEqual(result["duplicateGroups"], 1)
            self.assertEqual(before, digest_tree(source))
            rows = database.fetch_all("SELECT * FROM assets")
            for row in rows:
                database.execute("UPDATE assets SET review_state='kept' WHERE id=?", (row["id"],))
                database.execute(
                    "INSERT INTO caption_revisions(id,project_id,asset_id,revision,profile,sources_json,sections_json,final_text,status,created_at) VALUES(?,?,?,?,?,'{}','{}',?,'approved',?)",
                    (new_id("caption"), project["id"], row["id"], 1, "character", "1girl, charx001", now),
                )
            exported = export_dataset(database, project, {"requireApproved": True})
            self.assertEqual(exported["imageCount"], 2)
            self.assertTrue(Path(exported["datasetConfigPath"]).is_file())
            manifest = json.loads(Path(exported["manifestPath"]).read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["assets"]), 2)
            self.assertEqual(before, digest_tree(source))


if __name__ == "__main__":
    unittest.main()
