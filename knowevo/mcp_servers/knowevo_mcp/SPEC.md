# knowevo_mcp —— 自研 MCP 工具服务（FastMCP v4）
**依据**: [架构决策记录](../../docs/adr/0001-0008-已冻结架构决策.md)（单一 schema 源、双注册形态见下节）

## 形态与注册（ADR 决策）
- 独立 FastMCP v4 服务：`mcp_servers/knowevo_mcp/server.py`（`mcp run` / Docker 双形态；华为云一卡部署是决赛形态）；
- 同一份 Pydantic 工具 schema 导出两处：① FastMCP 装饰器（独立服务）② `backend/tool_collection/mcp/kg_tools.py` Local MCP 内嵌注册（本地开发/演示形态）——**单一 schema 源，双注册，禁双份漂移**；
- 全部工具经 config 服务的内部 HTTP 调 services 层（不直连 DB）——独立服务不复制业务逻辑。

## 8 工具 schema（冻结）
`kg_search` / `kg_multi_hop` / `kg_evolution_trace` / `ontology_diff` / `asset_search` / `decision_card_render` / `evidence_verify` / `kg_stats`

> **2026-09-29 asset_search 增量回填**：注册面 8→9——冻结 8 工具 + 冻结外 `skill_template_apply` 全部注册；`asset_search` 由 asset-search 立项闭合（服务层 `DocAssetService.search_assets`，schema 摘录见下「### asset_search」增量块，kg_search 冻结示例块零改动；该增量与最初冻结行存在登记在案的加法 I/O 形状偏差）。单一 schema 源 = `mcp_servers/knowevo_mcp/schemas.py`；注册面唯一事实源 = `KG_MCP_TOOL_NAMES`（`backend/tool_collection/mcp/kg_tools.py`）。

```python
# 示例: kg_search 的 Pydantic 冻结定义（其余 7 个同风格, 详见 mcp_servers/knowevo_mcp/schemas.py）
class KGSearchInput(BaseModel):
    query: str
    hop: int = Field(1, ge=1, le=2)
    top_k: int = Field(5, ge=1, le=20)
    ontology_version: str | None = None

class KGSearchOutput(BaseModel):
    entities: list[EntityCard]   # {stable_id, name, class, props, evidence_ids}
    edges: list[EdgeCard]
    valid_view: datetime         # 现行视图口径时间
```

### asset_search（2026-09-29 增量回填；形状以 schemas.py 实际模型为准）

```python
# 单一 schema 源：mcp_servers/knowevo_mcp/schemas.py（此处摘录形状语义）
class AssetCard(BaseModel):
    id: str
    asset_no: str
    title: str
    modality: str
    doc_type: str
    authority_level: int   # 1 国标 / 2 指南 / 3 说明书 / 4 科普
    score: float           # ES 路径 = 归一 0-1（top=1.0）；PG 兜底 = 0.0（无相关性信号，绝不伪造）
    why: dict              # 可审计命中原因：ES 原分/PG 排序规则 + 过滤集 + parse 门槛
    parse_status: str | None = None     # 唯一 parse 门槛 = "processed"
    parse_quality: float | None = None  # 仅审计透出——质量阈值立项未定义，不发明
    superseded: bool = False            # 是否已被其他版本取代（默认折叠）

class AssetSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    modality: str | None = Field(None, max_length=12)    # 精确等值过滤（单值）
    doc_type: str | None = Field(None, max_length=24)    # 精确等值过滤
    authority_min: int | None = Field(None, ge=1, le=4)  # 权威下限（非精确值过滤）
    include_superseded: bool = False                     # 默认折叠被取代版本
    limit: int = Field(5, ge=1, le=20)                   # 护杆对齐 kg_search（SPEC 纪律 3）

class AssetSearchOutput(BaseModel):
    assets: list[AssetCard]
    valid_view: datetime         # 现行视图口径时间
    used_tokens: int = 0
    elapsed_ms: int = 0
```

## 工具命名与行为纪律
1. 全部 `snake_case`、无行业前缀（行业是运行时参数——多行业适配的设计）；
2. 每工具附 `used_tokens`/`elapsed_ms` 返回字段（cost-ledger 自动采集的最前端）；
3. 输入上限：`kg_multi_hop.depth ≤3`、`beam ≤3`（硬编码护杆，防 Agent 幻觉传参）；
4. 错误返回结构化（`{error_code, hint}`），供 Skill 层决定降级而非重试。

## 目录
```
mcp_servers/knowevo_mcp/
├── server.py          # FastMCP app + 9 工具注册
├── schemas.py         # 9 工具的 Pydantic 输入输出（单一 schema 源）
├── client.py          # 调 config 服务 HTTP 的薄客户端（带 tenant 注入）
├── Dockerfile         # 华为云部署形态
└── tests/
```

## 验收锚点
- `pytest mcp_servers/knowevo_mcp/tests -v`：9 工具 happy path + 输入护杆（depth=4 拒绝）+ 错误结构；
- Local MCP 注册后 Nexent Agent 工具列表可见（截图进 deliverables）；
- MCP 官方 SDK 兼容性冒烟（`mcp list-tools`）。

## kg_search 三路 RRF 融合行为（2026-09-30 增量，L6-M2 接线）

> **行为**：`ELASTICSEARCH_HOST` 配置存在时，`kg_search_handler` 先走三路融合（工厂 `services/knowevo/kg_fusion.py::fused_entity_cards`，内核 `rrf_fusion.fuse` k=60，全路统一图实体 id 空间 `stable_id`）：bm25 = 实体 ES 索引原始名次（`services/knowevo/es_raw_list.py` **自建** `multi_match(["name","aliases.alias"], operator=and)` 查询——平台 `accurate_search` 面向 KB schema 字段 title/content、对本索引**静默零命中**（2026-09-30 实库验收实测），租户 term filter 强制注入、缺 stable_id 的 hit 丢弃）；dense = 诚实空表（理由 `entity_index_has_no_embedding_field` 入审计）；graph = `services/knowevo/graph_retrieve.py`（entity_lookup 种子 → 逐跳 BFS → `(hop, -degree, stable_id)` 确定性序，禁 PPR）。融合序前 `top_k` 个 stable_id 作为种子 → `neighbors` 邻域扩展调用不变 → **输出成员不变**（仍是 neighbors 结果，不发明成员），**仅实体卡顺序改变**：种子卡按融合名次在前，其余邻域卡按 `(hop, -degree, stable_id)`（`ordering_key`，未知 id 排全序尾部）排后。`_store()` 在 adapter 存在时注入 `es_client`，使 `entity_lookup` 的 ES-first 分支生产生效（`KW_ENTITY_ES_INDEX` 缺省 `knowevo_entities_m2`）。
>
> **降级**：ES env 缺失（工厂返回 None）/ 融合路径任何异常 / 融合结果为空 → **逐位回退现状路径**（`entity_lookup` → `neighbors`；「逐位」的精确口径 = **handler 代码分支**与接线前逐字一致：env 缺失时连 store 行为也逐位同接线前（纯 PG lexical）；env 存在时回退路径的 `entity_lookup` 已因 `_store()` 注入变为 ES-first），debug log 记录、绝不向 MCP runtime 抛出；`kg_search_failed` 错误码语义不变（仅由回退路径的 store 失败产生）。bm25 单路空表不是回退条件——RRF 缺路=空表语义，融合退化为 graph 单路保持其自身顺序。
>
> **诚实边界**：① bm25 索引 `knowevo_entities_m2` 为验收用最小索引（standard analyzer **无 IK 中文分词**，中文召回缺口仍在）；② **dense 槽空**：实体索引无 `embedding` 字段（`KgEntity.embedding` 是 PG JSONB，无 ES 回填写路径），融合实际为 bm25+graph 两路，`FusionOutcome.dense_reason` 审计可见，不写成「三路满员」；③ 索引生命周期/写路径属后续接线，不写成「生产实体检索已全部接通 ES」；④ **asset_search 不接融合**（dense 无资产索引可服务，接了是单路表演——handler docstring 已声明）；⑤ 权威先验本回合未应用（实体卡无统一 authority 字段），若未来应用只许路内重排、RRF 层禁乘；⑥ 融合种子若全部无法在图中解析（如 ES 实体索引陈旧于 PG），输出为空且**不回退**（回退路径此时本可给出非空结果；是否把「邻域空且种子非空」纳入回退条件属设计裁量，本回合不改）；⑦ hop 组合说明：`inputs.hop=2` 时图路 BFS 深度 2 + 邻域扩展 hop=2，输出邻域相对原始命中最远可达 4 跳——「输出成员不变」指 = `neighbors` 结果，不指与接线前同成员。
>
> 依据：L6 检索融合设计评审（§6/§7、R1–R12）；契约见 `knowevo/backend/services/knowevo/{es_raw_list,graph_retrieve,kg_fusion}.py.md`。
