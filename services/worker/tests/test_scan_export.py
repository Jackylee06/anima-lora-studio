from __future__ import annotations

import hashlib
import json
import os
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
from scanner import analyze_assets, register_assets, scan_project
from util import new_id, utc_now


def digest_tree(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}


class ScanExportTests(unittest.TestCase):
    def test_only_real_content_changes_invalidate_review_captions_and_stage_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "pixiv"
            image_dir = source / "AI" / "All Ages" / "作者-8"
            image_dir.mkdir(parents=True)
            changed = image_dir / "800_p0-changed.png"
            touched = image_dir / "801_p0-touched.png"
            Image.new("RGB", (320, 480), "red").save(changed)
            Image.new("RGB", (320, 480), "green").save(touched)
            workspace = root / "content-change.alora"
            database = Database(workspace / "project.sqlite3")
            database.initialize()
            project = {
                "id": new_id("project"), "name": "change", "workspacePath": str(workspace),
                "sourceRoot": str(source), "pathTemplate": DEFAULT_TEMPLATE, "profile": "character", "trigger": "charx008",
            }
            now = utc_now()
            database.execute(
                "INSERT INTO projects(id,name,workspace_path,source_root,path_template,profile,trigger,settings_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'{}',?,?)",
                (project["id"], project["name"], project["workspacePath"], project["sourceRoot"], project["pathTemplate"], project["profile"], project["trigger"], now, now),
            )
            scan_project(database, project, compile_template(DEFAULT_TEMPLATE))
            rows = database.fetch_all("SELECT * FROM assets ORDER BY relative_path")
            old_hashes = {row["relative_path"]: row["sha256"] for row in rows}
            for row in rows:
                database.execute("UPDATE assets SET review_state='kept' WHERE id=?", (row["id"],))
                database.execute(
                    "INSERT INTO caption_revisions(id,project_id,asset_id,revision,profile,sources_json,sections_json,final_text,status,created_at) VALUES(?,?,?,?,?,'{}','{}',?,'approved',?)",
                    (new_id("caption"), project["id"], row["id"], 1, "character", "approved caption", now),
                )
                database.execute(
                    "INSERT INTO stage_results(id,project_id,asset_id,stage,model_id,config_hash,status,result_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'{}',?,?)",
                    (new_id("stage"), project["id"], row["id"], "wd14", "mock", "old", "succeeded", now, now),
                )

            Image.new("RGB", (320, 480), "blue").save(changed)
            touched_stat = touched.stat()
            os.utime(touched, ns=(touched_stat.st_atime_ns, touched_stat.st_mtime_ns + 2_000_000_000))
            register_assets(database, project, compile_template(DEFAULT_TEMPLATE))
            pending_changed = database.fetch_one("SELECT * FROM assets WHERE source_path=?", (str(changed.resolve()),))
            pending_touched = database.fetch_one("SELECT * FROM assets WHERE source_path=?", (str(touched.resolve()),))
            self.assertFalse(pending_changed["eligible"])
            self.assertFalse(pending_touched["eligible"])
            self.assertEqual(pending_changed["exclusion_reason"], "source_change_pending")
            self.assertEqual(pending_touched["exclusion_reason"], "source_change_pending")
            analyzed = analyze_assets(database, project)

            changed_row = database.fetch_one("SELECT * FROM assets WHERE source_path=?", (str(changed.resolve()),))
            touched_row = database.fetch_one("SELECT * FROM assets WHERE source_path=?", (str(touched.resolve()),))
            self.assertEqual(analyzed["contentChanged"], 1)
            self.assertNotEqual(changed_row["sha256"], old_hashes[changed_row["relative_path"]])
            self.assertEqual(changed_row["review_state"], "pending")
            self.assertEqual(touched_row["sha256"], old_hashes[touched_row["relative_path"]])
            self.assertEqual(touched_row["review_state"], "kept")
            changed_caption = database.fetch_one("SELECT status FROM caption_revisions WHERE asset_id=?", (changed_row["id"],))
            touched_caption = database.fetch_one("SELECT status FROM caption_revisions WHERE asset_id=?", (touched_row["id"],))
            changed_stage = database.fetch_one("SELECT status FROM stage_results WHERE asset_id=?", (changed_row["id"],))
            touched_stage = database.fetch_one("SELECT status FROM stage_results WHERE asset_id=?", (touched_row["id"],))
            self.assertEqual(changed_caption["status"], "needs_review")
            self.assertEqual(touched_caption["status"], "approved")
            self.assertEqual(changed_stage["status"], "stale")
            self.assertEqual(touched_stage["status"], "succeeded")

    def test_fast_registration_exposes_assets_before_background_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "pixiv"
            image_dir = source / "AI" / "All Ages" / "作者-7"
            image_dir.mkdir(parents=True)
            image = image_dir / "700_p0-fast.png"
            Image.new("RGB", (1200, 1600), "purple").save(image)
            workspace = root / "fast.alora"
            database = Database(workspace / "project.sqlite3")
            database.initialize()
            project = {
                "id": new_id("project"), "name": "fast", "workspacePath": str(workspace),
                "sourceRoot": str(source), "pathTemplate": DEFAULT_TEMPLATE, "profile": "character", "trigger": "charx007",
            }
            now = utc_now()
            database.execute(
                "INSERT INTO projects(id,name,workspace_path,source_root,path_template,profile,trigger,settings_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'{}',?,?)",
                (project["id"], project["name"], project["workspacePath"], project["sourceRoot"], project["pathTemplate"], project["profile"], project["trigger"], now, now),
            )

            registered = register_assets(database, project, compile_template(DEFAULT_TEMPLATE))
            row = database.fetch_one("SELECT * FROM assets WHERE project_id=?", (project["id"],))
            self.assertEqual(registered["pendingAnalysis"], 1)
            self.assertEqual(row["kind"], "image")
            self.assertIsNone(row["thumbnail_path"])
            self.assertIsNone(row["technical_score"])

            analyzed = analyze_assets(database, project)
            row = database.fetch_one("SELECT * FROM assets WHERE project_id=?", (project["id"],))
            self.assertEqual(analyzed["processed"], 1)
            self.assertTrue(Path(row["thumbnail_path"]).is_file())
            self.assertIsNotNone(row["technical_score"])

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

            registered = register_assets(database, project, compile_template(DEFAULT_TEMPLATE))
            broken_row = database.fetch_one("SELECT * FROM assets WHERE source_path=?", (str(broken.resolve()),))
            self.assertEqual(broken_row["kind"], "unsupported")
            self.assertEqual(registered["pendingAnalysis"], 0)

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
