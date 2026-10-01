# PENDING WIRING — L10 evolutionBoard（登记待接线，本文件不改共享注册）

> 纪律：只新建 `frontend/features/evolutionBoard/**` 独占文件。
> 共享路由 / 菜单 / RBAC **只登记、不修改**。

## 待接线项（T-08 / 接线工单）

| # | 共享文件 | 要做的事 | 状态 |
|---|---|---|---|
| W1 | `frontend/app/[locale]/evolutionBoard/page.tsx` | 仿 `skillTemplate/page.tsx`：`import EvolutionBoardPage from "@/features/evolutionBoard/EvolutionBoardPage"` | **✅ 已建（γ）** |
| W2 | `frontend/components/navigation/SideNavigation.tsx` `ROUTE_CONFIG` | 增加 `{ path: "/evolutionBoard", labelKey: "sidebar.evolutionBoard", Icon: History, order: 16 }` | **✅ 已改（γ）** |
| W3 | `frontend/hooks/auth/useAuthorization.ts` / 权限种子 SQL | `VISIBILITY.LEFT_NAV_MENU /evolutionBoard`（可仿 `v2.5.5_kw_007_skill_template_rbac.sql`） | **✅ kw_013 落盘；库已含同值（β/#175）** |
| W4 | `frontend/public/locales/{zh,en}/common.json` | 可选：`evolutionBoard.*` / `sidebar.evolutionBoard` 键（组件已带 `defaultValue`，缺键可跑） | **✅ sidebar.evolutionBoard 已加（δ）** |
| W5 | `backend/apps/knowledge_graph_app.py` | `GET /api/knowevo/evolution/timeline` + `GET .../rounds/{id}`（**含租户闸 403**） | **✅ 已接（α）** |

## 本包已用、无需再接的数据源（已接线）

- `GET /api/knowevo/alignment/diff/list` — 时间轴文档级事件
- `GET /api/knowevo/ontology/diff?from&to` — 三色 diff / 节点级对比
- `GET /api/knowevo/ontology/versions/{v}/metrics`（service 预留）

## 红线自检

- 未改 `app/[locale]/**`、`SideNavigation`、权限 SQL、locale JSON、`knowledge_graph_app.py`
- 未 commit / 未 push
- 未改台账（任务总账 / pitfalls / evidence-index / 00-索引）
- 轮次台账无路由时 **不编造数据**（Alert=pending-wiring）

## 本地验收（独占面，接线前已可跑）

```bash
cd nexent/frontend
node --test features/evolutionBoard/*.test.ts
./node_modules/.bin/tsc --noEmit
```

纯函数测试：`diffColor.test.ts`（三色语义）/ `opsSummary.test.ts`（保留键 `_` 前缀不入 op 计数、null 不造 0）。
UI：文档 diff 选中 → `AlignmentDiffDetail`；轮次选中 → `RoundDetailPanel`（含 ops 计数）；时间轴可按 来源过滤。

## 失败态分型（对齐工单 D6 / L10-R1，2026-09-30 精做）

| HTTP | UI | 文案口径 |
|---|---|---|
| 404/405（计划路由） | Alert=`pending_wiring` | 「待接线」，**不是**空列表 |
| 200 + `rounds: []` | Empty | 「暂无演进事件」 |
| 403 | Alert=`forbidden` | 「无权读取」（W5 跨租户闸），**不是**空 |
| 5xx / 网络 | Alert=error + 重试 | 「加载失败」，不编造轮次 |

分类纯函数：`errorClass.ts`（`classifyLoadFailure`），测试 `errorClass.test.ts`。
`listRounds` / `roundDetail` 返回 `{ok:true|false, kind}` 联合类型，UI 按 kind 分支。

> **2026-09-30 接线收口**：α/β/γ/δ 已实施（见 `l10-pending-wiring-workorder` 与 `任务总账`）。本文件保留为契约原件，状态列已更新。

## 接线后 404 语义（收线校正，2026-09-30）

接线完成后 404 **不再**表示「路由未接」：

| 面 | 404 含义 | UI kind / reason |
|---|---|---|
| `GET /evolution/timeline`（列表） | 列表接口不应 404；若 404 仍视为 pending_wiring | `pending_wiring` |
| `GET /evolution/rounds/{id}` | round_id 不在台账 | `not_found` |
| `POST /skill-template/apply` | 模板不存在（W10 KeyError→404） | `template_missing` |
| 405 | 方法/路由仍不可用 | `route_pending` |
| 403 | 跨租户 / RBAC | `forbidden` |

分类函数已按 context 区分；测试锁定。
