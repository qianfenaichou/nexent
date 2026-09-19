# 知识版本台账（evolution-log · 人写叙事版）
> 机器口径在 evolution_round_t（看板/工具）；本文件是决策背景与叙事，供答辩。轮次 ID 为两边外键。

| 轮次ID | 日期 | 触发源 | 变更内容摘要 | 评测分变化 | 采纳/否决及理由（叙事） |
|--------|------|--------|--------------|-----------|------------------------|
| (待T-11起逐轮登记) | | | | | |

## T-09 版本钉住消融设计（2026-09-17，非版本轮次，登记备用）

T-09 本身不产生知识版本变更，但它交付了**版本敏感结论可复现**的机制，T-11 的每轮演进将用它取证。设计已固化为两条可执行路径：

1. **on/off 消融**：同一问题、同一图，`pin_version=True/False` 各跑一次。关闭时会把"该版本之后才生效"或"该版本前已失效"的事实一并纳入路径（实测可见：pinned 走 a→b 得"旧版"证据；unpinned 走 a→c 得"新版"证据——见 `test_decision_service.py::TestVersionPinnedWalk`）。这组对比就是"可进化"的最硬证据（02-技术方案 §3.2 要求）。
2. **失败路径留痕**：被版本裁掉的事实不进结果集，而以 `failed` + `invalid_edge_reason`（含边的时间窗与版本号）返回，卡面据此说明"证据存在但不属于所询版本"——而不是伪装成"无知识"。这让每一轮演进都能回答"这次更新否决了哪些旧结论"。

T-11 起每轮演进登记时，请附：本轮 `knowledge_stamp`（ontology_version + kg_cutoff）、V 题 pinned/unpinned 两组分数、F 题回归护栏结果。

## T-18b 事实业务时间轴（D1）修复落地（2026-09-19，非版本轮次，机制登记）

本任务不产生新的知识版本，但修复了**双时轴只剩一条轴**的结构性缺陷，是 T-11 以后每一轮演进"版本间可比"的前提：

- **修复前**：`kg_relation_t.valid_at` 全部是入库墙钟，版本 t_v 也取墙钟（`created_at`），`valid_at <= t_v` 恒真——钉住谓词放行一切，"该版本知道什么"无从谈起（pitfalls #43）。
- **修复后**：事实的 `valid_at` = 来源文档业务出版日（registry `published_at`，可溯自 license_note 期刊年卷期）；版本的 `fact_cutoff`（metrics JSONB）= 该轮文档出版日上界，`resolve_version_clock` 优先取它当 t_v。**从 T-11 起每轮演进自动获得业务时间轴上的 t_v，版本间裁剪真实发生**。
- **机制证明（判别性护栏）**：单测 `TestDiscriminativeVersionPin`（同组边、两个 t_v、纳入集合不同）+ PG 集成 `test_discriminative_version_pin_on_real_db`（真库播种：t_v=2022-01-01 纳入 1 条 / t_v=2025-06-01 纳入 2 条）。
- **真库现状（诚实口径）**：现存 369 行关系全部无证据链（eval 夹具/合成 PoC 数据），回填脚本按 `no_evidence_link` 如实保留原值，SQL 级三 count 分化待 T-19/T-22 真实语料摄取后复跑验收命令 5；30 份 registry-dated 文档的业务日期已回填进 `doc_asset_t.metadata`，后续 `commit_version` 可直接取到 fact_cutoff。
- **对 A4 消融的意义**：t_v 与 valid_at 分轴后，"关掉版本钉住会多纳入哪些事实"第一次成为可测量的 Δ；T-22 的 A4 臂执行顺序见 `docs/verification-reports/t22-readiness.md` §⑥。
## E2 消融执行器首次真跑（2026-09-19，T-22，非版本轮次，能力+数据现实登记）

T-22 交付 A1→A4 四级消融 runner + E8 版本钉住 on/off 双臂（`backend/services/knowevo/pipeline/ablation.py`），本轮首次真跑（20 题种子集 × 3 runs 协议，LLM 墙钟预算到点检查点落盘，`partial: true`）：

- **交付能力**：四级配置各落一条 `eval_run_t`（INSERT-only，`config.ablation_level` 冻结枚举）；文档通道与 A1 逐字节相同（同一生成模型与 prompt），图谱通道经 `DecisionService.assemble_evidence` 融合（冲突标 contested）；A3 多跳为 `multi_hop` 服务直调（B3），A4/E8 版本钉住走 `version_pin.resolve_version_clock` 单一入口（B4，as_of=2024-06-01 可溯两版指南出版日之间）；E8 判分按臂选金标（on 臂对 answer_old、off 臂对 answer_new，V 题 rubric 按臂收窄——机制由单测锁定）；交叉表 by_type×by_level 零判定格显式 insufficient_data；95% CI 用 Wilson（eval_v1）。判分链复用 `eval_e1._judge_once`（T-18c 口径零漂移）。
- **D1 前置校验（判别性 SQL，经 pin_predicate 复跑）**：t_v=2022:0 / t_v=2024-06:121 / t_v=2025-06:1 / all:474，判别性成立（0≠1），放行 E8；与 T-18b 预期 0/1/369 的差异为评测夹具重复播种（行数 474、其中 121 行带 2024-01-01 业务日期来自集成测试播种）。
- **数据现实（不粉饰）**：构建租户 6756b0ab 图谱为 **0 实体/0 关系**（全库 474 条关系全部属临时租户）——任务书"真库 294/524 行可用的真实图"在当前库不成立。A2/A3 的图通道空载（kg_channel 逐题记录 n_seeds=0），A4/E8 两臂收到相同证据，**E8 的 Δ 在真实语料图摄取前结构性≈0**，机制证明仍由 T-18b 判别性测试承载。
- **本轮实测（部分矩阵，全部真跑）**：A1 10题 pass2=0.70 [0.397,0.892]（与 T-18c 全量 0.65 同量级）；A2 4题 0.75；A4 headline 3题 0.3333——其中 4/9 run 为端点空内容故障（执行器欠重试，已修复为 4 次重试并如实保留故障 run）；E8 off 臂 0 题（预算耗尽），**E8 Δ 本轮不可算**。完整数字/交叉表/成本见 `docs/e2-ablation.md` 与 `deliverables/e2-ablation-report.json`（resume 字段含精确续跑命令）。
- **诚实修订**：runner_error 条目原缺 type 字段导致 by_type 分母漏计失败 run——已修复并对存量 runs 按 question_id 回填（离线重算，零 LLM 成本），报告 notes 留痕。
- 待续：四级补齐 20 题、E8 双臂重测（建议先删 A4 旧条目换用修复后执行器）、真实语料图摄取后复跑 E8 验证 Δ。


## T-20 模板沉淀首次真实落库（2026-09-19，能力沉淀台账）

`mine_skill_templates.py` 首次真跑成功，从 `decision_card_t` 真实历史卡（100 张 / 75 租户，显式 `--cross-tenant` 汇聚）归纳出 **2 个参数化 SKILL.md 模板**，落 `skill_template_t`（租户 6756b0ab，构建租户）：

| 模板 | task_type | support | induced_by | 来源 |
|---|---|---|---|---|
| reasoning_decision-general | reasoning_decision | 50 | llm | 100 卡中 50 张推理决策卡的模式（mine-66f9eddc） |
| refusal-general | refusal | 50 | llm | 100 卡中 50 张拒答卡的诚实降级模式 |

- **LLM 通道**：mid=deepseek-v4-flash / large=glm-5.2（sensenova），2 次调用 1652+9096 tokens，cost-ledger 行 mine-66f9eddc；首次尝试因管线默认租户无 LLM 配置诚实降级为确定性骨架（0 次调用，行 mine-d3f9b505）——模型配置是**租户作用域**的（pitfalls #44）。
- **复用闭环已验证**：`apply_template` 实例化 reasoning_decision-general（domain=t2dm）成功渲染 SKILL.md，`reuse_count=1`、`reuse_success=1`（真库 psql 为证）。
- **待集成轮**：`skill_template_apply` MCP 注册、kw_007 RBAC、前端 /skillTemplate 页、SKILL.md 原生上传验证（spike 结论见 `docs/skill-mechanism.md`，frontmatter 白名单 name/description/allowed-tools/tags/script_outputs）。
