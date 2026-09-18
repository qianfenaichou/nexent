# T-22 真实消融（A1→A4 + E8 pinned on/off）就绪度检查报告

- 检查人：证据收集员（只读检查，不实现）；READY-4 子智能体四次中断后由主智能体接管完成，其收集的证据已全部合并入本报告
- 检查日期：2026-09-19
- 任务书：`competition/tasks/T-22-brief.md`
- 方法：grep 定位 → 局部精读，所有判定附 file:line 证据；查不到即写"未找到证据，无法确认"

---

## ① 结论速览

| 臂 | 就绪度 | 一句话判定 |
|---|---|---|
| A1 纯 RAG | 🟢 可跑（已实证） | `evaluate()` 全链路在库且历史真跑过两次（cost-ledger run 8aba7c1d / b64faa90） |
| A2 +图检索 | 🟡 服务就绪、管线无接线 | `kg_service` 检索/邻域能力在，但 eval 管线零图钩子 |
| A3 +多跳 | 🟡 服务就绪、管线无接线 | `multi_hop` 含版本钉住参数齐备；评测管线既不走 MCP 也不直调服务 |
| A4 完整双驱动 | 🔴 需 T-22 组装 | 融合/渲染/路由/版本参数传入均需在 runner 内新写编排 |

**A1 判定：可跑**。A2/A3/A4 共 **6 项阻塞**（见③），全部可由 T-22 编码任务解除，无一需要新算法。

## ② A1 可跑性确认

- `run_question`（`backend/services/knowevo/pipeline/eval_e1.py:336`）、`_call_with_retry`（:485）、`summarize`（:544）、`evaluate`（:699）与 T-22 简报所引契约一致（行号因 T-18c 后迭代略有偏移）。
- `config.ablation_level="A1_pure_rag"` 已硬编码于 evaluate 的 config 组装（`eval_e1.py:728`）。
- by-type（F/M/V/X）聚合已实现（`eval_e1.py:576-628`），随 metrics 落库（:837）。
- `--pace` 节流参数在（`eval_e1.py:775`）；`eval_run_t` 单行 INSERT-only 写入（`eval_e1.py:652-676`），`--skip-ledger` 开关在（:774）。
- 真跑证据：cost-ledger 行 `e1-8aba7c1d`（20题×3 runs，acc=0.7692 旧口径）与 `e1-b64faa90`（诚实口径 acc=0.6667 / pass2=0.65 / n_judged=60/60）。
- 运行条件：PG（kg 表不依赖，但 eval_run_t 需库）+ 本地 BM25 语料（检索走 `eval_e1.py:717-720`，语料根 `CORPUS_ROOT` :45，无向量库依赖）+ `KW_LLM_SMALL/MID/LARGE_MODEL_ID` 必填 env（`llm_client.py:41-43`）+ judge 模型；`--no-persist`（:773）、`--on-unexpected`（:777，默认 count_fail）可用；命令式样见 T-22 简报"验收命令"3。
- 行号漂移说明：T-22 简报引用 :264/:355/:405/:518，实测为 :336/:485/:544/:699（T-18c 迭代所致），契约本身未变。
- 遗留条件：无阻塞；建议 T-22 先 `2题×1 run` 冒烟复核当前端点与 judge 健康。

## ③ A2/A3/A4 阻塞项清单

| # | 阻塞点 | 现状证据 | 解除条件 | 建议归属 |
|---|---|---|---|---|
| B1 | 评测管线无图检索钩子：eval_e1 模块自述"no graph, no multi-hop"（`eval_e1.py:4`），全文 grep `kg_search/kg_multi_hop` 零命中；`e1_retrieval.py` 为纯 KB 检索（`Retriever.search` `e1_retrieval.py:348`） | 同左 | T-22 在 runner 内新增 kg_search 调用并做文档级+图谱两通道合并（复用 `assemble_evidence`，勿另写融合） | T-22 编码 |
| B2 | 图服务能力本身就绪（无新算法）：`kg_service.search_entities`（`kg_service.py:663`）、`kg_service.neighbors`（:676）、`graph_store.neighbors` 带 `as_of` 扩展（`graph_store.py:276`） | 同左 | 只需接线 | T-22 编码 |
| B3 | 多跳调用路径二选一未定：MCP 工具已双注册（`mcp_servers/knowevo_mcp/server.py:164` handler、:247 `@mcp.tool`），服务直调签名同样在（`decision_service.multi_hop` `decision_service.py:404`）——评测管线现走服务直调（e1_retrieval），A3/A4 需明确"直调 DecisionService"（推荐，免 MCP 往返与鉴权；本地进程内另有 `backend/tool_collection/mcp/kg_tools.py:64-71` handlers 可用）。⚠️ 版本语义注意：MCP `kg_search_handler`（`server.py:105-132`）是 current view、**无版本语义**——E8 钉住臂只能走 `kg_multi_hop_handler`（:164-182，内部即调 `svc.multi_hop`）或服务直调 | 同左 | T-22 开工时定死一条并写进 ablation.py | T-22 编码 |
| B4 | 版本钉住参数未进入评测管线：`eval_e1.py` 全文 grep `as_of/version` 无业务传参（仅 docstring）；而 `multi_hop(..., as_of, pin_version=True)` 形参齐备（`decision_service.py:407-408`），`resolve_version_clock` 支持 fact_cutoff（`version_pin.py:110-144`） | 同左 | A4/E8 臂把 `version`/`as_of` 从评测集题面或 CLI 传入 multi_hop | T-22 编码 |
| B5 | A4 融合与渲染编排不存在：`assemble_evidence`（`decision_service.py:802`）与 `render_card`（:878）是服务能力，评测管线无调用点；路由器（RM 并发）同样无评测钩子 | 同左 | A4 runner 组装：路由→双路→融合→渲染，全部复用既有服务函数 | T-22 编码 |
| B6 | **D1 前置未关闭**（见⑤）：pinned on/off 若 valid_at 仍是墙钟则 Δ≈0，E8 失效 | 同⑤ | 先跑 T-18b 判别性 SQL 并登记，再动 E8 | T-18b 收尾 + T-22 前置校验 |

相邻任务状态（2026-09-19）：T-18d 权威度检索已完成（`tasks/T-18d-brief.md:3`），非阻塞；T-18c 全量重跑进行中（`tasks/T-18c-brief.md:3`）——A1 的权威基线数字以 T-18c 重跑后的诚实口径为准；`ablation.py` / `test_ablation.py` 均尚不存在（T-22 独占文件，待建）。

## ④ V 题双金标就绪度专项

- 数据就绪：`competition/corpus/testset-v1-seed.json` 为 20 题种子集（F/M/V/X 各 5），5 道 V 题顶层 `answer = {answer_old, answer_new}` 双金标齐备（V-001 见 :363-364）；`eval_v1.py:91-94` 已有双金标强制校验可复用。
- **缺口（E8 的关键阻塞）**：判分链路不消费双金标——`render_judge_prompt`（`eval_e1.py:196-208`）只读 `rubric.key_facts`，而种子集 V 题的 key_facts 是单时点值（如 V-001 为 ["7.0%"]，且该题 answer_old == answer_new）。E8 两臂若都按现有 key_facts 判分，金标相同、分数差无从产生。T-22 需要：①判分按臂选金标（old 臂对 answer_old / new 臂对 answer_new）；②对 old/new 结论不同的 V 题补双时点 key_facts。这是执行器扩展，非数据阻塞。

## ⑤ D1 前置校验现状

- 代码接缝已就位（工作区，未提交）：`version_pin.resolve_version_clock` 支持 fact_cutoff（`version_pin.py:110-144`）；写入点 `ontology_service.commit_version`（`ontology_service.py:634,653-655`）；读取点 `decision_service.py:652-658`。backend 多文件 diff 为 T-18b/T-18c 实现会话产出（git status 可见，非本检查会话所改）。
- **未找到完成证据**：`tasks/T-18b-brief.md:3` 状态仍为"待开发"；`competition/docs/evolution-log.md` 与 `docs/verification-reports/*.md` 中 grep `T-18b|fact_cutoff` 零命中；无 T-18b receipt 文件；真库 294 条 `kg_relation_t.valid_at` 是否已回填业务出版时间——未找到证据，无法确认；T-22 简报要求的"判别性 SQL"（`T-18b-brief.md:79`，t_v 两个取值下纳入事实数不同）未见运行记录。
- 结论：**D1 视为未关闭**。T-22 动工前必须：跑判别性 SQL → 把结果登记进 evolution-log → 才允许跑 E8。

## ⑥ 给 T-22 实现 agent 的建议执行顺序（最小冒烟优先）

1. D1 校验：跑 T-18b 判别性 SQL，登记 evolution-log（不过关就停下报告）。
2. A1 冒烟：`2题×1 run` 复核端点/judge/落库链路（成本≈0.1% 预算）。
3. A2 runner：eval_e1 加 kg_search 钩子（1 跳邻域）→ 2 题冒烟。
4. A3 runner：服务直调 `multi_hop`（depth=2, beam=3）→ 2 题冒烟 → 核算每题 token/延迟，再全量。
5. A4 runner：路由 + `assemble_evidence` + `render_card` + 版本参数传入 → 2 题冒烟。
6. E8：A4 `--pin on,off` × V/F 题（V 题双金标执行器扩展先做）→ 20题×3 runs。
7. 交叉表：by_type×by_level 每格 `acc + n_judged`，零判定格显式 `insufficient_data`（`eval_e1.py:576-628` 的 by_type 骨架可复用）。
