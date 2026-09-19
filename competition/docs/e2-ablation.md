# E2 消融：A1→A4 × 题型 + E8 版本钉住 on/off（T-22 实测报告）

- 任务：T-22（`competition/tasks/T-22-brief.md`）；执行器：`backend/services/knowevo/pipeline/ablation.py`
- 日期：2026-09-19；分支 feat/kw-T18b-fact-time
- 测评集：`competition/corpus/testset-v1-seed.json`（20 题种子集，F/M/V/X 各 5；hash 见报告 JSON `testset_hash`）
- 协议：每题 3 次重复（K4），主指标 pass^2，同时报 pass^3 / acc / **n_judged** / trace_machine / trace_answer / p95 延迟 / token；95% CI 用 Wilson 区间（`eval_v1.wilson_interval`）
- 判分：复用 T-18c 冻结的五元 judge 链（`eval_e1._judge_once`，judge 模型 large 档，与生成模型不同族）；`count_fail` 契约——非平台故障与端点失败一律 pass=0 留在分母，不静默排除
- 原始逐题数据：`competition/deliverables/e2-ablation-report.json`（只含真跑数字；`partial: true` 表示预算内未全部完成，续跑命令在 `resume` 字段）

## 0. 数据现实（先读这个，不要跳过）

**构建租户的图谱是空的。** 2026-09-19 真库勘察（psql 实测）：

| 项 | 值 |
|---|---|
| `kg_relation_t` 全库 | 474 行（**全部**属于临时评测/集成测试租户，每租户 3-4 行） |
| 构建租户 6756b0ab（语料/评测所在） | 实体 **0**、关系 **0** |
| 业务时间分化 | valid_at≤2024-06-01 共 121 行（PG 集成测试播种）；min(valid_at)=2024-01-01 |
| `ontology_version_t` | 仅 v1.0.0（2026-09-18 提交，**无 fact_cutoff**） |
| `doc_asset_t`（构建租户） | 58 份语料文档完好（T-18b 已回填业务出版日） |

任务书背景"真库 kg_relation_t 294 行、kg_entity_t 524 行——A2/A3/A4 有真实图可用"**在当前库上不成立**（这些行属于临时租户；带证据链的真实语料摄取按 T-18b Evidence 规划归 T-19/T-22 后续真实摄取）。因此：

- **A2/A3 的图谱通道在本轮真跑中检索为空**（kg_channel.n_seeds=0/n_edges=0，逐题记录在报告 JSON，不静默）；
- **A4 与 E8 的两臂收到完全相同的证据（仅文档通道）**，A4 vs A2/A3 的差只剩路由器+卡片渲染的开销与其自身的模型抖动；
- **E8 的 Δ 在真实语料图摄取前不可观测**——这不是"钉住无效"，而是"钉住的对象尚不存在"。机制证明由 T-18b 的判别性护栏承载：单测 `TestDiscriminativeVersionPin` + PG 集成 `test_discriminative_version_pin_on_real_db`（真库播种：t_v=2022 纳入 1 / t_v=2025-06 纳入 2）。

## 1. D1 前置校验（T-18b 判别性 SQL，跑消融前实测）

runner 启动时经 `version_pin.pin_predicate`（单一谓词入口）复跑判别性计数，结果随报告落库：

| clock | 纳入事实数 |
|---|---|
| t_v=2022-01-01 | **0** |
| t_v=2024-06-01（E8 默认钉住点） | **121** |
| t_v=2025-06-01 | **1** |
| all（无谓词） | **474** |

两个 t_v 计数不同（0≠1）→ **D1 判别性成立，放行 E8**。与 T-18b 简报预期 0/1/369 的差异：全库行数已变为 474（评测夹具重复播种），判别对 0≠1 不变；121 行 2024-01-01 业务日期行来自集成测试播种。诚实结论不变：SQL 级三 count 的分化（期待"真实语料摄取后 t_v 两值纳入数显著不同"）仍待带证据链的真实摄取；当前差值 0 vs 1 对 474，**对 E8 的 Δ 而言等于没有可裁剪的图事实**。

## 2. 四级配置（02-技术方案 §3.5）

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

## 3. 结果（真跑数字）

**本轮为预算内部分完成**（LLM 真跑墙钟 ~57 分钟到点检查点落盘；`partial: true`，续跑命令见报告 `resume` 字段与 status.json）。所有数字来自真跑（sensenova 端点，mid=deepseek-v4-flash / judge large=glm-5.2，构建租户 6756b0ab，pace=8s），逐题 runs/details 见报告 JSON。

### 3.1 实测矩阵（每格 acc / n_judged；pass^2 为主指标）

| 级 | 已测题数 | acc | pass^2（95% CI Wilson） | n_judged/n_expected | trace_machine | p95 延迟 | tokens (in+out) | eval_run_t |
|---|---|---|---|---|---|---|---|---|
| A1_pure_rag | 10/20 | 0.7241 | 0.70 [0.397, 0.892] | 29/30 | 1.0 | 67.11s | 88657+70251 | c80dfa38 |
| A2_graph | 4/20 | 0.7500 | 0.75 [0.301, 0.954] | 12/12 | 1.0 | 48.62s | 33709+24048 | 1b98defc |
| A3_multihop | 0/20 | —（未跑到，预算检查点） | — | 0/0 | — | — | 0 | 59b71408（空行） |
| A4_full（pin=on，headline） | 3/20 | 0.3333 | 0.3333 [0.062, 0.792] | 9/9 | 1.0 | 96.03s | 20005+32954 | edcad253 |
| E8 A4 pin=on（按臂金标） | 2/10（V,F） | 0.5000 | 0.50 [0.188, 0.812] | 6/6 | 1.0 | 137.02s | 18810+45410 | a35a6e73 |
| E8 A4 pin=off（按臂金标） | 0/10 | —（未跑到） | — | 0/0 | — | — | 0 | eacba193（空行） |

### 3.2 by_type × by_level 交叉表（当前实测覆盖；零判定格显式 insufficient_data）

| 题型 | A1_pure_rag | A2_graph | A3_multihop | A4_full (pin=on) |
|---|---|---|---|---|
| F | acc 0.7857（n_judged=14） | acc 0.7500（n_judged=12） | insufficient_data | acc 0.3333（n_judged=9） |
| M | acc 0.6667（n_judged=15） | insufficient_data | insufficient_data | insufficient_data |
| V | insufficient_data | insufficient_data | insufficient_data | insufficient_data |
| X | insufficient_data | insufficient_data | insufficient_data | insufficient_data |

（预算检查点把每级都停在了 F/M 题段，V/X 尚未进入实测——交叉表如实以 insufficient_data 呈现，不缩格、不消失。）

### 3.3 必须与数字一起读的三个事实

1. **A4 的 9 个 run 里有 4 个是端点空内容故障**（F-002×1、F-003×3，"card render runner error: LLM output was not a JSON object; got: ''"——E0 已知的免费网关高负载空返回病理），按 count_fail 契约记 pass=0 留在分母。执行器最初只重试 1 次，**低于 E1 平台故障 6 次重试纪律**；本轮跑完后已修复为空内容重试 4 次（`_CountingLLM`），**已测的 4 个故障 run 如实保留**（可经 eval_run_t/cost-ledger 追溯），续跑题目将使用修复后执行器。因此上表 A4 的 0.3333 混入了执行器欠重试期的端点故障，**不等于 A4 管线的判分能力**；E8 on 臂同样含 2 个此类故障。建议续跑时对 A4/E8 臂做"删除条目重测"（报告 `resume` 字段步骤 1 给出精确命令），A1/A2 数字干净可续。
2. **图通道全程为空**（kg_channel.n_seeds=0，构建租户无图——见 §0），A2 的 0.75 实际上测的是"A1 通道 + 空图查询开销"，与 A1 的差异主要是题目子集不同（4 题对 10 题），**不能解读为图检索增益**。
3. A1 的 1 个 judge_unparseable 故障（F-003 run）按平台故障排除在分母外并单独计数（n_judged=29/30 的差值）。

## 4. E8 版本钉住 on/off

**本轮未产出可用的 Δ**，两个原因都已在 §0/§1 预告，此处如实登记：

| 臂 | 覆盖 | F 题分 | V 题分 |
|---|---|---|---|
| pin=on（对 answer_old 金标判分） | F-001、F-002（2 题 6 run） | acc 0.5（n_judged=6，含 2 个端点空内容故障） | insufficient_data（未跑到） |
| pin=off（对 answer_new 金标判分） | 未跑到（on 臂用尽预算） | insufficient_data | insufficient_data |
| Δ(on − off) | — | **null（无法计算）** | **null** |

- **数据现实**：构建租户图为空，两臂即使都跑到，图谱通道也收到完全相同的（空）证据，钉住对生成输入的裁剪为零——E8 的 Δ 在真实语料图摄取前**结构性地≈0**，本轮连"结构性≈0"都未测满（off 臂 0 题）。
- **判分按臂选金标机制已实现并经单测锁定**（V-004 on 臂 rubric 收窄到"宽松"、off 臂保留全量，`test_ablation.py::TestArmItem/TestArmGoldJudging`），机制就绪、数据未到。
- **机制证明的现行承载**：`TestDiscriminativeVersionPin`（单测）+ `test_discriminative_version_pin_on_real_db`（真库播种 t_v=2022 纳入 1 / t_v=2025-06 纳入 2）+ 本报告 §1 判别性 SQL（0/121/1/474）。

## 5. 结论与预期对照（诚实口径）

1. **A4 vs A1 pass^2 Δ**：本轮无法给出——A4 仅 3 题（且 4/9 run 为端点故障），与 20 题协议要求差得远；02-技术方案 §3.5 预期 **+10pp**，本轮**既未证实也未证伪**。A1 自身部分基线：pass^2=0.70 [0.397, 0.892]（10 题），与 T-18c 全量口径的 0.65（20 题）同量级，端点健康时基线可复现。
2. **溯源 +30pp 预期**：trace_machine 各级均为 1.0（文档通道 locators 完整）；图谱通道空载，trace_answer 的引用范围检查零违规——"图谱证据链增益"同样待真实摄取后复测。
3. **延迟**：A4 p95 96s ≈ A1 67s 的 1.4×（部分数据、含故障重试，方向上与"延迟 +2×"预期一致但未达）。
4. **本轮真正确立的交付**：四级 runner + E8 双臂 + 按臂金标判分 + 交叉表/insufficient_data/Wilson CI 的完整执行器（29 项编排单测锁定），D1 判别性校验入报告，成本入账 6 行。执行器就绪、语料图未就位——这就是本轮的诚实结论，续跑命令已就位（报告 `resume` + status.json）。

## 6. 成本入账（E2 本轮）

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

## 附录 A：执行与复现

```bash
# 四级 headline（每级一条 eval_run_t；可断点续跑：重跑同命令自动跳过已完成 pair）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> KW_LLM_SMALL_MODEL_ID=7 KW_LLM_MID_MODEL_ID=7 KW_LLM_LARGE_MODEL_ID=8 \
  uv run python -m services.knowevo.pipeline.ablation --levels A1,A2,A3,A4 --runs 3 --top-k 5 --pace 8

# E8：A4 pin on/off × V,F（判分按臂选金标）
cd backend && ... uv run python -m services.knowevo.pipeline.ablation --levels A4 --pin on,off --types V,F --runs 3

# 单测（不依赖真 LLM）
cd backend && uv run pytest ../test/backend/services/knowevo/test_ablation.py -q --no-header
```

## 附录 B：实现备注与坑

1. `graph_store.PgJsonbGraphStore`（版本钉住走它）与 `kg_service.PgStore`（本体快照在它上面）是两个类——hop 规划的本体词表需从后者加载，缺失时诚实降级为无过滤走图（逐题 errors 可见）。
2. `assemble_evidence` 的 contested 标记依赖 `edges_by_path`——A2 手工组装的 1 跳路径必须以 PathSet（含 claims_by_path/edges_by_path）形状喂入，直接传 Path 列表会丢掉边的 contested 标记。
3. 种子词必须是**实体名的子串**（store 的 ILIKE 是 name CONTAINS query）：CJK 长run 用 3 字滑窗（步长 2）+ 全 run，ASCII 词原样；上限 12 个查词/题，确定性可复现。
4. 卡片渲染的 llm 契约只回 str（token 不可见）——用 `_CountingLLM` 包装器过 `call_with_usage` 计量；非 JSON 卡片输出重试 1 次（模型抖动），仍失败按 count_fail 记 runner_error 留分母。
5. pin=None（A1-A3）与 pin=off 语义不同：`pin_on = (pin == "on")` 而不是 `!= "off"`——后者会让 A3 意外被钉住（冒烟 dry-run 抓到）。
