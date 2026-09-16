# kg_service.py —— K2 图谱构建（本体锚定抽取 + 三级对齐 + 增量合并）
**归属任务**: T-06（流水线）/ T-07（存储接线）· 依赖: ontology_service(active 本体)、graph_store、T-02 语料
**依据**: [备忘录 03-K2](../../../../../02-技术方案.md)

## 职责
证据段级抽取（LLM 通道+表格确定性通道）→ 实体对齐三级 → bi-temporal 合并 → 待审池维护 → 更新成本记账。**存储访问全部经 GraphStore**。

## 接口冻结

```python
class KGService:
    # ── 抽取 ────────────────────────────────────────────────
    async def extract(self, span: EvidenceSpan, ontology_summary: str) -> ExtractionResult:
        """LLM channel (mid-tier, instructor). Output entities must carry
        class_ref; unmappable → pending candidate. tag: EXTRACTED|INFERRED."""
    def extract_table(self, table: ParsedTable) -> ExtractionResult:
        """Deterministic channel: col headers→props, rows→entities.
        Zero LLM cost, reproducible (graphify-inspired)."""
    def fewshot_for(self, span: EvidenceSpan) -> list[Example]:
        """3 static + 2 dynamic (retrieved from confirmed-examples pool)."""

    # ── 三级对齐 (备忘录 03 §2) ──────────────────────────────
    async def align(self, entity: Entity) -> AlignDecision:
        """L1: cosine>tau1(0.80) auto-merge → alias edge
           L2: 0.60-0.80 LLM adjudicate (merge/new + reason)
           L3: pending pool for human review
           Record alias_type: brand|generic|abbr on merge."""
    async def calibrate_thresholds(self, labeled_pairs: list[LabeledPair]) -> Thresholds:
        """200 human-labeled pairs → ROC → tau1(false-merge<=2%)/tau2(recall>=95%).
        T-06 first-week experiment; result updates KW_ALIGN_* env."""

    # ── 合并冲突 (备忘录 03 §3.2 规则表) ─────────────────────
    async def merge_delta(self, extractions: list[ExtractionResult]) -> IngestReport:
        """NEW→insert; CONTRA→supersede by (valid_at, authority_level);
        CONTENDED→mark contested + human queue. Never silently overwrite."""
    async def split_entity(self, entity_id: str, criteria: str) -> tuple[str, str]:
        """Error-merge repair: two new entities + DEPRECATE origin + split_into.
        Re-anchor affected edges (batch SQL), record ΔA in extract run."""

    # ── 待审池 ──────────────────────────────────────────────
    async def update_pending_pool(self) -> PendingSummary: ...
    async def pending_to_proposals(self, min_mentions: int = 3) -> None:
        """Hand off high-frequency unmappables → ontology_service.propose_from_pending."""

    # ── 文档版本演进 (K2 §3.3, 由 alignment_service 触发) ────
    async def ingest_new_version(self, old_doc: UUID, new_doc: UUID,
                                 changed_spans: list[SpanRef]) -> VersionIngestReport:
        """Controlled supersede: explicit-negated→invalid_at stamp;
        silent-omitted→source_stale=true (medical default: keep)."""

    # ── 查询面 (MCP 工具薄层) ────────────────────────────────
    async def search(self, query: str, hop: int = 1, top_k: int = 5,
                    ontology_version: str | None = None) -> KGSearchResult: ...
    async def evolution_trace(self, entity_id: str | None, decision_id: str | None) -> Timeline: ...
```

## 数据契约
- `ExtractionResult` = `{entities: [{name, aliases, class_ref, props, tag, evidence_id}], edges: [{src, dst, rel_type, claim, tag, evidence_id}], pending: [...]}`
- `IngestReport` = `{added, merged, superseded, contended, tokens_spent, wall_seconds}`（cost-ledger 自动记账的入口结构）

## 批处理入口
`pipeline/ingest_graph.py`（T-06 CLI）：`python -m services.knowevo.pipeline.ingest_graph --docs <ids|batch.json> --domain healthcare`。断点续跑（extract_run 幂等：span hash 去重）。

## 验收锚点
- `pytest test/backend/services/knowevo/test_kg_service.py -v`：锚定失败→pending 流转、三级对齐各分支、CONTRA 时效性+权威度 tie-break、表格通道与 LLM 通道输出 schema 一致、split 往返。
- T-06 抽检：首批 20 份文档抽取结果人工抽检 50 条，信噪比 ≥70%（否则触发 K1 降级：中→大档，重算 K8 预算）。
