# T-05：本体工作台（确认环 API + 前端）
**Blocked by**: T-04（已交付并合并 develop ddbd5b4：ontology_service 753 行，提案/排序/版本化/K0 全就位）
**拆分裁决**（03 计划 §3.4 上下文预算：新文件 ≤8、新码 ≤1200 行）：本简报含 **T-05a（API+服务端确认环）** 与 **T-05b（前端工作台）** 两段；若一个会话做不完，先交付 a 再开 b 会话，分支同名 feat/kw-T05-ontology-workbench。
**独占文件**:
- a：`backend/apps/knowledge_graph_app.py`（新建，接口冻结于框架包同名 .md）、`backend/services/knowevo/ontology_service.py` 的**新增方法节**（review/reparent，追加不改动既有方法——T-04 文件归属转为 T-05 持有，需在简报回执里注明）、`test/backend/services/knowevo/test_knowledge_graph_app.py`
- b：`frontend/features/knowledgeGraph/`（SPEC.md 已冻结：提案队列页/本体树页 G6/diff 视图/质量面板）
**待接线项**: `config_app.py` 的 `include_router(knowevo_router)` 一行 + G6 依赖 `@antv/g6@5`（npm，03 计划 §4.1 白名单）——**登记给 T-08 接线任务，本任务不碰**；本地开发期可在分支内临时接线验证，合并前还原
**禁改清单**: 上游共享文件（apps/app_factory.py、config_app.py、runtime_app.py、consts/const.py、pyproject.toml）；T-04 已冻结接口（extract_seed/propose_concepts/autofix/rank/auto_accept/commit_version/diff/get_active/quality_metrics 签名禁改，只追加）
**允许的新依赖**: `@antv/g6@5`（npm，MIT，白名单内）；后端零新依赖
**本任务细节**（备忘录 02-K1 §3 + tasks/README T-05 行 + 框架包 SPEC）:
- **确认流信息最小化**：每条提案只展示 [提案名+证据锚点原文+影响面摘要+置信度]，确认动线 ≤3 击（看证据→决策→下一题）
- **键盘流**：A 确认 / X 否决 / P 改父类，支持 40 条/会话批量节奏（K1 §3 切批）
- API 端点（框架包冻结，5 个管理面）：
  - `GET /api/knowevo/ontology/proposals` —— 待审队列分页+排序特征（tenant_id 从会话注入，服务层签名不变）
  - `POST /api/knowevo/ontology/proposals/{id}/review` —— confirm/reject/reparent 三动作
  - `POST /api/knowevo/ontology/versions` —— commit_version
  - `GET /api/knowevo/ontology/versions/{v}/metrics` —— K0 四指标
  - `GET /api/knowevo/ontology/diff` —— diff(from,to)
- 服务端新增：`review_proposals(tenant_id, ids, action, new_parent?)`（批量审）——业务逻辑全在 services，apps 层零业务（上游分层铁律）
- **T-03 缺口吸收**：T-03 未交付 knowledge_graph_app.py 骨架（当时只交了 ORM/迁移/常量），本任务新建该文件即履约
- 分层铁律：apps 只做解析/鉴权/路由注册——复用 Nexent 多租户 RBAC 中间件，tenant_id 由 apps 注入
- 鉴权冒烟：每个端点 happy path + 401/403（框架包验收锚点）
- 前端（b 段）：提案队列页（键盘流+证据悬浮预览不打断队列）、本体树页（G6 树布局，类节点带 anchor 标记）、diff 视图（两版本 oplog 回放：added 绿/deprecated 灰/changed 黄）、质量面板（K0 四指标雷达图——recharts 已在依赖树则用，否则推迟到 T-12 并注明）
- 路由：页面挂 `/knowledgeGraph` 下，正式导航注册归 T-12（本任务用直接 URL 可达即验收）
**要构建的行为**（用户视角端到端）:
浏览器打开本体工作台 → 提案队列逐条出现（带证据原文、置信度、排序分）→ 按 A/X/P 键盘流审完一批 → 点"提交版本" → G6 树更新渲染 + 版本 diff 视图可看两个版本的增删改 → K0 质量指标四数值可见。全程同一租户数据隔离（另一租户看不到这批提案）。
**验收命令**:
```bash
# a 段（API）：
cd backend && uv run pytest ../test/backend/services/knowevo/test_knowledge_graph_app.py -v
# 分支内临时接线后路由冒烟（合并前还原接线）：
curl -s localhost:5010/api/knowevo/ontology/proposals -H "Authorization: ..." | jq '.items | length'
# b 段（前端）：
cd frontend && npm run check-all
# 端到端：浏览器 http://localhost:3000/knowledgeGraph 截图（提案队列/G6 树/diff）
```
**验收标准**:
- [ ] 5 个端点全通：happy path + 401/403 + OpenAPI schema 无冲突（/api/knowevo 命名空间）
- [ ] review_proposals 三动作（confirm/reject/reparent）+ reparent 触发 V1 环检测拒绝成环操作
- [ ] 队列分页 40/会话（排序特征返回：score/conf/novelty/impact/ev_rich）
- [ ] 确认后 commit_version 得新 semver 版本；diff 往返一致（T-04 已有 diff 逻辑复用）
- [ ] 提案队列页键盘流 A/X/P 可用，证据悬浮预览不打断
- [ ] G6 本体树渲染 ≥30 节点（吃 T-04 fixture 提案量）
- [ ] diff 视图三色（绿/灰/黄）操作回放
- [ ] `npm run check-all` 全过；ruff 全过
- [ ] 新踩坑记 pitfalls.md；截图存 competition/deliverables/
**Evidence**: <pytest 输出/curl 响应/浏览器截图——没有证据=没做完>

## 反幻觉条款（发任务时必附）
开工先读仓库根 AGENTS.md；框架包 knowledge_graph_app.py.md 与 frontend/features/SPEC.md 是契约；T-04 的 ontology_service.py 已冻结方法签名禁改（只允许追加新方法，且回执里列出追加清单）；G6 用 v5 API（@antv/g6@5，勿用 v4 老文档）；不得发明环境变量；接线项（include_router/G6 install）登记 T-08，分支内临时验证后合并前还原。
