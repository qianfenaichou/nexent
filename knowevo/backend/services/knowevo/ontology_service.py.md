# ontology_service.py —— K1 本体半自动构建流水线（L2 核心）
**归属任务**: T-04（构建）/ T-05（确认 UI 的服务端）/ T-11（增量提案）· 依赖: `knowevo_models.py`(T-03)
**依据**: [备忘录 02-K1](../../../../../02-技术方案.md) · [01-K0](../../../../../02-技术方案.md)

## 职责
种子提取 → 两级提案 → 规则校验 → 排序入队 → 确认环服务端 → 版本化提交 → 质量指标计算。全部业务逻辑在本层；`knowledge_graph_app.py` 只做 HTTP 解析/鉴权。

## 接口冻结（函数签名）

```python
class OntologyService:
    # ── 阶段0: 种子 ─────────────────────────────────────────
    async def extract_seed(self, doc_ids: list[str]) -> SeedSkeleton:
        """Parse standard docs' TOC + term nomination (mid-tier LLM).
        Returns: section tree + top-K concepts per chapter with aliases."""

    # ── 阶段1: 概念提名 (第一级提案) ──────────────────────────
    async def propose_concepts(self, seed: SeedSkeleton, ontology_summary: str | None) -> list[ConceptProposal]:
        """Batch per-chapter, temp=0.2, instructor-structured output."""

    # ── 阶段4: schema 装配 (第二级提案) ──────────────────────
    async def propose_schema(self, confirmed_classes: list[ClassRef], evidence: list[Evidence]) -> list[SchemaProposal]:
        """Props/rel-types per confirmed class. Large-tier model."""

    # ── 校验 (V1-V9, 备忘录 02 §4) ───────────────────────────
    def autofix(self, proposals: list[P]) -> list[P]:
        """V1-V5,V9 auto-repair/reject; V6/V7/V8 flagged for human review."""
    def validate_cycle(self, parent_map: dict[str, str]) -> bool: ...

    # ── 排序与确认环 ────────────────────────────────────────
    def rank_proposals(self, proposals: list[P], weights: Weights = W_DEFAULT) -> list[P]:
        """score = 0.5·conf + 0.2·novelty + 0.3·impact (L6 待 A/B)."""
    async def auto_accept(self, queue: list[P], threshold: float) -> tuple[list[P], list[P]]:
        """conf>=threshold AND validator-pass AND ev_rich==1.0 → auto bucket."""

    # ── 版本化 ──────────────────────────────────────────────
    async def commit_version(self, round_id: UUID, applied_ops: list[Op]) -> OntologyVersion:
        """Persist snapshot JSONB + ops log; bump semver (minor for additions,
        major for deprecations); compute K0 metrics into `metrics` field."""
    async def deprecate(self, class_stable_id: str, reason: str) -> Op: ...

    # ── 增量提案入口 (K1 回流 / K5 触发) ─────────────────────
    async def propose_from_pending(self, pending: list[PendingEntity]) -> list[ConceptProposal]: ...

    # ── 查询面 (工作台/MCP 共用) ─────────────────────────────
    async def get_active(self, tenant_id: UUID) -> OntologySnapshot: ...
    async def diff(self, from_v: str, to_v: str) -> list[DiffOp]: ...
    async def quality_metrics(self, version: str) -> Metrics: ...
```

## 数据契约
- `ConceptProposal` = `{name, aliases[], parent_stable_id?, evidence_spans[], confidence, rationale, impact_hint}`
- `Op`（操作码枚举）= `CLS_ADD|CLS_UPD|CLS_DEPRECATE|CLS_DEL|PROP_ADD|PROP_UPD|PROP_DEPRECATE|REL_ADD|REL_UPD|REL_DEPRECATE|AXIOM_ADD|AXIOM_DEL`（备忘录 01-K0 §1.1）
- 本体序列化注入上限 15000 token（L4 遗留值），超限走"active 类+高频属性"裁剪器。

## 伪代码锚点
见备忘录 02-K1 §6（build_ontology_round 主循环）。增量入口：`propose_from_pending` 与 K5 的 `trigger_source=standard_update` 共用 `ontology_change_proposal_t` 队列。

## 验收锚点
- `pytest test/backend/services/knowevo/test_ontology_service.py -v`：种子提取、排序、auto_accept 线、V1 环检测、版本 commit+diff 往返一致、K0 指标 SQL 正确性（对 fixture 小本体手算对照）。
- 效率对照实验数据出口：`quality_metrics` + 人工基线记录表（备忘录 02 §5）。

## 禁改清单
上游 `backend/services/` 既有文件；本文件为新文件。接线项：无（router 挂载归 knowledge_graph_app → T-03/T-08）。
