from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

from constants import TORCH_CUDA_INDEX
from runtime import RuntimeManager, validate_torch_runtime


class RuntimeTests(unittest.TestCase):
    def test_accepts_only_validated_cu128_bf16_runtime(self) -> None:
        result = validate_torch_runtime({
            "version": "2.8.0+cu128", "cudaRuntime": "12.8", "cudaAvailable": True,
            "bf16Supported": True, "device": "NVIDIA GeForce RTX 4090 Laptop GPU",
        })
        self.assertTrue(result["validated"])
        self.assertEqual(result["index"], TORCH_CUDA_INDEX)

    def test_rejects_cpu_or_unavailable_cuda_runtime(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "构建错误"):
            validate_torch_runtime({
                "version": "2.8.0", "cudaRuntime": None, "cudaAvailable": False, "bf16Supported": False,
            })
        with self.assertRaisesRegex(RuntimeError, "CUDA 不可用"):
            validate_torch_runtime({
                "version": "2.8.0+cu128", "cudaRuntime": "12.8", "cudaAvailable": False,
                "bf16Supported": False,
            })

    def test_status_requires_a_validated_runtime_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / "resources"
            resources.mkdir()
            manager = RuntimeManager(root / "runtime", resources)
            caption_python = manager.environment_python("caption")
            caption_python.parent.mkdir(parents=True)
            caption_python.touch()
            self.assertFalse(manager.status()["environments"]["caption"]["ready"])
            (caption_python.parent.parent / "anima-runtime.json").write_text(json.dumps({
                "torch": {"version": "2.8.0+cu128", "cudaRuntime": "12.8", "validated": True},
            }), encoding="utf-8")
            status = manager.status()["environments"]["caption"]
            self.assertTrue(status["ready"])
            self.assertEqual(status["torch"]["version"], "2.8.0+cu128")


if __name__ == "__main__":
    unittest.main()
