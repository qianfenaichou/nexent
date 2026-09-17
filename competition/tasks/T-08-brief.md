# T-08：接线集中任务（波次 4 前沿 · 解锁 T-09/T-10a-2）

> 本简报由调度会话（2026-09-17）据 03 计划 §3.2 接线清单 + T-05/T-06/T-07/T-10a 四个简报的"待接线项"登记汇总生成。
> **核心原则**：T-08 只清"待接线项"，不实现新功能；每项接线必须能在代码里 grep 到落地行（坑 #27 教训）。

**Blocked by**: T-04~T-07、T-10a-1（均已合并 develop）

**独占文件**（本任务可创建/修改）:
- `backend/apps/config_app.py`（**接线文件**：加 `include_router(knowevo_router)`——T-05 登记）
- `backend/apps/runtime_app.py`（**接线文件**：加 `include_router(knowevo_router)`）
- `backend/tool_collection/mcp/local_mcp_service.py`（**上游禁改文件**，T-07 登记"注册动作留 T-08"——本任务例外授权）
- `backend/services/knowevo/llm_client.py`（**新建**，T-08 唯一新文件：三档模型真实 LLM async callable，对齐上游 `tenant_config_manager.get_model_config(MODEL_CONFIG_MAPPING["llm"], tenant_id)` 模式）
- `backend/services/knowevo/pipeline/` 下接线点（若 ingest/eval 需要真实 llm 注入，只改注入行不改算法）
- `backend/consts/const.py`（**接线文件**：若需新增 env 常量，只追加不改既有）
- `backend/pyproject.toml`（**接线文件**：lockfile 合并；本任务**不引入** instructor/networkx——归 T-09/T-06）
- `competition/tasks/T-08-brief.md`（本文件）
- 权限种子：`backend/database/role_permission_db.py` 或迁移 SQL（RBAC 行 `RESOURCE.KNOWLEDGE_GRAPH.MANAGE`，knowledge_graph_app.py 注释明示"RBAC rows are a T-08 wiring item"）

**待接线项**（承接自各简报，本任务处理）:
| # | 来源 | 接线内容 | 落地位置 |
|---|---|---|---|
| 1 | T-05 | `include_router(knowevo_router)` 一行 | config_app.py + runtime_app.py |
| 2 | T-07 | Local MCP 工具注册 `kg_tools.wire()` → `local_mcp_service.mount` | local_mcp_service.py |
| 3 | T-06/T-10a | LLM 真实调用链路（extract/align/judge/expand 的 tier 路由） | llm_client.py + 注入点 |
| 4 | T-06 | `calibrate_thresholds` 产物回写 env | const.py 只读 → 台账记录，不写回 env（降级为文档化） |
| 5 | T-10a | 检索链路：知识库 → RAG 上下文 → 生成（E1 基线） | 评估工作台接线（T-10a-2 用） |
| 6 | T-05 | RBAC 权限种子 `RESOURCE.KNOWLEDGE_GRAPH.MANAGE` | role_permission 种子 |

**禁改清单**: 上游共享文件除上表列出的接线文件外一律禁改（apps/app_factory.py 只读、`apps/runtime_app.py`/`config_app.py` 仅加 include_router 行）；T-03 表结构（不 ALTER）；`deploy/sql/migrations/` 已存在文件；前端（路由/菜单注册归 T-12，本任务不碰前端）。

**允许的新依赖**: 无（fastmcp/mcp 已在树；instructor/networkx/deepeval 归各自任务）

**要构建的行为**（用户视角端到端）:
1. `GET /api/knowevo/ontology/proposals` 等 5 个管理端点**真实可访问**（config 服务起后 curl 有响应，不再是"代码存在但 404"）
2. Agent 的本地 MCP 工具列表里出现 `kg_search`/`kg_stats`（T-07b 双注册的 Local 侧真正挂上）
3. 抽取/对齐/judge 不再依赖注入 fake——`llm_client.py` 按 tier 路由到平台注册的三档模型（`KW_LLM_SMALL/MID/LARGE_MODEL_ID` 未配置时降级报清晰错误，不静默失败）
4. 本体工作台管理员（ADMIN 角色）能访问，不再因缺 RBAC 种子被 403 锁死

**验收命令**:
```bash
cd backend && uv run pytest ../test/backend/services/knowevo/ -q --no-header   # 全绿（含新增接线测试）
cd backend && uv run python -c "from apps.config_app import app; print(len(app.routes))"  # router 挂载冒烟
cd backend && uv run python -c "from tool_collection.mcp.local_mcp_service import local_mcp_service; print([t.name for t in local_mcp_service._tool_manager._tools.values()])"  # kg_search/kg_stats 在列
cd backend && uv run ruff check services/knowevo/llm_client.py apps/config_app.py apps/runtime_app.py tool_collection/mcp/local_mcp_service.py
```

**验收标准**:
- [x] `config_app.py`/`runtime_app.py` 各含且仅含一行 `include_router(knowevo_router)`（grep 可证，见 Evidence）
- [x] `local_mcp_service.py` 里能 grep 到 `kg_search`/`kg_stats` 挂载（坑 #27：勾验收项前必须指到实现行；挂载后 `MountedServer(prefix='knowevo')` 工具表含两工具）
- [x] `llm_client.py` 实现 `LlmRouter(tenant_id)` + `build_llm_callable(tenant_id)`，三档 tier 活读取 `KW_LLM_*_MODEL_ID`，未配置模型报 `LLMConfigurationError`（清晰错误）
- [x] 新增接线测试（test_llm_client.py 5 个 + test_t08_wiring.py 10 个 HTTP/挂载护栏）进 test 树
- [x] 不引入任何新依赖（pyproject 零改动；fastmcp/mcp 已在树）
- [x] 前端零改动（菜单接线归 T-12）
- [x] 新踩坑记 `competition/docs/pitfalls.md`（#29 ruff --fix 删 globals() import、#30 包装型假阴性、#31 router 双前缀）

**运行时验证补充（2026-09-17）**:
- TestClient HTTP 冒烟发现 **T-05 遗留接线 bug**：`knowledge_graph_app.py` 的 `prefix="/api/knowevo"` 与 `create_app(root_path="/api")` 叠出 `/api/api/knowevo` → 5 端点全 404。已修为 `prefix="/knowevo"`（T-08 接线职责）。
- 修复后真实命中：`GET /api/knowevo/ontology/proposals` → 403（路由存在+鉴权拦截）、`POST .../review` → 405（POST 路由存在）、runtime_app 同验——**均非 404**。
- 全量 161 passed（146 既有 + 5 llm_client + 10 wiring 护栏），14 skipped（PG 门控）。

**Evidence**:
```bash
# 1. 全量测试（161 passed = 146 既有 + 5 llm_client + 10 wiring 护栏）
$ cd backend && uv run pytest ../test/backend/services/knowevo/ -q --no-header
161 passed, 14 skipped, 5 warnings

# 2. PG 集成全量（RUN_POSTGRES_INTEGRATION=1，真实 PG 5434）：165 passed, 0 skipped
#    （含 GraphStore/抽取流水线/MCP 双注册集成测试，全部真库执行通过）

# 3. router 挂载冒烟（FastAPI _IncludedRouter 惰性包装，经 original_router 断言）
config_app knowevo routes (5): /api/knowevo/ontology/proposals, .../review, .../versions, .../versions/{version}/metrics, .../diff
runtime_app knowevo routes (5): 同上 5 条

# 4. HTTP 端到端（TestClient）：prefix 修复前 404，修复后 403/405（路由真实命中）
$ GET  /api/knowevo/ontology/proposals            -> 403 (route hit, auth gate)
$ POST /api/knowevo/ontology/proposals/review     -> 405 (route hit, method gate)
$ POST /api/knowevo/ontology/versions             -> 405
$ GET  /api/knowevo/ontology/versions/v1/metrics  -> 403
$ GET  /api/knowevo/ontology/diff?from=v0&to=v1   -> 403

# 5. Local MCP 双注册冒烟（FastMCP MountedServer 命名空间挂载）
knowevo mounted server tools: ['kg_search', 'kg_stats']

# 6. ruff：llm_client.py 零告警；config_app/runtime_app 的 4 个既有告警（I001/BLE001/RUF010）经 git stash 对比确认是上游基线遗留，非本任务引入

# 7. RBAC 种子真库验证（nexent-postgresql）：INSERT 0 2；幂等重跑同结果；查询得 SU/ADMIN 各行
```

**合并门禁备注**: 已合并 develop（33ac351b3 fix 含护栏测试；⚠️ fix 因在 develop 上直接验证发现并提交，未走独立分支——流程偏差已记录，教训见坑 #31 备注）。
