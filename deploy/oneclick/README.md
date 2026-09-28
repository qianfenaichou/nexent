# Nexent One-Click Deployment

One command to go from a fresh clone to a running Nexent stack, on Linux,
macOS, and Windows.

```
bash deploy/oneclick/deploy-oneclick.sh                                   # Linux / macOS
powershell -ExecutionPolicy Bypass -File deploy\oneclick\deploy-oneclick.ps1   # Windows
```

## Relationship to the upstream deploy script

These scripts are a **thin wrapper / simplified path** around the existing
upstream entry point `deploy/docker/deploy.sh`:

- The wrapper runs `deploy/docker/deploy.sh --defaults`, which is the
  upstream **non-interactive** path (default components:
  `infrastructure,application,data-process,supabase`, development port
  policy, `general` image sources, `lightweight` sandbox).
- All deployment logic lives upstream and is **not duplicated** here:
  `deploy/env/.env` bootstrap (from `deploy/env/.env.example`), secret
  generation (MinIO AK/SK, Supabase JWTs, Elasticsearch API key), data
  directory layout, `docker compose up`, and SQL migrations.
- What the wrapper adds: preflight checks (docker, compose v2, disk, port
  conflicts), an explicit non-interactive guarantee, health polling after
  the stack starts, and a failure diagnostics block.
- For advanced configuration (component selection, monitoring providers,
  image registry mirrors, terminal container, sandbox modes), use the
  upstream interactive TUI instead: `bash deploy/deploy.sh --config`.

Note: `deploy/docker/generate_env.sh` is intentionally **not** called by this
wrapper. That script rewrites service URLs to `localhost:*`, which only fits
the *infrastructure mode* (services running as host processes). The
containerized path bootstrapped by `deploy.sh --defaults` needs the
service-name URLs from `deploy/env/.env.example`.

## Prerequisites

### Linux / macOS
- `bash`
- Docker Engine with the compose v2 plugin (`docker compose version` works;
  legacy `docker-compose` v1 is not supported)
- `curl` (optional; used for HTTP health polling)
- ~20 GB free disk for images (first run pulls several GB)
- The ports listed below must be free

### Windows
- Docker Desktop with the WSL2 / compose v2 backend
- **Git for Windows** (the upstream deploy script is bash; Git Bash is used
  to run it)
- Windows PowerShell 5.1 or later (Windows PowerShell, no extra install)
- ~20 GB free disk; the ports listed below must be free

> The PowerShell script was reviewed on Linux only and is not exercised in
> CI (see the header note in `deploy-oneclick.ps1`). Report issues if your
> Windows setup behaves differently.

## One-command deploy

```bash
# Linux / macOS (from the repository root, or from anywhere - paths self-locate)
bash deploy/oneclick/deploy-oneclick.sh

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File deploy\oneclick\deploy-oneclick.ps1
```

What happens:

1. Preflight: docker daemon, `docker compose` v2, disk space, port conflicts
   (ports already served by Nexent containers are ignored, so re-runs are
   safe).
2. **Existing-stack fast path**: when all core containers already exist
   (`nexent-postgresql`, `nexent-elasticsearch`, `nexent-redis`, `nexent-minio`,
   `nexent-config`, `nexent-runtime`, `nexent-northbound`, `nexent-mcp`,
   `nexent-data-process`), the script only starts the stopped ones and polls
   health - nothing is recreated. Containers whose host port is held by a
   non-Nexent process (e.g. a host uvicorn/npm dev process) are skipped
   instead of being crashed into a port conflict. Pass `--redeploy` /
   `-Redeploy` to skip the fast path and run the full deployment below.
3. `deploy/docker/deploy.sh --defaults` runs: creates `deploy/env/.env` from
   `.env.example` on first run, generates secrets, creates the data
   directory (default `~/nexent-data` / `%USERPROFILE%\nexent-data`), starts
   infrastructure (Elasticsearch, PostgreSQL, Redis, MinIO), generates the
   Elasticsearch API key, then starts the application services and Supabase.
4. Health polling: Elasticsearch container health, PostgreSQL
   `pg_isready`, backend API (`http://localhost:5010/docs`, development
   policy only), web UI (`http://localhost:3000/`).
4. Success banner with entry points and credential locations; on failure, a
   diagnostics block with the exact log commands to run.

### Options

| Flag (.sh) | Flag (.ps1) | Effect |
|---|---|---|
| `--production` | `-Production` | Production port policy: only 3000 and 5013 published |
| `--mainland` | `-Mainland` | Use mainland China image mirrors |
| `--general` | `-General` | Use global image sources (default) |
| `--local-latest` | `-LocalLatest` | Use locally built `:latest` images |
| `--root-dir PATH` | `-RootDir PATH` | Data/log root directory |
| any other arg | trailing args | Forwarded verbatim to `deploy/docker/deploy.sh` (e.g. `--sandbox-mode full`) |

Environment overrides:

| Variable | Effect |
|---|---|
| `NEXENT_ONECLICK_ROOT_DIR=PATH` | Data dir used on first run (instead of `~/nexent-data`) |
| `ONECLICK_IGNORE_PORT_CONFLICTS=1` | Continue even when non-Nexent processes hold required ports |

`deploy/env/.env` is the single source of truth for configuration. It is
created on first run and **reused on later runs** (existing keys and
`ROOT_DIR` are preserved), which is what makes the scripts idempotent.

## One-command uninstall

Uninstall is provided by the existing upstream script (the wrapper does not
duplicate it):

```bash
bash deploy/uninstall.sh docker --delete-volumes false   # stop + remove containers, keep data
bash deploy/uninstall.sh docker --delete-volumes true    # also delete data volumes
```

Windows users: run the same command from Git Bash.

## Port map (development policy, default)

| Host port | Container | Purpose |
|---|---|---|
| 3000 | nexent-web | Web UI (published in both policies) |
| 5010 | nexent-config | Backend config API (`/docs` for Swagger) |
| 5011 | nexent-mcp | MCP service |
| 5015 | nexent-mcp | MCP management API |
| 5012 | nexent-data-process | Data processing service |
| 5555 | nexent-data-process | Celery Flower |
| 8265 | nexent-data-process | Ray dashboard |
| 5013 | nexent-northbound | Northbound API (published in both policies) |
| 5014 | nexent-runtime | Runtime service (agent WebSocket) |
| 5434 | nexent-postgresql | PostgreSQL |
| 6379 | nexent-redis | Redis |
| 9010 | nexent-minio | MinIO API |
| 9011 | nexent-minio | MinIO console |
| 9210 | nexent-elasticsearch | Elasticsearch HTTP (container-internal port is 9200) |
| 9310 | nexent-elasticsearch | Elasticsearch transport |
| 8000 | supabase-kong-mini | Supabase API gateway (full version) |
| 8443 | supabase-kong-mini | Supabase gateway TLS (full version) |
| 5436 | supabase-db-mini | Supabase PostgreSQL (full version) |

Production policy (`.prod.yml`) publishes only **3000** (web) and **5013**
(northbound); all other services stay on the internal docker network. The
optional terminal container (upstream `terminal` component, not enabled by
default here) additionally publishes **2222**.

## Where credentials live

- `deploy/env/.env` — all generated credentials: `POSTGRES_*`,
  `MINIO_*`, `SUPABASE_*`, `JWT_SECRET`, `ELASTICSEARCH_API_KEY`,
  `NEXENT_SUPER_ADMIN_PASSWORD`.
- The default super admin account (full version) is `suadmin@nexent.com`
  with the password from `NEXENT_SUPER_ADMIN_PASSWORD` in
  `deploy/env/.env`. Change it before exposing the stack beyond localhost.

## Troubleshooting

1. **Port already in use** (e.g. a local PostgreSQL on 5434 or a dev server
   on 3000). Free the port or set `ONECLICK_IGNORE_PORT_CONFLICTS=1`. Find
   the owner with `lsof -iTCP:<port> -sTCP:LISTEN` (Linux/macOS) or
   `Get-NetTCPConnection -LocalPort <port> -State Listen` (Windows). Ports
   already served by Nexent containers are ignored automatically.

2. **"Disto compose v2 detected ... would fall back to compose v1"** —
   distro-packaged compose v2 (e.g. Ubuntu's `docker-compose-v2` .deb)
   prints its version without the leading `v` (`2.40.3+ds1...`), which the
   upstream deployer's version probe does not recognize; it then falls back
   to the legacy `docker-compose` 1.x binary, and compose v1 crashes while
   recreating containers (`KeyError: 'ContainerConfig'`). Fix: install the
   official compose plugin from Docker's repository
   (https://docs.docker.com/compose/install/linux/). If a complete existing
   stack is present, this script uses its fast path (start + health, no
   recreate) and does not need the upstream path at all.

3. **"Docker daemon is not reachable"** — start Docker first
   (`sudo systemctl start docker` on Linux, launch Docker Desktop on
   Windows), then re-run. The script is idempotent, so re-running after
   fixing a preflight failure is safe.

3. **Elasticsearch never becomes healthy.** On Linux, Elasticsearch 8.x
   requires `vm.max_map_count >= 262144`:
   `sudo sysctl -w vm.max_map_count=262144`. Check logs:
   `docker logs --tail 100 nexent-elasticsearch`. Low free disk also trips
   ES disk watermarks (see `ES_DISK_WATERMARK_*` in `deploy/env/.env`).

4. **Web UI up but backend errors / knowledge features fail.** Inspect the
   backend log: `docker logs --tail 100 nexent-config` and
   `docker logs --tail 100 nexent-runtime`. A common cause on first boot is
   a missing or invalid model configuration — complete it in the web UI
   (Settings / Model configuration).

5. **Re-run after changing `.env` seems to keep old values.** The scripts
   deliberately reuse the existing `deploy/env/.env` (idempotency). Remove
   the keys you want regenerated (or the whole file for a factory reset),
   then re-run; secrets are regenerated when absent.

6. **Windows: "bash not found on PATH"** — install Git for Windows
   (https://git-scm.com/download/win), reopen the shell, and confirm
   `bash --version` works, then re-run the PowerShell script.

7. **First run takes very long / image pull fails.** The stack pulls several
   GB of images; progress is shown by the upstream deploy script. Re-run the
   same command to resume where the pull left off.
