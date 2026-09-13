# KnowEvo —— Nexent 二开包「领域资产认知智能体」（框架阶段）

> **本目录是按 [04-算法与架构决策备忘录](../04-算法与架构决策备忘录/00-README-执行摘要与决策总表.md) 冻结的项目框架**：目录结构与真实 fork 内布局一致（对齐 [03-代码对接计划书](../03-代码对接与并行开发计划书.md) §1.1），当前每个模块以 `.md` 规格书形式存在，供后续编码任务（T 系列）直接对接。**本阶段不写实现代码**——每个 .md 含：模块职责、对外接口（函数签名级）、数据契约、伪代码、验收锚点。
>
> 后续开发时：把本包内容**拷入 fork 的 nexent/ 仓库对应位置**，同名 .md 保留为模块文档（Nexent 上游要求注释仅英文，文档无此限制），`.py`/`.ts` 按规格书创建。

## 目录导览（对应六层架构）

```
knowevo/
├── README.md                        ← 本文件
├── CONTEXT.md                        ← 领域术语表（全队统一语言）
├── docs/adr/                         ← 架构决策记录（已做裁决的留档）
├── backend/
│   ├── apps/                         ← L1-L4 HTTP 边界（Nexent 分层铁律：apps 只做解析/鉴权）
│   │   ├── knowledge_graph_app.py.md
│   │   └── asset_app.py.md
│   ├── services/knowevo/             ← 全部业务逻辑（06 层核心）
│   │   ├── ontology_service.py.md    （K1 本体流水线）
│   │   ├── kg_service.py.md          （K2 图谱+GraphStore 抽象）
│   │   ├── graph_store.py.md         （A1 存储适配器）
│   │   ├── alignment_service.py.md   （K2 对齐 + K5 变更检测/受影响面）
│   │   ├── evolution_service.py.md   （K5 进化轮编排/版本台账）
│   │   ├── decision_service.py.md   （K3 双驱动编排/证据链/决策卡）
│   │   ├── skill_template_service.py.md（K6 模板归纳）
│   │   └── pipeline/                 ← 离线批处理（python -m 可跑）
│   │       ├── build_ontology.py.md
│   │       ├── ingest_graph.py.md
│   │       ├── detect_doc_change.py.md
│   │       └── gen_synthetic_graph.py.md
│   ├── database/knowevo_models.py.md ← 12 表 ORM 说明（DDL 见备忘录 10 分册）
│   └── prompts/                      ← 双语 prompt 模板清单（knowevo_*.yaml 对）
├── mcp_servers/knowevo_mcp/          ← A2：8 个自研 MCP 工具（FastMCP v4 独立服务）
│   └── SPEC.md
├── frontend/features/                ← L5 三面板（knowledgeGraph/decisionCard/assetDashboard）
│   └── SPEC.md
├── skills/                           ← K6 SKILL.md 模板库（入口/检索路/推理路/组装 四模板）
├── eval/                             ← K4 评测协议与测试集骨架
│   ├── testset-template.json
│   └── judge-rubric.md
├── competition/                      ← 参赛专用
│   ├── tasks/                        ← T-00..T-17 任务简报（03 计划 §7 模板实例化）
│   └── docs/                         ← 三台账模板（pitfalls/evolution-log/cost-ledger）
└── deploy/
    └── sql/v2.5.x_kw_001_knowevo_core.sql.md   ← 迁移文件说明（DDL 源=备忘录 10）
```

## 与计划文档的对应关系

| 层 | 承载 | 规格书 | 依据分册 |
|---|---|---|---|
| L1 资产层 | doc_asset_t 登记/血缘/解析体检 | asset_app / pipeline | 10（DDL-7）|
| L2 知识引擎 | 本体流水线+图谱构建 | ontology_service / kg_service / pipeline | 01/02/03 分册 |
| L3 双驱动 | 路由+多跳+决策卡 | decision_service + skills/ | 04 分册 |
| L4 进化与沉淀 | 变更检测/进化轮/模板 | alignment_service / evolution_service / skill_template_service | 06/07 分册 |
| L5 看板 | 三面板 | frontend/SPEC | 各分册"进 PPT"节 |
| 横切 | 评测/成本/存储 | eval/ + K8 + GraphStore | 05/08/09 分册 |

## 硬规则（每个编码任务开工前重读）

1. 分层：apps 解析鉴权 → services 业务 → database ORM；注释仅英文；行宽 119（ruff）；prompt 双语成对。
2. 只动自包含新文件；接线（router/常量/依赖注册）统一走 T-03/T-08 接线任务。
3. 每个 T 任务的验收命令 + Evidence 节必须真实跑过（03 计划 §5）。
4. 接口以本包 .md 的"接口冻结"节为准——改动接口须先改 .md 并在 ADR 记录（接口即契约）。
