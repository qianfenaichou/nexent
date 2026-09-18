# T-18c：D2 诚实分母（非平台异常必须计入失败或 re-raise）+ D3 答案级溯源

**状态**: ★ 待开发（2026-09-18 调度会话）
**Blocked by**: 无
**独占文件**（本任务创建/修改）:
- `backend/services/knowevo/pipeline/eval_e1.py`
- `backend/services/knowevo/pipeline/eval_v1.py`（trace 双口径：机器 + 答案级）
- `backend/prompts/knowevo_trace_{en,zh}.yaml`（**新建**，答案级溯源判定 prompt，双语成对）
- `test/backend/services/knowevo/test_eval_e1.py` / `test_eval_v1.py`（更新+新增）
- `competition/deliverables/e1-baseline-report.json`（**重跑覆盖**，口径修正后）
- `competition/docs/cost-ledger.md` / `pitfalls.md`（补记）

**待接线项**: 无
**禁改清单**: `backend/consts/const.py`、`apps/*`、`deploy/sql/migrations/*`、`eval_run_t` schema、`e1_retrieval.py`（T-18d 独占）、`version_pin.py`/`kg_service.py`（T-18b 独占）
**允许的新依赖**: **无**

**要构建的行为**（用户视角端到端）:
1. **D2**：跑分时若生成/judge 抛**非平台异常**（代码 bug、schema 错、KeyError、Pydantic 校验失败等），该 run **不得**被静默 `return None,None` 后标成 `platform_fault` 移出分母。规则：
   - 非平台异常 → **re-raise**（默认，代码 bug 必须炸出来）；
   - 或显式计入失败（`pass=0` 且 `error="runner_error"`，**进分母**），仅当调用方显式选择 `on_unexpected="count_fail"`；
   - `platform_fault` **只**由 `_is_platform_fault` 白名单（500/429/超时/连接重置/配额）触发。
2. **D2 聚合口径**：`summarize` 新增 `n_judged`（= `pass in (0,1)` 的 run 数）与 `n_expected`（= 题数 × runs）；`by_type` 对**零有效判定**的题型**显式标注**（`n_judged=0` + `insufficient_data=true`），不再静默消失或伪造 acc=1.0。
3. **D2 报告诚实**：`acc` 同时报 `n_judged`；若 `n_judged < n_expected`，报告里**并列**平台故障数与非平台错误数，且非平台错误数 >0 时**整体标红**（`integrity_warning=true`）。
4. **D3**：`trace_completeness` 拆成双口径——
   - `trace_machine`：既有字段完整率（保留，改名说明它测的是"检索器是否保住定位符"）；
   - `trace_answer`：**答案级**——生成的回答里每个 `[n]` 引用编号是否指向真实检索到的 chunk，且该 chunk 是否支撑该句（LLM 判定，或至少确定性校验引用编号 ∈ 检索集）。
5. **判据**：`trace_answer` 不再是常数 1.0；对"无引用/引用越界/引用不支撑"三种情况有可区分取值。

**背景（现状核验，2026-09-18）**:
- `eval_e1.py:397-399`：`except Exception as exc: ... if _is_platform_fault(...) {retry} else {if raise_on_fault: raise; return None, None}` —— **`raise_on_fault` 默认 False**（:357），生成调用显式传 `raise_on_fault=False`（:286）。于是**任何非平台异常都被吞成 `(None,None)`**，上层（:287-294）无条件标 `platform_fault: True` → `summarize` 的 `judged = [r for r in runs if r.get("pass") in (0,1)]`（:413）把它移出分母。
- `_is_platform_fault`（:122-124）用 `_PLATFORM_FAULT_MARKERS` 子串匹配——**若异常消息恰好含某 marker 的偶然子串，代码 bug 会被误判为平台故障**。
- `summarize.by_type`（:420-427）：`sub = [r for r in judged if r.get("type")==qtype]; if sub: ...` —— 零有效判定的题型**直接不进 by_type**（静默消失）。实测 M 型 15 run 只判 8，M-003 整题消失，`by_type.M.acc` 因此虚高。
- `trace_completeness`（:249-261）：只检查 `evidence` 里每条的 `doc_id/chunk_idx/span_hash/title` 非空——检索器**构造上必然**填全 → **恒 1.0**，与答案无关。
- `judge_parse_errors`（:428）单独计数但同样被标 `platform_fault` 移出分母（:316-327）——judge 输出不可解析**既可能是平台截断也可能是 prompt 问题**，需区分。

**验收命令**:
```bash
# 1. 单测（D2 三类异常路径 + D3 双口径）
cd backend && uv run pytest ../test/backend/services/knowevo/test_eval_e1.py ../test/backend/services/knowevo/test_eval_v1.py -q --no-header
# 2. ruff
cd backend && uv run ruff check services/knowevo/pipeline/eval_e1.py services/knowevo/pipeline/eval_v1.py
# 3. ★D2 回归护栏：非平台异常必须不被吞
#    (测试内注入 ValueError("boom") 到 router，断言 pytest.raises 或 pass=0 进分母，二选一按实现)
# 4. 重跑 E1（修正口径后的权威数字，必须同时报 n_judged）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> KW_LLM_SMALL_MODEL_ID=7 KW_LLM_MID_MODEL_ID=7 KW_LLM_LARGE_MODEL_ID=8 \
  uv run python -m services.knowevo.pipeline.eval_e1 --runs 3 --top-k 5 --pace 8
# 5. PG 集成全量
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> RUN_POSTGRES_INTEGRATION=1 \
  uv run pytest ../test/backend/services/knowevo/ -q --no-header
```

**验收标准**:
- [ ] 非平台异常**不再**被静默转成 `platform_fault`：默认 re-raise；`on_unexpected="count_fail"` 时计 `pass=0` 进分母（含 3 类单测：普通异常 re-raise、count_fail 进分母、平台异常仍隔离）
- [ ] `platform_fault` 仅由白名单触发；新增单测证明"消息里偶然含 marker 的代码异常"不被误判（marker 匹配收紧为结构化判定，如状态码/异常类型优先）
- [ ] `summarize` 输出含 `n_judged` 与 `n_expected`；`by_type` 对零判定题型输出 `{n:0, n_judged:0, insufficient_data:true}`（不再消失）
- [ ] 报告 `integrity_warning`：非平台错误 >0 时为 true
- [ ] `trace_machine` 语义注释改准（"检索定位符完整率"），新增 `trace_answer`（引用编号 ∈ 检索集 + 支撑判定）；单测覆盖三种可区分情况（全引用正确 / 引用越界 / 无引用）
- [ ] E1 重跑：`acc` 与 `n_judged` **同时**出现在报告与简报；`by_type` 四型齐全（M 型不再消失）
- [ ] `e1-baseline-report.json` 用新口径覆盖，旧口径数字在简报里**如实说明被替换的原因**（不得只留新数字抹掉旧的）
- [ ] 零新依赖、零新增 env、`eval_run_t` 只 INSERT
- [ ] `pitfalls.md` 补记"分母洗白"这一坑

**Evidence**: <粘贴 pytest 输出 / 重跑报告的 acc+n_judged / by_type 四型 / integrity_warning 值>

---

## 实现备注（给实现 agent）

1. **D2 的伦理内核**：铁律 6"不得伪造任何数字，跑分分母必须诚实"。当前代码把"代码 bug"和"平台故障"混为一谈，**用排除运行抬高了准确率**。修复后若 `acc` **下降**，那是**对的**——旧数字是虚高的。简报必须如实写"旧 acc=0.7692 基于被污染的分母"。
2. **重跑成本**：E1 一次全量 ~50 分钟 + token 成本（见 cost-ledger）。重跑前先跑小样本（2 题）确认口径改动无 bug，再全量。
3. **`trace_answer` 的实现选择**：LLM 判定每句是否有支撑成本高（每题一次调用）。**推荐分层**：确定性层先做"引用编号 ∈ 检索集"（零成本、可复现），LLM 层做"引用是否支撑该句"（抽样或全量，按预算）。两层分别报 `trace_answer_cite` 与 `trace_answer_support`。
4. `eval_v1.py` 的 `passk_aggregate` 是共享 scorer（T-10a-1 冻结），**改它要同步改两处调用**；优先只在 `summarize` 层做口径修正，不动 `passk_aggregate` 的数学。
