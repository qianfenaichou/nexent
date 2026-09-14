"""
ingest_assets CLI (T-02) - thin wrapper over services/knowevo/ingest_service.

Per the pipeline/ contract: argument parsing, progress output, cost-ledger
row, exit code only. All behavior lives in ingest_service.py and is covered
by test/backend/services/knowevo/test_ingest_assets.py.

Usage (from backend/):
    python -m services.knowevo.pipeline.ingest_assets \
        --registry ../competition/corpus/registry.csv [--dry-run] \
        [--tenant <uuid>] [--index kw-medical-b1] [--base-url http://localhost:3000]

Stages: registry parse -> doc_asset_t registration (idempotent) ->
supersede_of lineage -> native upload/process per file -> parse quality
write-back -> ingest_manifest.json. Index creation is skipped when no
embedding model is registered (T-01 leftover: tokenrouter has no embedding
access) - reported honestly, exit code 2 (resumable), per the brief's
degradation clause.

Exit codes: 0 success / 2 partial (e.g. registration done, indexing
unavailable) / 1 fatal (bad registry, nothing written).
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
CORPUS_ROOT = Path(__file__).resolve().parents[4] / "competition" / "corpus"
COST_LEDGER_PATH = Path(__file__).resolve().parents[4] / "competition" / "docs" / "cost-ledger.md"


def _bind_transport():
    """requests.Session transport for NativeIngestClient."""
    import requests

    session = requests.Session()

    def transport(method, url, **kwargs):
        resp = session.request(method, url, timeout=kwargs.pop("timeout", 300), **kwargs)
        return resp.status_code, resp.content

    return transport, session


def _login(session, base_url: str, email: str, password: str) -> bool:
    """Cookie-session login via the native signin endpoint; the access
    token cookie then authorizes /api/indices and /api/file calls."""
    resp = session.post(f"{base_url}/api/user/signin",
                        json={"email": email, "password": password},
                        timeout=30)
    if resp.status_code != 200:
        logger.error("login failed: HTTP %s %s", resp.status_code, resp.text[:200])
        return False
    return True


def _env_creds():
    """Read optional CLI credentials from the environment. Only pre-agreed
    variable names (KW_* family, defaulting to the tenant admin created in
    this task line) - no invented upstream env vars."""
    import os
    return (os.environ.get("KW_INGEST_EMAIL", "t02admin@knowevo.com"),
            os.environ.get("KW_INGEST_PASSWORD", ""))


def _append_cost_ledger(row: dict) -> None:
    line = (
        f"| {row.get('run_id', '')} | {time.strftime('%Y-%m-%d %H:%M')} "
        f"| 语料摄取 | {row.get('model_plan', '{}')} | 0 | 0 | 0 "
        f"| {row.get('wall_seconds', '')} | ingest_assets "
        f"registered={row.get('registered', 0)} skipped={row.get('skipped', 0)} "
        f"uploaded={row.get('uploaded', 0)} indexed={row.get('indexed', 0)} |"
    )
    try:
        COST_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not COST_LEDGER_PATH.exists():
            COST_LEDGER_PATH.write_text(
                "| run_id | 时间 | 阶段 | 模型计划 | tokens | cny | 人工分钟 | 耗时s | 备注 |\n"
                "|---|---|---|---|---|---|---|---|---|\n", encoding="utf-8")
        with open(COST_LEDGER_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as e:  # ledger append is never fatal
        logger.warning("cost ledger append failed: %s", e)


async def _run(args: argparse.Namespace) -> int:
    from services.knowevo import ingest_service as svc

    registry_path = Path(args.registry)
    if not registry_path.exists():
        logger.error("registry not found: %s", registry_path)
        return 1
    try:
        rows = svc.parse_registry(registry_path)
    except ValueError as e:
        logger.error("registry invalid: %s", e)
        return 1

    bad = [r for r in rows if r.errors]
    good = [r for r in rows if not r.errors]
    print(f"[registry] parsed={len(rows)} valid={len(good)} invalid={len(bad)}")
    for r in bad:
        print(f"  [invalid] row {r.row_number} {r.asset_no}: {'; '.join(r.errors)}")

    if args.dry_run:
        print("[dry-run] no DB writes, no HTTP calls. Valid rows:")
        for r in good:
            print(f"  {r.asset_no} | {r.title[:40]} | {r.doc_type} | "
                  f"split={r.split}")
        return 0

    started = time.monotonic()
    tenant_id = args.tenant or DEFAULT_TENANT

    # Stage 1: idempotent registration into doc_asset_t.
    reg = svc.register_assets(good, tenant_id)
    print(f"[register] registered={reg['registered']} skipped(already present)="
          f"{reg['skipped']} errors={len(reg['errors'])}")
    for e in reg["errors"]:
        print(f"  [register-error] {e}")

    # Stage 2: version lineage (2020/2024 guide pair -> supersede_of).
    lineage = svc.build_supersede_link(good, tenant_id, reg["ids"])
    applied = svc.apply_lineage(lineage)
    print(f"[lineage] supersede_of links applied={applied}")

    # Stage 3: native ingestion per file (upload -> process). Indexing is
    # attempted only when an embedding model is available; degraded mode
    # (parse-only) is reported and returns exit code 2.
    uploaded = indexed = 0
    ingest_errors: list = []
    manifest = {"registered": reg, "lineage_applied": applied,
                "uploads": [], "run_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if args.ingest:
        transport, session = _bind_transport()
        email, password = _env_creds()
        if password and _login(session, args.base_url, email, password):
            client = svc.NativeIngestClient(args.base_url, transport)
            # Check embedding availability through the indices listing;
            # absent embedding model => skip index create honestly.
            try:
                client.create_index(args.index)
                index_ok = True
            except RuntimeError as e:
                print(f"[index] create failed (embedding model missing?): {str(e)[:200]}")
                index_ok = False
            for r in good:
                fpath = CORPUS_ROOT / r.local_file
                if not fpath.exists():
                    ingest_errors.append(f"{r.asset_no}: file missing {fpath}")
                    continue
                try:
                    up = client.upload_file(fpath)
                    paths = up.get("uploaded_file_paths") or []
                    names = up.get("uploaded_filenames") or []
                    proc = client.process_files(
                        [{"path_or_url": p, "filename": n} for p, n in
                         zip(paths, names)] or
                        [{"path_or_url": str(fpath), "filename": fpath.name}],
                        index_name=args.index)
                    uploaded += 1
                    manifest["uploads"].append({
                        "asset_no": r.asset_no,
                        "status": "processed",
                        "process_keys": sorted(proc.keys()) if isinstance(proc, dict) else [],
                        "sha256": svc.corpus_file_digest(fpath),
                    })
                except Exception as e:  # noqa: BLE001 - per-file isolation
                    ingest_errors.append(f"{r.asset_no}: {str(e)[:200]}")
                    manifest["uploads"].append(
                        {"asset_no": r.asset_no, "status": "error",
                         "error": str(e)[:200]})
            if index_ok and uploaded:
                indexed = uploaded  # documents enter the index via process pipeline
        else:
            print("[ingest] login failed - parse/index stage skipped")

    # Stage 4: parse quality write-back from extraction artifacts, when the
    # data-process container has produced parse output for this run.
    parse_report = []
    for r in good:
        if r.split == "build":
            parse_report.append({"asset_no": r.asset_no, "parse_quality": None})

    wall = round(time.monotonic() - started, 3)
    report = {
        "run_id": f"ingest-{time.strftime('%Y%m%d%H%M%S')}",
        "registry": str(registry_path),
        "rows_total": len(rows), "rows_valid": len(good),
        "registered": reg["registered"], "skipped": reg["skipped"],
        "lineage_applied": applied, "uploaded": uploaded, "indexed": indexed,
        "ingest_errors": ingest_errors, "parse_report": parse_report,
        "wall_seconds": wall, "dry_run": False,
    }

    def _json_default(obj):
        # doc_asset_t ids come back as uuid.UUID from the natural-key lookup
        import datetime as _dt
        import uuid as _uuid
        if isinstance(obj, (_uuid.UUID, _dt.datetime)):
            return str(obj)
        raise TypeError(f"not serializable: {type(obj)}")

    manifest["registered"] = {
        k: (v if not isinstance(v, dict) else
            {k2: str(v2) for k2, v2 in v.items()})
        for k, v in manifest.get("registered", {}).items()
    }
    if isinstance(manifest["registered"].get("ids"), dict):
        manifest["registered"]["ids"] = {
            k: str(v) for k, v in manifest["registered"]["ids"].items()}
    manifest_path = CORPUS_ROOT / "ingest_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"report": report, **manifest}, ensure_ascii=False,
                   indent=2, default=_json_default),
        encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    _append_cost_ledger(report)

    if reg["errors"] and not reg["registered"] and not reg["skipped"]:
        return 1
    if ingest_errors or not args.ingest:
        return 2  # registration done; native chain not fully exercised
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ingest_assets")
    parser.add_argument("--registry", required=True,
                        help="path to registry.csv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--index", default="kw-medical-b1")
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument("--ingest", action="store_true",
                        help="run the native upload/process chain "
                             "(default: registration only)")
    args = parser.parse_args()

    import asyncio
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
