# THIRD_PARTY_NOTICE

> 依赖登记台账：后续任务（T-02 起）逐条登记引入的第三方依赖。格式与登记纪律见 03-代码对接计划书 §4.1 依赖白名单矩阵。
> 上游 Nexent 自带依赖不在本台账重复登记（以 `backend/pyproject.toml`、`frontend/package.json`、`docker/` 镜像为准）。

## 依赖登记表

| # | 引入任务 | 依赖名 | 版本 | 用途 | 许可证 | 引入方式(pip/npm/docker/manual) | 备注 |
|---|---------|--------|------|------|--------|-------------------------------|------|
| 1 | T-07b | fastmcp | >=2.14.2,<3.0 | KnowEvo MCP 工具服务（kg_search/kg_stats 双注册表面） | Apache-2.0 | pip（上游 pyproject 已含，本任务启用） | 上游自带依赖；独立 FastMCP 服务 + Local MCP 内嵌注册，单一 schema 源 |
| 2 | T-07b | mcp（官方 SDK） | >=1.24.0,<1.30 | MCP 协议层（本地与远端工具调用） | MIT | pip（上游 sdk/pyproject 已含） | 上游自带；FastMCP 依赖链，mcp list-tools 冒烟在 T-08 接线后跑 |
| 3 | T-09 | instructor | — | **实测否决，未引入**：03 计划 §4.1 曾列其为决策卡结构化输出方案，实测 `uv pip install instructor` 会把上游核心依赖 `openai` 从 3.13.0 降到 3.3.0、`rich` 15→14（污染上游依赖链，风险不可接受） | MIT | 无 | **改判**：决策卡改用仓库既有的「YAML 双语 prompt + JSON 解析 + Pydantic 校验」路径（与 `kg_service._parse_extraction` 同款），零新依赖。已本地安装验证并回滚，pyproject 零改动 |
| 4 | T-09 | networkx | 3.6.1 | 仅作为多跳路径排序的备选实现 | BSD-3 | 上游已含（未在本任务 import） | 首版路径打分用词面重叠（延迟预算内、可解释），networkx/PPR 留作备选；本任务未实际调用，故不引入 |
| 5 | L10/knowledgeGraph | @antv/g6 | ^5.1.1 | 本体树/图可视化（`frontend/features/knowledgeGraph/components/OntologyTreePanel.tsx` 真 import） | MIT | npm（上游 `frontend/package.json` 已含） | 前端真用；开源前补登记（opensource-readiness §1.4）。上游自带依赖，本行补「已消费」事实 |
| 6 | evaluation 页 | recharts | ^2.15.0 | 评测图表（`app/[locale]/evaluation/[id]/page.tsx`、`DiffAndQualityPanel.tsx` 真 import） | MIT | npm（上游 package.json 已含） | 同上，前端真用 |
| 7 | Agent 调用关系图 | react-d3-tree | ^3.6.6 | 树/层级图（`frontend/components/agent/AgentCallRelationshipModal.tsx` 真 import） | MIT | npm（上游 package.json 已含） | 2026-09-30 补登（supplement-draft §1）；`@antv/*` 传递面 lockfile 共 **16** 包：**15 个 MIT + `@antv/vendor` 为 `MIT AND ISC`（非纯 MIT）**；已点名 `g`/`g-canvas`/`graphlib`/`layout`，其余传递依赖按登记纪律 §3 不逐条展开 |


## Fork 改作归属声明（开源前必读）

本仓库为 **ModelEngine-Group/Nexent** 的 fork 二开（竞赛改作 **KnowEvo / Nexent-KnowEvo**）。
上游根目录 `LICENSE` 为 **MIT**（Copyright (c) 2025 Huawei Technologies），`NOTICE` 登记 Unstructured.io / SmolAgents / Ray。
**KnowEvo 竞赛改作部分**（`backend/services/knowevo/**`、`mcp_servers/knowevo_mcp/**`、`competition/**`、迁移 `v2.5.5_kw_*` 等）由参赛团队编写，随本 fork 以相同 MIT 条款发布；对外文案须同时保留上游版权与 NOTICE，不得移除或改名当原创。

### 评估参照、未引入（勿当作运行时依赖）

| 对象 | 角色 | 许可 |
|---|---|---|
| goldenmatch | 评估参照（MCP 工具面 70+ 对照） | MIT |
| docling | 评估参照（PDF 解析候选，未 import） | MIT |
| graphiti | 设计参照（bi-temporal 边思想） | 见上游 |
| instructor / networkx | 曾评估/备选，**未引入**（见登记表 #3/#4） | MIT / BSD-3 |

## 登记纪律

1. **白名单优先**：引入任何依赖前对照 03 计划 §4.1 矩阵；白名单外依赖需先在简报中申请并说明理由。
2. **一条一行**：每个依赖一行，含版本与许可证，便于 T-16 参赛交付物装配时汇总。
3. **禁止静默引入**：pip/npm 隐式装上的传递依赖不逐条登记，但顶层新增必须在提交信息中提及。
4. **许可证审查**：引入前确认许可证与参赛要求兼容（MIT/Apache-2.0/BSD 优先，GPL 需报告）。
