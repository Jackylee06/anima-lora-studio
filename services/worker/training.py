from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from util import atomic_write_json, new_id, safe_slug, utc_now
from constants import PINNED_SD_SCRIPTS_COMMIT


OFFICIAL_NEGATIVE = "worst quality, low quality, score_1, score_2, score_3, artist name, blurry, jpeg artifacts, chromatic aberration"
OFFICIAL_POSITIVE = "masterpiece, best quality, score_7, safe"


def validate_training_inputs(params: dict[str, Any]) -> dict[str, Any]:
    required = {
        "trainerPython": "trainer Python",
        "engineRoot": "sd-scripts 目录",
        "exportPath": "训练集导出目录",
        "animaBasePath": "Anima Base v1.0",
        "qwen3Path": "Qwen3 0.6B",
        "vaePath": "Qwen Image VAE",
    }
    missing: list[str] = []
    for key, label in required.items():
        value = Path(str(params.get(key) or ""))
        if not value.exists():
            missing.append(f"{label}: {value}")
    engine = Path(str(params.get("engineRoot") or ""))
    script = engine / "anima_train_network.py"
    if engine.exists() and not script.is_file():
        missing.append(f"训练脚本: {script}")
    export = Path(str(params.get("exportPath") or ""))
    dataset = export / "dataset.toml"
    manifest = export / "manifest.json"
    if export.exists() and not dataset.is_file():
        missing.append(f"dataset.toml: {dataset}")
    if export.exists() and not manifest.is_file():
        missing.append(f"manifest.json: {manifest}")
    if missing:
        raise ValueError("训练前检失败：\n" + "\n".join(missing))
    marker = engine / ".anima-studio-commit"
    actual_commit = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    if not actual_commit and (engine / ".git").exists():
        completed = subprocess.run(
            ["git", "-C", str(engine), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20, check=False,
        )
        if completed.returncode == 0:
            actual_commit = completed.stdout.strip()
    if actual_commit != PINNED_SD_SCRIPTS_COMMIT:
        raise ValueError(
            f"sd-scripts 版本不匹配：需要 {PINNED_SD_SCRIPTS_COMMIT}，当前 {actual_commit or '无法识别'}。"
            "请使用设置页安装的固定训练环境，或将本地仓库 checkout 到该 commit。"
        )
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    return {"script": script, "dataset": dataset, "manifest": manifest_data}


def validate_help_compatibility(python: str, script: Path, advanced_args: dict[str, Any]) -> None:
    completed = subprocess.run(
        [python, str(script), "--help"], capture_output=True, text=True, timeout=120, check=False,
    )
    help_text = completed.stdout + "\n" + completed.stderr
    if completed.returncode != 0:
        raise RuntimeError("无法读取 anima_train_network.py --help：\n" + "\n".join(help_text.splitlines()[-20:]))
    required = {
        "network_train_unet_only", "qwen3", "vae", "timestep_sampling", "weighting_scheme", "loss_type",
        *(str(key).lstrip("-") for key in advanced_args),
    }
    missing = sorted(name for name in required if f"--{name}" not in help_text)
    if missing:
        raise ValueError("固定训练后端不支持这些参数：" + ", ".join(f"--{name}" for name in missing))


def probe_vram(trainer_python: str, report: Callable[[int, str], None]) -> None:
    for index, mib in enumerate((1024, 2048), start=1):
        report(index, f"显存探测 {index}/2：临时分配 {mib} MiB")
        script = (
            "import torch; "
            "assert torch.cuda.is_available(), 'CUDA unavailable'; "
            f"x=torch.empty(({mib}*1024*1024//2,),dtype=torch.float16,device='cuda'); "
            "x.fill_(0); torch.cuda.synchronize(); del x; torch.cuda.empty_cache()"
        )
        completed = subprocess.run(
            [trainer_python, "-c", script], capture_output=True, text=True, timeout=120, check=False,
        )
        if completed.returncode != 0:
            detail = "\n".join((completed.stdout + completed.stderr).splitlines()[-12:])
            raise RuntimeError(
                f"第 {index} 步显存探测失败。训练参数未被修改；请降低最大分辨率或显式启用 block swap。\n{detail}"
            )


def _sample_prompts(profile: str, trigger: str, seed: int) -> list[dict[str, Any]]:
    if profile == "style":
        positive = [
            f"{OFFICIAL_POSITIVE}, @{trigger.lstrip('@')}, 1girl, dynamic pose, city background",
            f"{OFFICIAL_POSITIVE}, @{trigger.lstrip('@')}, 1boy, fantasy armor, forest, dramatic lighting",
            f"{OFFICIAL_POSITIVE}, @{trigger.lstrip('@')}, landscape, architecture, no humans",
            f"{OFFICIAL_POSITIVE}, 1girl, dynamic pose, city background",
        ]
    else:
        positive = [
            f"{OFFICIAL_POSITIVE}, 1girl, {trigger}, portrait, looking at viewer, simple background",
            f"{OFFICIAL_POSITIVE}, 1girl, {trigger}, casual clothes, walking in a city, full body",
            f"{OFFICIAL_POSITIVE}, 1girl, {trigger}, fantasy outfit, action pose, detailed background",
            f"{OFFICIAL_POSITIVE}, 1girl, portrait, looking at viewer, simple background",
        ]
    return [
        {"prompt": prompt, "negative_prompt": OFFICIAL_NEGATIVE, "seed": seed + index, "width": 768, "height": 1024}
        for index, prompt in enumerate(positive)
    ]


def _flag(name: str) -> str:
    return name if name.startswith("--") else f"--{name}"


def build_training_plan(params: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    checked = validate_training_inputs(params)
    manifest = checked["manifest"]
    config = dict(manifest.get("trainingConfig") or {})
    config.update(params.get("trainingConfig") or {})
    validate_help_compatibility(str(params["trainerPython"]), checked["script"], dict(config.get("advancedArgs") or {}))
    profile = str(manifest["project"].get("profileBase") or manifest["project"]["profile"])
    trigger = str(manifest["project"]["trigger"])
    run_id = run_id or new_id("train")
    output_root = Path(str(params.get("outputRoot") or Path(params["exportPath"]) / "training-runs"))
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts_path = output_dir / "sample-prompts.json"
    atomic_write_json(prompts_path, _sample_prompts(profile, trigger, int(config.get("seed", 42))))
    output_name = safe_slug(str(params.get("outputName") or manifest["project"]["name"]), "anima-lora")
    max_steps = int(config.get("maxTrainSteps", 600))
    warmup = max(1, round(max_steps * 0.05))
    arguments: dict[str, Any] = {
        "pretrained_model_name_or_path": params["animaBasePath"],
        "qwen3": params["qwen3Path"],
        "vae": params["vaePath"],
        "dataset_config": str(checked["dataset"]),
        "output_dir": str(output_dir),
        "output_name": output_name,
        "save_model_as": "safetensors",
        "network_module": "networks.lora_anima",
        "network_dim": int(config.get("networkDim", 32)),
        "network_alpha": int(config.get("networkAlpha", 32)),
        "learning_rate": float(config.get("learningRate", 2e-5)),
        "optimizer_type": "AdamW8bit",
        "lr_scheduler": "constant_with_warmup",
        "lr_warmup_steps": warmup,
        "max_grad_norm": 1.0,
        "timestep_sampling": "sigmoid",
        "weighting_scheme": "uniform",
        "loss_type": "l2",
        "max_train_steps": max_steps,
        "gradient_accumulation_steps": int(config.get("gradientAccumulationSteps", 4)),
        "mixed_precision": "bf16",
        "save_precision": "bf16",
        "seed": int(config.get("seed", 42)),
        "save_every_n_steps": int(config.get("saveEverySteps", max(100, max_steps // 5))),
        "sample_every_n_steps": int(config.get("sampleEverySteps", max(100, max_steps // 5))),
        "sample_prompts": str(prompts_path),
        "vae_chunk_size": 64,
    }
    switches = {
        "network_train_unet_only", "gradient_checkpointing", "cache_latents", "cache_latents_to_disk",
        "cache_text_encoder_outputs", "cache_text_encoder_outputs_to_disk", "vae_disable_cache",
        "save_state", "save_state_on_train_end",
    }
    for key, value in dict(config.get("advancedArgs") or {}).items():
        normalized = str(key).lstrip("-")
        if normalized in {
            "pretrained_model_name_or_path", "qwen3", "vae", "dataset_config", "output_dir", "output_name",
            "network_module", "train_llm_adapter",
        }:
            raise ValueError(f"高级参数不得覆盖受保护选项：{key}")
        if isinstance(value, bool):
            if value: switches.add(normalized)
            else: switches.discard(normalized)
        else:
            arguments[normalized] = value
    command = [
        str(params["trainerPython"]), "-m", "accelerate.commands.launch", "--num_cpu_threads_per_process", "1",
        str(checked["script"]),
    ]
    for name, value in arguments.items():
        command.extend([_flag(name), str(value)])
    for name in sorted(switches):
        command.append(_flag(name))
    if params.get("resumePath"):
        command.extend(["--resume", str(params["resumePath"])])
    return {
        "runId": run_id, "command": command, "cwd": str(Path(params["engineRoot"]).resolve()),
        "outputPath": str(output_dir), "outputName": output_name, "config": config,
        "totalSteps": max_steps, "samplePromptsPath": str(prompts_path),
    }


STEP_RE = re.compile(r"(?:steps?|global_step)[ =:/]+(?P<step>\d+)", re.IGNORECASE)
LOSS_RE = re.compile(r"loss[=: ]+(?P<loss>\d+(?:\.\d+)?(?:e[-+]?\d+)?)", re.IGNORECASE)


def _latest_checkpoint(output_path: Path) -> str | None:
    candidates = list(output_path.glob("*.safetensors")) + list(output_path.glob("*-state"))
    if not candidates:
        return None
    return str(max(candidates, key=lambda item: item.stat().st_mtime_ns))


def _stop_process(process: subprocess.Popen[str], graceful: bool = True) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt" and graceful:
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=15)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def run_training(
    plan: dict[str, Any],
    progress: Callable[[int, int, str, dict[str, Any]], None],
    cancel: threading.Event,
    pause: threading.Event,
) -> dict[str, Any]:
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        plan["command"], cwd=plan["cwd"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=creation_flags,
    )
    lines: list[str] = []
    current_step = 0
    latest_loss: float | None = None
    try:
        assert process.stdout is not None
        while True:
            line = process.stdout.readline()
            if line:
                clean = line.rstrip()
                lines.append(clean)
                if len(lines) > 1000:
                    lines = lines[-1000:]
                step_match = STEP_RE.search(clean)
                loss_match = LOSS_RE.search(clean)
                if step_match:
                    current_step = max(current_step, int(step_match.group("step")))
                if loss_match:
                    latest_loss = float(loss_match.group("loss"))
                progress(current_step, int(plan["totalSteps"]), clean, {"loss": latest_loss, "pid": process.pid})
                if pause.is_set() and re.search(r"sav(?:e|ing)|checkpoint|state", clean, re.IGNORECASE):
                    _stop_process(process)
                    return {
                        "state": "paused", "step": current_step, "loss": latest_loss,
                        "latestCheckpoint": _latest_checkpoint(Path(plan["outputPath"])), "logs": lines[-200:],
                    }
            if cancel.is_set():
                _stop_process(process, graceful=False)
                return {"state": "cancelled", "step": current_step, "loss": latest_loss, "logs": lines[-200:]}
            code = process.poll()
            if code is not None:
                if code != 0:
                    raise RuntimeError(f"训练进程退出码 {code}\n" + "\n".join(lines[-40:]))
                break
            if not line:
                time.sleep(0.05)
    finally:
        if process.poll() is None:
            _stop_process(process, graceful=False)
    return {
        "state": "succeeded", "step": max(current_step, int(plan["totalSteps"])), "loss": latest_loss,
        "latestCheckpoint": _latest_checkpoint(Path(plan["outputPath"])), "logs": lines[-200:],
    }
