# doc_asset_service.py —— asset_search 资产检索服务
**新建**: 2026-09-29（asset-search 立项；MCP 包装同期完成）· 依据: asset-search 立项设计（详见正文职责节）

## 职责
doc_asset_t 的文本检索能力，冻结工具 `asset_search` 的唯一包装对象。此前资产域只有按 id/asset_no 精确查（ingest/kg/decision 三处自用），**无任何文本/语义检索**——本服务是「先补能力、再包装」，不是薄包装；既有精确查**不迁移**（裁决：迁移动三处生产调用点需整链重验，收敛留作后续独立小任务）。只读：不建索引、不写路径（ES 资产索引写入属后续接线）。

## 接口冻结

> **2026-09-29 asset_search M1 增量回填**：新建 `DocAssetService`（本文件为该模块首份契约，无旧冻结面）。ES-first + PG ilike 兜底，与 `graph_store.entity_lookup`（L1 第 3 步）逐位同构：`es_client` 经 `__init__` 可选注入（默认 None = 纯 PG，行为离线可跑），注入客户端须提供 `asset_search(tenant_id, query, limit) -> list[dict]`（同步或协程皆可，服务自适应）；ES 异常 → `logger.debug(..., exc_info=True)` 静默回退，ES 返回空/None/过滤后为空 → 回退 PG，ES 过滤后命中 < limit → PG 按 id 去重补足；`query.strip()` 为空 → 直接 `[]`（不触任何后端）。打分语义（冻结裁决）：ES `_score` 按**过滤后集合**最大值归一 0–1（top=1.0；max<=0 → 全 0.0），**原分保留在 `why.es_score_raw`**；排序按归一分降序、tie 按 `authority_level` 升序。PG 兜底路径无相关性信号：score 全 0.0，`why.ranked_by` 记录确定性排序规则，**绝不伪造分数**。parse 门槛唯一 = `parse_status == "processed"`（2026-09-29 真库实查 parse_status 仅 processed/no_index_chunk 两值）；**parse_quality 只作为字段透出供审计，不设质量阈值——立项未定义，不发明**。supersede 默认折叠：SQL `NOT EXISTS`（同租户其他行 supersede_of = 本行 id），不做全表回捞判断。ES over-fetch = `min(limit*3, 30)`：后过滤发生在 ES 排序之后，须多取候选才能保足页大小。

```python
class DocAssetService:
    def __init__(self, es_client: Any = None, session_factory: Any = None) -> None: ...

    async def search_assets(
        self,
        tenant_id: str,
        query: str,                                  # 自然语言/关键词
        modality: str | None = None,                 # 精确过滤
        doc_type: str | None = None,                 # 精确过滤
        authority_min: int | None = None,            # 权威下限
        include_superseded: bool = False,            # 默认折叠被取代版本
        limit: int = 5,
    ) -> list[AssetHit]:
        """ES-first hybrid（title+metadata），失败/空索引回退 PG ilike（title）。
        返回形状与 kg_search 对齐：id / asset_no / title / modality /
        doc_type / authority_level / score / why（可审计命中原因）。"""

    async def _search_assets_pg(self, tenant_id: str, q: str,
                                modality: str | None, doc_type: str | None,
                                authority_min: int | None,
                                include_superseded: bool,
                                limit: int) -> list[AssetHit]: ...
```

`_search_assets_pg` 是独立兜底方法（仿 `graph_store._entity_lookup_ilike` 先例）：供 ES 分支回退/补足复用，供离线测试 stub，不需要数据库即可测编排逻辑。`session_factory` 可注入（mirror `ingest_service` 惯例），默认解析共享会话上下文管理器。

## ES seam 形状（注入客户端须实现）

`asset_search(tenant_id, query, limit) -> list[dict]`（同步或协程皆可），dict 字段约定：

| 键 | 类型 | 约定 |
|---|---|---|
| `id` | str | doc_asset_t 行 id（**必有**；非 dict / 缺 id / 缺 title 的命中按畸形跳过，仿 `_as_entity_card` 防御） |
| `asset_no` | str | 资产编号 |
| `title` | str | **主检索面**（必有） |
| `modality` | str | 精确过滤维度 |
| `doc_type` | str | 精确过滤维度 |
| `authority_level` | int | 1–4 档；缺失按列默认 3 补（**不得落 0**——0 会在 authority 升序 tie-break 中夺魁） |
| `score` | float | ES 原始分（服务层归一，原分进 `why.es_score_raw`） |
| `superseded` | bool | 缺省 False；该行是否已被其他版本取代 |
| `parse_status` | str | 门槛：仅 `"processed"` 通过（缺失视同未通过） |
| `parse_quality` | float | 仅透出供审计（无质量阈值） |

ES 资产索引**写入路径不存在**（从未 wired，属后续接线）：空索引/宕机时回退 PG ilike，行为安全（真实 ES 接入随后续工作留证据）。

## 结果形状 AssetHit（dataclass 声明于 services/knowevo/schemas.py）

字段：`id, asset_no, title, modality, doc_type, authority_level, score, why, parse_status?, parse_quality?, superseded=False`。`why` 两路形状（审计契约，"束=可审计"裁决）：

- ES 路径：`{"es_score_raw": <raw>, "matched": "title+metadata", "filters_applied": {...}, "parse_gate": "processed"}`
- PG 路径：`{"matched": "title ilike", "ranked_by": "authority_level asc, created_at desc", "es_score_raw": None, "filters_applied": {...}}`

`filters_applied` 恒记录调用方请求的过滤集（未启用者记 None/False，不省略）——审计要看到「问过什么」，不只是「命中什么」。

## PG 兜底排序理由

该路径无相关性信号，唯一诚实的排序是资产自身治理元数据：`authority_level` 升序（1 国标 … 4 科普，最权威在前）+ `created_at` 降序（同档新者在前）。确定性、可解释、零伪造。

## 验收锚点

- `pytest test/backend/services/knowevo/test_doc_asset_service.py -v`：离线编排全绿（ES 命中在前+补足去重 / 异常回退 / 空回退 / 全被过滤回退 / modality-doc_type-authority_min 后过滤 / supersede 折叠与保留 / 归一化 top=1.0 且原分进 why / max<=0 全 0 / PG 形状 / 空 query / 同步异步适配 / fetch_n=min(limit*3,30) / parse 门槛 / limit 截断）；
- 真实 ES 检索（对 58 条真库资产）的验证证据另录交付材料，不在本契约验收内。
