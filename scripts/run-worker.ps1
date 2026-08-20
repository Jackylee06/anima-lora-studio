$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
python (Join-Path $repoRoot "services\worker\main.py")

