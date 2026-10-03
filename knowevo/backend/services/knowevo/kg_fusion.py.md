# kg_fusion.py —— L6-M2 kg_search 三路融合工厂（rrf_fusion 的第一个生产消费者）
**新建**: 2026-09-30（L6-M2 生产接线）· 依据: L6 ES+RRF 三路融合设计（详见正文职责节）

## 职责

组织 kg_search 三路（**统一在图实体 id 空间 `stable_id`**——融合 id 空间冻结裁决，`rrf_fusion.py.md` §融合 id 空间），调冻结内核 `rrf_fusion.fuse(lists, k=60)`，返回融合种子序 + FusedHit 审计：

- **bm25** = `EsRawListClient.entity_bm25_hits`：平台 SDK 是**同步**调用，经 `asyncio.to_thread` 下放工作线程，不阻塞事件循环（handler 在 asyncio loop 里）；
- **dense** = `EsRawListClient.dense_entity_hits`：当前诚实空表，冻结理由常量随 `FusionOutcome.dense_reason` 入审计；
- **graph** = `graph_route_cards`：种子 → 逐跳 BFS → `(hop, -degree, stable_id)` 确定性序。

`retrieve_three_way` 内部是同步拉取（`_pull(fn(query))`），生产 handler 不能直接用（会阻塞 loop）；本工厂用「先 await 三路列表再 `fuse()`」的组合等价实现，`retrieve_three_way` 保持不动（M1 冻结零改动）。

## 接口冻结

```python
@dataclass(frozen=True)
class FusionOutcome:
    ids: tuple[str, ...]               # 完整融合序（stable_id 空间）；调用方自行切片种子页
    hits: tuple[FusedHit, ...]         # 内核审计：各路名次 / best_rank / first_seen
    bm25_count: int
    dense_count: int
    graph_count: int
    dense_reason: str = DENSE_ENTITY_DISABLED_REASON
    graph_route: GraphRoute | None = None

async def fused_entity_cards(client, store, tenant_id, query, *,
                             seed_top_k=DEFAULT_SEED_TOP_K,
                             hop=DEFAULT_HOP,
                             k=DEFAULT_RRF_K,
                             dense_reason=DENSE_ENTITY_DISABLED_REASON) -> FusionOutcome: ...
```

`client` = `EsRawListClient`（或 duck-typed fake）；`store` = GraphStore seam 对象；`EntityCard`/`FusedHit` 复用 `graph_store`/`rrf_fusion` 的形状，不另声明。

## 语义（冻结）

- **三路拉取顺序**：bm25（线程池）→ dense（同步，恒 `[]`）→ graph（await）；任一路返回 `None` 视为空表；
- **融合**：`fuse([bm25, dense, graph_cards], k=k)`——单路非空严格保持该路序；跨路重复 id 求和；平局 `(-score, best_rank, id)`（内核冻结语义，本模块不重排）；
- **审计**：`hits` 逐位保留内核 `FusedHit`（`ranks` 三槽 = bm25/dense/graph；`first_seen` = 最早出现的原始 hit 对象）；`*_count` 记录各路贡献量；`dense_reason` 是诚实空表理由，不是分数；
- **失败语义**：路内异常**向调用方传播**（内核同款纪律；静默回退属调用方 = kg_search handler 的逐位回退）；`client is None` → `ValueError`（「ES 是否配置」是调用方决策，None 静默融空属于掩盖）；
- `seed_top_k`/`hop`/`query` 的校验委托 `graph_route_cards`（同款 TypeError/ValueError 分型）；`k` 校验委托 `fuse`。

## 诚实边界

- **未来 dense 实现的并发语义（预告）**：当前 `dense_entity_hits` 恒 `[]`（同步、零 I/O），在事件循环内直接调用无害；当 dense 未来真实现时**必须**像 bm25 路一样经 `asyncio.to_thread` 下放线程池——本契约的冻结签名不覆盖未来实现的并发语义。

- 本模块不保证检索质量：三路列表质量由各路（ES 索引 / 图数据）负责；dense 槽空使 kg_search 融合**实际是 bm25+graph 两路**，`dense_count=0` 与 `dense_reason` 让这件事在审计面可见，不写成「三路已满员」。
- BM25 standard analyzer 无 IK 中文分词的召回缺口原样继承（对外不得写「中文分词已解决」）。
- 权威先验（`e1_retrieval.DEFAULT_AUTHORITY_WEIGHTS`）当前**未应用**于任何一路（实体卡无统一 authority 字段，侦察风险 R8）；若未来应用，只允许路内重排（进 fuse 前），RRF 层禁止再乘。
- `FusedHit.first_seen` 与 `ranks` 是唯一审计面；本模块不向 MCP 输出卡添加 score/why 字段（EntityCard 冻结形状不动）。

## 验收锚点

- `pytest test/backend/services/knowevo/test_kg_fusion.py -v`：10 用例离线全绿（手算 RRF 三路序与 ranks/first_seen 审计 / dense 槽理由与零伪造 / graph 元数据随行 / bm25 调用参数透传 / 全空融合 / client None ValueError / 路内异常传播 / None 列表视空 / 双跑确定 / k 转发校验）；
- 全量回归：`pytest ../test/backend/services/knowevo/ -q` 基线不降 + `ruff check backend/services/knowevo mcp_servers` 0 新违例；
