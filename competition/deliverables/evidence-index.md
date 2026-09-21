# 素材 → 评分维度映射表（Evidence Index）· Nexent-KnowEvo

> T-23 交付物之一。初赛《智能体设计思路及详细说明》的「示例问答截图 + 各素材对应评分维度」映射基准。
> 事实基准：评分四维来自 `05-计划书-初赛提交版.md` §2.4（维度1 编排与 MCP/Skill / 维度2 资产激活 / 维度3 算法创新 / 维度4 交互与沉淀，各 25%）。
> 状态标注：`✅ 已有` = 素材已存在；`🟡 待补` = 依赖截图/数字（见缺口清单）；`待续跑` = 依赖构建租户图谱摄取 → T-22 续跑。

---

## 一、总体映射表

| 素材（证据） | 对应评分维度 | 证据强度 | 来源文件 | 状态 |
|---|---|---|---|---|
| 调用关系图（Agent→Skill→MCP→服务→存储） | 维度1 | ★★★ | `docs/call-graph.md`（Mermaid） | ✅ 已有 |
| 自研 MCP 工具双注册（5 工具 + 平台 34 工具） | 维度1 | ★★★ | `docs/agent-config.md` §3；`backend/tool_collection/mcp/kg_tools.py` | ✅ 已有 |
| Skill 三层编排（入口路由 + 检索/推理/证据装配） | 维度1 | ★★★ | `docs/agent-config.md` §5；`ag_skill_info_t` 真实 4 条 | ✅ 已有 |
| 平台跑通（智能体发布/对话/模型注册截图） | 维度1/2 | ★★☆ | `deliverables/T-01-*.png`（3 张，2026-09-14） | ✅ 已有 |
| **示例问答截图 ≥3（真实问答，可回溯 decision_card_t）** | 维度2/4 | ★★★ | `deliverables/`（待拍） | 🟡 待补 |
| **本体工作台截图（本体树 + diff 三色 + 导航）** | 维度2 | ★★★ | `deliverables/`（待拍） | 🟡 待补 |
| **决策卡截图（证据链展开 + 版本戳 + 免责声明）** | 维度2 | ★★★ | `deliverables/`（待拍） | 🟡 待补 |
| **新旧指南版本对比（同题两版答案不同）** | 维度2/3 | ★★★ | `deliverables/`（待拍） | 🟡 待补 |
| **Skill 模板库页面截图** | 维度4 | ★★☆ | `deliverables/`（待拍） | 🟡 待补 |
| **评测报告图（消融柱状图/曲线）** | 维度3 | ★★★ | `deliverables/`（待生成） | 🟡 待补（依赖 E2 数字） |
| E1 纯 RAG 基线（acc=0.6667 / pass2=0.65） | 维度3（对照） | ★★★ | `deliverables/e1-baseline-report.json` | ✅ 已有 |
| E2 消融报告（A1/A2/A3/A4 × E8） | 维度3 | ★★★ | `deliverables/e2-ablation-report.json`（**partial:true，待续跑**） | 🟡 待续跑 |
| 机制预验证四探针（P1 100% vs 20.6% / P2 VOI / P3 0.02ms / P4 98.8%） | 维度3 | ★★★ | `deliverables/algorithm-probes/probe_p{1..4}_*.json` | ✅ 已有 |
| 版本钉住机制测试（TestVersionPinnedWalk） | 维度3 | ★★★ | `test/backend/services/knowevo/test_decision_service.py` | ✅ 已有 |
| 消融单测 + PG 集成（551 passed） | 维度3 | ★★★ | `pytest` 运行记录 | ✅ 已有 |
| 性能 PoC（多跳 p95=12.5ms / supersede p95=22.7ms） | 维度2/3 | ★★★ | `docs/poc-graphstore.md` | ✅ 已有 |
| 跳数标定（depth=2 饱和） | 维度3 | ★★☆ | `cost-ledger` t09-curve 行 | ✅ 已有 |
| 语料台账（58 份 + 批次核查） | 维度2 | ★★☆ | `corpus/registry.csv` + `docs/verification-reports/batch{1,2,3}-*.md` | ✅ 已有 |
| 构建租户图谱（实体/关系/证据链，摄取中） | 维度2/3 | ★★★ | `kg_graph` + `kg_evidence_t`（doc_id 链） | 🟡 摄取中（120 段） |
| 踩坑台账 44 条（调试迭代经验素材） | 维度4 | ★★★ | `docs/pitfalls.md` | ✅ 已有 |
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
| 六类官方截图（本体工作台/决策卡/示例问答≥3/版本对比/Skill 库/评测图） | docker 栈未起 | `deploy/deploy.sh --image-source mainland docker` + 按 `T-23-brief` §3 清单拍摄 | 🟡 |
| 评测报告图 | E2 partial（A3 0/20、E8 两臂 Δ=null） | 摄取 120/120 → `t22-resume.status.json` resume 四段命令 → 生成图表 | 🟡 |
| 示例问答可回溯性核验 | 截图拍摄 | psql 查 `decision_card_t`/`eval_run_t` 行对应 | 🟡 |
| T-05 e2e 截图欠账（本体树 ≥30 节点 + diff 三色） | 同上起栈 | 补拍（T-05-receipt.md:41 未勾选） | 🟡 |
| vision agent 逐张审图 | 截图就位后 | 按 `wave-plan.md:59` / vision skill 审图，结论回填本表 | 🟡 |

---

## 四、生成记录

- 首版创建：2026-09-20（T-23 无阻塞装配轮，loop-r11）
- 素材核验方式：文件存在性 + DB 真实查询（`ag_tenant_agent_t` / `model_record_t` / `ag_skill_info_t` / `ag_tool_info_t`）
- 更新纪律：每新增/重拍一张截图、每落定一个评测数字，同步更新本表状态列。
