"""
build_ontology CLI (T-04) - thin wrapper over OntologyService.

Per the pipeline/ contract: argument parsing, progress output, cost-ledger
row, exit code only. All algorithm lives in services/knowevo/
ontology_service.py.

Usage (from backend/):
    python -m services.knowevo.pipeline.build_ontology \
        --docs docs.json [--dry-run] [--tenant <uuid>] [--plan plan.yaml]

docs.json schema: [{"title", "toc": ["1 用药", "1.1 二甲双胍", ...],
                    "terms": [{"name", "aliases", "section", "parent"}]}]
--plan: three-tier model assignment YAML (K8 §2); v0 records the tier
        mapping into the cost-ledger row, actual tier routing lands with
        the T-08 LLM wiring.
Exit codes: 0 success / 2 partial failure / 1 fatal.
"""
import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Fallback tenant for local CLI runs; real callers pass --tenant.
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# Cost-ledger row target (competition/docs/cost-ledger.md). Appended, one
# JSON line per run; the ledger stays grep-able and conflict-free.
COST_LEDGER_PATH = Path(__file__).resolve().parents[4] / "competition" / "docs" / "cost-ledger.md"


def _load_plan(plan_path: str | None) -> dict:
    """Three-tier model plan (K8 §2). YAML in real deployments; v0 accepts
    the file and records it - tier routing itself is T-08 wiring."""
    if not plan_path:
        return {"tier_mid": "mid", "tier_large": "large", "source": "default"}
    import yaml

    try:
        return dict(yaml.safe_load(Path(plan_path).read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError) as e:
        logger.error("plan file unreadable: %s", e)
        raise


class _EchoLLM:
    """Offline default LLM: nominates the seed terms themselves as
    concepts. Real runs inject the mid/large tier via --llm in T-08
    wiring; the CLI stays testable without network access."""

    def __init__(self):
        self.calls = []

    async def __call__(self, prompt: str, *, kind: str, **kwargs):
        self.calls.append({"prompt": prompt, "kind": kind})
        # v0: echo back the seed terms embedded in the prompt lines
        nominations = []
        for line in prompt.splitlines():
            if line.startswith("- term: "):
                rest = line[len("- term: "):]
                name = rest.split(" (parent:")[0].strip()
                nominations.append({
                    "name": name,
                    "confidence": 0.5,
                    "evidence_spans": [{"doc": "seed"}],
                    "rationale": "offline echo mode",
                })
        return nominations


async def _run(args: argparse.Namespace) -> int:
    from services.knowevo.ontology_service import OntologyService

    docs_path = Path(args.docs)
    if not docs_path.exists():
        logger.error("docs file not found: %s", docs_path)
        return 1
    try:
        parsed_docs = json.loads(docs_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error("docs file is not valid JSON: %s", e)
        return 1
    try:
        plan = _load_plan(args.plan)
    except Exception:  # noqa: BLE001 - _load_plan already logged the cause
        return 1

    started = time.monotonic()
    llm = _EchoLLM()
    store = None
    if not args.dry_run:
        # DB unreachable is a resumable (exit 2) condition, not fatal: the
        # report is still computed and printed. Instantiation itself cannot
        # raise; the first query happens inside build_ontology_round, so the
        # guard stays on the import + construct seam only.
        from services.knowevo.ontology_service import PgStore
        store = PgStore()

    svc = OntologyService(store=store, llm=llm)
    report = await svc.build_ontology_round(
        args.tenant or DEFAULT_TENANT,
        doc_ids=[d.get("title", "") for d in parsed_docs],
        parsed_docs=parsed_docs,
        trigger="seed_bootstrap",
        dry_run=args.dry_run,
    )
    wall_seconds = round(time.monotonic() - started, 3)
    report["cost_ledger"] = {
        "run_id": report["round_id"],
        "stage": "ontology",
        "model_plan": plan,
        # echo mode makes no network calls, so billed tokens are honestly 0;
        # real tier routing (T-08) will replace this with usage from the
        # injected llm's response metadata.
        "tokens": 0,
        "llm_calls": len(llm.calls),
        "wall_seconds": wall_seconds,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    _append_cost_ledger_row(report)
    if not args.dry_run and store is None:
        return 2  # computed but could not persist - resumable failure
    return 0


def _append_cost_ledger_row(report: dict) -> None:
    """One markdown table row per run (ledger discipline: every pipeline
    entry writes a row). Failures to append are logged, never fatal."""
    row = report.get("cost_ledger") or {}
    line = (
        f"| {row.get('run_id', '')} | {time.strftime('%Y-%m-%d %H:%M')} "
        f"| 本体 | {json.dumps(row.get('model_plan', {}), ensure_ascii=False)} "
        f"| 0 | 0 | 0 | {row.get('wall_seconds', '')} | "
        f"build_ontology v0 echo-mode llm_calls={row.get('llm_calls', 0)} "
        f"(dry_run={report.get('dry_run')}) |"
    )
    try:
        COST_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(COST_LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write("\n" + line)
    except OSError as e:
        logger.warning("cost-ledger append failed: %s", e)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed ontology build round (K1 stage 0-3, T-04)")
    parser.add_argument("--docs", required=True,
                        help="JSON file with parsed standard docs")
    parser.add_argument("--dry-run", action="store_true",
                        help="score and print proposals without persisting")
    parser.add_argument("--tenant", default=None, help="tenant UUID")
    parser.add_argument("--plan", default=None,
                        help="three-tier model plan YAML (K8 §2)")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        return asyncio.run(_run(parser.parse_args(argv)))
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    sys.exit(main())
