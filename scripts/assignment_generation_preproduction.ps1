param(
    [ValidateSet("prepare", "config", "up", "status")]
    [string]$Action = "prepare",
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$ProjectName,
    [Parameter(Mandatory = $true)][int]$HttpsPort
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$evidenceRoot = Join-Path $root ".preproduction-assignment-generation"
$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $evidenceRoot $RunId))
if (-not $runtimeRoot.StartsWith($evidenceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "runtime directory escaped the assignment-generation evidence root"
}
if ($RunId -notmatch '^assignment-generation-(v3|landing-v1)-[0-9]{8}-[0-9]{6}$') {
    throw "invalid Stage 6 run id"
}
$landing = $RunId.StartsWith('assignment-generation-landing-v1-')
$marker = $RunId.Replace('assignment-generation-v3-', '').Replace('assignment-generation-landing-v1-', '').Replace('-', '').ToLowerInvariant()
$expectedProject = if ($landing) { "ahamarkassignmentlandingv1$marker" } else { "ahamarkassignmentv6c$marker" }
if ($ProjectName -ne $expectedProject) {
    throw "project name does not match the unique run marker"
}
$envFile = Join-Path $runtimeRoot "runtime.env"
$compose = Join-Path $root "docker-compose.preproduction.yml"

function New-Secret([int]$Bytes = 32) {
    $buffer = New-Object byte[] $Bytes
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($buffer) } finally { $generator.Dispose() }
    return ([BitConverter]::ToString($buffer) -replace "-", "").ToLowerInvariant()
}

if ($Action -eq "prepare") {
    if (Test-Path -LiteralPath $runtimeRoot) { throw "refusing to reuse run directory" }
    $containerIds = @(docker ps -a --filter "label=com.docker.compose.project=$ProjectName" --format "{{.ID}}")
    $volumeNames = @(docker volume ls --filter "name=agv6_${marker}_" --format "{{.Name}}")
    $networkNames = @(docker network ls --filter "name=agv6_${marker}_network" --format "{{.Name}}")
    if ($LASTEXITCODE -ne 0 -or $containerIds.Count -or $volumeNames.Count -or $networkNames.Count) {
        throw "refusing to reuse Docker resources"
    }
    New-Item -ItemType Directory -Path (Join-Path $runtimeRoot "certs") | Out-Null
    $values = [ordered]@{
        COMPOSE_PROJECT_NAME = $ProjectName
        PREPROD_RUN_ID = $RunId
        PREPROD_EVIDENCE_ROOT = ".preproduction-assignment-generation"
        PREPROD_HTTPS_PORT = $HttpsPort
        POSTGRES_DB = "ahamark_assignment_$marker"
        POSTGRES_USER = "ahamark_assignment_$marker"
        POSTGRES_PASSWORD = New-Secret
        SESSION_HMAC_SECRET = New-Secret 48
        MINIO_ACCESS_KEY = "agv6" + (New-Secret 8)
        MINIO_SECRET_KEY = New-Secret
        MINIO_BUCKET = "agv6-$marker"
        POSTGRES_VOLUME = "agv6_${marker}_postgres"
        REDIS_VOLUME = "agv6_${marker}_redis"
        MINIO_VOLUME = "agv6_${marker}_minio"
        PREPROD_NETWORK = "agv6_${marker}_network"
        PREPROD_TEACHER_EMAIL = "assignment-v6-$marker@evaluation.synthetic.invalid"
        PREPROD_TEACHER_PASSWORD = (New-Secret 24) + "A!"
        ASSIGNMENT_GENERATION_PROVIDER = "unavailable"
        ASSIGNMENT_GENERATION_ENABLED = "true"
        ASSIGNMENT_GENERATION_ALLOW_EXTERNAL_PROVIDER_REQUESTS = "false"
        ASSIGNMENT_GENERATION_ALLOW_TEACHER_START = "true"
        ASSIGNMENT_GENERATION_SUGGESTION_ONLY = "true"
        ASSIGNMENT_GENERATION_REAL_PROVIDER_QUALITY_PASSED = "false"
    }
    $lines = $values.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
    [IO.File]::WriteAllLines($envFile, $lines, [Text.UTF8Encoding]::new($false))
    $acl = Get-Acl $envFile
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        [Security.Principal.WindowsIdentity]::GetCurrent().Name,
        "FullControl", "Allow"
    )
    $acl.SetAccessRule($rule)
    Set-Acl -LiteralPath $envFile -AclObject $acl
    $openssl = "C:\Program Files\Git\usr\bin\openssl.exe"
    if (-not (Test-Path -LiteralPath $openssl)) { throw "local OpenSSL not found" }
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" -keyout (Join-Path $runtimeRoot "certs\localhost.key") -out (Join-Path $runtimeRoot "certs\localhost.crt") 2>$null
    $ErrorActionPreference = $previousErrorAction
    if ($LASTEXITCODE -ne 0) { throw "certificate generation failed" }
    Write-Output "PREPARED"
    exit
}
if (-not (Test-Path -LiteralPath $envFile)) { throw "runtime.env not found" }
if ($Action -eq "config") {
    docker compose -p $ProjectName --env-file $envFile -f $compose config --quiet
} elseif ($Action -eq "up") {
    docker compose -p $ProjectName --env-file $envFile -f $compose up -d --build
} elseif ($Action -eq "status") {
    docker compose -p $ProjectName --env-file $envFile -f $compose ps
}
if ($LASTEXITCODE -ne 0) { throw "docker compose action failed" }
