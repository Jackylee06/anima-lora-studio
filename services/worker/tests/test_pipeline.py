from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

from database import Database
from naming import DEFAULT_TEMPLATE, compile_template
from pipeline import run_joycaption, run_refine, run_wd14
from scanner import scan_project
from util import new_id, utc_now


class MockPipelineTests(unittest.TestCase):
    def test_mock_pipeline_caches_and_invalid_llm_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "pixiv"
            directory = source / "AI" / "All Ages" / "artist-12"
            directory.mkdir(parents=True)
            image_path = directory / "300_p0-demo.png"
            Image.new("RGB", (640, 896), "purple").save(image_path)
            workspace = root / "pipeline.alora"
            database = Database(workspace / "project.sqlite3")
            database.initialize()
            project = {
                "id": new_id("project"), "name": "pipeline", "workspacePath": str(workspace),
                "sourceRoot": str(source), "pathTemplate": DEFAULT_TEMPLATE, "profile": "character", "trigger": "charx003",
            }
            now = utc_now()
            database.execute(
                "INSERT INTO projects(id,name,workspace_path,source_root,path_template,profile,trigger,settings_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'{}',?,?)",
                (project["id"], project["name"], project["workspacePath"], project["sourceRoot"], project["pathTemplate"], project["profile"], project["trigger"], now, now),
            )
            scan_project(database, project, compile_template(DEFAULT_TEMPLATE))
            database.execute("UPDATE assets SET review_state='kept' WHERE project_id=?", (project["id"],))
            cancel = threading.Event()
            noop = lambda _current, _total, _message: None
            first = run_wd14(database, project, {"mock": True, "modelId": "mock"}, noop, cancel)
            second = run_wd14(database, project, {"mock": True, "modelId": "mock"}, noop, cancel)
            self.assertEqual(first["processed"], 1)
            self.assertEqual(second["cached"], 1)
            self.assertEqual(run_joycaption(database, project, {"mock": True, "modelId": "mock"}, noop, cancel)["processed"], 1)
            with patch("pipeline.chat", return_value="not json") as call:
                refined = run_refine(
                    database, project,
                    {"provider": {"kind": "openai", "model": "fake", "baseUrl": "http://unused"}},
                    noop, cancel,
                )
            self.assertEqual(call.call_count, 3)
            self.assertEqual(refined["fallback"], 1)
            caption = database.fetch_one("SELECT * FROM caption_revisions ORDER BY revision DESC LIMIT 1")
            self.assertEqual(caption["status"], "needs_review")
            self.assertIn("charx003", caption["final_text"])


if __name__ == "__main__":
    unittest.main()
