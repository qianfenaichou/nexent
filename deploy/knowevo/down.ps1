<#
.SYNOPSIS
    KnowEvo competition stack tear-down (Windows, PowerShell 5.1+).

.DESCRIPTION
    Logic mirrors deploy/knowevo/down.sh; not executable here - reviewed by
    inspection against the .sh implementation.

    Counterpart to deploy/knowevo/up.ps1: stops and removes the containers of
    the compose project "nexent" defined by the core compose files (plus the
    Supabase trio when present). Data is preserved by default:
      - bind-mount data under the data root (PostgreSQL, Elasticsearch,
        MinIO, Redis) is never touched by this script;
      - named docker volumes are kept unless -Purge is passed (then they are
        removed together with the containers).

    The backend config API runs as a host process (port 5010) and is NOT
    started, stopped, or otherwise managed by this script.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\knowevo\down.ps1 -Purge
#>
[CmdletBinding()]
param(
    [switch]$Purge,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Passthrough
)

$ErrorActionPreference = 'Stop'

$ScriptDir        = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot         = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$ComposeDir       = Join-Path $RepoRoot "deploy\docker\compose"
$RootEnvFile      = Join-Path $RepoRoot "deploy\env\.env"
$GeneratedEnvFile = Join-Path $RepoRoot "deploy\docker\.env.generated"

function Write-Info    { param([string]$Message) Write-Host "[knowevo] $Message" }
function Write-WarnMsg { param([string]$Message) Write-Host "[knowevo] WARNING: $Message" -ForegroundColor Yellow }
function Write-Fail    { param([string]$Message) Write-Host "[knowevo] ERROR: $Message" -ForegroundColor Red }

if ($Passthrough) {
    Write-WarnMsg "unrecognized arguments ignored: $($Passthrough -join ' ')"
}

$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Write-Fail "docker CLI not found."
    exit 1
}
cmd /c "docker info >nul 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Docker daemon is not reachable. Start Docker Desktop and retry."
    exit 1
}

# Compose interpolation needs both env files; either may legitimately be
# absent on a half-deployed host, in which case compose is invoked without it.
$coreArgs = @('--env-file', $RootEnvFile, '-p', 'nexent',
    '-f', (Join-Path $ComposeDir "docker-compose.yml"),
    '-f', (Join-Path $ComposeDir "docker-compose.override.yml"))
$supabaseArgs = @('--env-file', $RootEnvFile, '-p', 'nexent',
    '-f', (Join-Path $ComposeDir "docker-compose-supabase.yml"))
if (Test-Path -LiteralPath $GeneratedEnvFile) {
    $coreArgs = @('--env-file', $GeneratedEnvFile) + $coreArgs
    $supabaseArgs = @('--env-file', $GeneratedEnvFile) + $supabaseArgs
} else {
    Write-WarnMsg "$GeneratedEnvFile not found; continuing without it (down does not need image references)."
}

$downArgs = @('down')
if ($Purge) {
    $downArgs += '-v'
    Write-Info "Purge requested: named docker volumes will be removed as well."
}

Write-Info "Stopping application and infrastructure containers..."
& docker compose @coreArgs @downArgs
if ($LASTEXITCODE -ne 0) {
    Write-Fail "docker compose down failed for the core files."
    exit 1
}

Write-Info "Stopping Supabase containers (no-op when none exist)..."
& docker compose @supabaseArgs @downArgs
if ($LASTEXITCODE -ne 0) {
    Write-Fail "docker compose down failed for the Supabase files."
    exit 1
}

Write-Host ""
Write-Info "Tear-down finished."
Write-Info "Data under the data root (PostgreSQL, Elasticsearch, MinIO, Redis bind mounts) and"
Write-Info "the deploy/env files are preserved."
Write-Info "The host backend config API (port 5010) was not touched; stop it manually if needed."
