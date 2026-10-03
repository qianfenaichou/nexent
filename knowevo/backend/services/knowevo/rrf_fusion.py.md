# rrf_fusion.py —— L6 三路 RRF 秩融合内核
**新建**: 2026-09-29（L6 ES 三路 RRF）· 依据: L6 三路 RRF 融合设计（详见正文职责节）

## 职责

BM25 + dense + 图召回三路有序列表的 **RRF（Reciprocal Rank Fusion, Cormack et al. SIGIR 2009）** 秩融合内核。纯函数、stdlib-only、零 DB、零 LLM、零 ES。只吃名次不吃分数——这是选 RRF 替代平台加权归一分（`elasticsearch_core.hybrid_search` 的 `w*norm_acc+(1-w)*norm_sem`）的根本理由：分数跨路不可比、max-归一对离群分敏感、w 是拍脑袋常数、且平台无第三路图召回。

**生产接线不在本模块**（属后续接线；与 `update_planner.py` 同款诚实分层）：内核与 `ingest_service`/`vectordatabase_app` 无任何 import 关系，接线前默认检索行为零变化。`retrieve_three_way` 是注入式 seam（三路 callable 由调用方提供），不是生产挂点。

## 接口冻结

> **裁决注记（2026-09-30）**：id 抽取中 **`id is None` 视为未设置**，回落 `stable_id`（Mapping 与对象路径一致）；**非 str 且非 None** 的 id → `TypeError`，不静默回落。空串仍 `ValueError`。


```python
DEFAULT_RRF_K = 60   # Cormack 原文常用值

@dataclass(frozen=True)
class FusedHit:
    id: str
    score: float                      # RRF 和的 float 视图（排序用精确 Fraction，见下）
    ranks: tuple[int | None, ...]     # 各路 1-based 名次；该路缺位为 None；长度=输入路数
    best_rank: int                    # min(出现过的名次)
    first_seen: Any                   # 最早出现的原始 hit 对象（路序 0,1,2… 优先）

def fuse(three_lists: Sequence[Sequence[Any]], k: Any = DEFAULT_RRF_K) -> list[FusedHit]: ...

def retrieve_three_way(
    query: str,
    *,
    bm25: Callable[[str], Sequence[Any]],
    dense: Callable[[str], Sequence[Any]],
    graph: Callable[[str], Sequence[Any]],
    k: Any = DEFAULT_RRF_K,
) -> list[FusedHit]: ...
```

`three_lists` 为有序路列表序列（生产用法 = bm25/dense/graph 三路；路数不限，缺路传空表即可）。每项 hit 须可抽取字符串 id。

## 语义（冻结）

**公式**：`score(d) = Σ_lists 1/(k + rank_L(d))`，rank 为 **1-based**；路内首次出现定名次。计分用 `fractions.Fraction` **精确累加**后 cast float——数学平局必然真平局，不被浮点求和顺序拆开。

**平局排序（全序）**：`(-score, best_rank, id)`——分高者先 → 最好名次靠前者先 → id 字典序升。与输入路内顺序无关（路序置换不改融合顺序与分数，只改 `ranks` 槽位）。

**退化语义**：
- 外层空 / 全空路 → `[]`；
- 单路非空 → 严格保持该路顺序（score = 1/(k+rank)）；
- 两路其中一路空 → 与单路同构，另一路照常；
- 跨路重复 id → 各路贡献**求和**（RRF 本意）；路内重复 id → **只保留首次（最好）名次**，不重复加分。

**id 抽取**：裸 `str` → 自身；`Mapping` → `id` 优先、否则 `stable_id`；对象 → `.id` 优先、否则 `.stable_id`。缺 id / 非 str id → `TypeError`；空串 id → `ValueError`（校验先行，不静默跳过）。`first_seen` 保留最早出现的原始 hit 对象（审计面）。

**k 校验**：非 int/float、bool → `TypeError`；NaN/Inf、负数 → `ValueError`；`k=0` 合法（score=1/rank）。

**`retrieve_three_way`**：三路 callable 注入，逐个以 `query` 调用后 `fuse`。某路返回 `None` 视为空表；callable 内异常**向调用方抛出**（静默回退属调用方，如 ES-first+ilike，不进内核）。

## authority_weights 与 RRF 并存（冻结分层）

权威先验（`e1_retrieval.DEFAULT_AUTHORITY_WEIGHTS`，乘性 `ranked_score = bm25 * w(authority_level)`）**只作用在各路列表构造内**（进 fuse 前）；**RRF 层禁止再乘 authority**——乘回融合分会破坏秩融合的尺度无关性。E1 离线评测路径（authority prior + per_doc_quota）不被本模块修改；两者共享同一张权重表但互不 import 对方实现。调用方在 `why` 里标注「权威是否已在路内应用」（束=可审计）。

## 图路 seam（L6 增量 #1）

图路 = 返回有序 hit 列表的 callable，hit 形状与 bm25/dense 一致。实现须确定性（递归 CTE / L5 摘要邻域），**禁止** PPR/随机游走（L1 裁决沿用）。路内排序（边权/跳数/权威）在进 fuse 之前完成；本内核不负责路内排序。

## 诚实边界

- 本模块不拉任何检索、不估任何分数；三路列表质量完全由调用方保证。
- 与平台加权归一分的对照只在合成探针里做机制验证（`competition/experiments/probe_l6_rrf.py`），不构成真实库检索质量结论。
- 生产调用点接线属后续工作；本契约不冻结任何调用点。

## 验收锚点

- `pytest test/backend/services/knowevo/test_rrf_fusion.py -v`：33 用例离线全绿（手算 RRF 例 / 空表·单路·两路退化 / 平局 best_rank+id 破平 / 路内与跨路重复 id / id 抽取四形 / k 校验（TypeError/ValueError 分型）/ 路序置换 / `retrieve_three_way` 注入与 None 兜底）；
- 冻结探针：`python3 competition/experiments/probe_l6_rrf.py` → `competition/deliverables/probe_l6_rrf.json`（`content_sha256` 自校验配方见该 JSON；无时间戳字段，双跑逐字节一致）；
- 全量回归：pytest 基线不降 + ruff 0 新违例。

## 融合 id 空间（2026-09-29 冻结）

`fuse()` **不做**跨空间映射。同一调用内所有路 id **必须**已映射到同一空间：
- **kg_search 语境** = 图实体空间 `stable_id`
- **asset_search 语境** = 资产空间 `AssetHit.id`（图路二期）
- **禁止** `document.id` 与 `stable_id` 同 fuse（静默拼接是错误）

完整裁决即上述四条：同调用同空间、kg 用 `stable_id`、asset 用 `AssetHit.id`、`document.id` 禁与 `stable_id` 同 fuse。
