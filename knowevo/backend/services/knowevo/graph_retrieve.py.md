# graph_retrieve.py —— L6-M2 图路组合函数：种子 → 逐跳 BFS → 确定性序
**新建**: 2026-09-30（L6-M2 生产接线）· 依据: L6 ES+RRF 检索设计（详见正文职责节）

## 职责

kg_search 三路融合的**图路**：query → `entity_lookup` 取种子 → 沿冻结 `neighbors` seam 逐跳 BFS → **确定性排序的去重 EntityCard 列表**。冻结 seam 只给了构件（`Subgraph.entities` 无相关性排序、PG 行序非契约——侦察风险 R5），本模块是缺失的组合层。

确定性合同（L1 裁决沿用：可解释 > 优雅）：

- 排序键 = **`(hop_distance, -degree, stable_id)`**，经 `ordering_key` 对**任意卡集**构成全序；
- 禁止 PPR / 随机游走 / 不可复现打分；
- `hop_distance` 用「每层一次 `neighbors(hop=1)`」的 BFS 得到精确值（seeds=0），不改 `neighbors` 冻结签名、不写递归 CTE（k≤3 下递归 CTE 是可选优化非前置，侦察 §2.2）；
- `degree` = BFS 看到的每条边对其两端各计 1；对返回的每张卡取值（孤立种子 = 0，不是缺键）。

## 接口冻结

```python
DEFAULT_SEED_TOP_K = 5
DEFAULT_HOP = 1

@dataclass(frozen=True)
class GraphRoute:
    cards: tuple[EntityCard, ...]      # 按 (hop, -degree, stable_id) 排序
    hop_by_id: Mapping[str, int]       # BFS 元数据（审计 + 邻域尾卡排序复用）
    degree_by_id: Mapping[str, int]

def ordering_key(stable_id: str, hop_by_id: Mapping[str, int],
                 degree_by_id: Mapping[str, int]) -> tuple[float, int, str]: ...

async def graph_route_cards(store, tenant_id, query, *,
                            seed_top_k=DEFAULT_SEED_TOP_K,
                            hop=DEFAULT_HOP) -> GraphRoute: ...
```

`store` 为 GraphStore seam 对象（`entity_lookup` + `neighbors`），真实适配器与离线 fake 均可；`EntityCard` 复用 `graph_store` 的 dataclass，不另声明第二形状。

## 语义（冻结）

**`ordering_key`（全序保证）**：元数据认识的 id → `(hop, -degree, stable_id)`；不认识的 id → `(inf, 0, stable_id)`——排在所有已知 id 之后、未知 id 之间按字典序。handler 把融合种子卡与邻域尾卡混排时依赖这条全序，不允许出现第二把排序钥匙。

**`graph_route_cards`**：
- 空白 query / 种子空 → 空 `GraphRoute`，**零 `neighbors` 调用**；
- BFS 每层 `neighbors(tenant, frontier, hop=1)`；层内新见 id 记 `hop=depth` 并入下一前沿；边按 `edge.id` 去重后计度；
- 同一 stable_id 经多条路径/既是种子又是邻居 → 只保留**最小 hop** 的一张卡（`cards_by_id` setdefault 语义）；
- 校验先行：`seed_top_k`/`hop` 非 int（或 bool）→ `TypeError`；`<1` → `ValueError`；`hop > 3`（对齐 kg_multi_hop depth 上限）→ `ValueError`；`query` 非 str → `TypeError`；
- store seam 内的异常**向调用方抛出**（静默回退属调用方 = kg_search handler）。

**复用纪律**：`ordering_key` 是唯一的图路排序定义；handler 的「种子卡按融合名次在前、其余邻域卡按 `(hop, -degree, stable_id)` 排后」直接消费 `GraphRoute.hop_by_id`/`degree_by_id` + `ordering_key`，不得另写排序。

## 诚实边界

- 本模块不做相关性打分，只做结构序；融合语义（跨路共识）归 `rrf_fusion`。
- PG ilike 兜底行序非契约（`graph_store.py` `_entity_lookup_ilike` 无 ORDER BY）——本模块的排序**不依赖** lookup 内序，只消费其成员与 ES-first 序。
- 生产写路径/索引不在本契约范围；本契约不冻结任何调用点（调用点 = `kg_fusion.fused_entity_cards`，见其契约）。

## 验收锚点

- `pytest test/backend/services/knowevo/test_graph_retrieve.py -v`：13 用例离线全绿（双层 hop 标注 / 度数手算 / `(hop,-degree,id)` 全序 / lookup 序不破平 / 空种子零 neighbors / 空白 query / 跨路径去重最小 hop / seed_top_k·hop 透传 / 校验异常 / ordering_key 全序与缺键行为）；
- 全量回归：`pytest ../test/backend/services/knowevo/ -q` 基线不降 + `ruff check backend/services/knowevo mcp_servers` 0 新违例；
