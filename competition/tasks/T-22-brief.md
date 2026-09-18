# T-22：消融 A1-A4 × 题型（D1 是前置）

**状态**: ★ 待开发（2026-09-18 调度会话）
**Blocked by**: T-18b（事实业务时间）、T-18c（诚实分母 + 答案级溯源）、T-18d（权威度检索）
**独占文件**（本任务创建/修改）:
- `backend/services/knowevo/pipeline/ablation.py`（**新建**：A1-A4 四级 runner）
- `backend/services/knowevo/pipeline/eval_e1.py`（**加性**：抽出可复用的 `run_question` 契约给 ablation 复用，不改口径逻辑——T-18c 已定型）
- `backend/services/knowevo/pipeline/eval_v1.py`（**加性**：by-type × by-level 聚合）
- `test/backend/services/knowevo/test_ablation.py`（**新建**）
- `competition/deliverables/e2-ablation-report.json`（**新建**）
- `competition/docs/cost-ledger.md` / `evolution-log.md`（登记 E2）
- `competition/docs/e2-ablation.md`（**新建**：消融结论叙事，供初赛文档）

**待接线项**: 无
**禁改清单**: `backend/consts/const.py`、`apps/*`、既有迁移、`decision_service.py` 算法、`e1_retrieval.py`（只调用）、`eval_run_t` schema（只 INSERT）
**允许的新依赖**: **无**

**要构建的行为**（用户视角端到端）:
1. 同一评测集、同一生成模型与 prompt，跑**四个配置**（02-技术方案 §3.5）：
   - **A1 纯 RAG**：只 `e1_retrieval` 检索（T-10a-2 已交付）
   - **A2 +图检索**：+ `kg_search`（1 跳邻域）
   - **A3 +多跳推理**：+ `kg_multi_hop` 束搜索
   - **A4 完整双驱动**：+ 路由器 + 证据链融合 + 反事实 + **版本钉住**
2. 每级报 `pass^2`（主）/`pass^3`/`acc`（**同时报 `n_judged`**）/溯源完整度（`trace_machine` + `trace_answer`）/p95 延迟/token；
3. **按题型 × 按级别**交叉表（F/M/V/X × A1-A4）——这是"版本钉住对 V 题有效、对 F 题无回归"的直接证据；
4. **★E8 版本钉住 on/off**：A4 内部再切 pinned on/off 两臂，在 V 题上对比，F 题作回归护栏；
5. 结果落 `eval_run_t`（`config.ablation_level` ∈ `{A1_pure_rag, A2_graph, A3_multihop, A4_full}`），产出 `e2-ablation-report.json`。

**背景（现状核验，2026-09-18）**:
- `eval_e1.py` 已实现 A1（`config.ablation_level="A1_pure_rag"`），有 `run_question`（:264）、`_call_with_retry`（:355）、`summarize`（:405）、`evaluate`（:518）。**A2/A3/A4 runner 不存在**。
- `DecisionService.multi_hop`（:404）/`assemble_evidence`（:796）/`render_card`（:872）已交付（T-09），`kg_search`/`kg_multi_hop` MCP 工具双注册（T-07b/T-09）。**消融只需编排这些既有能力，不新写算法**。
- `VersionClock`/`pin_predicate`/`edge_in_version`（version_pin.py）是版本钉住的**单一开关**（T-09 简报称"ablation pinned on/off 有 one clean switch to flip"）。
- 真库：`kg_relation_t` 294 行、`kg_entity_t` 524 行、`decision_card_t` 76 张——A2/A3/A4 有真实图可用。
- 02-技术方案 §3.5 预期：A4 vs A1 `pass^2 +10pp`、溯源 `+30pp`、延迟 `+2×`（**预期值，实测不符须如实报告**）。
- 02 §6.2 诚实口径：120 题 × 3 重复 = 360 观测，"检出 5pp 不足，降格为检出 ≥8pp + 报 95% CI"。
- **D1 是前置**：若 valid_at 仍是墙钟，pinned on/off 无差异（Δ≈0），E8 失效。T-18b 必须先完成并用其"判别性 SQL"验证。

**验收命令**:
```bash
# 1. 单测（四级配置编排 + by-type×by-level 聚合 + pinned on/off 开关）
cd backend && uv run pytest ../test/backend/services/knowevo/test_ablation.py -q --no-header
# 2. ruff
cd backend && uv run ruff check services/knowevo/pipeline/ablation.py
# 3. ★真跑：四级消融（20 题种子集 × 3 runs，先小样本验证再全量）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> KW_LLM_SMALL_MODEL_ID=7 KW_LLM_MID_MODEL_ID=7 KW_LLM_LARGE_MODEL_ID=8 \
  uv run python -m services.knowevo.pipeline.ablation --levels A1,A2,A3,A4 --runs 3 --top-k 5 --pace 8
# 4. ★E8 版本钉住 on/off（V 题专项）
cd backend && POSTGRES_HOST=... uv run python -m services.knowevo.pipeline.ablation \
  --levels A4 --pin on,off --types V,F --runs 3
# 5. PG 集成全量
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> RUN_POSTGRES_INTEGRATION=1 \
  uv run pytest ../backend/../test/backend/services/knowevo/ -q --no-header
```

**验收标准**:
- [ ] `ablation.py` 四级配置可独立运行、可组合；每级落一条 `eval_run_t`（`config.ablation_level` 正确）
- [ ] 交叉表：`by_type × by_level` 四型 × 四级，每格报 `acc` + `n_judged`（**零判定格显式标 `insufficient_data`，不消失**）
- [ ] 每级同时报 `trace_machine` 与 `trace_answer`（T-18c 口径）
- [ ] **E8 pinned on/off**：V 题两组分数 + F 题回归护栏，`pin` 开关走 `version_pin` 单一入口（不另写谓词）
- [ ] 真跑数字落 `e2-ablation-report.json`，`cost-ledger.md` 登记 token/延迟，`evolution-log.md` 登记 E2
- [ ] `e2-ablation.md` 写结论：A4 vs A1 的 Δpass^2 + 95% CI；**实测与预期不符时如实写"预期 +10pp，实测 X"**
- [ ] 复现性：至少两次独立跑分（不同端点或不同 judge）结果一致（沿用 T-10a-2 口径）
- [ ] **D1 前置校验**：跑消融前先跑 T-18b 的判别性 SQL，确认 t_v 两个取值下纳入事实数不同；若相同 → **停下报告，D1 未修好**
- [ ] 零新依赖；零新增 env；`eval_run_t` 只 INSERT

**Evidence**: <粘贴 pytest 输出 / 四级 acc+pass2+n_judged / by_type×by_level 交叉表 / E8 V 题 on/off / 两次跑分一致性 / 判别性 SQL>

---

## 实现备注（给实现 agent）

1. **诚实口径是第一原则**（铁律 6）：`acc` 必须与 `n_judged` 并列；`n_judged < n_expected` 时必须说明原因（平台故障 vs 非平台错误）。**禁止用"排除运行"抬高准确率**（这正是 D2 的病灶，T-18c 已修，本任务不得回退）。
2. **样本量与显著性**：20 题种子集 → 单轮不足以检出 5pp。报告用 pass^2（3 次重复）并给 95% CI（Wilson 区间），**答辩话术按 02 §6.2"报告置信区间与效应量，不宣称 5pp 显著"**。
3. **成本预算**：四级 × 20 题 × 3 runs ≈ 240 run，A3/A4 每 run 含多跳 LLM 调用，token 显著高于 A1。先跑 2 题 × 1 run 冒烟确认链路与成本，再全量；`--pace` 防限流（T-10a-2 坑#39）。
4. **A2/A3/A4 的"融合"别过度实现**：02 §3.4 的双路融合是"文档级 + 图谱路径节点"两通道，冲突标 contested。**复用 `assemble_evidence`**，不要在 ablation 里另写融合。
5. **E8 是"可进化"的最硬证据**（02 §3.2 明文）：V 题提升 + F 题不回归 = 版本钉住有效。这组数字进初赛文档维度③。
