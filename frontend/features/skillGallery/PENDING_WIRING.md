# PENDING WIRING — L10 skillGallery（登记待接线，本文件不改共享注册）

> 纪律：只新建 `frontend/features/skillGallery/**` 独占文件。
> **不修改**既有 `skillTemplate/SkillTemplatePanel.tsx`（表格页保留）。
> 共享路由 / 菜单 / RBAC **只登记、不修改**。

## 待接线项（T-08 / 接线工单）

| # | 共享文件 | 要做的事 | 状态 |
|---|---|---|---|
| W1 | `frontend/app/[locale]/skillGallery/page.tsx` | `import SkillGalleryPage from "@/features/skillGallery/SkillGalleryPage"` | **✅ 已建（γ）** |
| W2 | `frontend/components/navigation/SideNavigation.tsx` `ROUTE_CONFIG` | 增加 `/skillGallery`（Icon=LayoutGrid, order=17） | **✅ 已改（γ）** |
| W3 | 权限种子 SQL | `VISIBILITY.LEFT_NAV_MENU /skillGallery`（可并入 kw_007 风格新迁移） | **✅ kw_013 落盘；库已含同值（β/#175）** |
| W4 | `frontend/public/locales/{zh,en}/common.json` | 可选 `skillGallery.*` / `sidebar.skillGallery`（组件已带 `defaultValue`） | **✅ sidebar.skillGallery 已加（δ）** |
| W5 | `backend/apps/knowledge_graph_app.py` | `POST /api/knowevo/skill-template/apply`（body 字段 **name**） | **✅ 已接（α）** |

## 本包已用、无需再接

- `GET /api/knowevo/skill-template/list`（经 `skillTemplateService.listTemplates`，已接线）
- 本地确定性渲染 `renderTemplate.ts`（与后端 `render_template` 同规则，仅预览/复制）

## 一键实例化诚实口径

| 路径 | reuse_count | UI 文案 |
|---|---|---|
| POST apply 成功 | +1（服务端） | 「已实例化（服务端已记复用）」 |
| 路由 404 / 网络失败 → 本地渲染 | **不变** | 「已本地渲染实例（未改 reuse_count）」+ Alert 说明 MCP `skill_template_apply` |
| MCP `skill_template_apply` | +1（服务端） | 不经本页（聊天/工具面） |

## 红线自检

- 未改 `skillTemplate/**`、`app/[locale]/**`、`SideNavigation`、权限 SQL、locale JSON、`knowledge_graph_app.py`
- 未 commit / 未 push
- 未改台账
- 失败路径不假装服务端 apply 成功

## 本地验收（独占面，接线前已可跑）

```bash
cd nexent/frontend
node --test features/skillGallery/*.test.ts
./node_modules/.bin/tsc --noEmit
```

纯函数测试：`renderTemplate.test.ts`（与后端 `render_template` 对拍：已知键替换 / 空值不替换 / `{{domain}}` 内层匹配 / `template_name` 特例）。
UI：卡片一键实例化、`/` 聚焦过滤、结果区 via 徽标（服务端 / 本地预览）。

## 失败态分型（对齐工单 W10 / 红线 #6，2026-09-30 精做）

| 条件 | via | reason | reuse_count |
|---|---|---|---|
| HTTP 200 且 `skill_md` 为非空字符串 | `server` | — | 仅透传服务端有限数 |
| 200 但无 `skill_md` | `client_preview` | `server` | **不写** |
| 404/405 | `client_preview` | `route_pending` | **不写** |
| 403 | `client_preview` | `forbidden` | **不写** |
| 其他 4xx | `client_preview` | `template_missing` | **不写** |
| 5xx | `client_preview` | `server` | **不写** |
| 网络异常 | `client_preview` | `network` | **不写** |

分类纯函数：`errorClass.ts`（`classifyApplyFallback` / `classifyApplyStatus`），测试 `errorClass.test.ts`。
UI 按 `reason` 切 Alert 文案；**禁止**在错误路径显示「服务端已记复用」。

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
