# alignment_service.py —— K2 对齐裁决 + K5 变更检测与受影响面
**依赖**: kg_service
**依据**: [架构决策记录](../../../docs/adr/0001-0008-已冻结架构决策.md)（ADR-0008 弃用优先策略）· 三段式变更检测设计（详见本文档接口冻结节）

## 职责
两个内聚职责共享本文件（对齐裁决的 LLM 通道与变更检测的 LLM 通道是同类"证据对比"任务）：① 实体对齐第 2 级 LLM 裁决；② 文档版本三段式变更检测；③ 受影响面分析；④ 更新提案生成。

## 接口冻结

```python
class AlignmentService:
    # ── 对齐裁决 (第2级) ───────────────────────────────
    async def adjudicate(self, entity: Entity, candidates: list[Entity]) -> Adjudication:
        """Input both sides' evidence summaries; output merge/new/split + reason
        + confidence. >=0.9 execute, else human queue."""

    # ── 变更检测: 三段式 ───────────────────
    async def detect_doc_change(self, old_doc: UUID, new_doc: UUID) -> DocDiff:
        """STEP1 section-tree align (deterministic DP, tree edit distance)
        STEP2 paragraph semantic align (Hungarian on embedding sim matrix;
        0.85+ pass, 0.60-0.85 LLM adjudicate)
        STEP3 change classify per aligned pair {ADD|UPDATE|DELETE|MOVE|
        RENUMBER|SPLIT|MERGE} + proposition-level diff extraction.
        Tables: deterministic cell-hash diff (zero LLM)."""
    async def calibrate_detection(self, sampled: list[ChangeJudgment]) -> PRScore:
        """60-judgment human audit → precision>=0.90 / recall>=0.85 gate.
        Below gate: widen STEP2 LLM adjudication to all matched paragraphs
        (+0.3M tokens/version pair, re-check K8 budget)."""

    # ── 受影响面 (索引查询, 非图遍历) ─────────────────
    async def impact_scope(self, changed_spans: list[SpanRef]) -> ImpactReport:
        """1. evidence lookup: kg_evidence_t by span → E_aff
        2. reverse: GraphStore.reachable_decisions(E_aff) → D_aff
        3. output {E_aff, D_aff, suggested_actions} <100ms @10k cards."""
    async def propose_updates(self, impact: ImpactReport) -> list[UpdateProposal]:
        """Fact-level → kg_service.ingest_new_version queue;
        Ontology-level → ontology_service proposals (trigger_source=
        'standard_update'); Decision-level → D_aff needs_rerun=true."""

    # ── 演示剧本支撑 ──────────────────────────────
    async def precompute_demo_timeline(self, old_doc: UUID, new_doc: UUID) -> DemoBundle:
        """Offline batch of the ①-④ heavy steps; on-site only confirms +
        re-asks. Guardrail: V+F 30-question regression before demo."""
```

## 数据契约
- `DocDiff` = `{section_pairs, changes: [{type, old_span, new_span, key_points[], affected_evidence_ids}]}`
- `ImpactReport` = `{entities: [...], decisions: [{card_id, original_conclusion}], actions: [...]}`
- 变更检测 P/R 落 `doc_version_diff_t.precision/recall`（每次版本对登记）。

## 验收锚点
- `pytest test/backend/services/knowevo/test_alignment_service.py -v`：三段式各步（fixture 双版本文档）、表格确定性 diff、impact_scope 反向命中（预埋决策卡引用变更段证据后必须出现在 D_aff）、P/R 标定函数。
- 端到端：2020→2024 指南演示全链路（演示剧本五步走通）+ 回归护栏（V+F 30 题无回归）。
