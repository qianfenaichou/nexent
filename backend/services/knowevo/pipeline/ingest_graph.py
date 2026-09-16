"""
ingest_graph CLI (T-06) - thin wrapper over KGService.

Per the pipeline/ contract: argument parsing, progress output, cost-ledger
row, exit code only. All algorithm lives in services/knowevo/kg_service.py.

Usage (from backend/):
    python -m services.knowevo.pipeline.ingest_graph --batch batch.json \\
        [--tables-only] [--dry-run] [--tenant <uuid>] [--checkpoint 100]

batch.json schema:
    {"tenant_id": "<uuid or omit>", "docs": [
        {"doc_id": "<uuid>", "chunks": [
            {"chunk_idx": 0, "modality": "text", "text": "..."},
            {"chunk_idx": 1, "modality": "table", "headers": ["名称", "剂量"],
             "rows": [["二甲双胍", "500mg"]], "class_hint": "Drug"}
        ]}]}

--tables-only skips the LLM channel (deterministic extraction only; this is
the smoke path that needs no model access). Without it, text chunks go
through the offline echo LLM in v0 - real tier routing is T-08 wiring, the
CLI shape stays identical.

Idempotency: each span's hash lands in kg_extract_run_t; re-running a
batch only fills gaps. Exit codes: 0 success / 2 partial (some spans
failed but the run is resumable) / 1 fatal (bad batch, no run started).
"""
import argparse
import asyncio
import json
import logging
import sys
import time
import uuid as uuid_mod
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
COST_LEDGER_PATH = Path(__file__).resolve().parents[4] / "competition" / "docs" / "cost-ledger.md"


class _EchoLLM:
    """Offline default LLM: extracts nothing (zero entities), so a batch
    run without model wiring still exercises the full chain - anchors,
    alignment, merges - honestly. Real runs inject the mid tier via T-08
    wiring; the CLI stays testable without network access."""

    def __init__(self):
        self.calls = []

    async def __call__(self, prompt: str, *, kind: str, **kwargs):
        self.calls.append({"prompt": prompt, "kind": kind, **kwargs})
        return {"entities": [], "edges": []}


async def _run(args: argparse.Namespace) -> int:
    from services.knowevo.kg_service import KGService, PgStore
    from services.knowevo.schemas import EvidenceSpan, ParsedTable

    batch_path = Path(args.batch)
    if not batch_path.exists():
        logger.error("batch file not found: %s", batch_path)
        return 1
    try:
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error("batch file is not valid JSON: %s", e)
        return 1
    docs = batch.get("docs") or []
    if not docs:
        logger.error("batch has no docs array")
        return 1

    started = time.monotonic()
    run_id = uuid_mod.uuid4()
    store = None
    if not args.dry_run:
        store = PgStore()
    tenant = args.tenant or batch.get("tenant_id") or DEFAULT_TENANT

    if args.dry_run:
        planned = sum(len(d.get("chunks", [])) for d in docs)
        print(json.dumps({
            "run_id": str(run_id), "tenant_id": tenant,
            "mode": "dry-run", "docs": len(docs), "chunks_planned": planned,
        }, ensure_ascii=False, indent=2))
        return 0

    snapshot = await store.load_ontology_snapshot(tenant)
    svc = KGService(store=store,
                    llm=None if args.tables_only else _EchoLLM(),
                    ontology=snapshot, tenant_id=tenant)

    spans_total = 0
    extracted = 0
    skipped = 0
    tokens = 0
    errors: list[str] = []
    merged = {"added": 0, "merged": 0, "superseded": 0,
              "contended": 0, "pending": 0}

    for doc in docs:
        doc_id = doc.get("doc_id")
        if doc_id is None:
            errors.append("chunk skipped: doc_id missing")
            continue
        for chunk in doc.get("chunks", []):
            spans_total += 1
            modality = chunk.get("modality", "text")
            if modality == "table":
                span = ParsedTable(
                    doc_id=doc_id, chunk_idx=chunk.get("chunk_idx", 0),
                    headers=chunk.get("headers") or [],
                    rows=chunk.get("rows") or [],
                    title=chunk.get("title", ""),
                    class_hint=chunk.get("class_hint"),
                    page=chunk.get("page"))
            else:
                span = EvidenceSpan(
                    doc_id=doc_id, chunk_idx=chunk.get("chunk_idx", 0),
                    text=chunk.get("text", ""),
                    modality=modality,
                    page=chunk.get("page"))
            if not getattr(span, "text", "") and modality != "table":
                errors.append(f"chunk {chunk.get('chunk_idx')}: empty text")
                continue
            if await store.is_extract_done(tenant, span.span_hash()):
                skipped += 1
                continue
            if modality == "table":
                result = svc.extract_table(span)
                channel = "table"
            elif args.tables_only:
                skipped += 1  # LLM channel off; text spans are skipped
                continue
            else:
                result = await svc.extract(span)
                channel = "llm"
            report = await svc.merge_delta([result])
            extracted += 1
            tokens += int(getattr(result, "tokens_spent", 0))
            for key in ("added", "merged", "superseded",
                        "contended", "pending"):
                merged[key] += getattr(report, key, 0)
            await store.record_extract_run(
                tenant, run_id, span.span_hash(), channel,
                int(getattr(result, "tokens_spent", 0)))
            if spans_total % args.checkpoint == 0:
                logger.info("progress: %d spans, %d extracted, %d skipped",
                            spans_total, extracted, skipped)

    wall_seconds = round(time.monotonic() - started, 3)
    report = {
        "run_id": str(run_id),
        "tenant_id": tenant,
        "ontology_classes": len(snapshot.get("classes", [])),
        "spans_total": spans_total,
        "extracted": extracted,
        "skipped": skipped,
        "tokens": tokens,
        "merged": merged,
        "errors": errors,
        "wall_seconds": wall_seconds,
        "cost_ledger": {
            "run_id": str(run_id), "stage": "ingest_graph",
            "tokens": tokens, "llm_calls": len(
                getattr(svc.llm, "calls", []) if svc.llm is not None else []),
            "wall_seconds": wall_seconds,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    _append_cost_ledger_row(report)
    return 2 if errors else 0


def _append_cost_ledger_row(report: dict) -> None:
    """One markdown table row per run (ledger discipline: every pipeline
    entry writes a row). Failures to append are logged, never fatal."""
    row = report.get("cost_ledger") or {}
    line = (
        f"| {row.get('run_id', '')} | {time.strftime('%Y-%m-%d %H:%M')} "
        f"| 图谱抽取 | - (tier routing T-08) | {row.get('tokens', 0)} | 0 | 0 "
        f"| {row.get('wall_seconds', '')} | "
        f"spans={report.get('spans_total')} extracted={report.get('extracted')} "
        f"skipped={report.get('skipped')} llm_calls={row.get('llm_calls', 0)} "
        f"errors={len(report.get('errors') or [])} |"
    )
    try:
        COST_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(COST_LEDGER_PATH, "a", encoding="utf-8") as fh:
            fh.write("\n" + line)
    except OSError as e:
        logger.warning("cost-ledger append failed: %s", e)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Graph extraction batch run (K2, T-06)")
    parser.add_argument("--batch", required=True,
                        help="JSON batch file (docs + chunks)")
    parser.add_argument("--tables-only", action="store_true",
                        help="deterministic table channel only, no LLM")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the batch and print a plan")
    parser.add_argument("--tenant", default=None, help="tenant UUID")
    parser.add_argument("--checkpoint", type=int, default=100,
                        help="progress line every N spans")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        return asyncio.run(_run(parser.parse_args(argv)))
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    sys.exit(main())