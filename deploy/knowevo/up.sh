#!/usr/bin/env bash
#
# KnowEvo competition stack bring-up (Linux/macOS).
#
# Brings up the container side of the KnowEvo stack under the compose project
# name "nexent", matching the layout this fork is developed against:
#   - infrastructure: nexent-postgresql (host 5434), nexent-elasticsearch
#     (9210/9310), nexent-minio (9010/9011), redis (6379)
#   - application:    nexent-mcp (5011/5015), nexent-runtime (5014),
#                     nexent-northbound (5013)
#   - auth:           supabase kong/auth/db (8000/8443, 5436) by default;
#                     skip with --no-supabase
#
# The backend config API (port 5010) is NOT part of the container set in this
# stack: it runs as a host process from backend/ (see the summary output for
# the host-mode environment it expects). For a fully containerized alternative
# use --with-config, and --with-web for the bundled web UI container.
#
# Compose posture (must match the deployed stack):
#   - compose v2 only; two env files are passed together: deploy/env/.env
#     (business values, secrets) and deploy/docker/.env.generated (image
#     references). The business env file intentionally carries no IMAGE
#     variables, so compose fails fast without the second file.
#   - explicit -f lists: docker-compose.yml plus docker-compose.override.yml.
#     The override pins nexent-mcp to the locally built fork image and sets
#     KW_MCP_TENANT_ID; an explicit -f list never auto-loads the override, so
#     both files must always be listed.
#   - NEXENT_USER_DIR is exported explicitly: compose does not expand the
#     nested default ${NEXENT_USER_DIR:-$HOME/nexent} reliably.
#
# Idempotent: when all target containers already exist they are only started
# if stopped and then polled for health; nothing is recreated. Pass --redeploy
# to force the compose up path (recreates containers whose definition changed).
#
# Usage:
#   bash deploy/knowevo/up.sh [options]
# Options:
#   --with-config   also start the containerized config service (nexent-config,
#                   port 5010); refused when a foreign process already binds it
#   --with-web      also start the web UI container (nexent-web, port 3000);
#                   the UI expects the config service at http://nexent-config:5010
#   --no-supabase   skip the Supabase auth stack
#   --redeploy      run docker compose up even when the stack already exists
#   -h | --help     show this help

# bash is required for arrays and /dev/tcp health probes.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script must be run with bash: bash deploy/knowevo/up.sh" >&2
  exit 1
fi
set -euo pipefail

# --------------------------------------------------------------------------
# Path self-location: works from any CWD (repo root, deploy/knowevo, cron...)
# --------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_DIR="$REPO_ROOT/deploy/docker/compose"
ASSETS_DIR="$REPO_ROOT/deploy/docker/assets"
ROOT_ENV_FILE="$REPO_ROOT/deploy/env/.env"
ROOT_ENV_EXAMPLE="$REPO_ROOT/deploy/env/.env.example"
GENERATED_ENV_FILE="$REPO_ROOT/deploy/docker/.env.generated"

# Compose argument arrays (space-safe: never expand them unquoted).
CORE_COMPOSE_ARGS=(
  --env-file "$ROOT_ENV_FILE"
  --env-file "$GENERATED_ENV_FILE"
  -p nexent
  -f "$COMPOSE_DIR/docker-compose.yml"
  -f "$COMPOSE_DIR/docker-compose.override.yml"
)
SUPABASE_COMPOSE_ARGS=(
  --env-file "$ROOT_ENV_FILE"
  --env-file "$GENERATED_ENV_FILE"
  -p nexent
  -f "$COMPOSE_DIR/docker-compose-supabase.yml"
)

# Host ports published by this stack. Must match the "ports:" sections of
# deploy/docker/compose/docker-compose*.yml. 5010/3000 belong to the optional
# config/web containers and are only preflighted when those are requested.
BASE_PORTS=(5434 9210 9310 6379 9010 9011 5436 8000 8443 5011 5013 5014 5015)

# Default container set. nexent-config and nexent-data-process are NOT part of
# the KnowEvo stack (the config API runs on the host; data-process is unused).
INFRA_CONTAINERS=(nexent-postgresql nexent-elasticsearch nexent-minio nexent-redis)
APP_CONTAINERS=(nexent-runtime nexent-northbound nexent-mcp)
SUPABASE_CONTAINERS=(supabase-kong-mini supabase-auth-mini supabase-db-mini)

WITH_CONFIG="false"
WITH_WEB="false"
WITH_SUPABASE="true"
REDEPLOY="false"

info() { printf '[knowevo] %s\n' "$*"; }
warn() { printf '[knowevo] WARNING: %s\n' "$*" >&2; }
err()  { printf '[knowevo] ERROR: %s\n' "$*" >&2; }

usage() {
  cat <<'USAGE'
Usage: bash deploy/knowevo/up.sh [options]

Options:
  --with-config   also start the containerized config service (nexent-config, port 5010)
  --with-web      also start the web UI container (nexent-web, port 3000)
  --no-supabase   skip the Supabase auth stack (kong/auth/db)
  --redeploy      run docker compose up even when the stack already exists
  -h | --help     show this help

Tear down: bash deploy/knowevo/down.sh [--purge]
USAGE
}

# --------------------------------------------------------------------------
# Read the last value of KEY from deploy/env/.env (strip quotes and CR).
# --------------------------------------------------------------------------
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

# Append KEY=VALUE only when the key is absent; existing values are never
# rewritten so re-runs and hand-tuned files stay untouched.
append_env_var() {
  local key="$1" value="$2"
  if env_has_var "$key"; then
    return 0
  fi
  printf '%s=%s\n' "$key" "$value" >> "$ROOT_ENV_FILE"
  info "deploy/env/.env: added $key"
}

# --------------------------------------------------------------------------
# Docker preflight (same gates as deploy/oneclick).
# --------------------------------------------------------------------------
check_docker() {
  command -v docker >/dev/null 2>&1 || {
    err "docker CLI not found. Install Docker first (https://docs.docker.com/get-docker/)."
    exit 1
  }
  docker info >/dev/null 2>&1 || {
    err "Docker daemon is not reachable. Start Docker (Docker Desktop / systemctl start docker) and retry."
    exit 1
  }
  docker compose version >/dev/null 2>&1 || {
    err "docker compose v2 plugin not found. Install docker-compose-plugin (v1 'docker-compose' is not supported)."
    exit 1
  }
  info "Docker and docker compose v2 detected."
}

# TCP port in use? lsof -> ss -> netstat -> assume free.
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

# True when every docker container publishing PORT is one of ours.
port_owned_by_stack() {
  local port="$1" name ports found="false" out
  out="$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null || true)"
  [ -n "$out" ] || return 1
  while IFS=$'\t' read -r name ports; do
    [ -n "${name:-}" ] || continue
    case "${ports:-}" in
      *":${port}->"*)
        found="true"
        case "$name" in
          nexent-*|nexent_*|supabase-*-mini) ;;
          *) return 1 ;;
        esac
        ;;
    esac
  done <<EOF
$out
EOF
  [ "$found" = "true" ]
}

check_ports() {
  local ports=("$@") port occupied=()
  for port in "${ports[@]}"; do
    if ! port_in_use "$port"; then
      continue
    fi
    if port_owned_by_stack "$port"; then
      info "Port $port already served by the stack (ok for re-run)."
      continue
    fi
    occupied+=("$port")
  done
  if [ "${#occupied[@]}" -gt 0 ]; then
    err "Ports required by the stack are held by OTHER processes: ${occupied[*]}"
    err "Free them or stop the owning services before retrying."
    exit 1
  fi
  info "Port preflight passed (${#ports[@]} ports checked)."
}

# --------------------------------------------------------------------------
# Environment bootstrap: create deploy/env/.env from the template when missing
# and fill only missing keys (secrets, KnowEvo model ids, data/log roots).
# Existing values are never rewritten, so this is a no-op on a deployed host.
# --------------------------------------------------------------------------
generate_jwt() {
  # HS256 JWT for the Supabase anon/service_role keys, same shape as the
  # upstream deployer produces.
  local role="$1" now header payload header_b64 payload_b64 signature
  now="$(date +%s)"
  header='{"alg":"HS256","typ":"JWT"}'
  payload="{\"role\":\"$role\",\"iss\":\"supabase\",\"iat\":$now,\"exp\":$((now + 157680000))}"
  header_b64="$(printf '%s' "$header" | base64 | tr -d '\n=' | tr '/+' '_-')"
  payload_b64="$(printf '%s' "$payload" | base64 | tr -d '\n=' | tr '/+' '_-')"
  signature="$(printf '%s' "$header_b64.$payload_b64" \
    | openssl dgst -sha256 -hmac "$(env_var JWT_SECRET)" -binary \
    | base64 | tr -d '\n=' | tr '/+' '_-')"
  printf '%s.%s.%s' "$header_b64" "$payload_b64" "$signature"
}

prepare_env_files() {
  if [ ! -f "$ROOT_ENV_FILE" ]; then
    if [ ! -f "$ROOT_ENV_EXAMPLE" ]; then
      err "Neither $ROOT_ENV_FILE nor $ROOT_ENV_EXAMPLE exists; cannot bootstrap environment."
      exit 1
    fi
    cp "$ROOT_ENV_EXAMPLE" "$ROOT_ENV_FILE"
    info "deploy/env/.env created from .env.example (default credentials; change them before exposing anything)."
  else
    info "Existing deploy/env/.env found; its values will be reused (idempotent)."
  fi

  # Secrets: generated only when absent, mirroring the upstream deployer.
  command -v openssl >/dev/null 2>&1 || {
    err "openssl not found; required to generate missing secrets."
    exit 1
  }
  if ! env_has_var MINIO_ACCESS_KEY || ! env_has_var MINIO_SECRET_KEY; then
    append_env_var MINIO_ACCESS_KEY "$(openssl rand -hex 12 | tr -d '\r\n' | sed 's/[^a-zA-Z0-9]//g')"
    append_env_var MINIO_SECRET_KEY "$(openssl rand -base64 32 | tr -d '\r\n' | sed 's/[^a-zA-Z0-9+/=]//g')"
    info "MinIO access keys generated."
  fi
  if [ "$WITH_SUPABASE" = "true" ]; then
    if ! env_has_var JWT_SECRET; then
      append_env_var JWT_SECRET "$(openssl rand -base64 32 | tr -d '[:space:]')"
      info "JWT_SECRET generated."
    fi
    append_env_var SECRET_KEY_BASE "$(openssl rand -base64 64 | tr -d '[:space:]')"
    append_env_var VAULT_ENC_KEY "$(openssl rand -base64 32 | tr -d '[:space:]')"
    if ! env_has_var SUPABASE_KEY; then
      append_env_var SUPABASE_KEY "$(generate_jwt anon)"
    fi
    if ! env_has_var SERVICE_ROLE_KEY; then
      append_env_var SERVICE_ROLE_KEY "$(generate_jwt service_role)"
    fi
    info "Supabase secrets present."
  fi

  # KnowEvo model ids (platform model table row ids used by the pipeline).
  append_env_var KW_LLM_SMALL_MODEL_ID "7"
  append_env_var KW_LLM_MID_MODEL_ID "7"
  append_env_var KW_LLM_LARGE_MODEL_ID "8"

  # Data/log roots. Compose bind-mounts live under ROOT_DIR; LOG_DIR is the
  # in-container path that maps to ${ROOT_DIR}/logs.
  append_env_var ROOT_DIR "${NEXENT_ONECLICK_ROOT_DIR:-$HOME/nexent-data}"
  append_env_var LOG_DIR "/mnt/nexent-data/logs"

  # Image references live in a separate file (the business env carries none).
  if [ ! -f "$GENERATED_ENV_FILE" ]; then
    cat > "$GENERATED_ENV_FILE" <<'EOF'
# Image references for the compose files (interpolation only; never mounted
# into containers). Written once by deploy/knowevo/up.sh when missing; an
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
EOF
    info "deploy/docker/.env.generated created with reference image tags."
  else
    info "Existing deploy/docker/.env.generated found; reused as-is."
  fi

  # Fail before any compose call when an image variable is missing: without it
  # compose would render an empty image name and fail mid-deploy instead.
  local missing=() key
  for key in NEXENT_IMAGE ELASTICSEARCH_IMAGE POSTGRESQL_IMAGE REDIS_IMAGE \
             MINIO_IMAGE SUPABASE_KONG SUPABASE_GOTRUE SUPABASE_DB; do
    grep -qE "^${key}=" "$GENERATED_ENV_FILE" || missing+=("$key")
  done
  if [ "$WITH_WEB" = "true" ]; then
    grep -qE '^NEXENT_WEB_IMAGE=' "$GENERATED_ENV_FILE" || missing+=("NEXENT_WEB_IMAGE")
  fi
  if [ "${#missing[@]}" -gt 0 ]; then
    err "deploy/docker/.env.generated is missing image variables: ${missing[*]}"
    err "Fix or remove the file so it can be regenerated from the reference template."
    exit 1
  fi
}

# --------------------------------------------------------------------------
# Data-root bootstrap: bind-mount targets the compose files expect. The kong
# declarative config is a FILE mount - if absent, docker would create a
# directory of that name and kong would crash-loop.
# --------------------------------------------------------------------------
prepare_data_root() {
  local root_dir
  root_dir="$(env_var ROOT_DIR "$HOME/nexent-data")"
  mkdir -p "$root_dir"/{logs,skills,memory-provider-plugins,scripts,openssh-server/ssh-keys,volumes/api,volumes/db/data}
  if [ ! -f "$root_dir/volumes/api/kong.yml" ] && [ -f "$ASSETS_DIR/volumes/api/kong.yml" ]; then
    cp "$ASSETS_DIR/volumes/api/kong.yml" "$root_dir/volumes/api/kong.yml"
    info "kong.yml copied into $root_dir/volumes/api/."
  fi
  if [ ! -f "$root_dir/scripts/sync_user_supabase2pg.py" ] \
    && [ -f "$ASSETS_DIR/scripts/sync_user_supabase2pg.py" ]; then
    cp "$ASSETS_DIR/scripts/sync_user_supabase2pg.py" "$root_dir/scripts/"
  fi
  info "Data root ready: $root_dir"
}

# --------------------------------------------------------------------------
# The override file pins nexent-mcp to a locally built fork image (never
# pushed). Without it the mcp container cannot start, so check before deploy.
# --------------------------------------------------------------------------
fork_image_tag() {
  grep -oE 'nexent/nexent:knowevo-[0-9]+' "$COMPOSE_DIR/docker-compose.override.yml" 2>/dev/null | head -n 1
}

check_fork_image() {
  local tag
  tag="$(fork_image_tag)"
  if [ -z "$tag" ]; then
    warn "No fork image pin found in docker-compose.override.yml; nexent-mcp will use the upstream image."
    return 0
  fi
  if docker image inspect "$tag" >/dev/null 2>&1; then
    info "Fork image present: $tag"
    return 0
  fi
  err "Fork image $tag (pinned by docker-compose.override.yml) not found locally."
  err "Build it from the repository root first, e.g.:"
  err "  docker build -f deploy/images/dockerfiles/main/Dockerfile.knowevo -t $tag ."
  return 1
}

# --------------------------------------------------------------------------
# Health polls with timeouts. Container health where a healthcheck exists,
# HTTP/TCP probes on the published ports otherwise (endpoints verified against
# the running stack).
# --------------------------------------------------------------------------
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

wait_for_postgres() {
  local timeout_s="$1" deadline pg_user pg_db
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

wait_for_tcp() {
  local name="$1" port="$2" timeout_s="$3" deadline
  deadline=$(( $(date +%s) + timeout_s ))
  while true; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      info "$name is accepting connections on port $port."
      return 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      err "Timed out waiting for $name on port $port after ${timeout_s}s."
      return 1
    fi
    sleep 5
  done
}

poll_health() {
  local failed="false"
  wait_for_container_healthy nexent-elasticsearch 300 || failed="true"
  wait_for_postgres 120 || failed="true"
  wait_for_container_healthy nexent-redis 60 || failed="true"
  wait_for_http "MinIO API" "http://127.0.0.1:9010/minio/health/live" 120 || failed="true"
  if [ "$WITH_SUPABASE" = "true" ]; then
    wait_for_container_healthy supabase-db-mini 180 || failed="true"
    wait_for_container_healthy supabase-auth-mini 180 || failed="true"
    wait_for_container_healthy supabase-kong-mini 180 || failed="true"
  fi
  wait_for_http "runtime service" "http://localhost:5014/docs" 180 || failed="true"
  wait_for_http "northbound API" "http://localhost:5013/api/docs" 180 || failed="true"
  wait_for_tcp "MCP service" 5011 180 || failed="true"
  wait_for_http "MCP management API" "http://localhost:5015/docs" 120 || failed="true"
  if [ "$WITH_CONFIG" = "true" ]; then
    wait_for_http "config service (container)" "http://localhost:5010/docs" 300 || failed="true"
  fi
  if [ "$WITH_WEB" = "true" ]; then
    wait_for_http "web UI" "http://localhost:3000/" 180 || failed="true"
  fi
  [ "$failed" = "false" ]
}

# Create the Elasticsearch API key the backend expects, but only when the
# environment does not have one yet; existing keys are never overwritten.
ensure_es_api_key() {
  if env_has_var ELASTICSEARCH_API_KEY; then
    return 0
  fi
  local elastic_password api_key_json api_key
  elastic_password="$(env_var ELASTIC_PASSWORD nexent@2025)"
  api_key_json="$(docker exec nexent-elasticsearch curl -s \
    -u "elastic:${elastic_password}" \
    -H "Content-Type: application/json" \
    -d '{"name":"knowevo_deploy_key","role_descriptors":{"knowevo_role":{"cluster":["all"],"index":[{"names":["*"],"privileges":["all"]}]}}}' \
    "http://localhost:9200/_security/api_key" 2>/dev/null || true)"
  api_key="$(printf '%s' "$api_key_json" | grep -o '"encoded":"[^"]*"' | awk -F'"' '{print $4}' || true)"
  if [ -n "$api_key" ]; then
    append_env_var ELASTICSEARCH_API_KEY "$api_key"
    info "ELASTICSEARCH_API_KEY generated and stored in deploy/env/.env."
  else
    warn "Could not generate an Elasticsearch API key; the backend may need one to talk to ES."
  fi
}

# --------------------------------------------------------------------------
# Fast path: every target container already exists (deployed stack). Start the
# stopped ones in place - never recreate - then poll health.
# --------------------------------------------------------------------------
container_ports() {
  case "$1" in
    nexent-postgresql)    echo 5434 ;;
    nexent-elasticsearch) echo 9210 9310 ;;
    nexent-redis)         echo 6379 ;;
    nexent-minio)         echo 9010 9011 ;;
    nexent-runtime)       echo 5014 ;;
    nexent-northbound)    echo 5013 ;;
    nexent-mcp)           echo 5011 5015 ;;
    nexent-config)        echo 5010 ;;
    nexent-web)           echo 3000 ;;
    supabase-kong-mini)   echo 8000 8443 ;;
    supabase-db-mini)     echo 5436 ;;
    supabase-auth-mini)   echo "" ;;
    *)                    echo "" ;;
  esac
}

# Start every stopped container of one wave, skipping containers whose host
# port is held by a foreign process (starting them would crash on bind).
start_wave() {
  local name port foreign started=() skipped=()
  for name in "$@"; do
    [ "$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null)" = "running" ] && continue
    foreign="false"
    for port in $(container_ports "$name"); do
      [ -n "$port" ] || continue
      if port_in_use "$port" && ! port_owned_by_stack "$port"; then
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
      warn "Could not start $name; run the full deploy path (--redeploy)."
    fi
  done
  [ ${#started[@]} -gt 0 ] && info "Started: ${started[*]}"
  [ ${#skipped[@]} -gt 0 ] && warn "Skipped (a foreign process holds their ports): ${skipped[*]}"
  return 0
}

target_containers() {
  local names=()
  names+=("${INFRA_CONTAINERS[@]}" "${APP_CONTAINERS[@]}")
  [ "$WITH_SUPABASE" = "true" ] && names+=("${SUPABASE_CONTAINERS[@]}")
  [ "$WITH_CONFIG" = "true" ] && names+=(nexent-config)
  [ "$WITH_WEB" = "true" ] && names+=(nexent-web)
  printf '%s\n' "${names[@]}"
}

existing_stack_complete() {
  local name
  while IFS= read -r name; do
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$name" || return 1
  done < <(target_containers)
  return 0
}

try_existing_stack_fastpath() {
  info "Complete existing stack found; using the fast path (start + health only, no recreate)."
  start_wave "${INFRA_CONTAINERS[@]}"
  if ! wait_for_container_healthy nexent-elasticsearch 300; then
    return 1
  fi
  if ! wait_for_postgres 120; then
    return 1
  fi
  ensure_es_api_key
  if [ "$WITH_SUPABASE" = "true" ]; then
    start_wave "${SUPABASE_CONTAINERS[@]}"
  fi
  start_wave "${APP_CONTAINERS[@]}"
  [ "$WITH_CONFIG" = "true" ] && start_wave nexent-config
  [ "$WITH_WEB" = "true" ] && start_wave nexent-web
  return 0
}

# --------------------------------------------------------------------------
# Full path: compose up in waves so that infrastructure is healthy before the
# application containers boot (they expect Elasticsearch up; the optional
# config container runs SQL migrations at boot).
# --------------------------------------------------------------------------
full_deploy() {
  local services
  info "Starting infrastructure services..."
  if ! docker compose "${CORE_COMPOSE_ARGS[@]}" up -d \
      nexent-elasticsearch nexent-postgresql nexent-minio redis; then
    err "Failed to start infrastructure services."
    return 1
  fi
  if ! wait_for_container_healthy nexent-elasticsearch 300; then
    return 1
  fi
  if ! wait_for_postgres 120; then
    return 1
  fi
  ensure_es_api_key

  if [ "$WITH_SUPABASE" = "true" ]; then
    info "Starting Supabase services..."
    if ! docker compose "${SUPABASE_COMPOSE_ARGS[@]}" up -d; then
      err "Failed to start Supabase services."
      return 1
    fi
  fi

  services=(nexent-mcp nexent-runtime nexent-northbound)
  [ "$WITH_CONFIG" = "true" ] && services+=(nexent-config)
  [ "$WITH_WEB" = "true" ] && services+=(nexent-web)
  info "Starting application services: ${services[*]}"
  if ! docker compose "${CORE_COMPOSE_ARGS[@]}" up -d "${services[@]}"; then
    err "Failed to start application services."
    return 1
  fi
  return 0
}

print_diagnostics() {
  cat <<EOF

[knowevo] Diagnostics:
  1. Container status:
     docker compose --env-file "$ROOT_ENV_FILE" --env-file "$GENERATED_ENV_FILE" \\
       -p nexent -f "$COMPOSE_DIR/docker-compose.yml" \\
       -f "$COMPOSE_DIR/docker-compose.override.yml" ps -a
  2. Elasticsearch on Linux often needs: sysctl -w vm.max_map_count=262144
  3. Logs: docker logs --tail 100 <container>
  4. Environment files: $ROOT_ENV_FILE
                        $GENERATED_ENV_FILE
  5. Data root: $(env_var ROOT_DIR "$HOME/nexent-data")

EOF
}

print_summary() {
  local root_dir pg_user pg_db
  root_dir="$(env_var ROOT_DIR "$HOME/nexent-data")"
  pg_user="$(env_var POSTGRES_USER root)"
  pg_db="$(env_var POSTGRES_DB nexent)"
  cat <<EOF

============================================================
 KnowEvo stack is up.
============================================================
 PostgreSQL:             localhost:5434 (user "$pg_user", db "$pg_db"; nexent + supabase on 5436)
 Elasticsearch:          http://localhost:9210
 Redis:                  localhost:6379
 MinIO API / console:    http://localhost:9010 / http://localhost:9011
 MCP service (SSE):      http://localhost:5011/sse
 MCP management API:     http://localhost:5015/docs
 Runtime service (WS):   ws://localhost:5014
 Northbound API:         http://localhost:5013/api
EOF
  if [ "$WITH_SUPABASE" = "true" ]; then
    echo " Supabase gateway:       http://localhost:8000"
  fi
  if [ "$WITH_WEB" = "true" ]; then
    echo " Web UI:                 http://localhost:3000"
  fi
  cat <<EOF
 Backend config API:     http://localhost:5010/docs - host process, NOT managed
                         by this script. Expected host-mode environment:
                         POSTGRES_HOST=localhost POSTGRES_PORT=5434
                         MINIO_ENDPOINT=http://127.0.0.1:9010
                         NEXENT_SANDBOX_DEFAULT_LEVEL=local
                         KW_MCP_TENANT_ID=<primary tenant uuid>
 Credentials location:   deploy/env/.env
 Data root:              $root_dir
 Tear down:              bash deploy/knowevo/down.sh (add --purge to also
                         remove named volumes; bind-mount data under the data
                         root is always kept)
============================================================

EOF
}

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --with-config) WITH_CONFIG="true"; shift ;;
    --with-web)    WITH_WEB="true"; shift ;;
    --no-supabase) WITH_SUPABASE="false"; shift ;;
    --redeploy)    REDEPLOY="true"; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             err "Unknown option: $1"; usage >&2; exit 1 ;;
  esac
done

info "Repository root: $REPO_ROOT"
check_docker

ports=("${BASE_PORTS[@]}")
[ "$WITH_CONFIG" = "true" ] && ports+=(5010)
[ "$WITH_WEB" = "true" ] && ports+=(3000)
check_ports "${ports[@]}"

prepare_env_files
prepare_data_root

# compose does not expand the nested default ${NEXENT_USER_DIR:-$HOME/nexent}
# reliably; export it explicitly.
export NEXENT_USER_DIR="${NEXENT_USER_DIR:-$HOME/nexent}"
info "NEXENT_USER_DIR=$NEXENT_USER_DIR"

ok="true"
if [ "$REDEPLOY" != "true" ] && existing_stack_complete; then
  if ! try_existing_stack_fastpath; then
    ok="false"
  fi
else
  if ! check_fork_image; then
    exit 1
  fi
  if ! full_deploy; then
    ok="false"
  fi
fi

if [ "$ok" != "true" ]; then
  err "Bring-up did not complete; see the messages above."
  print_diagnostics
  exit 1
fi

info "Stack is up; polling service health..."
if ! poll_health; then
  err "One or more services did not become healthy in time."
  print_diagnostics
  exit 1
fi

print_summary
info "Done."
