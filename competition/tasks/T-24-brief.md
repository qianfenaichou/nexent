# T-24：抽取运行诊断落库（P1-4 产品侧，r22 轮）

**状态**: ✅ 已完成（r22 实现 + 提交 ac233818e「T-24 product-side extract diagnostics in kg_extract_run_t (kw_009, pitfalls #52/#55)」；2026-09-22 r26 主会话核验简报 Evidence 在案后翻转）
**Blocked by**: 无（r21 已在调用层落地 `call_with_usage` 的 reasoning_tokens/finish_reason）
**独占文件**（本任务创建/修改）:
- `deploy/sql/migrations/v2.5.5_kw_009_extract_run_diagnostics.sql`（**新建**：幂等只加列）
- `backend/database/knowevo_db.py`（**加性**：`KgExtractRun` 新增诊断列）
- `backend/services/knowevo/kg_service.py`（**加性**：`record_extract_run` 接受并写入诊断字段）
- `backend/services/knowevo/llm_client.py`（**加性**：若需要，暴露"最近一次调用的诊断"读取面，不改 `__call__` 返回类型）
- `backend/services/knowevo/pipeline/ingest_graph.py`（**加性**：把调用级诊断聚合到 span 级并随 `record_extract_run` 落库）
- `test/backend/services/knowevo/test_kg_service.py` / `test_knowevo_models.py` / 新 `test_llm_client.py` 增补
- `competition/docs/pitfalls.md` / `cost-ledger.md`（登记）

**待接线项**: 无
**禁改清单**: 既有迁移文件、`apps/*`、`decision_service.py` 算法、`eval_*` 口径逻辑、`kg_extract_run_t` 既有列语义（只加列不改名）
**允许的新依赖**: **无**

**要构建的行为**（用户视角端到端）:
每条 `kg_extract_run_t` 行（每次 span 抽取的台账行）除了 `tokens_spent`，还要能回答"这次为什么没抽出东西"：
1. span 级记录本次抽取窗口内的调用聚合：`llm_calls`（次数）、`empty_content_calls`（空正文次数）、`finish_reasons`（各 finish_reason 出现次数的 JSONB）、`reasoning_tokens`（推理 token 合计）；
2. 数据源 = r21 已落地的调用层 `call_with_usage` 返回的 `reasoning_tokens` / `finish_reason` + `empty_content_counts()`；
3. **向后兼容**：老行/不传诊断的调用方行为不变（新列可空或带默认值）；`LlmRouter.__call__` 返回类型保持 `str`（调用方 `await self.llm(...)` 不受影响）；
4. 迁移幂等：`kw_009` 跑两遍不报错（`ADD COLUMN IF NOT EXISTS`）。

**背景（现状核验，2026-09-21 r22）**:
- r21 已落地：`llm_client.call_with_usage` 返回 `tuple[str, dict]`（新增 `reasoning_tokens:int` / `finish_reason:str|None`）；`empty_content_counts() -> {kind: {calls, empty_content}}`；`event=llm_empty_content` 日志。证据 = `test_llm_client.py` 14 passed。
- `LlmRouter.__call__`（llm_client.py:236-247）目前 `content, _usage = await self.call_with_usage(...)` 丢弃 usage → 走 `__call__` 的调用方（含 `KGService.extract` :791 / `_adjudicate_llm` :958）看不到诊断。
- 落库点：`KGService.record_extract_run`（kg_service.py，签名为 `(tenant_id, run_id, span_hash, channel, tokens_spent)`）；产品管线调用点 = `pipeline/ingest_graph.py:146`；产品管线自带 LLM 包装类在 `ingest_graph.py:52`（`__call__(prompt, *, kind, **kwargs)`）。
- 驱动侧参考实现（**仓库外**，非 git 管理）：`.task_b_status/ingest-r7/run_real_ingest_paced.py` 已把 `reasoning_tokens`/`finish_reasons`/`llm_empty_content_by_kind` 写进 `usage-<pid>.json`（r21 冒烟通过）——**本任务是把同一形态落到产品表**，不是复制驱动。
- 最新迁移编号 = `v2.5.5_kw_008_alignment_index.sql` → 本任务用 `kw_009`。
- 迁移铁律：既有迁移不可改；只允许新增；本地 scratch 库跑两遍验证幂等。

**验收命令**:
```bash
# 1. 单测
cd backend && uv run pytest ../test/backend/services/knowevo/test_kg_service.py ../test/backend/database/test_knowevo_models.py -q --no-header
# 2. ruff
cd backend && uv run ruff check database/knowevo_db.py services/knowevo/kg_service.py services/knowevo/llm_client.py services/knowevo/pipeline/ingest_graph.py
# 3. 迁移幂等（scratch 库，两遍）
PGPW=$(grep -m1 '^NEXENT_POSTGRES_PASSWORD=' ../deploy/env/.env | cut -d= -f2)
docker exec -e PGPASSWORD=$PGPW nexent-postgresql psql -U root -d nexent -c "select 1" # 连通性
# kw_009 应用两遍（详见实现备注 4 的具体命令），第二遍必须 0 error
# 4. PG 集成全量（本任务改表，必须跑——坑 #25）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=$PGPW RUN_POSTGRES_INTEGRATION=1 \
  uv run pytest ../test/backend/services/knowevo/ -q --no-header
```

**验收标准**:
- [ ] `kw_009` 迁移幂等（两遍 0 error），真库 `kg_extract_run_t` 新列可见（`\d nexent.kg_extract_run_t` 证据）
- [ ] `record_extract_run` 可选诊断参数可写、老调用（不传）不炸；`LlmRouter.__call__` 签名/返回类型不变
- [ ] `ingest_graph.py` 产品管线把 span 内跨调用聚合后的诊断落进新列（单测锁定聚合语义：N 次调用含 M 次空正文 → `llm_calls=N` / `empty_content_calls=M`）
- [ ] 单测新增 ≥4 条（聚合语义、老调用兼容、模型列存在、空正文计数）
- [ ] PG 集成全量零回归（对照 r21 基线：knowevo Layer-1 675 passed）
- [ ] ruff 零新增告警；注释英文；无新依赖、无新环境变量
- [ ] `pitfalls.md` 登记（#52/#55 的产品侧闭环）+ `cost-ledger.md` 备注（无 LLM 真实调用，token=0）

**Evidence**（r22 实现，全程无真实 LLM 调用，token=0）:
- 单测（验收 #1，`test_kg_service.py` + `test_knowevo_models.py`）：`90 passed, 22 skipped in 0.38s`；`test_llm_client.py`：`15 passed in 1.13s`。新增 10 条 = span 聚合语义 4（N 次含 M 次空正文、reset 开新窗、空 dict=无正文 vs `{"entities":[]}`=无实体、router-like 回灌）+ `record_extract_run` 老调用兼容 1 / 诊断落库 1 / FakeStore 回环 1 + 模型列 1 + 迁移契约 1 + `last_usage()` 1。
- ruff（验收 #2 四文件）：`Found 10 errors`，全部落在 `database/knowevo_db.py` 既有行（UP035/UP006/PIE790/RUF012/UP045），改动区无新增；逐文件 HEAD==NOW 基线（`knowevo_db/kg_service/llm_client/ingest_graph/test_kg_service/test_llm_client/test_knowevo_models` = 10/0/0/0/0/0/25，两侧一致）= **零新增**。
- 迁移幂等（scratch=真库 `nexent`，kw_009 两遍）：pass1 `BEGIN / CREATE SCHEMA (NOTICE: already exists) / ALTER TABLE / COMMENT×4 / COMMIT`；pass2 四条 `NOTICE: column "..." of relation "kg_extract_run_t" already exists, skipping` + `ALTER TABLE / COMMENT×4 / COMMIT`，**0 error**。
- 真库表结构 `\d nexent.kg_extract_run_t`：新增 `llm_calls integer not null default 0`、`empty_content_calls integer not null default 0`、`reasoning_tokens integer not null default 0`、`finish_reasons jsonb`（nullable），既有列语义未动。
- PG 集成全量（验收 #4，`POSTGRES_DB=nexent`）：`713 passed, 1 warning in 49.13s`（0 failed；warning 为既有 authlib 弃用告警）。

---

## 实现备注（给实现 agent）

1. **"无正文" vs "无实体"**：本任务的唯一目的就是让这两件事在**产品台账**里可区分（pitfalls #52 沉淀机制）。`empty_content_calls > 0` 且实体数为 0 → "无正文"；`empty_content_calls == 0` 且实体数为 0 → "模型说没有"。不要发明超越这个目的的设计。
2. **别再动 `call_with_usage` 契约**（r21 刚定，有 14 条测试锁定）；只需在 `LlmRouter.__call__` 或 `KGService` 的取用处**加**一个可读诊断面（如 `last_usage()` / 显式 `call_with_usage` 调用），保持 `__call__` 兼容。
3. **列设计建议**（可微调，但需在 docstring 说明）：`llm_calls INTEGER NOT NULL DEFAULT 0`、`empty_content_calls INTEGER NOT NULL DEFAULT 0`、`reasoning_tokens INTEGER NOT NULL DEFAULT 0`、`finish_reasons JSONB`。默认值保证老行兼容。
4. **迁移幂等验证命令参考**：
   ```bash
   docker exec -e PGPASSWORD=$PGPW -i nexent-postgresql psql -U root -d nexent -v ON_ERROR_STOP=1 \
     -f - < deploy/sql/migrations/v2.5.5_kw_009_extract_run_diagnostics.sql   # 第 1 遍
   docker exec -e PGPASSWORD=$PGPW -i nexent-postgresql psql -U root -d nexent -v ON_ERROR_STOP=1 \
     -f - < deploy/sql/migrations/v2.5.5_kw_009_extract_run_diagnostics.sql   # 第 2 遍（必须 0 error）
   ```
   注：仓库迁移由 `migrate` 链加载，本任务只验证 SQL 幂等 + ORM 对齐；不要改 deploy 的迁移加载顺序文件。
5. **不做的事**：不写看门狗/心跳；不 push；不改 `eval_*`；不顺手改 `e1_retrieval`/`decision_service`；不引入新依赖。
6. **参考文档**：`competition/docs/pitfalls.md` #52/#54/#55；`.task_b_status/resume-plan.md` §6。
