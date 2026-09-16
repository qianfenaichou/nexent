# T-05 回执：本体工作台（T-05a + T-05b）

> 依据 `T-05-brief.md` 验收标准逐条对账。日期：2026-09-16（b 段复核会话）。

## 文件归属声明（简报要求）

- `backend/services/knowevo/ontology_service.py`：**T-04 → T-05 持有**。T-04 冻结签名（extract_seed/propose_concepts/autofix/rank/auto_accept/commit_version/diff/get_active/quality_metrics 等）**未改动**；追加方法见下方清单。
- a 段独占文件：`backend/apps/knowledge_graph_app.py`（新建，吸收 T-03 遗留骨架缺口）、`test/backend/services/knowevo/test_knowledge_graph_app.py`。
- b 段独占文件：`frontend/features/knowledgeGraph/`（3 组件 + Page shell）、`frontend/app/[locale]/knowledgeGraph/page.tsx`、`frontend/services/knowledgeGraphService.ts`、`frontend/types/knowledgeGraph.ts`；locale 增量键（en/zh common.json 尾部 `knowledgeGraph.*`）+ 顺带修复 JSON 重复键（`chat.threadList.loadMore/loadingMore/allLoaded` 双定义，保留与 last-wins 语义一致的值，无行为变化）。

## 服务层追加清单（T-05a + 本次复核）

| 方法 | 来源 | 说明 |
|---|---|---|
| `review_proposals` | T-05a | 批量审 confirm/reject/reparent，reparent 走 V1 环检测原子拒绝 |
| `commit_from_queue` | T-05a | 队列折叠→commit_version，空 confirmed_ids=折叠整个已确认会话 |
| `active_version` | b 段复核追加 | 只读返回最新已提交版本行（append 序=提交序，与 diff 的 oplog 回放同序）；无版本时 None |

## 契约偏差记录（相对框架包 knowledge_graph_app.py.md 冻结接口）

1. **review 路由批量形**：冻结契约写 `POST /ontology/proposals/{id}/review`，实现为 `POST /ontology/proposals/review`（body 携 `ids[]`）。理由：服务层 `review_proposals(tenant_id, ids, action)` 本就是批量语义，键盘流逐条调用同一端点，40 条/会话批量节奏天然成立。前端按实现契约对接。
2. **新增只读端点 `GET /ontology/versions/active`**（本次复核追加）：冻结 5 端点无任何"读当前版本"能力，b 段曾被迫用 `POST /versions` 探测——而 `confirmed_ids=[]` 在后端语义是**折叠整个已确认会话**，即"看一眼树页签就提交一个新版本"。只读端点是修掉该副作用的最小方案；服务方法追加、零 T-04 签名变更。**两条偏差需在 T-08 接线任务或下次 ADR 升版时回填框架包 .md。**

## b 段复核修复的三处跨栈契约 bug

1. `versionMetrics` 未解包端点包裹 `{version, metrics}`——质量面板雷达图/四指标原本恒为 undefined；
2. 树面板加载时调 `POST /versions`（见上，副作用提交）——改读 `GET /versions/active` + 会话内提交行直传；
3. 树面板的版本切换 Segmented 只重取 metrics 不重渲染树（半残）——移除，当前版本以 Tag 展示，历史版本浏览归 T-12 看板。

测试侧同修：`FakeReviewStore.list_versions/get_version_row` 原不按租户过滤（diff/metrics/active 在测试里跨租户泄漏），已补 tenant 过滤对齐 PgStore 契约。

## Evidence

- 后端：`pytest ../test/backend/services/knowevo/test_knowledge_graph_app.py -v` → **17 passed, 1 skipped**（PG 集成测试按 pitfalls #14 模板门控，本会话无 env 跳过）；`ruff check` 全过。
- 前端：`tsc --noEmit` 全过；T-05 文件 `next lint` **0 错误**（上游基线自身 4404 个 prettier 风格错误遍布 `app/`、`components/`、`lib/`，与本任务无关）；prettier 对 T-05 文件已格式化。
- **未跑**：`npm run build`/完整 `check-all` ——本机 15G 驻留全栈时 build 即假死（pitfalls #22），验收降级为上述轻量等价；待 docker 栈停机窗口补跑。

## 待办（合并前清单）

- [ ] 路由冒烟：分支内临时 `include_router(knowevo_router)` → curl 5+1 端点 happy path/401/403 → **合并前还原接线**（登记 T-08）
- [ ] 浏览器 e2e：`/knowledgeGraph` 提案队列 A/X/P → 提交版本 → G6 树 ≥30 节点 → diff 三色 → 截图存 `competition/deliverables/`
- [ ] docker 栈停机窗口跑 `npm run check-all` 完整版
- [ ] 框架包 .md 回填两条契约偏差（可并入 T-08）
