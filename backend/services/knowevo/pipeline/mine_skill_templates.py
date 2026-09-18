"""mine_skill_templates CLI (T-20) - thin wrapper over SkillTemplateService.

Per the pipeline/ contract: argument parsing, progress output, cost-ledger
row, exit code only. All algorithm lives in services/knowevo/
skill_template_service.py.

Decision trajectory -> pattern induction -> template candidates:
decision_card_t history (read-only) is grouped by (domain, task_type);
groups with support >= --min-support become parameterized SKILL.md
templates upserted into skill_template_t (INSERT/UPDATE only, zero ALTER).

Honesty guards (pitfalls #38/#39):
- prints the resolved model id + api_base BEFORE any call; a silent
  fallback to a wrong tenant default is the most expensive failure mode;
- every LLM call: 120s hard timeout, 3 attempts max;
- whole run: 10-minute budget, 3 consecutive failures abort the LLM channel
  (the run degrades to the deterministic skeleton channel, clearly labelled
  induced_by="deterministic" - no invented content either way);
- no cards / no group reaching support -> honest "样本不足" exit, zero rows.

Usage (from backend/):
    POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root \\
    POSTGRES_DB=nexent NEXENT_POSTGRES_PASSWORD=<pw> \\
    KW_LLM_MID_MODEL_ID=<id> KW_LLM_LARGE_MODEL_ID=<id> \\
    uv run python -m services.knowevo.pipeline.mine_skill_templates \\
        --min-support 2

Exit codes: 0 mined+saved / 2 degraded (LLM channel down; deterministic
templates saved) / 1 fatal (no cards, DB error) / 3 nothing reached support.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
import uuid as uuid_mod
from pathlib import Path

logger = logging.getLogger(__name__)

COST_LEDGER_PATH = (Path(__file__).resolve().parents[4] / "competition" /
                    "docs" / "cost-ledger.md")

# Pipeline default tenant (ingest_graph.py convention) - the write target
# for cross-tenant pooled mining, never an existing tenant's history.
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

CALL_TIMEOUT_S = 120
MAX_ATTEMPTS = 3
TOTAL_BUDGET_S = 600.0
MAX_CONSECUTIVE_FAILS = 3


class GuardedLLM:
    """Wraps the injected LLM callable with the honesty guards.

    - per call: asyncio.wait_for(CALL_TIMEOUT_S), MAX_ATTEMPTS tries;
    - per run: TOTAL_BUDGET_S wall budget and MAX_CONSECUTIVE_FAILS
      consecutive failures -> LLMChannelDown; the caller degrades to the
      deterministic channel instead of fabricating.
    Token usage from call_with_usage is accumulated for the ledger.
    """

    class LLMChannelDown(RuntimeError):
        pass

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0
        self.failures = 0
        self.consecutive_fails = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.down_reason = ""
        self._start = time.monotonic()

    def _budget_left(self) -> float:
        return TOTAL_BUDGET_S - (time.monotonic() - self._start)

    async def __call__(self, prompt: str, *, kind: str,
                       tier: str = "mid", temperature: float = 0.0) -> str:
        if self.consecutive_fails >= MAX_CONSECUTIVE_FAILS:
            raise self.LLMChannelDown(self.down_reason or
                                      "consecutive failure limit reached")
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            left = self._budget_left()
            if left <= 0:
                self.consecutive_fails = MAX_CONSECUTIVE_FAILS
                self.down_reason = "10-minute run budget exhausted"
                raise self.LLMChannelDown(self.down_reason)
            try:
                text, usage = await asyncio.wait_for(
                    self.inner.call_with_usage(
                        prompt, kind=kind, tier=tier, temperature=temperature),
                    timeout=min(CALL_TIMEOUT_S, left))
                self.calls += 1
                self.consecutive_fails = 0
                self.input_tokens += int(usage.get("input_tokens") or 0)
                self.output_tokens += int(usage.get("output_tokens") or 0)
                return text
            except Exception as exc:  # noqa: BLE001 - guard, then retry
                last_error = exc
                self.failures += 1
                logger.warning("LLM call attempt %d/%d failed (%s: %s)",
                               attempt, MAX_ATTEMPTS, type(exc).__name__, exc)
        self.consecutive_fails += 1
        if self.consecutive_fails >= MAX_CONSECUTIVE_FAILS:
            self.down_reason = f"3 consecutive failures, last: {last_error}"
            logger.error("LLM channel down: %s", self.down_reason)
        raise self.LLMChannelDown(self.down_reason or str(last_error))


def _self_check(tenant_id: str) -> dict:
    """Print the model actually wired per tier (pitfall #38 discipline)."""
    from services.knowevo.llm_client import LlmRouter, _tier_model_id
    from utils.config_utils import get_model_name_from_config

    report = {}
    router = LlmRouter(tenant_id=tenant_id)
    for tier in ("mid", "large"):
        env_id = _tier_model_id(tier)
        config = None
        try:
            config = router._resolve_model_config(tier)
        except Exception as exc:  # noqa: BLE001 - self-check reports, dies later
            config = None
            logger.warning("tier=%s config resolution failed: %s", tier, exc)
        resolved = ""
        api_base = ""
        if config:
            resolved = get_model_name_from_config(config)
            api_base = str(config.get("base_url") or "")
        report[tier] = {"env_id": env_id, "model": resolved,
                        "api_base": api_base}
        print(f"[self-check] tier={tier} KW_model_id={env_id or '(unset)'} "
              f"-> model={resolved or '(unresolved)'} api_base={api_base or '?'}")
        if not env_id:
            print(f"[self-check] WARNING: KW_LLM_{tier.upper()}_MODEL_ID is "
                  "unset; the router will silently fall back to the tenant "
                  "default LLM (pitfall #38). Verify the line above before "
                  "trusting this run.")
    return report


async def _pick_tenant(explicit: str | None) -> str | None:
    """Explicit --tenant, else the tenant holding the most decision cards."""
    from sqlalchemy import func

    from database.knowevo_db import DecisionCard, _get_db_session

    if explicit:
        return explicit
    with _get_db_session() as session:
        rows = (session.query(DecisionCard.tenant_id,
                              func.count(DecisionCard.id).label("n"))
                .group_by(DecisionCard.tenant_id)
                .order_by(func.count(DecisionCard.id).desc())
                .limit(5)
                .all())
    if not rows:
        return None
    for tenant_id, n in rows:
        print(f"[tenant] decision cards: tenant={tenant_id} count={n}")
    return str(rows[0][0]) if rows[0][0] else None


async def _run(args: argparse.Namespace) -> int:
    from services.knowevo.llm_client import build_llm_callable
    from services.knowevo.skill_template_service import SkillTemplateService

    # Cross-tenant pooling is an explicit offline admin operation: mined
    # templates land in the pipeline default tenant (ingest_graph.py
    # convention), never mixed into an existing tenant's history.
    if args.cross_tenant and not args.tenant:
        args.tenant = DEFAULT_TENANT
        print(f"[tenant] cross-tenant pooling: templates will be written to "
              f"the pipeline default tenant {DEFAULT_TENANT}")

    started = time.monotonic()
    tenant_id = await _pick_tenant(args.tenant)
    if not tenant_id:
        print("[abort] decision_card_t has no rows - nothing to mine from. "
              "No template written (nothing fabricated).")
        return 1

    model_report = _self_check(tenant_id)

    inner_llm = None
    if not args.no_llm:
        inner_llm = build_llm_callable(tenant_id=tenant_id)
    llm = GuardedLLM(inner_llm) if inner_llm is not None else None

    service = SkillTemplateService(tenant_id=tenant_id, llm=llm, lang=args.lang)

    if args.cross_tenant:
        # Explicit offline pooling: single-tenant support cannot reach the
        # threshold when history is spread thin (real DB: 2 cards/tenant).
        # Writes still go to the explicit --tenant; every candidate's
        # source records cross_tenant=True next to per-card provenance.
        cards = await service._get_store().list_cards_all_tenants(
            limit=args.limit)
        candidates = await service.induce_from_cards(
            cards, min_support=args.min_support)
        for cand in candidates:
            cand["source"]["cross_tenant"] = True
    else:
        candidates = await service.induce(min_support=args.min_support,
                                          limit=args.limit)
    dropped = service.last_dropped
    print(f"[mine] cards grouped; {len(candidates)} candidate group(s) at "
          f"min_support={args.min_support}, {len(dropped)} dropped: "
          f"{dropped}")
    if not candidates:
        print("[done] no group reached min_support - 样本不足, no template "
              "written (nothing fabricated). Lower --min-support only with "
              "human sign-off.")
        return 3

    llm_degraded = False
    saved: list[dict] = []
    if args.dry_run:
        for cand in candidates:
            print(f"[dry-run] would upsert {cand['name']} "
                  f"support={cand['support']} "
                  f"induced_by={cand['source']['induced_by']}")
    else:
        for cand in candidates:
            row_id = await service.save_candidate(cand)
            saved.append({"name": cand["name"], "support": cand["support"],
                          "induced_by": cand["source"]["induced_by"],
                          "id": row_id})
            print(f"[save] {cand['name']} support={cand['support']} "
                  f"induced_by={cand['source']['induced_by']} id={row_id}")
        # Re-reading templates proves the store seam round-trips; keeps the
        # report honest about what is actually in skill_template_t.
        rows = await service.list_templates(limit=50)
        print(f"[verify] skill_template_t now holds {len(rows)} row(s) for "
              f"tenant={tenant_id}")

    if llm is not None and (llm.consecutive_fails or llm.down_reason):
        llm_degraded = True
        print(f"[degraded] LLM channel down ({llm.down_reason}); "
              f"deterministic skeletons used - clearly labelled, no "
              f"fabricated content.")
    if llm is not None and llm.calls == 0 and not llm_degraded \
            and not args.no_llm and any(
                c["induced_by"] == "deterministic" for c in candidates):
        print("[note] zero LLM calls were made (candidates fell back to "
              "deterministic skeletons); verify model wiring above.")

    wall = round(time.monotonic() - started, 3)
    _append_cost_ledger_row(tenant_id, model_report, llm, saved, args, wall)
    print(f"[done] run_id=mine-{time.strftime('%Y%m%d%H%M%S')} "
          f"wall={wall}s saved={len(saved)} llm_calls="
          f"{llm.calls if llm else 0} llm_tokens="
          f"{(llm.input_tokens if llm else 0)}+"
          f"{(llm.output_tokens if llm else 0)}")
    return 2 if llm_degraded else 0


def _append_cost_ledger_row(tenant_id: str, model_report: dict, llm,
                            saved: list[dict], args: argparse.Namespace,
                            wall: float) -> None:
    """One markdown table row per run (ledger discipline: every pipeline
    entry writes a row). Failures to append are logged, never fatal."""
    tier_desc = {}
    for tier, info in model_report.items():
        tier_desc[tier] = info.get("model") or "unresolved"
    names = ",".join(f"{s['name']}(n={s['support']})" for s in saved) or "-"
    line = (
        f"| mine-{uuid_mod.uuid4().hex[:8]} | {time.strftime('%Y-%m-%d %H:%M')} "
        f"| 模板沉淀 | mid={tier_desc.get('mid', '?')} / "
        f"large={tier_desc.get('large', '?')} "
        f"| {llm.input_tokens if llm else 0} "
        f"| {llm.output_tokens if llm else 0} | 0 | {wall}s | "
        f"T-20 mine_skill_templates tenant={tenant_id} min_support="
        f"{args.min_support} limit={args.limit} no_llm={args.no_llm} "
        f"cross_tenant={args.cross_tenant} "
        f"dry_run={args.dry_run} lang={args.lang} llm_calls="
        f"{llm.calls if llm else 0} llm_failures="
        f"{llm.failures if llm else 0} saved=[{names}] |"
    )
    try:
        COST_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(COST_LEDGER_PATH, "a", encoding="utf-8") as fh:
            fh.write("\n" + line)
    except OSError as exc:
        logger.warning("cost-ledger append failed: %s", exc)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Mine parameterized SKILL.md templates from "
                    "decision_card_t history (T-20)")
    parser.add_argument("--min-support", type=int, default=2,
                        help="minimum group support to become a candidate")
    parser.add_argument("--limit", type=int, default=200,
                        help="max decision cards read (newest first)")
    parser.add_argument("--tenant", default=None,
                        help="tenant UUID (default: tenant with most cards)")
    parser.add_argument("--lang", default="zh", choices=("zh", "en"),
                        help="induction prompt language")
    parser.add_argument("--no-llm", action="store_true",
                        help="deterministic skeleton channel only")
    parser.add_argument("--cross-tenant", action="store_true",
                        help="pool decision cards across tenants for "
                             "induction (offline admin operation; writes "
                             "land in the pipeline default tenant)")
    parser.add_argument("--dry-run", action="store_true",
                        help="mine and print candidates, write nothing")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("[abort] interrupted")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
