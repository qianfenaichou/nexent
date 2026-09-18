# T-10a-2：E1 纯 RAG 基线跑分（本地 BM25 检索 + LLM-as-judge）

> 本简报由调度会话（2026-09-17）据 [03-开发计划](../../../03-开发计划.md) §7 任务表 + 02-技术方案 §3.5/§6（E1 纯 RAG 基线 + K4 评测协议） + T-10a-1 简报（评测集 v1 已落地）生成。
> **核心原则**：E1 是 A1 消融臂——只检索、无图无跳；分数必须真实（平台故障不混入 acc），judge 必须与生成不同家族（K4 §6.3）。

**状态**: ✅ **完成**（2026-09-17，分支 `feat/kw-T10a2-e1-baseline`）
**Blocked by**: T-02（语料+registry）、T-07（GraphStore，供后续消融对照）、T-08（llm_client 三档链接线）、T-10a-1（testset-v1-seed.json + judge prompt + eval_v1 校验器）

**独占文件**（本任务创建/修改）:
- `backend/services/knowevo/e1_retrieval.py`（**新建**，E1 检索底座：语料 PDF/HTML→文本→chunk + BM25 索引，零新依赖）
- `backend/services/knowevo/pipeline/eval_e1.py`（**新建**，E1 runner：检索→生成→judge→pass^k→落 eval_run_t，含平台故障隔离与重试）
- `backend/prompts/knowevo_e1_answer_{en,zh}.yaml`（**新建**，E1 生成 prompt 双语成对，强制引用编号）
- `backend/services/knowevo/llm_client.py`（**加性扩展**：① `call_with_usage` 返回 token 计数；② 透传 `max_output_tokens`——`__call__` 冻结契约一字未改）
- `test/backend/services/knowevo/test_e1_retrieval.py`（**新建**，21 例）
- `test/backend/services/knowevo/test_eval_e1.py`（**新建**，26 例）

**运行时操作**（非代码，已执行）:
- `model_record_t` 新增 6 条模型（租户 `6756b0ab-...`），**两个端点**：
  - freeshare.cc.cd（id 4 `deepseek-v4-flash` / 5 `deepseek-v4.1-flash` / 6 `glm-5.3-flash`）——首试端点，共享免费池限流严重（60 run 丢 7 个），**降级为备用**（见坑#36）
  - token.sensenova.cn（id 7 `deepseek-v4-flash` / 8 `glm-5.2` / 9 `deepseek-v4-pro`）——**主用端点**，稳定商用
- `deploy/env/.env` 配 `KW_LLM_SMALL/MID_MODEL_ID=7`、`KW_LLM_LARGE_MODEL_ID=8`（judge 复用 large 档，**零新增 env 变量**——const.py 冻结清单）
- 模型记录均设 `max_output_tokens=8192`：推理型 judge 在 provider 默认上限下会把 JSON 截断（freeshare 轮实测 2 例），显式上限后消除

**待接线项**: 无（E1 是离线管线，不走 HTTP/路由）

**禁改清单**: `apps/*`、`consts/const.py`（零新增 env）、`deploy/sql/migrations/`（零迁移）、`eval_run_t` 只 INSERT 不 ALTER、E0 基线文件只读（`e0-baseline.md`/`e0-questions.md` 为对照锚点）

**允许的新依赖**: **无**（pypdf/pdfplumber/bs4/lxml 均在既有 lockfile；中文分词用字符 bigram 自实现，不引 jieba；BM25 自实现，不引 rank_bm25）

**要构建的行为**（用户视角端到端）:
1. 对评测集任意一题，从 58 份语料检索到支撑片段，LLM 生成带引用编号的回答；
2. judge（GLM 家族）按五元组 rubric 判分，pass^k 聚合出 E1 基线分；
3. 平台限流/500 时自动退避重试，最终仍失败则标记 `platform_fault` 并从 acc 分母隔离（**不把平台故障记成答错**——E0 局限#3 的正确性修复）；
4. 每次跑分落一条 `eval_run_t`（ablation_level=A1_pure_rag + 指标），并追加 cost-ledger。

**验收命令**:
```bash
cd backend && uv run pytest ../test/backend/services/knowevo/test_e1_retrieval.py ../test/backend/services/knowevo/test_eval_e1.py -q --no-header
cd backend && uv run ruff check services/knowevo/e1_retrieval.py services/knowevo/pipeline/eval_e1.py services/knowevo/llm_client.py
# PG 集成全量（只带 PG 变量，不 source 全 .env —— 坑见下）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=... RUN_POSTGRES_INTEGRATION=1 \
  uv run pytest ../test/backend/services/knowevo/ -q --no-header
# E1 真实跑分（20 题 × 3 runs）
# ⚠️ 必须显式 export KW_LLM_*：const.py 的 load_dotenv 只找 backend/.env（不存在），
#    deploy/env/.env 不会自动加载；不传则 fallback 到租户默认 LLM
#    （= model_id 2 tokenrouter glm-5.3-free，旧配置已作废——本次恰好抓到）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=... KW_LLM_SMALL_MODEL_ID=4 KW_LLM_MID_MODEL_ID=4 KW_LLM_LARGE_MODEL_ID=6 \
  uv run python -m services.knowevo.pipeline.eval_e1 --runs 3 --top-k 5
```

**验收标准**:
- [x] `e1_retrieval.py`：58 份语料全部解析成功（0 parse_error），3243 chunks；BM25 检索"二甲双胍→指南2024+说明书"命中正确；中文 bigram/ASCII 词 tokenizer 纯标准库
- [x] `eval_e1.py`：检索→生成（mid=deepseek）→judge（large=glm）→pass^k→落 `eval_run_t` 全链路真实跑通
- [x] judge 五元组判分复用 T-10a-1 冻结的 `knowevo_judge_*` prompt；pass^k 复用 `eval_v1.passk_aggregate`（同一 scorer，不重写）
- [x] 平台故障隔离：`_is_platform_fault` 识别 500/429/配额/超时/连接重置；故障 run 记 `pass=None` + `platform_fault`，**不进 acc 分母**（3 处 run 标记 + `summarize` 用 `pass in (0,1)` 过滤，含 6 条单测）
- [x] judge 输出不可解析（空/截断/散文）同样按 judge 故障隔离，不虚记为答错（含单测）
- [x] 三层时间防护：model_record `timeout_seconds=120`（HTTP client）+ `CALL_HARD_TIMEOUT_SECONDS=180`（`asyncio.wait_for` 硬兜底）+ `_retry_after_seconds` 退避封顶 300s——修掉"进程假活、零 IO 静默挂死"（坑#39）
- [x] 调用节流：`_pace()` / `MIN_CALL_INTERVAL_SECONDS` / CLI `--pace`，把请求速率压到端点 TPM 配额以下（sensenova 实测 429 由 22 次降到可控）
- [x] judge 与生成不同家族（deepseek 生成 / glm judge，K4 §6.3）；`model_plan` 从 router 实解析、不再硬编码（坑#38 的机制修复）
- [x] 零新依赖（lockfile 未动）；零新增 env（const.py 未动）；零迁移；`eval_run_t` 只 INSERT 且 metrics 只存汇总（明细写 deliverables）
- [x] `llm_client.py` 加性扩展：`call_with_usage` + `max_output_tokens` 透传，`__call__` 契约一字未改（既有 llm_client 测试全绿）
- [x] E1 生成 prompt 双语成对，强制引用编号 + 拒答 + 安全边界（对齐 K4 rubric）
- [x] 真实跑分两次（20 题 × 3 runs），acc/pass2/pass3/trace/p95/token 全部产出并落库；两次结果一致（0.77 / 0.79）证明可复现
- [x] 失分归因完成（rubric 过严 / 检索召回错版本 / 测试集语义混用 / 平台故障四类），写入简报实跑节
- [x] 完整逐题报告归档 `competition/deliverables/e1-baseline-report.json`
- [x] 新踩坑记 `competition/docs/pitfalls.md`（#36 免费池限流 / #37 ROOT_DIR 注入 / #38 静默模型 fallback / #39 TPM 限流与静默挂起）

**Evidence**:
```
# 1. 新测试全绿（56 例：检索器 22 + runner 34）
$ uv run pytest test_e1_retrieval.py test_eval_e1.py -q --no-header
56 passed in 0.47s

# 2. ruff
$ uv run ruff check services/knowevo/e1_retrieval.py services/knowevo/pipeline/eval_e1.py services/knowevo/llm_client.py \
    ../test/backend/services/knowevo/test_eval_e1.py ../test/backend/services/knowevo/test_e1_retrieval.py
All checks passed!

# 3. PG 集成全量（真实 PG 5434；含既有 327 + 本次新增 56，无回归）
$ POSTGRES_* + RUN_POSTGRES_INTEGRATION=1 uv run pytest ../test/backend/services/knowevo/ -q --no-header
382 passed, 5 warnings in 50.16s

# 4. E1 冒烟（2 题 × 1 run，真实 LLM）——链路验证
HTTP 200 (生成) / HTTP 200 (judge)；eval_run_t 落库可见 config.ablation_level=A1_pure_rag
（该冒烟行已删除：当时 KW_LLM_* 未导出，实际 fallback 到旧 tokenrouter 模型，记录的模型档失实——坑#38）

# 5. 全量跑分两次（见「实跑节」）
$ ... eval_e1 --runs 3 --top-k 5 [--pace 8]
sensenova（权威）: acc=0.7692 pass2=0.6842 trace=1.0  52/60 有效判定，故障 8 已隔离
freeshare（佐证）: acc=0.7925 pass2=0.7000 trace=1.0  53/60 有效判定，故障 7 已隔离
落库: eval_run_t 8aba7c1d（最新/权威）、9388c240（佐证）
完整报告归档: competition/deliverables/e1-baseline-report.json + .log

交付：
- e1_retrieval.py（语料 PDF/HTML→文本→chunk + BM25，零新依赖，字符 bigram 中文分词）
- eval_e1.py（E1 runner：检索→生成→judge→pass^k→eval_run_t；平台故障隔离 + 节流 + 三层超时）
- knowevo_e1_answer_{en,zh}.yaml（生成 prompt 双语成对，强制引用/拒答/安全边界）
- llm_client 加性扩展（call_with_usage token 计量 + max_output_tokens 透传）
- 56 个新单测 + 台账四坑（#36 免费池限流 / #37 ROOT_DIR 注入 / #38 静默模型 fallback / #39 TPM 限流与挂起）
```

## 实跑节：E1 纯 RAG 基线结果（2026-09-17）

### 权威结果（sensenova 端点，含全部护栏）

```
eval_run_id = 8aba7c1d-8725-4c50-8f33-e05b911b202c   （落 eval_run_t，task_ref=T-10a-2）
testset_hash = fd56571a304fb09d（与 T-10a-1 种子集一致）
配置: generator = mid:deepseek-v4-flash(id7) / judge = large:glm-5.2(id8)  ← 跨家族（K4 §6.3）
      retrieval = bm25_local_corpus（3243 chunks）/ top_k=5 / 3 runs/题 / pace=8s

acc = 0.7692   pass2 = 0.6842   pass3 = 0.6316
trace_machine = 1.0（检索证据的定位符完整率）
p95 = 75.7s    tokens = 164,022 in / 103,118 out
有效判定 52 run / 60（平台故障 8：生成 4 + judge 4，全部隔离出 acc 分母，未虚记为答错）
题型: F 0.80(5题) / M 1.00(4题) / V 0.80(5题) / X 0.57(5题)
```

### 佐证结果（freeshare 端点，早先一次独立跑分）

```
eval_run_id = 9388c240-7bc6-4d3f-a25e-d03919bff8a8
generator = deepseek-v4-flash(id4) / judge = glm-5.3-flash(id6)
acc = 0.7925   pass2 = 0.70   pass3 = 0.60   trace = 1.0   有效判定 53/60（故障 7）
题型: F .90 / M .80 / V .857 / X .643
```

**两次独立跑分（不同端点、不同 judge 模型）得 acc 0.77 / 0.79、pass2 0.68 / 0.70** —— 差异在 52-53 个有效判定的采样噪声内，说明 E1 管线**可复现**，基线数字稳定。报告页取最新一条（sensenova）。

### ★ 失分归因（本基线的核心产出，直接支撑后续消融与优化）

逐题复盘 5 道全败题，归因分三类——**只有一类是"模型弱"**：

| 题 | 现象 | 归因 | 对项目的意义 |
|---|---|---|---|
| **F-002** | 模型答"2~3个月"（**医学上正确**），判 0 | **rubric 过严**：key_facts 同时列了 `8-12周` 与 `2-3个月` 两个**等价**表述，judge 要求两点都覆盖 | rubric 应视为"同义点合并"，改评测集时修（T-10a-3 人工校验一并处理） |
| **V-001** | 问 2024 版指南 HbA1c 目标，模型答"知识库无依据" | **检索召回错版本**：BM25 把《2020 版指南》排 #1、《2024 版》排 #3-4（双栏 PDF 文本串行干扰），模型只见围手术期/<8% 的片段 | ★ 这正是 **A1 纯 RAG 在版本敏感题上的结构性缺陷**——无版本概念，检索混版。是 B2 版本钉住（T-09 已交付）与 E8 消融的**动机实证**：预期 A4 在 V 题上显著优于 A1 |
| **X-002** | 问"达格列净致泌尿感染后换哪类药"，模型拒答 | **测试集语义**：该题源自 E0 迁移泛化题（本应可答），但知识库无明示"换用非 SGLT2 类"的段落 → 纯 RAG 必然拒答；而 rubric `refusal_expected=false`，拒答被罚 | X 型在种子集里混了两种语义（库外拒答 X-003/X-004 vs 临床判断题 X-001/002/005），T-10a-3 扩展时需统一 |
| **X-005** | 低血糖急症第一步处置，模型拒答 | 同上（临床判断题，知识库无逐字步骤） | 同上 |
| M-002 / M-004 | 各仅 1 个有效判定（生成/judge 被 sensenova 配额限流） | **平台故障**（已隔离，非答错） | 计入 `platform_faults`，不污染 acc |

**结论**：E1 = 0.77-0.79 的基线分里，X 型（0.57）与 V 型（0.80）是主要失分区，且**V 型失分的根因是"无版本概念"而非模型能力**——这为 A2/A3/A4 消融提供了清晰的预期方向（V 题提升、F 题回归护栏），并让 T-10a-3 的评测集修订有据可依。

### 已知限制（诚实口径）

1. **judge 与生成同为"deepseek vs glm"跨家族，但仍是免费/低成本档模型**；K4 §6.3 要求的"judge 大档"受可用端点限制，未使用最强模型（记录在案）。
2. **两个端点都会限流**（freeshare 免费池账户冷却；sensenova TPM/RPM + 配额），120 次调用必然产生少量平台故障；本管线已隔离 + 节流（`--pace`），但**故障率受端点配额制约**，非代码质量问题。
3. **PDF 双栏串行**：pdfplumber 抽取双栏 PDF 时文本跨栏串行（见 V-001 检索噪声），影响 BM25 召回；解析质量优化（版面分析/分栏）未在本任务范围。
4. 种子集 20 题样本量小（K4 §6.2 诚实口径：单轮不足统计功效），pass^2 差异须报告 CI，不宣称显著性——120 题完整集归 T-10a-3。

## 与 E0 的关系（答辩口径）

- E0 = 裸模型基线（glm 无知识库，45%/65% any-match）→ **E1 = 纯 RAG 基线**（deepseek + 本地 BM25 检索，LLM-as-judge 五元组）
- E0 局限#3（平台 429/500/空响应污染命中率）在 E1 已修：平台故障隔离 + 重试
- E0 判分（关键词 any-match）→ E1 判分（K4 五元组 LLM-as-judge），是 T-10a-1 到本任务的判分升级
- A4 完整版（+图检索+多跳+版本钉住）预期 vs A1 的 pass^2 提升，将是 E2 消融的核心数字（T-10b）
