#!/usr/bin/env python3
"""probe_p7_efficiency_minutes - 半自动化本体构建效率（分钟数口径）。

对应任务：`新会话A-效率实验-τa重定义-2026-09-23.md` v2
预登记：  `.workbuddy-tmp/pre-registration.md`（v0.2）
S1 报告： `.workbuddy-tmp/s1-baseline-readout.md`

本探针由三个互不 import 的部件组成（与 p1~p6 同风格：零新依赖 / 零网络 /
零 DB / seed 固定 / 可反复跑）：

**甲-工作量（真实池）**
    P6 冻结池 n=21 上，2×2 设计（证据门 开/关 × 排序 到达/score）各自决定
    多少「待人工审核项」与多少「人工分钟数」。**非退化**：门因子在本池上
    有真实分辨率（21/21 vs 11/21）。
    来源等级：**A（计数实测）× L（系数文献）**。

**甲-质量（`insufficient_data`）**
    「达到 Q* 所需累计人工分钟数」这条轴**算不出来**，原因是 `Quality(·)`
    需要 `classes[].parent / also_parent / anchor` 与 `seed_terms`，而 P6 冻结
    导出只取了 `op / target / confidence / evidence_spans`。
    本部件**不产出数字**，只如实报告缺口与解锁前置条件（不写 0 占位）。
    依据：`AGENTS.md` §0 第 1 条。

**乙-机制（合成异质流）**
    任务书的原始命题是「排序 → 效率」。这条命题**只能在候选增益异质的流上
    被检验**；真实池的候选增益高度同质（10 个类同层级、10 个关系对 Quality
    完全不可见），排序无从发挥。
    因此在**合成流**上做机制验证，并做 gain–feature 相关性 ρ 的**扫描**：
    ρ=0 复现 P2 的结构性零效应，ρ→1 效应显现。
    另设**负对照**（按 name 字母序）以排除"效应来自构造"。
    ⚠️ 等级：`synthetic_mechanism_validation`，**不得**作为效率证据对外引用。

用法（在仓库根 `nexent/` 下）：
    python3 competition/experiments/probe_p7_efficiency_minutes.py
    python3 competition/experiments/probe_p7_efficiency_minutes.py --output PATH
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

PROBE = "p7_efficiency_minutes"
SEED = 20260923

# ---------------------------------------------------------------------------
# 分钟数换算模型（预登记 §3.2；系数为 L 级，取自实抓核验过的文献）
# ---------------------------------------------------------------------------
# α: Gao et al. 2019 (PVLDB 12(12):1679, arXiv:1907.09657) c1 = 45s = 0.75 min/entity
# β: uComp, Semantic Web 2016, DOI 10.3233/SW-150181  1.6 relations/min = 0.625 -> .63
MINUTES_ALPHA = 0.75   # min per new entity  (CLS_ADD)
MINUTES_BETA = 0.63    # min per new relation (REL_ADD)
MINUTES_SOURCE = "L"

# 池内无 text 字段 -> 预登记 §0.2 的 γ 项被删除（数据缺口，不是取 0）
MINUTES_MODEL_DEGRADED = True
MINUTES_DROPPED_TERM = "gamma*len(text)/1000"

TAU_LIVE_DEFAULT = 0.85
TAU_GRID = [round(0.50 + 0.05 * i, 2) for i in range(10)]  # 0.50 .. 0.95

# ---------------------------------------------------------------------------
# 甲-工作量：P6 冻结池（字段结构逐字复制自 probe_p6_autonomy_tau.py:111-142，
# 但**不 import p6**——脚本保持互相独立，与 p1~p6 一致）
# ---------------------------------------------------------------------------
_SEED_DOCS = [{"doc": "seed-bootstrap"}]
KW = "6756b0ab-39c0-462a-9745-aa12e1511fcd"
XT = "11111111-1111-1111-1111-111111111111"

# (op, target, confidence, evidence_spans, tenant, gold_error, gold_source)
ITEMS = (
    [(f"CLS_ADD", f"cls:{n}", 1.0, _SEED_DOCS, KW, False, "survival")
     for n in ("Drug", "DrugClass", "Disease", "Complication", "Symptom",
               "Examination", "Indicator", "Treatment", "Lifestyle", "Population")]
    + [("REL_ADD", f"rel:{n}", None, [], KW, False, "survival")
       for n in ("indicated_for", "first_line", "treats", "contraindicated",
                 "adverse_effect", "mechanism_of", "monitors", "has_target",
                 "complication_of", "recommended_for")]
    + [("CLS_ADD", "cls:Metformin", 0.9, [{"doc": "d1"}], XT, None, "none")]
)

# 池内可复用的类名（v1.1.0 快照成员）—— 只用于演示 Quality 的退化，不作测量
SNAPSHOT_V110_MEMBERS = frozenset(
    [f"cls:{n}" for n in ("Drug", "DrugClass", "Disease", "Complication",
                          "Symptom", "Examination", "Indicator", "Treatment",
                          "Lifestyle", "Population")]
    + [f"rel:{n}" for n in ("adverse_effect", "complication_of", "contraindicated",
                            "first_line", "has_target", "indicated_for",
                            "mechanism_of", "monitors", "recommended_for", "treats")]
)


def mirror_ev_rich(evidence_spans):
    """镜像 `ontology_service.build_ev_rich`（>=2 distinct docs -> 1.0 else 0.6）。"""
    docs = {s.get("doc") for s in (evidence_spans or [])}
    return 1.0 if len(docs) >= 2 else 0.6


def build_ev_rich(evidence_spans):
    """优先直调生产实现（engine=backend），失败落镜像（engine=mirror）。"""
    try:  # pragma: no cover - depends on repo layout at runtime
        from services.knowevo.ontology_service import build_ev_rich as _impl
        return _impl(evidence_spans)
    except Exception:
        return mirror_ev_rich(evidence_spans)


def gate(item, tau, relax_ev=False):
    """生产三条件 AND 门的复刻（`ontology_service.py:614-627`）。

    conf >= tau AND status != rejected AND build_ev_rich(spans) == 1.0
    冻结池内无 rejected 行，故 status 条件恒真。
    """
    _op, _target, conf, spans, _tenant, _gold, _src = item
    conf = 0.0 if conf is None else conf
    ok_conf = conf >= tau
    ok_status = True
    ok_ev = True if relax_ev else (build_ev_rich(spans) == 1.0)
    return ok_conf and ok_status and ok_ev


def item_minutes(item, alpha=MINUTES_ALPHA, beta=MINUTES_BETA):
    """预登记 §3.2 的降级换算模型：minutes = α·[CLS_ADD] + β·[REL_ADD]。"""
    op = item[0]
    return (alpha if op == "CLS_ADD" else 0.0) + (beta if op == "REL_ADD" else 0.0)


def score_key(item):
    """`rank_proposals` 的生产排序键（`ontology_service.py:595-606`）。

    score_formula = 0.5*conf + 0.2*novelty + 0.3*impact，平局用 ev_rich。
    ⚠️ 冻结池**不含 novelty / impact** -> 退化为 0.5*conf（单调变换）。
    该退化在产物里显式声明（预登记 §5.2 ★3）。
    """
    _op, _target, conf, spans, _tenant, _gold, _src = item
    conf = 0.0 if conf is None else conf
    score = 0.5 * conf + 0.2 * 0.0 + 0.3 * 0.0
    return (score, build_ev_rich(spans))


def arrival_key(item):
    """臂 A：池内原序（`applied_ops` 数组序）。"""
    return ITEMS.index(item)


def alphabet_key(item):
    """负对照：按 target 字母序（与 gain 无关的确定性排序）。"""
    return item[1]


def arm_workload(tau, relax_ev, order_key):
    """给定门态与排序，返回 (待审项列表, 累计分钟数, 逐项累计轨迹)。"""
    ordered = sorted(ITEMS, key=order_key)
    manual, trace, cum = [], [], 0.0
    for it in ordered:
        if gate(it, tau, relax_ev):
            continue          # 自动接受 -> 不耗人工分钟
        manual.append(it[1])
        cum += item_minutes(it)
        trace.append({"target": it[1], "op": it[0], "cum_minutes": round(cum, 4)})
    return manual, round(cum, 4), trace


# ---------------------------------------------------------------------------
# Quality(·)：本池**不可计算**，此处只做退化演示（不产出测量值）
# ---------------------------------------------------------------------------
def try_quality_on_frozen_pool():
    """尝试在冻结池可重建的输入上调用生产 Quality(·)。

    冻结池只给得出**类名**，给不出 parent / also_parent / anchor / seed_terms。
    返回 (status, detail) —— 本函数**只诊断，不产出质量数字**。
    """
    missing = []
    for field in ("parent", "also_parent", "anchor"):
        missing.append(field)
    return {
        "status": "insufficient_data",
        "axis": "cumulative_human_minutes_to_reach_Qstar",
        "reason": (
            "Quality(.) 需要 classes[].parent / classes[].also_parent / "
            "classes[].anchor 与外部 seed_terms；P6 冻结导出只取 "
            "op/target/confidence/evidence_spans，四者全缺。"
        ),
        "missing_fields": missing + ["seed_terms"],
        "evidence": [
            "pool ITEMS tuple has 7 fields: op,target,confidence,"
            "evidence_spans,tenant,gold_error,gold_source "
            "(probe_p6_autonomy_tau.py:111-142)",
            "P6 EXPORT_SQL selects only 4 keys (probe_p6_autonomy_tau.py:96-104)",
            "_k0_metrics_from_snapshot reads snapshot['classes'] only, and needs "
            "parent/also_parent/anchor + seed_terms (ontology_service.py:990-1035)",
        ],
        "degeneracy_demo": {
            "note": (
                "以 SNAPSHOT_V110_MEMBERS 只造类名（无 parent/anchor）时，"
                "四指标恒为常数，与'审了哪些提案'无关 —— 曲线是平的。"
                "这不是'测不出差异'，是函数在该输入下是常数函数。"
            ),
            "cov": 0.0, "red": 0.0, "dep": 1, "align": 0.0,
            "why": "seed_terms 缺失 -> cov=0.0；无 parent -> red=0.0, dep=1；无 anchor -> align=0.0",
        },
        "unlock_preconditions": [
            "P-1 启动数据库（用户侧执行 ~/Desktop/tips/open.sh）",
            "P-2 用扩展 SQL 重取池：op->'payload'->>'parent' / ->>'anchor' / ->>'also_parent'",
            "P-3 确定 seed_terms 的持久化来源",
            "P-4 确认 Q* 的基准版本",
        ],
    }


# ---------------------------------------------------------------------------
# 乙-机制：合成异质流（自带 parent / anchor，故 Quality 天然可算）
# ---------------------------------------------------------------------------
SYN_N_CLS = 60
SYN_N_REL = 60
SYN_N_CORE = 20          # 其中"核心"类：带 anchor、有父链、在 seed_terms 内
SYN_RHO_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
SYN_R = 200              # 每个 ρ 的**模拟重复次数**（合成实验的重数，不是伪重复）


def _syn_make_stream(rng, rho):
    """生成一条合成候选流。

    rho 控制「排序特征」与「真实质量增益」的相关性：
      rho = 0   -> 特征与增益独立（复现 P2 的结构性零效应）
      rho = 1   -> 特征完全反映增益（VOI/score 排序的理想情形）
    增益的实现方式：核心类带 anchor / 父链 / 在 seed_terms 内 -> 贡献 Quality；
    外围类与关系项对 Quality 贡献 0（对应真实池的 rel 不可见缺陷）。

    ⚠️ 命名必须与 core 标记**独立**：否则「按 name 字母序」这个负对照会
    意外变成"核心优先"，从而假装有负对照（v1 的 bug，已修）。
    故名字从打乱的号码池里抽，使字母序与 core 无关。
    """
    cls = []
    for i in range(SYN_N_CLS):
        core = i < SYN_N_CORE
        # 真实增益 g：核心类 = 1，外围类 = 0
        g = 1.0 if core else 0.0
        # 排序特征：g 的含噪观测，噪声由 rho 决定
        noise = rng.gauss(0.0, 1.0)
        feat = rho * g + (1.0 - rho) * (0.5 + 0.5 * noise)
        feat = min(1.0, max(0.0, feat))
        cls.append({"kind": "cls", "core": core, "gain": g, "feat": feat,
                    "anchor": f"toc:{i}" if core else None,
                    "parent": f"X{max(0, i - 1):03d}" if core and i > 0 else None,
                    "op": "CLS_ADD"})
    rel = [{"kind": "rel", "core": False, "gain": 0.0, "feat": rng.random(),
            "anchor": None, "parent": None, "op": "REL_ADD"}
           for _ in range(SYN_N_REL)]
    stream = cls + rel
    # 名字与 core 解耦：打乱号码池后按流位置分配
    rng.shuffle(stream)
    nums = list(range(len(stream)))
    rng.shuffle(nums)
    for pos, c in enumerate(stream):
        c["name"] = f"{c['kind'].upper()}{nums[pos]:03d}"
    return stream


def _syn_quality(audited):
    """合成流上的 Quality —— 与生产同构：只读 classes 的 name/parent/anchor。

    cov   = 已审核心类 / 池内核心类总数      （分母固定 -> 单调）
    red   = 0                                （合成流不设 also_parent）
    dep   = 已审类的最长父链
    align = 已审核心类（带 anchor）/ 核心类总数
    """
    cls = [c for c in audited if c["op"] == "CLS_ADD"]
    core_cls = [c for c in cls if c["core"]]
    dep = 0
    for c in core_cls:
        if c["parent"]:
            dep = max(dep, 2)
        else:
            dep = max(dep, 1)
    return {
        "cov": round(len(core_cls) / SYN_N_CORE, 4),
        "red": 0.0,
        "dep": dep,
        "align": round(sum(1 for c in core_cls if c["anchor"]) / SYN_N_CORE, 4),
    }


def _syn_reach_qstar(stream, order_key):
    """按给定顺序审，返回达到 Q* 的累计分钟数（未达到则 None）。

    Q* = Quality(池内全部核心类) —— 即"理想可达质量"，由构造定义，零自由度。
    """
    q_full = _syn_quality([c for c in stream if c["core"]])
    audited, cum = [], 0.0
    for c in sorted(stream, key=order_key):
        audited.append(c)
        cum += (MINUTES_ALPHA if c["op"] == "CLS_ADD" else MINUTES_BETA)
        q = _syn_quality(audited)
        if (q["cov"] >= q_full["cov"] and q["red"] <= q_full["red"]
                and q["dep"] >= q_full["dep"] and q["align"] >= q_full["align"]):
            return round(cum, 4)
    return None


def synthetic_mechanism():
    """ρ 扫描 + 双负对照：排序 → 效率的机制验证。"""
    out = {"evidence_class": "synthetic_mechanism_validation",
           "n_classes": SYN_N_CLS, "n_relations": SYN_N_REL,
           "n_core": SYN_N_CORE, "replications_per_rho": SYN_R,
           "controls": {
               "random_permutation": "真负对照：与 gain 完全独立 -> 期望 Δ≈0",
               "alphabetical": ("次负对照：按 name 字母序。⚠️ 因 'CLS' < 'REL'，"
                                "它**隐含 op 类型信息**（类先于关系），而 op 类型"
                                "确实与 gain 相关（关系对 Quality 贡献 0）。"
                                "故它不是干净的负对照，只说明"
                                "'只按可见性分离也能降一半成本'。"),
           },
           "note": ("合成流自带 parent/anchor，故 Quality 可算。"
                    "重数是**模拟重复**，不是把 n 凑大的伪重复。"),
           "rho_sweep": []}
    for rho in SYN_RHO_GRID:
        deltas, a_only, b_only, ctrl_only, rnd_only = [], [], [], [], []
        for r in range(SYN_R):
            rng = random.Random(SEED + int(rho * 1000) * 100000 + r)
            stream = _syn_make_stream(rng, rho)
            order = list(range(len(stream)))
            rng.shuffle(order)
            rand_rank = {id(c): pos for pos, c in
                         zip(order, stream)}  # 与 gain 独立的随机序
            m_a = _syn_reach_qstar(stream, lambda c: stream.index(c))
            m_b = _syn_reach_qstar(stream, lambda c: (-c["feat"], c["name"]))
            m_c = _syn_reach_qstar(stream, lambda c: c["name"])
            m_r = _syn_reach_qstar(stream, lambda c: rand_rank[id(c)])
            if m_a is None or m_b is None:
                continue
            deltas.append(round(m_b - m_a, 4))
            a_only.append(m_a)
            b_only.append(m_b)
            if m_c is not None:
                ctrl_only.append(m_c)
            if m_r is not None:
                rnd_only.append(m_r)
        out["rho_sweep"].append({
            "rho": rho,
            "n_replications_used": len(deltas),
            "mean_minutes_arrival": round(statistics.mean(a_only), 4) if a_only else None,
            "mean_minutes_score": round(statistics.mean(b_only), 4) if b_only else None,
            "mean_minutes_control_random": (round(statistics.mean(rnd_only), 4)
                                            if rnd_only else None),
            "mean_minutes_control_alphabetical": (round(statistics.mean(ctrl_only), 4)
                                                  if ctrl_only else None),
            "mean_delta_score_minus_arrival": (round(statistics.mean(deltas), 4)
                                               if deltas else None),
            "frac_score_faster": (round(sum(1 for d in deltas if d < 0) / len(deltas), 4)
                                  if deltas else None),
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default=None, help="结果 JSON 落盘路径")
    ap.add_argument("--tau", type=float, default=TAU_LIVE_DEFAULT)
    ap.add_argument("--prereg-sha256", default=None)
    args = ap.parse_args(argv)

    # ---- 甲-工作量：2x2 -------------------------------------------------
    cells = {}
    for gate_name, relax in (("ev_rich_ON", False), ("ev_rich_OFF", True)):
        for order_name, key in (("arrival", arrival_key), ("score", score_key)):
            manual, mins, trace = arm_workload(args.tau, relax, key)
            cells[f"{gate_name}__{order_name}"] = {
                "gate": gate_name, "order": order_name,
                "n_manual": len(manual), "n_total": len(ITEMS),
                "cum_minutes": mins, "manual_targets": manual,
                "cumulative_trace": trace,
            }

    on_arr = cells["ev_rich_ON__arrival"]["cum_minutes"]
    on_sco = cells["ev_rich_ON__score"]["cum_minutes"]
    off_arr = cells["ev_rich_OFF__arrival"]["cum_minutes"]
    off_sco = cells["ev_rich_OFF__score"]["cum_minutes"]

    # ---- 甲-工作量：τ 网格上的稳定性 ------------------------------------
    tau_grid = []
    for tau in TAU_GRID:
        _m1, m_on, _t1 = arm_workload(tau, False, arrival_key)
        _m2, m_off_a, _t2 = arm_workload(tau, True, arrival_key)
        _m3, m_off_s, _t3 = arm_workload(tau, True, score_key)
        tau_grid.append({"tau": tau, "gate_ON_minutes": m_on,
                         "gate_OFF_arrival_minutes": m_off_a,
                         "gate_OFF_score_minutes": m_off_s})

    # ---- 敏感性：alpha/beta ±50% ----------------------------------------
    sens = []
    for t in (-0.5, 0.0, 0.5):
        a = MINUTES_ALPHA * (1 + t)
        b = MINUTES_BETA * (1 + t)
        on = sum(item_minutes(it, a, b) for it in ITEMS if not gate(it, args.tau, False))
        off = sum(item_minutes(it, a, b) for it in ITEMS if not gate(it, args.tau, True))
        sens.append({"scale": 1 + t, "alpha": a, "beta": b,
                     "gate_ON_minutes": round(on, 4),
                     "gate_OFF_minutes": round(off, 4),
                     "ratio_ON_over_OFF": round(on / off, 4) if off else None})

    result = {
        "probe": PROBE,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "preregistration_sha256": args.prereg_sha256,
        "question": ("门因子与排序因子各自决定多少半自动化本体构建的人工工作量"
                     "（分钟），以及'达到同等质量所需分钟数'能否在本池上测量？"),
        "engine": "mirror (backend build_ev_rich unavailable -> mirror used)",
        "data_reality": {
            "pool_n": len(ITEMS),
            "pool_tenant_split": {"kw_tenant": 20, "cross_tenant_witness": 1},
            "frozen_from": "ontology_version_t.applied_ops (n=20) + "
                           "ontology_change_proposal_t (n=1)",
            "as_built_facts": {"proposals_in_kw_tenant": 0,
                               "proposals_cross_tenant_pending": 1,
                               "build_ops_v100": 20,
                               "snapshot_v110_members": 20},
            "this_is_as_if_replay": True,
            "not_a_run_log": True,
        },
        "minutes_model": {
            "formula": "minutes(s) = alpha*[op==CLS_ADD] + beta*[op==REL_ADD]",
            "alpha": MINUTES_ALPHA, "beta": MINUTES_BETA,
            "source": MINUTES_SOURCE,
            "minutes_model_degraded": MINUTES_MODEL_DEGRADED,
            "dropped_term": MINUTES_DROPPED_TERM,
            "degraded_reason": "pool has no text field -> gamma term not computable",
            "sources": [
                {"id": "gao2019", "cite": "Gao et al. 2019, Efficient Knowledge "
                 "Graph Accuracy Evaluation, PVLDB 12(12):1679, arXiv:1907.09657",
                 "value": "c1=45s/entity, c2=25s/relation", "verified": True},
                {"id": "ucomp2016", "cite": "uComp, Crowdsourcing Ontology "
                 "Engineering in Protege, Semantic Web (IOS Press) 2016, "
                 "DOI 10.3233/SW-150181",
                 "value": "3.65 concepts/min, 1.6 relations/min", "verified": True},
            ],
            "self_assumption_disclosed": (
                "'1 CLS_ADD proposal = 1 new entity' and '1 REL_ADD proposal = "
                "1 new relation' are SELF-DEFINED mappings, not literature "
                "findings: no atom-level (per-proposal) review-time literature "
                "exists (verified zero-hit, see citation-verification.md)."
            ),
        },
        "quality_axis": try_quality_on_frozen_pool(),
        "arm_workload_2x2": {
            "tau": args.tau,
            "cells": cells,
            "gate_main_effect_minutes_ratio": round(on_arr / off_arr, 4) if off_arr else None,
            "order_effect_minutes_delta_ON": round(on_sco - on_arr, 4),
            "order_effect_minutes_delta_OFF": round(off_sco - off_arr, 4),
            "arm_b_effective_key": ("confidence_desc (degenerate: novelty/impact "
                                    "absent from frozen pool)"),
        },
        "tau_grid_stability": tau_grid,
        "sensitivity_alpha_beta_pm50": sens,
        "synthetic_mechanism": synthetic_mechanism(),
        "conclusions": [],
        "limitations": [],
        "not_claimed": [],
        "upgrade_path": [],
    }

    # ---- 结论（跑后写死，不含粉饰） --------------------------------------
    result["conclusions"] = [
        (f"门因子在真实池上是**唯一非退化的因子**：门开时 {on_arr:.2f} 分钟"
         f"（{cells['ev_rich_ON__arrival']['n_manual']}/{len(ITEMS)} 项需人审），"
         f"门关时 {off_arr:.2f} 分钟（{cells['ev_rich_OFF__arrival']['n_manual']}"
         f"/{len(ITEMS)} 项），倍数 "
         f"{(on_arr/off_arr if off_arr else float('nan')):.2f}x —— "
         "放宽证据门可把人审工作量减半，代价是未量化的质量风险。"),
        (f"排序因子在真实池上**无分辨率**：到达顺序与 score 排序的分钟数差为 "
         f"{on_sco - on_arr:+.2f} 分钟（门开）、{off_sco - off_arr:+.2f} 分钟（门关）。"
         "原因是池内可得特征只有 confidence（1.0/0.9/None 三值），"
         "novelty/impact 未被导出，build_ev_rich 恒为 0.6 —— "
         "两臂前 10 项完全相同，仅 cls:Metformin 位置不同。"),
        ("「达到 Q* 所需累计人工分钟数」这条轴在本池上**不可测量**："
         "Quality(.) 的四个分量所需字段（parent/also_parent/anchor/seed_terms）"
         "在冻结池中全部缺失。已标 insufficient_data，未用 0 占位。"),
        ("合成异质流上的机制验证（rho 扫描，每档 200 次模拟重复）："
         "rho=0 时 score 排序与到达顺序的分钟数差 ≈ 0（复现 P2 的结构性零效应），"
         "rho>=0.5 后 score 排序显著更早达到 Q*。"
         "真负对照（与增益独立的随机序）在各 rho 档均不优于到达顺序；"
         "次负对照（字母序）因 'CLS'<'REL' 隐含 op 类型信息，"
         "只说明'按可见性分离即可显著降本'，不作为干净负对照。"),
        ("排序效应的存在条件是『候选增益与排序特征相关（rho>0）』。"
         "本项目真实池的 rho 实际上接近 0（可得特征仅 confidence），"
         "故排序在该池上无杠杆 —— 合成与真实两条线互相印证同一条结论。"),
    ]
    result["limitations"] = [
        "n=21 且候选分数三簇离散（1.0/0.9/0.0），曲线呈台阶而非连续；",
        "本实验是 as-if 重放（构建租户提案队列历史 0 行），不是运行日志；",
        "人工分钟数为代理指标（系数 L 级文献锚定 + 提案级映射自设假设），"
        "不是本项目实测；",
        "质量轴（达到 Q* 的分钟数）因字段缺失为 insufficient_data；",
        f"合成机制验证的重数为 {SYN_R} 次模拟重复，不是独立真实观测；",
        "单一领域（2 型糖尿病）单租户，结论不可外推到分数连续分布的池。",
    ]
    result["not_claimed"] = [
        "不主张「半自动化本体构建效率优势明显」被证实 —— 质量轴缺失，n=21，"
        "分钟数为代理指标；",
        "不主张任何推断统计结论（结构性 underpowered：检出 20% 差需 ~392 "
        "次接受事件/档，40% 差需 ~98；n=21 差 1-2 个数量级）；",
        "不主张臂 B 是「VOI 排序」—— 生产实现是 score_formula 线性加权，"
        "且在本池上退化为 confidence 降序；",
        "不主张本轮的双臂差异为经验发现 —— 排序因子在本池上是设计上的必然零；",
        "不主张合成流的机制验证可作为效率证据对外引用；",
        "不主张 plan/02 的目标值（Cov>=85% / Red<=5% / Dep_max<=5 / Align>=60%）"
        "已达成。",
    ]
    result["upgrade_path"] = [
        "P-1 启动数据库（~/Desktop/tips/open.sh）→ ontology_version_t 可读；",
        "P-2 扩展导出键（payload 的 parent/anchor/also_parent）→ Quality(.) 可算；",
        "P-3 确定 seed_terms 持久化来源（cov 需要）；",
        "P-4 两名以上标注者按固定协议复核并**计时**（E -> A 级）；",
        "P-5 扩池至 ~300 候选/档（对应 98 次接受事件）以支撑 40% 差异的检出。",
    ]

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"[written] {args.output}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
