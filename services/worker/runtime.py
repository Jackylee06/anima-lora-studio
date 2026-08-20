from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from util import atomic_write_json, sha256_file, utc_now
from constants import (
    PINNED_SD_SCRIPTS_COMMIT,
    TORCH_CUDA_INDEX,
    TORCH_CUDA_RUNTIME,
    TORCH_VERSION,
    TORCHVISION_VERSION,
)


UV_WINDOWS_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"


def validate_torch_runtime(details: dict[str, Any]) -> dict[str, Any]:
    version = str(details.get("version") or "")
    cuda_runtime = str(details.get("cudaRuntime") or "")
    if not version.startswith(f"{TORCH_VERSION}+cu128"):
        raise RuntimeError(f"Torch 构建错误：需要 {TORCH_VERSION}+cu128，实际为 {version or 'unknown'}")
    if cuda_runtime != TORCH_CUDA_RUNTIME:
        raise RuntimeError(f"Torch CUDA Runtime 错误：需要 {TORCH_CUDA_RUNTIME}，实际为 {cuda_runtime or 'none'}")
    if not details.get("cudaAvailable"):
        raise RuntimeError("Torch CUDA 不可用；请检查 NVIDIA 驱动，环境不会标记为就绪")
    if not details.get("bf16Supported"):
        raise RuntimeError("当前 GPU/Torch 组合不支持 BF16，无法使用 Anima 安全预设")
    return {**details, "validated": True, "index": TORCH_CUDA_INDEX}


def _run_checked(command: list[str], timeout: int, cwd: Path | None = None) -> None:
    completed = subprocess.run(
        command, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    if completed.returncode != 0:
        detail = "\n".join((completed.stdout + completed.stderr).splitlines()[-40:])
        raise RuntimeError(f"命令执行失败（退出码 {completed.returncode}）：{command[1] if len(command) > 1 else command[0]}\n{detail}")


def system_diagnostics() -> dict[str, Any]:
    result: dict[str, Any] = {
        "os": os.name,
        "python": os.sys.version,
        "workerExecutable": os.sys.executable,
        "nvidia": None,
    }
    executable = shutil.which("nvidia-smi")
    if executable:
        command = [
            executable, "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        if completed.returncode == 0:
            gpus = []
            for line in completed.stdout.strip().splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 4:
                    gpus.append({"name": parts[0], "memoryTotalMiB": int(parts[1]), "memoryFreeMiB": int(parts[2]), "driver": parts[3]})
            result["nvidia"] = gpus
    return result


def _download(url: str, destination: Path, progress: Callable[[int, int, str], None] | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    existing = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "Anima-LoRA-Studio/0.1"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length", "0")) + existing
        mode = "ab" if existing and response.status == 206 else "wb"
        if mode == "wb":
            existing = 0
        written = existing
        with partial.open(mode) as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
                written += len(chunk)
                if progress:
                    progress(written, total, destination.name)
    os.replace(partial, destination)


class RuntimeManager:
    def __init__(self, root: Path, resources: Path):
        self.root = root.resolve()
        self.resources = resources.resolve()
        self.tools = self.root / "tools"
        self.environments = self.root / "environments"

    @property
    def uv(self) -> Path:
        return self.tools / "uv.exe"

    def ensure_uv(self, progress: Callable[[int, int, str], None] | None = None) -> Path:
        if self.uv.is_file():
            return self.uv
        archive = self.tools / "uv.zip"
        _download(UV_WINDOWS_URL, archive, progress)
        with zipfile.ZipFile(archive) as bundle:
            member = next((item for item in bundle.namelist() if item.lower().endswith("uv.exe")), None)
            if not member:
                raise RuntimeError("uv 下载包中没有 uv.exe")
            self.tools.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, self.uv.open("wb") as target:
                shutil.copyfileobj(source, target)
        return self.uv

    def environment_python(self, name: str) -> Path:
        return self.environments / name / "Scripts" / "python.exe"

    @property
    def inference_runner(self) -> Path:
        return self.resources / "ml_runner.py"

    @property
    def trainer_engine(self) -> Path:
        return self.root / "engines" / f"sd-scripts-{PINNED_SD_SCRIPTS_COMMIT}"

    def ensure_trainer_engine(self, progress: Callable[[int, int, str], None] | None = None) -> Path:
        marker = self.trainer_engine / ".anima-studio-commit"
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == PINNED_SD_SCRIPTS_COMMIT:
            return self.trainer_engine
        engines = self.trainer_engine.parent
        engines.mkdir(parents=True, exist_ok=True)
        archive = engines / f"sd-scripts-{PINNED_SD_SCRIPTS_COMMIT}.zip"
        _download(
            f"https://github.com/kohya-ss/sd-scripts/archive/{PINNED_SD_SCRIPTS_COMMIT}.zip",
            archive, progress,
        )
        staging = engines / f".extract-{PINNED_SD_SCRIPTS_COMMIT}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        try:
            with zipfile.ZipFile(archive) as bundle:
                root = staging.resolve()
                for member in bundle.infolist():
                    candidate = (staging / member.filename).resolve()
                    if candidate != root and root not in candidate.parents:
                        raise RuntimeError("sd-scripts 下载包包含不安全路径")
                bundle.extractall(staging)
            extracted = next((item for item in staging.iterdir() if item.is_dir()), None)
            if not extracted or not (extracted / "anima_train_network.py").is_file():
                raise RuntimeError("sd-scripts 下载包缺少 anima_train_network.py")
            if self.trainer_engine.exists():
                raise RuntimeError(f"固定训练引擎目录已存在但版本标记无效：{self.trainer_engine}")
            extracted.replace(self.trainer_engine)
            marker.write_text(PINNED_SD_SCRIPTS_COMMIT, encoding="utf-8")
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return self.trainer_engine

    def _install_cuda_torch(
        self, uv: Path, python: Path, progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        if progress:
            progress(0, 1, f"安装 PyTorch {TORCH_VERSION} · CUDA {TORCH_CUDA_RUNTIME}")
        try:
            self._torch_diagnostics(python)
            if progress:
                progress(1, 1, f"PyTorch {TORCH_VERSION} · CUDA {TORCH_CUDA_RUNTIME} 已就绪")
            return
        except RuntimeError:
            pass
        _run_checked([
            str(uv), "pip", "install", "--python", str(python), "--force-reinstall",
            "--index-url", TORCH_CUDA_INDEX,
            f"torch=={TORCH_VERSION}", f"torchvision=={TORCHVISION_VERSION}",
        ], 7200)
        if progress:
            progress(1, 1, f"PyTorch {TORCH_VERSION} · CUDA {TORCH_CUDA_RUNTIME} 下载完成")

    def _install_requirements(self, uv: Path, python: Path, requirements: Path, cwd: Path | None = None) -> None:
        constraints = self.resources / "constraints-torch-cu128.txt"
        _run_checked([
            str(uv), "pip", "install", "--python", str(python),
            "--constraint", str(constraints), "--extra-index-url", TORCH_CUDA_INDEX,
            "-r", str(requirements),
        ], 7200, cwd)

    @staticmethod
    def _torch_diagnostics(python: Path) -> dict[str, Any]:
        script = (
            "import json,torch; "
            "print(json.dumps({'version':torch.__version__,'cudaRuntime':torch.version.cuda,"
            "'cudaAvailable':torch.cuda.is_available(),"
            "'bf16Supported':torch.cuda.is_available() and torch.cuda.is_bf16_supported(),"
            "'device':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))"
        )
        completed = subprocess.run(
            [str(python), "-c", script], capture_output=True, text=True, timeout=120, check=False,
        )
        if completed.returncode != 0:
            detail = "\n".join((completed.stdout + completed.stderr).splitlines()[-20:])
            raise RuntimeError("无法验证 PyTorch CUDA 环境：\n" + detail)
        try:
            details = json.loads(completed.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as error:
            raise RuntimeError("PyTorch CUDA 验证输出无效") from error
        return validate_torch_runtime(details)

    def bootstrap(
        self, name: str, requirements_file: Path, progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        if name not in {"caption", "trainer"}:
            raise ValueError("未知运行环境")
        uv = self.ensure_uv(progress)
        environment = self.environments / name
        python = self.environment_python(name)
        _run_checked([str(uv), "python", "install", "3.11"], 900)
        if not python.is_file():
            environment.parent.mkdir(parents=True, exist_ok=True)
            _run_checked([str(uv), "venv", "--python", "3.11", str(environment)], 300)
        self._install_cuda_torch(uv, python, progress)
        self._install_requirements(uv, python, requirements_file)
        engine = None
        if name == "trainer":
            engine = self.ensure_trainer_engine(progress)
            engine_requirements = engine / "requirements.txt"
            if engine_requirements.is_file():
                self._install_requirements(uv, python, engine_requirements, engine)
        torch_details = self._torch_diagnostics(python)
        manifest = {
            "name": name, "python": str(python), "requirements": str(requirements_file),
            "requirementsSha256": sha256_file(requirements_file), "updatedAt": utc_now(),
            "engineRoot": str(engine) if engine else None,
            "engineCommit": PINNED_SD_SCRIPTS_COMMIT if engine else None,
            "torch": torch_details,
            "torchConstraintsSha256": sha256_file(self.resources / "constraints-torch-cu128.txt"),
        }
        atomic_write_json(environment / "anima-runtime.json", manifest)
        return manifest

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {"root": str(self.root), "uv": str(self.uv) if self.uv.is_file() else None, "environments": {}, "engine": None}
        for name in ("caption", "trainer"):
            python = self.environment_python(name)
            manifest_path = python.parent.parent / "anima-runtime.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}
            torch_details = manifest.get("torch") if isinstance(manifest, dict) else None
            ready = bool(python.is_file() and isinstance(torch_details, dict) and torch_details.get("validated"))
            result["environments"][name] = {
                "ready": ready, "python": str(python), "torch": torch_details,
                "reason": None if ready else "CUDA Torch 尚未安装或未通过验证",
            }
        marker = self.trainer_engine / ".anima-studio-commit"
        if marker.is_file():
            result["engine"] = {"ready": True, "root": str(self.trainer_engine), "commit": marker.read_text(encoding="utf-8").strip()}
        return result
