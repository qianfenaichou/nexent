#!/usr/bin/env bash
#
# KnowEvo competition stack tear-down (Linux/macOS).
#
# Counterpart to deploy/knowevo/up.sh: stops and removes the containers of the
# compose project "nexent" defined by the core compose files (plus the
# Supabase trio when present). Data is preserved by default:
#   - bind-mount data under the data root (PostgreSQL, Elasticsearch, MinIO,
#     Redis) is never touched by this script;
#   - named docker volumes are kept unless --purge is passed (then they are
#     removed together with the containers).
#
# The backend config API runs as a host process (port 5010) and is NOT
# started, stopped, or otherwise managed by this script.
#
# Usage:
#   bash deploy/knowevo/down.sh [--purge] [-h]
# Options:
#   --purge   also remove named docker volumes (compose down -v)
#   -h        show this help

if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script must be run with bash: bash deploy/knowevo/down.sh" >&2
  exit 1
fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_DIR="$REPO_ROOT/deploy/docker/compose"
ROOT_ENV_FILE="$REPO_ROOT/deploy/env/.env"
GENERATED_ENV_FILE="$REPO_ROOT/deploy/docker/.env.generated"

PURGE="false"

info() { printf '[knowevo] %s\n' "$*"; }
warn() { printf '[knowevo] WARNING: %s\n' "$*" >&2; }

usage() {
  cat <<'USAGE'
Usage: bash deploy/knowevo/down.sh [--purge]

Options:
  --purge   also remove named docker volumes (bind-mount data under the data
            root is always kept)
  -h        show this help
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --purge)   PURGE="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *)         warn "Unknown option: $1 (ignored)"; shift ;;
  esac
done

command -v docker >/dev/null 2>&1 || {
  echo "[knowevo] ERROR: docker CLI not found." >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "[knowevo] ERROR: Docker daemon is not reachable. Start Docker and retry." >&2
  exit 1
}

# Compose interpolation needs both env files; either may legitimately be
# absent on a half-deployed host, in which case compose is invoked without it.
core_args=(--env-file "$ROOT_ENV_FILE" -p nexent
  -f "$COMPOSE_DIR/docker-compose.yml"
  -f "$COMPOSE_DIR/docker-compose.override.yml")
supabase_args=(--env-file "$ROOT_ENV_FILE" -p nexent
  -f "$COMPOSE_DIR/docker-compose-supabase.yml")
if [ -f "$GENERATED_ENV_FILE" ]; then
  core_args=(--env-file "$GENERATED_ENV_FILE" "${core_args[@]}")
  supabase_args=(--env-file "$GENERATED_ENV_FILE" "${supabase_args[@]}")
else
  warn "$GENERATED_ENV_FILE not found; continuing without it (down does not need image references)."
fi

down_args=(down)
if [ "$PURGE" = "true" ]; then
  down_args+=(-v)
  info "Purge requested: named docker volumes will be removed as well."
fi

info "Stopping application and infrastructure containers..."
docker compose "${core_args[@]}" "${down_args[@]}" || {
  echo "[knowevo] ERROR: docker compose down failed for the core files." >&2
  exit 1
}

info "Stopping Supabase containers (no-op when none exist)..."
docker compose "${supabase_args[@]}" "${down_args[@]}" || {
  echo "[knowevo] ERROR: docker compose down failed for the Supabase files." >&2
  exit 1
}

cat <<'EOF'

[knowevo] Tear-down finished.
  Data under the data root (PostgreSQL, Elasticsearch, MinIO, Redis bind
  mounts) and the deploy/env files are preserved.
  The host backend config API (port 5010) was not touched; stop it manually
  if needed.
EOF
