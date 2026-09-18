# T-18a：D5 三件套 —— 本体工作台导航 + `GET /ontology/versions/active` + g6 依赖声明

**状态**: ★ 待开发（2026-09-18 调度会话）
**Blocked by**: 无（波次 0，止血项）
**独占文件**（本任务创建/修改）:
- `frontend/components/navigation/SideNavigation.tsx`（**接线文件·独占**：新增本体工作台菜单项）
- `frontend/public/locales/zh/common.json` / `frontend/public/locales/en/common.json`（**接线文件·独占**：新增 `sidebar.knowledgeGraph` 键）
- `frontend/package.json`（**接线文件·独占**：补 `@antv/g6` 声明）
- `backend/apps/knowledge_graph_app.py`（**接线文件·独占**：新增 active 路由）
- `backend/services/knowevo/ontology_service.py`（加性扩展：`list_versions` 补 `created_at`；`get_active` 增强返回 active 版本行）
- `deploy/sql/migrations/v2.5.5_kw_004_nav_rbac.sql`（**新建**：`LEFT_NAV_MENU` 权限种子 `/knowledgeGraph`）
- `test/backend/services/knowevo/test_knowledge_graph_app.py`（追加 active 端点测试）
- `test/backend/services/knowevo/test_ontology_service.py`（追加 `get_active` 增强测试）

**待接线项**: 无（本任务即接线任务）
**禁改清单**: `backend/consts/const.py`（零新增 env）、`backend/apps/app_factory.py`、`deploy/sql/migrations/v2.5.5_kw_001~003*.sql`（已存在文件不可改）、上游 `frontend/components/navigation/*` 之外的导航组件、`eval_run_t`/12 张域表 schema（零 ALTER）
**允许的新依赖**: **无**（`@antv/g6@^5.1.1` 已在 lockfile 与 node_modules，本任务只把它写进 `package.json` 的 `dependencies`）

**要构建的行为**（用户视角端到端）:
1. 登录 Nexent（ADMIN 角色）后，左侧导航"资源空间"下出现「本体工作台」项，点击进入 `/knowledgeGraph`，页面正常渲染本体树；
2. 前端 `ontologyService.activeVersion()` 调 `GET /api/knowevo/ontology/versions/active` 返回已发布版本行（无版本时 404 → 前端返回 `null`），本体树面板显示当前版本号；
3. `npm run type-check` 通过，`@antv/g6` 不再靠 node_modules 隐式存在（`package.json` 显式声明）。

**背景（现状核验，2026-09-18）**:
- `SideNavigation.tsx` 的 `ROUTE_CONFIG`（L57 起）**无** `/knowledgeGraph` 项；菜单按 `accessibleRoutes`（RBAC `VISIBILITY.LEFT_NAV_MENU`）过滤 → 光加菜单项不够，**必须同时种 RBAC 行**（`deploy/sql/migrations/v2.5.5_kw_003_knowledge_graph_rbac.sql` 只种了 `RESOURCE.KNOWLEDGE_GRAPH.MANAGE`，未种 `VISIBILITY.LEFT_NAV_MENU`）。
- `knowledge_graph_app.py` 现有 5 条路由（`/ontology/proposals`、`/ontology/proposals/review`、`/ontology/versions`、`/ontology/versions/{version}/metrics`、`/ontology/diff`），**无** `GET /ontology/versions/active`。
- 前端 `frontend/services/knowledgeGraphService.ts:93` 已经在调 `GET ${BASE}/versions/active`（404 → null）——**客户端接缝已存在，后端路由缺失**。
- `ontology_service.py:627 get_active(tenant_id)` 已存在，返回 `store.load_active_snapshot()`（**只返回 snapshot dict，无 version/created_at**）；`PgStore.load_active_snapshot`（L199）查 `status="published"` 最新一行。需增强为返回**版本行**（version/status/snapshot/applied_ops/metrics/created_at），匹配前端 `OntologyVersionRow` 类型（`frontend/types/knowledgeGraph.ts:46`）。
- `frontend/package.json` 无 `@antv/g6`，但 `package-lock.json` 有 `^5.1.1` 且 `node_modules/@antv/g6` 存在，`OntologyTreePanel.tsx:10` 已 import。

**验收命令**:
```bash
# 1. 后端单测（active 端点 + service 增强）
cd backend && uv run pytest ../test/backend/services/knowevo/test_knowledge_graph_app.py ../test/backend/services/knowevo/test_ontology_service.py -q --no-header
# 2. ruff
cd backend && uv run ruff check apps/knowledge_graph_app.py services/knowevo/ontology_service.py
# 3. 前端类型检查（本机 15G 内存，check-all 的 build 阶段可能假死 → 降级为 tsc + 范围 lint，见 pitfalls #22）
cd frontend && npm run type-check
# 4. PG 集成（active 端点真库 404→200 路径）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<见 deploy/env/.env> RUN_POSTGRES_INTEGRATION=1 \
  uv run pytest ../test/backend/services/knowevo/ -q --no-header
# 5. 导航可见性（真库查 RBAC 种子）
docker exec -e PGPASSWORD=<pw> nexent-postgresql psql -U root -d nexent -tAc \
  "select user_role, permission_type, permission_subtype from nexent.role_permission_t where permission_subtype='/knowledgeGraph'"
```

**验收标准**:
- [ ] `GET /api/knowevo/ontology/versions/active`：无 published 版本 → 404；有 → 返回 `{version, status, snapshot, applied_ops, metrics, created_at}`（字段对齐前端 `OntologyVersionRow`）
- [ ] 端点走 `_require_workbench_context`（RBAC 复用，不新开权限路径）；无授权 → 403
- [ ] `OntologyService.get_active` 增强后**向后兼容**（既有调用点 `ontology_service.py:560/714` 仍工作）——新增 `get_active_row` 或让 `get_active` 返回版本行并同步改调用点（二选一，写明）
- [ ] `list_versions` 补 `created_at` 字段（修 T-18b 依赖的 fallback 路径：`resolve_version_clock` 的 `version_created_at` 分支）
- [ ] `ROUTE_CONFIG` 新增 `/knowledgeGraph`（parentKey `/resource-space`，Icon `Network`/`GitBranch` 之一），zh/en 两个 locale 均加 `sidebar.knowledgeGraph` 键
- [ ] `v2.5.5_kw_004_nav_rbac.sql`：为 SU/ADMIN 种 `VISIBILITY.LEFT_NAV_MENU /knowledgeGraph`，`ON CONFLICT` 幂等
- [ ] `package.json` 显式声明 `"@antv/g6": "^5.1.1"`；`npm run type-check` 通过
- [ ] 无新增 env、无 ALTER 既有表、未改既有迁移文件

**Evidence**: <完成后粘贴 pytest 输出关键行 / type-check 输出 / psql 查询结果>

---

## 实现备注（给实现 agent）

1. **导航权限是双闸门**：`SideNavigation` 只渲染 `accessibleRoutes` 里的 path。加 `ROUTE_CONFIG` 项 + 加 RBAC 种子，缺一不可。真库验证必须查到两行（SU、ADMIN）。
2. **active 端点语义**：`PgStore.load_active_snapshot` 已按 `created_at desc` 取最新 published。新端点直接复用它取行，但需同时返回 `version` 等元数据 → 在 `PgStore` 加 `load_active_version_row(tenant_id)`（**加性**，不动 `load_active_snapshot`），`OntologyService.get_active_row` 调它。
3. **404 而非空对象**：前端契约是 `if (res.status === 404) return null`。无 published 版本必须 404，不能返回 `{"version": null}`。
4. 迁移文件命名遵循 `v2.5.5_kw_0NN_描述.sql`，**只增不改**。
