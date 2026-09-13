# graph_store.py —— A1 图存储抽象与 PG JSONB 适配器
**归属任务**: T-07（默认实现）/ PoC 分支（Kuzu 只读适配器）· 依赖: T-03 建表
**依据**: [备忘录 09-A1](../../../04-算法与架构决策备忘录/09-A1-图存储选型.md)

## 职责
隔离图存储 seam：束搜索/邻域/受影响面等查询的统一接口；PG JSONB 为默认适配器。**调用方（kg_service/decision_service）只 import 本模块，绝不直写 CTE**——这是 seam 纪律（两适配器=真 seam）。

## 接口冻结

```python
class GraphStore(ABC):
    async def upsert_entities(self, ents: list[Entity]) -> None: ...
    async def upsert_relations(self, rels: list[Relation]) -> None: ...
    async def neighbors(self, entity_ids: list[str], rel_types: list[str] | None = None,
                        hop: int = 1, valid_view: bool = True) -> Subgraph: ...
    async def multi_hop(self, seeds: list[str], hop_plan: HopPlan,
                        beam: int = 3, depth: int = 3) -> list[Path]: ...
    async def supersede(self, edge_ids: list[UUID], invalid_at: datetime, reason: str) -> None: ...
    async def reachable_decisions(self, entity_ids: list[str]) -> list[UUID]:
        """Impact-scope query via kg_evidence_t GIN index (NOT graph traversal)."""
    async def entity_lookup(self, query: str, top_k: int = 5) -> list[Entity]: ...
    async def stats(self, scope: str) -> dict: ...

class PgJsonbGraphStore(GraphStore):
    """Default. 1-hop CTE per expansion step; beam scoring in service layer.
    GIN(name_aliases), b-tree(src,dst,rel_type), b-tree(valid_at,invalid_at)."""

class KuzuGraphStore(GraphStore):
    """PoC branch, READ-ONLY (neighbors + multi_hop only). Switch via
    KW_GRAPH_STORE_BACKEND env (T-03 wiring)."""
```

## 实现要点（PG 适配器）
1. `neighbors` 单跳递归 CTE（骨架见备忘录 09 §4），`hop` 参数由服务层循环调用展开（不一次递归到底，留给束搜索打分剪枝）。
2. `valid_view=True` 时全部查询自动叠加现行视图过滤（`valid_at/invalid_at` 谓词）。
3. `entity_lookup`：ES 冗余索引（name+summary）优先，回退 PG GIN 模糊。ES 写入与 PG upsert 同事务边界外做最终一致（对齐是后台批，可接受）。
4. embedding 列：T-03 探明 pgvector 可用性；不可用则 JSONB 数组 + 服务层 cosine（2 万实体 <50ms，备忘录 10 §2）。

## PoC 三必测点（T-07 第一周，备忘录 09 §3）
P1 多跳 p95<1.5s @2万实体/3万边 · P2 批量 supersede p95<200ms · P3 ES 协同端到端 p95<3s。合成数据来自 `pipeline/gen_synthetic_graph.py`。

## 验收锚点
- `pytest test/backend/services/knowevo/test_graph_store.py -v`：现行视图过滤、supersede 后历史视图可查、reachable_decisions 命中率（fixture 决策卡反向引用）。
- PoC 数据落 `competition/docs/` + cost-ledger。
