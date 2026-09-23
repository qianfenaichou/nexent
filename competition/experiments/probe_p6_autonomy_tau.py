#!/usr/bin/env python3
"""P6 probe: ontology semi-auto construction autonomy sweep (tau_a).

Answers one question with existing build records only: **how much does the
auto_accept threshold tau_a actually move (a) the human-intervention ratio
and (b) the error rate of what it lets through?**

Inputs (frozen 2026-09-23 export, no LLM, no DB, no network):

* the 20 build ops of the construction tenant's ontology v1.0.0
  (``ontology_version_t.applied_ops``: 10 CLS_ADD conf=1.0 with
  single-doc seed evidence + 10 REL_ADD with no confidence and no evidence)
  - the as-if pool "all candidates through the proposal gate";
* 1 cross-tenant queue witness (``ontology_change_proposal_t``: cls:Metformin,
  conf=0.9, single-doc evidence, status=pending) - kept for schema reality,
  labeled as such everywhere;
* the v1.1.0 published snapshot member list (10 classes + 10 rel_types) as
  the survival gold (did the op's target survive into the current version?).

It replays the *production* auto_accept gate (three-condition AND, mirrored
from ``ontology_service.py:614-627``; ``build_ev_rich`` from
``ontology_service.py:115-118``) across tau_a in [0.50, 0.95], twice:

* **arm A (as recorded)** - gate exactly as production runs it;
* **arm B (ev_rich relaxed)** - counterfactual with the evidence-richness
  condition forced satisfied, to isolate tau_a's own leverage.

Honesty diagnostics that a single-curve sweep would hide:

1. per-condition binding decomposition at the live default tau_a=0.85
   (which of conf / rejected / ev_rich actually blocks each candidate);
2. the error axis is reported against TWO golds separately: adjudication
   gold (human reviewed/rejected rows) - currently n=0 -> ``insufficient_data``
   - and the survival proxy (target present in current snapshot), with
   Wilson 95% bounds and an ``insufficient_data`` mark whenever fewer than
   5 labeled items got through;
3. the as-built fact that the construction tenant has 0 proposal-queue rows
   (seed tier committed directly), so this is a *replay*, not a run log.

Engine: prefers the real backend helpers (``services.knowevo.ontology_service``
build_ev_rich) so the gate arithmetic is the production one; falls back to a
stdlib mirror of the same two formulas when the backend import is unavailable
(the path used is reported in the output).

Run (from ``nexent/``, either interpreter works; < 1s):

    python3 competition/experiments/probe_p6_autonomy_tau.py
    backend/.venv/bin/python competition/experiments/probe_p6_autonomy_tau.py

Add ``--output PATH`` to persist the JSON report (default: stdout only).

适用范围：对**既有真实构建记录**的离线重放（零 LLM、零 DB、零网络），
候选池 n=21（20 构建租户 + 1 测试租户见证）、裁决金标 n=0；
不是一次新的构建跑分，也不构成真实医疗数据上的效果结论。
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Contract anchors (the gate semantics being replayed)
# ---------------------------------------------------------------------------
GATE_SRC = ("backend/services/knowevo/ontology_service.py:614-627 "
            "(auto_accept: three-condition AND gate)")
EVRICH_SRC = ("backend/services/knowevo/ontology_service.py:115-118 "
              "(build_ev_rich: >=2 distinct docs -> 1.0 else 0.6)")

# Try the real backend helper first (production arithmetic, not a re-derivation).
ENGINE = "mirror"
try:
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
    from services.knowevo.ontology_service import build_ev_rich as _backend_ev_rich
    ENGINE = "backend"
except Exception:  # noqa: BLE001 - mirror fallback is a documented feature
    _backend_ev_rich = None


def build_ev_rich(evidence_spans: list[dict]) -> float:
    """Mirror of ontology_service.build_ev_rich (used only when backend absent)."""
    if _backend_ev_rich is not None:
        return _backend_ev_rich(evidence_spans)
    docs = {span.get("doc") for span in evidence_spans}
    return 1.0 if len(docs) >= 2 else 0.6


# ---------------------------------------------------------------------------
# Frozen export (2026-09-23, SQL in data_reality.export_sql)
# ---------------------------------------------------------------------------
FROZEN_AT = "2026-09-23T12:30:00+08:00"

EXPORT_SQL = [
    "select (op->>'op')||'|'||(op->>'target')||'|'||coalesce((op->'payload'->>'confidence'),'NA')||'|'||"
    "(op->'payload'->'evidence_spans')::text from nexent.ontology_version_t v, "
    "jsonb_array_elements(v.applied_ops) op where v.tenant_id='6756b0ab-39c0-462a-9745-aa12e1511fcd' "
    "and jsonb_array_length(v.applied_ops)=20;",
    "select 'CLS:'||(elem->>'name') from jsonb_array_elements((select snapshot->'classes' from "
    "nexent.ontology_version_t where tenant_id='6756b0ab-39c0-462a-9745-aa12e1511fcd' and version='v1.1.0')) elem "
    "union all select 'REL:'||(elem->>'name') from jsonb_array_elements((select snapshot->'rel_types' from "
    "nexent.ontology_version_t where tenant_id='6756b0ab-39c0-462a-9745-aa12e1511fcd' and version='v1.1.0')) elem;",
    "select tenant_id||'|'||target||'|'||confidence||'|'||status from nexent.ontology_change_proposal_t;",
]

_SEED_DOCS = [{"doc": "seed-bootstrap"}]
KW = "6756b0ab-39c0-462a-9745-aa12e1511fcd"

# op, target, confidence, evidence_spans, tenant, gold_error (survival), gold_source
ITEMS = (
    [("CLS_ADD", "cls:Drug", 1.0, _SEED_DOCS, KW, False, "survival"),
     ("CLS_ADD", "cls:DrugClass", 1.0, _SEED_DOCS, KW, False, "survival"),
     ("CLS_ADD", "cls:Disease", 1.0, _SEED_DOCS, KW, False, "survival"),
     ("CLS_ADD", "cls:Complication", 1.0, _SEED_DOCS, KW, False, "survival"),
     ("CLS_ADD", "cls:Symptom", 1.0, _SEED_DOCS, KW, False, "survival"),
     ("CLS_ADD", "cls:Examination", 1.0, _SEED_DOCS, KW, False, "survival"),
     ("CLS_ADD", "cls:Indicator", 1.0, _SEED_DOCS, KW, False, "survival"),
     ("CLS_ADD", "cls:Treatment", 1.0, _SEED_DOCS, KW, False, "survival"),
     ("CLS_ADD", "cls:Lifestyle", 1.0, _SEED_DOCS, KW, False, "survival"),
     ("CLS_ADD", "cls:Population", 1.0, _SEED_DOCS, KW, False, "survival")]
    + [("REL_ADD", f"rel:{name}", None, [], KW, False, "survival")
       for name in ("indicated_for", "first_line", "treats", "contraindicated",
                    "adverse_effect", "mechanism_of", "monitors", "has_target",
                    "complication_of", "recommended_for")]
    # cross-tenant queue witness: gold unknown outside KW snapshot
    + [("CLS_ADD", "cls:Metformin", 0.9, [{"doc": "d1"}],
        "11111111-1111-1111-1111-111111111111", None, "none")]
)

SNAPSHOT_V110_MEMBERS = frozenset(
    [f"cls:{n}" for n in ("Drug", "DrugClass", "Disease", "Complication", "Symptom",
                          "Examination", "Indicator", "Treatment", "Lifestyle", "Population")]
    + [f"rel:{n}" for n in ("adverse_effect", "complication_of", "contraindicated",
                            "first_line", "has_target", "indicated_for", "mechanism_of",
                            "monitors", "recommended_for", "treats")]
)

TAU_GRID = [round(0.50 + 0.05 * i, 2) for i in range(10)]  # 0.50 .. 0.95
TAU_LIVE_DEFAULT = 0.85
MIN_GOLD_AUTO = 5  # below this the error axis is insufficient_data, not a rate


def gate(item, tau: float, relax_ev: bool = False):
    """Replica of ontology_service.auto_accept (status: never rejected here)."""
    _op, _target, conf, spans, _tenant, _gold, _src = item
    conf = 0.0 if conf is None else conf
    ok_conf = conf >= tau
    ok_status = True  # frozen pool contains no rejected rows
    ok_ev = True if relax_ev else (build_ev_rich(spans) == 1.0)
    return ok_conf and ok_status and ok_ev


def fail_reasons(item, tau: float):
    _op, _target, conf, spans, _tenant, _gold, _src = item
    conf = 0.0 if conf is None else conf
    reasons = []
    if conf < tau:
        reasons.append("conf")
    if build_ev_rich(spans) != 1.0:
        reasons.append("ev_rich")
    return reasons


def wilson_95(err: int, n: int):
    """Wilson score interval (z=1.96). At err=0 the upper bound is z^2/(n+z^2)."""
    if n == 0:
        return None
    z = 1.96
    p = err / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def sweep(relax_ev: bool):
    rows = []
    for tau in TAU_GRID:
        auto = [it for it in ITEMS if gate(it, tau, relax_ev)]
        manual = [it for it in ITEMS if not gate(it, tau, relax_ev)]
        gold_auto = [it for it in auto if it[6] == "survival"]
        n_err = sum(1 for it in gold_auto if it[5])
        if len(gold_auto) < MIN_GOLD_AUTO:
            err_cell = {"status": "insufficient_data",
                        "reason": f"labeled auto n={len(gold_auto)} < {MIN_GOLD_AUTO}",
                        "n_auto_gold": len(gold_auto), "n_err": n_err}
        else:
            err_cell = {"status": "measured", "n_auto_gold": len(gold_auto),
                        "n_err": n_err, "rate": round(n_err / len(gold_auto), 4),
                        "gold": "survival proxy (adjudication gold n=0 -> insufficient_data)",
                        "wilson_95": wilson_95(n_err, len(gold_auto))}
        rows.append({
            "tau": tau,
            "n_auto": len(auto),
            "n_manual": len(manual),
            "manual_ratio": round(len(manual) / len(ITEMS), 4),
            "error_axis": err_cell,
        })
    return rows


def decomposition(tau: float):
    per_item = {}
    counts = {"conf": 0, "ev_rich": 0, "rejected": 0}
    for it in ITEMS:
        reasons = fail_reasons(it, tau)
        per_item[it[1]] = reasons or ["pass"]
        for r in reasons:
            counts[r] += 1
    return {"tau": tau, "binding_counts": counts, "per_item": per_item,
            "n_blocked_by_ev_rich": counts["ev_rich"],
            "note": "an item can be blocked by several conditions; ev_rich binds all 21"}


def main() -> None:
    out_path = None
    if "--output" in sys.argv:
        out_path = sys.argv[sys.argv.index("--output") + 1]

    # survival gold re-derived from the frozen snapshot list (audit trail)
    assert all((it[1] in SNAPSHOT_V110_MEMBERS) == (it[5] is False)
               for it in ITEMS if it[6] == "survival"), "survival gold mismatch"

    report = {
        "probe": "p6_autonomy_tau",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "question": "tau_a 对人工干预比例 / 放行错误率的真实杠杆有多大？",
        "contract_anchors": {"auto_accept_gate": GATE_SRC, "build_ev_rich": EVRICH_SRC},
        "engine": ENGINE,
        "data_reality": {
            "frozen_at": FROZEN_AT,
            "export_sql": EXPORT_SQL,
            "pool_n": len(ITEMS),
            "pool_tenant_split": {"kw_tenant": 20, "cross_tenant_witness": 1},
            "as_built_facts": {
                "proposals_in_kw_tenant": 0,
                "proposals_cross_tenant_pending": 1,
                "build_ops_v100": 20,
                "snapshot_v110_members": len(SNAPSHOT_V110_MEMBERS),
            },
            "gold": {
                "adjudication_gold": {"n_labeled": 0, "status": "insufficient_data",
                                      "why": "ontology_change_proposal_t 无已裁决行（唯一 1 行 status=pending）"},
                "survival_proxy": {"n_labeled": 20, "n_error": 0,
                                   "protocol": "op.target 存在于 v1.1.0 现行快照 -> 记非错；缺失 -> 记错；跨租户见证项无金标"},
            },
        },
        "tau_grid": TAU_GRID,
        "curves": {
            "arm_a_as_recorded": sweep(relax_ev=False),
            "arm_b_ev_rich_relaxed": sweep(relax_ev=True),
        },
        "decomposition_tau_085": decomposition(TAU_LIVE_DEFAULT),
        "conclusions": [
            "arm A：人工干预比例在 tau_a∈[0.50,0.95] 全程 = 1.0（100%）——三条件门中 "
            "ev_rich（证据跨≥2文档）对 21/21 候选全部拒绝，tau_a 阈值根本轮不到生效。",
            "arm B（放开 ev_rich）：干预比例 47.62%（tau≤0.90）→ 52.38%（tau=0.95），"
            "整个网格只有约 +4.8pp 的位移——候选 confidence 聚集在 {1.0, 0.9, 0.0}，"
            "阈值在该池上没有分辨率。",
            "错误率轴：裁决金标 n=0 → insufficient_data；存活代理金标下放行集 0 错/10 "
            "（arm B，tau≤0.90），Wilson 95% 上界随 n 报告，不宣称低错误率。",
            "综合：当前池上『提高自动化率』的杠杆是证据跨文档聚合（ev_rich）与候选分数的分布改善，"
            "调 tau_a 的边际收益 ≈ 0。",
        ],
        "limitations": [
            "n=21 且候选分数三簇离散（1.0/0.9/0.0），曲线呈台阶而非连续；",
            "存活代理金标只捕捉『被后续版本丢弃』类错误，捕捉不了仍存活的错误；",
            "as-built 事实是构建租户 0 条提案队列行（种子层直接提交），本探针是 as-if 重放而非运行日志；",
            "单一领域（2 型糖尿病）单租户，阈值结论不可外推到分数连续分布的池。",
        ],
        "upgrade_path": [
            "本体工作台待审队列积累人工裁决（confirm/reject+reject_reason）后，裁决金标 n>0，"
            "同探针加 --live 复算错误率轴；",
            "两级提案第二级（pending 池回流，mention_count>=3）产出带分数候选后，池的分数分布变连续，"
            "tau_a 扫描才有分辨率；",
            "抽检协议：每次版本发布前对 auto_accepted 候选抽 20-30 条人工复核，复核记录落 "
            "ontology_change_proposal_t.status/reject_reason，即为抽检金标。",
        ],
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"[OK] wrote {out_path}")
    else:
        print(text)

    for row in report["curves"]["arm_a_as_recorded"][:2]:
        print(f"[chk] armA tau={row['tau']} manual_ratio={row['manual_ratio']}")
    for row in report["curves"]["arm_b_ev_rich_relaxed"]:
        print(f"[chk] armB tau={row['tau']} manual_ratio={row['manual_ratio']} "
              f"err={row['error_axis']['status']}")
    print(f"[chk] engine={ENGINE} pool_n={len(ITEMS)} "
          f"ev_rich_binds={report['decomposition_tau_085']['n_blocked_by_ev_rich']}")


if __name__ == "__main__":
    main()
