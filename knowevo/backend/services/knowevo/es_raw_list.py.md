# es_raw_list.py —— L6-M2 实体 ES 原始列表适配器（BM25 路 + store seam + dense 槽）
**归属任务**: L6-M2 生产接线（2026-09-30 新建）· 依据: `nexent/competition/docs/tech-optimization-2026-09-28/l6-m2-recon-2026-09-29.md` §1/§7 + `l6-es-rrf-design-2026-09-29.md`

> **2026-09-30 双轴审查 P1-1 更正注记（加法；冻结签名块不动）**：本文原写「只调 `accurate_search`」已过时——实现**自建 `multi_match` DSL**（`name` + `aliases.alias`，`operator=and`）经 **`core.client.search`** 执行，**不走** `accurate_search`。原因（实现 docstring + 2026-09-30 真 ES 验收）：`accurate_search` 的 `build_weighted_query` 按 KB 字段 `title`/`content` 建权，实体索引（strict mapping：`name`/`aliases.alias`/`class_ref`）上**静默 0 命中**（bm25_count=0）。duck-type 面自此 = `core.client.search`（+未来 dense 的 `semantic_search`）；dense 路**诚实空表**不变。冻结签名块、塑形语义、租户过滤、dense 空表理由常量均零变化。

## 职责

把实体索引的 raw ES 命中（自建 `multi_match` DSL + `core.client.search`，形状 `{score, document, index}`，id 埋在 `document` 里）塑形进**图实体 id 空间（`stable_id`）**——这是 `rrf_fusion.fuse` 唯一可消费的形状（raw hit 直接喂 `_extract_id` 会 TypeError，且 `document.id` 属知识库文档空间，与图空间禁止同 fuse）。一个适配器三个面：

1. `entity_bm25_hits` —— kg_search 融合的 BM25 原始名次列表；
2. `entity_search` —— `PgJsonbGraphStore` 的 `es_client` seam（`async entity_search(tenant_id, query, top_k)`，store 的 `_maybe_await` 对协程直接 await）：生产 `_store()` 注入后 `entity_lookup` 的 ES-first 分支真正生效。**2026-09-30 Standards 审查后由 sync 改 async**（「先契约后改码」）：ES 查询经 `asyncio.to_thread` 下放线程池，避免同步 ES 调用（超时 20s×3 重试）阻塞 handler 事件循环——与融合路径 bm25 路的线程化同理由；`entity_bm25_hits` 保持 sync（kg_fusion 在调用点 to_thread），两个公开面的并发语义自此不对称、各自注明；
3. `dense_entity_hits` —— dense 槽位，**本回合诚实返回 `[]`**（见诚实边界）。

环境变量在本模块读取（**不改 `backend/consts/const.py`**，该文件是共享接线文件）：`ELASTICSEARCH_HOST` / `ELASTICSEARCH_API_KEY`（平台既有名）+ `KW_ENTITY_ES_INDEX`（实体索引名，默认 `knowevo_entities_m2`——L6-M2 探针自建自灌的验收用最小索引，生产写路径归 T-08）。

## 接口冻结

```python
ENTITY_INDEX_DEFAULT = "knowevo_entities_m2"
DENSE_ENTITY_DISABLED_REASON = "entity_index_has_no_embedding_field"

ENV_ES_HOST = "ELASTICSEARCH_HOST"
ENV_ES_API_KEY = "ELASTICSEARCH_API_KEY"
ENV_ENTITY_INDEX = "KW_ENTITY_ES_INDEX"

class EsRawListClient:
    def __init__(self, core, entity_index=ENTITY_INDEX_DEFAULT, embedding_model=None): ...
    def entity_bm25_hits(self, tenant_id, query, top_k) -> list[dict]: ...
    async def entity_search(self, tenant_id, query, top_k) -> list[dict]: ...
    def dense_entity_hits(self, tenant_id, query, top_k) -> list[dict]: ...

def build_es_raw_client(core=None) -> EsRawListClient | None: ...
```

`core` 是 duck-typed：只调 **`core.client.search`**（自建 `multi_match` DSL；**不走** `accurate_search`——见头部 P1-1 更正注记）。未来 dense 才会用 `semantic_search`；离线测试注入 fake 即可。

## 语义（冻结）

**租户隔离**：每一路强制注入 `filter=[{"term": {"tenant_id": tenant_id}}]`，不接受调用方传入的过滤（侦察风险 R6）。

**BM25 路（`entity_bm25_hits`）**：
- 塑形为 `{"id": <stable_id>, "name": ..., "es_score": ..., "index": ...}`，**ES 相对顺序即列表顺序**；
- `document` 缺 `stable_id`（或非 str/空串）的 hit **丢弃**——融合空间里不允许出现图邻域无法解析的 id；
- `es_score` 仅随行审计，RRF 只吃名次；
- 空白 query 返回 `[]` 且**零 ES 调用**。

**store seam（`entity_search`）**：
- 形状对齐 `GraphStore._entity_to_card`：`stable_id` + `name` 必备（缺一丢弃，store 不发明实体），`class_ref` 缺省 `"Unknown"`，`props` 透传，aliases 归一成 `list[str]`（`[a["alias"] for dict a if alias]`）；
- ES 只贡献命中与相对顺序，分数不进卡（`_as_entity_card` 丢弃未知键）。

**dense 槽（`dense_entity_hits`）**：无条件返回 `[]`、零 ES 调用（理由见诚实边界）；multimodal 守卫仍保留——只有 `model_type == "embedding"` 的模型在语义上允许服务本槽（`semantic_search` 的 multimodal 分支返回两段 knn 拼接列表，不是单一有序列表，侦察风险 R4）。

**工厂（`build_es_raw_client`）**：
- `ELASTICSEARCH_HOST` 缺失/空 → 返回 `None`，**所有调用方逐位保持现状行为**（`_store()` 无参构造、handler 走现行路径）；
- SDK core 懒构造（函数内 import），模块 import 不拉 ES 客户端栈；注入 `core` 时跳过构造（测试/已有 core 的调用方）；
- 构造成功不等于连接成功——连接失败在调用时暴露，静默回退属调用方契约。

## 诚实边界

- **不走 `accurate_search`**（2026-09-30 真 ES 验收）：其加权查询打 KB 字段 `title`/`content`，实体索引静默 0 命中；本模块自建 `multi_match` DSL 经 `core.client.search` 执行。不得写成「复用平台 accurate_search」。
- **dense 槽空**：实体索引无 `embedding` 字段（`KgEntity.embedding` 是 PG JSONB，无写路径回填向量到 ES），发 knn 查询会在缺字段上报错——返回 `[]` 而不是伪造检索；空表理由 = 冻结常量 `DENSE_ENTITY_DISABLED_REASON`，由 `kg_fusion.FusionOutcome.dense_reason` 记入审计。
- `knowevo_entities_m2` 是验收用最小索引（BM25 standard analyzer，无 IK 中文分词、无 dense），索引生命周期与写路径归 T-08；本模块不得写成「生产实体检索已全部接通 ES」。
- 本模块不做任何跨 id 空间映射（`fuse` 同款纪律）；`document.id` 与 `stable_id` 不得同 fuse。

## 验收锚点

- `pytest test/backend/services/knowevo/test_es_raw_list.py -v`：15 用例离线全绿（id 空间塑形 / 缺 stable_id 丢弃 / 租户 filter 强制注入 + 索引名与 top_k 透传 / 空白 query 零调用 / seam dict 形状与 alias 归一 / dense 三态守卫 / 工厂 env 缺失 None + 懒构造 host/api_key 断言）；
- 全量回归：`pytest ../test/backend/services/knowevo/ -q` 基线不降 + `ruff check backend/services/knowevo mcp_servers` 0 新违例；
- 契约双副本 IDENTICAL（根 `knowevo/` + 仓内副本）。
