from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


class WD14Tagger:
    def __init__(self, model_path: Path, tags_path: Path, providers: list[str] | None = None):
        try:
            import numpy as np
            import onnxruntime as ort
            from PIL import Image
        except ImportError as error:
            raise RuntimeError("WD14 环境缺少 numpy、onnxruntime-gpu 或 Pillow") from error
        self.np = np
        self.Image = Image
        selected = providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
        available = set(ort.get_available_providers())
        selected = [item for item in selected if item in available] or ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(model_path), providers=selected)
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        self.image_size = int(shape[1] if isinstance(shape[1], int) else 448)
        with tags_path.open("r", encoding="utf-8") as handle:
            self.tags = list(csv.DictReader(handle))

    def _prepare(self, image_path: Path) -> Any:
        with self.Image.open(image_path) as image:
            image = image.convert("RGB")
            width, height = image.size
            side = max(width, height)
            canvas = self.Image.new("RGB", (side, side), "white")
            canvas.paste(image, ((side - width) // 2, (side - height) // 2))
            canvas = canvas.resize((self.image_size, self.image_size), self.Image.Resampling.LANCZOS)
            array = self.np.asarray(canvas, dtype=self.np.float32)
            array = array[:, :, ::-1]
            return self.np.expand_dims(array, axis=0)

    def tag(self, image_path: Path, general_threshold: float = 0.35, character_threshold: float = 0.85) -> dict[str, Any]:
        scores = self.session.run(None, {self.input_name: self._prepare(image_path)})[0][0]
        result: dict[str, list[dict[str, Any]]] = {"rating": [], "character": [], "general": [], "vector": []}
        for row, score in zip(self.tags, scores, strict=False):
            category = int(row.get("category", 0))
            name = row.get("name") or row.get("tag") or ""
            item = {"tag": name, "confidence": round(float(score), 6)}
            if category == 9:
                result["rating"].append(item)
            elif category == 4:
                result["vector"].append(item)
                if score >= character_threshold:
                    result["character"].append(item)
            elif category == 0:
                result["vector"].append(item)
                if score >= general_threshold:
                    result["general"].append(item)
        for key in result:
            result[key].sort(key=lambda item: item["confidence"], reverse=True)
        result["vector"] = result["vector"][:256]
        return result

    def close(self) -> None:
        self.session = None  # type: ignore[assignment]


def mock_tag(image_path: Path) -> dict[str, Any]:
    stem = image_path.stem.lower()
    subject = "1boy" if "boy" in stem else "1girl"
    return {
        "rating": [{"tag": "general", "confidence": 0.98}],
        "character": [],
        "general": [
            {"tag": subject, "confidence": 0.97},
            {"tag": "solo", "confidence": 0.91},
            {"tag": "looking_at_viewer", "confidence": 0.78},
            {"tag": "simple_background", "confidence": 0.66},
        ],
        "vector": [
            {"tag": subject, "confidence": 0.97},
            {"tag": "solo", "confidence": 0.91},
            {"tag": "looking_at_viewer", "confidence": 0.78},
            {"tag": "simple_background", "confidence": 0.66},
        ],
    }
