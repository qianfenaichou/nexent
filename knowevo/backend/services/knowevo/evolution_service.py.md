# evolution_service.py —— K5 进化轮编排与版本台账（L4）
**归属任务**: T-11 · 依赖: alignment/ontology/kg 三服务 + T-10b 评测
**依据**: [备忘录 06-K5 §2.2](../../../../../02-技术方案.md) · 10 分册（evolution_round_t）

## 职责
把"一次知识变更事件"编排为完整进化轮（记账原子）：触发→执行（调其他服务）→成本归集→（可选）重测→时间线落库。**本服务不直接做算法，只做编排与台账**——各算法在属主服务。

## 接口冻结

```python
class EvolutionService:
    async def start_round(self, trigger: Trigger) -> UUID: ...
        """trigger ∈ {new_docs(ids), standard_update(old,new), manual, correction}.
        Creates evolution_round_t row (status=running)."""

    async def run_standard_update(self, round_id: UUID) -> RoundReport:
        """Orchestrates the K5 chain:
        detect_doc_change → impact_scope → propose_updates →
        [await human confirm via proposal queue] → kg.ingest_new_version +
        ontology.commit_version (if ops) → mark D_aff needs_rerun →
        settle cost (tokens + human_minutes from confirm session logs)."""

    async def settle(self, round_id: UUID, eval_run_id: UUID | None = None) -> None:
        """ops_summary + cost + eval_delta (if retested) written atomically.
        eval_delta = {testset_hash, acc_before, acc_after} per K4 §3.1."""

    async def rollback(self, round_id: UUID) -> UUID:
        """Reverse round: supersede-stamped edges restored (invalid_at cleared),
        ontology version reverted (child version marked deprecated, NOT deleted).
        New corrective round recorded (trigger=correction)."""

    # ── 时间线接口 (L5 看板 + kg_evolution_trace MCP 共用) ───
    async def timeline(self, tenant_id: UUID, since: datetime | None) -> list[RoundSummary]: ...
    async def round_detail(self, round_id: UUID) -> RoundReport: ...
```

## 与三本台账的关系
`evolution_round_t` 是**结构化**的 evolution-log（看板/工具的机器口径）；`competition/docs/evolution-log.md` 是**人写叙事版**（答辩素材：决策背景、踩坑、否决记录）。两边同源不同粒度——轮次 ID 是外键。

## 验收锚点
- `pytest test/backend/services/knowevo/test_evolution_service.py -v`：标准更新全链路（mock 三个下游服务）、settle 原子性（失败中断不留半账）、rollback 恢复现行视图、timeline 聚合。
- T-11 演示验收：D3 剧本 5 步 + rollback 演练 1 次（答辩 Q"更新错了怎么办"的答案）。
