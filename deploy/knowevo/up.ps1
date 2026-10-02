<#
.SYNOPSIS
    KnowEvo competition stack bring-up (Windows, PowerShell 5.1+).

.DESCRIPTION
    Logic mirrors deploy/knowevo/up.sh; not executable here - reviewed by
    inspection against the .sh implementation.

    Brings up the container side of the KnowEvo stack under the compose
    project name "nexent", matching the layout this fork is developed
    against:
      - infrastructure: nexent-postgresql (host 5434), nexent-elasticsearch
        (9210/9310), nexent-minio (9010/9011), redis (6379)
      - application:    nexent-mcp (5011/5015), nexent-runtime (5014),
                        nexent-northbound (5013)
      - auth:           supabase kong/auth/db (8000/8443, 5436) by default;
                        skip with -NoSupabase

    The backend config API (port 5010) is NOT part of the container set in
    this stack: it runs as a host process from backend/ (see the summary
    output for the host-mode environment it expects). For a fully
    containerized alternative use -WithConfig, and -WithWeb for the bundled
    web UI container.

    Compose posture (must match the deployed stack):
      - compose v2 only; two env files are passed together:
        deploy/env/.env (business values, secrets) and
        deploy/docker/.env.generated (image references).
      - explicit -f lists: docker-compose.yml plus
        docker-compose.override.yml. The override pins nexent-mcp to the
        locally built fork image and sets KW_MCP_TENANT_ID; an explicit -f
        list never auto-loads the override, so both files must be listed.
      - NEXENT_USER_DIR is set explicitly: compose does not expand the nested
        default ${NEXENT_USER_DIR:-$HOME/nexent} reliably.

    Idempotent: when all target containers already exist they are only
    started if stopped and then polled for health; nothing is recreated.
    Pass -Redeploy to force the compose up path (recreates containers whose
    definition changed).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\knowevo\up.ps1

.NOTES
    Options (mirrors of the .sh flags):
      -WithConfig    also start the containerized config service
                     (nexent-config, port 5010); refused when a foreign
                     process already binds it
      -WithWeb       also start the web UI container (nexent-web, port 3000)
      -NoSupabase    skip the Supabase auth stack
      -Redeploy      run docker compose up even when the stack already exists

    Tear down: powershell -File deploy\knowevo\down.ps1 [-Purge]
#>
[CmdletBinding()]
param(
    [switch]$WithConfig,
    [switch]$WithWeb,
    [switch]$NoSupabase,
    [switch]$Redeploy,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Passthrough
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Path self-location: works from any CWD (repo root, deploy\knowevo, ...).
# ---------------------------------------------------------------------------
$ScriptDir         = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot          = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$ComposeDir        = Join-Path $RepoRoot "deploy\docker\compose"
$AssetsDir         = Join-Path $RepoRoot "deploy\docker\assets"
$RootEnvFile       = Join-Path $RepoRoot "deploy\env\.env"
$RootEnvExample    = Join-Path $RepoRoot "deploy\env\.env.example"
$GeneratedEnvFile  = Join-Path $RepoRoot "deploy\docker\.env.generated"

$WithSupabase = (-not $NoSupabase)

if ($Passthrough) {
    Write-Host "[knowevo] WARNING: unrecognized arguments ignored: $($Passthrough -join ' ')" -ForegroundColor Yellow
}

# Compose argument arrays (passed splatted, so paths with spaces are safe).
$CoreComposeArgs = @(
    '--env-file', $RootEnvFile,
    '--env-file', $GeneratedEnvFile,
    '-p', 'nexent',
    '-f', (Join-Path $ComposeDir "docker-compose.yml"),
    '-f', (Join-Path $ComposeDir "docker-compose.override.yml")
)
$SupabaseComposeArgs = @(
    '--env-file', $RootEnvFile,
    '--env-file', $GeneratedEnvFile,
    '-p', 'nexent',
    '-f', (Join-Path $ComposeDir "docker-compose-supabase.yml")
)

# Host ports published by this stack. Must match the "ports:" sections of
# deploy/docker/compose/docker-compose*.yml. 5010/3000 belong to the optional
# config/web containers and are only preflighted when those are requested.
$BasePorts = @(5434, 9210, 9310, 6379, 9010, 9011, 5436, 8000, 8443, 5011, 5013, 5014, 5015)

# Default container set. nexent-config and nexent-data-process are NOT part
# of the KnowEvo stack (the config API runs on the host; data-process unused).
$InfraContainers    = @('nexent-postgresql', 'nexent-elasticsearch', 'nexent-minio', 'nexent-redis')
$AppContainers      = @('nexent-runtime', 'nexent-northbound', 'nexent-mcp')
$SupabaseContainers = @('supabase-kong-mini', 'supabase-auth-mini', 'supabase-db-mini')

function Write-Info    { param([string]$Message) Write-Host "[knowevo] $Message" }
function Write-WarnMsg { param([string]$Message) Write-Host "[knowevo] WARNING: $Message" -ForegroundColor Yellow }
function Write-Fail    { param([string]$Message) Write-Host "[knowevo] ERROR: $Message" -ForegroundColor Red }

# ---------------------------------------------------------------------------
# deploy/env/.env readers. Existing values are never rewritten, so re-runs and
# hand-tuned files stay untouched.
# ---------------------------------------------------------------------------
function Get-EnvFileVar {
    param([string]$Name)
    if (-not (Test-Path -LiteralPath $RootEnvFile)) { return $null }
    $line = Get-Content -LiteralPath $RootEnvFile | Where-Object { $_ -match "^$Name=" } | Select-Object -Last 1
    if (-not $line) { return $null }
    $value = $line.Substring($line.IndexOf('=') + 1).Trim()
    return $value.Trim('"').Trim("'")
}

function Test-EnvFileVar {
    param([string]$Name)
    if (-not (Test-Path -LiteralPath $RootEnvFile)) { return $false }
    $hit = Get-Content -LiteralPath $RootEnvFile | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
    return ($null -ne $hit)
}

function Add-EnvFileVar {
    param([string]$Name, [string]$Value)
    if (Test-EnvFileVar -Name $Name) { return }
    Add-Content -LiteralPath $RootEnvFile -Value ("{0}={1}" -f $Name, $Value) -Encoding Ascii
    Write-Info "deploy/env/.env: added $Name"
}

# ---------------------------------------------------------------------------
# Random material for missing secrets (mirrors the upstream deployer: an
# alphanumeric access key and a base64 secret).
# ---------------------------------------------------------------------------
function New-RandomBytes {
    param([int]$Count)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] $Count
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return ,$bytes
}

function New-AccessKey {
    # 24 hexadecimal characters, same shape as the bash implementation.
    $bytes = New-RandomBytes -Count 12
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function New-SecretValue {
    # Base64 of random bytes; may contain + / = like the bash original.
    param([int]$ByteCount = 32)
    $bytes = New-RandomBytes -Count $ByteCount
    return [System.Convert]::ToBase64String($bytes)
}

# HS256 JWT for the Supabase anon/service_role keys, same shape as the
# upstream deployer produces.
function New-SupabaseJwt {
    param([string]$Role, [string]$Secret)
    $now = [int64](Get-Date -UFormat %s)
    $exp = $now + 157680000
    $header  = '{"alg":"HS256","typ":"JWT"}'
    $payload = '{"role":"' + $Role + '","iss":"supabase","iat":' + $now + ',"exp":' + $exp + '}'
    $b64url = {
        param([string]$Text)
        [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Text)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    }
    $h = & $b64url $header
    $p = & $b64url $payload
    $hmac = New-Object System.Security.Cryptography.HMACSHA256
    $hmac.Key = [System.Text.Encoding]::UTF8.GetBytes($Secret)
    $sig = [System.Convert]::ToBase64String($hmac.ComputeHash([System.Text.Encoding]::UTF8.GetBytes("$h.$p"))).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $hmac.Dispose()
    return "$h.$p.$sig"
}

# ---------------------------------------------------------------------------
# Docker preflight (same gates as deploy/oneclick).
# ---------------------------------------------------------------------------
function Test-DockerEnv {
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        Write-Fail "docker CLI not found. Install Docker Desktop first (https://docs.docker.com/get-docker/)."
        exit 1
    }
    cmd /c "docker info >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Docker daemon is not reachable. Start Docker Desktop and retry."
        exit 1
    }
    cmd /c "docker compose version >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "docker compose v2 not available. Enable the docker-compose-plugin / WSL2 backend (v1 'docker-compose' is not supported)."
        exit 1
    }
    Write-Info "Docker and docker compose v2 detected."
}

# TCP port in use? Get-NetTCPConnection on modern Windows, netstat fallback.
function Test-PortInUse {
    param([int]$Port)
    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        return ($null -ne $conn)
    }
    $lines = netstat -an -p tcp
    foreach ($line in $lines) {
        if ($line -match "[:.]$Port\s" -and $line -match "LISTENING") { return $true }
    }
    return $false
}

# True when every docker container publishing Port is one of ours.
function Test-PortOwnedByStack {
    param([int]$Port)
    $lines = & docker ps --format "{{.Names}}`t{{.Ports}}"
    if ($LASTEXITCODE -ne 0) { return $false }
    $found = $false
    foreach ($line in @($lines)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $parts = $line -split "`t", 2
        $name = $parts[0]
        $ports = if ($parts.Count -gt 1) { $parts[1] } else { "" }
        if ($ports -match "[.:]${Port}->") {
            $found = $true
            $isOurs = ($name -like "nexent-*") -or ($name -like "nexent_*") -or ($name -like "supabase-*-mini")
            if (-not $isOurs) { return $false }
        }
    }
    return $found
}

function Test-Ports {
    param([int[]]$Ports)
    $occupied = @()
    foreach ($port in $Ports) {
        if (-not (Test-PortInUse -Port $port)) { continue }
        if (Test-PortOwnedByStack -Port $port) {
            Write-Info "Port $port already served by the stack (ok for re-run)."
            continue
        }
        $occupied += $port
    }
    if ($occupied.Count -gt 0) {
        Write-Fail ("Ports required by the stack are held by OTHER processes: {0}" -f ($occupied -join ', '))
        Write-Fail "Free them or stop the owning services before retrying."
        exit 1
    }
    Write-Info ("Port preflight passed ({0} ports checked)." -f $Ports.Count)
}

# ---------------------------------------------------------------------------
# Environment bootstrap: create deploy/env/.env from the template when missing
# and fill only missing keys (secrets, KnowEvo model ids, data/log roots).
# ---------------------------------------------------------------------------
function Initialize-EnvFiles {
    if (-not (Test-Path -LiteralPath $RootEnvFile)) {
        if (-not (Test-Path -LiteralPath $RootEnvExample)) {
            Write-Fail "Neither deploy/env/.env nor deploy/env/.env.example exists; cannot bootstrap environment."
            exit 1
        }
        Copy-Item -LiteralPath $RootEnvExample -Destination $RootEnvFile
        Write-Info "deploy/env/.env created from .env.example (default credentials; change them before exposing anything)."
    } else {
        Write-Info "Existing deploy/env/.env found; its values will be reused (idempotent)."
    }

    if (-not (Test-EnvFileVar -Name "MINIO_ACCESS_KEY") -or -not (Test-EnvFileVar -Name "MINIO_SECRET_KEY")) {
        Add-EnvFileVar -Name "MINIO_ACCESS_KEY" -Value (New-AccessKey)
        Add-EnvFileVar -Name "MINIO_SECRET_KEY" -Value (New-SecretValue)
        Write-Info "MinIO access keys generated."
    }

    if ($WithSupabase) {
        if (-not (Test-EnvFileVar -Name "JWT_SECRET")) {
            Add-EnvFileVar -Name "JWT_SECRET" -Value (New-SecretValue)
            Write-Info "JWT_SECRET generated."
        }
        Add-EnvFileVar -Name "SECRET_KEY_BASE" -Value (New-SecretValue -ByteCount 64)
        Add-EnvFileVar -Name "VAULT_ENC_KEY" -Value (New-SecretValue)
        if (-not (Test-EnvFileVar -Name "SUPABASE_KEY")) {
            $jwtSecret = Get-EnvFileVar -Name "JWT_SECRET"
            Add-EnvFileVar -Name "SUPABASE_KEY" -Value (New-SupabaseJwt -Role "anon" -Secret $jwtSecret)
        }
        if (-not (Test-EnvFileVar -Name "SERVICE_ROLE_KEY")) {
            $jwtSecret = Get-EnvFileVar -Name "JWT_SECRET"
            Add-EnvFileVar -Name "SERVICE_ROLE_KEY" -Value (New-SupabaseJwt -Role "service_role" -Secret $jwtSecret)
        }
        Write-Info "Supabase secrets present."
    }

    # KnowEvo model ids (platform model table row ids used by the pipeline).
    Add-EnvFileVar -Name "KW_LLM_SMALL_MODEL_ID" -Value "7"
    Add-EnvFileVar -Name "KW_LLM_MID_MODEL_ID" -Value "7"
    Add-EnvFileVar -Name "KW_LLM_LARGE_MODEL_ID" -Value "8"

    # Data/log roots. Compose bind-mounts live under ROOT_DIR; LOG_DIR is the
    # in-container path that maps to ${ROOT_DIR}/logs.
    $dataRoot = $env:NEXENT_ONECLICK_ROOT_DIR
    if (-not $dataRoot) { $dataRoot = Join-Path $HOME "nexent-data" }
    Add-EnvFileVar -Name "ROOT_DIR" -Value $dataRoot
    Add-EnvFileVar -Name "LOG_DIR" -Value "/mnt/nexent-data/logs"

    # Image references live in a separate file (the business env carries none).
    if (-not (Test-Path -LiteralPath $GeneratedEnvFile)) {
        $template = @'
# Image references for the compose files (interpolation only; never mounted
# into containers). Written once by deploy/knowevo/up.ps1 when missing; an
# existing file is never rewritten. Versions below mirror the images the
# reference stack runs; the fork image tag is pinned per service in
# docker-compose.override.yml.
NEXENT_IMAGE="ccr.ccs.tencentyun.com/nexent-hub/nexent:v2.5.1"
NEXENT_WEB_IMAGE="ccr.ccs.tencentyun.com/nexent-hub/nexent-web:v2.5.1"
NEXENT_DATA_PROCESS_IMAGE="ccr.ccs.tencentyun.com/nexent-hub/nexent-data-process:v2.5.1"
NEXENT_MCP_DOCKER_IMAGE="ccr.ccs.tencentyun.com/nexent-hub/nexent-mcp:v2.5.1"
NEXENT_SANDBOX_IMAGE="ccr.ccs.tencentyun.com/nexent-hub/nexent-sandbox:v2.5.1"
ELASTICSEARCH_IMAGE="elastic.m.daocloud.io/elasticsearch/elasticsearch:8.17.4"
POSTGRESQL_IMAGE="docker.m.daocloud.io/postgres:15-alpine"
REDIS_IMAGE="docker.m.daocloud.io/redis:alpine"
MINIO_IMAGE="quay.m.daocloud.io/minio/minio:RELEASE.2023-12-20T01-00-02Z"
OPENSSH_SERVER_IMAGE="ccr.ccs.tencentyun.com/nexent-hub/nexent-ubuntu-terminal:v2.5.1"
SUPABASE_KONG="docker.m.daocloud.io/kong:2.8.1"
SUPABASE_GOTRUE="docker.m.daocloud.io/supabase/gotrue:v2.170.0"
SUPABASE_DB="docker.m.daocloud.io/supabase/postgres:15.8.1.060"
'@
        Set-Content -LiteralPath $GeneratedEnvFile -Value $template -Encoding Ascii
        Write-Info "deploy/docker/.env.generated created with reference image tags."
    } else {
        Write-Info "Existing deploy/docker/.env.generated found; reused as-is."
    }

    # Fail before any compose call when an image variable is missing: without
    # it compose would render an empty image name and fail mid-deploy.
    $requiredImageKeys = @('NEXENT_IMAGE', 'ELASTICSEARCH_IMAGE', 'POSTGRESQL_IMAGE', 'REDIS_IMAGE',
        'MINIO_IMAGE', 'SUPABASE_KONG', 'SUPABASE_GOTRUE', 'SUPABASE_DB')
    if ($WithWeb) { $requiredImageKeys += 'NEXENT_WEB_IMAGE' }
    $generatedLines = Get-Content -LiteralPath $GeneratedEnvFile
    $missing = @()
    foreach ($key in $requiredImageKeys) {
        $hit = $generatedLines | Where-Object { $_ -match "^$key=" } | Select-Object -First 1
        if (-not $hit) { $missing += $key }
    }
    if ($missing.Count -gt 0) {
        Write-Fail ("deploy/docker/.env.generated is missing image variables: {0}" -f ($missing -join ', '))
        Write-Fail "Fix or remove the file so it can be regenerated from the reference template."
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Data-root bootstrap: bind-mount targets the compose files expect. The kong
# declarative config is a FILE mount - if absent, docker would create a
# directory of that name and kong would crash-loop.
# ---------------------------------------------------------------------------
function Initialize-DataRoot {
    $rootDir = Get-EnvFileVar -Name "ROOT_DIR"
    if (-not $rootDir) { $rootDir = Join-Path $HOME "nexent-data" }
    foreach ($sub in @("logs", "skills", "memory-provider-plugins", "scripts",
            "openssh-server\ssh-keys", "volumes\api", "volumes\db\data")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $rootDir $sub) | Out-Null
    }
    $kongTarget = Join-Path $rootDir "volumes\api\kong.yml"
    $kongSource = Join-Path $AssetsDir "volumes\api\kong.yml"
    if ((-not (Test-Path -LiteralPath $kongTarget)) -and (Test-Path -LiteralPath $kongSource)) {
        Copy-Item -LiteralPath $kongSource -Destination $kongTarget
        Write-Info "kong.yml copied into $kongTarget."
    }
    $syncTarget = Join-Path $rootDir "scripts\sync_user_supabase2pg.py"
    $syncSource = Join-Path $AssetsDir "scripts\sync_user_supabase2pg.py"
    if ((-not (Test-Path -LiteralPath $syncTarget)) -and (Test-Path -LiteralPath $syncSource)) {
        Copy-Item -LiteralPath $syncSource -Destination $syncTarget
    }
    Write-Info "Data root ready: $rootDir"
}

# ---------------------------------------------------------------------------
# The override file pins nexent-mcp to a locally built fork image (never
# pushed). Without it the mcp container cannot start, so check before deploy.
# ---------------------------------------------------------------------------
function Get-ForkImageTag {
    if (-not (Test-Path -LiteralPath (Join-Path $ComposeDir "docker-compose.override.yml"))) { return $null }
    $hit = Select-String -LiteralPath (Join-Path $ComposeDir "docker-compose.override.yml") `
        -Pattern 'nexent/nexent:knowevo-[0-9]+' | Select-Object -First 1
    if ($hit) { return $hit.Matches[0].Value }
    return $null
}

function Test-ForkImage {
    $tag = Get-ForkImageTag
    if (-not $tag) {
        Write-WarnMsg "No fork image pin found in docker-compose.override.yml; nexent-mcp will use the upstream image."
        return $true
    }
    & docker image inspect $tag *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Fork image present: $tag"
        return $true
    }
    Write-Fail "Fork image $tag (pinned by docker-compose.override.yml) not found locally."
    Write-Fail "Build it from the repository root first, e.g.:"
    Write-Fail "  docker build -f deploy/images/dockerfiles/main/Dockerfile.knowevo -t $tag ."
    return $false
}

# ---------------------------------------------------------------------------
# Health polls with timeouts. Container health where a healthcheck exists,
# HTTP/TCP probes on the published ports otherwise (endpoints verified against
# the running stack).
# ---------------------------------------------------------------------------
function Wait-ContainerHealthy {
    param([string]$Name, [int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
        $out = & docker ps --filter "name=^$Name$" --filter "health=healthy" --format "{{.Names}}" 2>$null
        if (@($out) -contains $Name) {
            Write-Info "$Name is healthy."
            return $true
        }
        if ((Get-Date) -ge $deadline) {
            Write-Fail "Timed out waiting for $Name to become healthy (after ${TimeoutSec}s)."
            return $false
        }
        Start-Sleep -Seconds 5
    }
}

function Wait-Postgres {
    param([int]$TimeoutSec)
    $pgUser = Get-EnvFileVar -Name "POSTGRES_USER"
    if (-not $pgUser) { $pgUser = "root" }
    $pgDb = Get-EnvFileVar -Name "POSTGRES_DB"
    if (-not $pgDb) { $pgDb = "nexent" }
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
        cmd /c "docker exec nexent-postgresql pg_isready -U $pgUser -d $pgDb >nul 2>&1"
        if ($LASTEXITCODE -eq 0) {
            Write-Info "PostgreSQL is accepting connections."
            return $true
        }
        if ((Get-Date) -ge $deadline) {
            Write-Fail "Timed out waiting for PostgreSQL (nexent-postgresql) after ${TimeoutSec}s."
            return $false
        }
        Start-Sleep -Seconds 5
    }
}

function Wait-HttpEndpoint {
    param([string]$Name, [string]$Url, [int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
                Write-Info "$Name is up: $Url"
                return $true
            }
        } catch { }
        if ((Get-Date) -ge $deadline) {
            Write-Fail "Timed out waiting for $Name at $Url after ${TimeoutSec}s."
            return $false
        }
        Start-Sleep -Seconds 5
    }
}

function Wait-TcpPort {
    param([string]$Name, [int]$Port, [int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
            if ($async.AsyncWaitHandle.WaitOne(3000) -and $client.Connected) {
                Write-Info "$Name is accepting connections on port $Port."
                return $true
            }
        } catch { } finally { $client.Close() }
        if ((Get-Date) -ge $deadline) {
            Write-Fail "Timed out waiting for $Name on port $Port after ${TimeoutSec}s."
            return $false
        }
        Start-Sleep -Seconds 5
    }
}

function Invoke-HealthPoll {
    $failed = $false
    if (-not (Wait-ContainerHealthy -Name "nexent-elasticsearch" -TimeoutSec 300)) { $failed = $true }
    if (-not (Wait-Postgres -TimeoutSec 120)) { $failed = $true }
    if (-not (Wait-ContainerHealthy -Name "nexent-redis" -TimeoutSec 60)) { $failed = $true }
    if (-not (Wait-HttpEndpoint -Name "MinIO API" -Url "http://127.0.0.1:9010/minio/health/live" -TimeoutSec 120)) { $failed = $true }
    if ($WithSupabase) {
        if (-not (Wait-ContainerHealthy -Name "supabase-db-mini" -TimeoutSec 180)) { $failed = $true }
        if (-not (Wait-ContainerHealthy -Name "supabase-auth-mini" -TimeoutSec 180)) { $failed = $true }
        if (-not (Wait-ContainerHealthy -Name "supabase-kong-mini" -TimeoutSec 180)) { $failed = $true }
    }
    if (-not (Wait-HttpEndpoint -Name "runtime service" -Url "http://localhost:5014/docs" -TimeoutSec 180)) { $failed = $true }
    if (-not (Wait-HttpEndpoint -Name "northbound API" -Url "http://localhost:5013/api/docs" -TimeoutSec 180)) { $failed = $true }
    if (-not (Wait-TcpPort -Name "MCP service" -Port 5011 -TimeoutSec 180)) { $failed = $true }
    if (-not (Wait-HttpEndpoint -Name "MCP management API" -Url "http://localhost:5015/docs" -TimeoutSec 120)) { $failed = $true }
    if ($WithConfig) {
        if (-not (Wait-HttpEndpoint -Name "config service (container)" -Url "http://localhost:5010/docs" -TimeoutSec 300)) { $failed = $true }
    }
    if ($WithWeb) {
        if (-not (Wait-HttpEndpoint -Name "web UI" -Url "http://localhost:3000/" -TimeoutSec 180)) { $failed = $true }
    }
    return (-not $failed)
}

# Create the Elasticsearch API key the backend expects, but only when the
# environment does not have one yet; existing keys are never overwritten.
function Add-ElasticsearchApiKey {
    if (Test-EnvFileVar -Name "ELASTICSEARCH_API_KEY") { return }
    $elasticPassword = Get-EnvFileVar -Name "ELASTIC_PASSWORD"
    if (-not $elasticPassword) { $elasticPassword = "nexent@2025" }
    $body = '{"name":"knowevo_deploy_key","role_descriptors":{"knowevo_role":{"cluster":["all"],"index":[{"names":["*"],"privileges":["all"]}]}}}'
    $json = $null
    try {
        $json = & docker exec nexent-elasticsearch curl -s `
            -u "elastic:$elasticPassword" -H "Content-Type: application/json" -d $body `
            "http://localhost:9200/_security/api_key" 2>$null
    } catch { }
    $jsonText = ($json -join "`n")
    if ($jsonText -match '"encoded":"([^"]*)"') {
        Add-EnvFileVar -Name "ELASTICSEARCH_API_KEY" -Value $Matches[1]
        Write-Info "ELASTICSEARCH_API_KEY generated and stored in deploy/env/.env."
    } else {
        Write-WarnMsg "Could not generate an Elasticsearch API key; the backend may need one to talk to ES."
    }
}

# ---------------------------------------------------------------------------
# Fast path: every target container already exists (deployed stack). Start the
# stopped ones in place - never recreate - then poll health.
# ---------------------------------------------------------------------------
function Get-ContainerPorts {
    param([string]$Name)
    switch ($Name) {
        'nexent-postgresql'    { @(5434) }
        'nexent-elasticsearch' { @(9210, 9310) }
        'nexent-redis'         { @(6379) }
        'nexent-minio'         { @(9010, 9011) }
        'nexent-runtime'       { @(5014) }
        'nexent-northbound'    { @(5013) }
        'nexent-mcp'           { @(5011, 5015) }
        'nexent-config'        { @(5010) }
        'nexent-web'           { @(3000) }
        'supabase-kong-mini'   { @(8000, 8443) }
        'supabase-db-mini'     { @(5436) }
        'supabase-auth-mini'   { @() }
        default                { @() }
    }
}

function Get-TargetContainers {
    $names = @()
    $names += $InfraContainers + $AppContainers
    if ($WithSupabase) { $names += $SupabaseContainers }
    if ($WithConfig)   { $names += 'nexent-config' }
    if ($WithWeb)      { $names += 'nexent-web' }
    return ,$names
}

function Test-ExistingStackComplete {
    foreach ($name in (Get-TargetContainers)) {
        $out = & docker ps -a --format "{{.Names}}" --filter "name=^$name$" 2>$null
        if (@($out) -notcontains $name) { return $false }
    }
    return $true
}

# Start every stopped container of one wave, skipping containers whose host
# port is held by a foreign process (starting them would crash on bind).
function Start-FastPathWave {
    param([string[]]$Containers)
    $started = @()
    $skipped = @()
    foreach ($name in $Containers) {
        $status = (& docker inspect -f "{{.State.Status}}" $name 2>$null) -join ""
        if ($status -eq "running") { continue }
        $foreign = $false
        foreach ($port in (Get-ContainerPorts $name)) {
            if ((Test-PortInUse -Port $port) -and -not (Test-PortOwnedByStack -Port $port)) {
                $foreign = $true
                break
            }
        }
        if ($foreign) {
            $skipped += $name
            continue
        }
        & docker start $name *> $null
        if ($LASTEXITCODE -eq 0) {
            $started += $name
        } else {
            Write-WarnMsg "Could not start $name; run the full deploy path (-Redeploy)."
        }
    }
    if ($started.Count -gt 0) { Write-Info ("Started: {0}" -f ($started -join ', ')) }
    if ($skipped.Count -gt 0) { Write-WarnMsg ("Skipped (a foreign process holds their ports): {0}" -f ($skipped -join ', ')) }
}

function Invoke-FastPath {
    Write-Info "Complete existing stack found; using the fast path (start + health only, no recreate)."
    Start-FastPathWave -Containers $InfraContainers
    if (-not (Wait-ContainerHealthy -Name 'nexent-elasticsearch' -TimeoutSec 300)) { return $false }
    if (-not (Wait-Postgres -TimeoutSec 120)) { return $false }
    if ($WithSupabase) { Start-FastPathWave -Containers $SupabaseContainers }
    Start-FastPathWave -Containers $AppContainers
    if ($WithConfig) { Start-FastPathWave -Containers @('nexent-config') }
    if ($WithWeb)    { Start-FastPathWave -Containers @('nexent-web') }
    return $true
}

# ---------------------------------------------------------------------------
# Full path: compose up in waves so that infrastructure is healthy before the
# application containers boot (they expect Elasticsearch up; the optional
# config container runs SQL migrations at boot).
# ---------------------------------------------------------------------------
function Invoke-FullDeploy {
    Write-Info "Starting infrastructure services..."
    & docker compose @CoreComposeArgs up -d nexent-elasticsearch nexent-postgresql nexent-minio redis
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to start infrastructure services."
        return $false
    }
    if (-not (Wait-ContainerHealthy -Name "nexent-elasticsearch" -TimeoutSec 300)) { return $false }
    if (-not (Wait-Postgres -TimeoutSec 120)) { return $false }
    Add-ElasticsearchApiKey

    if ($WithSupabase) {
        Write-Info "Starting Supabase services..."
        & docker compose @SupabaseComposeArgs up -d
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Failed to start Supabase services."
            return $false
        }
    }

    $services = @('nexent-mcp', 'nexent-runtime', 'nexent-northbound')
    if ($WithConfig) { $services += 'nexent-config' }
    if ($WithWeb)    { $services += 'nexent-web' }
    Write-Info ("Starting application services: {0}" -f ($services -join ' '))
    & docker compose @CoreComposeArgs up -d @services
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to start application services."
        return $false
    }
    return $true
}

function Write-Diagnostics {
    Write-Host ""
    Write-Fail "Diagnostics:"
    Write-Host "  1. Container status:"
    Write-Host "     docker compose --env-file `"$RootEnvFile`" --env-file `"$GeneratedEnvFile`" -p nexent ``"
    Write-Host "       -f `"$ComposeDir\docker-compose.yml`" -f `"$ComposeDir\docker-compose.override.yml`" ps -a"
    Write-Host "  2. Logs: docker logs --tail 100 <container>"
    Write-Host "  3. Environment files: $RootEnvFile"
    Write-Host "                        $GeneratedEnvFile"
    $dataRoot = Get-EnvFileVar -Name "ROOT_DIR"
    if (-not $dataRoot) { $dataRoot = Join-Path $HOME "nexent-data" }
    Write-Host "  4. Data root: $dataRoot"
    Write-Host ""
}

function Write-Summary {
    $dataRoot = Get-EnvFileVar -Name "ROOT_DIR"
    if (-not $dataRoot) { $dataRoot = Join-Path $HOME "nexent-data" }
    $pgUser = Get-EnvFileVar -Name "POSTGRES_USER"
    if (-not $pgUser) { $pgUser = "root" }
    $pgDb = Get-EnvFileVar -Name "POSTGRES_DB"
    if (-not $pgDb) { $pgDb = "nexent" }
    Write-Host ""
    Write-Host "============================================================"
    Write-Host " KnowEvo stack is up."
    Write-Host "============================================================"
    Write-Host " PostgreSQL:             localhost:5434 (user ""$pgUser"", db ""$pgDb""; supabase db on 5436)"
    Write-Host " Elasticsearch:          http://localhost:9210"
    Write-Host " Redis:                  localhost:6379"
    Write-Host " MinIO API / console:    http://localhost:9010 / http://localhost:9011"
    Write-Host " MCP service (SSE):      http://localhost:5011/sse"
    Write-Host " MCP management API:     http://localhost:5015/docs"
    Write-Host " Runtime service (WS):   ws://localhost:5014"
    Write-Host " Northbound API:         http://localhost:5013/api"
    if ($WithSupabase) { Write-Host " Supabase gateway:       http://localhost:8000" }
    if ($WithWeb)      { Write-Host " Web UI:                 http://localhost:3000" }
    Write-Host " Backend config API:     http://localhost:5010/docs - host process, NOT managed"
    Write-Host "                         by this script. Expected host-mode environment:"
    Write-Host "                         POSTGRES_HOST=localhost POSTGRES_PORT=5434"
    Write-Host "                         MINIO_ENDPOINT=http://127.0.0.1:9010"
    Write-Host "                         NEXENT_SANDBOX_DEFAULT_LEVEL=local"
    Write-Host "                         KW_MCP_TENANT_ID=<primary tenant uuid>"
    Write-Host " Credentials location:   deploy\env\.env"
    Write-Host " Data root:              $dataRoot"
    Write-Host " Tear down:              powershell -File deploy\knowevo\down.ps1 (add -Purge to"
    Write-Host "                         also remove named volumes; bind-mount data under the"
    Write-Host "                         data root is always kept)"
    Write-Host "============================================================"
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Write-Info "Repository root: $RepoRoot"
Test-DockerEnv

$ports = $BasePorts
if ($WithConfig) { $ports += 5010 }
if ($WithWeb)    { $ports += 3000 }
Test-Ports -Ports $ports

Initialize-EnvFiles
Initialize-DataRoot

# compose does not expand the nested default ${NEXENT_USER_DIR:-$HOME/nexent}
# reliably; set it explicitly for the compose child processes.
if (-not $env:NEXENT_USER_DIR) { $env:NEXENT_USER_DIR = Join-Path $HOME "nexent" }
Write-Info "NEXENT_USER_DIR=$($env:NEXENT_USER_DIR)"

$ok = $true
if (-not $Redeploy -and (Test-ExistingStackComplete)) {
    if (-not (Invoke-FastPath)) { $ok = $false }
} else {
    if (-not (Test-ForkImage)) { exit 1 }
    if (-not (Invoke-FullDeploy)) { $ok = $false }
}

if (-not $ok) {
    Write-Fail "Bring-up did not complete; see the messages above."
    Write-Diagnostics
    exit 1
}

Write-Info "Stack is up; polling service health..."
if (-not (Invoke-HealthPoll)) {
    Write-Fail "One or more services did not become healthy in time."
    Write-Diagnostics
    exit 1
}

Write-Summary
Write-Info "Done."
