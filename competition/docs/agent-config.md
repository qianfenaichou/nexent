# 智能体配置完整细节（Agent Config）· Nexent-KnowEvo

> T-23 交付物之一。本文件是初赛《Nexent 平台智能体设计思路及详细说明》中「Agent 配置完整细节」部分的素材母本。
> **所有配置均来自真实运行实例**（2026-09-20 从 `nexent` 库 `ag_tenant_agent_t` / `model_record_t` / `ag_skill_info_t` / `ag_tool_info_t` 导出），非手写编造。
> ⚠️ 安全说明：数据库中的 `api_key`/`access_token` 一律**不进入**本文件与 `deliverables/agent-config.json`（T-23 红线：不得把密钥写入交付物）。

---

## 1. Agent 主配置（真实落库值）

| 字段 | 值 | 来源 |
|---|---|---|
| agent_id | 1 | `ag_tenant_agent_t` |
| name | `knowevo_assistant` | 同上 |
| display_name | Knowevo 助手 | 同上 |
| model_name | （空，走 `KW_LLM_SMALL/MID/LARGE_MODEL_ID` 三档路由，见 §2） | 同上 |
| version_no | 1（当前版本） | 同上 |
| tenant_id | `6756b0ab-39c0-462a-9745-aa12e1511fcd`（构建租户） | 同上 |
| duty_prompt | 「你是一个简洁友好的中文助手。你的职责是准确、清晰地回答用户的问题，不编造事实。」 | `duty_prompt` 字段原文 |
| max_steps | （默认，未显式设置） | 同上 |

> 设计要点：Agent 的 prompt 只写**职责与约束**（诚实、不编造），不写领域执行细节——领域逻辑全部下沉到 Skill 分层（§4）与 MCP 工具（§3），保证 Agent 本体可随平台升级零改动。

---

## 2. 模型信息（真实落库 9 档，API key 已脱敏）

来源：`model_record_t`（tenant=`6756b0ab-…`），`connect_status=available`。

| model_id | 模型 | 工厂 | 类型 | 用途（display_name） | context_window |
|---|---|---|---|---|---|
| 1 | glm-5.3-free | OpenAI-API-Compatible | llm | 主档 | 32768 |
| 2 | glm-5.3-free | OpenAI-API-Compatible | llm | 小档 | 32768 |
| 3 | bge-m3 | silicon | embedding | bge-m3-embedding | — |
| 4 | deepseek-v4-flash | OpenAI-API-Compatible | llm | 生成/小中档 | 65536 |
| 5 | deepseek-v4.1-flash | OpenAI-API-Compatible | llm | 大档 | 65536 |
| 6 | glm-5.3-flash | OpenAI-API-Compatible | llm | judge·异家族 | 65536 |
| 7 | deepseek-v4-flash@sn | OpenAI-API-Compatible | llm | 生成·中档（sensenova 网关） | 65536 |
| 8 | glm-5.2@sn | OpenAI-API-Compatible | llm | judge·异家族（sensenova 网关） | 65536 |
| 9 | deepseek-v4-pro@sn | OpenAI-API-Compatible | llm | 大档·预留（sensenova 网关） | 65536 |

**三档路由**（`backend/services/knowevo/llm_client.py`，env 注入）：`KW_LLM_SMALL_MODEL_ID=7`、`KW_LLM_MID_MODEL_ID=7`、`KW_LLM_LARGE_MODEL_ID=8`。
- small/mid 档 = deepseek-v4-flash（sensenova 网关，快、便宜，日常抽取/检索）；
- large 档 = glm-5.2（异家族，用于降级重试与判别）；
- judge 档 = glm-5.3-flash（异家族，用于评测判定，与生成模型同源偏差解耦）。

---

## 3. 工具信息（MCP）

### 3.1 自研 KnowEvo MCP 工具（双注册，代码层）

来源：`backend/tool_collection/mcp/kg_tools.py`（`SERVICE_NAME="knowevo"`，`KG_MCP_TOOL_NAMES`），Local MCP + FastMCP SSE 双注册（官方文档 A）。

| 工具名 | 功能 | 输入（要点） | 输出 | 对应任务 |
|---|---|---|---|---|
| `kg_search` | 图谱实体/邻域检索（1 跳） | 查询词、hop、top_k | 实体+关系+证据 | T-07b |
| `kg_stats` | 图谱统计 | 统计维度 | 实体/关系/类别计数 | T-07b |
| `kg_multi_hop` | 版本钉住多跳 + 证据链 | 起点实体、跳数、版本语义 | PathSet + evidence_chain | T-09 |
| `decision_card_render` | 决策卡结构化输出 | EvidenceBundle、约束 | 决策卡 JSON（版本戳/置信度/免责） | T-19 |
| `skill_template_apply` | 模板实例化 | 领域、任务类型、关系模板、规则 | 实例化 Skill 模板（四变量） | T-20 |

### 3.2 平台侧工具（真实落库 34 个，节选与本场景相关）

来源：`ag_tool_info_t`（34 条，`is_available=t`）。与本场景直接相关的：

| 工具名 | 类别 | 用途 |
|---|---|---|
| `knowledge_base_search` | search | 平台知识库检索（ES 混合检索） |
| `aidp_search` | search | AIDP 知识库 FusionSearch 多模态检索 |
| `search_memory` / `store_memory` | search / database | 平台记忆读写（Dreaming 记忆整理实据之一；类别取 `ag_tool_info_t` 实测值） |
| `postgres_database` / `mysql_database` / `mssql_database` | database | 结构化数据查询 |
| `read_file` / `create_file` / `list_directory` 等 | file | 文件操作 |
| `exa_search` / `tavily_search` / `linkup_search` | search | 联网检索 |
| `parallel_executor` | — | 并行执行（评测吞吐支撑） |

---

## 4. 知识库信息

| 项 | 值 | 来源 |
|---|---|---|
| 语料登记 | 58 份（`corpus/registry.csv`） | T-02 溯源核查通过 |
| 语料构成 | 药品说明书 28 + 临床指南 11 + 诊疗路径 9 + 检验 2 + 科普 8（batch1-3 核查报告） | `docs/verification-reports/batch{1,2,3}-*.md` |
| 图谱资产 | `kg_graph`（实体/关系 + `kg_evidence_t` doc_id 证据链），构建租户持续摄取（120 段语料分块）。**当前快照（2026-09-21 r20 收盘，权威口径）**：摄取 **15/120 段**、**136 实体 / 65 关系（含 2024 权威关系 32 条）、38 证据行**——来源 `cost-ledger` 行 `r20-burst-c` + `deliverables/e2-ablation-report.json` 的 `data_reality.ablation_tenant_graph`（entities=136 / relations=65）。实体类别分布（Disease 15 / Population 7 / Symptom 6 / Examination 6 / Indicator 4 / Drug 1 / Treatment 1）为**更早的 40 实体期快照**，未随新增段重算，引用时须注明 | 构建租户图谱摄取管线 |

> ⚠️ **2026-09-24 口径更新（任务Q6）**：上表「r20 收盘快照」（15/120 段、136 实体/65 关系/38 证据行）**已过时**，保留备查。**真库现状实测**（构建租户 `6756b0ab`，库 = docker 容器 `nexent-postgresql`，`docker exec nexent-postgresql psql -U root -d nexent`，2026-09-24 13:40 CST）：摄取 **46/120 段**、**357 实体 / 294 关系 / 70 证据行**。完整命令与原始输出见 `00-索引与状态.md` §八 任务Q6；两套 PG 的身份区分见同文件 §四·补。
| 本体 | 10 类 / 10 关系 / `fact_cutoff=2025-01-01`（ontology v1.1.0） | OntologyService.commit_version |
| 平台记忆 | `memory_records_t`（Dreaming 整理） | 平台「可进化」实据之一 |

---

## 5. Skill 分层（真实落库 4 个，`ag_skill_info_t`）

| skill_id | skill_name | 定位 | 职责摘要（原文要点） |
|---|---|---|---|
| 1 | `domain-asset-cognition` | 入口·路由层 | 只做路由与总装：锚定实体 → 判断问题类型 → 加载子技能 → 交证据组装；禁止绕过子技能直调 `kg_multi_hop` 等 |
| 2 | `evidence-assembly` | 证据组装技能 | 生成可溯源决策卡（`decision_card_render`），带认知辅助免责声明（`disclaimer_domains` 控制） |
| 3 | `reasoning-path` | 推理路子技能 | 跨文档决策考量、版本对比（`kg_multi_hop` 版本钉住多跳 + 证据链） |
| 4 | `retrieval-path` | 检索路子技能 | 单点事实查询（`kg_search` / `knowledge_base_search`） |

编排契约（来自入口 Skill 原文）：判断不确定 → 双路并发；同类单路失败 2 次 → 升级双路；无证据时必须输出「现有知识库无依据」，不得编造。

---

## 6. 关键文件说明（json/知识库/MCP 同步提交物）

| 文件 | 说明 |
|---|---|
| `competition/deliverables/agent-config.json` | 本文件 §1-§4 的机器可读版（真实导出，key 脱敏） |
| `competition/docs/call-graph.md` | 调用关系图（Mermaid），对应本文件 §3/§5 的连线 |
| `corpus/registry.csv` | 58 份语料登记表（知识库清单） |
| `backend/tool_collection/mcp/kg_tools.py` | 自研 MCP 工具双注册代码（需同步提交的 MCP 说明依据） |
| `backend/services/knowevo/*.py` | 服务层实现（kg_service / decision_service / version_pin / ontology_service 等） |

---

## 7. 调试迭代经验（摘要，完整版见 `pitfalls.md` 台账 **63 条**，2026-09-22 实数）

- **诚实性血泪**：E1 基线曾因 eval 判定「忽略未答」导致分母污染——改为「未答计错」并全量重跑（诚实口径 acc=0.6667 / n_judged=60/60，旧口径 0.7692 作废）。
- **诚实性血泪（二）**：E8 版本钉住消融曾出现跨 invocation 的伪 Δ=+0.3334，后经同一次 invocation 配对复跑纠正为 **Δ=0.0（honest null）**——详见 `pitfalls.md` #62，教训是「不同批次的两臂不能当配对实验」。
- **LLM 契约坑**：平台 llm 契约要求返回解析后的 dict；`str` 返回会在 `_parse_extraction` 被拆字符炸掉——摄取驱动补了 str→JSON 适配层（仓库代码零改动）。
- **网关契约坑**：sensenova 网关要求 `reasoning_effort:none` 必须与 `thinking:disabled` 成对下发，否则 400（`pitfalls.md` #60，含字段级探针证据）。
- **429 风暴**：网关 tpm 小配额，连续打 1-2 span 即撞 429；驱动内置风暴熔断 + 断点续跑（span_hash 幂等），空段绝不写死。
- **双注册一致性**：Local MCP 与 FastMCP 共享单 schema（`tool_schemas()`/`handlers()`），保证工具名全平台唯一。
- 全部 63 条踩坑记录见 `competition/docs/pitfalls.md`（「调试迭代经验」章节素材）。