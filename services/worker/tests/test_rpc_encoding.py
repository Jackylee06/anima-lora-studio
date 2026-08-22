from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

import main as worker_main


class LegacyEncodedStdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, value: str) -> int:
        return self.buffer.write(value.encode("gbk"))

    def flush(self) -> None:
        return None


class RpcEncodingTests(unittest.TestCase):
    def test_rpc_output_is_utf8_even_when_windows_text_stream_uses_gbk(self) -> None:
        output = LegacyEncodedStdout()
        payload = {"v": 1, "id": "中文路径", "ok": True, "result": {"title": "测试图片"}}

        with patch.object(worker_main.sys, "stdout", output):
            worker_main.write_message(payload)

        raw = output.buffer.getvalue()
        self.assertEqual(json.loads(raw.decode("utf-8")), payload)


if __name__ == "__main__":
    unittest.main()
