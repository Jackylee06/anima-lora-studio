from __future__ import annotations

import json
import sys
import threading
import traceback
from pathlib import Path
from typing import Any


WORKER_ROOT = Path(__file__).resolve().parent
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from service import WorkerService  # noqa: E402


write_lock = threading.Lock()


def write_message(value: dict[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    with write_lock:
        binary = getattr(sys.stdout, "buffer", None)
        if binary is not None:
            binary.write(payload)
            binary.flush()
        else:
            sys.stdout.write(payload.decode("utf-8"))
            sys.stdout.flush()


def emit(event: str, data: dict[str, Any]) -> None:
    write_message({"v": 1, "event": event, "data": data})


def run() -> int:
    service = WorkerService(emit)
    input_stream = getattr(sys.stdin, "buffer", sys.stdin)
    for raw_line in input_stream:
        line = raw_line.strip()
        if not line:
            continue
        request_id = "unknown"
        try:
            request = json.loads(line)
            request_id = str(request.get("id") or "unknown")
            if request.get("v") != 1:
                raise ValueError("不支持的 RPC 版本")
            method = str(request.get("method") or "")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("RPC params 必须是 object")
            result = service.dispatch(method, params)
            write_message({"v": 1, "id": request_id, "ok": True, "result": result})
            if service.shutdown_requested:
                return 0
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            write_message({
                "v": 1, "id": request_id, "ok": False,
                "error": {"code": type(error).__name__, "message": str(error)},
            })
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
