from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
ROOT = WORKER.parents[1]
sys.path.insert(0, str(WORKER))

from constants import APP_VERSION


class VersionTests(unittest.TestCase):
    def test_release_versions_stay_in_sync(self) -> None:
        root_version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
        desktop_version = json.loads(
            (ROOT / "apps" / "desktop" / "package.json").read_text(encoding="utf-8")
        )["version"]

        self.assertEqual(root_version, desktop_version)
        self.assertEqual(root_version, APP_VERSION)


if __name__ == "__main__":
    unittest.main()
