param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [Parameter(Mandatory = $true)]
    [string]$ModelDir,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python executable not found"
}
if (-not (Test-Path -LiteralPath $ModelDir -PathType Container)) {
    throw "Formula model directory not found"
}
$token = [string]$env:AHAMARK_FORMULA_PROVIDER_TOKEN
if ($token.Length -lt 32) {
    throw "Set AHAMARK_FORMULA_PROVIDER_TOKEN to a value of at least 32 characters"
}
if ($Port -lt 1024 -or $Port -gt 65535) {
    throw "Port must be between 1024 and 65535"
}

$resolvedModelDir = (Resolve-Path -LiteralPath $ModelDir).Path
@("inference.json", "inference.pdiparams", "inference.yml") | ForEach-Object {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedModelDir $_) -PathType Leaf)) {
        throw "Formula model is incomplete: missing $_"
    }
}

$existingListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existingListener) {
    throw "Port $Port is already in use"
}

& $PythonExe -c "import fastapi, ftfy, paddle, paddleocr, tokenizers, uvicorn" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Formula provider Python dependencies are incomplete"
}

$env:AHAMARK_FORMULA_MODEL_DIR = $resolvedModelDir
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
    & $PythonExe -m uvicorn scripts.local_formula_provider:app --host 127.0.0.1 --port $Port --no-access-log
}
finally {
    Pop-Location
}
