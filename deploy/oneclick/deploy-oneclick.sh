#!/usr/bin/env bash
#
# Nexent one-click deployment (Linux/macOS).
#
# This is a THIN WRAPPER around the upstream entry point
# deploy/docker/deploy.sh --defaults. It adds non-interactive preflight
# checks (docker, compose v2, disk, port conflicts) and post-deployment
# health polling, and it deliberately delegates ALL deployment logic
# (env bootstrap, secret generation, compose up, ES API key) to the
# upstream script so behavior never drifts.
#
# Relationship to upstream deploy.sh:
#   - default path here  = deploy/docker/deploy.sh --defaults (non-interactive)
#   - advanced config    = deploy/deploy.sh --config (upstream interactive TUI)
#
# Idempotent: safe to re-run. Existing deploy/env/.env values (keys,
# ROOT_DIR) are reused by upstream logic; ports already served by Nexent
# containers are not treated as conflicts.
#
# Usage:
#   bash deploy/oneclick/deploy-oneclick.sh [options]
# Options:
#   --production          Production port policy (only 3000/5013 published)
#   --mainland            Use mainland China image sources
#   --general             Use general (global) image sources
#   --local-latest        Use locally built :latest images
#   --root-dir PATH       Data/log root dir (default: ~/nexent-data on first run)
#   -h | --help           Show this help
#   other args are forwarded verbatim to deploy/docker/deploy.sh
# Environment overrides:
#   NEXENT_ONECLICK_ROOT_DIR=PATH   data dir used when .env has no ROOT_DIR yet
#   ONECLICK_IGNORE_PORT_CONFLICTS=1  continue despite non-Nexent port conflicts

# Exit immediately on errors; bash is required for arrays.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script must be run with bash: bash deploy/oneclick/deploy-oneclick.sh" >&2
  exit 1
fi
set -euo pipefail

# --------------------------------------------------------------------------
# Path self-location: works from any CWD (repo root, deploy/oneclick, cron...)
# --------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_DOCKER_DIR="$REPO_ROOT/deploy/docker"
COMPOSE_DIR="$DEPLOY_DOCKER_DIR/compose"
ROOT_ENV_FILE="$REPO_ROOT/deploy/env/.env"
ROOT_ENV_EXAMPLE="$REPO_ROOT/deploy/env/.env.example"

# Ports published by each port policy. Must match
# deployment_compute_docker_ports() in deploy/common/common.sh and the
# "ports:" sections of deploy/docker/compose/docker-compose*.yml.
DEV_PORTS=(3000 5010 5011 5012 5013 5014 5015 5434 5436 5555 6379 8000 8443 8265 9010 9011 9210 9310)
PROD_PORTS=(3000 5013)

PORT_POLICY="development"
COMPOSE_SUFFIX=".yml"
ROOT_DIR_OPT=""
PASS_ARGS=(--defaults)   # always run the upstream non-interactive path

info() { printf '[oneclick] %s\n' "$*"; }
warn() { printf '[oneclick] WARNING: %s\n' "$*" >&2; }
err()  { printf '[oneclick] ERROR: %s\n' "$*" >&2; }

usage() {
  cat <<'USAGE'
Usage: bash deploy/oneclick/deploy-oneclick.sh [options]

Options:
  --production          Production port policy (only ports 3000/5013 published)
  --mainland            Use mainland China image sources
  --general             Use general (global) image sources
  --local-latest        Use locally built :latest images
  --root-dir PATH       Data/log root dir (default: ~/nexent-data on first run)
  --redeploy            Skip the existing-stack fast path and run the full
                        upstream deployment (may recreate containers)
  -h | --help           Show this help

Any other option (e.g. --sandbox-mode full) is forwarded verbatim to
deploy/docker/deploy.sh. Environment overrides:
  NEXENT_ONECLICK_ROOT_DIR=PATH     data dir used when .env has no ROOT_DIR yet
  ONECLICK_IGNORE_PORT_CONFLICTS=1  continue despite non-Nexent port conflicts

Uninstall: bash deploy/uninstall.sh docker --delete-volumes false
USAGE
}

# Read the last value of KEY from deploy/env/.env (strip quotes and CR).
env_var() {
  local key="$1" default="${2:-}" line value
  [ -f "$ROOT_ENV_FILE" ] || { printf '%s' "$default"; return 0; }
  line="$(grep -E "^${key}=" "$ROOT_ENV_FILE" | tail -n 1 || true)"
  if [ -z "$line" ]; then
    printf '%s' "$default"
    return 0
  fi
  value="${line#*=}"
  value="${value%$'\r'}"
  value="${value%\"}"
  value="${value#\"}"
  printf '%s' "$value"
}

env_has_var() {
  [ -f "$ROOT_ENV_FILE" ] && grep -qE "^$1=" "$ROOT_ENV_FILE"
}

# TCP port in use? Mirrors upstream is_port_in_use() (deploy/common/common.sh):
# lsof -> ss -> netstat -> assume free.
port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN -P -n >/dev/null 2>&1 && return 0
    return 1
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}$" && return 0
    return 1
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -an 2>/dev/null | grep -qE "[:.]${port}[[:space:]]" && return 0
    return 1
  fi
  return 1
}

# True when every docker container publishing PORT is a Nexent container.
# Mirrors upstream is_nexent_container_name() (deploy/common/common.sh).
port_owned_by_nexent() {
  local port="$1" name ports found="false" out
  out="$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null || true)"
  [ -n "$out" ] || return 1
  while IFS=$'\t' read -r name ports; do
    [ -n "${name:-}" ] || continue
    case "${ports:-}" in
      *":${port}->"*)
        found="true"
        case "$name" in
          nexent-*|nexent_*|supabase-*-mini) ;;    # Nexent-owned, ignore
          *) return 1 ;;                            # foreign container
        esac
        ;;
    esac
  done <<EOF
$out
EOF
  [ "$found" = "true" ]
}

check_docker() {
  command -v docker >/dev/null 2>&1 || {
    err "docker CLI not found. Install Docker first (https://docs.docker.com/get-docker/)."
    exit 1
  }
  docker info >/dev/null 2>&1 || {
    err "Docker daemon is not reachable. Start Docker (Docker Desktop / systemctl start docker) and retry."
    exit 1
  }
  if ! docker compose version >/dev/null 2>&1; then
    err "docker compose v2 plugin not found. Install docker-compose-plugin (v1 'docker-compose' is not supported)."
    exit 1
  fi
  info "Docker and docker compose v2 detected."
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
COMPOSE_UPSTREAM_COMPAT="false"
check_compose_compat() {
  local out
  out="$(docker compose version 2>/dev/null || true)"
  if [[ "$out" =~ v([0-9]+\.[0-9]+\.[0-9]+) ]]; then
    info "Compose v2 (${BASH_REMATCH[1]}) is recognized by the upstream version probe."
    COMPOSE_UPSTREAM_COMPAT="true"
    return 0
  fi
  warn "Distro compose v2 detected (\"$out\"): the upstream deployer cannot parse this"
  warn "version string and would fall back to compose v1, which crashes on recreate."
  if [ "${FASTPATH_DISABLE:-false}" != "true" ] && existing_stack_complete; then
    warn "A complete existing stack was found: the fast path below avoids recreate entirely."
    return 0
  fi
  err "Refusing to continue: fix the install first (official plugin: https://docs.docker.com/compose/install/linux/),"
  err "or re-run with --redeploy to force the upstream path at your own risk."
  exit 1
}

# Containers of a complete stack, in two waves: infrastructure must be
# healthy before application containers start (the config service runs SQL
# migrations at boot and aborts if PostgreSQL is not reachable yet).
# When ALL of them already exist, a re-run only needs to start stopped ones
# and poll health: going through the upstream deploy path reconciles config
# and may recreate containers, which is exactly what compose v1 cannot do
# safely (see check_compose_compat).
FASTPATH_INFRA=(nexent-postgresql nexent-elasticsearch nexent-redis
  nexent-minio)
FASTPATH_APPS=(nexent-config nexent-runtime nexent-northbound nexent-mcp
  nexent-data-process nexent-web)

container_ports() {
  case "$1" in
    nexent-postgresql)   echo 5434 ;;
    nexent-elasticsearch) echo 9210 9310 ;;
    nexent-redis)        echo 6379 ;;
    nexent-minio)        echo 9010 9011 ;;
    nexent-config)       echo 5010 ;;
    nexent-runtime)      echo 5014 ;;
    nexent-northbound)   echo 5013 ;;
    nexent-mcp)          echo 5011 5015 ;;
    nexent-data-process) echo 5012 5555 8265 ;;
    nexent-web)          echo 3000 ;;
    *)                   echo "" ;;
  esac
}

existing_stack_complete() {
  local name
  for name in "${FASTPATH_INFRA[@]}" "${FASTPATH_APPS[@]}"; do
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$name" || return 1
  done
  return 0
}

# Start every stopped container of one wave, skipping containers whose host
# port is held by a NON-Nexent process (e.g. a host uvicorn/npm dev
# process): starting them would crash on bind.
start_wave() {
  local name port foreign started=() skipped=()
  for name in "$@"; do
    [ "$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null)" = "running" ] && continue
    foreign="false"
    for port in $(container_ports "$name"); do
      if port_in_use "$port" && ! port_owned_by_nexent "$port"; then
        foreign="true"
        break
      fi
    done
    if [ "$foreign" = "true" ]; then
      skipped+=("$name")
      continue
    fi
    if docker start "$name" >/dev/null 2>&1; then
      started+=("$name")
    else
      warn "Could not start $name; it may need the full deploy path."
    fi
  done
  [ ${#started[@]} -gt 0 ] && info "Started: ${started[*]}"
  [ ${#skipped[@]} -gt 0 ] && warn "Skipped (a host process holds their ports): ${skipped[*]}"
}

# Start stopped containers of an existing stack in place - never recreate.
# Wave 1: infrastructure, waited until healthy. Wave 2: application
# containers (safe to boot now: SQL migrations will find PostgreSQL up).
try_existing_stack_fastpath() {
  start_wave "${FASTPATH_INFRA[@]}"
  if ! wait_for_container_healthy nexent-elasticsearch 180; then
    print_diagnostics
    return 1
  fi
  if ! wait_for_postgres 120; then
    print_diagnostics
    return 1
  fi
  start_wave "${FASTPATH_APPS[@]}"
  # If a host process holds 3000 the web container was skipped above and
  # the host process serves the UI instead; the poll then checks that.
  if ! poll_health; then
    err "Existing stack did not become healthy in time."
    print_diagnostics
    return 1
  fi
  return 0
}

check_disk() {
  local target="$1" avail_kb docker_root
  docker_root="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)"
  if [ -n "$docker_root" ] && [ -d "$docker_root" ]; then
    target="$docker_root"
  fi
  avail_kb="$(df -Pk "$target" 2>/dev/null | awk 'NR==2 {print $4}' || true)"
  if [ -z "$avail_kb" ]; then
    warn "Cannot determine free disk space for $target; skipping disk check."
    return 0
  fi
  # Images for the full stack are several GB; 20 GiB is a comfortable floor.
  if [ "$avail_kb" -lt 2097152 ]; then
    err "Less than 2 GiB free on $target. Free space before deploying."
    exit 1
  fi
  if [ "$avail_kb" -lt 20971520 ]; then
    warn "Less than 20 GiB free on $target; image pulls may fail on a first install."
  else
    info "Disk check passed ($(("$avail_kb" / 1048576)) GiB free on $target)."
  fi
}

check_ports() {
  local ports=("$@") port occupied=()
  command -v docker >/dev/null 2>&1 || return 0
  for port in "${ports[@]}"; do
    if ! port_in_use "$port"; then
      continue
    fi
    if port_owned_by_nexent "$port"; then
      info "Port $port already served by Nexent containers (ok for re-run)."
      continue
    fi
    occupied+=("$port")
  done
  if [ "${#occupied[@]}" -gt 0 ]; then
    warn "Ports required by Nexent are held by OTHER processes: ${occupied[*]}"
    if [ "${ONECLICK_IGNORE_PORT_CONFLICTS:-false}" = "true" ]; then
      warn "ONECLICK_IGNORE_PORT_CONFLICTS=1 set; continuing despite conflicts."
    else
      err "Free the ports (see README troubleshooting) or re-run with ONECLICK_IGNORE_PORT_CONFLICTS=1."
      err "Interactive override is also available via the upstream TUI: bash deploy/deploy.sh --config"
      exit 1
    fi
  else
    info "Port preflight passed (${#ports[@]} ports checked)."
  fi
}

# Diagnostics printed when the upstream deployment or a health poll fails.
print_diagnostics() {
  local compose_file="$COMPOSE_DIR/docker-compose$COMPOSE_SUFFIX"
  cat <<EOF

[oneclick] Diagnostics:
  1. Container status:
     docker compose -p nexent --env-file "$ROOT_ENV_FILE" -f "$compose_file" ps -a
  2. Backend (config service) logs:
     docker logs --tail 100 nexent-config
  3. Elasticsearch logs:
     docker logs --tail 100 nexent-elasticsearch
  4. PostgreSQL logs:
     docker logs --tail 100 nexent-postgresql
  5. Runtime / data-process logs:
     docker logs --tail 100 nexent-runtime
     docker logs --tail 100 nexent-data-process
  6. Environment file (credentials, URLs): $ROOT_ENV_FILE
  7. Data and log directory:              $(env_var ROOT_DIR "$HOME/nexent-data")
  Elasticsearch on Linux often needs: sysctl -w vm.max_map_count=262144

EOF
}

# Poll until a docker compose healthcheck reports healthy.
wait_for_container_healthy() {
  local name="$1" timeout_s="$2" status deadline
  deadline=$(( $(date +%s) + timeout_s ))
  while true; do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || echo missing)"
    if [ "$status" = "healthy" ]; then
      info "$name is healthy."
      return 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      err "Timed out waiting for $name to become healthy (last status: $status)."
      return 1
    fi
    sleep 5
  done
}

# Poll until PostgreSQL accepts connections (inside the container, no host
# port needed - works under both port policies).
wait_for_postgres() {
  local timeout_s="$1" deadline
  local pg_user pg_db
  pg_user="$(env_var POSTGRES_USER root)"
  pg_db="$(env_var POSTGRES_DB nexent)"
  deadline=$(( $(date +%s) + timeout_s ))
  while true; do
    if docker exec nexent-postgresql pg_isready -U "$pg_user" -d "$pg_db" >/dev/null 2>&1; then
      info "PostgreSQL is accepting connections."
      return 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      err "Timed out waiting for PostgreSQL (nexent-postgresql) after ${timeout_s}s."
      return 1
    fi
    sleep 5
  done
}

# Poll an HTTP endpoint until it answers 2xx/3xx.
wait_for_http() {
  local name="$1" url="$2" timeout_s="$3" deadline
  if ! command -v curl >/dev/null 2>&1; then
    warn "curl not found; skipping HTTP health check for $name ($url)."
    return 0
  fi
  deadline=$(( $(date +%s) + timeout_s ))
  while true; do
    if curl -fsS -o /dev/null --max-time 5 "$url" 2>/dev/null; then
      info "$name is up: $url"
      return 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      err "Timed out waiting for $name at $url after ${timeout_s}s."
      return 1
    fi
    sleep 5
  done
}

poll_health() {
  # $1 (optional): "web" enforces the web UI poll (default), "noweb" skips
  # it - used by the fast path when no nexent-web container exists.
  local web_mode="${1:-web}" failed="false"
  wait_for_container_healthy nexent-elasticsearch 180 || failed="true"
  wait_for_postgres 120 || failed="true"
  if [ "$PORT_POLICY" = "development" ]; then
    wait_for_http "backend API (config service)" "http://localhost:5010/docs" 300 || failed="true"
  fi
  if [ "$web_mode" != "noweb" ]; then
    wait_for_http "web UI" "http://localhost:3000/" 300 || failed="true"
  else
    warn "No nexent-web container in this layout; web UI not polled."
  fi
  [ "$failed" = "false" ]
}

print_summary() {
  local root_dir version
  root_dir="$(env_var ROOT_DIR "$HOME/nexent-data")"
  version="$(env_var DEPLOYMENT_VERSION speed)"
  cat <<EOF

============================================================
 Nexent deployment finished.
============================================================
 Web UI:                 http://localhost:3000
 Backend API (docs):     http://localhost:5010/docs     (development policy only)
 Runtime service (WS):   ws://localhost:5014            (development policy only)
 Northbound API:         http://localhost:5013/api
 MCP service:            http://localhost:5011          (development policy only)
 MinIO console:          http://localhost:9011          (development policy only)
 Supabase gateway:       http://localhost:8000          (full version only)
 Credentials location:   deploy/env/.env
                         (POSTGRES_*, MINIO_*, SUPABASE_*, NEXENT_SUPER_ADMIN_PASSWORD)
 Super admin account:    suadmin@nexent.com (full version; password value is
                         NEXENT_SUPER_ADMIN_PASSWORD in deploy/env/.env)
 Data and log directory: $root_dir (logs under $root_dir/logs)
 Uninstall:              bash deploy/uninstall.sh docker --delete-volumes false
                         (add --delete-volumes true to also wipe data)
============================================================

EOF
}

# --------------------------------------------------------------------------
# Argument parsing: oneclick flags are mapped to upstream flags; anything
# else is forwarded verbatim so upstream options (--sandbox-mode, ...) work.
# --------------------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --production)
      PORT_POLICY="production"
      COMPOSE_SUFFIX=".prod.yml"
      PASS_ARGS+=(--port-policy production)
      shift
      ;;
    --mainland)
      PASS_ARGS+=(--image-source mainland)
      shift
      ;;
    --general)
      PASS_ARGS+=(--image-source general)
      shift
      ;;
    --local-latest)
      PASS_ARGS+=(--image-source local-latest)
      shift
      ;;
    --root-dir)
      [ $# -ge 2 ] || { err "--root-dir requires a value"; exit 1; }
      ROOT_DIR_OPT="$2"
      PASS_ARGS+=(--root-dir "$2")
      shift 2
      ;;
    --redeploy)
      FASTPATH_DISABLE="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      PASS_ARGS+=("$1")
      shift
      ;;
  esac
done

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
info "Repository root: $REPO_ROOT"
check_docker
check_compose_compat
check_disk "$REPO_ROOT"

# Environment bootstrap strategy: upstream deploy.sh creates deploy/env/.env
# from deploy/env/.env.example (and merges missing keys into an existing
# file) and generates all secrets (MinIO AK/SK, Supabase JWTs, ES API key)
# on first run. We only verify the template exists; generate_env.sh is NOT
# called here because it rewrites service URLs to localhost, which fits the
# host-process infrastructure mode only, not this containerized path.
if [ ! -f "$ROOT_ENV_FILE" ] && [ ! -f "$ROOT_ENV_EXAMPLE" ]; then
  err "Neither $ROOT_ENV_FILE nor $ROOT_ENV_EXAMPLE exists; cannot bootstrap environment."
  exit 1
fi
if [ -f "$ROOT_ENV_FILE" ]; then
  info "Existing deploy/env/.env found; its values will be reused (idempotent)."
else
  info "deploy/env/.env not found; upstream deploy.sh will create it from .env.example and generate secrets."
fi

if [ "$PORT_POLICY" = "production" ]; then
  check_ports "${PROD_PORTS[@]}"
else
  check_ports "${DEV_PORTS[@]}"
fi

# Avoid the single interactive ROOT_DIR prompt in upstream: pass --root-dir
# only when .env does not define ROOT_DIR yet (otherwise reuse the saved one).
if [ -z "$ROOT_DIR_OPT" ] && ! env_has_var ROOT_DIR; then
  PASS_ARGS+=(--root-dir "${NEXENT_ONECLICK_ROOT_DIR:-$HOME/nexent-data}")
fi

# Fast path: a complete existing stack is started in place (no recreate,
# no config reconciliation). The full upstream deployment only runs when
# there is nothing to start, or --redeploy was passed explicitly.
if [ "${FASTPATH_DISABLE:-false}" != "true" ] && existing_stack_complete; then
  info "Complete existing stack found; using the fast path (start + health only)."
  if try_existing_stack_fastpath; then
    print_summary
    info "Done (fast path). Re-run with --redeploy to go through the full upstream deployment."
    exit 0
  fi
  if [ "$COMPOSE_UPSTREAM_COMPAT" != "true" ]; then
    err "Fast path failed and the upstream deployer is not safe on this compose install (see above)."
    exit 1
  fi
  warn "Fast path failed; falling back to the full upstream deployment."
fi

info "Launching upstream deployment: deploy/docker/deploy.sh ${PASS_ARGS[*]:-}"
info "First run pulls several GB of images and may take a while; progress is shown below."
echo "------------------------------------------------------------"
if ! bash "$DEPLOY_DOCKER_DIR/deploy.sh" "${PASS_ARGS[@]}"; then
  err "Upstream deployment failed. See the upstream output above."
  print_diagnostics
  exit 1
fi
echo "------------------------------------------------------------"

info "Stack is up; polling service health..."
if ! poll_health; then
  err "One or more services did not become healthy in time."
  print_diagnostics
  exit 1
fi

print_summary
info "Done."
