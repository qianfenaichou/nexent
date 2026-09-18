# 作战波次与任务简报索引（2026-09-18 调度会话）

> 依据：用户任务书「作战波次」节 + [01-总纲](../../../../archive/已取代文档/01-总纲.md) §4 评分映射 + [03-开发计划](../../../03-开发计划.md) §5 质量门禁。
> **本文件是波次执行的唯一索引**；每个任务的简报在 `competition/tasks/T-*.md`。
> 铁律：不重构架构；新代码只在自包含目录；接线文件由专门任务独占；每个任务一条 `feat/kw-TNN-*` 分支，验收命令真实跑过才算完成；不得伪造数字。

---

## 波次表（按 ROI 排序，波内可并行）

| 波次 | 任务 | 标题 | 独占文件（摘要） | Blocked by | 状态 |
|---|---|---|---|---|---|
| 0 | T-10a-2 | E1 基线提交+合回 develop | — | — | ✅ 完成 |
| 0 | **T-18a** | D5 三件套：导航 / active 端点 / g6 | SideNavigation.tsx、knowledge_graph_app.py、package.json、kw_004 迁移、locales | 无 | ★ 进行中 |
| 1 | **T-18b** | D1 事实业务时间（valid_at 显式写入） | knowevo_db.py、ingest_service.py、kg_service.py、graph_store.py、version_pin.py、registry.csv、kw_004 迁移 | T-18a（同迁移文件序） | 待 |
| 1 | **T-18c** | D2 诚实分母 + D3 答案级溯源 | pipeline/eval_e1.py、pipeline/eval_v1.py | 无 | 待 |
| 1 | **T-18d** | D4 权威度感知检索 + 每文档配额 | e1_retrieval.py | 无 | 待 |
| 1 | **T-19** | 决策卡生产入口 + 对话渲染 | decisionCard feature、knowledge_graph_app.py 路由、SideNavigation（决策卡项）、locales | T-18a | 待 |
| 2 | **T-20** | Skill 分层编排 + SKILL.md 模板库（D6） | skill 相关新服务/工具、skill_template 服务、frontend skillTemplate feature | T-18a | 待 |
| 3 | **T-21** | 标准对齐器垂直切片（创新主轴） | alignment_service.py、pipeline/diff_guidelines.py、prompt 双语对 | T-18b | 待 |
| 3 | **T-22** | 消融 A1-A4 × 题型（D1 前置） | pipeline/ablation.py、e1_retrieval 复用 | T-18b/c/d | 待 |
| 4 | **T-23** | 交付物取证与装配 | deliverables/*、competition/docs/* | 多数完成 | 待 |

**不做的**：T-14 资产看板（冻结砍序第一）、T-15 政务迁移（除非 1-4 全完）。

---

## 六个洞 ↔ 任务映射

| 洞 | 优先级 | 症状 | 修复任务 |
|---|---|---|---|
| D1 | P0 | valid_at 是入库墙钟时间，与本体版本 created_at 不可区分 → A4 消融 Δ≈0 | T-18b |
| D2 | P0 | eval_e1.py:397-399 非平台异常 return None,None → 上层标 platform_fault 移出分母，M-003 整题消失、by_type.M.acc=1.0 假象 | T-18c |
| D3 | P1 | trace_completeness 测 BM25 字段填全率（恒 1.0），非答案级溯源 | T-18c |
| D4 | P1 | BM25 丢 authority_level、无每文档配额 | T-18d |
| D5 | P1 | 决策卡无生产入口；本体工作台无导航；GET /ontology/versions/active 缺失；g6 不在 package.json | T-18a + T-19 |
| D6 | P1 | Skill 分层编排与模板库零代码（官方任务3/4 点名） | T-20 |

---

## 评分维度 ↔ 波次（每做一件事先问：进哪一维？证据是什么？）

| 维度 | 波次贡献 | 证据形态 |
|---|---|---|
| ① 编排与 MCP/Skill 深度集成 | T-19（决策卡工具链）、T-20（Skill 分层） | 调用关系图、MCP 工具清单、Skill 目录树 |
| ② 资产激活业务价值 | T-18b/d（检索质量）、T-21（版本演进） | N→M→K 漏斗、新旧指南对比截图 |
| ③ 算法创新性 | T-18b（版本钉住前置）、T-21（对齐器）、T-22（消融） | 形式化定义 + 消融数字 + 人审预算曲线 |
| ④ 交互体验与能力沉淀 | T-18a（导航）、T-19（决策卡面板）、T-20（模板库） | 界面截图、SKILL.md 模板、复用统计 |

---

## 并行 agent 分工（禁改对方文件）

| agent | 职责 | 触发 |
|---|---|---|
| 实现 agent | 每任务一个，按简报独占文件 | 任务开工 |
| code-review agent | 每任务收尾一轮，按 `~/.agents/skills/code-review/SKILL.md`，报告 P0/P1/P2 | 任务验收后 |
| 论文 agent | arXiv API 反证搜索，输出「主张收窄稿 + 对比表」 | 波次 2/3 期间后台 |
| vision agent | 审 `competition/deliverables/` 每张截图，判「作为初赛示例问答截图够不够格」 | T-23 |
| 42crunch | API 安全清单（静态），新端点/MCP 工具上线前过一遍 | T-18a/T-19/T-20 验收前 |
| agent-sdk-verifier-py | **不适用**（项目用 smolagents，无 OpenAI Agents SDK） | — |
