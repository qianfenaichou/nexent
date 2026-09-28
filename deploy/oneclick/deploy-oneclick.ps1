<#
.SYNOPSIS
    Nexent one-click deployment (Windows, PowerShell 5.1+).

.DESCRIPTION
    Logic mirrors deploy-oneclick.sh; not executed in CI - reviewed on Linux only.

    This is a THIN WRAPPER around the upstream entry point
    deploy/docker/deploy.sh --defaults. The upstream script is bash, so Git
    Bash (installed with Git for Windows) is required to run it. This wrapper
    adds non-interactive preflight checks (docker, compose v2, disk, port
    conflicts) and post-deployment health polling, and delegates ALL
    deployment logic (env bootstrap, secret generation, compose up, ES API
    key) to the upstream script so behavior never drifts.

    Relationship to upstream deploy.sh:
      - default path here = deploy/docker/deploy.sh --defaults (non-interactive)
      - advanced config   = deploy/deploy.sh --config (upstream interactive TUI)

    Idempotent: safe to re-run. Existing deploy/env/.env values (keys,
    ROOT_DIR) are reused by upstream logic; ports already served by Nexent
    containers are not treated as conflicts.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\oneclick\deploy-oneclick.ps1

.NOTES
    Options (mirrors of the .sh flags):
      -Production          production port policy (only ports 3000/5013 published)
      -Mainland            use mainland China image sources
      -General             use general (global) image sources
      -LocalLatest         use locally built :latest images
      -RootDir PATH        data/log root dir (default: ~\nexent-data on first run)
      -Redeploy            skip the existing-stack fast path and run the full
                           upstream deployment (may recreate containers)
      extra args           forwarded verbatim to deploy/docker/deploy.sh
    Environment overrides:
      NEXENT_ONECLICK_ROOT_DIR=PATH     data dir used when .env has no ROOT_DIR yet
      ONECLICK_IGNORE_PORT_CONFLICTS=1  continue despite non-Nexent port conflicts
#>
[CmdletBinding()]
param(
    [switch]$Production,
    [switch]$Mainland,
    [switch]$General,
    [switch]$LocalLatest,
    [switch]$Redeploy,
    [string]$RootDir = "",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Passthrough
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Path self-location: works from any CWD (repo root, deploy\oneclick, ...).
# ---------------------------------------------------------------------------
$ScriptDir      = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot       = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$DeployDockerDir = Join-Path $RepoRoot "deploy\docker"
$ComposeDir     = Join-Path $DeployDockerDir "compose"
$RootEnvFile    = Join-Path $RepoRoot "deploy\env\.env"
$RootEnvExample = Join-Path $RepoRoot "deploy\env\.env.example"

# Ports published by each port policy. Must match
# deployment_compute_docker_ports() in deploy/common/common.sh and the
# "ports:" sections of deploy/docker/compose/docker-compose*.yml.
$DevPorts  = @(3000, 5010, 5011, 5012, 5013, 5014, 5015, 5434, 5436, 5555, 6379, 8000, 8443, 8265, 9010, 9011, 9210, 9310)
$ProdPorts = @(3000, 5013)

$PortPolicy    = "development"
$ComposeSuffix = ".yml"
$deployArgs    = @('--defaults')   # always run the upstream non-interactive path

function Write-Info    { param([string]$Message) Write-Host "[oneclick] $Message" }
function Write-WarnMsg { param([string]$Message) Write-Host "[oneclick] WARNING: $Message" -ForegroundColor Yellow }
function Write-Fail    { param([string]$Message) Write-Host "[oneclick] ERROR: $Message" -ForegroundColor Red }

function Get-EnvFileVar {
    param([string]$Name)
    if (-not (Test-Path -LiteralPath $RootEnvFile)) { return $null }
    $line = Get-Content -LiteralPath $RootEnvFile | Where-Object { $_ -match "^$Name=" } | Select-Object -Last 1
    if (-not $line) { return $null }
    $value = $line.Substring($line.IndexOf('=') + 1).Trim()
    return $value.Trim('"').Trim("'")
}

# TCP port in use? Get-NetTCPConnection on modern Windows, netstat fallback.
# Note: the netstat fallback only matches English "LISTENING" state text.
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

# True when every docker container publishing Port is a Nexent container.
# Mirrors is_nexent_container_name() in deploy/common/common.sh.
function Test-PortOwnedByNexent {
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
            $isNexent = ($name -like "nexent-*") -or ($name -like "nexent_*") -or ($name -like "supabase-*-mini")
            if (-not $isNexent) { return $false }
        }
    }
    return $found
}

# Upstream deploy.sh probes compose with the regex v<digit>.<digit>.<digit>
# (get_compose_version in deploy/docker/deploy.sh). Distro-packaged compose
# v2 (e.g. Ubuntu's docker-compose-v2 .deb) prints its version WITHOUT the
# leading "v" ("2.40.3+ds1..."), so the upstream probe misses it and falls
# back to the legacy docker-compose v1 binary. Compose v1 crashes while
# recreating containers built from modern image metadata
# (KeyError: 'ContainerConfig'), which can leave a stack half-recreated.
# Gate here on the SAME regex so an incompatible install fails BEFORE
# anything is touched instead of mid-recreate.
$script:ComposeUpstreamCompat = $false
function Test-ComposeCompat {
    $out = (& docker compose version 2>$null) -join " "
    if ($out -match 'v(\d+\.\d+\.\d+)') {
        Write-Info "Compose v2 ($($Matches[1])) is recognized by the upstream version probe."
        $script:ComposeUpstreamCompat = $true
        return
    }
    Write-WarnMsg "Distro compose v2 detected (""$out""): the upstream deployer cannot parse this"
    Write-WarnMsg "version string and would fall back to compose v1, which crashes on recreate."
    if (-not $Redeploy -and (Test-ExistingStackComplete)) {
        Write-WarnMsg "A complete existing stack was found: the fast path below avoids recreate entirely."
        return
    }
    Write-Fail "Refusing to continue: fix the install first (official plugin: https://docs.docker.com/compose/install/linux/),"
    Write-Fail "or re-run with -Redeploy to force the upstream path at your own risk."
    exit 1
}

# Containers of a complete stack, in two waves: infrastructure must be
# healthy before application containers start (the config service runs SQL
# migrations at boot and aborts if PostgreSQL is not reachable yet).
# When ALL of them already exist, a re-run only needs to start stopped ones
# and poll health: going through the upstream deploy path reconciles config
# and may recreate containers, which is exactly what compose v1 cannot do
# safely (see Test-ComposeCompat).
$FastpathInfra = @('nexent-postgresql', 'nexent-elasticsearch', 'nexent-redis',
    'nexent-minio')
$FastpathApps = @('nexent-config', 'nexent-runtime', 'nexent-northbound',
    'nexent-mcp', 'nexent-data-process', 'nexent-web')

function Get-ContainerPorts {
    param([string]$Name)
    switch ($Name) {
        'nexent-postgresql'    { @(5434) }
        'nexent-elasticsearch' { @(9210, 9310) }
        'nexent-redis'         { @(6379) }
        'nexent-minio'         { @(9010, 9011) }
        'nexent-config'        { @(5010) }
        'nexent-runtime'       { @(5014) }
        'nexent-northbound'    { @(5013) }
        'nexent-mcp'           { @(5011, 5015) }
        'nexent-data-process'  { @(5012, 5555, 8265) }
        'nexent-web'           { @(3000) }
        default                { @() }
    }
}

function Test-ExistingStackComplete {
    foreach ($name in ($FastpathInfra + $FastpathApps)) {
        $out = & docker ps -a --format "{{.Names}}" --filter "name=^$name$" 2>$null
        if (@($out) -notcontains $name) { return $false }
    }
    return $true
}

# Start every stopped container of one wave, skipping containers whose host
# port is held by a NON-Nexent process (e.g. a host uvicorn/npm dev
# process): starting them would crash on bind.
function Start-FastPathWave {
    param([string[]]$Containers)
    $started = @()
    $skipped = @()
    foreach ($name in $Containers) {
        $status = (& docker inspect -f "{{.State.Status}}" $name 2>$null) -join ""
        if ($status -eq "running") { continue }
        $foreign = $false
        foreach ($port in (Get-ContainerPorts $name)) {
            if ((Test-PortInUse $port) -and -not (Test-PortOwnedByNexent $port)) {
                $foreign = $true
                break
            }
        }
        if ($foreign) {
            $skipped += $name
            continue
        }
        & docker start $name | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $started += $name
        } else {
            Write-WarnMsg "Could not start $name; it may need the full deploy path."
        }
    }
    if ($started.Count -gt 0) { Write-Info "Started: $($started -join ', ')" }
    if ($skipped.Count -gt 0) { Write-WarnMsg "Skipped (a host process holds their ports): $($skipped -join ', ')" }
}

# Start stopped containers of an existing stack in place - never recreate.
# Wave 1: infrastructure, waited until healthy. Wave 2: application
# containers (safe to boot now: SQL migrations will find PostgreSQL up).
function Invoke-FastPath {
    Start-FastPathWave $FastpathInfra
    if (-not (Wait-ContainerHealthy -Name 'nexent-elasticsearch' -TimeoutSec 180)) {
        Write-Diagnostics
        return $false
    }
    if (-not (Wait-Postgres -TimeoutSec 120)) {
        Write-Diagnostics
        return $false
    }
    Start-FastPathWave $FastpathApps
    # If a host process holds 3000 the web container was skipped above and
    # the host process serves the UI instead; the poll then checks that.
    if (-not (Invoke-HealthPoll)) {
        Write-Fail "Existing stack did not become healthy in time."
        Write-Diagnostics
        return $false
    }
    return $true
}

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

function Test-DiskSpace {
    param([string]$Target)
    # Prefer the Docker storage root (images land there); fall back to repo drive.
    $dockerRoot = $null
    try {
        $out = cmd /c 'docker info --format "{{.DockerRootDir}}" 2>nul'
        if ($LASTEXITCODE -eq 0 -and $out) { $dockerRoot = @($out)[0] }
    } catch { }
    if ($dockerRoot -and (Test-Path -LiteralPath $dockerRoot)) { $Target = $dockerRoot }
    $driveName = $Target.Substring(0, 1)
    $drive = $null
    try { $drive = Get-PSDrive -Name $driveName -ErrorAction Stop } catch { }
    if (-not $drive) {
        Write-WarnMsg "Cannot determine free disk space for $Target; skipping disk check."
        return
    }
    # Images for the full stack are several GB; 20 GiB is a comfortable floor.
    if ($drive.Free -lt 2GB) {
        Write-Fail "Less than 2 GiB free on $Target. Free space before deploying."
        exit 1
    } elseif ($drive.Free -lt 20GB) {
        Write-WarnMsg "Less than 20 GiB free on $Target; image pulls may fail on a first install."
    } else {
        Write-Info ("Disk check passed ({0} GiB free on {1})." -f [math]::Floor($drive.Free / 1GB), $Target)
    }
}

function Test-Ports {
    param([int[]]$Ports)
    $occupied = @()
    foreach ($port in $Ports) {
        if (-not (Test-PortInUse -Port $port)) { continue }
        if (Test-PortOwnedByNexent -Port $port) {
            Write-Info "Port $port already served by Nexent containers (ok for re-run)."
            continue
        }
        $occupied += $port
    }
    if ($occupied.Count -gt 0) {
        Write-WarnMsg ("Ports required by Nexent are held by OTHER processes: {0}" -f ($occupied -join ', '))
        if ($env:ONECLICK_IGNORE_PORT_CONFLICTS -eq "1") {
            Write-WarnMsg "ONECLICK_IGNORE_PORT_CONFLICTS=1 set; continuing despite conflicts."
        } else {
            Write-Fail "Free the ports (see README troubleshooting) or re-run with ONECLICK_IGNORE_PORT_CONFLICTS=1."
            Write-Fail "Interactive override is also available via the upstream TUI: bash deploy/deploy.sh --config"
            exit 1
        }
    } else {
        Write-Info ("Port preflight passed ({0} ports checked)." -f $Ports.Count)
    }
}

function Write-Diagnostics {
    $composeFile = Join-Path $ComposeDir ("docker-compose" + $ComposeSuffix)
    Write-Host ""
    Write-Fail "Diagnostics:"
    Write-Host "  1. Container status:"
    Write-Host "     docker compose -p nexent --env-file `"$RootEnvFile`" -f `"$composeFile`" ps -a"
    Write-Host "  2. Backend (config service) logs:"
    Write-Host "     docker logs --tail 100 nexent-config"
    Write-Host "  3. Elasticsearch logs:"
    Write-Host "     docker logs --tail 100 nexent-elasticsearch"
    Write-Host "  4. PostgreSQL logs:"
    Write-Host "     docker logs --tail 100 nexent-postgresql"
    Write-Host "  5. Runtime / data-process logs:"
    Write-Host "     docker logs --tail 100 nexent-runtime"
    Write-Host "     docker logs --tail 100 nexent-data-process"
    Write-Host "  6. Environment file (credentials, URLs): $RootEnvFile"
    $dataDir = Get-EnvFileVar "ROOT_DIR"
    if (-not $dataDir) { $dataDir = Join-Path $HOME "nexent-data" }
    Write-Host "  7. Data and log directory:              $dataDir"
    Write-Host ""
    # Best-effort live status via docker compose v2.
    try {
        & docker compose -p nexent --env-file $RootEnvFile -f $composeFile ps -a
    } catch { }
}

function Wait-ContainerHealthy {
    param([string]$Name, [int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
        $out = & docker ps --filter "name=^$Name$" --filter "health=healthy" --format "{{.Names}}"
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

# Poll until PostgreSQL accepts connections (inside the container, no host
# port needed - works under both port policies).
function Wait-Postgres {
    param([int]$TimeoutSec)
    $pgUser = Get-EnvFileVar "POSTGRES_USER"
    if (-not $pgUser) { $pgUser = "root" }
    $pgDb = Get-EnvFileVar "POSTGRES_DB"
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

function Invoke-HealthPoll {
    # $WebMode "web" enforces the web UI poll (default), "noweb" skips it -
    # used by the fast path when no nexent-web container exists.
    param([string]$WebMode = "web")
    $failed = $false
    if (-not (Wait-ContainerHealthy -Name "nexent-elasticsearch" -TimeoutSec 180)) { $failed = $true }
    if (-not (Wait-Postgres -TimeoutSec 120)) { $failed = $true }
    if ($PortPolicy -eq "development") {
        if (-not (Wait-HttpEndpoint -Name "backend API (config service)" -Url "http://localhost:5010/docs" -TimeoutSec 300)) { $failed = $true }
    }
    if ($WebMode -ne "noweb") {
        if (-not (Wait-HttpEndpoint -Name "web UI" -Url "http://localhost:3000/" -TimeoutSec 300)) { $failed = $true }
    } else {
        Write-WarnMsg "No nexent-web container in this layout; web UI not polled."
    }
    return (-not $failed)
}

function Write-Summary {
    $dataDir = Get-EnvFileVar "ROOT_DIR"
    if (-not $dataDir) { $dataDir = Join-Path $HOME "nexent-data" }
    $version = Get-EnvFileVar "DEPLOYMENT_VERSION"
    if (-not $version) { $version = "speed" }
    Write-Host ""
    Write-Host "============================================================"
    Write-Host " Nexent deployment finished."
    Write-Host "============================================================"
    Write-Host " Web UI:                 http://localhost:3000"
    Write-Host " Backend API (docs):     http://localhost:5010/docs     (development policy only)"
    Write-Host " Runtime service (WS):   ws://localhost:5014            (development policy only)"
    Write-Host " Northbound API:         http://localhost:5013/api"
    Write-Host " MCP service:            http://localhost:5011          (development policy only)"
    Write-Host " MinIO console:          http://localhost:9011          (development policy only)"
    Write-Host " Supabase gateway:       http://localhost:8000          (full version only)"
    Write-Host " Credentials location:   deploy/env/.env"
    Write-Host "                         (POSTGRES_*, MINIO_*, SUPABASE_*, NEXENT_SUPER_ADMIN_PASSWORD)"
    Write-Host " Super admin account:    suadmin@nexent.com (full version; password value is"
    Write-Host "                         NEXENT_SUPER_ADMIN_PASSWORD in deploy/env/.env)"
    Write-Host " Data and log directory: $dataDir (logs under $dataDir\logs - as mounted into containers)"
    Write-Host " Uninstall:              bash deploy/uninstall.sh docker --delete-volumes false"
    Write-Host "                         (run via Git Bash; add --delete-volumes true to wipe data)"
    Write-Host "============================================================"
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Argument mapping: switches map to upstream flags; extra args are forwarded
# verbatim so upstream options (--sandbox-mode, ...) work.
# ---------------------------------------------------------------------------
if ($Production) {
    $PortPolicy = "production"
    $ComposeSuffix = ".prod.yml"
    $deployArgs += '--port-policy'; $deployArgs += 'production'
}
if ($Mainland)    { $deployArgs += '--image-source'; $deployArgs += 'mainland' }
if ($General)     { $deployArgs += '--image-source'; $deployArgs += 'general' }
if ($LocalLatest) { $deployArgs += '--image-source'; $deployArgs += 'local-latest' }
if ($RootDir)     { $deployArgs += '--root-dir'; $deployArgs += $RootDir }
if ($Passthrough) { foreach ($extra in $Passthrough) { $deployArgs += $extra } }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Write-Info "Repository root: $RepoRoot"
Test-DockerEnv
Test-ComposeCompat
Test-DiskSpace -Target $RepoRoot

# Environment bootstrap strategy: upstream deploy.sh creates deploy/env/.env
# from deploy/env/.env.example (and merges missing keys into an existing
# file) and generates all secrets (MinIO AK/SK, Supabase JWTs, ES API key)
# on first run. We only verify the template exists; generate_env.sh is NOT
# called here because it rewrites service URLs to localhost, which fits the
# host-process infrastructure mode only, not this containerized path.
if (-not (Test-Path -LiteralPath $RootEnvFile) -and -not (Test-Path -LiteralPath $RootEnvExample)) {
    Write-Fail "Neither deploy/env/.env nor deploy/env/.env.example exists; cannot bootstrap environment."
    exit 1
}
if (Test-Path -LiteralPath $RootEnvFile) {
    Write-Info "Existing deploy/env/.env found; its values will be reused (idempotent)."
} else {
    Write-Info "deploy/env/.env not found; upstream deploy.sh will create it from .env.example and generate secrets."
}

if ($PortPolicy -eq "production") {
    Test-Ports -Ports $ProdPorts
} else {
    Test-Ports -Ports $DevPorts
}

# Avoid the single interactive ROOT_DIR prompt in upstream: pass --root-dir
# only when .env does not define ROOT_DIR yet (otherwise reuse the saved one).
$hasRootDir = $false
if (Get-EnvFileVar "ROOT_DIR") { $hasRootDir = $true }
if (-not $RootDir -and -not $hasRootDir) {
    $dataDir = $env:NEXENT_ONECLICK_ROOT_DIR
    if (-not $dataDir) { $dataDir = Join-Path $HOME "nexent-data" }
    $deployArgs += '--root-dir'; $deployArgs += $dataDir
}

# Fast path: a complete existing stack is started in place (no recreate,
# no config reconciliation). The full upstream deployment only runs when
# there is nothing to start, or -Redeploy was passed explicitly.
if (-not $Redeploy -and (Test-ExistingStackComplete)) {
    Write-Info "Complete existing stack found; using the fast path (start + health only)."
    if (Invoke-FastPath) {
        Write-Summary
        Write-Info "Done (fast path). Re-run with -Redeploy to go through the full upstream deployment."
        exit 0
    }
    if (-not $script:ComposeUpstreamCompat) {
        Write-Fail "Fast path failed and the upstream deployer is not safe on this compose install (see above)."
        exit 1
    }
    Write-WarnMsg "Fast path failed; falling back to the full upstream deployment."
}

$deployArgsText = ($deployArgs -join ' ')
Write-Info "Launching upstream deployment: deploy/docker/deploy.sh $deployArgsText"
Write-Info "First run pulls several GB of images and may take a while; progress is shown below."
Write-Host "------------------------------------------------------------"

$bashCmd = Get-Command bash -ErrorAction SilentlyContinue
if (-not $bashCmd) {
    Write-Fail "bash not found on PATH. The upstream deploy script is bash-based; install Git for Windows (which ships Git Bash) and re-open the shell."
    exit 1
}
& bash $deployArgs
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Upstream deployment failed. See the upstream output above."
    Write-Diagnostics
    exit 1
}
Write-Host "------------------------------------------------------------"

Write-Info "Stack is up; polling service health..."
$healthy = Invoke-HealthPoll
if (-not $healthy) {
    Write-Fail "One or more services did not become healthy in time."
    Write-Diagnostics
    exit 1
}

Write-Summary
Write-Info "Done."
