from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

from database import Database
from jobs import JobManager


class _RaceExecutor:
    def __init__(self, running: threading.Event):
        self.running = running

    def submit(self, function, *args):
        future: Future[object] = Future()

        def run() -> None:
            try:
                future.set_result(function(*args))
            except BaseException as error:
                future.set_exception(error)

        threading.Thread(target=run, daemon=True).start()
        if not self.running.wait(timeout=2):
            raise TimeoutError("任务没有进入 running 状态")
        return future

    def shutdown(self, **_kwargs) -> None:
        return None


class JobEventTests(unittest.TestCase):
    def test_fast_job_events_never_regress_from_succeeded_to_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "project.sqlite3")
            database.initialize()
            running = threading.Event()
            release_task = threading.Event()
            succeeded = threading.Event()
            states: list[str] = []

            def emit(_event: str, job: dict) -> None:
                states.append(job["state"])
                if job["state"] == "running":
                    running.set()
                if job["state"] == "succeeded":
                    succeeded.set()

            manager = JobManager(database, emit)
            race_executor = _RaceExecutor(running)
            manager.cpu_pool = race_executor  # type: ignore[assignment]
            submitting_thread = threading.current_thread()
            original_get = manager.get

            def delayed_get(job_id: str) -> dict:
                job = original_get(job_id)
                if threading.current_thread() is submitting_thread and job["state"] == "running":
                    release_task.set()
                    self.assertTrue(succeeded.wait(timeout=2))
                return job

            def task(_context) -> dict:
                release_task.wait(timeout=0.25)
                return {"ok": True}

            with patch.object(manager, "get", side_effect=delayed_get):
                submitted = manager.submit("fast", None, {}, task)
                manager.futures[submitted["id"]].result(timeout=2)

            self.assertEqual(states[-1], "succeeded")
            self.assertEqual(states, ["queued", "running", "succeeded"])


if __name__ == "__main__":
    unittest.main()
