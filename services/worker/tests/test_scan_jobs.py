from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

from service import WorkerService


class ScanJobTests(unittest.TestCase):
    def test_scan_job_registers_first_then_schedules_background_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "pixiv"
            image_dir = source / "AI" / "All Ages" / "artist-12"
            image_dir.mkdir(parents=True)
            Image.new("RGB", (900, 1200), "purple").save(image_dir / "300_p0-demo.png")
            (image_dir / "301-story.txt").write_text("novel", encoding="utf-8")

            with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "local")}, clear=False):
                events: list[tuple[str, dict]] = []
                service = WorkerService(lambda event, data: events.append((event, data)))
                service.project_create({
                    "name": "jobs",
                    "sourceRoot": str(source),
                    "workspacePath": str(root / "jobs.alora"),
                    "profile": "character",
                    "trigger": "charx012",
                })
                first_job = service.scan_start({})
                assert service.jobs is not None
                service.jobs.futures[first_job["id"]].result(timeout=10)
                registered = service.jobs.get(first_job["id"])

                self.assertEqual(registered["state"], "succeeded")
                analysis_id = registered["result"]["analysisJobId"]
                self.assertEqual(registered["result"]["pendingAnalysis"], 1)
                service.jobs.futures[analysis_id].result(timeout=10)
                analyzed = service.jobs.get(analysis_id)

                self.assertEqual(analyzed["state"], "succeeded")
                self.assertEqual(analyzed["result"]["processed"], 1)
                page = service.assets_query({"kind": "image", "limit": 10})
                self.assertEqual(page["total"], 1)
                asset = page["items"][0]
                self.assertIsNotNone(asset["thumbnailPath"])
                self.assertIsNotNone(asset["metrics"]["technicalScore"])
                service.system_shutdown({})


if __name__ == "__main__":
    unittest.main()
