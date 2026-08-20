from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from captions import ANIMA_RULES_VERSION
from database import Database, json_value
from util import atomic_write_json, atomic_write_text, new_id, safe_slug, utc_now


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _same_volume(left: Path, right: Path) -> bool:
    if os.name == "nt":
        return left.drive.casefold() == right.drive.casefold()
    return left.stat().st_dev == right.parent.stat().st_dev


def _training_config(project: dict[str, Any], image_count: int) -> dict[str, Any]:
    effective_batch = 4
    if project["profile"] == "style":
        steps = max(1200, min(3000, (image_count * 50 + effective_batch - 1) // effective_batch))
    else:
        steps = max(600, min(1600, (image_count * 100 + effective_batch - 1) // effective_batch))
    checkpoint = max(100, round((steps / 5) / 50) * 50)
    return {
        "profile": project["profile"], "networkDim": 32, "networkAlpha": 32, "learningRate": 2e-5,
        "batchSize": 1, "gradientAccumulationSteps": 4, "maxTrainSteps": steps,
        "minBucketResolution": 512, "maxBucketResolution": 1024, "bucketResolutionSteps": 64,
        "saveEverySteps": checkpoint, "sampleEverySteps": checkpoint, "seed": 42, "advancedArgs": {},
    }


def export_dataset(
    database: Database,
    project: dict[str, Any],
    params: dict[str, Any],
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    require_approved = bool(params.get("requireApproved", True))
    rows = database.fetch_all(
        "SELECT * FROM assets WHERE project_id=? AND eligible=1 AND review_state='kept' ORDER BY relative_path",
        (project["id"],),
    )
    if not rows:
        raise ValueError("没有已保留且可训练的图片")
    selected: list[tuple[Any, Any]] = []
    missing: list[str] = []
    for row in rows:
        caption = database.fetch_one(
            "SELECT * FROM caption_revisions WHERE asset_id=? ORDER BY revision DESC LIMIT 1", (row["id"],)
        )
        if not caption or (require_approved and caption["status"] != "approved"):
            missing.append(str(row["relative_path"]))
        else:
            selected.append((row, caption))
    if missing:
        preview = "、".join(missing[:5])
        raise ValueError(f"有 {len(missing)} 张图片缺少已审核 caption：{preview}")

    workspace = Path(project["workspacePath"])
    export_root = Path(str(params.get("outputRoot") or workspace / "exports"))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    export_id = new_id("export")
    export_dir = export_root / f"{stamp}-{safe_slug(project['name'])}-{export_id[-8:]}"
    images_dir = export_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=False)
    manifest_assets: list[dict[str, Any]] = []
    used_names: set[str] = set()
    hardlinked = copied = 0

    for index, (row, caption) in enumerate(selected, start=1):
        source = Path(row["source_path"])
        metadata = json_value(row, "metadata_json", {})
        preferred = str(metadata.get("id") or source.stem)
        base = safe_slug(preferred, row["id"][-12:])
        candidate = base
        suffix = source.suffix.lower()
        counter = 2
        while candidate.casefold() in used_names:
            candidate = f"{base}-{counter}"
            counter += 1
        used_names.add(candidate.casefold())
        destination = images_dir / f"{candidate}{suffix}"
        strategy = "copy"
        try:
            if _same_volume(source, destination):
                os.link(source, destination)
                strategy = "hardlink"
                hardlinked += 1
            else:
                shutil.copy2(source, destination)
                copied += 1
        except OSError:
            shutil.copy2(source, destination)
            copied += 1
        atomic_write_text(images_dir / f"{candidate}.txt", str(caption["final_text"]).strip() + "\n")
        manifest_assets.append({
            "assetId": row["id"], "sourcePath": row["source_path"], "relativePath": row["relative_path"],
            "sourceSha256": row["sha256"], "exportName": destination.name, "linkStrategy": strategy,
            "captionRevision": caption["revision"], "captionStatus": caption["status"], "metadata": metadata,
        })
        if progress:
            progress(index, len(selected), str(row["relative_path"]))

    training = dict(params.get("trainingConfig") or _training_config(project, len(selected)))
    dataset_toml = f"""[general]
caption_extension = ".txt"
shuffle_caption = false
caption_dropout_rate = 0.0
enable_bucket = true
bucket_no_upscale = true
min_bucket_reso = {int(training.get('minBucketResolution', 512))}
max_bucket_reso = {int(training.get('maxBucketResolution', 1024))}
bucket_reso_steps = {int(training.get('bucketResolutionSteps', 64))}

[[datasets]]
resolution = [{int(training.get('maxBucketResolution', 1024))}, {int(training.get('maxBucketResolution', 1024))}]
batch_size = {int(training.get('batchSize', 1))}

  [[datasets.subsets]]
  image_dir = {_toml_string(str(images_dir.resolve()))}
  num_repeats = 1
"""
    atomic_write_text(export_dir / "dataset.toml", dataset_toml)
    manifest = {
        "schemaVersion": 1,
        "exportId": export_id,
        "createdAt": utc_now(),
        "project": {
            "id": project["id"], "name": project["name"], "profile": project["profile"],
            "profileBase": project.get("settings", {}).get("baseProfile", project["profile"]),
            "trigger": project["trigger"], "sourceRoot": project["sourceRoot"],
            "pathTemplate": project["pathTemplate"],
        },
        "rulesVersion": ANIMA_RULES_VERSION,
        "trainingConfig": training,
        "assets": manifest_assets,
    }
    atomic_write_json(export_dir / "manifest.json", manifest)
    return {
        "exportId": export_id, "path": str(export_dir), "imagesPath": str(images_dir),
        "datasetConfigPath": str(export_dir / "dataset.toml"), "manifestPath": str(export_dir / "manifest.json"),
        "imageCount": len(selected), "hardlinked": hardlinked, "copied": copied, "trainingConfig": training,
    }
