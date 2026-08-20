from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from captions import ANIMA_RULES_VERSION, assemble_caption
from constants import APP_VERSION
from database import Database, json_value
from exporter import export_dataset
from jobs import JobContext, JobManager
from models import ModelRegistry
from naming import DEFAULT_TEMPLATE, TemplateError, compile_template, parse_with_source_root
from pipeline import run_joycaption, run_refine, run_wd14
from runtime import RuntimeManager, system_diagnostics
from scanner import asset_from_row, scan_project
from training import build_training_plan, probe_vram, run_training
from util import atomic_write_json, new_id, safe_slug, utc_now


Emit = Callable[[str, dict[str, Any]], None]


def _project_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"], "workspacePath": row["workspace_path"],
        "sourceRoot": row["source_root"], "pathTemplate": row["path_template"],
        "profile": row["profile"], "trigger": row["trigger"], "settings": json_value(row, "settings_json", {}),
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def _validate_trigger(profile: str, trigger: str) -> str:
    value = trigger.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,31}", value):
        raise ValueError("Trigger 必须是 3–32 位英文字母/数字，且以字母开头；不要使用空格或下划线")
    return value.lower()


class WorkerService:
    def __init__(self, emit: Emit):
        self.emit = emit
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "AnimaLoRAStudio"
        self.state_path = local / "worker-state.json"
        resources = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "resources"
        self.runtime = RuntimeManager(local / "runtime", resources)
        self.database: Database | None = None
        self.project: dict[str, Any] | None = None
        self.jobs: JobManager | None = None
        self.models: ModelRegistry | None = None
        self.shutdown_requested = False
        self._restore_last_project()

    def _restore_last_project(self) -> None:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            workspace = Path(state["lastProject"])
            if (workspace / "project.sqlite3").is_file():
                self._activate(workspace)
        except (OSError, KeyError, json.JSONDecodeError):
            return

    def _activate(self, workspace: Path) -> dict[str, Any]:
        workspace = workspace.resolve()
        if self.jobs and self.jobs.has_active():
            raise RuntimeError("当前仍有任务运行，不能切换项目")
        database = Database(workspace / "project.sqlite3")
        database.initialize()
        row = database.fetch_one("SELECT * FROM projects LIMIT 1")
        if not row:
            raise ValueError("选择的目录不是有效的 .alora 项目")
        database.execute(
            "UPDATE jobs SET state='failed',error='应用上次异常退出',finished_at=?,updated_at=? "
            "WHERE state IN ('queued','running','pause_requested')",
            (utc_now(), utc_now()),
        )
        if self.jobs:
            self.jobs.shutdown()
        self.database = database
        self.project = _project_from_row(row)
        self.jobs = JobManager(database, self.emit)
        self.models = ModelRegistry(database)
        self.models.seed()
        atomic_write_json(self.state_path, {"lastProject": str(workspace)})
        return self.project

    def _require_project(self) -> tuple[Database, dict[str, Any], JobManager]:
        if not self.database or not self.project or not self.jobs:
            raise RuntimeError("请先创建或打开项目")
        return self.database, self.project, self.jobs

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "system.ping": self.system_ping,
            "system.diagnostics": self.system_diagnostics,
            "system.shutdown": self.system_shutdown,
            "naming.validate": self.naming_validate,
            "naming.preview": self.naming_preview,
            "project.create": self.project_create,
            "project.open": self.project_open,
            "project.current": self.project_current,
            "project.update": self.project_update,
            "scan.start": self.scan_start,
            "assets.query": self.assets_query,
            "assets.setReview": self.assets_set_review,
            "assets.tagFrequency": self.assets_tag_frequency,
            "captions.list": self.captions_list,
            "captions.edit": self.captions_edit,
            "captions.setStatus": self.captions_set_status,
            "pipeline.wd14": self.pipeline_wd14,
            "pipeline.joycaption": self.pipeline_joycaption,
            "pipeline.refine": self.pipeline_refine,
            "export.start": self.export_start,
            "jobs.list": self.jobs_list,
            "jobs.get": self.jobs_get,
            "jobs.cancel": self.jobs_cancel,
            "jobs.pause": self.jobs_pause,
            "models.list": self.models_list,
            "models.register": self.models_register,
            "models.verify": self.models_verify,
            "models.download": self.models_download,
            "runtime.status": self.runtime_status,
            "runtime.bootstrap": self.runtime_bootstrap,
            "license.status": self.license_status,
            "license.accept": self.license_accept,
            "training.plan": self.training_plan,
            "training.start": self.training_start,
            "training.list": self.training_list,
            "training.samples": self.training_samples,
        }
        handler = handlers.get(method)
        if not handler:
            raise KeyError(f"未知 RPC 方法：{method}")
        return handler(params)

    def system_ping(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"version": APP_VERSION, "rpcVersion": 1, "ready": True, "projectOpen": self.project is not None}

    def system_diagnostics(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {**system_diagnostics(), "runtime": self.runtime.status(), "project": self.project}

    def system_shutdown(self, _params: dict[str, Any]) -> dict[str, Any]:
        if self.jobs:
            self.jobs.shutdown()
        self.shutdown_requested = True
        return {"ok": True}

    def naming_validate(self, params: dict[str, Any]) -> dict[str, Any]:
        compiled = compile_template(str(params.get("template") or DEFAULT_TEMPLATE))
        return {"valid": True, "template": compiled.template, "optionalSegments": compiled.optional_segments}

    def naming_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        compiled = compile_template(str(params.get("template") or DEFAULT_TEMPLATE))
        relative = str(params.get("path") or "")
        parsed = parse_with_source_root(compiled, str(params.get("sourceRootName") or "pixiv"), relative)
        return {"matched": parsed is not None, "metadata": parsed}

    def project_create(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "新项目").strip()
        source = Path(str(params.get("sourceRoot") or "")).resolve()
        if not source.is_dir():
            raise FileNotFoundError(f"Pixiv 源目录不存在：{source}")
        template = str(params.get("pathTemplate") or DEFAULT_TEMPLATE)
        compile_template(template)
        profile = str(params.get("profile") or "character")
        if profile not in {"character", "style", "custom"}:
            raise ValueError("未知项目类型")
        trigger = _validate_trigger(profile, str(params.get("trigger") or ""))
        if params.get("workspacePath"):
            workspace = Path(str(params["workspacePath"]))
        else:
            root = Path(str(params.get("workspaceRoot") or "")).resolve()
            workspace = root / f"{safe_slug(name)}.alora"
        workspace.mkdir(parents=True, exist_ok=False)
        database = Database(workspace / "project.sqlite3")
        database.initialize()
        project_id, now = new_id("project"), utc_now()
        settings = dict(params.get("settings") or {})
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO projects(id,name,workspace_path,source_root,path_template,profile,trigger,settings_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (project_id, name, str(workspace.resolve()), str(source), template, profile, trigger,
                 json.dumps(settings, ensure_ascii=False), now, now),
            )
        atomic_write_json(workspace / "project.json", {
            "schemaVersion": 1, "id": project_id, "name": name, "database": "project.sqlite3", "createdAt": now,
        })
        return self._activate(workspace)

    def project_open(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(params.get("workspacePath") or "")).resolve()
        if path.is_file():
            path = path.parent
        return self._activate(path)

    def project_current(self, _params: dict[str, Any]) -> dict[str, Any] | None:
        return self.project

    def project_update(self, params: dict[str, Any]) -> dict[str, Any]:
        database, project, _jobs = self._require_project()
        name = str(params.get("name", project["name"]))
        template = str(params.get("pathTemplate", project["pathTemplate"]))
        profile = str(params.get("profile", project["profile"]))
        trigger = _validate_trigger(profile, str(params.get("trigger", project["trigger"])))
        compile_template(template)
        settings = {**project["settings"], **dict(params.get("settings") or {})}
        database.execute(
            "UPDATE projects SET name=?,path_template=?,profile=?,trigger=?,settings_json=?,updated_at=? WHERE id=?",
            (name, template, profile, trigger, json.dumps(settings, ensure_ascii=False), utc_now(), project["id"]),
        )
        row = database.fetch_one("SELECT * FROM projects WHERE id=?", (project["id"],))
        assert row is not None
        self.project = _project_from_row(row)
        return self.project

    def scan_start(self, _params: dict[str, Any]) -> dict[str, Any]:
        database, project, jobs = self._require_project()
        compiled = compile_template(project["pathTemplate"])
        return jobs.submit("scan", project["id"], {}, lambda context: scan_project(
            database, project, compiled, lambda current, total, message: context.progress(current, total, message)
        ))

    def assets_query(self, params: dict[str, Any]) -> dict[str, Any]:
        database, project, _jobs = self._require_project()
        clauses = ["project_id=?"]
        values: list[Any] = [project["id"]]
        state = params.get("reviewState")
        if state and state != "all":
            clauses.append("review_state=?")
            values.append(state)
        if "eligible" in params and params["eligible"] is not None:
            clauses.append("eligible=?")
            values.append(int(bool(params["eligible"])))
        if params.get("age"):
            clauses.append("json_extract(metadata_json,'$.age')=?")
            values.append(params["age"])
        if params.get("ai"):
            if params["ai"] == "AI": clauses.append("json_extract(metadata_json,'$.AI')='AI'")
            else: clauses.append("json_extract(metadata_json,'$.AI') IS NULL")
        if params.get("search"):
            clauses.append("(relative_path LIKE ? OR metadata_json LIKE ?)")
            term = f"%{params['search']}%"
            values.extend([term, term])
        where = " AND ".join(clauses)
        order = {
            "score_desc": "technical_score DESC, relative_path ASC",
            "updated_desc": "updated_at DESC",
            "path": "relative_path ASC",
        }.get(params.get("sort"), "technical_score DESC, relative_path ASC")
        limit = max(1, min(500, int(params.get("limit", 200))))
        offset = max(0, int(params.get("offset", 0)))
        rows = database.fetch_all(f"SELECT * FROM assets WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*values, limit, offset))
        total_row = database.fetch_one(f"SELECT COUNT(*) AS count FROM assets WHERE {where}", values)
        counts_rows = database.fetch_all(
            "SELECT review_state,COUNT(*) AS count FROM assets WHERE project_id=? GROUP BY review_state", (project["id"],)
        )
        counts = {"pending": 0, "kept": 0, "rejected": 0}
        counts.update({row["review_state"]: row["count"] for row in counts_rows})
        return {"items": [asset_from_row(database, row) for row in rows], "total": total_row["count"] if total_row else 0, "counts": counts}

    def assets_set_review(self, params: dict[str, Any]) -> dict[str, Any]:
        database, project, _jobs = self._require_project()
        ids = [str(item) for item in params.get("assetIds") or []]
        state = str(params.get("reviewState"))
        if not ids or state not in {"pending", "kept", "rejected"}:
            raise ValueError("assetIds 或 reviewState 无效")
        placeholders = ",".join("?" for _ in ids)
        count = database.execute(
            f"UPDATE assets SET review_state=?,updated_at=? WHERE project_id=? AND id IN ({placeholders})",
            (state, utc_now(), project["id"], *ids),
        )
        return {"updated": count, "reviewState": state}

    def assets_tag_frequency(self, _params: dict[str, Any]) -> dict[str, Any]:
        database, project, _jobs = self._require_project()
        assets = database.fetch_all(
            "SELECT id FROM assets WHERE project_id=? AND eligible=1 AND review_state='kept'", (project["id"],)
        )
        frequencies: Counter[str] = Counter()
        for asset in assets:
            row = database.fetch_one(
                "SELECT result_json FROM stage_results WHERE asset_id=? AND stage='wd14' AND status='succeeded' ORDER BY updated_at DESC LIMIT 1",
                (asset["id"],),
            )
            if not row: continue
            result = json.loads(row["result_json"])
            tags = [item["tag"] for key in ("character", "general") for item in result.get(key, []) if isinstance(item, dict)]
            frequencies.update(set(tags))
        total = len(assets)
        items = [
            {"tag": tag, "count": count, "ratio": round(count / total, 4) if total else 0, "identityCandidate": total > 0 and count / total >= 0.8}
            for tag, count in frequencies.most_common(200)
        ]
        return {"imageCount": total, "items": items}

    def captions_list(self, params: dict[str, Any]) -> dict[str, Any]:
        database, project, _jobs = self._require_project()
        limit = max(1, min(500, int(params.get("limit", 200))))
        rows = database.fetch_all(
            """SELECT c.*,a.relative_path,a.thumbnail_path FROM caption_revisions c
            JOIN assets a ON a.id=c.asset_id
            JOIN (SELECT asset_id,MAX(revision) AS revision FROM caption_revisions GROUP BY asset_id) latest
            ON latest.asset_id=c.asset_id AND latest.revision=c.revision
            WHERE c.project_id=? ORDER BY a.relative_path LIMIT ?""",
            (project["id"], limit),
        )
        return {"items": [self._caption_row(row) for row in rows], "total": len(rows)}

    @staticmethod
    def _caption_row(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"], "assetId": row["asset_id"], "revision": row["revision"], "profile": row["profile"],
            "sources": json_value(row, "sources_json", {}), "sections": json_value(row, "sections_json", {}),
            "finalText": row["final_text"], "status": row["status"], "createdAt": row["created_at"],
            "relativePath": row["relative_path"] if "relative_path" in row.keys() else None,
            "thumbnailPath": row["thumbnail_path"] if "thumbnail_path" in row.keys() else None,
        }

    def captions_edit(self, params: dict[str, Any]) -> dict[str, Any]:
        database, project, _jobs = self._require_project()
        asset_id, text = str(params["assetId"]), str(params["finalText"]).strip()
        if not text: raise ValueError("Caption 不能为空")
        latest = database.fetch_one("SELECT * FROM caption_revisions WHERE asset_id=? ORDER BY revision DESC LIMIT 1", (asset_id,))
        revision = int(latest["revision"] if latest else 0) + 1
        sections = json_value(latest, "sections_json", {}) if latest else {}
        sources = json_value(latest, "sources_json", {}) if latest else {"manual": True}
        status = str(params.get("status") or "approved")
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO caption_revisions(id,project_id,asset_id,revision,profile,sources_json,sections_json,final_text,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (new_id("caption"), project["id"], asset_id, revision, project["profile"], json.dumps(sources, ensure_ascii=False),
                 json.dumps(sections, ensure_ascii=False), text, status, utc_now()),
            )
        return {"assetId": asset_id, "revision": revision, "status": status}

    def captions_set_status(self, params: dict[str, Any]) -> dict[str, Any]:
        database, _project, _jobs = self._require_project()
        ids = [str(item) for item in params.get("assetIds") or []]
        status = str(params.get("status") or "approved")
        if status not in {"draft", "needs_review", "approved"}: raise ValueError("Caption 状态无效")
        updated = 0
        with database.transaction() as connection:
            for asset_id in ids:
                row = connection.execute(
                    "SELECT id FROM caption_revisions WHERE asset_id=? ORDER BY revision DESC LIMIT 1", (asset_id,)
                ).fetchone()
                if row:
                    updated += connection.execute("UPDATE caption_revisions SET status=? WHERE id=?", (status, row["id"])).rowcount
        return {"updated": updated, "status": status}

    def pipeline_wd14(self, params: dict[str, Any]) -> dict[str, Any]:
        database, project, jobs = self._require_project()
        effective = dict(params)
        if not effective.get("mock") and effective.get("modelId") != "mock":
            effective["captionPython"] = str(self.runtime.environment_python("caption"))
            effective["mlRunnerPath"] = str(self.runtime.inference_runner)
        return jobs.submit("wd14", project["id"], effective, lambda context: run_wd14(
            database, project, effective, lambda c, t, m: context.progress(c, t, m), context.cancel_event
        ), gpu=True)

    def pipeline_joycaption(self, params: dict[str, Any]) -> dict[str, Any]:
        database, project, jobs = self._require_project()
        effective = dict(params)
        if not effective.get("mock") and effective.get("modelId") != "mock":
            effective["captionPython"] = str(self.runtime.environment_python("caption"))
            effective["mlRunnerPath"] = str(self.runtime.inference_runner)
        return jobs.submit("joycaption", project["id"], effective, lambda context: run_joycaption(
            database, project, effective, lambda c, t, m: context.progress(c, t, m), context.cancel_event
        ), gpu=True)

    def pipeline_refine(self, params: dict[str, Any]) -> dict[str, Any]:
        database, project, jobs = self._require_project()
        return jobs.submit("llm_refine", project["id"], {k: v for k, v in params.items() if k != "provider"}, lambda context: run_refine(
            database, project, params, lambda c, t, m: context.progress(c, t, m), context.cancel_event
        ), gpu=False)

    def export_start(self, params: dict[str, Any]) -> dict[str, Any]:
        database, project, jobs = self._require_project()
        return jobs.submit("export", project["id"], params, lambda context: export_dataset(
            database, project, params, lambda c, t, m: context.progress(c, t, m)
        ))

    def jobs_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        _database, _project, jobs = self._require_project()
        return jobs.list(int(params.get("limit", 100)))

    def jobs_get(self, params: dict[str, Any]) -> dict[str, Any]:
        _database, _project, jobs = self._require_project()
        return jobs.get(str(params["jobId"]))

    def jobs_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        _database, _project, jobs = self._require_project()
        return jobs.cancel(str(params["jobId"]))

    def jobs_pause(self, params: dict[str, Any]) -> dict[str, Any]:
        _database, _project, jobs = self._require_project()
        return jobs.pause(str(params["jobId"]))

    def models_list(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        self._require_project()
        assert self.models
        return self.models.list()

    def models_register(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_project(); assert self.models
        return self.models.register(params)

    def models_verify(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_project(); assert self.models
        return self.models.verify(str(params["modelId"]))

    def models_download(self, params: dict[str, Any]) -> dict[str, Any]:
        _database, project, jobs = self._require_project(); assert self.models
        model = next((item for item in self.models.list() if item["id"] == str(params["modelId"])), None)
        if not model:
            raise KeyError("模型不存在")
        if model["kind"] in {"anima_base", "qwen3", "vae"}:
            accepted = _database.fetch_one(
                "SELECT 1 FROM license_acceptances WHERE license_id='circlestone-anima-non-commercial'"
            )
            if not accepted:
                raise PermissionError("下载 Anima 权重前，请先在训练页阅读并接受官方非商业许可")
        root = Path(str(params.get("destinationRoot") or Path(project["workspacePath"]) / "models"))
        filenames = [str(item) for item in params.get("filenames") or [params.get("filename")] if item]
        return jobs.submit("model_download", project["id"], params, lambda context: self.models.download_files(
            str(params["modelId"]), filenames, root,
            lambda c, t, m: context.progress(c, t, m),
        ))

    def runtime_status(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.status()

    def runtime_bootstrap(self, params: dict[str, Any]) -> dict[str, Any]:
        _database, project, jobs = self._require_project()
        name = str(params.get("name") or "caption")
        requirements = self.runtime.resources / f"requirements-{name}.txt"
        return jobs.submit("runtime_bootstrap", project["id"], {"name": name}, lambda context: self.runtime.bootstrap(
            name, requirements, lambda c, t, m: context.progress(c, t, m)
        ))

    def license_status(self, _params: dict[str, Any]) -> dict[str, Any]:
        database, _project, _jobs = self._require_project()
        row = database.fetch_one("SELECT * FROM license_acceptances WHERE license_id='circlestone-anima-non-commercial'")
        return {"accepted": row is not None, "acceptedAt": row["accepted_at"] if row else None}

    def license_accept(self, params: dict[str, Any]) -> dict[str, Any]:
        database, _project, _jobs = self._require_project()
        if not params.get("accepted"): raise ValueError("必须明确接受许可后才能继续")
        now = utc_now()
        database.execute(
            "INSERT INTO license_acceptances(license_id,accepted_at,metadata_json) VALUES(?,?,?) "
            "ON CONFLICT(license_id) DO UPDATE SET accepted_at=excluded.accepted_at,metadata_json=excluded.metadata_json",
            ("circlestone-anima-non-commercial", now, json.dumps({"source": "https://huggingface.co/circlestone-labs/Anima"})),
        )
        return {"accepted": True, "acceptedAt": now}

    def training_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        database, _project, _jobs = self._require_project()
        accepted = database.fetch_one("SELECT 1 FROM license_acceptances WHERE license_id='circlestone-anima-non-commercial'")
        if not accepted: raise PermissionError("请先阅读并接受 Anima 模型许可")
        return build_training_plan(params)

    def training_start(self, params: dict[str, Any]) -> dict[str, Any]:
        database, project, jobs = self._require_project()
        accepted = database.fetch_one("SELECT 1 FROM license_acceptances WHERE license_id='circlestone-anima-non-commercial'")
        if not accepted: raise PermissionError("请先阅读并接受 Anima 模型许可")
        plan = build_training_plan(params)

        def task(context: JobContext) -> dict[str, Any]:
            now = utc_now()
            with database.transaction() as connection:
                connection.execute(
                    """INSERT INTO training_runs(id,project_id,job_id,state,export_path,output_path,config_json,command_json,
                    total_steps,started_at,created_at,updated_at) VALUES(?,?,?,'running',?,?,?,?,?,?,?,?)""",
                    (plan["runId"], project["id"], context.job_id, params["exportPath"], plan["outputPath"],
                     json.dumps(plan["config"], ensure_ascii=False), json.dumps(plan["command"], ensure_ascii=False),
                     plan["totalSteps"], now, now, now),
                )

            def report(current: int, total: int, message: str, extra: dict[str, Any]) -> None:
                database.execute(
                    "UPDATE training_runs SET current_step=?,latest_loss=?,updated_at=? WHERE id=?",
                    (current, extra.get("loss"), utc_now(), plan["runId"]),
                )
                context.progress(current, total, message, extra)

            try:
                probe_vram(
                    str(params["trainerPython"]),
                    lambda probe, message: context.progress(0, plan["totalSteps"], message, {"probe": probe}),
                )
                result = run_training(plan, report, context.cancel_event, context.pause_event)
                database.execute(
                    """UPDATE training_runs SET state=?,current_step=?,latest_loss=?,latest_checkpoint=?,
                    finished_at=?,updated_at=? WHERE id=?""",
                    (result["state"], result.get("step", 0), result.get("loss"), result.get("latestCheckpoint"),
                     utc_now() if result["state"] != "paused" else None, utc_now(), plan["runId"]),
                )
                return result
            except Exception:
                database.execute(
                    "UPDATE training_runs SET state='failed',finished_at=?,updated_at=? WHERE id=?",
                    (utc_now(), utc_now(), plan["runId"]),
                )
                raise

        return jobs.submit("training", project["id"], {"runId": plan["runId"], "outputPath": plan["outputPath"]}, task, gpu=True)

    def training_list(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        database, project, _jobs = self._require_project()
        rows = database.fetch_all("SELECT * FROM training_runs WHERE project_id=? ORDER BY created_at DESC", (project["id"],))
        return [{
            "id": row["id"], "state": row["state"], "exportPath": row["export_path"], "outputPath": row["output_path"],
            "config": json_value(row, "config_json", {}), "command": json_value(row, "command_json", []),
            "currentStep": row["current_step"], "totalSteps": row["total_steps"], "latestLoss": row["latest_loss"],
            "latestCheckpoint": row["latest_checkpoint"], "startedAt": row["started_at"], "finishedAt": row["finished_at"],
        } for row in rows]

    def training_samples(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        database, project, _jobs = self._require_project()
        run_id = str(params.get("runId") or "")
        if run_id:
            row = database.fetch_one(
                "SELECT id,output_path FROM training_runs WHERE id=? AND project_id=?", (run_id, project["id"])
            )
        else:
            row = database.fetch_one(
                "SELECT id,output_path FROM training_runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
                (project["id"],),
            )
        if not row:
            return []
        output = Path(row["output_path"])
        if not output.is_dir():
            return []
        samples = []
        for path in output.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                stat = path.stat()
                samples.append({
                    "runId": row["id"], "path": str(path), "name": path.name,
                    "relativePath": path.relative_to(output).as_posix(), "modifiedAt": stat.st_mtime,
                })
        return sorted(samples, key=lambda item: (item["modifiedAt"], item["relativePath"]), reverse=True)[:80]
