# 素材 → 评分维度映射表（Evidence Index）· Nexent-KnowEvo

> T-23 交付物之一。初赛《智能体设计思路及详细说明》的「示例问答截图 + 各素材对应评分维度」映射基准。
> 事实基准：评分四维来自 `05-计划书-初赛提交版.md` §2.4（维度1 编排与 MCP/Skill / 维度2 资产激活 / 维度3 算法创新 / 维度4 交互与沉淀，各 25%）。
> 状态标注：`✅ 已有` = 素材已存在；`🟡 待补` = 依赖截图/数字（见缺口清单）；`🟡 采集中` = 截图正在采集（t23-capture2）；`待跑` = 实验未执行；`insufficient_data` = 零判定格（**≠ 0 分**）；`待续跑` = 依赖构建租户图谱摄取 → T-22 续跑。

---

## 一、总体映射表

| 素材（证据） | 对应评分维度 | 证据强度 | 来源文件 | 状态 |
|---|---|---|---|---|
| 调用关系图（Agent→Skill→MCP→服务→存储） | 维度1 | ★★★ | `docs/call-graph.md`（Mermaid） | ✅ 已有 |
| 自研 MCP 工具双注册（5 工具 + 平台 34 工具） | 维度1 | ★★★ | `docs/agent-config.md` §3；`backend/tool_collection/mcp/kg_tools.py` | ✅ 已有（口径注：05 母本 §「8 自研 MCP 工具」含 `knowevo_mcp/SPEC.md` 规划项，实际注册 `KG_MCP_TOOL_NAMES`=5；对外表述以实际注册数为准并注明规划项） |
| Skill 三层编排（入口路由 + 检索/推理/证据装配） | 维度1 | ★★★ | `docs/agent-config.md` §5；`ag_skill_info_t` 真实 4 条 | ✅ 已有 |
| 平台跑通（智能体发布/对话/模型注册截图） | 维度1/2 | ★★☆ | `deliverables/T-01-agent-published.png` / `T-01-chat-conversation.png` / `T-01-models-registered.png`（3 张，2026-09-14；**真实平台界面**） | ✅ 已有 |
| 平台登录后首页 | 维度1/4 | ★★☆ | `deliverables/T23-home-loggedin.png`（2026-09-20；**真实页面**：登录态首页） | ✅ 已有 |
| **示例问答截图 ≥3（真实问答，可回溯 decision_card_t）** | 维度2/4 | ★★★ | `deliverables/`（**待补**：见 t23-capture2.status.json。本轮实测阻塞——平台唯一智能体 `knowevo_assistant` 在 `ag_tool_instance_t` 中 **0 工具绑定**、且「尚未配置向量模型」，chat 全链路只能纯 LLM 回答，无证据链/版本戳，**不满足本行合格判据**；需先给智能体绑定 KG/决策卡工具后再采集，未以低质图充数） | 🟡 待补（有阻塞说明） |
| **本体工作台截图（本体树 + 导航）** | 维度2 | ★★★ | `deliverables/t23-shot-ontology-workbench.png`（2026-09-21 04:03；**真实页面**：宿主机 next dev :3000 + uvicorn :5010，本体树视图） | ✅ 已有 |
| 本体工作台-待审队列截图 | 维度2 | ★★☆ | `deliverables/t23-shot-ontology-queue.png`（2026-09-21 04:03；**真实页面**：待审队列视图） | ✅ 已有 |
| **决策卡截图（证据链展开 + 版本戳 + 免责声明）** | 维度2 | ★★★ | `deliverables/T-23-decision-card.png`（2026-09-21 16:09 UTC；**真实页面** `/zh/decisionCard` + 真库行 `decision_card_t.id=284ff0c1-a3bb-450c-8069-e1bb60512d4b`；问题「糖尿病前期」/本体 v1.1.0；含导航+永久免责声明+`建议`/`知识版本:v1.1.0`/`截止 2025-01-01T00:00:00+00:00`/`时钟 fact_cutoff`/`version_pinned` 戳 + Top-1 候选(置信度0.80) + 展开证据链(EXTRACTED/版本钉住/kg_path)） | ✅ 已有 |
| **新旧指南版本对比（同题两版答案不同）** | 维度2/3 | ★★★ | `deliverables/T-23-version-compare-2024.png` / `T-23-version-compare-2026.png`（2026-09-21 16:14 UTC；**真实页面**，同题「糖尿病前期」+同本体 v1.1.0，仅 `as_of` 不同：`as_of=2024-01-01`→`证据不足`(0候选, card `3fd88441-70be-4163-8db9-fcbc423fcd88`) vs `as_of=2026-01-01`→`建议`(1候选+证据链, card `9189abf8-4e36-4f2d-b922-27a7956de396`)；两版 `knowledge_stamp.clock_source=explicit`、`kg_cutoff` 分别为 2024-01-01/2026-01-01 且不同 → 判别门通过）= 真实双时态版本分化 | ✅ 已有 |
| **Skill 模板库页面截图** | 维度4 | ★★☆ | `deliverables/t23-shot-skill-template.png`（2026-09-21 04:03；**真实页面**：skill_template_t 落库模板） | ✅ 已有 |
| **评测报告图（消融柱状图/曲线）** | 维度3 | ★★★ | `deliverables/t23-eval-report.png`（2026-09-21 11:54；由 `e2-ablation-report.json` 真实数字绘制，matplotlib；insufficient_data 格明确标注；E2 续跑后需重绘） | ✅ 已有 |
| E1 纯 RAG 基线（acc=0.6667 / pass2=0.65 / n_judged=60/60） | 维度3（对照） | ★★★ | `deliverables/e1-baseline-report.json`；权威口径 = `docs/cost-ledger.md` 行 `e1-b64faa90-64ac-44bc-95e9-564ad4b99006` | ✅ 已有 |
| E2 消融报告（A1/A2/A3/A4 × E8） | 维度3 | ★★★ | `deliverables/e2-ablation-report.json`（**partial:true**）：**A1_pure_rag** n_q=10 acc=0.7241 / pass2=0.70 / n_judged=29/30；**A2_graph** n_q=4 acc=0.75 / n_judged=12/12；**A4_full pin=on** n_q=2 acc=0.5 / n_judged=6/6（旧行 2026-09-19）；**A4_full pin=off** n_q=1 acc=0.5 / pass2=0.0 / n_judged=2/2（新行 2026-09-21）；**A3_multihop 待跑**（insufficient_data）；**E8 Δ 不可测**（`e8.arm_vintage.same_invocation=null`，两臂非同一次配对实验，pitfalls #56） | 🟡 待续跑 |
| 机制预验证四探针（P1 100% vs 20.6% / P2 VOI / P3 0.02ms / P4 98.8%） | 维度3 | ★★★ | `deliverables/algorithm-probes/probe_p{1..4}_*.json` | ✅ 已有 |
| 版本钉住机制测试（TestVersionPinnedWalk） | 维度3 | ★★★ | `test/backend/services/knowevo/test_decision_service.py` | ✅ 已有 |
| 消融单测 + PG 集成（688 passed，2026-09-21 r16 全量实测） | 维度3 | ★★★ | `pytest` 运行记录 | ✅ 已有 |
| 性能 PoC（多跳 p95=12.5ms / supersede p95=22.7ms） | 维度2/3 | ★★★ | `docs/poc-graphstore.md` | ✅ 已有 |
| 跳数标定（depth=2 饱和） | 维度3 | ★★☆ | `cost-ledger` t09-curve 行 | ✅ 已有 |
| 语料台账（58 份 + 批次核查） | 维度2 | ★★☆ | `corpus/registry.csv` + `docs/verification-reports/batch{1,2,3}-*.md` | ✅ 已有 |
| 构建租户图谱（实体/关系/证据链） | 维度2/3 | ★★★ | `kg_graph` + `kg_evidence_t`（doc_id 链）；2026-09-21 r20 收盘快照：15/120 段、**136 实体 / 65 关系**（含 2024 权威关系 32 条、b_2024plus=13）、38 证据行（来源 `cost-ledger` 行 `r20-burst-c` + 报告 `data_reality` entities=136/relations=65）；D1 判别性计数 all=674 / t_v=2022-01-01=33 / t_v=2024-06-01=190 / t_v=2025-06-01=66，`discriminative:true`（E8 判别门已解锁） | 🟡 摄取中（120 段） |
| 踩坑台账 56 条（调试迭代经验素材，2026-09-21 实数） | 维度4 | ★★★ | `docs/pitfalls.md` | ✅ 已有 |
| 演进台账（evolution-log） | 维度4 | ★★☆ | `docs/evolution-log.md` | ✅ 已有 |
| 模板复用统计 R/S/D（skill_template_t） | 维度4 | ★★☆ | `skill_template_t`（T-20 起真实落库） | ✅ 已有 |

---

## 二、评分维度 → 最硬证据（答辩对齐，05 §2.4）

| 维度（各25%） | 主承载 | 最硬证据（本表行） |
|---|---|---|
| 1 编排与 MCP/Skill | 双驱动 + 自研 MCP 5 工具 + Skill 三层 | 调用关系图 + 双注册代码 + NL2Skill（示例问答演示） |
| 2 资产激活业务价值 | L1 资产化 + 决策卡 | 资产漏斗数字 + 版本混乱真实案例 + 溯源完整度（决策卡截图） |
| 3 算法创新性 | §3/§4 三个算法贡献（版本钉住/VOI/低资源构建） | 形式化 + 性质测试 + 消融（E2）+ 机制预验证四探针 |
| 4 交互与能力沉淀 | L5 三面板 + 模板库 | 截图 + 模板复用统计（R/S/D）+ 踩坑→机制叙事 |

---

## 三、缺口跟踪（随 T-23 装配推进更新）

| 缺口 | 阻塞源 | 解锁动作 | 状态 |
|---|---|---|---|
| 六类官方截图（本体工作台/决策卡/示例问答≥3/版本对比/Skill 库/评测图） | 前端栈（宿主机 next dev 3000 + uvicorn 5010；r17 实证 stock 镜像无 knowevo 路由） | **已拍 7 张**：`t23-shot-ontology-workbench.png` / `t23-shot-ontology-queue.png` / `t23-shot-skill-template.png`（r17，2026-09-21 04:03）、`t23-eval-report.png`（2026-09-21 11:54）、`T23-home-loggedin.png`（2026-09-20）、`T-23-decision-card.png` / `T-23-version-compare-{2024,2026}.png`（r22 `t23-capture2`，2026-09-21 16:09/16:14 UTC）；**待补 1 类**：示例问答≥3（阻塞：唯一智能体 `knowevo_assistant` 0 工具绑定 + 未配置向量模型） | 🟡 进行中 |
| **待补截图：示例问答≥3** | 智能体未绑定任何工具（`ag_tool_instance_t` agent_id=1 无行）+ 未配置向量模型 | 决策卡面板 / 版本对比 2 张已由 `t23-capture2` 于 2026-09-21 16:09/16:14 UTC 采得（真库行 `284ff0c1`/`3fd88441`/`9189abf8`）；示例问答须先给智能体绑定 KG/决策卡工具后再采集，每个答案须可回溯 `decision_card_t`/`eval_run_t` 行 | 🟡 待补 |
| 评测报告图 | E2 partial；`e8.arm_vintage.same_invocation=null`（E8 Δ 不可测） | 摄取 120/120 → `t22-resume.status.json` resume 四段命令 → 回填完整矩阵；已按现有真实数字出图（`t23-eval-report.png`），缺口格标注 insufficient_data；续跑后需重绘 | 🟡 |
| 示例问答可回溯性核验 | 截图拍摄 | psql 查 `decision_card_t`/`eval_run_t` 行对应 | 🟡 |
| T-05 e2e 截图欠账（本体树 ≥30 节点 + diff 三色） | 同上起栈 | 补拍（T-05-receipt.md:41 未勾选） | 🟡 |
| vision agent 逐张审图 | 截图就位后 | 按 `wave-plan.md:59` / vision skill 审图，结论回填本表 | 🟡 |

---

## 四、生成记录

- 首版创建：2026-09-20（T-23 无阻塞装配轮，loop-r11）
- r22 数字回填（t23-docs）：E1/E2/图谱快照对齐 `cost-ledger` 与 `e2-ablation-report.json` 真实来源；登记 5 张已有截图；待补截图标注采集中（t23-capture2）；未完成项一律 `待跑`/`insufficient_data`，不以 0 或占位冒充。
- 2026-09-21 16:09/16:14 UTC（t23-capture2，配额空档）：新增 3 张真实截图——决策卡面板 `T-23-decision-card.png`（真库 `decision_card_t.id=284ff0c1`）与版本对比 `T-23-version-compare-{2024,2026}.png`（同题「糖尿病前期」+同本体 v1.1.0，仅 `as_of` 不同：2024→证据不足 / 2026→建议+证据链；真库 `3fd88441` / `9189abf8`）。示例问答≥3 经实测阻塞（智能体 0 工具绑定 + 无向量模型），如实标注，未伪造。
- 素材核验方式：文件存在性 + DB 真实查询（`ag_tenant_agent_t` / `model_record_t` / `ag_skill_info_t` / `ag_tool_info_t`）
- 更新纪律：每新增/重拍一张截图、每落定一个评测数字，同步更新本表状态列。
