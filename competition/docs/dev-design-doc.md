# 开发设计文档（初赛提交版）

> **Nexent-KnowEvo**：基于华为 ModelEngine 生态开源平台 Nexent（v2.6.0）构建的**领域知识资产认知与决策智能体**
> 赛题：基于 ModelEngine Nexent 打造可进化决策智能体（赛题3）· 参赛材料：初赛开发设计文档（官方模板1）
> **文档状态（2026-09-22 整理定稿）**：官方模板1 要求的六节 + 智能体设计说明（Agent 配置 / 模型 / 工具 / 知识库 / Skill / 调用关系图 / 调试经验 / 附件说明）**已全部成文，无占位符**。每个数字标注来源等级：**A=实测**（有产物可回溯）/ **E=估算** / **L=文献** / **P=计划**。
> 平台相关表述已通过《平台事实红线检查报告》（v2.2）校准；待补项集中于 §6.3「当前已知缺口」，全部如实声明。
> 配套文件清单见 §5.10；对外表述以 `05-计划书-初赛提交版.md` 为准，引用以 `06-论文与开源武器库-v2.md` 为准。

---

## 1. 项目概述

### 1.1 一句话定位

以 2 型糖尿病用药决策为主场景，把沉睡的行业文档（指南/说明书/路径/检验单）激活为**版本化的知识图谱资产**，通过「检索-推理」双驱动执行流产出**每一步可溯源的用药决策卡**，并让知识体系随行业标准演进而**增量进化**（A：平台真实部署跑通，2026-09 起）。

### 1.2 痛点与价值主张

| 痛点 | 现状 | 本项目回答 |
|---|---|---|
| 知识版本混乱 | 新版指南发布后旧版仍在基层使用，检索系统无法区分新旧事实 | 版本钉住的推理语义层：多跳遍历强制版本谓词，过期事实显式上报 |
| 知识资产沉睡 | 行业文档散落在语料库，未成结构化认知资产 | L1 资产登记 + L2 图谱化 + L3 决策执行 + L4 进化闭环 |
| 决策不可溯源 | 大模型回答无证据链、无版本戳、无免责 | 决策卡：证据链展开 + 版本戳 + 认知辅助免责声明 |
| 标准演进成本高 | 指南更新后知识体系手动梳理，人力成本高 | 渐变编排层：变更检测→受影响面→最小充分更新集→人审预算择优（L 算法，A 机制预验证） |

### 1.3 与赛题五条任务的对齐

| # | 官方任务（原文摘录） | 本项目落点 | 承载模块 |
|---|---|---|---|
| 1 | 多模态异构数据资产认知：统一识别、理解、**资产标识** | L1 摄取管道 + registry 资产登记（编号 / 模态 / 权威等级 / 版本血缘 / 业务出版日） | §6 §7 |
| 2 | 低资源场景本体**半自动化**构建、**持续动态更新**、与行业规范对齐 | L2 种子引导流水线 + L4 标准对齐器（★创新主轴） | §3 §4 |
| 3 | 深度集成 MCP 协议与 Skills 编排，"检索-推理"**双驱动** | L3 双驱动执行流 + 自研 MCP 工具族 + Skill 分层 | §5 |
| 4 | 跨文档多跳推理、**决策链路可溯源**；沉淀可复用 Skill 模板 | 版本钉住多跳（★）+ 决策卡 + 模板库 | §4.3 §5.2 §5.4 |
| 5 | 智能体基于 Nexent 平台可运行 | fork 上真实部署、HTTP + MCP 双入口、评测走平台"智能体评估" | §2.3 §5.5 |

### 1.4 开发模式声明（诚实且加分）

本项目采用**人机协作分层开发**：人类队员负责赛题对齐、算法设计决策、数据与评估金标、最终评审；AI 编码智能体在**接口契约与验收命令约束下**实现模块（任务包-验收-证据三件套管理，规范见 `07-AI代码编写规范与并行开发手册.md`）。全部代码可开源复现，开发过程留有完整台账（**踩坑台账 63 条**、token 成本台账、知识版本演进记录）——这本身即是官方要求项「调试迭代经验」的实据。

---

## 2. 整体方案设计

### 2.1 三条设计铁律（A：代码结构实证）

1. **最小侵入上游**：业务代码全部在自包含目录（`backend/services/knowevo/`、`mcp_servers/knowevo_mcp/`、`frontend/features/*`），上游 Nexent 零修改（除 ≤30 行纯新增渲染分支）；v2.5.0→v2.6.0 实测零冲突合并（A）。
2. **弃用优先于删除**：知识资产只打戳不物理删除，决策卡历史可回溯（溯源链完整性）。
3. **零侵入评测**：自定义指标写成 Nexent 原生 Code 判定，签名逐字 `evaluate(query, expected, actual, runtime_events)`（官方文档核验 A），上游评估服务零改动。

### 2.2 六层架构

```
L5 交互层   本体工作台(树/diff/版本) · 决策卡面板(证据链展开) · 模板库页 · 评测报告
            （实现于 frontend/features/{knowledgeGraph,decisionCard,skillTemplate}）
L4 知识进化层 ★创新主轴
            标准对齐器：变更检测 → 受影响面 → 最小充分更新集（§4）
            冲突消解：检测→分类→裁决记录 · Skill 模板库（轨迹→模式→SKILL.md）
L3 决策执行层
            检索-推理双驱动（§5）：路由(规则+few-shot)
              检索路(knowledge_base_search + kg_search) ／
              推理路(kg_multi_hop 束搜索·版本钉住★) → 证据链融合 → 决策卡
L2 知识引擎层（§3）
            本体半自动构建(种子引导 + 两级提案 + 人审预算★) · 本体锚定抽取(子图检索注入)
            · 三级实体对齐(外部主键 blocking) · bi-temporal
            · PG JSONB + 递归 CTE 图存储（同库同事务，溯源 JOIN 一致性）
L1 数据资产层
            Nexent 摄取管线(Unstructured) + 专项解析兜底
            registry 资产登记：编号 / 模态 / authority_level / published_at / 血缘
L0 Nexent 底座 v2.6.0（fork · 零修改）
            FastAPI 多服务 + Next.js 15 + PG/ES/Redis/MinIO + smolagents 内核
            + MCP 生态 + SKILL.md 渐进披露 + 三层记忆 & Dreaming
            + 智能体评估 + RBAC

进化闭环：标准更新 → L1 摄取 → L4 变更检测→受影响面→更新提案→人审确认
        → L2 版本化增量演进 → L3 决策卡带知识版本戳 → 智能体评估验证 Δ质量
```

> 说明（诚实口径）：L5 设计与实际实现的差异——本体工作台、决策卡面板、模板库页已实现；**资产看板与进化看板动画未实现**（资产看板经裁决放弃，见 `00-总纲与决策依据.md` §九）。

### 2.3 平台集成事实清单（核验版）

| 能力 | 核验结论（2026-09-18，官方文档 + 源码） |
|---|---|
| Agent 内核 | smolagents `CoreAgent(CodeAgent)`（代码层确认：`sdk/nexent/core/agents/core_agent.py:494`） |
| MCP | 自定义工具 = FastMCP `@mcp.tool()` + 界面注册 URL；本项目**双注册**（Local MCP + 独立 FastMCP 服务），单一 schema 源 |
| Skill | SKILL.md + YAML frontmatter + `<reference>` 渐进披露 + 沙箱执行 + ZIP 上传 + **NL2Skill**（`nl2skill_agent.py`） |
| 记忆 | Tenant / User / Agent 三层 + Dreaming（短期→长期记忆整理） |
| 检索 | ES `dense_vector` + kNN + BM25 混合（基于 Elasticsearch；**平台检索未见独立向量数据库组件**，本项目向量走服务层 cosine，与平台一致） |
| 评估 | 「智能体评估」模块：LLM 判定 + Code 判定（签名逐字 `evaluate(query, expected, actual, runtime_events)`，AST 安全扫描 + 白名单沙箱） |
| 版本 | **v2.6.0**（2026-09-16 发布），月度发版；本项目迁移文件名前缀 v2.5.5 为 fork 时点的历史事实，兼容 v2.6 |

> ⚠️ **平台事实红线**（写任何对外材料前必须遵守，完整清单见 `verification-reports/platform-facts-redline.md`）：平台版本必须写 **v2.6.0**（不是 v2.5.x）；**不得**声称平台有 pgvector（走 ES 混合检索）；评估模块官方名为**「智能体评估」**；代码评测器签名逐字如上；
> **「可进化」是本项目 L4 的实现，不是平台自带能力**——平台侧可引实据仅三件：Dreaming 记忆整理、Agent 版本管理回滚、智能体评估反馈闭环。

---

## 3. 知识图谱构建方案（重点节）

> 对应评分维度②③。结构：本体流水线 → 抽取对齐 → bi-temporal 增量更新 → 版本化；创新贡献③（低资源构建层）落本节。

### 3.1 本体半自动构建流水线（含创新贡献③）

- 种子引导 + 两级提案 + **人审预算显式目标**：max Σq(sᵢ) s.t. Σminutes(sᵢ) ≤ B（L：VoI 1909.08234 定义信息价值；E3 预算-质量曲线实验协议）。
- VOI 贪心接 (1−1/e) 子模近似框架（Kempe et al. 2003 移植，**诚实适用声明**：仅对覆盖型 impact 成立；P4 已实证 400/400 ≥ 1−1/e，mean=98.8%，A）。
- 真实落库：构建租户 ontology v1.1.0（10 类 / 10 关系 / fact_cutoff=2025-01-01，A）。

### 3.2 抽取与实体对齐（两个正确性设计）

- 本体锚定抽取：子图检索注入 fewshot，约束实体类别（A：构建租户摄取 **15/120 段、136 实体 / 65 关系**，2026-09-21 r20 收盘快照——来源 `cost-ledger` 行 `r20-burst-c`（runs 13→15 / 实体 113→136 / 关系 46→65 / 2024 关系 13→32 / 证据 35→38）+ 报告 `data_reality` 实测 entities=136 / relations=65；其中实体类别分布 Disease×15 / Population×7 / Symptom×6 / Examination×6 / Indicator×4 / Drug×1 / Treatment×1 为 **40 实体期快照**（2026-09-21 早，未随新增段重算）；0 缺 doc_id / 0 缺 valid_at，质量门禁全绿）。
- 三级实体对齐：外部主键 blocking（药品 ID/ATC 码等）+ 别名合并（重抽 merge 去重）。

### 3.3 bi-temporal 增量更新与冲突消解

- 事实双时间轴（valid_at / published_at），supersede 不打删（A：批量 supersede p95=22.7ms @2万实体，PoC；来源 `cost-ledger` 行 `poc-20260917-graphstore`）。
- 判定用 Code 判定按版本选金标（版本敏感题双金标设计；E：120 题测试集含 V 题 20 双金标属规划，当前实态为 20 题 seed 落库，见 §5.5）。

### 3.4 版本化

- OntologyService.commit_version 发布本体版本；版本钉住多跳在有效子图 G_v 上求解（形式化 + 构造性 soundness 五条性质具名测试锁定，A：机制预验证 V 题 100% vs 随机 20.6% / 最新版偏置 25.0%，probe_p1）。

---

## 4. 标准演进对齐器（★创新主轴 = 创新贡献①）

> 对应评分维度③。三段式：变更检测 → 受影响面分析 → 最小充分更新集。

### 4.1 三段式变更检测

文档级 diff → 条款级 diff → 语义级变更分类（新增/修订/废弃/冲突），产变更报告。

### 4.2 受影响面分析（传播算法）

- 溯源二部图反向可达（倒排索引物化，两次索引查询）。
- A：影响传播 0.02ms @合成 80 文档/2000 实体/3000 决策卡（`probe_p3_impact_prop.json`；`cost-ledger` 行 `algo-probe-20260919` 记 P3 0.02ms / 164 实体 / 750 卡）。

### 4.3 最小充分更新集（Minimal Sufficient Update Set，创新内核）

- 预算约束价值最大化：VOI 贪心 (1−1/e) 近似（与低资源构建层共用同一框架，诚实适用声明）。
- 人审确认后进入版本化增量演进（E9 更新集大小-质量损失曲线，T-11 交付，计划）。

### 4.4 压轴演示数据

《中国2型糖尿病防治指南》2020 版 ↔ 2024 版对比（A：语料已入 registry，11 份指南类核查通过）——「同一问题、两版答案不同」的版本对比演示数据源。

---

## 5. 智能体构建方案（重点节）

> 对应评分维度①②。SDK 层 = 检索-推理双驱动执行流（含创新贡献②）；MCP 工具族；Skill 分层编排；决策卡。

### 5.1 检索-推理双驱动执行流（含创新贡献②）

- 路由（规则 + few-shot）→ 检索路（kb_search + kg_search）→ 推理路（kg_multi_hop 束搜索·版本钉住★）→ 证据链融合 → 决策卡。
- 创新贡献② = **版本钉住的多跳推理语义层**：束搜索逐跳强制版本谓词、有效子图 G_v 上求解、被裁剪事实显式上报（路径有效性定义 + 构造性 soundness；A：V 题机制预验证 100% vs 20.6%/25.0%，F 题三臂 100% 无回归）。
- 运行表现（A）：多跳 p95=12.5ms @2万实体/3万边 depth≤3 beam=3（`cost-ledger` 行 `poc-20260917-graphstore`）；depth=2 精度饱和（30 题链式夹具，`cost-ledger` 行 `t09-20260917-curve`）。

### 5.2 自研 MCP 工具族（双注册）

**已注册 5 个**（唯一口径 = `backend/tool_collection/mcp/kg_tools.py` 的 `KG_MCP_TOOL_NAMES`）：

| 工具 | 功能 | 状态 |
|---|---|---|
| `kg_search` | 图谱实体 / 邻域检索（1 跳） | ✅ 双注册 |
| `kg_stats` | 图谱统计 | ✅ 双注册 |
| `kg_multi_hop` | 版本钉住多跳 + 证据链 | ✅ 双注册（T-09） |
| `decision_card_render` | 决策卡生成（结构化输出） | ✅ 双注册（T-19） |
| `skill_template_apply` | 模板实例化 | ✅ 双注册（T-20） |

规划中但**尚未实现**（不得写进对外材料）：`kg_evolution_trace`、`ontology_diff`、`asset_search`、`evidence_verify`。

注册机制：同一 FastMCP 应用实例被挂载进 Local MCP，两处注册共用 `mcp_servers/knowevo_mcp/schemas.py` 这一份 schema，**无漂移**。

### 5.3 Skill 分层编排

```
Agent(duty prompt 只写职责与约束)
 └─ Skill: domain-asset-cognition（入口·路由：锚定→路由→加载子技能→总装→出口）
     ├─ Skill: retrieval-path → kg_search / knowledge_base_search / asset_search
     ├─ Skill: reasoning-path → kg_multi_hop / kg_evolution_trace（版本对比 task=version_compare）
     └─ Skill: evidence-assembly → decision_card_render / evidence_verify
```
A：4 个 Skill 真实落库（`ag_skill_info_t`，2026-09-20 导出）；模板库从真实决策轨迹归纳参数化母版（四变量 `{domain}/{task_type}/{关系模板}/{行业规则}`，支撑政务迁移附加分）；复用统计 R/S/D 落 `skill_template_t`（T-20 起真实落库）。

### 5.4 决策卡（可溯源的直接载体）

决策卡 JSON 含：`knowledge_stamp`（ontology_version + kg_cutoff）、candidates（score + confidence_calibrated + evidence_chain[claim/provenance/version_pinned/tag]）、risks、counterfactual、conflict_adjudications、uncertainty_notes、disclaimer（「本系统提供认知辅助，不构成处方建议」）。
A：置信度校准 ECE 目标 ≤0.10（无校准表诚实标 `calibration_applied=false`）；反事实仅 top-1（成本控制）。

### 5.5 评测体系与当前结果（诚实口径）

- 测试集（规划 E）：120 题四题型（F 35 / M 45 / V 20 双金标 / X 20）；3 队员交叉标注 Krippendorff α≥0.7（计划）；80/20 构建盲区隔离（计划）。**实态（A）**：当前落库 `testset-v1-seed.json` 20 题（e2-ablation testset n=20），120 题全量未完成（T-10a-3 未执行）——引用时按此如实口径。
- 指标：pass^2（每题 3 跑 ≥2 对，τ-bench 口径）+ acc（**同报 n_judged**）+ 溯源完整度双口径 + p95/token。
- judge 偏差控制：judge 与生成异家族模型、五元组 rubric、20% 翻转测试、10% 人工抽检。
- 当前数字（A，如实标注状态）：

| 实验 | 状态 | 数字 |
|---|---|---|
| E1 纯 RAG 基线 | ✅（诚实口径重跑） | acc=0.6667 / pass2=0.65 / n_judged=60/60 / trace=1.0（20 题×3 runs；旧口径污染已修复：未答计错）。权威口径 = `cost-ledger` 行 `e1-b64faa90-64ac-44bc-95e9-564ad4b99006`，逐题报告 `deliverables/e1-baseline-report.json` |
| 机制预验证四探针 | ✅（seed 固定可复跑） | P1 V 题 100% vs 20.6%/25.0%；P2 VOI 净差 +1~+6；P3 0.02ms；P4 98.8% |
| E2 四级消融 | 🟡 partial（报告 `partial:true`；已跑 A1 10 题 / A2 4 题 / A4 两臂配对，A3 待跑） | **A1_pure_rag** n_q=10，acc=0.7241 / pass2=0.70 / n_judged=**29/30**（F .7857(14) / M .6667(15) / V·X insufficient_data）；**A2_graph** n_q=4，acc=0.75 / n_judged=12/12（仅 F）；**A4_full 配对终版**（`e2-ablation-paired2.json`，`same_invocation=true`、零空正文污染）：V 题 3 题×2 runs **两臂各 acc=1.0（n=6/6）→ Δ=0.0**；**A3_multihop 待跑**（0 题，`insufficient_data`——零判定不等于 0 分） |
| E8 版本钉住 on/off | ✅（honest null） | **Δ=0.0**（配对终版，取代早期跨 invocation 的 +0.3334 伪方向，见 `pitfalls.md` #62）。**诚实限制：n=6 小样本 + 两臂均满分，Δ=0 不得反推「版本钉住无效」**；机制证据改用判别题双时钟对（糖化血红蛋白：`as_of=2021-06-01` → `INSUFFICIENT_EVIDENCE` 旧时钟 0 证据诚实拒绝 vs `as_of=2025-06-01` → `RECOMMEND` + 3 证据） |
| PG 集成 + knowevo 全量单测 | ✅ | **711 passed / 30 skipped / 0 failed**（2026-09-22 本次提交实测，`pytest ../test/backend/services/knowevo/ -q`） |

### 5.6 Agent 主配置（真实落库值）

来源：`nexent` 库 `ag_tenant_agent_t`（2026-09-20 导出，**非手写**）。密钥类字段（`api_key`/`access_token`）一律不进交付物。

| 字段 | 值 |
|---|---|
| agent_id / name | `1` / `knowevo_assistant`（display_name：Knowevo 助手） |
| tenant_id | `6756b0ab-39c0-462a-9745-aa12e1511fcd`（构建租户） |
| version_no | 1（当前版本） |
| model_name | 空——走 `KW_LLM_SMALL/MID/LARGE_MODEL_ID` 三档路由（见 §5.7） |
| duty_prompt | 「你是一个简洁友好的中文助手。你的职责是准确、清晰地回答用户的问题，不编造事实。」 |

> 设计要点：Agent 的 prompt 只写**职责与约束**，不写领域执行细节——领域逻辑全部下沉到 Skill 分层（§5.3）与 MCP 工具（§5.2），保证 Agent 本体可随平台升级零改动。

### 5.7 模型信息（真实落库 9 档，API key 脱敏）

来源：`model_record_t`（`connect_status=available`）。

| model_id | 模型 | 类型 | 用途 | context_window |
|---|---|---|---|---|
| 1 | glm-5.3-free | llm | 主档 | 32768 |
| 2 | glm-5.3-free | llm | 小档 | 32768 |
| 3 | bge-m3 | embedding | 向量化（dim=1024） | — |
| 4 | deepseek-v4-flash | llm | 生成 / 小中档 | 65536 |
| 5 | deepseek-v4.1-flash | llm | 大档 | 65536 |
| 6 | glm-5.3-flash | llm | judge·**异家族** | 65536 |
| 7 | deepseek-v4-flash@sn | llm | 生成·中档（sensenova 网关） | 65536 |
| 8 | glm-5.2@sn | llm | judge·异家族 / 降级重试 | 65536 |
| 9 | deepseek-v4-pro@sn | llm | 大档·预留 | 65536 |

**三档路由**（`backend/services/knowevo/llm_client.py`）：small/mid = `deepseek-v4-flash@sn`（快、便宜：日常抽取与检索）；large = `glm-5.2@sn`（异家族，用于降级重试与判别）；judge = `glm-5.3-flash`（**与生成模型异家族**，解耦自评偏差——评测方法论要求）。

### 5.8 工具信息与知识库信息

**自研 MCP 工具**：见 §5.2（5 个已注册，双注册）。
**平台侧工具**：真实落库 **34 个**（`ag_tool_info_t`，`is_available=t`），本场景直接相关的有 `knowledge_base_search`（平台 ES 混合检索）、`aidp_search`、`search_memory` / `store_memory`、`postgres_database`、`exa_search` / `tavily_search`、`parallel_executor` 等。

**知识库**：

| 项 | 值 | 来源 |
|---|---|---|
| 语料登记 | 58 份 | `corpus/registry.csv` |
| 语料构成 | 药品说明书 28 / 临床指南 11 / 诊疗路径 9 / 检验 2 / 科普 8 | `verification-reports/batch{1,2,3}-*.md` |
| 图谱资产 | 摄取 15/120 段；**136 实体 / 65 关系 / 38 证据行**（2026-09-21 r20 收盘） | `cost-ledger` 行 `r20-burst-c`；`e2-ablation-report.json` 的 `data_reality` |
| 本体 | 10 类 / 10 关系，`fact_cutoff=2025-01-01`（v1.1.0） | `OntologyService.commit_version` |
| 平台记忆 | `memory_records_t`（Dreaming 整理） | 平台「可进化」实据之一 |

**Skill 分层（真实落库 4 个，`ag_skill_info_t`）**：

| skill_id | skill_name | 定位 |
|---|---|---|
| 1 | `domain-asset-cognition` | 入口·路由层：锚定实体 → 判断问题类型 → 加载子技能 → 交证据组装；**禁止**绕过子技能直调 `kg_multi_hop` |
| 2 | `evidence-assembly` | 证据组装：生成可溯源决策卡，带认知辅助免责声明 |
| 3 | `reasoning-path` | 推理路：跨文档考量、版本对比（`kg_multi_hop`） |
| 4 | `retrieval-path` | 检索路：单点事实查询（`kg_search` / `knowledge_base_search`） |

编排契约（入口 Skill 原文）：判断不确定 → 双路并发；同类单路失败 2 次 → 升级双路；**无证据时必须输出「现有知识库无依据」，不得编造**。

### 5.9 调用关系图（Agent → Skill → MCP → 服务 → 存储）

```
用户提问
  → Agent 主进程（duty prompt 只写职责与约束）
  → 入口 Skill: domain-asset-cognition（路由）
       ├─ 单点事实类（检索路）→ Skill retrieval-path
       │      → MCP kg_search ／ 平台 knowledge_base_search（ES 混合检索）
       └─ 多跳/演进类（推理路）→ Skill reasoning-path
              → MCP kg_multi_hop（束搜索 · 版本钉住★）
  → Skill evidence-assembly → MCP decision_card_render
  → 决策卡落库 decision_card_t 并展示（含知识版本戳 + 证据链 + 免责声明）

旁路：本体/版本管理 → ontology_service.commit_version → ontology_version_t
存储：PostgreSQL JSONB（kg_graph / kg_evidence_t / decision_card_t /
      skill_template_t / eval_run_t）；Elasticsearch（知识库文档索引）
评测：平台「智能体评估」Code 判定 evaluate(query, expected, actual, runtime_events)
      → eval_run_t → 消融报告
```

> 完整版（含 Mermaid 渲染图、三条关键链路逐条拆解、注册机制事实依据）见随交付物提交的 `call-graph.md`。

### 5.10 需同步提交的文件说明（官方要求项）

| 文件 | 说明 |
|---|---|
| `deliverables/agent-config.json` | 本文件 §5.6-§5.8 的机器可读版（真实导出，密钥脱敏） |
| `docs/call-graph.md` | 调用关系图（Mermaid）+ 三条关键链路拆解 |
| `docs/dev-design-doc.md` | 本文件（开发设计文档母本） |
| `docs/agent-config.md` | Agent 配置说明（含平台 34 工具清单与 Skill 原文要点） |
| `docs/reproduce-README.md` | 复现步骤（含部署方式） |
| `corpus/registry.csv` | **知识库清单**：58 份语料登记（编号 / 模态 / 权威等级 / 来源 URL / license 说明） |
| `backend/tool_collection/mcp/kg_tools.py` | **MCP 说明依据**：自研工具双注册代码（`KG_MCP_TOOL_NAMES` 唯一口径） |
| `mcp_servers/knowevo_mcp/{server.py,schemas.py}` | 独立 FastMCP 服务与单一 schema 源 |
| `backend/services/knowevo/*.py` | 服务层实现（kg_service / decision_service / version_pin / ontology_service / skill_template_service / llm_client） |
| `docs/pitfalls.md` | 调试迭代经验台账（**63 条**） |
| `deliverables/*.png` | 示例问答与界面截图（**「示例问答 ≥3」尚待补齐**，见 §6.3 诚实说明） |

---

## 6. 原始数据说明 / 数据处理说明

### 6.1 原始数据说明

| 项 | 内容 |
|---|---|
| 规模 | 58 份真实公开语料（registry.csv 全量登记，2026-09-21 逐行实测） |
| 构成 | 药品说明书（drug_label）28 / 指南（guideline）11 / 政策（policy）9 / 科普图文（edu_graphic）8 / 检验报告（lab_report）2 |
| 来源与合规 | 全部公开渠道（官方指南 PDF、NMPA 公开说明书、公开临床路径）；**原始 PDF 不入 git、不对外再分发**（版权铁律），交付物只含解析文本与元数据 |
| 权威度分级 | authority_level 1-4（国家标准=1 … 科普=4），进检索排序（T-18d 实测生效 A） |

### 6.2 数据处理说明

```
原始 PDF/图文 → Nexent 摄取管线(Unstructured) + 表格专项解析/OCR 兜底
  → 解析体检(抽样评分，不合格重解析) → registry 登记(编号/模态/权威度/出版日/血缘)
  → 80/20 盲区切分(构建/评测隔离) → chunk(600字,保留定位符) → ES 索引 + L2 图谱抽取
  → 本体锚定抽取(子图检索注入) → 三级对齐 → bi-temporal 入图 → 版本化
```
质量控制点：解析体检报告、抽取 EXTRACTED/INFERRED 双标签 + 待审池、对齐阈值 ROC 标定（基准见 E1 失分归因）、入图前 pySHACL 校验（可选）、成本台账周检（cost-ledger 逐轮登记，A）。

### 6.3 评测与结果

见 §5.5 表格。**提交定稿前待补**：构建租户图谱摄取补齐至 120/120 → T-22 四段续跑（E8 off/on 臂 → A3 → 交叉表）→ 回填 E2 全量矩阵 → 重绘评测报告图（消融柱状图/曲线）。缺失数字一律如实标注「待续跑」，**不做估计**（诚实性条款，见 `07-AI代码编写规范` §4.3）。

**当前已知缺口（如实声明，不掩盖）**：
1. 「示例问答截图 ≥3」**尚未取得**——平台内完整问答链路 `/api/agent/run` 当前被部署环境缺陷阻塞（`NEXENT_SANDBOX_DEFAULT_LEVEL=docker` 导致宿主机解析不了容器 DNS 名 `nexent-minio:9000` → `EndpointConnectionError` → `MemoryPreparationException`），已实测 3 轮均失败，**未以低质截图充数**。解锁方案与截图采集脚本已就绪（覆盖 `NEXENT_SANDBOX_DEFAULT_LEVEL=local` 或改 `MINIO_ENDPOINT` 指向宿主机映射端口）。
2. E2 四级消融的 **A3（multihop）尚未跑**；E8 配对终版为 Δ=0.0 的 honest null（n=6 小样本，不可反推机制无效）。
3. 120 题全量测试集未建成，当前为 20 题 seed + 8 题判别集。

以上三项均为**已知且已登记**的缺口，交付时以实际完成状态如实呈现。

---

## 附：创新点声明（分层对标，正面回应"缝合"质疑）

> 完整分层对标表见 05 §8（三层：(a) 最接近的已有工作 → (b) 它们解决什么 → (c) 我们的差异 → (d) 证据锚点）。答辩一句话：「我们没有重新发明图数据库和 RAG；我们把'知识版本'做成了推理约束，把'标准变更'做成了带预算控制的增量演进算法，并给出了每个设计的消融证据。」

| 层 | 落点 | 最硬证据 |
|---|---|---|
| 推理语义层（贡献②） | 版本钉住多跳：束搜索强制版本谓词 | V 题 100% vs 20.6%/25.0%（probe_p1）+ 构造性 soundness 测试 |
| 演进编排层（贡献①） | 变更→影响→最小更新全链条编排 | 0.02ms 传播预验证 + E9/E3 消融 |
| 低资源构建层（贡献③） | 人审预算显式优化目标 + 预算-质量曲线 | VOI 净差 +1~+6 + 子模性 mean=98.8%（probe_p2/p4） |