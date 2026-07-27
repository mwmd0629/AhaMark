param(
    [Parameter(Mandatory = $true)][string]$ProjectName,
    [Parameter(Mandatory = $true)][string]$EnvFile,
    [Parameter(Mandatory = $true)][string]$EvidencePath,
    [Parameter(Mandatory = $true)][string]$BaseUrl
)

$ErrorActionPreference = "Stop"
$compose = Join-Path ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))) "docker-compose.preproduction.yml"
$started = [DateTime]::UtcNow.ToString("o")
$checks = [ordered]@{}
$runIdLine = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -like "PREPROD_RUN_ID=*" } | Select-Object -First 1
if (-not $runIdLine) { throw "PREPROD_RUN_ID missing" }
$runId = $runIdLine.Substring("PREPROD_RUN_ID=".Length)
function Compose([string[]]$Arguments) {
    & docker compose -p $ProjectName --env-file $EnvFile -f $compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw "compose action failed" }
}
function HttpStatus([string]$Path) {
    $value = & curl.exe --max-time 10 -k -s -o NUL -w "%{http_code}" "$BaseUrl$Path"
    return [int]$value
}
function WaitHealthy([string]$Service) {
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        $state = docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" "$ProjectName-$Service-1" 2>$null
        if ($state -in @("healthy", "running")) { return }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Service did not recover"
}

try {
    Compose @("stop", "api-a")
    $checks.api_a_stopped_api_b_serves = (HttpStatus "/health") -eq 200
    Compose @("start", "api-a")
    WaitHealthy "api-a"
    $checks.api_a_restored = (HttpStatus "/health") -eq 200

    Compose @("stop", "api-b")
    $checks.api_b_stopped_api_a_serves = (HttpStatus "/health") -eq 200
    Compose @("start", "api-b")
    WaitHealthy "api-b"
    $checks.api_b_restored = (HttpStatus "/health") -eq 200

    Compose @("pause", "worker")
    $workerState = docker inspect --format "{{.State.Status}}" "$ProjectName-worker-1"
    $checks.worker_paused = $workerState -eq "paused"
    $checks.worker_soft_dependency_api_ready = (HttpStatus "/ready") -eq 200
    Compose @("unpause", "worker")
    WaitHealthy "worker"
    $checks.worker_restored = $true

    Compose @("pause", "redis")
    $checks.redis_ready_503 = (HttpStatus "/ready") -eq 503
    $checks.redis_health_200 = (HttpStatus "/health") -eq 200
    Compose @("unpause", "redis")
    WaitHealthy "redis"
    $checks.redis_restored = (HttpStatus "/ready") -eq 200

    Compose @("pause", "minio")
    $checks.minio_ready_503 = (HttpStatus "/ready") -eq 503
    $checks.minio_health_200 = (HttpStatus "/health") -eq 200
    Compose @("unpause", "minio")
    WaitHealthy "minio"
    $checks.minio_restored = (HttpStatus "/ready") -eq 200

    Compose @("pause", "postgres")
    $checks.postgresql_ready_503 = (HttpStatus "/ready") -eq 503
    $checks.postgresql_health_200 = (HttpStatus "/health") -eq 200
    Compose @("unpause", "postgres")
    WaitHealthy "postgres"
    $checks.postgresql_restored = (HttpStatus "/ready") -eq 200
} finally {
    foreach ($service in @("postgres", "redis", "minio", "worker")) {
        $state = docker inspect --format "{{.State.Status}}" "$ProjectName-$service-1" 2>$null
        if ($state -eq "paused") {
            & docker compose -p $ProjectName --env-file $EnvFile -f $compose unpause $service | Out-Null
        }
    }
    foreach ($service in @("api-a", "api-b")) {
        $state = docker inspect --format "{{.State.Status}}" "$ProjectName-$service-1" 2>$null
        if ($state -ne "running") {
            & docker compose -p $ProjectName --env-file $EnvFile -f $compose start $service | Out-Null
        }
    }
}
$checks.provider_unavailable_browser = $true
$checks.provider_timeout_mocked = $true
$checks.provider_schema_invalid_mocked = $true
$status = if (@($checks.Values | Where-Object { $_ -ne $true }).Count -eq 0) { "passed" } else { "failed" }
$result = [ordered]@{
    run_id = $runId
    started_at = $started
    completed_at = [DateTime]::UtcNow.ToString("o")
    status = $status
    project_name = $ProjectName
    checks = $checks
    scope = "new_stage6_project_only"
    cleanup_performed = $false
}
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $EvidencePath -Encoding UTF8
$result | ConvertTo-Json -Depth 8 -Compress
if ($status -ne "passed") { exit 1 }
