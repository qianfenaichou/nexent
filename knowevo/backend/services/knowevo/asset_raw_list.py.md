# asset_raw_list.py —— asset_search 融合二期资产 ES 原始列表适配器
**新建**: 2026-09-30 · 依据: asset_search 融合二期设计结论（资产 id 空间冻结裁决，详见正文职责节）

## 职责

把资产索引的 raw ES 命中塑形进**资产 id 空间 `AssetHit.id`（str 规范化）**——`rrf_fusion.fuse` 在 asset 语境唯一可消费的形状（id 空间冻结裁决：禁止与 `document.id` / `stable_id` 同 fuse）。镜像 `es_raw_list.EsRawListClient` 的分层：查询构造在本模块、租户强制过滤、dense 槽诚实空表。

两个面：

1. `asset_bm25_hits` —— asset 融合的 BM25 原始名次列表；
2. `asset_dense_hits` —— dense 槽位，**本回合诚实返回 `[]`**（资产索引无 `embedding` 字段；`es_index_writer` 冻结 dense 写回理由）。

**查询构造禁走 `accurate_search`**：其加权查询打 KB 的 `title`/`content`，资产索引无 `content` 会静默零命中。本模块自建 `multi_match` over `title`（`operator=and`）+ 强制 tenant filter，走 raw `client.search`。

环境变量在本模块读取（**不改 `backend/consts/const.py`**）：`ELASTICSEARCH_HOST` / `ELASTICSEARCH_API_KEY` + `KW_ASSET_ES_INDEX`（默认 `knowevo_assets_m2`——asset_search 探针灌装的验收用最小索引）。生产索引命名留待后续决策；**本模块不建生产索引、不改 `es_raw_list` 默认**。

## 接口冻结

```python
ASSET_INDEX_DEFAULT = "knowevo_assets_m2"
ASSET_INDEX_HAS_NO_EMBEDDING_FIELD = "asset_index_has_no_embedding_field"

ENV_ES_HOST = "ELASTICSEARCH_HOST"
ENV_ES_API_KEY = "ELASTICSEARCH_API_KEY"
ENV_ASSET_INDEX = "KW_ASSET_ES_INDEX"

class AssetRawListClient:
    def __init__(self, core, asset_index=ASSET_INDEX_DEFAULT, embedding_model=None): ...
    def asset_bm25_hits(self, tenant_id, query, top_k) -> list[dict]: ...
    def asset_dense_hits(self, tenant_id, query, top_k) -> list[dict]: ...

def build_asset_raw_client(core=None) -> AssetRawListClient | None: ...
```

`core` duck-typed：只调 `client.search`；离线测试注入 fake。

## 语义（冻结）

**id 抽取（AssetHit.id 空间）**：ES `_id` 优先，否则 `document.id`；非 str / 空串 → **丢弃**。**禁止**用 `asset_no` 当融合 id（id 空间裁决：`AssetHit` 两字段并存，不得混用同一列表）。

**BM25 路（`asset_bm25_hits`）**：
- 塑形为 `{"id", "title", "asset_no", "authority_level", "es_score", "index"}`，**ES 相对顺序即列表顺序**；
- `es_score` / `authority_level` 仅随行审计，RRF 只吃名次；authority 重排（若做）只允许路内、进 fuse 前——**融合后禁止再乘**；
- 空白 query 返回 `[]` 且**零 ES 调用**。

**租户隔离**：每一路强制 `filter=[{"term": {"tenant_id": tenant_id}}]`，不接受调用方过滤。

**dense 槽（`asset_dense_hits`）**：无条件返回 `[]`、零 ES 调用；multimodal 守卫保留（只有 `model_type == "embedding"` 语义上可服务本槽）。空表理由 = 冻结常量 `ASSET_INDEX_HAS_NO_EMBEDDING_FIELD`。

**工厂（`build_asset_raw_client`）**：`ELASTICSEARCH_HOST` 缺失/空 → `None`（调用方逐位保持现状）；SDK core 懒构造；注入 `core` 跳过构造。

## 诚实边界

- dense 槽空 → 融合两路满员**今天做不到**；点火门（`asset_fusion`）保证单路不表演。
- `knowevo_assets_m2` 是验收用最小索引（standard analyzer、无 IK、无 dense、title 唯一实质检索面）；索引生命周期与写路径属后续运维决策。
- 本模块不做跨 id 空间映射；`document.id` 不得与 `AssetHit.id` 同 fuse。

## 验收锚点

- `pytest test/backend/services/knowevo/test_asset_raw_list.py -v`：10 用例离线全绿（id 空间塑形与丢弃 / `_id` 与 `document.id` 回退 / 租户 filter 强制 + 索引与 size 透传 / 空白 query 零调用 / dense 恒空与 multimodal 守卫 / 工厂 env 门与索引覆盖）；
- 全量回归：`pytest ../test/backend/services/knowevo/ -q` 基线不降 + `ruff check backend/services/knowevo mcp_servers` 0 新违例。
