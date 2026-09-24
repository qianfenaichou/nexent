# 实验报告集（E0 / E2 / PoC / τ_a）

> 2026-09-23 文档整合轮：由原 `e0-baseline.md`、`e2-ablation.md`、`poc-graphstore.md`、`tau-a-autonomy.md` 四份合并（各部分标题降级为节，内容未改一字）。**数字权威源仍是 `deliverables/` 下 JSON**，本文是叙事版；E2 终版以 `t22-resume.status.json` 四段命令跑完后的回填为准。

---

## 第一部分 · E0 基线

> 原文件：`docs/e0-baseline.md`（已并入本文件）

E0 微基准基线（T-01 遗留回收 · T-02 落档 · 2026-09-14）

> 回收遗留值：L1（模型基线质量）/ L2（延迟分位）/ L7（token 成本）。
> 题集：20 题四级分布（F 事实 5 / M 多跳 5 / V 版本敏感 5 / X 迁移泛化 5），见 [e0-questions.md](../corpus/e0-questions.md)。
> 双档模型：均 glm-5.3-free @ tokenrouter（主档 Key / 小档 Key，同一模型名、独立账户额度——注：两档实际是同一底座模型，
> 分档差异来自账户限流与并发配额，非模型能力差异；正式双档（强/弱异构）待 T-08 接线后补测）。
> 判分：关键词 any-match（判分词表内嵌于 e0_runner.py，可审计）。

### 最终轮数据（2026-09-14 第三轮，429 退避 + 空响应追踪）

| 档位 | 应答/20 | 命中/20 | 命中率 | 平均延迟 | 总 tokens |
|------|---------|---------|--------|----------|-----------|
| 主档 glm-5.3-free | 19 | 9 | **45%** | 24.78s | 10,310 |
| 小档 glm-5.3-free | 16 | 13 | **65%** | 36.80s | 13,102 |

#### 差异原因（如实记录）
- 小档 4 题未应答：1 超时 + 3 次 HTTP 500（tokenrouter 服务端错误，与早前 embedding 403 额度耗尽同期的服务不稳定）
- 主档 1 题超时；两档共有 ~40% 概率返回**空响应**（content 空、tokens≈问题本身），免费档特性
- 命中率反转（小档>主档）主因是主档空响应率更高，**不是能力差异**——同模型同参，两档差异是噪声
- 结论：E0 作为"裸模型基线"成立；知识库接入后（T-06/T-10）同题重测才是真正的效果对照

### 轮次历史（数据稳定性参考）

| 轮次 | 主档 | 小档 | 备注 |
|------|------|------|------|
| 第 1 轮（无退避） | 35%（20/20 应答） | 5%（3/20，17 题 429） | 小档无退避被限流击穿 |
| 第 2 轮（小档退避重跑） | — | 25%（18/20，2 超时） | 空响应未单独标记 |
| 第 3 轮（终版） | 45% | 65% | 见上表，判分修正为 any-match |

### 分题型命中（第 3 轮合计）

| 题型 | 主档命中 | 小档命中 | 观察 |
|------|----------|----------|------|
| F 事实 | q1/q3/q4（3/5） | q1/q3/q4/q5（4/5） | 数字型事实（OGTT 阈值）空响应多 |
| M 多跳 | q8/q10（2/5） | q6/q7/q9/q10（4/5） | 机制类问题表现最好 |
| V 版本敏感 | q13/q15（2/5） | q13/q14（2/5） | 两档均弱——正是 RAG 知识库要解决的（指南版本内容裸模型不可靠） |
| X 迁移泛化 | q16/q18（2/5） | q16/q17/q20（3/5） | 临床决策类延迟最高（主档 q18 145.9s/3093 tokens） |

### 延迟与成本（L2/L7 回收）

- 主档延迟分布：0.5s（空响应）～ 145.9s（q18 复杂决策题），有效应答平均 ~35s
- 小档延迟分布：0.5s（空响应）～ 91.0s，有效应答平均 ~48s
- glm-5.3 是推理型模型（含 reasoning_content），延迟与 token 消耗显著高于非推理档
- Token 成本：40 次调用 23,412 tokens（合计），折合 ¥0（免费档）；正式成本基线待 T-08 双档异构模型接入后重测

### 运行方式（复现）

```bash
## Keys 从 Nexent 模型配置读出（不硬编码）
KW_E0_KEY_PRIMARY=<key1> KW_E0_KEY_SMALL=<key2> \
  python3 competition/corpus/e0_runner.py
## 原始逐题数据：/tmp/e0_final.json（随 Evidence 归档为 e0_raw_round3.json）
```

### 已知局限（答辩合规声明）
1. 双档当前为同模型不同 Key——分档对比不构成模型能力差异证据
2. 判分为关键词 any-match，非语义判分（T-10 的原生 LLM 评测器将替换）
3. 免费档空响应/429/500 是平台稳定性问题，E0 数据取的是"可用应答"子集的命中率上限估计


---

## 第二部分 · E2 消融

> 原文件：`docs/e2-ablation.md`（已并入本文件）

E2 消融：A1→A4 × 题型 + E8 版本钉住 on/off（T-22 实测报告）

- 任务：T-22（`competition/tasks/T-22-brief.md`）；执行器：`backend/services/knowevo/pipeline/ablation.py`
- 日期：2026-09-19；分支 feat/kw-T18b-fact-time
- 测评集：`competition/corpus/testset-v1-seed.json`（20 题种子集，F/M/V/X 各 5；hash 见报告 JSON `testset_hash`）
- 协议：每题 3 次重复（K4），主指标 pass^2，同时报 pass^3 / acc / **n_judged** / trace_machine / trace_answer / p95 延迟 / token；95% CI 用 Wilson 区间（`eval_v1.wilson_interval`）
- 判分：复用 T-18c 冻结的五元 judge 链（`eval_e1._judge_once`，judge 模型 large 档，与生成模型不同族）；`count_fail` 契约——非平台故障与端点失败一律 pass=0 留在分母，不静默排除
- 原始逐题数据：`competition/deliverables/e2-ablation-report.json`（只含真跑数字；`partial: true` 表示预算内未全部完成，续跑命令在 `resume` 字段）

### 0. 数据现实（先读这个，不要跳过）

**构建租户的图谱是空的。** 2026-09-19 真库勘察（psql 实测）：

| 项 | 值 |
|---|---|
| `kg_relation_t` 全库 | 474 行（**全部**属于临时评测/集成测试租户，每租户 3-4 行） |
| 构建租户 6756b0ab（语料/评测所在） | 实体 **0**、关系 **0** |
| 业务时间分化 | valid_at≤2024-06-01 共 121 行（PG 集成测试播种）；min(valid_at)=2024-01-01 |
| `ontology_version_t` | 仅 v1.0.0（2026-09-18 提交，**无 fact_cutoff**） |
| `doc_asset_t`（构建租户） | 58 份语料文档完好（T-18b 已回填业务出版日） |

任务书背景"真库 kg_relation_t 294 行、kg_entity_t 524 行——A2/A3/A4 有真实图可用"**在当前库上不成立**（这些行属于临时租户；带证据链的真实语料摄取按 T-18b Evidence 规划归 T-19/T-22 后续真实摄取）。因此：

> **2026-09-24 口径校正（任务Q6）**：上表为 **2026-09-19 快照**，保留不改。真库（docker 容器 `nexent-postgresql`）现状实测：**构建租户 `6756b0ab`** = 摄取段 **46/120**、实体 **357** / 关系 **294** / 证据 **70**、`doc_asset_t` **58**；**全库合计**（含 150 个评测租户 + 集成测试夹具）= `doc_asset_t` 508 / `kg_entity_t` 1594 / `kg_relation_t` 948 / `kg_evidence_t` 154。两套 PG 的身份区分见 `00-索引与状态.md` §四·补；完整命令与原始输出见同文件 §八 任务Q6。

- **A2/A3 的图谱通道在本轮真跑中检索为空**（kg_channel.n_seeds=0/n_edges=0，逐题记录在报告 JSON，不静默）；
- **A4 与 E8 的两臂收到完全相同的证据（仅文档通道）**，A4 vs A2/A3 的差只剩路由器+卡片渲染的开销与其自身的模型抖动；
- **E8 的 Δ 在真实语料图摄取前不可观测**——这不是"钉住无效"，而是"钉住的对象尚不存在"。机制证明由 T-18b 的判别性护栏承载：单测 `TestDiscriminativeVersionPin` + PG 集成 `test_discriminative_version_pin_on_real_db`（真库播种：t_v=2022 纳入 1 / t_v=2025-06 纳入 2）。

### 1. D1 前置校验（T-18b 判别性 SQL，跑消融前实测）

runner 启动时经 `version_pin.pin_predicate`（单一谓词入口）复跑判别性计数，结果随报告落库：

| clock | 纳入事实数 |
|---|---|
| t_v=2022-01-01 | **0** |
| t_v=2024-06-01（E8 默认钉住点） | **121** |
| t_v=2025-06-01 | **1** |
| all（无谓词） | **474** |

两个 t_v 计数不同（0≠1）→ **D1 判别性成立，放行 E8**。与 T-18b 简报预期 0/1/369 的差异：全库行数已变为 474（评测夹具重复播种），判别对 0≠1 不变；121 行 2024-01-01 业务日期行来自集成测试播种。诚实结论不变：SQL 级三 count 的分化（期待"真实语料摄取后 t_v 两值纳入数显著不同"）仍待带证据链的真实摄取；当前差值 0 vs 1 对 474，**对 E8 的 Δ 而言等于没有可裁剪的图事实**。

### 2. 四级配置（02-技术方案 §3.5）

| 级 | 配置 | 实现路径 |
|---|---|---|
| A1_pure_rag | 纯文档检索（BM25 本地语料，top-k=5） | `eval_e1.run_question` 原样复用（T-10a-2 口径零改动） |
| A2_graph | +kg_search 1 跳邻域 | `store.entity_lookup`（确定性种子词）→ `store.neighbors(hop=1)` → 组装 PathSet → `DecisionService.assemble_evidence` 与文档通道融合，冲突标 contested |
| A3_multihop | +多跳束搜索 | 服务直调 `DecisionService.multi_hop`（depth=2, beam=3，current view；B3：不走 MCP，因 MCP kg_search 无版本语义） |
| A4_full | +路由 +双通道融合 +反事实卡片 +版本钉住 | `route_async`（L1/L2/L3）→ 双通道 → `assemble_evidence` → `render_card`（卡片答案=首选候选+依据；证据不足时确定性拒答）→ 版本钉住走 `version_pin.resolve_version_clock` 单一入口 |

关键约束的落实：

- **同一生成模型与 prompt**：A1/A2/A3 的文档上下文逐字节相同（同一 `retrieve_context` 渲染），生成走同一 E1 prompt（mid 档）；A4 的"生成"即卡片渲染（decision-card prompt），这是该级的定义本身，报告中如实区分。
- **版本钉住单一开关（B4）**：pin-on 臂 `resolve_version_clock(None, as_of=2024-06-01)`（source=explicit，可溯：guide-2020 出版 2021-04-01 / guide-2024 出版 2025-01-01 之间），`multi_hop(as_of=..., pin_version=True)`；pin-off 臂 `pin_version=False`。无自造谓词。
- **判分按臂选金标（E8 执行器扩展）**：显式 `--pin on,off` 运行中，pin-on 臂对 `answer_old`、pin-off 臂对 `answer_new` 判分（V 题 rubric.key_facts 按臂收窄，如 V-004 on 臂只留"宽松"）；F/M/X 金标不分版，原样判分。headline 单臂运行用标准 rubric，保证交叉表 A1-A4 可比。
- **诚实分母**：平台故障排除并单独计数；runner 错误（含卡片渲染失败、端点 429 耗尽）按 count_fail 记 pass=0 留在分母；每级报 `integrity_warning`；零判定题型格显式 `insufficient_data`。
- 卡片渲染 token：DecisionService 的 llm 契约只回 str，经 `_CountingLLM` 包装器走 `call_with_usage` 计量——账本只记实测，不估算。

### 3. 结果（真跑数字）

**本轮为预算内部分完成**（LLM 真跑墙钟 ~57 分钟到点检查点落盘；`partial: true`，续跑命令见报告 `resume` 字段与 status.json）。所有数字来自真跑（sensenova 端点，mid=deepseek-v4-flash / judge large=glm-5.2，构建租户 6756b0ab，pace=8s），逐题 runs/details 见报告 JSON。

#### 3.1 实测矩阵（每格 acc / n_judged；pass^2 为主指标）

| 级 | 已测题数 | acc | pass^2（95% CI Wilson） | n_judged/n_expected | trace_machine | p95 延迟 | tokens (in+out) | eval_run_t |
|---|---|---|---|---|---|---|---|---|
| A1_pure_rag | 10/20 | 0.7241 | 0.70 [0.397, 0.892] | 29/30 | 1.0 | 67.11s | 88657+70251 | c80dfa38 |
| A2_graph | 4/20 | 0.7500 | 0.75 [0.301, 0.954] | 12/12 | 1.0 | 48.62s | 33709+24048 | 1b98defc |
| A3_multihop | 0/20 | —（未跑到，预算检查点） | — | 0/0 | — | — | 0 | 59b71408（空行） |
| A4_full（pin=on，headline） | 3/20 | 0.3333 | 0.3333 [0.062, 0.792] | 9/9 | 1.0 | 96.03s | 20005+32954 | edcad253 |
| E8 A4 pin=on（按臂金标） | 2/10（V,F） | 0.5000 | 0.50 [0.188, 0.812] | 6/6 | 1.0 | 137.02s | 18810+45410 | a35a6e73 |
| E8 A4 pin=off（按臂金标） | 0/10 | —（未跑到） | — | 0/0 | — | — | 0 | eacba193（空行） |

#### 3.2 by_type × by_level 交叉表（当前实测覆盖；零判定格显式 insufficient_data）

| 题型 | A1_pure_rag | A2_graph | A3_multihop | A4_full (pin=on) |
|---|---|---|---|---|
| F | acc 0.7857（n_judged=14） | acc 0.7500（n_judged=12） | insufficient_data | acc 0.3333（n_judged=9） |
| M | acc 0.6667（n_judged=15） | insufficient_data | insufficient_data | insufficient_data |
| V | insufficient_data | insufficient_data | insufficient_data | insufficient_data |
| X | insufficient_data | insufficient_data | insufficient_data | insufficient_data |

（预算检查点把每级都停在了 F/M 题段，V/X 尚未进入实测——交叉表如实以 insufficient_data 呈现，不缩格、不消失。）

#### 3.3 必须与数字一起读的三个事实

1. **A4 的 9 个 run 里有 4 个是端点空内容故障**（F-002×1、F-003×3，"card render runner error: LLM output was not a JSON object; got: ''"——E0 已知的免费网关高负载空返回病理），按 count_fail 契约记 pass=0 留在分母。执行器最初只重试 1 次，**低于 E1 平台故障 6 次重试纪律**；本轮跑完后已修复为空内容重试 4 次（`_CountingLLM`），**已测的 4 个故障 run 如实保留**（可经 eval_run_t/cost-ledger 追溯），续跑题目将使用修复后执行器。因此上表 A4 的 0.3333 混入了执行器欠重试期的端点故障，**不等于 A4 管线的判分能力**；E8 on 臂同样含 2 个此类故障。建议续跑时对 A4/E8 臂做"删除条目重测"（报告 `resume` 字段步骤 1 给出精确命令），A1/A2 数字干净可续。
2. **图通道全程为空**（kg_channel.n_seeds=0，构建租户无图——见 §0），A2 的 0.75 实际上测的是"A1 通道 + 空图查询开销"，与 A1 的差异主要是题目子集不同（4 题对 10 题），**不能解读为图检索增益**。
3. A1 的 1 个 judge_unparseable 故障（F-003 run）按平台故障排除在分母外并单独计数（n_judged=29/30 的差值）。

### 4. E8 版本钉住 on/off

**本轮未产出可用的 Δ**，两个原因都已在 §0/§1 预告，此处如实登记：

| 臂 | 覆盖 | F 题分 | V 题分 |
|---|---|---|---|
| pin=on（对 answer_old 金标判分） | F-001、F-002（2 题 6 run） | acc 0.5（n_judged=6，含 2 个端点空内容故障） | insufficient_data（未跑到） |
| pin=off（对 answer_new 金标判分） | 未跑到（on 臂用尽预算） | insufficient_data | insufficient_data |
| Δ(on − off) | — | **null（无法计算）** | **null** |

- **数据现实**：构建租户图为空，两臂即使都跑到，图谱通道也收到完全相同的（空）证据，钉住对生成输入的裁剪为零——E8 的 Δ 在真实语料图摄取前**结构性地≈0**，本轮连"结构性≈0"都未测满（off 臂 0 题）。
- **判分按臂选金标机制已实现并经单测锁定**（V-004 on 臂 rubric 收窄到"宽松"、off 臂保留全量，`test_ablation.py::TestArmItem/TestArmGoldJudging`），机制就绪、数据未到。
- **机制证明的现行承载**：`TestDiscriminativeVersionPin`（单测）+ `test_discriminative_version_pin_on_real_db`（真库播种 t_v=2022 纳入 1 / t_v=2025-06 纳入 2）+ 本报告 §1 判别性 SQL（0/121/1/474）。

### 5. 结论与预期对照（诚实口径）

1. **A4 vs A1 pass^2 Δ**：本轮无法给出——A4 仅 3 题（且 4/9 run 为端点故障），与 20 题协议要求差得远；02-技术方案 §3.5 预期 **+10pp**，本轮**既未证实也未证伪**。A1 自身部分基线：pass^2=0.70 [0.397, 0.892]（10 题），与 T-18c 全量口径的 0.65（20 题）同量级，端点健康时基线可复现。
2. **溯源 +30pp 预期**：trace_machine 各级均为 1.0（文档通道 locators 完整）；图谱通道空载，trace_answer 的引用范围检查零违规——"图谱证据链增益"同样待真实摄取后复测。
3. **延迟**：A4 p95 96s ≈ A1 67s 的 1.4×（部分数据、含故障重试，方向上与"延迟 +2×"预期一致但未达）。
4. **本轮真正确立的交付**：四级 runner + E8 双臂 + 按臂金标判分 + 交叉表/insufficient_data/Wilson CI 的完整执行器（29 项编排单测锁定），D1 判别性校验入报告，成本入账 6 行。执行器就绪、语料图未就位——这就是本轮的诚实结论，续跑命令已就位（报告 `resume` + status.json）。

### 6. 成本入账（E2 本轮）

cost-ledger.md 6 行（run id 与 eval_run_t 一致）：

| run | input_tok | output_tok | 说明 |
|---|---|---|---|
| e2-c80dfa38 | 88657 | 70251 | A1 10题×3runs |
| e2-edcad253 | 20005 | 32954 | A4 headline 3题×3runs |
| e2-a35a6e73 | 18810 | 45410 | E8 on 臂 2题×3runs |
| e2-eacba193 | 0 | 0 | E8 off 臂 0题（检查点） |
| e2-1b98defc | 33709 | 24048 | A2 4题×3runs |
| e2-59b71408 | 0 | 0 | A3 0题（检查点） |

合计 **161,181 input + 172,663 output tokens**（另冒烟 2题×1run×4级 ≈ 4 万 tokens 未单列，--no-persist）。

### 附录 A：执行与复现

```bash
## 四级 headline（每级一条 eval_run_t；可断点续跑：重跑同命令自动跳过已完成 pair）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> KW_LLM_SMALL_MODEL_ID=7 KW_LLM_MID_MODEL_ID=7 KW_LLM_LARGE_MODEL_ID=8 \
  uv run python -m services.knowevo.pipeline.ablation --levels A1,A2,A3,A4 --runs 3 --top-k 5 --pace 8

## E8：A4 pin on/off × V,F（判分按臂选金标）
cd backend && ... uv run python -m services.knowevo.pipeline.ablation --levels A4 --pin on,off --types V,F --runs 3

## 单测（不依赖真 LLM）
cd backend && uv run pytest ../test/backend/services/knowevo/test_ablation.py -q --no-header
```

### 附录 B：实现备注与坑

1. `graph_store.PgJsonbGraphStore`（版本钉住走它）与 `kg_service.PgStore`（本体快照在它上面）是两个类——hop 规划的本体词表需从后者加载，缺失时诚实降级为无过滤走图（逐题 errors 可见）。
2. `assemble_evidence` 的 contested 标记依赖 `edges_by_path`——A2 手工组装的 1 跳路径必须以 PathSet（含 claims_by_path/edges_by_path）形状喂入，直接传 Path 列表会丢掉边的 contested 标记。
3. 种子词必须是**实体名的子串**（store 的 ILIKE 是 name CONTAINS query）：CJK 长run 用 3 字滑窗（步长 2）+ 全 run，ASCII 词原样；上限 12 个查词/题，确定性可复现。
4. 卡片渲染的 llm 契约只回 str（token 不可见）——用 `_CountingLLM` 包装器过 `call_with_usage` 计量；非 JSON 卡片输出重试 1 次（模型抖动），仍失败按 count_fail 记 runner_error 留分母。
5. pin=None（A1-A3）与 pin=off 语义不同：`pin_on = (pin == "on")` 而不是 `!= "off"`——后者会让 A3 意外被钉住（冒烟 dry-run 抓到）。

### 附录 C：续跑增记（2026-09-19 下午，主智能体）

- **A4 headline 重测完成**（e2-17ce73d5，47,769 in + 129,556 out tokens）：重试修复生效——2 题 × 3 runs 全部判定（n_judged 6/6，**零 runner_error**），acc=0.5。报告 JSON 的 A4 段已替换（旧 3 题含 4 条网关空内容污染，DB 行保留作台账，eval_run_t 保持 INSERT-only）。
- **ablation.py 两个续跑安全修复**：预算空跑不再覆盖 prev 条目/不再插空行；报告 header 跨 invocation 保留最宽 scope。单测 31 passed。
- **E8 全量补跑未完成（诚实记录）**：13:2x 两段 8 分钟预算运行**零新 eval_run_t 行**——今天端点劣化（>50% 空内容响应，每次重试 ~70s），单段预算内连一道题的 3 runs 都无法完成。结合「构建租户图谱为空 → 图通道空载 → E8 Δ 结构性≈0」的既定数据现实，**继续微观补跑性价比为负**，全量 E8 矩阵挂起，解锁路径=构建租户图谱摄取（见 .task_b_status/ingest-feasibility.md：pdftotext + batch.json 轻量路径，定向 ~60 chunk ≈ 0.3-0.5M tokens，**待用户授权成本**）。
- 现状结论不变：机制证明（判别性谓词分得开事实）由 T-18b 单测 + PG 集成测试承载；真实图谱数字待摄取后由断点续跑补齐（命令见附录 A，runner 自动跳过已完成 pair）。


---

## 第三部分 · 图存储 PoC

> 原文件：`docs/poc-graphstore.md`（已并入本文件）

T-07a PoC 基准报告（A1 图存储）

> 日期：2026-09-17 · 数据：`gen_synthetic_graph` 合成图（seed=42，确定性）· 库：本地 PG 15（supabase-db-mini 5436，knowevo_test）
> 目标（备忘录 09 §3）：P1 多跳 p95<1.5s @2万/3万 · P2 批量 supersede p95<200ms

### 结果

| 探针 | 目标 | 实测 | 判定 |
|---|---|---|---|
| P1 多跳（beam=3, depth=3, 20 seeds） | p95 < 1500ms | **p95=12.5ms**（mean 8.2ms） | ✅ 快 ~120× |
| P2 批量 supersede（100 边/批） | p95 < 200ms | **p95=22.7ms** | ✅ 快 ~9× |
| upsert 2 万实体 | — | 79.4s（逐行 session） | ⚠️ 见下 |
| upsert 3 万边 | — | 120.2s（逐行 session） | ⚠️ 见下 |

### 环境与方法

- 宿主：本机（Linux），PG 15.8 容器 supabase-db-mini，`nexent` schema 13 表由迁移 001+002 建。
- 图：20,000 实体（15 类均衡）/ 30,000 边（8 种 rel_type 均衡），随机优先附着保持连通。
- 多跳：`PgJsonbGraphStore.multi_hop(seeds, HopPlan(), beam=3, depth=3)`，20 个随机种子，每个测一次，p95 取排序后 95 分位。
- supersede：`supersede(edge_ids, now, reason)`，每批 100 条。

### 结论

1. **查询路径无瓶颈**：多跳与 supersede 都在毫秒级，预留 100× 余量给 T-09 的 PPR/embedding 打分。PG JSONB + 索引方案（备忘录 09 §5 决策）维持。
2. **写入路径是短板**：upsert 逐行开 session（~4ms/行），2 万实体需 80s。量级翻倍（T-02 58 份语料真实抽取规模远小于此）可接受；若 T-10 评测批量灌图需提速，改 `bulk_save_objects` + 单 session（预计 10×）。这是 T-07b/T-08 的优化项，登记待办。
3. **集成测试门控已真跑**：`RUN_POSTGRES_INTEGRATION=1` 全 knowevo 131 测试在真实 PG 通过——T-06 之前 3 个集成测试从未在真库跑，本次抓出并修复 1 个真实 bug（LLM `new` 裁决被误降级 pending_review，见 pitfalls #25）。

### 复现

```bash
docker start supabase-db-mini
docker exec supabase-db-mini psql -U supabase_admin -d supabase -c "CREATE DATABASE knowevo_test" 2>/dev/null || true
docker exec -i supabase-db-mini psql -U supabase_admin -d knowevo_test -v ON_ERROR_STOP=1 < deploy/sql/migrations/v2.5.5_kw_001_knowevo_core.sql
cd backend
POSTGRES_HOST=localhost POSTGRES_PORT=5436 POSTGRES_USER=supabase_admin NEXENT_POSTGRES_PASSWORD=Huawei123 POSTGRES_DB=knowevo_test RUN_POSTGRES_INTEGRATION=1 uv run pytest ../test/backend/services/knowevo/ -q
POSTGRES_HOST=localhost POSTGRES_PORT=5436 POSTGRES_USER=supabase_admin NEXENT_POSTGRES_PASSWORD=Huawei123 POSTGRES_DB=knowevo_test uv run python -m services.knowevo.pipeline.gen_synthetic_graph --entities 20000 --edges 30000
```


---

## 第四部分 · τ_a 半自动构建效率

> 原文件：`docs/tau-a-autonomy.md`（已并入本文件）

半自动本体构建效率实验：τ_a 扫描（人工干预比例 vs 放行错误率）

> P1-4b（2026-09-23）。产物：`deliverables/algorithm-probes/probe_p6_autonomy_tau.json`（探针 `experiments/probe_p6_autonomy_tau.py`，零 LLM / 零 DB / 零网络、可反复跑、<1s）。
> 数字与 JSON 逐位一致；本文件是叙事版，权威数字以 JSON 为准。engine=**backend**（直接调用生产版 `build_ev_rich`，非重推导）。

### 1. 问题与口径

「低资源本体半自动构建」的效率卖点是：**设好 auto_accept 阈值 τ_a（现网默认 0.85），大部分候选自动通过，人只审剩下的**。本实验回答两个问题：

1. τ_a 对**人工干预比例**的真实杠杆有多大？
2. 被放行候选的**错误率**是多少（放行质量）？

重放对象 = 既有真实构建记录（候选池 n=21）：

| 池成分 | n | confidence | 证据 | 说明 |
|---|---|---|---|---|
| 构建租户 v1.0.0 构建操作（10 类 + 10 关系） | 20 | CLS=1.0；REL=无分数 | CLS=单文档（seed-bootstrap）；REL=空 | `ontology_version_t.applied_ops` 冻结导出 |
| 跨租户队列见证（cls:Metformin） | 1 | 0.9 | 单文档（d1） | `ontology_change_proposal_t` 唯一行，status=pending，单独标注 |

**重放门 = 生产三条件 AND 门**（`ontology_service.py:614-627`）：`conf ≥ τ_a ∧ status≠rejected ∧ ev_rich==1.0`；其中 `build_ev_rich`（`ontology_service.py:115-118`）= 证据跨 **≥2 个不同文档** 才为 1.0，否则 0.6。

**金标双轨（分开报，不混用）**：

- **裁决金标**（人工 confirm/reject 记录）：`ontology_change_proposal_t` 已裁决行 **n=0**（唯一行还在 pending）→ 该轴 **`insufficient_data`**，不产出错误率。
- **存活代理金标**：操作 target 存在于 v1.1.0 现行快照（10 类 + 10 关系）→ 记非错。20/20 存活、0 丢失；跨租户见证项无金标（不进错误率分母）。

### 2. 曲线结果（两个对照臂）

**arm A（按生产现状原样重放）**——人工干预比例：

| τ_a | 0.50 | 0.65 | 0.85（现网） | 0.95 |
|---|---|---|---|---|
| 人工干预比例 | **1.0** | **1.0** | **1.0** | **1.0** |

**全程 100%，与 τ_a 无关**。原因在绑定分解（τ_a=0.85）：`ev_rich` 拒绝 **21/21**（每个候选的证据都只跨 1 个文档或为空）、`conf` 拒绝 10/21（REL 操作无分数，门内按 0.0 计）、`rejected` 拒绝 0。**三条件门里轮不到 τ_a 生效**——单文档证据的候选在任何阈值下都进人工。

**arm B（反事实：放开 ev_rich，隔离 τ_a 自身杠杆）**：

| τ_a | 0.50 | 0.70 | 0.85（现网） | 0.90 | 0.95 |
|---|---|---|---|---|---|
| 人工干预比例 | 0.4762 | 0.4762 | 0.4762 | 0.4762 | **0.5238** |
| 放行错误率（存活代理） | 0.0（0/10） | 0.0（0/10） | 0.0（0/10） | 0.0（0/10） | 0.0（0/10） |
| 错误率 Wilson 95% | [0.0, 0.2775] | [0.0, 0.2775] | [0.0, 0.2775] | [0.0, 0.2775] | [0.0, 0.2775] |

放开 ev_rich 后 τ_a 扫完整个 [0.50, 0.95] 网格只移动 **+0.0476**（10/21 → 11/21）：候选分数呈 **三簇离散**（1.0 / 0.9 / 无分数），阈值在该池上没有分辨率。

**错误率读法（诚实）**：存活代理下 0/10、Wilson 95% 区间 **[0.0, 0.2775]**——这是「n=10 时零错的统计边界」，**不许读成「错误率 <3%」**；且存活代理只捕捉『被后续版本丢弃』类错误，仍存活的错误捕捉不到。裁决金标轴维持 `insufficient_data`。

### 3. 结论

1. **当前池上 τ_a 的边际收益 ≈ 0**：干预比例的瓶颈是 `ev_rich`（证据跨文档聚合），不是阈值。想提高自动化率，杠杆按序是：①证据聚合（让候选跨 ≥2 文档）→ ②REL 候选补分数（否则永远进人工）→ ③最后才是调 τ_a。
2. 即便 ev_rich 达标，τ_a 从 0.50 扫到 0.95 也只换到 ~5pp 干预位移——**分数分布不拉开（三簇离散）时，阈值调优是伪工作**。
3. 放行质量轴目前只能给统计边界（0/10，上界 0.2775），**不宣称低错误率**。

### 4. 限制（如实）

- n=21、单域（2 型糖尿病）、单租户主池 + 1 跨租户见证；曲线是台阶不是连续。
- as-built 事实：构建租户提案队列历史 **0 行**（种子层直接提交，未走队列）——本实验是 as-if 重放，不是运行日志。
- 存活代理金标只捕捉「被丢弃」类错误；裁决金标 n=0。

### 5. 升级路径（需要什么数据）

1. 本体工作台待审队列积累人工裁决（confirm / reject + `reject_reason`）→ 裁决金标 n>0，探针可复算错误率轴（同文件 `--output` 重跑即可）。
2. 两级提案第二级（pending 池回流，`mention_count≥3`）持续产出带分数候选 → 分数分布变连续，τ_a 扫描才有分辨率。
3. 抽检协议：每次版本发布前对 `auto_accepted` 候选抽 20-30 条人工复核，复核结论落 `ontology_change_proposal_t.status/reject_reason`——即为抽检金标。

---

**复现**（零依赖、<1s）：

```bash
cd nexent && python3 competition/experiments/probe_p6_autonomy_tau.py \
  --output competition/deliverables/algorithm-probes/probe_p6_autonomy_tau.json
```


---

