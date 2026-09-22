# T-29：决策卡 used_tokens 观测缺口修复（零 LLM，pytest-only）

**状态**: ✅ 已完成（2026-09-22 r26，kw-dev 成员 t29-usedtokens，本地提交 887912443；主会话核验：提交仅含 2 个独占文件，Evidence 真实，台账行已登记 cost-ledger `t29-usedtokens` 行）
**Blocked by**: 无
**独占文件**（本任务创建/修改）:
- `backend/services/knowevo/decision_service.py`（**加性**：render_card 的 usage 赋值）
- `test/backend/services/knowevo/test_decision_card_app.py`（加性）或新建 `test_used_tokens.py`
- `.task_b_status/t29-usedtokens/**`（运行工件，不进 git；**该目录已随 `.task_b_status/` 于 2026-09-22 归档至工作区根 `archive/ai-loop-scratch-2026-09/`**）

**待接线项**: 无
**禁改清单**: `backend/apps/knowledge_graph_app.py`、`pipeline/ablation.py` 评测语义、`llm_client.py` 返回类型与既有签名、前端全部、`frontend/public/locales/**`（主会话有未提交改动）、其他任务独占文件。**本任务不起后端服务**（5010 生命周期归 T-27 独占），pytest-only。
**允许的新依赖**: **无**；**LLM 调用 = 0**（单测 mock）

## 背景（现状核验 2026-09-22 r26）
- `llm_client.call_with_usage` 已返回 `tuple[str, dict]`（含 tokens/reasoning_tokens/finish_reason，r21 落地）。
- 决策卡渲染链 `render_card`（decision_service）生成 `decision_card_t` 行，但从不写 `used_tokens` 字段 → 落库后恒 0，无法从卡面判断「是否真的走到了 LLM」（T-26 复验时被迫用网关日志佐证）。
- 确定性拒绝路径（无证据 → 0 LLM 直接 INSUFFICIENT）是**诚实特性**：`used_tokens=0` 在该路径语义正确，不得篡改。

## 任务（最小改动）
1. render_card 内：凡真实发起 LLM 渲染调用的路径，把本次调用的 usage（`call_with_usage` 返回的 tokens；如有分项一并）聚合赋给 `card.used_tokens`（落库与 payload 双侧一致；若 payload 有既有 schema 校验，加性字段处理）。
2. 未调 LLM 的确定性拒绝路径：显式保持/写入 0（诚实口径，并在注释说明 zero-LLM-deterministic-refusal）。
3. 单测：①LLM 路径 used_tokens = mock usage 值；②确定性拒绝路径 used_tokens = 0；③卡其余语义字段零变化。
4. 回归：`pytest ../test/backend/services/knowevo/ -q` 全量绿（从 backend 目录跑），ruff 独占文件零新增。

## 验收标准
- [ ] LLM 路径 used_tokens 真实赋值（单测断言）
- [ ] 确定性拒绝路径 used_tokens = 0（单测断言）
- [ ] pytest knowevo 全量零回归（贴数字）+ ruff 零新增
- [ ] 注释英文；不改变卡语义/决策逻辑/证据链（只加观测）
- [ ] 独占文件边界外零改动；不起后端；不 push 不合并；台账行报主会话登记

**Evidence**（r26，t29-usedtokens，真实命令输出；LLM=0，台账行已由主会话登记 cost-ledger `t29-usedtokens`）:
- 实现（本地提交 **887912443**，仅 2 个独占文件，+230/-2）：
  - `decision_service.py` 加性：新增 `_call_llm_with_usage`——优先走 `llm.call_with_usage` seam（r21 已有）取 `(raw, usage)`；无 seam 时回退冻结 str 契约 → usage 空 dict → 上层报 0（**measured-only，绝不估算**）；
  - render_card LLM 渲染路径：`card.used_tokens = usage.input_tokens + output_tokens`（与 ablation.py 账本口径一致，reasoning_tokens 不另加）；payload 与落库同源 `to_payload`，双侧天然一致；`DecisionCardContract` 本就含 `used_tokens`，`validate_card` 通过；
  - 确定性拒绝路径：显式 `card.used_tokens = 0`（注释 zero-LLM-deterministic-refusal，诚实口径保持）。
- 新测试 `test_used_tokens.py` 6 个：LLM 路径 mock usage 赋值 / payload+contract 双侧一致 / 拒绝路径 0 且零 LLM 调用 / 冻结契约回退报 0 / 空 usage 报 0 / **卡语义零变化**（decision/candidates/notes/route/disclaimer 等与冻结契约渲染逐项相等）。
- 验收：`pytest ../test/backend/services/knowevo/ -q` → **700 passed, 30 skipped, 1 warning in 57.68s**（T-26 基线 694 + 6 新增，全量零回归）；`ruff check` 独占文件两枚 All checks passed；零新依赖；注释英文；5010 未启动（T-27 独占）；`knowledge_graph_app.py`/`ablation.py`/`llm_client.py`/`frontend/**` 零触碰。
