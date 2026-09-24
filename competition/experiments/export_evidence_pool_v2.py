"""export_evidence_pool_v2.py — 证据池扩展导出（P0-2，严格版升级路径）

目的
----
probe_p6 的冻结导出（`probe_p6_autonomy_tau.py:95-105` 的 FROZEN EXPORT_SQL）只取
4 个键：`op / target / confidence / evidence_spans`，导致 `Quality(·)` 在冻结池上
**不可计算**（缺 `parent / also_parent / anchor / seed_terms`）。本脚本是**新文件**，
**不改** `probe_p6_autonomy_tau.py` 原脚本、不重算其原 JSON。它把质量函数所需的
四个字段纳入导出，使下一阶段（真实库就绪后）能重算「达到 Q* 的累计人工分钟数」。

设计要点（来自 P0-0 / s1-baseline-readout P-2 / P-3 的核查结论）
----------------------------------------------------------------
1. `parent / also_parent / anchor` 来自 `ontology_version_t.applied_ops[].payload`
   —— `_apply_ops()`（`ontology_service.py`）对 CLS_ADD 直接把 payload 整包当 class
   字典塞进 `snapshot["classes"]`，因此这三者确实存在于 payload，只是 P6 的 SQL 丢掉了。
   本脚本用 `op->'payload'->>'parent'` 等取回（与 s1-baseline-readout §2.3 一致）。

2. `seed_terms`：**不是数据库列，也不是 `seed_terms.py` 模块的输出**。
   - `seed_terms.py`（`backend/services/knowevo/seed_terms.py`）是「问题驱动的 KG 检索种子词」
     用于 T-26 / 决策卡检索通道，与 `Quality(·).cov` 需要的是**两回事**。
   - `Quality(·)`（`ontology_service.py:754,990-1035`）的 `cov` 分量需要调用方注入的
     `seed_terms: list[str]`，生产代码里**从未被传入**（仅 `test_ontology_service.py:389`
     单测里显式传过）→ 故冻结池 `cov ≡ 0.0`。
   - 依 `s1-baseline-readout.md` P-3，其**预期来源**是「种子骨架的 S2 输出（每章 top-K 术语）」，
     即 `seed-bootstrap` 骨架生成产物；其**持久化位置当前未接线**（open prerequisite C-3）。
   ⇒ 本脚本把 `seed_terms` 设计为**外部注入参数**（从 `--seed-terms-manifest` JSON 载入），
     默认 `None`，并显式标注「持久化未建立」——不伪造、不硬编码。

3. 本脚本只是「写好、不跑」：Phase 0 无服务依赖，**禁止起 PG/ES/minio**
   （任务书铁律）。真实导出在 Phase 1+（Q2B 服务栈就绪后）执行。

用法（Phase 1+ 才用，本阶段不要执行）
-------------------------------------
    python export_evidence_pool_v2.py \
        --tenant-id 6756b0ab-39c0-462a-9745-aa12e1511fcd \
        --out deliverables/algorithm-probes/probe_p7b_evidence_pool.json \
        --seed-terms-manifest path/to/seed_terms_manifest.json   # 可选；缺省 cov 仍 = 0
"""

from __future__ import annotations

import argparse
import json
from typing import Any


# 与 probe_p6 同构的租户常量（不改原脚本，这里自带一份）
KW = "6756b0ab-39c0-462a-9745-aa12e1511fcd"
FROZEN_AT = "2026-09-23T12:30:00+08:00"  # 仅作记录，v2 不冻结旧池


# ---------------------------------------------------------------------------
# 扩展导出 SQL：在 P6 4 键基础上追加 parent / also_parent / anchor
# ---------------------------------------------------------------------------
# P6 原 SQL（仅供对照，不调用）：
#   select (op->>'op')||'|'||(op->>'target')||'|'||coalesce((op->'payload'->>'confidence'),'NA')||'|'||
#          (op->'payload'->'evidence_spans')::text from nexent.ontology_version_t v,
#          jsonb_array_elements(v.applied_ops) op where v.tenant_id='...' and jsonb_array_length(v.applied_ops)=20;
#
# v2 改为：每行取出 op / target / confidence / evidence_spans / parent / also_parent / anchor
# 这样下游 _k0_metrics_from_snapshot 的 red / dep / align 才能算出非常数。
EXPORT_SQL_V2 = """
select
    op->>'op'                                          as op,
    op->>'target'                                      as target,
    coalesce(op->'payload'->>'confidence', 'NA')       as confidence,
    (op->'payload'->'evidence_spans')::text            as evidence_spans,
    op->'payload'->>'parent'                           as parent,
    op->'payload'->>'also_parent'                      as also_parent,
    op->'payload'->>'anchor'                           as anchor
from nexent.ontology_version_t v,
     jsonb_array_elements(v.applied_ops) op
where v.tenant_id = %(tenant)s
  and jsonb_array_length(v.applied_ops) = 20;
"""


# 第二路：v1.1.0 快照的 classes（含 payload 全键），用于 Quality 计算
SNAPSHOT_CLASSES_SQL_V2 = """
select elem->>'name'                       as name,
       elem->>'parent'                     as parent,
       elem->>'also_parent'                as also_parent,
       elem->>'anchor'                     as anchor
from jsonb_array_elements(
    (select snapshot->'classes'
     from nexent.ontology_version_t
     where tenant_id = %(tenant)s and version = 'v1.1.0')
) elem;
"""


def load_seed_terms(manifest_path: str | None) -> list[str] | None:
    """载入 cov 所需的 seed_terms。

    结论（P0-2 的明确结论）：
    - seed_terms 不是 DB 列，也不是 seed_terms.py 的「问题检索种子词」。
    - 它是 Quality().cov 的调用方注入参数（ontology_service.py:754,990-1035）。
    - 预期来源 = 种子骨架 S2 输出（每章 top-K 术语），持久化位置当前未接线（C-3）。
    - 因此这里只从外部 manifest 载入；未提供时返回 None（cov 将 = 0.0，如实）。
    """
    if not manifest_path:
        return None
    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # 兼容两种形态：纯列表，或 {"terms": [...]} / {"seed_terms": [...]}
    if isinstance(data, list):
        return [str(t) for t in data]
    for key in ("terms", "seed_terms", "chapter_topk_terms"):
        if key in data and isinstance(data[key], list):
            return [str(t) for t in data[key]]
    return None


def build_pool_rows(raw_rows: list[dict[str, Any]], seed_terms: list[str] | None) -> dict[str, Any]:
    """把 DB 行组装成证据池对象（与 probe_p7 的 data_reality / quality_axis 字段对齐）。

    这里只做结构化组装，不计算 Quality（避免像 P6 那样在缺字段时硬算常数）。
    Quality 计算留给下游调用 ontology_service._k0_metrics_from_snapshot，
    由它决定是否因 seed_terms=None 而报 cov=0.0（不伪造）。
    """
    pool_n = len(raw_rows)
    return {
        "export_version": "v2",
        "frozen_from": "ontology_version_t.applied_ops (n=20) + v1.1.0 snapshot classes",
        "pool_n": pool_n,
        "keys_present": ["op", "target", "confidence", "evidence_spans",
                          "parent", "also_parent", "anchor"],
        "seed_terms_source": (
            "injected-from-manifest" if seed_terms is not None
            else "NONE (Quality.cov will be 0.0; persistence open item C-3)"
        ),
        "seed_terms_count": len(seed_terms) if seed_terms is not None else 0,
        "rows": raw_rows,
        "note": "Quality(.) must be computed by ontology_service._k0_metrics_from_snapshot "
                "with these fields; not computed here to avoid constant-function冒充测量 "
                "(see AGENTS.md §0.1 and s1-baseline-readout §2.5).",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="v2 evidence-pool export (parent/also_parent/anchor + seed_terms)")
    ap.add_argument("--tenant-id", default=KW)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed-terms-manifest", default=None,
                    help="JSON file with seed_terms for Quality.cov (source: seed skeleton S2; not a DB column)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印 SQL 与字段清单，不连库（Phase 0 无服务时用）")
    args = ap.parse_args()

    seed_terms = load_seed_terms(args.seed_terms_manifest)

    if args.dry_run:
        print("=== EXPORT_SQL_V2 ===")
        print(EXPORT_SQL_V2 % {"tenant": args.tenant_id})
        print("=== SNAPSHOT_CLASSES_SQL_V2 ===")
        print(SNAPSHOT_CLASSES_SQL_V2 % {"tenant": args.tenant_id})
        print("=== seed_terms ===", seed_terms)
        print("Phase 0: no DB connection. Run for real in Phase 1+ after Q2B stack is up.")
        return

    # Phase 1+ 真实执行占位：此处应 pg_connect + 执行两条 SQL + build_pool_rows + 落盘。
    # 本阶段（Phase 0）不允许起服务，故不实现连接逻辑，仅保留结构。
    raise RuntimeError(
        "Phase 0 禁止起 PG。请待 Q2B 服务栈就绪后在 Phase 1+ 执行真实导出。"
    )


if __name__ == "__main__":
    main()
