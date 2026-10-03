# community_summary.py —— L5 社区摘要内核：聚类 + 确定性骨架 + global 路由
**新建**: 2026-09-29（L5 社区摘要层）· 依据: L5 社区摘要层设计（详见正文职责节）

## 职责

把 KnowEvo 图视图聚成**社区**，为每社区产出**确定性骨架摘要**（无 LLM），并暴露一条 **global 检索 seam**：把聚合类问题排成社区命中，再展开成**有序 entity id 列表**（L6 图路形状）。纯函数、stdlib-only、零 DB、零 LLM、零 ES。与 `rrf_fusion` / `update_planner` / `budget_curve` 采用同一分层方式。

**生产接线由调用方完成**：内核与 `schemas` / `kg_service` / `ingest_service` 无任何 import 关系，本模块不改变默认检索/路由行为。`cluster_fn` 是注入式 seam（聚类 callable 由调用方提供），不是生产挂点。

**反向证据约束（契约级）**：Zeng et al. 2025（**arXiv:2506.06331**）指出 GraphRAG 评测存在 unrelated questions + evaluation biases 两缺陷，无偏复测后增益远比先前报告温和。故本模块**不宣称**摘要涨点；任何增益主张必须走设计 §5 的 E2 式消融，无增益如实报 null。

## 接口冻结

> **设计注记（2026-09-30）**：`cluster_greedy_modularity` 的优化目标是**简单图**（无向边 `(src,dst)` 去重）；`modularity()` 的报告值按**多重图**计边（对外报告 Q=0.6237 即此口径）。同 pair 多关系图上二者不等；后续版本拟统一为简单图。`bridge_claims` = canonical edge key 去重后字典序前 `max_claims` 条的 claim 文本——**不同 edge key 可共享同一 claim 文本**（允许重复）；fingerprint 含 `bridge_claims` 列表，改文本去重会改指纹。


```python
DEFAULT_DEPTH = 3          # 对齐 graph_store.multi_hop / KW_MULTIHOP_*
DEFAULT_BEAM = 3
DEFAULT_TOP_ENTITIES = 5
DEFAULT_MAX_CLAIMS = 3
DEFAULT_MAX_ITER = 16
MODULARITY_EPS = 1e-12
GLOBAL_ROUTE = "G"         # 建议的 schemas.ROUTE_GLOBAL 加法常量；schemas.py 本轮不改

LLM_SUMMARY_PROTOCOL: dict   # 冻结 prompt 协议 + 验收门（见设计 §3.2）

@dataclass(frozen=True)
class Community:
    community_id: str          # 必须 = min(member_ids)
    member_ids: tuple[str, ...]  # 非空、升序
    level: int = 0             # 层级预留；本内核只做 level 0

@dataclass(frozen=True)
class SkeletonSummary:
    community_id: str
    size: int
    top_entities: tuple[tuple[str, str, int], ...]   # (id, name, degree)
    rel_type_counts: tuple[tuple[str, int], ...]     # (rel_type, count)
    bridge_claims: tuple[str, ...]
    fingerprint: str                                 # sha256 canonical JSON
    @property
    def searchable_text(self) -> str: ...

@dataclass(frozen=True)
class GlobalHit:
    community_id: str
    score: int
    matched_terms: tuple[str, ...]
    rank: int                    # 1-based

@dataclass(frozen=True)
class GlobalRouteResult:
    route: str                   # GLOBAL_ROUTE
    hits: tuple[GlobalHit, ...]
    entity_ids: tuple[str, ...]  # 有序去重 entity id（L6 图路形状）
    depth: int
    beam: int

def tokenize(text: str) -> tuple[str, ...]: ...
def cluster_connected_components(node_ids, edges) -> list[Community]: ...
def cluster_lpa(node_ids, edges, *, max_iter=DEFAULT_MAX_ITER) -> list[Community]: ...
def cluster_greedy_modularity(node_ids, edges) -> list[Community]: ...
def modularity(communities, *, node_ids, edges) -> float: ...
def skeleton_summary(community, *, names, edges, top_k=DEFAULT_TOP_ENTITIES, max_claims=DEFAULT_MAX_CLAIMS) -> SkeletonSummary: ...
def render_skeleton_text(skel) -> str: ...
def build_llm_prompt(skel, *, question=None, language="zh") -> str: ...
def score_communities(query, summaries, *, top_n=3) -> list[GlobalHit]: ...
def entities_from_hits(hits, communities, *, edges=(), depth=DEFAULT_DEPTH, beam=DEFAULT_BEAM) -> tuple[str, ...]: ...
def global_route(query, *, communities, names, edges, top_n=3, depth=DEFAULT_DEPTH, beam=DEFAULT_BEAM, cluster_fn=None) -> GlobalRouteResult: ...
```

## 语义（冻结）

### 聚类选型

默认 **`cluster_greedy_modularity`**（CNM 式贪心模块度最大化，纯 stdlib）。**不用 Leiden**：`igraph`/`leidenalg` 非声明依赖（`pyproject.toml` 是冻结接线文件），networkx `leiden_communities` 在 3.6.1 是 backend-dispatch stub（无独立 backend 即 `NotImplementedError`）。`cluster_fn` seam 允许后续换 Leiden，不改内核。

备选 `cluster_lpa`（确定性标签传播，破平 = 标签字典序）有**已知塌缩**：两团 + 一条桥边会塌成一团；仅适合已分离分量。`cluster_connected_components` 为退化基线。

### 确定性合同

- 每个公开函数是参数的纯函数，零 RNG；
- `community_id = min(member_ids)`；社区按 `community_id` 升序返回；
- 合并平局破平 = 社区 id 对 `(min_id, max_id)` 字典序升；`dQ <= MODULARITY_EPS` 即停；
- 骨架 `fingerprint = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))`；
- global 排序破平 = `(-score, -size, community_id)`；
- 同输入（含边序置换）→ 同输出逐位一致（测试 + 探针双跑钉死）。

### 模块度

`Q = Σ_c [ E_c/m − (K_c/(2m))^2 ]`，`E_c` = 社区内无向边数（**每条边计一次**），空图 → 0.0。
⚠️ 历史 bug：`_edges_between` 对集合内边只计一次，旧代码又 `// 2`，两个不相交三角形算出 `Q=−0.166…`（正确 0.5）。已修；聚类分区行为未变。

### 骨架摘要

只吃**社区内**边（degree / rel_type_counts / bridge_claims）。`names` 缺名回退 id 不抛。`bridge_claims` = canonical edge key 去重后字典序前 `max_claims` 条。

### LLM 摘要协议（stub，不调模型）

`build_llm_prompt` 只产 prompt 字符串；`LLM_SUMMARY_PROTOCOL` 冻结 `must` / `must_not` / `acceptance_gates` / `on_gate_fail`。门失败 → **丢弃 LLM 输出，保留骨架**（`summary_kind='skeleton'`）。`language` 只接受 `"zh"` / `"en"`。

### global 路由与展开

- `score = |distinct(query tokens) ∩ searchable_text tokens|`；tokenize = 小写 ASCII 词 + CJK 单字/双字二元；
- `entities_from_hits`：hit rank → 社区内成员 id 升序播种 → 沿边确定性一步一跳扩展；`depth ≤ DEFAULT_DEPTH(3)`，`beam ≤ DEFAULT_DEPTH` 校验的是 depth；beam 只计**新采纳**节点（已进有序集的邻居 continue 不占 beam）；零环；
- `global_route`：`communities` 为空且给了 `cluster_fn` 时用 `sorted(names.keys())` 作节点集现聚；两者都缺 → `ValueError`。

### 校验（TypeError / ValueError，校验先行）

`Community`：空 `member_ids` / 未排序 / `community_id != min(member_ids)` → `ValueError`。
`top_k` / `max_claims` / `top_n` / `depth` / `beam` / `max_iter`：非 int 或 <1 → `TypeError` / `ValueError`；`depth > 3` → `ValueError`。
`query`/`text` 非 str → `TypeError`；`names` 非 Mapping → `TypeError`；`language` 非 zh/en → `ValueError`。

## 与 L6 的共用点

`entities_from_hits` / `global_route.entity_ids` 直接给出**有序去重 entity id**，即 L6 图路 callable 的产出（`rrf_fusion.py.md` §图路 seam：确定性查询 / L5 摘要邻域，禁止 PPR）。路内排序在进 `fuse` 之前完成；权威先验若用，只作用在路内（L6 §4 分层并存）。

## `ROUTE_GLOBAL` 加法建议（schemas.py 本轮不改）

```python
ROUTE_GLOBAL = "G"   # additive only; R/M/RM 与默认 Route.route=ROUTE_BOTH 零变化
```

内核常量 `GLOBAL_ROUTE = "G"` 与之同值；接线时禁止两套字面量。

## `kg_summary_t` 草案

见 `kg_summary_t` 落地迁移 `deploy/sql/migrations/v2.5.5_kw_012_kg_summary.sql`。**本契约不冻结任何 SQL**；落库由 `summary_store` 承担。

## 诚实边界

- 本模块不拉检索、不调模型、不写库；社区/摘要质量完全由调用方与消融实验保证。
- LLM 摘要质量 = `insufficient_data`（协议 stub 未跑模型）；真实聚合题增益 = `insufficient_data`，必须走 E2 式消融并遵守 Zeng null 路径。
- 生产调用点接线由调用方完成；本契约不冻结任何调用点。

## 验收锚点

- `pytest test/backend/services/knowevo/test_community_summary.py -v`：38 用例离线全绿（确定性双跑/边序置换 / 模块度手算 0.5·0.0·5/14 / LPA 塌缩限制 / 骨架指纹稳定与敏感 / global 破平 / entities beam 语义 / prompt 协议 / tokenize / 校验异常）；
- 冻结探针：`python3 competition/experiments/probe_l5_community.py` → `competition/deliverables/probe_l5_community.json`（`content_sha256` 自校验配方见该 JSON；无时间戳字段，双跑逐字节一致；`llm_summary_quality=insufficient_data`）；
- 全量回归：`pytest ../test/backend/services/knowevo/ -q` 基线不降 + `ruff check backend/services/knowevo` 0 违例。
