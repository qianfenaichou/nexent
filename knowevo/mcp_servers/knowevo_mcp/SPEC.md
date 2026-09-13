# knowevo_mcp —— 自研 MCP 工具服务（FastMCP v4）
**归属任务**: T-07（kg_search 等 4 个）/ T-09（决策类 3 个）/ T-12 前对接线 · **依据**: [备忘录 10-A2 §1](../../../04-算法与架构决策备忘录/10-A2-MCP工具面与A3版本模型.md)

## 形态与注册（ADR 决策）
- 独立 FastMCP v4 服务：`mcp_servers/knowevo_mcp/server.py`（`mcp run` / Docker 双形态；华为云一卡部署是决赛形态）；
- 同一份 Pydantic 工具 schema 导出两处：① FastMCP 装饰器（独立服务）② `backend/tool_collection/mcp/kg_tools.py` Local MCP 内嵌注册（本地开发/演示形态，走 T-08 接线）——**单一 schema 源，双注册，禁双份漂移**；
- 全部工具经 config 服务的内部 HTTP 调 services 层（不直连 DB）——独立服务不复制业务逻辑。

## 8 工具 schema（冻结，备忘录 10 §1 表）
`kg_search` / `kg_multi_hop` / `kg_evolution_trace` / `ontology_diff` / `asset_search` / `decision_card_render` / `evidence_verify` / `kg_stats`

```python
# 示例: kg_search 的 Pydantic 冻结定义（其余 7 个同风格, 详见备忘录 10）
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

## 工具命名与行为纪律
1. 全部 `snake_case`、无行业前缀（行业是运行时参数——多行业适配的设计）；
2. 每工具附 `used_tokens`/`elapsed_ms` 返回字段（cost-ledger 自动采集的最前端）；
3. 输入上限：`kg_multi_hop.depth ≤3`、`beam ≤3`（硬编码护杆，防 Agent 幻觉传参）；
4. 错误返回结构化（`{error_code, hint}`），供 Skill 层决定降级而非重试。

## 目录
```
mcp_servers/knowevo_mcp/
├── server.py          # FastMCP app + 8 工具注册
├── schemas.py         # 8 工具的 Pydantic 输入输出（单一 schema 源）
├── client.py          # 调 config 服务 HTTP 的薄客户端（带 tenant 注入）
├── Dockerfile         # 华为云部署形态
└── tests/
```

## 验收锚点
- `pytest mcp_servers/knowevo_mcp/tests -v`：8 工具 happy path + 输入护杆（depth=4 拒绝）+ 错误结构；
- Local MCP 注册后 Nexent Agent 工具列表可见（截图进 deliverables）；
- MCP 官方 SDK 兼容性冒烟（`mcp list-tools`）。
