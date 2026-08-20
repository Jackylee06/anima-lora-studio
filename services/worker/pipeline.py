from __future__ import annotations

import json
import queue
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from adapters.joycaption import mock_caption
from adapters.llm import CAPTION_SCHEMA, chat, mock_refine
from adapters.wd14 import mock_tag
from captions import ANIMA_RULES_VERSION, assemble_caption, build_refine_messages, fallback_from_wd, parse_llm_json
from database import Database, json_value
from util import config_hash, new_id, utc_now
from semantic import assign_semantic_groups


ProgressCallback = Callable[[int, int, str], None]


def _external_inference(
    project: dict[str, Any], params: dict[str, Any], stage: str, assets: list[Any], cancel: threading.Event,
) -> list[dict[str, Any]]:
    python = Path(str(params["captionPython"]))
    runner = Path(str(params["mlRunnerPath"]))
    if not python.is_file():
        raise RuntimeError("Caption 环境尚未安装，请先在设置中运行环境引导")
    if not runner.is_file():
        raise RuntimeError(f"推理入口缺失：{runner}")
    cache_root = Path(project["workspacePath"]) / "cache" / "jobs"
    cache_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{stage}-", dir=cache_root) as temporary:
        temp = Path(temporary)
        config_path = temp / "config.json"
        assets_path = temp / "assets.json"
        if stage == "wd14":
            config = {
                "modelPath": params["modelPath"], "tagsPath": params["tagsPath"],
                "providers": params.get("providers"), "generalThreshold": params["generalThreshold"],
                "characterThreshold": params["characterThreshold"],
            }
        else:
            config = {
                "modelId": params["modelId"], "cacheDir": params.get("cacheDir"),
                "precision": params["precision"], "revision": params.get("revision"), "prompt": params["prompt"],
                "maxTokens": params["maxTokens"],
            }
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        assets_path.write_text(
            json.dumps([{"id": row["id"], "path": row["source_path"]} for row in assets], ensure_ascii=False),
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [str(python), str(runner), stage, "--config", str(config_path), "--assets", str(assets_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        output: queue.Queue[str | None] = queue.Queue()
        errors: list[str] = []

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output.put(line)
            output.put(None)

        def read_errors() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                errors.append(line.rstrip())
                if len(errors) > 100:
                    del errors[:-100]

        threading.Thread(target=read_output, daemon=True).start()
        threading.Thread(target=read_errors, daemon=True).start()
        results: list[dict[str, Any]] = []
        ended = False
        while not ended:
            if cancel.is_set() and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
            try:
                line = output.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                ended = True
            else:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    errors.append(f"无效推理输出：{line.rstrip()}")
        code = process.wait()
        if not cancel.is_set() and code != 0:
            raise RuntimeError(f"{stage} 推理进程退出码 {code}\n" + "\n".join(errors[-20:]))
        return results


def _selected_assets(database: Database, project_id: str, asset_ids: list[str] | None) -> list[Any]:
    if asset_ids:
        placeholders = ",".join("?" for _ in asset_ids)
        return database.fetch_all(
            f"SELECT * FROM assets WHERE project_id=? AND eligible=1 AND id IN ({placeholders}) ORDER BY relative_path",
            (project_id, *asset_ids),
        )
    return database.fetch_all(
        "SELECT * FROM assets WHERE project_id=? AND eligible=1 AND review_state='kept' ORDER BY relative_path",
        (project_id,),
    )


def _cache_get(database: Database, asset_id: str, stage: str, model_id: str, digest: str) -> dict[str, Any] | None:
    row = database.fetch_one(
        "SELECT result_json FROM stage_results WHERE asset_id=? AND stage=? AND model_id=? AND config_hash=? AND status='succeeded'",
        (asset_id, stage, model_id, digest),
    )
    return json.loads(row["result_json"]) if row else None


def _stage_latest(database: Database, asset_id: str, stage: str) -> dict[str, Any] | None:
    row = database.fetch_one(
        "SELECT result_json FROM stage_results WHERE asset_id=? AND stage=? AND status='succeeded' ORDER BY updated_at DESC LIMIT 1",
        (asset_id, stage),
    )
    return json.loads(row["result_json"]) if row else None


def _cache_put(
    database: Database, project_id: str, asset_id: str, stage: str, model_id: str, digest: str,
    status: str, result: dict[str, Any], error: str | None = None,
) -> None:
    now = utc_now()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO stage_results(id,project_id,asset_id,stage,model_id,config_hash,status,result_json,error,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(asset_id,stage,model_id,config_hash) DO UPDATE SET
                status=excluded.status,result_json=excluded.result_json,error=excluded.error,updated_at=excluded.updated_at""",
            (new_id("stage"), project_id, asset_id, stage, model_id, digest, status,
             json.dumps(result, ensure_ascii=False), error, now, now),
        )


def run_wd14(
    database: Database, project: dict[str, Any], params: dict[str, Any], progress: ProgressCallback,
    cancel: threading.Event,
) -> dict[str, Any]:
    assets = _selected_assets(database, project["id"], params.get("assetIds"))
    model_id = str(params.get("modelId") or "SmilingWolf/wd-eva02-large-tagger-v3")
    general_threshold = float(params.get("generalThreshold", 0.35))
    character_threshold = float(params.get("characterThreshold", 0.85))
    base_config = {"model": model_id, "fingerprint": params.get("modelFingerprint"), "general": general_threshold, "character": character_threshold}
    processed = cached = failed = 0
    pending: list[Any] = []
    digests: dict[str, str] = {}
    by_id = {str(asset["id"]): asset for asset in assets}
    for asset in assets:
        digest = config_hash({**base_config, "sha256": asset["sha256"]})
        digests[str(asset["id"])] = digest
        if not params.get("force") and _cache_get(database, asset["id"], "wd14", model_id, digest):
            cached += 1
            progress(cached, len(assets), f"缓存：{asset['relative_path']}")
        else:
            pending.append(asset)
    if params.get("mock") or model_id == "mock":
        records = []
        for asset in pending:
            try:
                records.append({"assetId": asset["id"], "ok": True, "result": mock_tag(Path(asset["source_path"]))})
            except Exception as error:
                records.append({"assetId": asset["id"], "ok": False, "error": str(error)})
    else:
        records = _external_inference(project, params, "wd14", pending, cancel) if pending else []
    for record in records:
        asset = by_id.get(str(record.get("assetId")))
        if not asset:
            continue
        if record.get("ok"):
            _cache_put(database, project["id"], asset["id"], "wd14", model_id, digests[asset["id"]], "succeeded", record["result"])
            processed += 1
        else:
            _cache_put(database, project["id"], asset["id"], "wd14", model_id, digests[asset["id"]], "failed", {}, str(record.get("error") or "未知推理错误"))
            failed += 1
        progress(cached + processed + failed, len(assets), str(asset["relative_path"]))
    semantic_groups = 0 if cancel.is_set() else assign_semantic_groups(database, project["id"])
    return {"processed": processed, "cached": cached, "failed": failed, "semanticGroups": semantic_groups, "cancelled": cancel.is_set()}


def run_joycaption(
    database: Database, project: dict[str, Any], params: dict[str, Any], progress: ProgressCallback,
    cancel: threading.Event,
) -> dict[str, Any]:
    assets = _selected_assets(database, project["id"], params.get("assetIds"))
    model_id = str(params.get("modelId") or "fancyfeast/llama-joycaption-beta-one-hf-llava")
    precision = str(params.get("precision") or "nf4")
    prompt = str(params.get("prompt") or (
        "Write an objective, detailed two-sentence English description of visible subjects, clothing, pose, expression, "
        "objects, background and composition. Do not guess the artist, character identity, franchise, quality score or safety rating."
    ))
    base_config = {"model": model_id, "revision": params.get("revision"), "precision": precision, "prompt": prompt, "maxTokens": params.get("maxTokens", 320)}
    processed = cached = failed = 0
    pending: list[Any] = []
    digests: dict[str, str] = {}
    by_id = {str(asset["id"]): asset for asset in assets}
    for asset in assets:
        digest = config_hash({**base_config, "sha256": asset["sha256"]})
        digests[str(asset["id"])] = digest
        if not params.get("force") and _cache_get(database, asset["id"], "joycaption", model_id, digest):
            cached += 1
            progress(cached, len(assets), f"缓存：{asset['relative_path']}")
        else:
            pending.append(asset)
    if params.get("mock") or model_id == "mock":
        records = []
        for asset in pending:
            try:
                caption = mock_caption(Path(asset["source_path"]))
                records.append({"assetId": asset["id"], "ok": True, "result": {"caption": caption, "prompt": prompt}})
            except Exception as error:
                records.append({"assetId": asset["id"], "ok": False, "error": str(error)})
    else:
        external = dict(params)
        external.update({"modelId": model_id, "revision": params.get("revision"), "precision": precision, "prompt": prompt, "maxTokens": int(params.get("maxTokens", 320))})
        records = _external_inference(project, external, "joycaption", pending, cancel) if pending else []
    for record in records:
        asset = by_id.get(str(record.get("assetId")))
        if not asset:
            continue
        if record.get("ok"):
            _cache_put(database, project["id"], asset["id"], "joycaption", model_id, digests[asset["id"]], "succeeded", record["result"])
            processed += 1
        else:
            _cache_put(database, project["id"], asset["id"], "joycaption", model_id, digests[asset["id"]], "failed", {}, str(record.get("error") or "未知推理错误"))
            failed += 1
        progress(cached + processed + failed, len(assets), str(asset["relative_path"]))
    return {"processed": processed, "cached": cached, "failed": failed, "cancelled": cancel.is_set()}


def _insert_caption(
    database: Database, project: dict[str, Any], asset_id: str, sources: dict[str, Any],
    sections: dict[str, Any], text: str, status: str,
) -> int:
    row = database.fetch_one("SELECT MAX(revision) AS revision FROM caption_revisions WHERE asset_id=?", (asset_id,))
    revision = int(row["revision"] or 0) + 1
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO caption_revisions(
                id,project_id,asset_id,revision,profile,sources_json,sections_json,final_text,status,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (new_id("caption"), project["id"], asset_id, revision, project["profile"],
             json.dumps(sources, ensure_ascii=False), json.dumps(sections, ensure_ascii=False), text, status, utc_now()),
        )
    return revision


def run_refine(
    database: Database, project: dict[str, Any], params: dict[str, Any], progress: ProgressCallback,
    cancel: threading.Event,
) -> dict[str, Any]:
    assets = _selected_assets(database, project["id"], params.get("assetIds"))
    provider = dict(params.get("provider") or {"kind": "mock"})
    model_id = str(provider.get("model") or provider.get("kind") or "mock")
    mode = params.get("mode")
    effective_profile = str(project.get("settings", {}).get("baseProfile") or project["profile"]) if project["profile"] == "custom" else project["profile"]
    custom_instructions = str(project.get("settings", {}).get("customInstructions") or "")
    identity_omit = params.get("identityOmit") or []
    processed = fallback_count = failed = 0
    for index, asset in enumerate(assets, start=1):
        if cancel.is_set():
            break
        wd = _stage_latest(database, asset["id"], "wd14") or {"rating": [], "character": [], "general": []}
        joy_result = _stage_latest(database, asset["id"], "joycaption") or {}
        joy = str(joy_result.get("caption") or "")
        metadata = json_value(asset, "metadata_json", {})
        digest = config_hash({
            "rules": ANIMA_RULES_VERSION, "provider": {k: v for k, v in provider.items() if k != "apiKey"},
            "mode": mode, "trigger": project["trigger"], "identityOmit": identity_omit,
            "wd": wd, "joy": joy,
        })
        try:
            if params.get("mock") or provider.get("kind") == "mock":
                raw = mock_refine(effective_profile, project["trigger"], wd, joy)
            else:
                messages = build_refine_messages(
                    profile=effective_profile, trigger=project["trigger"], metadata=metadata, wd=wd,
                    joycaption=joy, identity_omit=identity_omit,
                    source_artist_names=[str(metadata.get("user", ""))], mode=mode, custom_instructions=custom_instructions,
                )
                error: Exception | None = None
                raw = None
                for _attempt in range(3):
                    try:
                        raw = parse_llm_json(chat(provider, messages, CAPTION_SCHEMA))
                        break
                    except Exception as caught:
                        error = caught
                        messages.append({"role": "user", "content": "Your previous response was invalid. Return only valid JSON matching the schema."})
                if raw is None:
                    raise error or RuntimeError("LLM refine failed")
            assembled = assemble_caption(raw, effective_profile, project["trigger"], mode)
            sources = {"wd14": wd, "joycaption": joy, "llmModel": model_id, "rulesVersion": ANIMA_RULES_VERSION}
            revision = _insert_caption(
                database, project, asset["id"], sources, assembled.sections, assembled.text, assembled.status
            )
            result = {"revision": revision, "text": assembled.text, "sections": assembled.sections, "status": assembled.status}
            _cache_put(database, project["id"], asset["id"], "llm_refine", model_id, digest, "succeeded", result)
            processed += 1
        except Exception as error:
            fallback = fallback_from_wd(wd, effective_profile, project["trigger"])
            sources = {"wd14": wd, "joycaption": joy, "llmModel": model_id, "error": str(error), "rulesVersion": ANIMA_RULES_VERSION}
            revision = _insert_caption(
                database, project, asset["id"], sources, fallback.sections, fallback.text, "needs_review"
            )
            _cache_put(
                database, project["id"], asset["id"], "llm_refine", model_id, digest, "failed",
                {"revision": revision, "text": fallback.text}, str(error),
            )
            fallback_count += 1
        progress(index, len(assets), str(asset["relative_path"]))
    return {"processed": processed, "fallback": fallback_count, "failed": failed, "cancelled": cancel.is_set()}
