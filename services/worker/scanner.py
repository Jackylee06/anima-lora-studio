from __future__ import annotations

import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from database import Database, json_value
from naming import CompiledTemplate, parse_with_source_root
from util import new_id, sha256_file, utc_now

try:
    from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError
except ImportError:  # packaged worker includes Pillow; this keeps diagnostics readable in source mode
    Image = ImageFilter = ImageStat = None  # type: ignore[assignment]
    UnidentifiedImageError = OSError  # type: ignore[assignment,misc]


STATIC_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".jxl"}
ANIMATED_EXTENSIONS = {".gif", ".apng", ".zip"}
NOVEL_EXTENSIONS = {".txt", ".epub"}


def _dhash(image: Any) -> str:
    grayscale = image.convert("L").resize((9, 8))
    pixels = list(grayscale.getdata())
    value = 0
    for row in range(8):
        for column in range(8):
            left = pixels[row * 9 + column]
            right = pixels[row * 9 + column + 1]
            value = (value << 1) | int(left > right)
    return f"{value:016x}"


def _blur_score(image: Any) -> float:
    sample = image.convert("L")
    sample.thumbnail((768, 768))
    edges = sample.filter(ImageFilter.FIND_EDGES)
    variance = float(ImageStat.Stat(edges).var[0])
    return round(variance, 4)


def _technical_score(width: int, height: int, blur: float) -> float:
    short_edge = min(width, height)
    resolution = max(0.0, min(1.0, (short_edge - 256) / (1024 - 256))) * 45
    sharpness = max(0.0, min(1.0, math.log1p(max(0.0, blur)) / math.log1p(250))) * 40
    aspect = max(width / max(height, 1), height / max(width, 1))
    aspect_score = max(0.0, 1.0 - max(0.0, aspect - 2.0) / 2.0) * 15
    return round(resolution + sharpness + aspect_score, 2)


def _inspect_image(path: Path, thumbnail_path: Path) -> dict[str, Any]:
    if Image is None:
        raise RuntimeError("Pillow 未安装，无法扫描图片")
    with Image.open(path) as image:
        animated = bool(getattr(image, "is_animated", False) and getattr(image, "n_frames", 1) > 1)
        image.seek(0)
        image.load()
        width, height = image.size
        rgb = image.convert("RGB")
        blur = _blur_score(rgb)
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        thumb = rgb.copy()
        thumb.thumbnail((512, 512))
        thumb.save(thumbnail_path, "WEBP", quality=82, method=4)
        return {
            "animated": animated,
            "width": width,
            "height": height,
            "aspect_ratio": round(width / max(height, 1), 6),
            "blur_score": blur,
            "technical_score": _technical_score(width, height, blur),
            "perceptual_hash": _dhash(rgb),
        }


def _hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


class _BKNode:
    def __init__(self, value: str, asset_id: str):
        self.value = value
        self.assets = [asset_id]
        self.children: dict[int, _BKNode] = {}

    def insert(self, value: str, asset_id: str) -> None:
        distance = _hamming(self.value, value)
        if distance == 0:
            self.assets.append(asset_id)
            return
        child = self.children.get(distance)
        if child:
            child.insert(value, asset_id)
        else:
            self.children[distance] = _BKNode(value, asset_id)

    def query(self, value: str, radius: int, output: list[str]) -> None:
        distance = _hamming(self.value, value)
        if distance <= radius:
            output.extend(self.assets)
        for edge, child in self.children.items():
            if distance - radius <= edge <= distance + radius:
                child.query(value, radius, output)


class _UnionFind:
    def __init__(self, values: Iterable[str]):
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


def assign_duplicate_groups(database: Database, project_id: str, radius: int = 4) -> int:
    rows = database.fetch_all(
        "SELECT id, perceptual_hash FROM assets WHERE project_id=? AND eligible=1 AND perceptual_hash IS NOT NULL",
        (project_id,),
    )
    if not rows:
        return 0
    ids = [str(row["id"]) for row in rows]
    union = _UnionFind(ids)
    tree: _BKNode | None = None
    for row in rows:
        asset_id, value = str(row["id"]), str(row["perceptual_hash"])
        if tree is None:
            tree = _BKNode(value, asset_id)
        else:
            neighbors: list[str] = []
            tree.query(value, radius, neighbors)
            for neighbor in neighbors:
                union.union(asset_id, neighbor)
            tree.insert(value, asset_id)
    groups: dict[str, list[str]] = defaultdict(list)
    for asset_id in ids:
        groups[union.find(asset_id)].append(asset_id)
    duplicate_groups = [members for members in groups.values() if len(members) > 1]
    now = utc_now()
    with database.transaction() as connection:
        connection.execute("UPDATE assets SET duplicate_group=NULL WHERE project_id=?", (project_id,))
        for members in duplicate_groups:
            group_id = f"dup_{hashlib.sha1('|'.join(sorted(members)).encode()).hexdigest()[:12]}"
            connection.executemany(
                "UPDATE assets SET duplicate_group=?, updated_at=? WHERE id=?",
                [(group_id, now, member) for member in members],
            )
    return len(duplicate_groups)


def scan_project(
    database: Database,
    project: dict[str, Any],
    compiled_template: CompiledTemplate,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    registered = register_assets(database, project, compiled_template, progress)
    analyzed = analyze_assets(database, project, progress)
    counts = {
        str(row["kind"]): int(row["count"])
        for row in database.fetch_all(
            "SELECT kind,COUNT(*) AS count FROM assets WHERE project_id=? GROUP BY kind", (project["id"],)
        )
    }
    return {
        "scanned": registered["scanned"],
        "counts": counts,
        "missing": registered["missing"],
        "duplicateGroups": analyzed["duplicateGroups"],
        "contentChanged": analyzed["contentChanged"],
        "sourceMutated": False,
    }


def register_assets(
    database: Database,
    project: dict[str, Any],
    compiled_template: CompiledTemplate,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Quickly register filesystem metadata; image analysis is intentionally deferred."""
    source_root = Path(project["sourceRoot"]).resolve()
    workspace = Path(project["workspacePath"]).resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"源目录不存在：{source_root}")
    thumbnails = workspace / "thumbnails"
    files = [path for path in source_root.rglob("*") if path.is_file()]
    existing_rows = database.fetch_all("SELECT * FROM assets WHERE project_id=?", (project["id"],))
    existing = {str(row["source_path"]): row for row in existing_rows}
    seen: set[str] = set()
    counts = defaultdict(int)
    now = utc_now()
    records: list[tuple[Any, ...]] = []
    was_cancelled = False

    for index, path in enumerate(files, start=1):
        if cancelled and cancelled():
            was_cancelled = True
            break
        relative = path.relative_to(source_root).as_posix()
        suffix = path.suffix.lower()
        if suffix not in STATIC_EXTENSIONS | ANIMATED_EXTENSIONS | NOVEL_EXTENSIONS:
            continue
        if progress:
            progress(index, len(files), f"登记 {relative}")
        absolute = str(path.resolve())
        seen.add(absolute)
        stat = path.stat()
        prior = existing.get(absolute)
        unchanged = prior is not None and prior["size_bytes"] == stat.st_size and prior["mtime_ns"] == stat.st_mtime_ns
        asset_id = str(prior["id"]) if prior else new_id("asset")
        metadata = parse_with_source_root(compiled_template, source_root.name, relative)
        metadata_json: dict[str, Any] = metadata or {"path_template_matched": False}
        metadata_json["path_template_matched"] = metadata is not None
        kind = "unsupported"
        eligible = False
        exclusion_reason: str | None = None
        metrics: dict[str, Any] = {
            "width": None, "height": None, "aspect_ratio": None, "blur_score": None,
            "technical_score": None, "perceptual_hash": None,
        }
        sha256: str | None = str(prior["sha256"]) if unchanged and prior and prior["sha256"] else None
        analysis_previous_sha256: str | None = None
        thumbnail_path = thumbnails / f"{asset_id}.webp"
        stored_thumbnail: str | None = None

        if suffix in NOVEL_EXTENSIONS:
            kind, exclusion_reason = "novel", "novel_not_trainable"
        elif suffix in ANIMATED_EXTENSIONS:
            kind, exclusion_reason = "animated", "animated_asset_excluded"
        else:
            if prior and prior["analysis_previous_sha256"]:
                analysis_previous_sha256 = str(prior["analysis_previous_sha256"])
            elif not unchanged and prior and prior["sha256"]:
                analysis_previous_sha256 = str(prior["sha256"])
            prior_kind = str(prior["kind"]) if prior else None
            if unchanged and prior and prior_kind in {"animated", "unsupported"}:
                kind = prior_kind
                exclusion_reason = str(prior["exclusion_reason"] or "") or None
            else:
                kind = "image"
                eligible = metadata is not None
                exclusion_reason = None if eligible else "path_template_mismatch"
            if unchanged and prior and prior["width"] and Path(str(prior["thumbnail_path"] or "")).is_file():
                metrics = {
                    "width": prior["width"], "height": prior["height"], "aspect_ratio": prior["aspect_ratio"],
                    "blur_score": prior["blur_score"], "technical_score": prior["technical_score"],
                    "perceptual_hash": prior["perceptual_hash"],
                }
                stored_thumbnail = str(prior["thumbnail_path"])
            if analysis_previous_sha256:
                eligible = False
                exclusion_reason = "source_change_pending"

        counts[kind] += 1
        review_state = str(prior["review_state"]) if prior else "pending"
        created_at = str(prior["created_at"]) if prior else now
        records.append((
            asset_id, project["id"], absolute, relative, stored_thumbnail, kind, int(eligible), exclusion_reason,
            review_state, stat.st_size, stat.st_mtime_ns, sha256, analysis_previous_sha256,
            metrics["perceptual_hash"], metrics["width"], metrics["height"], metrics["aspect_ratio"],
            metrics["blur_score"], metrics["technical_score"], json.dumps(metadata_json, ensure_ascii=False),
            created_at, now,
        ))

    if records:
        with database.transaction() as connection:
            connection.executemany(
                """INSERT INTO assets(
                    id,project_id,source_path,relative_path,thumbnail_path,kind,eligible,exclusion_reason,review_state,
                    size_bytes,mtime_ns,sha256,analysis_previous_sha256,perceptual_hash,width,height,aspect_ratio,blur_score,technical_score,
                    metadata_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id,source_path) DO UPDATE SET
                    relative_path=excluded.relative_path,thumbnail_path=excluded.thumbnail_path,kind=excluded.kind,
                    eligible=excluded.eligible,exclusion_reason=excluded.exclusion_reason,size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,sha256=excluded.sha256,
                    analysis_previous_sha256=excluded.analysis_previous_sha256,perceptual_hash=excluded.perceptual_hash,
                    width=excluded.width,height=excluded.height,aspect_ratio=excluded.aspect_ratio,
                    blur_score=excluded.blur_score,technical_score=excluded.technical_score,
                    metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                records,
            )

    missing = [] if was_cancelled else [path for path in existing if path not in seen]
    if missing:
        with database.transaction() as connection:
            connection.executemany(
                "UPDATE assets SET eligible=0, exclusion_reason='source_missing', updated_at=? WHERE project_id=? AND source_path=?",
                [(now, project["id"], item) for item in missing],
            )
    pending_row = database.fetch_one(
        """SELECT COUNT(*) AS count FROM assets WHERE project_id=? AND kind='image'
        AND (sha256 IS NULL OR thumbnail_path IS NULL OR width IS NULL)""",
        (project["id"],),
    )
    return {
        "scanned": sum(counts.values()),
        "counts": dict(counts),
        "missing": len(missing),
        "pendingAnalysis": int(pending_row["count"]) if pending_row else 0,
        "cancelled": was_cancelled,
        "sourceMutated": False,
    }


def analyze_assets(
    database: Database,
    project: dict[str, Any],
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    workspace = Path(project["workspacePath"]).resolve()
    thumbnails = workspace / "thumbnails"
    rows = database.fetch_all(
        """SELECT * FROM assets WHERE project_id=? AND kind='image'
        AND (sha256 IS NULL OR thumbnail_path IS NULL OR width IS NULL) ORDER BY relative_path""",
        (project["id"],),
    )
    updates: list[tuple[Any, ...]] = []
    invalidations: list[str] = []
    processed = failed = animated = 0
    content_changed = 0
    was_cancelled = False

    def flush() -> None:
        if not updates:
            return
        with database.transaction() as connection:
            connection.executemany(
                """UPDATE assets SET thumbnail_path=?,kind=?,eligible=?,exclusion_reason=?,sha256=?,
                analysis_previous_sha256=NULL,perceptual_hash=?,width=?,height=?,aspect_ratio=?,blur_score=?,
                technical_score=?,review_state=?,updated_at=? WHERE id=?""",
                updates,
            )
            if invalidations:
                now = utc_now()
                connection.executemany(
                    "UPDATE stage_results SET status='stale',updated_at=? WHERE asset_id=? AND status='succeeded'",
                    [(now, asset_id) for asset_id in invalidations],
                )
                connection.executemany(
                    "UPDATE caption_revisions SET status='needs_review' WHERE asset_id=? AND status='approved'",
                    [(asset_id,) for asset_id in invalidations],
                )
                connection.executemany(
                    "DELETE FROM semantic_groups WHERE asset_id=?",
                    [(asset_id,) for asset_id in invalidations],
                )
        updates.clear()
        invalidations.clear()

    for index, row in enumerate(rows, start=1):
        if cancelled and cancelled():
            was_cancelled = True
            break
        path = Path(str(row["source_path"]))
        if progress:
            progress(index, len(rows), f"分析 {row['relative_path']}")
        thumbnail_path = thumbnails / f"{row['id']}.webp"
        metadata = json_value(row, "metadata_json", {})
        previous_sha256 = str(row["analysis_previous_sha256"] or "") or None
        try:
            metrics = _inspect_image(path, thumbnail_path)
            current_sha256 = sha256_file(path)
            changed = previous_sha256 is not None and current_sha256 != previous_sha256
            is_animated = bool(metrics.pop("animated", False))
            if is_animated:
                kind, eligible, reason = "animated", 0, "animated_asset_excluded"
                animated += 1
            else:
                kind = "image"
                eligible = int(bool(metadata.get("path_template_matched")))
                reason = None if eligible else "path_template_mismatch"
            updates.append((
                str(thumbnail_path), kind, eligible, reason, current_sha256, metrics["perceptual_hash"],
                metrics["width"], metrics["height"], metrics["aspect_ratio"], metrics["blur_score"],
                metrics["technical_score"], "pending" if changed else row["review_state"], utc_now(), row["id"],
            ))
            if changed:
                invalidations.append(str(row["id"]))
                content_changed += 1
            processed += 1
        except (UnidentifiedImageError, OSError, ValueError) as error:
            changed = previous_sha256 is not None
            updates.append((
                None, "unsupported", 0, f"decode_failed:{type(error).__name__}", None,
                None, None, None, None, None, None, "pending" if changed else row["review_state"], utc_now(), row["id"],
            ))
            if changed:
                invalidations.append(str(row["id"]))
                content_changed += 1
            failed += 1
        if len(updates) >= 50:
            flush()
    flush()
    duplicate_groups = 0 if was_cancelled else assign_duplicate_groups(database, project["id"])
    return {
        "processed": processed,
        "failed": failed,
        "animated": animated,
        "contentChanged": content_changed,
        "duplicateGroups": duplicate_groups,
        "cancelled": was_cancelled,
        "sourceMutated": False,
    }


def asset_from_row(database: Database, row: Any) -> dict[str, Any]:
    caption = database.fetch_one(
        "SELECT final_text,status FROM caption_revisions WHERE asset_id=? ORDER BY revision DESC LIMIT 1", (row["id"],)
    )
    semantic = database.fetch_one("SELECT group_id,similarity FROM semantic_groups WHERE asset_id=?", (row["id"],))
    return {
        "id": row["id"],
        "sourcePath": row["source_path"],
        "relativePath": row["relative_path"],
        "thumbnailPath": row["thumbnail_path"],
        "kind": row["kind"],
        "eligible": bool(row["eligible"]),
        "exclusionReason": row["exclusion_reason"],
        "reviewState": row["review_state"],
        "metadata": json_value(row, "metadata_json", {}),
        "metrics": {
            "width": row["width"], "height": row["height"], "aspectRatio": row["aspect_ratio"],
            "blurScore": row["blur_score"], "technicalScore": row["technical_score"],
            "perceptualHash": row["perceptual_hash"], "duplicateGroup": row["duplicate_group"],
            "semanticGroup": semantic["group_id"] if semantic else None,
            "semanticSimilarity": semantic["similarity"] if semantic else None,
        },
        "latestCaption": caption["final_text"] if caption else None,
        "captionStatus": caption["status"] if caption else "missing",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
