from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    workspace_path TEXT NOT NULL,
    source_root TEXT NOT NULL,
    path_template TEXT NOT NULL,
    profile TEXT NOT NULL CHECK(profile IN ('character','style','custom')),
    trigger TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    thumbnail_path TEXT,
    kind TEXT NOT NULL,
    eligible INTEGER NOT NULL DEFAULT 0,
    exclusion_reason TEXT,
    review_state TEXT NOT NULL DEFAULT 'pending' CHECK(review_state IN ('pending','kept','rejected')),
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT,
    perceptual_hash TEXT,
    width INTEGER,
    height INTEGER,
    aspect_ratio REAL,
    blur_score REAL,
    technical_score REAL,
    duplicate_group TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, source_path)
);

CREATE INDEX IF NOT EXISTS idx_assets_project_review ON assets(project_id, review_state);
CREATE INDEX IF NOT EXISTS idx_assets_project_eligible ON assets(project_id, eligible);
CREATE INDEX IF NOT EXISTS idx_assets_project_path ON assets(project_id, relative_path);

CREATE TABLE IF NOT EXISTS stage_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    model_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(asset_id, stage, model_id, config_hash)
);

CREATE INDEX IF NOT EXISTS idx_stage_asset ON stage_results(asset_id, stage);

CREATE TABLE IF NOT EXISTS semantic_groups (
    asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL,
    similarity REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_semantic_project_group ON semantic_groups(project_id, group_id);

CREATE TABLE IF NOT EXISTS caption_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    profile TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '{}',
    sections_json TEXT NOT NULL DEFAULT '{}',
    final_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','needs_review','approved')),
    created_at TEXT NOT NULL,
    UNIQUE(asset_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_caption_asset_revision ON caption_revisions(asset_id, revision DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    job_id TEXT,
    state TEXT NOT NULL,
    export_path TEXT NOT NULL,
    output_path TEXT NOT NULL,
    config_json TEXT NOT NULL,
    command_json TEXT NOT NULL,
    current_step INTEGER NOT NULL DEFAULT 0,
    total_steps INTEGER NOT NULL DEFAULT 0,
    latest_loss REAL,
    latest_checkpoint TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    local_path TEXT,
    sha256 TEXT,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS license_acceptances (
    license_id TEXT PRIMARY KEY,
    accepted_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(sql, params).fetchone()

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(sql, params).fetchall())

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(sql, params)
            return cursor.rowcount


def json_value(row: sqlite3.Row, key: str, default: Any) -> Any:
    raw = row[key]
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default
