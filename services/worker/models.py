from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable

from database import Database, json_value
from util import new_id, sha256_file, utc_now


DEFAULT_MODELS = [
    {
        "id": "wd14-eva02-v3", "kind": "wd14", "name": "WD EVA02-Large Tagger v3",
        "source": "https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3",
        "metadata": {"repo": "SmilingWolf/wd-eva02-large-tagger-v3", "revision": "b25b82a03f7282e41aa2f257a52c7583b710bd1c", "files": ["model.onnx", "selected_tags.csv"]},
    },
    {
        "id": "joycaption-beta-one", "kind": "joycaption", "name": "JoyCaption Beta One NF4",
        "source": "https://huggingface.co/fancyfeast/llama-joycaption-beta-one-hf-llava",
        "metadata": {"repo": "fancyfeast/llama-joycaption-beta-one-hf-llava", "revision": "ebf414ea497a020da0f82df3913e5b6cb8e9663a", "precision": "nf4"},
    },
    {
        "id": "anima-base-v1", "kind": "anima_base", "name": "Anima Base v1.0",
        "source": "https://huggingface.co/circlestone-labs/Anima",
        "metadata": {"repo": "circlestone-labs/Anima", "revision": "f7382c4bf9d7ffe4ceea593a0adbb470c56dd79b", "file": "split_files/diffusion_models/anima-base-v1.0.safetensors", "trainingAllowed": True},
    },
    {
        "id": "anima-qwen3-06b", "kind": "qwen3", "name": "Anima Qwen3 0.6B Text Encoder",
        "source": "https://huggingface.co/circlestone-labs/Anima",
        "metadata": {"repo": "circlestone-labs/Anima", "revision": "f7382c4bf9d7ffe4ceea593a0adbb470c56dd79b", "file": "split_files/text_encoders/qwen_3_06b_base.safetensors"},
    },
    {
        "id": "anima-qwen-image-vae", "kind": "vae", "name": "Qwen Image VAE",
        "source": "https://huggingface.co/circlestone-labs/Anima",
        "metadata": {"repo": "circlestone-labs/Anima", "revision": "f7382c4bf9d7ffe4ceea593a0adbb470c56dd79b", "file": "split_files/vae/qwen_image_vae.safetensors"},
    },
]


class ModelRegistry:
    def __init__(self, database: Database):
        self.database = database

    def seed(self) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            for model in DEFAULT_MODELS:
                connection.execute(
                    """INSERT INTO models(id,kind,name,source,local_path,sha256,status,metadata_json,created_at,updated_at)
                    VALUES(?,?,?,?,NULL,NULL,'missing',?,?,?) ON CONFLICT(id) DO UPDATE SET
                        kind=excluded.kind,name=excluded.name,source=excluded.source,
                        metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                    (model["id"], model["kind"], model["name"], model["source"],
                     json.dumps(model["metadata"], ensure_ascii=False), now, now),
                )

    def list(self) -> list[dict[str, Any]]:
        return [self._serialize(row) for row in self.database.fetch_all("SELECT * FROM models ORDER BY kind,name")]

    def register(self, values: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(values["localPath"])).resolve()
        if not path.exists():
            raise FileNotFoundError(f"模型路径不存在：{path}")
        model_id = str(values.get("id") or new_id("model"))
        digest = sha256_file(path) if path.is_file() and values.get("verifyHash", True) else None
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO models(id,kind,name,source,local_path,sha256,status,metadata_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'ready',?,?,?)
                ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,name=excluded.name,source=excluded.source,
                    local_path=excluded.local_path,sha256=excluded.sha256,status='ready',metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at""",
                (model_id, values["kind"], values.get("name") or path.name, values.get("source") or "local",
                 str(path), digest, json.dumps(values.get("metadata") or {}, ensure_ascii=False), now, now),
            )
        row = self.database.fetch_one("SELECT * FROM models WHERE id=?", (model_id,))
        assert row is not None
        return self._serialize(row)

    def verify(self, model_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM models WHERE id=?", (model_id,))
        if not row:
            raise KeyError("模型不存在")
        local = Path(str(row["local_path"] or ""))
        status = "ready" if local.exists() else "missing"
        digest = sha256_file(local) if local.is_file() else row["sha256"]
        metadata = json_value(row, "metadata_json", {})
        if row["sha256"] and digest != row["sha256"]:
            status = "invalid"
        if local.is_dir() and metadata.get("fileHashes"):
            for relative, expected in metadata["fileHashes"].items():
                candidate = local / relative
                if not candidate.is_file() or sha256_file(candidate) != expected:
                    status = "invalid"
                    break
        self.database.execute(
            "UPDATE models SET status=?,sha256=?,updated_at=? WHERE id=?", (status, digest, utc_now(), model_id)
        )
        updated = self.database.fetch_one("SELECT * FROM models WHERE id=?", (model_id,))
        assert updated is not None
        return self._serialize(updated)

    def download_file(
        self, model_id: str, filename: str, destination_root: Path,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        return self.download_files(model_id, [filename], destination_root, progress)

    def download_files(
        self, model_id: str, filenames: list[str], destination_root: Path,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM models WHERE id=?", (model_id,))
        if not row:
            raise KeyError("模型不存在")
        metadata = json_value(row, "metadata_json", {})
        repo = metadata.get("repo")
        if not repo:
            raise ValueError("模型条目缺少 Hugging Face repo")
        if not filenames:
            raise ValueError("没有可下载的模型文件")
        model_root = destination_root / model_id
        file_hashes: dict[str, str] = {}
        for file_index, filename in enumerate(filenames):
            destination = model_root / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_suffix(destination.suffix + ".part")
            existing = partial.stat().st_size if partial.exists() else 0
            headers = {"User-Agent": "Anima-LoRA-Studio/0.1"}
            if existing:
                headers["Range"] = f"bytes={existing}-"
            revision = str(metadata.get("revision") or "main")
            url = f"https://huggingface.co/{repo}/resolve/{revision}/{filename}"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length", "0")) + existing
                mode = "ab" if existing and response.status == 206 else "wb"
                if mode == "wb": existing = 0
                written = existing
                with partial.open(mode) as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        written += len(chunk)
                        if progress:
                            progress(file_index * max(total, 1) + written, len(filenames) * max(total, 1), filename)
            os.replace(partial, destination)
            file_hashes[filename] = sha256_file(destination)
        local_path = model_root if len(filenames) > 1 else model_root / filenames[0]
        digest = file_hashes[filenames[0]] if len(filenames) == 1 else None
        metadata["fileHashes"] = file_hashes
        self.database.execute(
            "UPDATE models SET local_path=?,sha256=?,status='ready',metadata_json=?,updated_at=? WHERE id=?",
            (str(local_path), digest, json.dumps(metadata, ensure_ascii=False), utc_now(), model_id),
        )
        updated = self.database.fetch_one("SELECT * FROM models WHERE id=?", (model_id,))
        assert updated is not None
        return self._serialize(updated)

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"], "kind": row["kind"], "name": row["name"], "source": row["source"],
            "localPath": row["local_path"], "sha256": row["sha256"], "status": row["status"],
            "metadata": json_value(row, "metadata_json", {}),
        }
