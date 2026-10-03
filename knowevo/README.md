# KnowEvo —— Nexent 二开包「领域资产认知智能体」（契约包）

> **本目录是 KnowEvo 的冻结契约包（依据 [架构决策记录](docs/adr/0001-0008-已冻结架构决策.md)）**：目录结构与仓库内布局一致，每个模块以 `.md` 规格书形式存在。每个 .md 含：模块职责、对外接口（函数签名级）、数据契约、伪代码、验收锚点；实现代码在仓库对应路径，同名 .md 保留为模块文档（Nexent 上游要求注释仅英文，文档无此限制）。

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
│   ├── services/knowevo/             ← 全部业务逻辑（06 层核心；实存 23 份契约）
│   │   ├── ontology_service.py.md    （K1 本体流水线）
│   │   ├── kg_service.py.md          （K2 图谱+GraphStore 抽象）
│   │   ├── graph_store.py.md         （A1 存储适配器）
│   │   ├── alignment_service.py.md   （K2 对齐 + K5 变更检测/受影响面）
│   │   ├── alignment_semantic.py.md  （语义对齐器）
│   │   ├── evolution_service.py.md   （K5 进化轮编排/版本台账）
│   │   ├── decision_service.py.md   （K3 双驱动编排/证据链/决策卡）
│   │   ├── skill_template_service.py.md（K6 模板归纳）
│   │   ├── doc_asset_service.py.md   （L1 资产检索/asset_search 后端）
│   │   ├── asset_raw_list.py.md / es_raw_list.py.md（PG/ES 原始清单兜底两路）
│   │   ├── es_index_writer.py.md     （ES 写路径索引）
│   │   ├── graph_retrieve.py.md      （图谱检索服务）
│   │   ├── rrf_fusion.py.md / kg_fusion.py.md / asset_fusion.py.md（三路 RRF 融合内核与接线）
│   │   ├── conflict_kernel.py.md     （A4 冲突内核：分类/裁决）
│   │   ├── conflict_adapter.py.md    （裁决→图写路径适配）
│   │   ├── update_planner.py.md      （最小充分更新集规划）
│   │   ├── community_summary.py.md / summary_store.py.md（社区摘要 + kg_summary_t 存取）
│   │   ├── conformal.py.md           （共形校准/不确定性）
│   │   ├── budget_curve.py.md        （预算-质量曲线）
│   │   └── pipeline/                 ← 离线批处理（python -m 可跑）
│   │       ├── build_ontology.py.md
│   │       ├── ingest_graph.py.md
│   │       ├── detect_doc_change.py.md
│   │       └── gen_synthetic_graph.py.md
│   ├── database/knowevo_models.py.md ← 12 表 ORM 说明（DDL 见落地迁移文件）
│   └── prompts/                      ← 双语 prompt 模板清单（knowevo_*.yaml 对）
├── mcp_servers/knowevo_mcp/          ← A2：自研 MCP 工具（FastMCP 独立服务；SPEC 冻结词表 8 个（全量已注册）+ 冻结外 skill_template_apply，对外口径 9 个：kg_search / asset_search / kg_stats / kg_multi_hop / kg_evolution_trace / ontology_diff / evidence_verify / decision_card_render / skill_template_apply）
│   └── SPEC.md
├── frontend/features/                ← L5 面板（knowledgeGraph/decisionCard/skillTemplate/agentAutomation；assetDashboard 已裁决砍除）
│   └── SPEC.md
├── skills/                           ← K6 SKILL.md 模板库（入口/检索路/推理路/组装 四模板）
├── eval/                             ← K4 评测协议与测试集骨架
│   ├── testset-template.json
│   └── judge-rubric.md
└── deploy/
    └── sql/v2.5.x_kw_001_knowevo_core.sql.md   ← 迁移文件说明（DDL 以落地迁移文件为唯一源）
```

## 与架构决策的对应关系

| 层 | 承载 | 规格书 | 相关 ADR（docs/adr/） |
|---|---|---|---|
| L1 资产层 | doc_asset_t 登记/血缘/解析体检 | asset_app / pipeline | —（设计见各契约文档）|
| L2 知识引擎 | 本体流水线+图谱构建 | ontology_service / kg_service / pipeline | ADR-0002 / 0004 / 0008 |
| L3 双驱动 | 路由+多跳+决策卡 | decision_service + skills/ | ADR-0003 |
| L4 进化与沉淀 | 变更检测/进化轮/模板 | alignment_service / evolution_service / skill_template_service | ADR-0002（增量提案回流） |
| L5 看板 | 三面板 | frontend/SPEC | —（设计见 frontend/SPEC.md） |
| 横切 | 评测/成本/存储 | eval/ + K8 + GraphStore | ADR-0005 / 0007 / 0001 |

## 硬规则（实现与修改前必读）

1. 分层：apps 解析鉴权 → services 业务 → database ORM；注释仅英文；行宽 119（ruff）；prompt 双语成对。
2. 只动自包含新文件；接线（router/常量/依赖注册）统一在共享接线文件集中修改。
3. 每项验收命令 + Evidence 必须真实跑过。
4. 接口以本包 .md 的"接口冻结"节为准——改动接口须先改 .md 并在 ADR 记录（接口即契约）。
