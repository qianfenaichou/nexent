#!/usr/bin/env python3
"""可复现证据采集器：时间区间索引下推（§6.6 ① ③④ ⑤）。

本项目给双时态知识图谱的「当前视图」提供了两种语义等价的谓词：

  * ``valid_now``            布尔形式： valid_at <= t AND (invalid_at IS NULL OR invalid_at > t)
  * ``valid_range_contains`` 区间形式： tstzrange(valid_at, COALESCE(invalid_at,'infinity'),'[)') @> t

迁移 ``v2.5.5_kw_011_knowevo_graph_indexes.sql`` 在两条表上建了 GiST 表达式索引
``ix_kr_valid_range`` / ``ix_ke_valid_range``（建在上面的 range 表达式上）。
本脚本把「翻开关到底有没有真的用上索引、能快多少」变成一个**可复现、可选择性跑、
无 PG 时干净退出**的证据采集器。

三项证据（对应上游 receipt §6.6）：

  ① 预检查：统计 ``kg_relation_t`` / ``kg_entity_t`` 中
     ``invalid_at IS NOT NULL AND invalid_at < valid_at`` 的行数，明确判定「必须为 0」。
     必须在建索引之前跑（本脚本的 standalone preflight 步骤不建任何索引）。
  ③/④ EXPLAIN 对照：对同一条查询分别用「区间谓词」与「布尔谓词」跑
     ``EXPLAIN (ANALYZE, BUFFERS)``，保留完整计划文本，并自动提取
     「计划里是否出现 ix_kr_valid_range / Bitmap Index Scan / Seq Scan」等结论。
  ⑤ 多跳 p95：用真实的 ``PgJsonbGraphStore``，对比 ``use_range_predicate``
     关闭 vs 开启时的多跳延迟，报告 p50 / p95。

所有结果写入 JSON 证据文件（默认
``competition/deliverables/algorithm-probes/explain_valid_range.json``）。

**无 PG 时**：脚本在真正测量前做一次连接探测，连不上就以清晰原因退出
（exit 1），**绝不编造任何数字、绝不用占位符冒充测量结果**。

多跳 p95 测试默认参数（不确定处取了合理默认值，均可在命令行覆盖）：

  * 合成图：--multihop-entities 2000 / --multihop-edges 6000，固定随机种子
    SEED，保证「规划器有真实统计、GiST 才有机会被选中」。
  * seeds：取「至少出现在一个关系里」的前 --multihop-seeds(=10) 个稳定 id，
    保证多跳真的能往外扩（空种子会退化成 p95=0 的假象）。
  * 每个模式（开关 False / True）各跑 --multihop-iterations(=50) 次 multi_hop
    （depth 3 / beam 3），逐次计时，最后报 p50 / p95（毫秒）。
  * kw_011 四个索引在测量前创建；翻开关前对合成租户跑退化行预检查，必须为 0
    （合成数据 invalid_at 全程为 NULL，天然满足）。

用法：
    python competition/experiments/explain_valid_range.py                 # 默认跑全部三项
    python competition/experiments/explain_valid_range.py --step preflight
    python competition/experiments/explain_valid_range.py --step explain --explain-entities 3000
    python competition/experiments/explain_valid_range.py --step multihop --multihop-iterations 80
    # 多项：--step 可重复，例如 --step preflight --step explain

仅当 --step 都没给时默认跑全部三项。脚本既可直接运行，也可被 import
（模块加载即把 <repo> 与 <repo>/backend 插入 sys.path；DB 相关 import 懒加载）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid as uuid_mod
from datetime import datetime

# ── 路径装配：让 `database.*` 与 `backend.*` 都可 import（当脚本跑 + 被 import 都行）──
_THIS_FILE = os.path.abspath(__file__)
_EXPERIMENTS_DIR = os.path.dirname(_THIS_FILE)
COMPETITION_DIR = os.path.dirname(_EXPERIMENTS_DIR)
REPO_ROOT = os.path.dirname(COMPETITION_DIR)
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
for _p in (REPO_ROOT, BACKEND_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SEED = 20260923
SCOPE_NOTE = (
    "适用范围：需要真实 PostgreSQL（含 KnowEvo schema 与 kw_011 索引已落地）；"
    "无数据库时本脚本以清晰原因退出，不编造任何测量数字。"
)
DEFAULT_OUTPUT = os.path.join(
    COMPETITION_DIR, "deliverables", "algorithm-probes", "explain_valid_range.json"
)

# kw_011 的四个索引（内联，便于本探针独立创建/存在性检查，不依赖迁移运行器）。
# 与 bench_write_path.py / deploy/sql/migrations/v2.5.5_kw_011_*.sql 保持一致。
INDEX_DDL = {
    "ix_kr_valid_range": (
        "CREATE INDEX IF NOT EXISTS ix_kr_valid_range ON nexent.kg_relation_t "
        "USING GIST (tstzrange(valid_at, COALESCE(invalid_at, "
        "'infinity'::timestamptz), '[)'))"
    ),
    "ix_ke_valid_range": (
        "CREATE INDEX IF NOT EXISTS ix_ke_valid_range ON nexent.kg_entity_t "
        "USING GIST (tstzrange(valid_at, COALESCE(invalid_at, "
        "'infinity'::timestamptz), '[)'))"
    ),
    "ix_kr_current": (
        "CREATE INDEX IF NOT EXISTS ix_kr_current ON nexent.kg_relation_t "
        "(tenant_id, src, rel_type) WHERE invalid_at IS NULL"
    ),
    "ix_kr_hop_rev_cover": (
        "CREATE INDEX IF NOT EXISTS ix_kr_hop_rev_cover ON nexent.kg_relation_t "
        "(tenant_id, dst, rel_type) INCLUDE (src)"
    ),
}
INDEX_NAMES = list(INDEX_DDL.keys())


# ---------------------------------------------------------------------------
# 合成图（确定性，便于复现；与 bench_write_path 同思路）
# ---------------------------------------------------------------------------

def build_synthetic_graph(n_entities: int, n_edges: int, seed: int = SEED):
    """返回 (ents, rels, incident_ids)。incident_ids 是至少在一个关系里出现过的
    稳定 id 列表（用于选多跳种子，保证能往外扩）。"""
    import random

    rng = random.Random(seed)
    classes = ["Drug", "Disease", "Gene", "Symptom"]
    rel_types = ["indicated_for", "contraindicated_for", "targets", "causes"]
    ents: list[dict] = []
    for i in range(n_entities):
        ents.append({
            "stable_id": f"e{i}",
            "name": f"node_{i}",
            "class_ref": rng.choice(classes),
            "props": {"idx": i, "weight": round(rng.random(), 4)},
            "aliases": [{"alias": f"alias_{i}", "type": "synthetic"}],
            "embedding": [round(rng.random(), 6) for _ in range(8)],
            "status": "active",
        })
    incident: set[str] = set()
    rels: list[dict] = []
    for j in range(n_edges):
        a, b = rng.randrange(n_entities), rng.randrange(n_entities)
        while b == a:
            b = rng.randrange(n_entities)
        rels.append({
            "src": f"e{a}",
            "dst": f"e{b}",
            "rel_type": rng.choice(rel_types),
            "claim": f"claim_{j % 997}",
            "props": {"conf": round(rng.random(), 4)},
        })
        incident.add(f"e{a}")
        incident.add(f"e{b}")
    return ents, rels, sorted(incident)


# ---------------------------------------------------------------------------
# DB 连接探测 / 索引确保 / 预检查
# ---------------------------------------------------------------------------

def _require_connection() -> None:
    """在真正测量前确认能连上 PG。连不上就清晰退出（exit 1），绝不编造数字。"""
    from database.knowevo_db import _get_db_session
    from sqlalchemy import text

    try:
        with _get_db_session() as session:
            session.execute(text("SELECT 1")).scalar()
    except Exception as exc:  # noqa: BLE001 - 我们要干净的失败，而不是 traceback
        print("[error] cannot connect to PostgreSQL: "
              f"{type(exc).__name__}: {exc}")
        print("[error] a reachable PostgreSQL with the KnowEvo schema is required.")
        print(f"[error] env: POSTGRES_HOST/POSTGRES_USER/NEXENT_POSTGRES_PASSWORD/"
              f"POSTGRES_DB/POSTGRES_PORT must be set.")
        print(f"[error] {SCOPE_NOTE}")
        sys.exit(1)


def ensure_indexes(present: bool = True) -> None:
    """创建（或丢弃） kw_011 四个索引。present=True 时创建。
    独立 session：DDL 在 PG 里会隐式提交，单独跑最稳妥。"""
    from database.knowevo_db import _get_db_session
    from sqlalchemy import text

    with _get_db_session() as session:
        for name in INDEX_NAMES:
            if present:
                session.execute(text(INDEX_DDL[name]))
        session.commit()


def run_preflight(tenant: str | None = None) -> dict:
    """① 退化行预检查：invalid_at IS NOT NULL AND invalid_at < valid_at 必须为 0。
    返回两表的计数与安全判定。tenant 可选（限定租户）；默认统计全表。"""
    from database.knowevo_db import _get_db_session, KgRelation, KgEntity
    from sqlalchemy import func, select

    def _count(model, session):
        q = select(func.count()).select_from(model)
        if tenant is not None:
            q = q.where(model.tenant_id == tenant)
        q = q.where(model.invalid_at.isnot(None), model.invalid_at < model.valid_at)
        return session.execute(q).scalar() or 0

    with _get_db_session() as session:
        rel_n = _count(KgRelation, session)
        ent_n = _count(KgEntity, session)
    observed_zero = (rel_n == 0 and ent_n == 0)
    return {
        "query": ("invalid_at IS NOT NULL AND invalid_at < valid_at "
                  "(kg_relation_t + kg_entity_t)"),
        "kg_relation_t_degenerate_rows": int(rel_n),
        "kg_entity_t_degenerate_rows": int(ent_n),
        "required_zero": True,
        "observed_zero": bool(observed_zero),
        "safe_to_enable_range_predicate": bool(observed_zero),
        "note": ("必须为 0：布尔谓词对退化行返回 false，而构造 tstzrange 会抛错 "
                 "(range lower bound must be less than or equal to range upper bound)。"
                 if not observed_zero else
                 "0 行退化，满足翻开关前置条件。"),
    }


# ---------------------------------------------------------------------------
# ③/④ EXPLAIN 对照
# ---------------------------------------------------------------------------

def _compile_select(q) -> str:
    from sqlalchemy.dialects import postgresql

    # 2.0 的 Select 本身即可编译；遗留 Query 需要 .statement。两者都兼容。
    stmt = getattr(q, "statement", q)
    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    return str(compiled)


def _explain(session, sql: str, force_index: bool = False) -> str:
    from sqlalchemy import text

    if force_index:
        session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = session.execute(
        text("EXPLAIN (ANALYZE, BUFFERS) " + sql)).fetchall()
    return "\n".join(str(r[0]) for r in rows)


def _extract_plan_flags(plan: str) -> dict:
    return {
        "uses_ix_kr_valid_range": "ix_kr_valid_range" in plan,
        "uses_ix_ke_valid_range": "ix_ke_valid_range" in plan,
        "bitmap_index_scan": ("Bitmap Index Scan" in plan) or ("BitmapAnd" in plan),
        "index_scan": "Index Scan" in plan,
        "seq_scan": "Seq Scan" in plan,
        "index_only_scan": "Index Only Scan" in plan,
    }


def run_explain(tenant: str, *, entities: int, edges: int,
                seed: int, force_index: bool) -> dict:
    """③/④ 对同一条查询分别用区间谓词 / 布尔谓词跑 EXPLAIN，提取结论。
    EXPLAIN 需要表里有真实统计才会认真考虑 GiST，故本步骤会载入一份确定性
    合成图到独立租户（已打印提示），并在其租户上先跑预检查。"""
    from database.knowevo_db import (
        _get_db_session, KgRelation, KgEntity,
        valid_now, valid_range_contains,
    )
    from sqlalchemy import select

    # 载入确定性合成图（独立租户），确保 EXPLAIN 有真实数据可谈。
    ents, rels, _ = build_synthetic_graph(entities, edges, seed)
    _load_graph(tenant, ents, rels)

    # 翻开关前置：该租户退化行必须为 0。
    pf = run_preflight(tenant=tenant)
    # 建索引（独立 session，DDL 隐式提交）。
    ensure_indexes(present=True)

    as_of = None  # 用 now()，与多跳默认一致
    base_where = [KgRelation.tenant_id == tenant]

    q_range = select(KgRelation.dst).where(
        *base_where, valid_range_contains(KgRelation, as_of=as_of))
    q_bool = select(KgRelation.dst).where(
        *base_where, valid_now(KgRelation, as_of=as_of))
    q_ent_range = select(KgEntity.stable_id).where(
        KgEntity.tenant_id == tenant,
        valid_range_contains(KgEntity, as_of=as_of),
    )

    # EXPLAIN 在独立的「干净」session 里跑（避免上面 DDL 的隐式提交影响事务态）。
    with _get_db_session() as session:
        plan_range = _explain(session, _compile_select(q_range), force_index=force_index)
        plan_bool = _explain(session, _compile_select(q_bool), force_index=force_index)
        plan_ent = _explain(session, _compile_select(q_ent_range),
                            force_index=force_index)

    return {
        "tenant": tenant,
        "synthetic_graph": {"entities": entities, "edges": edges, "seed": seed},
        "preflight_on_tenant": pf,
        "force_seqscan_off": bool(force_index),
        "relation_bool_predicate": {
            "sql": _compile_select(q_bool),
            "plan": plan_bool,
            "flags": _extract_plan_flags(plan_bool),
        },
        "relation_range_predicate": {
            "sql": _compile_select(q_range),
            "plan": plan_range,
            "flags": _extract_plan_flags(plan_range),
        },
        "entity_range_predicate": {
            "sql": _compile_select(q_ent_range),
            "plan": plan_ent,
            "flags": _extract_plan_flags(plan_ent),
        },
        "conclusion": _draw_explain_conclusion(plan_bool, plan_range, plan_ent),
    }


def _draw_explain_conclusion(plan_bool: str, plan_range: str, plan_ent: str) -> dict:
    rb = _extract_plan_flags(plan_range)
    bb = _extract_plan_flags(plan_bool)
    eb = _extract_plan_flags(plan_ent)
    return {
        "range_predicate_used_ix_kr_valid_range": rb["uses_ix_kr_valid_range"],
        "bool_predicate_used_ix_kr_valid_range": bb["uses_ix_kr_valid_range"],
        "range_predicate_used_ix_ke_valid_range": eb["uses_ix_ke_valid_range"],
        "bool_predicate_fell_to_seq_scan": bb["seq_scan"],
        "verdict": (
            "区间谓词命中 GiST 索引、布尔谓词未命中（或退化为 Seq Scan）— "
            "索引下推成立。"
            if (rb["uses_ix_kr_valid_range"] and not bb["uses_ix_kr_valid_range"])
            else "需要在真实 PG 上读取上述 plan 文本人工/自动裁定（可能二者都未命中，"
                 "或规划器在合成小表上选择了 Seq Scan）。"
        ),
    }


# ---------------------------------------------------------------------------
# ⑤ 多跳 p95
# ---------------------------------------------------------------------------

def _load_graph(tenant_id: str, ents: list[dict], rels: list[dict]) -> None:
    """用真实写入路径把合成图落库（幂等，重复跑不报错）。"""
    from backend.services.knowevo.graph_store import PgJsonbGraphStore

    store = PgJsonbGraphStore()
    asyncio.run(store.upsert_entities(tenant_id, ents))
    asyncio.run(store.upsert_relations(tenant_id, rels))


def _pctl(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if q <= 0:
        return s[0]
    if q >= 100:
        return s[-1]
    k = (len(s) - 1) * (q / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f]) + (s[c] - s[f]) * (k - f)


async def _measure_mode(store, tenant_id: str, seeds: list[str],
                        depth: int, beam: int, iterations: int) -> list[float]:
    from backend.services.knowevo.graph_store import HopPlan

    plan = HopPlan(rel_types=None, reverse=False, min_depth=1, max_depth=depth)
    lat: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        await store.multi_hop(tenant_id, seeds, plan, beam=beam, depth=depth)
        lat.append(time.perf_counter() - t0)
    return lat


def run_multihop(*, entities: int, edges: int, seeds_n: int,
                 iterations: int, depth: int, beam: int, seed: int) -> dict:
    """⑤ 用真实 PgJsonbGraphStore 对比 use_range_predicate 关闭/开启的多跳延迟。"""
    from backend.services.knowevo.graph_store import PgJsonbGraphStore

    tenant_id = str(uuid_mod.uuid4())
    ents, rels, incident = build_synthetic_graph(entities, edges, seed)
    _load_graph(tenant_id, ents, rels)

    # 翻开关前置：本租户退化行必须为 0。
    pf = run_preflight(tenant=tenant_id)
    if not pf["observed_zero"]:
        return {
            "skipped": True,
            "reason": "preflight found degenerate rows; refusing to enable range predicate",
            "preflight": pf,
        }

    # 测量前确保 kw_011 索引存在（独立 session）。
    ensure_indexes(present=True)

    # 选种子：至少出现在一个关系里的稳定 id 的前 seeds_n 个。
    seeds = incident[: max(1, seeds_n)]

    results: dict = {"tenant": tenant_id, "seed_count": len(seeds)}
    for label, use_range in (("before_range_predicate_off", False),
                              ("after_range_predicate_on", True)):
        store = PgJsonbGraphStore()
        store.use_range_predicate = use_range
        lat = asyncio.run(_measure_mode(
            store, tenant_id, seeds, depth, beam, iterations))
        results[label] = {
            "use_range_predicate": bool(use_range),
            "iterations": len(lat),
            "latency_s": [round(x, 6) for x in lat],
            "p50_ms": round((_pctl(lat, 50) or 0.0) * 1000.0, 4),
            "p95_ms": round((_pctl(lat, 95) or 0.0) * 1000.0, 4),
            "mean_ms": round((sum(lat) / len(lat)) * 1000.0, 4) if lat else None,
        }

    off = results["before_range_predicate_off"]
    on = results["after_range_predicate_on"]
    p95_off = off["p95_ms"]
    p95_on = on["p95_ms"]
    results["comparison"] = {
        "p95_ms_off": p95_off,
        "p95_ms_on": p95_on,
        "p95_delta_ms": round(p95_on - p95_off, 4),
        "p95_speedup_x": round(p95_off / p95_on, 4) if p95_on else None,
        "note": ("负值代表开启区间谓词后更快；正值为更慢。是否真的改善需在真实 PG 上读取。"),
    }
    results["config"] = {
        "entities": entities, "edges": edges, "seed": seed,
        "seeds_n": seeds_n, "iterations": iterations,
        "depth": depth, "beam": beam,
    }
    results["preflight"] = pf
    return results


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def run_steps(steps: list[str], args) -> dict:
    out: dict = {
        "probe": "explain_valid_range",
        "question": (
            "时间区间索引下推（ix_kr_valid_range / ix_ke_valid_range）是否在真实 PG 上"
            "被规划器选用，且对多跳 p95 有改善？"
        ),
        "scope_note": SCOPE_NOTE,
        "steps_requested": steps,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "results": {},
    }

    # 固定顺序：preflight → explain → multihop（保证预检查在建索引前）。
    for step in ("preflight", "explain", "multihop"):
        if step not in steps:
            continue
        if step == "preflight":
            out["results"]["preflight"] = run_preflight(tenant=args.tenant)
        elif step == "explain":
            out["results"]["explain"] = run_explain(
                tenant=args.tenant or str(uuid_mod.uuid4()),
                entities=args.explain_entities, edges=args.explain_edges,
                seed=SEED, force_index=args.force_index)
        elif step == "multihop":
            out["results"]["multihop"] = run_multihop(
                entities=args.multihop_entities, edges=args.multihop_edges,
                seeds_n=args.multihop_seeds, iterations=args.multihop_iterations,
                depth=args.multihop_depth, beam=args.multihop_beam, seed=SEED)
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="证据采集：时间区间索引下推（§6.6 ①③④⑤）。无 PG 时干净退出。")
    p.add_argument("--step", action="append",
                   choices=["preflight", "explain", "multihop"],
                   help="要跑哪几项；可重复。不给则默认全部。")
    p.add_argument("--tenant", default=None,
                   help="EXPLAIN 使用的租户 UUID（不给则自动生成并载入合成图）。")
    p.add_argument("--output", default=DEFAULT_OUTPUT,
                   help=f"输出 JSON 路径（默认 {DEFAULT_OUTPUT}）")
    # EXPLAIN 相关
    p.add_argument("--explain-entities", type=int, default=2000)
    p.add_argument("--explain-edges", type=int, default=6000)
    p.add_argument("--force-index", action="store_true",
                   help="EXPLAIN 时 SET LOCAL enable_seqscan=off，仅用于演示索引可用。")
    # 多跳相关
    p.add_argument("--multihop-entities", type=int, default=2000)
    p.add_argument("--multihop-edges", type=int, default=6000)
    p.add_argument("--multihop-seeds", type=int, default=10)
    p.add_argument("--multihop-iterations", type=int, default=50)
    p.add_argument("--multihop-depth", type=int, default=3)
    p.add_argument("--multihop-beam", type=int, default=3)
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    steps = args.step or ["preflight", "explain", "multihop"]

    # 真正测量前先确认能连上 PG（连不上清晰退出，不编造数字）。
    _require_connection()

    try:
        out = run_steps(steps, args)
    except Exception as exc:  # noqa: BLE001 - 真实运行期错误：清晰退出，不伪造
        print(f"[error] evidence run failed: {type(exc).__name__}: {exc}")
        print(f"[error] {SCOPE_NOTE}")
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[written] {args.output}")
    print("  steps:", ", ".join(steps))
    if "preflight" in out["results"]:
        pf = out["results"]["preflight"]
        print(f"  preflight: degenerate rel={pf['kg_relation_t_degenerate_rows']} "
              f"ent={pf['kg_entity_t_degenerate_rows']} "
              f"safe_to_enable={pf['safe_to_enable_range_predicate']}")
    if "explain" in out["results"]:
        c = out["results"]["explain"]["conclusion"]
        print("  explain: range_used_ix_kr_valid_range="
              f"{c['range_predicate_used_ix_kr_valid_range']} "
              f"bool_used_ix_kr_valid_range={c['bool_predicate_used_ix_kr_valid_range']}")
    if "multihop" in out["results"] and "comparison" in out["results"]["multihop"]:
        cmp = out["results"]["multihop"]["comparison"]
        print(f"  multihop p95: off={cmp['p95_ms_off']}ms on={cmp['p95_ms_on']}ms "
              f"delta={cmp['p95_delta_ms']}ms")


if __name__ == "__main__":
    main()
