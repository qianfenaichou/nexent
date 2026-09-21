# T-26：决策卡生产入口取证修复（整句 seed 命中 0 + 显式 as_of 证据缺失）

**状态**: ★ 待开发（2026-09-21/22 r22 尾段实测发现；来源 = cron-supervisor F1 + 主会话 00:10-00:25 真实 UI/DB 复现）
**Blocked by**: 无（可独立修；不需要 LLM 配额也能诊断，修复后复验需 ≤4 次 LLM）
**独占文件**:
- `backend/services/knowevo/graph_store.py`（`entity_lookup` / `neighbors` 的 seed 或时钟过滤，视诊断结论）
- `backend/services/knowevo/decision_service.py`（`multi_hop` / `_resolve_clock` / `assemble_evidence`，视诊断结论）
- `backend/apps/knowledge_graph_app.py`（`render_decision_card` 的 seed 构造）
- `test/backend/services/knowevo/test_decision_card_app.py`、`test_kg_service.py`、新增/扩展 seed 与时钟用例
- `competition/docs/pitfalls.md` / `cost-ledger.md`（登记）

**待接线项**: 无
**禁改清单**: `pipeline/ablation.py` 的评测语义（E8 口径不得变）、`competition/deliverables/**`、其他 features；不 push；零新依赖

## 现象（全部可复现，务必先各自复现一遍再动手）

1. **整句 seed 命中 0（结构性拒绝）**：`POST /api/knowevo/decision/card` 把**整个问题串**交给 `entity_lookup`（`knowledge_graph_app.py:283-285`），而 `graph_store.entity_lookup` 是 `KgEntity.name.ilike('%<query>%')`。
   - cron-supervisor 实测：20 道冻结测试题逐题 `entity_lookup(tenant, <whole question>)` = **0 seeds**；
   - 同一批问题经 `ablation.py:170 extract_seed_terms()` 抽词后**能命中**（V-004 糖尿病→5、V-001 HbA1c→3/型糖尿→4、M-002 SGLT2→1、F-001 二甲双→1）；
   - 评测链（ablation/MCP）与生产链因此**不是同一条**——评测能跑、UI 面板结构性强拒。

2. **显式 `as_of` 路径证据缺失**（本主会话实测，卡片 JSON 已落盘）：
   - `q=糖尿病前期, as_of=2026-09-21T00:00`（无版本 / 有 `v1.1.0`）→ `INSUFFICIENT_EVIDENCE`，note=「关键事实项证据全缺：检索路与推理路均未取到支撑证据」；
   - 同一问题 **clock=now（不带 as_of）** → `RECOMMEND`（1 候选「生活方式干预」+ 3 条证据链，card_id `3b02b2bb…`）；
   - 该题关键治疗边「糖尿病前期患者应给予生活方式干预」实测 `valid_at=2025-01-01, invalid_at=NULL`（租户 6756b0ab），在 2026-09-21 **应有效** → 与"证据全缺"矛盾，疑为显式时钟路径缺陷（实体/边/seed 过滤之一）。

3. **旧时钟 + 版本标签部分可用（作为对照）**：`q=糖尿病前期, version=v1.1.0, as_of=2021-06-01` → 取到"范畴定义"边并给出**版本专属诚实拒绝**（note 明写"该版本仅有范畴定义（包括IGT、IFG或二者兼有），未提供任何治疗/管理候选选项"）；`knowledge_version_pinned=True`。→ 说明带版本的旧时钟路径**能工作**，缺陷只在显式时钟/seed 一侧。

## 任务（诊断优先，禁止未定位就改码）

1. **诊断**（写 file:line + SQL 证据）：分别复现现象 1/2/3，给出根因。至少回答：
   - 生产路由的 seeds 从哪来、`entity_lookup` 是否按 `valid_now(KgEntity, as_of=…)` 过滤；
   - 显式 `as_of` 在 `multi_hop` 里如何解析（`_resolve_clock(None, as_of, versions)` 的 source/pinned 判定）、`t_v` 是否真被传给 `store.neighbors(as_of=…)`；
   - 为什么同一 as_of 在"带版本"时能取到边、"不带版本"时取不到（差异必须在代码里指出来）。
2. **修复**（最小改动）：
   - ① 生产入口改为**词级 seed**（复用 `extract_seed_terms` 或等价实现），并保留"整句直配"作为兜底/补充；注意 `extract_seed_terms` 的 CJK 切分粗糙（会产出 `双胍是` 这类碎片），seed 相关性**必须**用真实命中数度量后写进 Evidence，不得假设；
   - ② 修显式时钟路径的取证缺口（按诊断结论）；
   - ③ 两侧都不许破坏既有语义：拒绝仍要拒绝（无证据=确定性拒绝，0 LLM），不得为"出卡"而放宽证据门槛。
3. **复验**（真实链路）：
   - 整句问题（如 `2型糖尿病的血糖控制目标`）→ ≥1 seed 且不再结构性拒绝；
   - `as_of=2026-09-21` 与 `clock=now` 在同一问题上的证据集合**一致**；
   - `as_of=2021-06-01` vs `as_of=2026-09-21` 的证据集合**确实不同**（T-23 版本对比据此才成立）；
   - 每步贴命令 + 关键输出；`used_tokens>0` 才能算真的走到了 LLM。

## 验收标准
- [ ] 现象 1 复现 → 修复后整句问题 seed ≥1（贴命中词与实体名）
- [ ] 现象 2 根因定位（file:line）→ 修复后显式时钟与 now 证据一致
- [ ] 现象 3 保持可用（旧时钟版本拒绝语义不回归）
- [ ] 单测 + PG 集成零回归（跑 `test_decision_card_app.py` + `knowevo/` 全量，贴数字）
- [ ] ruff 零新增；注释英文；零新依赖；未改 ablation 语义
- [ ] pitfalls/cost-ledger 各一行（token 如实：LLM=0 或实测值）
- [ ] 不伪造：修不动就把根因与建议如实写进 status，评估交回主会话

**Evidence**: <粘贴各命令输出>
