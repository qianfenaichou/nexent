# decision_service.py —— K3 双驱动编排、证据链组装、决策卡（L3 核心）
**归属任务**: T-09 · 依赖: kg_service/kg 查询面、instructor（结构化输出）、Nexent 原生 doc_search
**依据**: [备忘录 04-K3](../../../../../02-技术方案.md)

## 职责
路由器 → 双路执行（检索路直答 / 推理路束搜索）→ 证据链组装与融合 → 决策卡生成（含校准/反事实）→ 落库带知识版本戳。**被 Skill 层调用（skill: reasoning-path 等），也是 kg_multi_hop 等 MCP 工具的服务端底座**。

## 接口冻结

```python
class DecisionService:
    # ── 路由 (备忘录 04 §1.2: 规则前置 + few-shot 分类) ──────
    def route(self, question: str, ctx: ConversationCtx) -> Route:
        """L1: LOOKUP_RULES (6 rules) → R;  version-compare words → RM
        L2: small-tier few-shot classify {R,M,RM} (~200 tok)
        L3: conf<0.7 → RM (default-safe);  signature-failure>=2 → escalate."""
    def route_hit_feedback(self, question: str, route: Route, correct: bool) -> None:
        """Feeds route-hit-rate metric per question type (K4 E6 实验)."""

    # ── 推理路: 束搜索多跳 (备忘录 04 §2.1) ──────────────────
    async def multi_hop(self, question: str, seeds: list[str] | None,
                        depth: int = 3, beam: int = 3) -> list[Path]:
        """plan_hops (LLM) → iteratively expand (GraphStore.multi_hop 1-hop)
        → score_path (relevance+evidence-richness+conflict-signal) → top-k
        → early termination on answerable. Failed paths kept for counterfactual."""
    async def calibrate_hops(self, questions: list[str]) -> HopCurve:
        """depth∈{1,2,3,4} grid on 30 multi-hop questions → acc/token/latency
        curve. Fixes KW_MULTIHOP_MAX_DEPTH (L5). PPT material."""

    # ── 证据链组装与融合 (双路并发时) ─────────────────────────
    async def assemble_evidence(self, paths: list[Path], doc_hits: list[DocHit]) -> EvidenceChain:
        """Doc channel → EXTRACTED priority; KG channel → connectivity.
        Cross-channel conflict → contested flag + 'knowledge inconsistency'
        note on card (honest-traceability)."""

    # ── 决策卡 ─────────────────────────────────────────────
    async def render_card(self, question: str, chain: EvidenceChain,
                          mode: Literal["full", "lite"] = "full") -> DecisionCard:
        """instructor-structured. Candidates×evidence_chain×calibrated_conf×
        risks×counterfactual(top-1 only, from failed paths)×knowledge_stamp.
        healthcare domain rule: disclaimer auto-appended (domain param)."""
    def calibrate(self, raw_conf: float) -> float:
        """10-bucket empirical calibration lookup (from eval_run_t.calibration).
        ECE<=0.10 target."""

    # ── 落库与重算 ─────────────────────────────────────────
    async def persist(self, card: DecisionCard, session_id: UUID) -> UUID: ...
    async def rerun_marked(self, tenant_id: UUID) -> list[UUID]:
        """Batch re-render needs_rerun cards under current knowledge stamp.
        Old-vs-new conclusion diff auto-recorded (Q2 台账素材)."""
```

## 数据契约
决策卡 JSON schema 冻结于备忘录 04-K3 §3（question_id/knowledge_stamp/candidates/evidence_chain/risks/counterfactual/decision/uncertainty_notes）。`evidence_chain` 每项 = `{claim, provenance:{doc, span, kg_path[]}, tag, source_channel}`。

## 延迟预算分解（p95 ≤30s 硬顶，备忘录 04 §1.1）
路由 ≤2s · 检索路 ≤8s · 推理路 ≤25s（3 跳×3 束×~2s/跳 LLM + 图查询 <1.5s）· 组装+卡 ≤5s。超预算降级顺序：beam 3→2 → depth 3→2 → 推理路退化为检索路+说明。

## 验收锚点
- `pytest test/backend/services/knowevo/test_decision_service.py -v`：路由三分支与默认 RM、束搜索提前终止、双通道冲突→contested、卡 schema 校验、校准查表、rerun 差异记录。
- T-09 实测：30 题延迟分布（p95 达标）+ 跳数曲线（calibrate_hops）。
