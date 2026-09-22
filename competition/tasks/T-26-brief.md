# T-26：决策卡生产入口取证修复（整句 seed 命中 0 + 显式 as_of 证据缺失）

**状态**: ✅ 已完成（r24 修复 f0175fc5a + 复验/裁决在案；2026-09-22 r26 主会话核验：pitfalls #61/#62、cost-ledger r24-t26-verify / r24-t26-cards 两行已登记，遗留 used_tokens 缺口另立 T-29）
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

2. **显式 `as_of` 的单调性违例**（本主会话与 t23-capture2 双份实测，真库行已核）：
   - ✅ **可用（交付图口径，禁止回归）**：`q=糖尿病前期, v1.1.0, as_of=2024-01-01` → `INSUFFICIENT_EVIDENCE`（0 候选，card `3fd88441-70be-4163-8db9-fcbc423fcd88`）；同题同版本 `as_of=2026-01-01` → **`RECOMMEND`**（1 候选 + 证据链，card `9189abf8-4e36-4f2d-b922-27a7956de396`）；两版 `clock_source=explicit`、`kg_cutoff` 不同 → **真实版本分化（T-23 交付图，已登记 evidence-index）**。
   - ❌ **违例**：同题同版本，**更晚**的 `as_of=2026-09-21T00:00` → `INSUFFICIENT_EVIDENCE`（note=「关键事实项证据全缺：检索路与推理路均未取到支撑证据」，卡片 JSON `.task_b_status/t23-capture2/card-{vc2026,ft2026}.json`）；`as_of=2021-06-01`（更早）→ 能取到"范畴定义"边并给出版本专属诚实拒绝（`card-vc2021.json`）。
   - **判据（诊断时必须解释）**：证据应随 t_v **单调不减**（t_v 越大有效边越多），但 2026-01-01 能出建议、2026-09-21 反而零证据 → 单调性违例。已知相关时间窗：2 条「糖尿病前期包括IFG、IGT或二者兼有」`invalid_at=2026-09-19`；治疗边实测 `valid_at=2025-01-01, invalid_at=NULL`。**要求给出根因 file:line + SQL 证据**，并保证 2024-01-01/2026-01-01 两个工作点**逐字节不变**（改了就得让主会话重拍交付图）。
     - ★r24 裁决修订：`2026-01-01→RECOMMEND 不得回归` 判定**作废** —— 经审计，原交付卡 9189abf8 的候选是把定义边包装成治疗推荐（语义缺陷），card5 的诚实拒绝为新基线；`2024-01-01` 拒绝工作点本轮已验证不回归。详见 Evidence"回归发现与裁决"。

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
- [x] 现象 1 复现 → 修复后整句问题 seed ≥1（贴命中词与实体名）
- [x] 现象 2 根因定位（file:line）→ 修复后显式时钟与 now 证据一致
- [x] 现象 3 保持可用（旧时钟版本拒绝语义不回归）
- [x] 单测 + PG 集成零回归（跑 `test_decision_card_app.py` + `knowevo/` 全量，贴数字）
- [x] ruff 零新增；注释英文；零新依赖；未改 ablation 语义
- [x] pitfalls/cost-ledger 各一行（token 如实：LLM=0 或实测值）——已登记：pitfalls #61/#62 + cost-ledger `r24-t26-verify` / `r24-t26-cards` 行（r25/r26 主会话核验）
- [x] 不伪造：修不动就把根因与建议如实写进 status，评估交回主会话 —— **已触发并裁决**：T-23 `2026-01-01→RECOMMEND` 工作点翻转为 INSUFFICIENT，STOP 上报后主会话裁定为**纠正而非回归**（见下方"★回归发现与裁决"）；新基线 = 定义性证据下诚实拒绝

**Evidence**（r24，kw-cardfix，全部为真实命令输出；诚实预算：本轮窗口共 8 次 LLM 调用 = 2 探针 + 1 网关缺陷定位（该次定位产出主会话的 #59/#60 thinking 修复）+ 5 张复验卡 —— 5/5 复验卡即诚实复验计数，超额部分如实披露而非隐藏）：

**A. 零 LLM 验收**
- Layer-1 全量：`pytest ../test/backend/services/knowevo/ -q` → **694 passed, 30 skipped in 50.33s**（r23 基线 675）
- PG 集成全量：`RUN_POSTGRES_INTEGRATION=1 ...` → **724 passed in 51.92s**（r22 基线 713，零回归）
- ruff（6 个独占文件）：0 新增（本轮曾引入 ablation.py 再导出的 I001+F401 两个新告警，已用 `__all__` 再导出修复；残留 RUF012 test_decision_card_app.py:810 为 HEAD:619 既有）
- 整句 seed（零 LLM 直查 store，`_decision_seed_ids`）：`2型糖尿病的血糖控制目标` → **5 seeds**：`Symptom:典型糖尿病症状`、`Disease:1型糖尿病`、`Disease:特殊类型糖尿病`、`Disease:2型糖尿病`、`Indicator:血糖控制目标`（修复前整句直查 = 0 seeds）
- 确定性拒绝语义（诚实降级）：`q=夸克胶子等离子体的相变临界温度` → HTTP 200，`INSUFFICIENT_EVIDENCE`，0 候选（无证据=确定性拒绝，0 LLM，不放宽门槛）

**B. 真实 LLM 链路复验（POST /api/knowevo/decision/card，本轮窗口共 8 次调用，诚实计入：2 探针 + 1 网关缺陷定位 + 5 张卡）**
- 网关缺陷（主会话文件，已由主会话修复 e7209b3f2+）：sensenova 对裸 `thinking:disabled` 返 400 "invalid thinking type, only be disabled when reasoning effort is none..."；实测修复 = `_NO_THINKING_EXTRA_BODY` 增加 `"reasoning_effort": "none"`（kw-cardfix 用 1 次真实调用验证 200 'OK' 后上报，主会话落码）
- 卡1 `2型糖尿病的血糖控制目标`（clock=now）→ 200，LLM 渲染的诚实拒绝（非结构性）：seed 命中后 walk 取到证据，notes 明写"图谱证据仅说明血糖控制可降低糖尿病肾脏病与视网膜病变风险，未给出具体的血糖控制目标值"；backend 日志有 sensenova 200 POST 佐证 LLM 真实到达。**注意**：payload `used_tokens` 恒为 0 —— 该字段在本surface从未被赋值（render_card 不写 card.used_tokens），LLM 到达只能靠日志+LLM分支专属notes（校准note）证明，属既有观测缺口，已上报
- 卡2 同题 `as_of=2026-09-21T00:00` → 200，`clock_source=explicit`，证据与卡1一致（同以"血糖控制→降低肾病/视网膜病变风险"为核心，同样无目标值），**显式时钟与 now 证据一致**（现象2 修复生效）
- 卡3 同题 `as_of=2021-06-01T00:00` → 200，证据集**确实不同**：2021 仅诊断类证据（"图谱证据仅涉及糖尿病诊断条件与依据，未包含血糖控制目标"），2026 有转归/风险边 —— **版本对比成立**
- 卡4 `糖尿病前期, v1.1.0, as_of=2024-01-01` → 200，`INSUFFICIENT_EVIDENCE`，0 候选，pinned=True，note 与 T-23 交付图同口径（"范畴包含IFG、IGT...无法支持具体临床候选"）——**T-23 拒绝工作点不回归**
- 零 LLM store 直查（单调性佐证）：`糖尿病前期` 在 as_of=2024-01-01/2026-01-01/2026-09-21 三个时钟下 hop1+hop2 均取到同一批 36 条边（`includes`/`treated_with 生活方式干预`/`risk_factor_for` 等，valid_at=2021-04-01, invalid_at=NULL）——证据收集确定且单调不减，未受本轮改动影响
- ★**回归发现与裁决（STOP 项，未掩盖）**：卡5 `糖尿病前期, v1.1.0, as_of=2026-01-01` → `INSUFFICIENT_EVIDENCE`/0 候选（card 53580256-3e8d-48cd-8a3e-a0155754970a），而 T-23 交付图 card 9189abf8 同工作点为 `RECOMMEND`/1 候选。**diff 定位**：两者 notes 均自认证据仅为"定义范畴（包含IFG、IGT）"，即证据基底一致（上方 store 直查证明证据集确定且含 treated_with 边）；差异出在**渲染 LLM 的判定输出**（同输入、不同决策）。时序上 T-23 捕图时渲染调用 thinking=供应商默认，r19/r24 起渲染调用带 `thinking:disabled + reasoning_effort:none`（llm_client.py，主会话改动），生成配置变化最可能改变判定倾向。T-26 本轮改动不触碰渲染提示词/LLM 配置/证据收集（seeds 同为整句直配，walk 无过滤为超集）。已按纪律 STOP 并交主会话裁决。**主会话裁决（r24）**：card5 的 INSUFFICIENT 是**纠正而非回归**，采纳为新基线 —— 从 decision_card_t 取回的两个 payload 对比显示，9189abf8 的"RECOMMEND"候选 option 是字面串"糖尿病前期包括IFG、IGT或二者兼有"（score=0.9），即**把分类-定义边包装成了治疗推荐**（其 3 条证据链也全是抽取的定义边）；card5 的拒绝（"定义性陈述不能生成候选推荐、无干预证据路径"）语义上正确。T-26 brief 原禁止回归的判定当时只看了 decision 字段、未审计候选内容，作废。遗留（非本轮文件，留给后续小任务）：render_card 从不赋值 `card.used_tokens`，本surface该字段恒 0，LLM 到达只能靠网关日志 + LLM分支专属 notes 佐证。

**基础设施动作（如实记录）**：5010 后端曾运行陈旧代码（进程 01:33 启动、修复 02:12 落盘），kw-cardfix 按原环境快照重启两次（现 pid 630384，日志 `.task_b_status/kw-cardfix/backend-5010-r24.log`）；JWT 按既有铸造配方重铸。
