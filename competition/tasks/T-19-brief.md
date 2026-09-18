# T-19：决策卡生产入口 + 对话/面板渲染

**状态**: ★ 待开发（2026-09-18 调度会话）
**Blocked by**: T-18a（导航接线模式复用）
**独占文件**（本任务创建/修改）:
- `backend/apps/knowledge_graph_app.py`（**自包含**：新增决策卡 HTTP 路由）
- `mcp_servers/knowevo_mcp/server.py` + `schemas.py`（**自包含**：新增 `decision_card_render` MCP 工具，双注册走既有 mount）
- `backend/tool_collection/mcp/kg_tools.py`（加性：工具清单纳入新工具）
- `frontend/features/decisionCard/`（**新建**：`DecisionCardPanel.tsx` + `components/EvidenceChain.tsx`）
- `frontend/app/[locale]/decisionCard/page.tsx`（**新建**：路由页）
- `frontend/services/decisionCardService.ts`（**新建**：HTTP 客户端）
- `frontend/types/decisionCard.ts`（**新建**：类型）
- `frontend/components/navigation/SideNavigation.tsx` + 两个 locale（**接线**：决策卡导航项）
- `deploy/sql/migrations/v2.5.5_kw_006_decision_card_rbac.sql`（**新建**：导航 RBAC 种子）
- `frontend/app/[locale]/newchat/assistant-ui/thread.tsx`（**接线·最小加性 diff**：新增一个 `case` 渲染决策卡 data part）
- `test/backend/services/knowevo/test_decision_card_app.py`（**新建**）
- `competition/docs/pitfalls.md` / `cost-ledger.md`

**待接线项**: 无（本任务即接线任务；thread.tsx 的加性分支是本任务唯一上游文件触碰，diff 必须 ≤30 行且纯新增）
**禁改清单**: `backend/consts/const.py`、`apps/app_factory.py`、`config_app.py`、`runtime_app.py`、既有迁移、`decision_service.py` 核心算法（只调用不修改）、`pipeline/*`（T-18 独占）
**允许的新依赖**: **无**

**要构建的行为**（用户视角端到端）:
1. **生产入口**：Agent 在对话中可调用 MCP 工具 `decision_card_render(question, ontology_version?, mode?)`，得到结构化决策卡 JSON（候选/证据链/置信度/风险/反事实/知识版本戳/版本钉住路径/冲突裁决/免责声明）；
2. **HTTP 入口**：`POST /api/knowevo/decision/card` 返回同一卡片（供前端面板与评测复用），走 RBAC 与租户隔离；
3. **前端面板**：`/decisionCard` 页面可输入问题 → 出卡 → 证据链可展开、`EXTRACTED/INFERRED` 标签可见、版本戳与 `version_pinned` 标志可见、冲突裁决区可见、免责声明常驻；
4. **对话渲染**：Agent 回答里带决策卡时，聊天流中以卡片形式渲染（复用既有 `data` part 机制），可点开证据链；
5. **落库**：生成即写 `decision_card_t`（复用 `DecisionService.persist`），`knowledge_stamp` 含 `ontology_version` + `kg_cutoff`。

**背景（现状核验，2026-09-18）**:
- `DecisionService`（decision_service.py:232）有完整 `route/multi_hop/assemble_evidence/render_card/validate_card/persist/rerun_marked`，但**无任何 HTTP 路由**（`grep -rn decision backend/apps` 零命中），**唯一消费者是 MCP**，且 MCP 只注册了 `kg_search/kg_stats/kg_multi_hop`（server.py:234/241/247）——**`render_card`/`persist` 无生产调用方**（仅测试）。
- `frontend/features/` 下**无** `decisionCard/`（grep 零命中）。
- 聊天自定义卡片机制：`thread.tsx` 的 `MessagePrimitive.GroupedParts` switch 里 `case "data"` 按 `(part as ...).name === "<name>"` 分发（如 `history-summary`/`execution-code`/`nl2skill-file`/`automation-proposal`，:1526-1580），末尾兜底 `part.dataRendererUI`。**这是唯一已存在的、上游提供的卡片扩展点**。
- `nl2a.content.subtype` 分支（:1591-1606）是另一条（NL2Agent 卡片）路径，**不适合决策卡**（那是 agent 生成前的推荐卡；NL2Agent 为代码层观察、非官方文档功能，进对外材料前须按红线报告禁写 #8 补核验）。
- 导航过滤见 T-18a：`ROUTE_CONFIG` + RBAC `LEFT_NAV_MENU` 双闸门。

**验收命令**:
```bash
# 1. 后端单测（HTTP 路由 + MCP 工具 schema）
cd backend && uv run pytest ../test/backend/services/knowevo/test_decision_card_app.py ../test/backend/services/knowevo/test_kg_multi_hop_mcp.py -q --no-header
# 2. ruff
cd backend && uv run ruff check apps/knowledge_graph_app.py services/knowevo/decision_service.py
# 3. 前端类型检查
cd frontend && npm run type-check
# 4. MCP 工具真跑（本地进程内调用，返回卡片 JSON）
cd backend && uv run python -c "
import asyncio
from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
from mcp_servers.knowevo_mcp.server import decision_card_render
print(asyncio.run(decision_card_render(DecisionCardInput(question='eGFR 45 的 2 型糖尿病患者如何选药?'))))
"
# 5. HTTP 冒烟（栈起来后）
curl -s -X POST localhost:3000/api/knowevo/decision/card -H 'Authorization: Bearer <t>' \
  -H 'Content-Type: application/json' -d '{"question":"..."}' | head -c 400
# 6. PG 集成全量
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> RUN_POSTGRES_INTEGRATION=1 \
  uv run pytest ../test/backend/services/knowevo/ -q --no-header
```

**验收标准**:
- [ ] MCP 工具 `decision_card_render` 注册（双注册：standalone `server.py` + `kg_tools.handlers()/tool_schemas()`），schema 单一来源在 `schemas.py`
- [ ] HTTP `POST /api/knowevo/decision/card` 走 `_require_workbench_context`；无授权 403；租户来自 session 不来自 body
- [ ] 卡片字段完整：`candidates[].evidence_chain[]` 带 `provenance{version_pinned, doc, span, kg_path}`、`risks`、`counterfactual`、`decision`、`knowledge_stamp{ontology_version, kg_cutoff}`、`conflict_adjudications`、免责声明
- [ ] 无证据时返回 `INSUFFICIENT_EVIDENCE` 且**不调 LLM**（T-09 既有诚实降级契约，本任务不得回退）
- [ ] `decision_card_t` 落库（`persist` 被真实调用，非测试）
- [ ] 前端 `/decisionCard` 页面：输入问题出卡；证据链可展开；`EXTRACTED/INFERRED`、版本戳、`version_pinned`、冲突区、免责声明**全部可见**
- [ ] 导航：`ROUTE_CONFIG` + `kw_006` RBAC 种子（SU/ADMIN）双闸门；真库查询能查到两行
- [ ] 聊天渲染：`thread.tsx` 新增分支渲染决策卡 data part；**diff ≤30 行且纯新增**（不重排/不重构既有分支）
- [ ] `npm run type-check` 通过；零新依赖；零新增 env
- [ ] 42crunch API 安全检查清单过一遍（新端点：输入校验、错误不泄内部、鉴权）

**Evidence**: <粘贴 pytest 输出 / MCP 真跑卡片 JSON 片段 / 前端截图路径 / psql RBAC 查询 / thread.tsx diff 行数>

---

## 实现备注（给实现 agent）

1. **卡片 JSON 契约**以 02-技术方案 §3.3 的 schema 为准（含 `version_pinned` 与 `conflict_adjudications` 两个新增字段）。`render_card` 已实现，本任务**只做暴露**（HTTP/MCP）+ 渲染，不改算法。
2. **聊天渲染的取舍**：`thread.tsx` 是上游文件。**优先方案**：Agent 工具返回的卡片作为 `data` part（name=`decision-card`）下发，thread.tsx 加一个 `case`。**若该 data part 无法从后端自然产生**（需改 adapter），则降级为：决策卡以 markdown 表格渲染在回答里（零上游改动），面板走独立页面。**在简报 Evidence 里写明实际走了哪条**。
3. **免责声明**：医疗场景，所有卡必须带"本系统提供认知辅助，不构成处方建议"（02 §3.3 医疗边界）。
4. **不要发明新 env**：`KW_LLM_*` 已存在（const.py 冻结），决策卡 LLM 走既有三档链。
