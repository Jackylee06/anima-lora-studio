from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from database import Database
from util import new_id, utc_now


EventEmitter = Callable[[str, dict[str, Any]], None]
Task = Callable[["JobContext"], dict[str, Any]]


@dataclass
class JobContext:
    job_id: str
    manager: "JobManager"
    cancel_event: threading.Event = field(default_factory=threading.Event)
    pause_event: threading.Event = field(default_factory=threading.Event)

    def progress(self, current: int, total: int, message: str, extra: dict[str, Any] | None = None) -> None:
        self.manager.update(self.job_id, current=current, total=total, message=message, extra=extra)


class JobManager:
    def __init__(self, database: Database, emit: EventEmitter):
        self.database = database
        self.emit = emit
        self.cpu_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="anima-cpu")
        self.gpu_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="anima-gpu")
        self.contexts: dict[str, JobContext] = {}
        self.futures: dict[str, Future[Any]] = {}
        self._lock = threading.RLock()

    def submit(self, kind: str, project_id: str | None, payload: dict[str, Any], task: Task, gpu: bool = False) -> dict[str, Any]:
        job_id = new_id("job")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO jobs(id,project_id,kind,state,payload_json,created_at,updated_at)
                VALUES(?,?,?,'queued',?,?,?)""",
                (job_id, project_id, kind, json.dumps(payload, ensure_ascii=False), now, now),
            )
        context = JobContext(job_id, self)
        with self._lock:
            self.contexts[job_id] = context
        job = self.get(job_id)
        self.emit("job.updated", job)
        with self._lock:
            executor = self.gpu_pool if gpu else self.cpu_pool
            self.futures[job_id] = executor.submit(self._run, context, task)
        return job

    def _run(self, context: JobContext, task: Task) -> None:
        self._set_state(context.job_id, "running", started_at=utc_now())
        try:
            result = task(context)
            state = str(result.get("state") or ("cancelled" if context.cancel_event.is_set() else "succeeded"))
            if state not in {"succeeded", "cancelled", "paused"}:
                state = "succeeded"
            self._set_state(context.job_id, state, result=result, finished_at=utc_now())
        except Exception as error:
            self._set_state(context.job_id, "failed", error=str(error), finished_at=utc_now())
        finally:
            with self._lock:
                self.contexts.pop(context.job_id, None)

    def update(
        self, job_id: str, *, current: int, total: int, message: str, extra: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        self.database.execute(
            "UPDATE jobs SET progress_current=?,progress_total=?,message=?,updated_at=? WHERE id=?",
            (current, total, message[-2000:], now, job_id),
        )
        job = self.get(job_id)
        if extra:
            job["extra"] = extra
        self.emit("job.updated", job)

    def _set_state(
        self, job_id: str, state: str, *, result: dict[str, Any] | None = None, error: str | None = None,
        started_at: str | None = None, finished_at: str | None = None,
    ) -> None:
        self.database.execute(
            """UPDATE jobs SET state=?,result_json=COALESCE(?,result_json),error=COALESCE(?,error),
            started_at=COALESCE(?,started_at),finished_at=COALESCE(?,finished_at),updated_at=? WHERE id=?""",
            (state, json.dumps(result, ensure_ascii=False) if result is not None else None, error,
             started_at, finished_at, utc_now(), job_id),
        )
        self.emit("job.updated", self.get(job_id))

    def get(self, job_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise KeyError("任务不存在")
        return self._serialize(row)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.database.fetch_all("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._serialize(row) for row in rows]

    def cancel(self, job_id: str) -> dict[str, Any]:
        context = self.contexts.get(job_id)
        if context:
            context.cancel_event.set()
            self._set_state(job_id, "cancelled" if self.futures[job_id].cancel() else "running")
        return self.get(job_id)

    def pause(self, job_id: str) -> dict[str, Any]:
        context = self.contexts.get(job_id)
        if not context:
            raise ValueError("任务当前不可暂停")
        context.pause_event.set()
        self._set_state(job_id, "pause_requested")
        return self.get(job_id)

    def has_active(self) -> bool:
        row = self.database.fetch_one(
            "SELECT COUNT(*) AS count FROM jobs WHERE state IN ('queued','running','pause_requested')"
        )
        return bool(row and row["count"])

    def shutdown(self) -> None:
        with self._lock:
            for context in self.contexts.values():
                context.cancel_event.set()
        self.cpu_pool.shutdown(wait=False, cancel_futures=True)
        self.gpu_pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return {
            "id": row["id"], "projectId": row["project_id"], "kind": row["kind"], "state": row["state"],
            "progressCurrent": row["progress_current"], "progressTotal": row["progress_total"],
            "message": row["message"], "result": result, "error": row["error"],
            "startedAt": row["started_at"], "finishedAt": row["finished_at"], "createdAt": row["created_at"],
        }
