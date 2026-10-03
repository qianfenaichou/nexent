# es_index_writer.py —— 摄取落库后的实体 ES upsert 生产写路径
**新建**: 2026-09-30 · 依据: ES 实体检索集成先例（只读参考）+ `es_raw_list.py.md`（query 侧孪生件）

## 职责

PG 是 graph of record；本模块是**投影**：摄取 run 把实体落进 `kg_entity_t` 之后（`kg_service.merge_delta -> store.insert_entity`，`pipeline/ingest_graph.py::_run` 完成点挂钩），把该租户 active 实体 **bulk reconcile** 进生产实体索引 `knowevo_entities`（M2 验收索引是 `knowevo_entities_m2`，探针自建自灌，二者不同名不混用）。让 `es_raw_list` / ES-first 查询面打真数据而不是探针索引。

## 接口冻结

```python
ENTITY_INDEX = "knowevo_entities"
ASSET_INDEX = "knowevo_assets"
DENSE_WRITEBACK_REASON = "embedding_model_not_resolvable_at_ingestion_time"
ENV_ES_HOST = "ELASTICSEARCH_HOST"
ENV_ES_API_KEY = "ELASTICSEARCH_API_KEY"
ENV_SYNC_ON_INGEST = "KW_ES_SYNC_ON_INGEST"

class EsIndexWriter:
    def __init__(self, core, entity_index=ENTITY_INDEX, asset_index=ASSET_INDEX, ik_enabled=None): ...
    def probe_ik(self) -> bool: ...
    def ensure_indices(self) -> list[str]: ...
    def upsert_entities(self, tenant_id, cards, refresh="wait_for") -> dict: ...

def entity_mapping(ik_enabled: bool = False) -> dict: ...
def asset_mapping(ik_enabled: bool = False) -> dict: ...
def collect_active_entities(tenant_id, session_factory=None) -> list[dict]: ...
def sync_tenant_entities(tenant_id, writer=None, session_factory=None) -> dict | None: ...
def sync_enabled() -> bool: ...
def build_es_index_writer(core=None) -> EsIndexWriter | None: ...
```

`core` 是 duck-typed（平台 `ElasticSearchCore` 或 fake）：只消费 `client.indices.exists/create`、`client.bulk`、`client.cat.plugins`。环境变量在本模块读取（**不改 `backend/consts/const.py`**，该文件是共享接线文件），名字用平台既有 ES env 名。

## 语义（冻结）

**显式 strict mapping**（`entity_mapping` / `asset_mapping`，M2 先例同形状）：`dynamic: "strict"`；BM25 面 = 实体 `name`/`aliases.alias`、资产 `title`；其余 keyword/integer/float/boolean；实体 `props` 为 `enabled: false` 只透传（动态映射猜子键炸 shard）；资产 `metadata` 显式子字段 + `dynamic: true` 透传（2026-09-29 实测自由文本解析炸 shard）。**mapping 不声明 `embedding` 字段**（见诚实边界）。

**IK 探测（`probe_ik`，三态入审计）**：一次 `GET _cat/plugins`（`cat.plugins(format="json")`）查 `analysis-ik`——`present`（用 `ik_max_word`/`ik_smart`）、`absent`（standard）、`error`（任何探测失败降级 standard，绝不阻塞）；结果缓存在 `audit["ik_probe"]`/`audit["ik_enabled"]`；构造注入 `ik_enabled` 时短路探测（audit 记 `injected`）。

**`ensure_indices` 幂等**：只对**不存在**的索引执行 `create`（settings + mappings 显式）；已存在的索引**零改动**（对比 M2 探针：这里没有 DELETE）；返回本次新建的索引名清单。

**`upsert_entities` bulk 幂等**：ES `_id = f"{tenant_id}::{stable_id}"`——`stable_id` 是内容派生（`{class}:{name-key}`），跨租户同实体同 stable_id、共享一个索引，租户前缀保证重跑覆盖原位而非跨租户互相覆盖；`tenant_id` 从**方法参数**强制注入文档（永不取卡片值）；卡片缺 `stable_id` 或 `name` 即丢弃（索引不发明实体）；aliases 接受 `[{alias, type}]` 与 `[str]` 两种形状归一；返回 `{"sent": n, "bulk_errors": k, "took_ms": ...}`（k/`took_ms` 取自 ES bulk 响应，绝不估算）；`refresh="wait_for"`（与平台 core bulk 语义一致，search-after-write 一致）。

**`collect_active_entities` reconcile 语义**：整租户 active 集合（非 run 增量）——按 (tenant, stable_id) 幂等、自愈历史残缺 run；只导出 mapping 声明的字段，`embedding`/PG 主键 `id` **不导出**。

**开关（`sync_enabled` / `build_es_index_writer` / `sync_tenant_entities`）**：`KW_ES_SYNC_ON_INGEST` 显式真值（`1/true/yes/on`）**且** `ELASTICSEARCH_HOST` 存在才装配；否则返回 `None`，**所有调用方逐位保持现状行为**。**只设 host 不算开**——测试 harness 全局注入 `ELASTICSEARCH_HOST`（`test/conftest.py`），host 存在性绝不能武装写路径。SDK core 懒构造（函数内 import），模块 import 不拉 ES 客户端栈。

## 摄取侧挂钩（`pipeline/ingest_graph.py::_run` 完成点）

`_es_upsert_best_effort(tenant, run_id)` 在 span 循环结束后、`wall_seconds` 计时**之外**调用 `sync_tenant_entities`。失败隔离是契约：任何异常（未装配 / ES 不可达 / bulk 拒绝 / import 失败）只 debug log，**绝不改变摄取结果与退出码**（与 graph_store ES-first 兜底同哲学）；`--dry-run` 不触发。

## 诚实边界

- **IK 未装**：2026-09-30 实测 ES 容器 `_cat/plugins` 为空（无 analysis-ik），本轮写路径落 **standard analyzer**；装插件属容器变更，不在本模块范围。探测结果入审计，探测失败按 standard。
- **dense 写回不实现**：摄取路径上 embedding 模型不可解析（无 KB 配置/模型网关可达 ingest CLI），不发伪向量；strict mapping 不声明 `embedding` 字段，knn 路由无从假装存在；理由 = 冻结常量 `DENSE_WRITEBACK_REASON`。
- **资产索引只建不写**：`knowevo_assets` 的常量与 strict mapping 存在且由 `ensure_indices` 创建，但本轮**没有** `doc_asset_t` 投影写路径（实体 reconcile 是唯一实现的写路径）。
- 本模块不是「生产实体检索已全部接通 ES」的声明：query 侧注入（`PgJsonbGraphStore(es_client=...)`）仍是独立接线项。

## 验收锚点

- `pytest test/backend/services/knowevo/test_es_index_writer.py -v`：36 用例离线全绿（mapping 形状 / IK 三态 + 注入短路 + 缓存 / ensure_indices 幂等与既有索引零改动 / bulk 幂等重发同 payload + 租户强制 + 坏卡丢弃 + 跨租户 `_id` 不碰撞 + bulk 错误计数 / PG 导出字段恰为 mapping 面 / 开关三态 / 摄取挂钩失败隔离 + dry-run 不触发）；
- 挂钩回归：`test_ingest_graph_empty_span_guard.py` 全绿（默认关 → 零副作用）；
- 全量回归：`pytest ../test/backend/services/knowevo/ -q` 基线不降 + `ruff check backend/services/knowevo mcp_servers` 0 新违例。
