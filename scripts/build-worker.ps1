$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$buildEnv = Join-Path $repoRoot ".runtime\build-worker"
$buildPython = Join-Path $buildEnv "Scripts\python.exe"
$requirements = Join-Path $repoRoot "services\worker\resources\requirements-worker-build.txt"
$workerMain = Join-Path $repoRoot "services\worker\main.py"
$workerPath = Join-Path $repoRoot "services\worker"
$workerResources = Join-Path $workerPath "resources"
$workerAdapters = Join-Path $workerPath "adapters"
$distPath = Join-Path $repoRoot "build\worker"
$workPath = Join-Path $repoRoot "build\pyinstaller\work"
$specPath = Join-Path $repoRoot "build\pyinstaller\spec"
$env:PIP_CACHE_DIR = Join-Path $repoRoot ".runtime\pip-cache"

if (-not (Test-Path -LiteralPath $buildPython)) {
    python -m venv $buildEnv
}

& $buildPython -m pip install --disable-pip-version-check -r $requirements

New-Item -ItemType Directory -Force -Path $distPath, $workPath, $specPath | Out-Null

& $buildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name anima-worker `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $specPath `
    --paths $workerPath `
    --add-data "$workerResources;resources" `
    --add-data "$workerAdapters;resources\adapters" `
    --collect-all PIL `
    $workerMain

Write-Host "Worker built: $(Join-Path $distPath 'anima-worker.exe')"
