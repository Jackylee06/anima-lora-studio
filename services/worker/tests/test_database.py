from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

from database import Database, SCHEMA, SCHEMA_VERSION


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_v1_project_adds_previous_source_hash_column(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy.sqlite3"
            legacy_schema = SCHEMA.replace("    analysis_previous_sha256 TEXT,\n", "")
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript(legacy_schema)
                connection.execute(
                    "INSERT INTO schema_meta(key,value) VALUES('schema_version','1') "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                )
                connection.commit()

            Database(path).initialize()

            with closing(sqlite3.connect(path)) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
                version = connection.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0]
            self.assertIn("analysis_previous_sha256", columns)
            self.assertEqual(version, str(SCHEMA_VERSION))


if __name__ == "__main__":
    unittest.main()
