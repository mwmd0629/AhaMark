param(
    [ValidateSet("prepare", "config", "up", "status")]
    [string]$Action = "prepare",
    [string]$RunId = ("v8-" + (Get-Date -Format "yyyyMMdd-HHmmss").ToLowerInvariant()),
    [int]$HttpsPort = 9443
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runtimeRoot = [System.IO.Path]::GetFullPath((Join-Path $root ".preproduction-v8\$RunId"))
if (-not $runtimeRoot.StartsWith((Join-Path $root ".preproduction-v8"), [StringComparison]::OrdinalIgnoreCase)) {
    throw "runtime directory escaped the repository"
}
$envFile = Join-Path $runtimeRoot "runtime.env"
$compose = Join-Path $root "docker-compose.preproduction.yml"

function New-Secret([int]$Bytes = 32) {
    $buffer = New-Object byte[] $Bytes
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($buffer)
    } finally {
        $generator.Dispose()
    }
    return ([BitConverter]::ToString($buffer) -replace "-", "").ToLowerInvariant()
}

if ($Action -eq "prepare") {
    if (Test-Path -LiteralPath $runtimeRoot) {
        throw "refusing to reuse existing run directory: $runtimeRoot"
    }
    $projectName = "ahamark-preprod-$RunId"
    $existingContainers = @(docker ps -a --filter "label=com.docker.compose.project=$projectName" --format "{{.ID}}")
    if ($LASTEXITCODE -ne 0) {
        throw "unable to inspect Docker containers before preparing the run"
    }
    $existingVolumes = @(docker volume ls --filter "label=com.docker.compose.project=$projectName" --format "{{.Name}}")
    if ($LASTEXITCODE -ne 0) {
        throw "unable to inspect Docker volumes before preparing the run"
    }
    $existingNetworks = @(docker network ls --filter "label=com.docker.compose.project=$projectName" --format "{{.Name}}")
    if ($LASTEXITCODE -ne 0) {
        throw "unable to inspect Docker networks before preparing the run"
    }
    if ($existingContainers.Count -or $existingVolumes.Count -or $existingNetworks.Count) {
        throw "refusing to reuse existing Docker resources for project: $projectName"
    }
    New-Item -ItemType Directory -Path (Join-Path $runtimeRoot "certs") | Out-Null
    $databaseSuffix = ($RunId -replace "[^a-zA-Z0-9]", "_").ToLowerInvariant()
    $values = [ordered]@{
        COMPOSE_PROJECT_NAME = $projectName
        PREPROD_RUN_ID = $RunId
        PREPROD_HTTPS_PORT = $HttpsPort
        POSTGRES_DB = "ahamark_preprod_$databaseSuffix"
        POSTGRES_USER = "ahamark_preprod_$databaseSuffix"
        POSTGRES_PASSWORD = New-Secret
        SESSION_HMAC_SECRET = New-Secret 48
        MINIO_ACCESS_KEY = "preprod" + (New-Secret 8)
        MINIO_SECRET_KEY = New-Secret
        MINIO_BUCKET = "ahamark-preprod-$RunId"
        PREPROD_TEACHER_EMAIL = "teacher-$RunId@preprod.synthetic.invalid"
        PREPROD_TEACHER_PASSWORD = (New-Secret 24) + "A!"
    }
    $lines = $values.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
    [IO.File]::WriteAllLines($envFile, $lines, [Text.UTF8Encoding]::new($false))
    $openssl = "C:\Program Files\Git\usr\bin\openssl.exe"
    if (-not (Test-Path -LiteralPath $openssl)) {
        throw "local OpenSSL not found"
    }
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" -keyout (Join-Path $runtimeRoot "certs\localhost.key") -out (Join-Path $runtimeRoot "certs\localhost.crt") 2>$null
    $ErrorActionPreference = $previousErrorAction
    if ($LASTEXITCODE -ne 0) {
        throw "certificate generation failed (docker exit $LASTEXITCODE)"
    }
    Write-Output "prepared ignored runtime: $runtimeRoot"
    Write-Output "next: .\scripts\preproduction_v8.ps1 -Action up -RunId $RunId -HttpsPort $HttpsPort"
    exit
}

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "runtime.env not found; run prepare with the same RunId first"
}
if ($Action -eq "config") {
    docker compose --env-file $envFile -f $compose config --quiet
} elseif ($Action -eq "up") {
    docker compose --env-file $envFile -f $compose up -d --build
} elseif ($Action -eq "status") {
    docker compose --env-file $envFile -f $compose ps
}
if ($LASTEXITCODE -ne 0) {
    throw "docker compose action failed (exit $LASTEXITCODE)"
}
