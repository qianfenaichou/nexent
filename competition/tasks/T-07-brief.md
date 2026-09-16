# T-07：图存储 GraphStore（PG JSONB）+ kg_search MCP 工具
**Blocked by**: T-06（KGService 抽取/对齐/合并流水线 + search v0 薄词面，已合并 develop）、T-01（部署基线，模型/租户就绪）、T-03（12 表 ORM + const.py 接线）
**拆分裁决**（03 计划 §3.4：新文件 ≤8、新码 ≤1200）：本任务拆两步——**T-07a 先做 GraphStore 抽象 + PgJsonbGraphStore + PoC 三必测点**（图存储 seam 是后续一切查询的地基）；T-07b 再做 `knowevo_mcp` FastMCP 服务 + kg_search/kg_stats 工具双注册。T-07b 依赖 fastmcp/mcp 依赖引入（走 §4.1 矩阵，登记 THIRD_PARTY_NOTICE）。若单会话做完 T-07a 且预算有余，可直接续 T-07b，回报注明。

**独占文件**:
- `backend/services/knowevo/graph_store.py`（**新建**，接口冻结于框架包同名 .md：GraphStore ABC + PgJsonbGraphStore）
- `test/backend/services/knowevo/test_graph_store.py`（**新建**，现行视图过滤/历史视图可查/supersede 语义/reachable_decisions 命中率）
- `backend/pipeline/gen_synthetic_graph.py`（**新建**，合成 2 万实体/3 万边，PoC 数据生成；对应备忘录 09 §3）
- T-07b 独占：`mcp_servers/knowevo_mcp/`（server.py + schemas.py + client.py + tests/）、`backend/tool_collection/mcp/kg_tools.py`（**新建**）
- 委托登记：`backend/tool_collection/mcp/local_mcp_service.py`（**上游文件，禁改**，注册动作留 T-08 接线清单）

**待接线项**（登记给 T-08，本任务不碰）:
- Local MCP 工具注册进 `local_mcp_service.py`；`KW_GRAPH_STORE_BACKEND` 环境变量若需新增（走 const.py）
- ES 冗余索引同步（graph_store.py.md 要点 3：ES 优先回退 PG GIN）——T-07a 若 ES 不可用，先 PG GIN 带 ES 桩接口，标注"ES 写入未接"
- 多跳/决策类 3 个工具（decision_card_render 等）归 T-09

**禁改清单**: 上游共享文件（apps/app_factory.py、config_app.py、runtime_app.py、consts/const.py、pyproject.toml、`backend/tool_collection/mcp/local_mcp_service.py`）；T-06 已冻结接口（KGService 既有方法签名：extract/align/merge_delta/search/split_entity/pending_to_proposals）；T-03 已冻结表结构；`deploy/sql/migrations/v2.5.5_kw_001/002` 既有迁移文件。

**允许的新依赖**（§4.1 矩阵白名单）: T-07a **无**（networkx 归 T-06/T-11，本任务图算法在服务层）；T-07b 引入 fastmcp（Apache-2.0）+ mcp 官方 SDK（MIT），登记 THIRD_PARTY_NOTICE。

**要构建的行为**（用户视角端到端）:
图存储访问统一走 `GraphStore` seam（调用方只 import 本模块，不直写 SQL/CTE）：邻居查询支持现行视图过滤（`valid_view=True` 自动叠加 valid_at/invalid_at 谓词）与多跳扩展（hop 由服务层循环调用，一次一跳留束搜索剪枝空间）；`supersede` 批量打 invalid_at 时间戳；`reachable_decisions` 走 kg_evidence_t 反查（不是图遍历）；`stats` 出规模数字。合成图数据能跑 PoC 三必测点，基准数字落 `competition/docs/`。T-07b 让 Agent 能通过 MCP 调 `kg_search`（词面+邻域，返回 EntityCard/EdgeCard）+ `kg_stats`，工具报 `used_tokens`/`elapsed_ms`，输入护杆生效。

**验收命令**:
```bash
cd backend && uv run pytest ../test/backend/services/knowevo/test_graph_store.py -v
cd backend && uv run python -m pipeline.gen_synthetic_graph --entities 20000 --edges 30000
# T-07b（若续做）：
cd mcp_servers/knowevo_mcp && uv run pytest tests -v
```

**验收标准**（T-07a）:
- [ ] `GraphStore` ABC 方法齐（upsert_entities/upsert_relations/neighbors/multi_hop/supersede/reachable_decisions/entity_lookup/stats），调用方 import 自 `graph_store` 模块、无直写 SQL
- [ ] `neighbors` 现行视图过滤：`valid_view=True` 只返回有效边；`supersede` 后新视图不含旧边、历史视图（`valid_view=False`）可查回旧边
- [ ] `upsert` 幂等：同 stable_id 二次 upsert 不产生重复行（合并 props 或跳过）
- [ ] 租户隔离：跨租户查询零泄漏（FakeStore/PgStore 契约一致，坑 #23 双路兼容）
- [ ] PoC：`gen_synthetic_graph` 能生成 2 万实体/3 万边；基准（P1 多跳 p95<1.5s、P2 批量 supersede p95<200ms）数字实测落 `competition/docs/`，未达标如实记录
- [ ] ruff 全过；注释/docstring 英文；无新增环境变量（若需 `KW_GRAPH_STORE_BACKEND` 登记待接线）
- [ ] 新踩坑记 `competition/docs/pitfalls.md`

**验收标准**（T-07b，若续做）:
- [ ] `kg_search`/`kg_stats` Pydantic 输入输出与备忘录 10 §1 完全一致（query/hop≤2/top_k≤20/ontology_version；输出 EntityCard+EdgeCard+valid_view）
- [ ] 工具带 `used_tokens`/`elapsed_ms`；错误返回结构化 `{error_code, hint}`
- [ ] 双注册不漂移：server.py（FastMCP）与 kg_tools.py（Local MCP）共用同一 schema 源
- [ ] `pytest mcp_servers/knowevo_mcp/tests` 绿：happy path + 护杆（hop=3 拒绝）+ 错误结构
- [ ] THIRD_PARTY_NOTICE 登记 fastmcp/mcp

**Evidence**: <GraphStore 测试输出 / PoC 基准数字落盘路径 / kg_search 工具调用日志——没有证据=没做完>

## 反幻觉条款（发任务时必附）
开工先读仓库根 AGENTS.md；框架包 `knowevo/backend/services/knowevo/graph_store.py.md` 与 `knowevo/mcp_servers/knowevo_mcp/SPEC.md` 是唯一事实（文件路径以讲义为准，与仓库冲突时停下报告）；T-06 的 KGService 方法签名禁改（只能调用 search v0 作种子）；不得发明环境变量；不得改 `local_mcp_service.py`（注册留 T-08）；PoC 基准用合成数据不得用真实语料（真实评估归 T-10）；不引入白名单外依赖。