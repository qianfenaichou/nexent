# graph_store.py —— A1 图存储抽象与 PG JSONB 适配器
**依赖**: knowevo_models 建表
**依据**: [架构决策记录](../../../docs/adr/0001-0008-已冻结架构决策.md)（ADR-0001 PG JSONB + 递归 CTE）

## 职责
隔离图存储 seam：束搜索/邻域/受影响面等查询的统一接口；PG JSONB 为默认适配器。**调用方（kg_service/decision_service）只 import 本模块，绝不直写 CTE**——这是 seam 纪律（两适配器=真 seam）。

## 接口冻结

> **2026-09-17 契约偏差回填**：实现为多租户语义给全部方法加了 `tenant_id: str` 为首参（全库标准）；`upsert_*` 输入与 `entity_lookup` 返回改用 `dict`/`EntityCard`（自包含 dataclass，避免服务层与 ORM 模型耦合）。方法名、查询行为（现行视图/束搜索/任务归属）不变。

> **2026-09-28 L1 增量参数回填（含漏记补记）**：实现在冻结签名之后追加过**可选尾部参数**，均为加法扩展，方法名、查询行为、返回形状不变：① `neighbors` 与 `multi_hop` 各加 `as_of: datetime | None = None`（版本钉住，见 `version_pin.py`；当时只写进了实现 docstring，本文件漏记，此处补记）；② `multi_hop` 加 `rank: Callable[[Path], float] | None = None`（L1 beam 保留打分器：路径 → 分数，高者先留；**None = v0 按长度保留，行为逐位不变**；调用方把问题闭包进 `rank`，store 保持域无关，保留决策每跳可解释、不引入 PPR 分数——审计性>理论优雅裁决仍有效）。

> **2026-09-28 L1 第3步 增量回填**：`entity_lookup` 实现从「PG ilike 唯一路径」升级为「ES-first + PG ilike 兜底」，**签名零变化**（seam shape is final 维持）。ES 能力经 `PgJsonbGraphStore.__init__` 新增可选尾部参数 `es_client: Any = None` 注入（默认 None = 行为逐位不变，与 L1 第1/2步「可选注入」模式一致）；注入客户端须提供 `entity_search(tenant_id, query, top_k) -> list[EntityCard]`（同步或协程皆可，store 自适应），命中按 ES hybrid 分数降序排在 PG 结果之前、剩余槽位用 ilike 补齐，store 不重排、不引入 PPR/随机游走分数（审计性>优雅裁决仍有效）；客户端未注入 / 抛异常 / 返回空 → 静默落到原 ilike 分支，返回形状不变。ES 写入与索引构建属后续接线范畴，本步只动读路径。

> **2026-09-30 A1 可插拔后端增量回填**：`make_graph_store(backend=None, **kwargs) -> GraphStore` 工厂 + `MemoryGraphStore` 第二实现（stdlib 内存适配器，与 `PgJsonbGraphStore` 同一冻结方法集与结果形状）。`backend` 默认读 `KW_GRAPH_STORE_BACKEND`（const/env，默认 `pg_jsonb`）；`memory|mem|in_memory|inmemory` → `MemoryGraphStore`；`pg_jsonb|pg|postgres|postgresql` → `PgJsonbGraphStore`；未知名 `ValueError` 大声失败。**ABC 冻结方法零增删**；`MemoryGraphStore.register_evidence_refs` 是适配器私有测试/演示钩子（不在 seam）。`multi_hop` 走同一贪心束算法；`as_of=None` 语义 = 现行视图（now），与 `valid_now` 一致。依据：可插拔后端设计评审（解耦性/扩展性硬证据）。契约：`test_graph_store_pluggable.py` 双后端表面 + 工厂选择。

```python
class GraphStore(ABC):
    async def upsert_entities(self, tenant_id: str, ents: list[dict]) -> None: ...
    async def upsert_relations(self, tenant_id: str, rels: list[dict]) -> None: ...
    async def neighbors(self, tenant_id: str, entity_ids: list[str],
                        rel_types: list[str] | None = None,
                        hop: int = 1, valid_view: bool = True) -> Subgraph: ...
    async def multi_hop(self, tenant_id: str, seeds: list[str], hop_plan: HopPlan,
                        beam: int = 3, depth: int = 3) -> list[Path]: ...
    async def supersede(self, tenant_id: str, edge_ids: list[UUID],
                        invalid_at: datetime, reason: str) -> None: ...
    async def reachable_decisions(self, tenant_id: str, entity_ids: list[str]) -> list[UUID]:
        """Impact-scope query via kg_evidence_t GIN index (NOT graph traversal)."""
    async def entity_lookup(self, tenant_id: str, query: str, top_k: int = 5) -> list[EntityCard]: ...
    async def stats(self, tenant_id: str, scope: str) -> dict: ...
    # 结果形状：EntityCard / EdgeCard / Subgraph / Path / HopPlan 冻结于
    # graph_store.py 模块 docstring（tenant 隔离是全库基线，不在 seam 重复）。

class PgJsonbGraphStore(GraphStore):
    """Default. 1-hop expansion per step; beam scoring in service layer.
    GIN(name_aliases), b-tree(src,dst,rel_type), b-tree(valid_at,invalid_at)."""

class KuzuGraphStore(GraphStore):
    """PoC branch, READ-ONLY (neighbors + multi_hop only). Switch via
    KW_GRAPH_STORE_BACKEND env."""
```

## 实现要点（PG 适配器）
1. `neighbors` 单跳递归 CTE，`hop` 参数由服务层循环调用展开（不一次递归到底，留给束搜索打分剪枝）。
2. `valid_view=True` 时全部查询自动叠加现行视图过滤（`valid_at/invalid_at` 谓词）。
3. `entity_lookup`：ES 冗余索引（name+summary）优先，回退 PG GIN 模糊。ES 写入与 PG upsert 同事务边界外做最终一致（对齐是后台批，可接受）。
4. embedding 列：上游 PG 无预装 pgvector，落地为 JSONB 数组 + 服务层 cosine（2 万实体 <50ms）。

## PoC 三必测点
P1 多跳 p95<1.5s @2万实体/3万边 · P2 批量 supersede p95<200ms · P3 ES 协同端到端 p95<3s。合成数据来自 `pipeline/gen_synthetic_graph.py`。

## 验收锚点
- `pytest test/backend/services/knowevo/test_graph_store.py -v`：现行视图过滤、supersede 后历史视图可查、reachable_decisions 命中率（fixture 决策卡反向引用）。
