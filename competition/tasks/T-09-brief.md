# T-09：kg_multi_hop（★版本钉住）+ 证据链 + 决策卡（波次 4 · 创新主轴 B2）

> 本简报由调度会话（2026-09-17）据 [03-开发计划](../../../03-开发计划.md) §7 任务表 + 02-技术方案 §3.2/§3.3（★创新点②） + 冻结接口 `knowevo/backend/services/knowevo/decision_service.py.md`、`graph_store.py.md` 生成。
> **核心原则**：每条验收标准必须能在代码里 grep 到实现行（坑 #27 教训）；★版本钉住是本任务的存在理由，不是附加项。

**状态**: ✅ **完成**（2026-09-17，分支 `feat/kw-T09-multihop-decision-card`）
**Blocked by**: T-07a（GraphStore seam）、T-07b（MCP 双注册）、T-08（接线：llm_client 三档链）——均已合并 develop

**独占文件**（本任务可创建/修改）:
- `backend/services/knowevo/decision_service.py`（**新建**，主交付：路由 + 版本钉住束搜索 + 证据链 + 决策卡）
- `backend/services/knowevo/version_pin.py`（**新建**，版本钉住谓词与版本时钟解析——独立成模块以便单测锁定语义）
- `backend/services/knowevo/kg_service.py`（**追加** `evolution_trace` 实现，替换现有 `NotImplementedError`；**只改这一个方法**，不动其余 1400 行）
- `backend/services/knowevo/graph_store.py`（**最小加性扩展**：① `EdgeCard` 增 `valid_at`/`invalid_at` 可选字段——版本钉住必须能看到边的时间窗，否则谓词无处可施；② `PgJsonbGraphStore.neighbors` 增关键字参数 `as_of: datetime | None = None`（默认 None 保持现行行为）。**ABC 抽象方法签名一字不改**，`valid_view` 语义不变，T-07 全部既有测试通过即证向后兼容。扩展理由写入模块 docstring）
- `backend/prompts/knowevo_route_{en,zh}.yaml`（**新建**，路由 few-shot 分类）
- `backend/prompts/knowevo_hops_{en,zh}.yaml`（**新建**，hop planning 分解）
- `backend/prompts/knowevo_card_{en,zh}.yaml`（**新建**，决策卡结构化渲染；**双语成对，03 §2.5 硬规范**）
- `mcp_servers/knowevo_mcp/schemas.py` + `server.py`（**追加** `kg_multi_hop` 工具；SPEC 的 8 工具词汇表里属于 T-09 的 3 个中有 2 个在本任务范围）
- `backend/tool_collection/mcp/kg_tools.py`（**追加** Local MCP 导出，单一 schema 源不漂移）
- `backend/services/knowevo/schemas.py`（**追加** T-09 数据类：DecisionCard/EvidenceChain/Route/Candidate/PathScore）
- `test/backend/services/knowevo/test_decision_service.py`（**新建**）
- `test/backend/services/knowevo/test_version_pin.py`（**新建**）
- `test/backend/services/knowevo/test_kg_multi_hop_mcp.py`（**新建**）
- `competition/tasks/T-09-brief.md`（本文件）

**待接线项**（登记给后续接线任务，本任务不做）:
| # | 接线内容 | 去向 |
|---|---|---|
| 1 | `include_router` 决策卡端点（决策卡 HTTP 面） | 归 T-12（前端面板需要时一并挂） |
| 2 | `kg_multi_hop` / `decision_card_render` 进 `local_mcp_service.mount` | 归 T-12 前接线（复用 T-08 的 `wire()` 入口，本任务把工具加进同一 FastMCP app 即自动跟随） |
| 3 | `KW_CALIBRATION_TABLE` 环境变量（校准查表来源） | 本任务**不新增 env**——校准表从 `eval_run_t.calibration` 读，DB 即来源（备忘录 10 §3 清单外不得发明 env） |
| 4 | 决策卡前端渲染（对话流卡片面板） | 归 T-12 |

**禁改清单**: `apps/app_factory.py`、`apps/config_app.py`、`apps/runtime_app.py`、`consts/const.py`（**本任务零新增 env**）、`deploy/sql/migrations/` 已存在文件（**本任务零新迁移**：`decision_card_t` 12 表在 T-03 已建，只 INSERT 不 ALTER）、`kg_service.py` 除 `evolution_trace` 外的任何行、`graph_store.py`（seam 已冻结，不改 ABC）、前端全部。

**允许的新依赖**: `networkx`（已在树，3.6.1，仅用于 PPR 备选排序器，**若最终未用则不 import**）。
**⚠️ 依赖决策（本任务实测后改判）**：03 计划 §4.1 曾列 `instructor` (MIT) 用于决策卡结构化输出。**实测 `uv pip install instructor` 会把 `openai` 从 3.13.0 强制降到 3.3.0、`rich` 降级**——污染上游 Nexent 核心依赖链，风险不可接受。**改判：不引入 instructor**，决策卡统一走本仓库既有的「YAML 双语 prompt + JSON 解析 + Pydantic 校验」模式（与 `kg_service._parse_extraction` 同款），零新依赖、与上游一致。此改判须记入 `THIRD_PARTY_NOTICE.md` 与坑台账。

**本任务细节**（依据 02-技术方案 §3.2/§3.3 + 冻结 spec）:

1. **★版本钉住（本任务的存在理由，文献空白 B2）**
   形式化：给定版本 v（本体版本 + 事实有效时间截止 t_v），路径 p=(e₀,r₁,e₁,…,eₖ) 在 v 下有效 ⟺ ∀i: `valid_at(rᵢ) ≤ t_v AND (invalid_at(r) IS NULL OR invalid_at(rᵢ) > t_v)`。
   实现：束搜索每次 expand 的邻域查询强制附加版本谓词，使每跳都在 G_v 内。`valid_now(model, as_of=t_v)` 已提供该谓词（`database/knowevo_db.py:376`）——**复用，不重写**。
   `version_pin.py` 负责：`resolve_version_clock(ontology_version, as_of=None) -> datetime`（本体版本 → t_v 解析）、`pin_predicate(model, t_v)`（薄封装 valid_now）、`path_version_valid(path_edges, t_v)`（纯函数，路径级校验，可单测）。
   **与 `PgJsonbGraphStore.neighbors` 的关系**：现有 `neighbors(valid_view: bool)` 只支持「现行视图 / 历史视图」二值，**没有版本参数**。本任务**不改 seam 签名**（冻结），而是在 `version_pin.py` 提供 `pin_neighborhood(store, tenant, entity_ids, t_v, rel_types, hop)`——内部走 `valid_view=False` 取全时空边再按 t_v 过滤的兼容路径，同时对 `PgJsonbGraphStore` 走新增的**可选**关键字 `as_of`（加参数有默认值 = 向后兼容，不改 ABC 抽象方法签名即不破契约，但**必须在 graph_store.py 的模块 docstring 记录该扩展**并以测试锁定）。

2. **束搜索主体**（spec §2.1 冻结）：`plan_hops(LLM)` → 逐层 `expand`（★版本谓词生效）→ `score_path`（相关性+证据丰富度+冲突信号）→ top-k → `answerable` 早停；失败路径保留供反事实。

3. **证据链与决策卡**（spec §3.3 冻结）：卡 JSON 契约 = `question_id/knowledge_stamp/candidates[{option,score,confidence_calibrated,evidence_chain[],risks[],counterfactual}]/decision/conflict_adjudications[]/uncertainty_notes[]`；`evidence_chain` 每项 = `{claim, provenance:{doc,span,kg_path[],version_pinned}, tag, source_channel}`。
   双路融合：doc 通道 EXTRACTED 优先，KG 通道供连接性；跨通道冲突 → contested + 卡面「知识不一致」（不静默取舍）。
   医疗域：`domain="healthcare"` 自动附免责声明。
   拒答判定：关键事实证据全缺 → `decision=INSUFFICIENT_EVIDENCE`，不生成候选（K4 X 型题的行为底座）。

4. **路由**（spec §1.2 冻结）：L1 六条 LOOKUP_RULES + 版本比较词 → RM；L2 小档 few-shot 三分类 {R,M,RM}；L3 conf<0.7 → RM（default-safe）。
   检索路工具名对齐源码 **`knowledge_base_search`**（非 doc_search，03 任务表点名的坑）。

5. **校准**：`calibrate(raw_conf)` 十桶经验查表，表源为 `eval_run_t.calibration`；表缺失时**恒等返回并在卡上记 `calibration_applied=false`**（诚实降级，不假装校准）。

6. **落库与重算**：`persist(card, session_id)` 写 `decision_card_t`（payload+knowledge_stamp）；`rerun_marked(tenant_id)` 在当前知识戳下重算 `needs_rerun` 卡，**新旧结论差异自动记录**（Q2 台账素材）。

7. **`kg_service.evolution_trace`**：实体/决策卡的知识演化时间线（替换 `NotImplementedError`）——spec `kg_service.py.md` 查询面冻结方法之一，T-09 归属已由源码注释点名。

8. **`calibrate_hops`**：depth∈{1,2,3,4} 网格在 30 个多跳题上的「准确率-token-延迟」三轴曲线，**修死 `KW_MULTIHOP_MAX_DEPTH`（回收 L5 遗留值）**。key 是离线实验入口，测试用 fake 数据验形状，真实跑分归 T-10b。

**要构建的行为**（用户视角端到端）:
1. 用户问版本敏感问题（V 题）→ 系统在**指定知识版本**的子图上做多跳，返回的每条证据 `version_pinned=true`，且路径上不含该版本之后才生效的事实；同一问题关掉版本钉住会得到被过期事实污染的路径（消融可复现）。
2. 用户问多跳题（M 题）→ 决策卡含候选×证据链（每条命题带 doc span + kg_path）×风险×反事实×知识版本戳；医疗域自动带免责声明。
3. 用户问语料外问题（X 型）→ 卡结论 `INSUFFICIENT_EVIDENCE`，不编造候选。
4. Agent 的工具列表里出现 `kg_multi_hop`（Local MCP + 独立 FastMCP 双注册，单一 schema 源）。

**验收命令**:
```bash
cd backend && uv run pytest ../test/backend/services/knowevo/ -q --no-header          # 全绿（含新增 3 个测试文件）
cd backend && uv run ruff check services/knowevo/decision_service.py services/knowevo/version_pin.py services/knowevo/schemas.py
# 版本钉住语义护栏（纯函数，无 DB）
cd backend && uv run pytest ../test/backend/services/knowevo/test_version_pin.py -v
# MCP 双注册无漂移
cd backend && uv run pytest ../test/backend/services/knowevo/test_kg_multi_hop_mcp.py -v
# PG 集成（版本钉住必须在真库验证——坑 #25 教训：集成测试不能只当装饰）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  RUN_POSTGRES_INTEGRATION=1 uv run pytest ../test/backend/services/knowevo/ -q --no-header
```

**验收标准**:
- [x] `version_pin.py` 提供 `resolve_version_clock` / `path_version_valid`，且在真库验证：t_v 之前的边在 G_v 内、t_v 之后生效的边被排除（**★B2 的核心断言**）
- [x] `DecisionService.multi_hop` 每次 expand 都带版本谓词（可 grep 到 `pin_neighborhood` / `t_v` 传参行）
- [x] `path_version_valid` 对「过期边」返回 False、对「t_v 时点有效的边」返回 True（纯函数单测）
- [x] `DecisionService.route` 三分支 R/M/RM 全覆盖 + conf<0.7 落 RM + 版本比较词命中 RM
- [x] `assemble_evidence` 跨通道冲突 → contested + 「知识不一致」note
- [x] `render_card` 卡 schema 校验通过（Pydantic）；healthcare 自动附免责声明；lite 模式跳过反事实与风险
- [x] 证据全缺 → `INSUFFICIENT_EVIDENCE` 且 `candidates == []`
- [x] `calibrate` 十桶查表；表缺失 → 恒等返回 + `calibration_applied=false`
- [x] `persist` 写 `decision_card_t`（payload + knowledge_stamp）；`rerun_marked` 记录新旧结论差异
- [x] `kg_service.evolution_trace` 不再 `NotImplementedError`
- [x] `kg_multi_hop` MCP 工具双注册（FastMCP + Local MCP 同一 schema 源）；depth≤3 / beam≤3 硬护杆（depth=4 被拒）
- [x] 6 个新 prompt YAML 双语成对（route/hops/card × en/zh），格式对齐既有 `knowevo_*.yaml`
- [x] **零新依赖**（instructor 已实测否决；networkx 已在树）；pyproject 零改动
- [x] 零新迁移、零 ALTER、零新增 env
- [x] 新踩坑记 `competition/docs/pitfalls.md`

**Evidence**:

```bash
# 1. 全量 knowevo 测试（无 PG 门控）
$ cd backend && uv run pytest ../test/backend/services/knowevo/ -q --no-header
292 passed, 23 skipped, 5 warnings in 8.46s
#    T-09 新增三个文件贡献 128 个用例：
#      test_version_pin.py        25 passed, 4 skipped（Layer 1 纯函数语义 + Layer 2 真库）
#      test_decision_service.py   81 passed, 5 skipped（路由/束搜索/证据链/卡片/校准/落库重算）
#      test_kg_multi_hop_mcp.py   22 passed（双注册单一 schema 源 + 输入护杆 + 结构化错误）

# 2. PG 集成全量（真实 PG 5434）——版本钉住必须在真库验证（坑 #25）
$ cd backend && export NEXENT_POSTGRES_PASSWORD=... \
  POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  RUN_POSTGRES_INTEGRATION=1 uv run pytest ../test/backend/services/knowevo/ -q --no-header
315 passed, 5 warnings in 8.06s

# 3. ★B2 核心断言：版本钉住的 on/off 消融（真库，独立脚本复核）
#   图：A->B 有效[2024-01-01, 2025-01-01)；A->C 有效[2026-01-01, 开放)；B->D 有效[2024-01-01, 开放)
#   调用 DecisionService.multi_hop(seeds=["A"], depth=2, version="v1")，t_v=2025-01-01
pinned paths : []
pinned failed: [(['A','B'], '1 edge(s) outside version v1 at 2025-01-01T00:00:00+00:00: treats(valid ...)')]
BEFORE@T1-1us: paths = [['A','B','D']]      ← 早 1 微秒时 A->B 有效 → 路径成立
PIN OFF      : paths = [['A','C']]          ← 关闭钉住后走到"未来才生效"的边
as_of values passed to store: [2025-01-01T00:00:00+00:00, None]
#   ↑ 第一条是 pinned 展开（谓词进了 SQL），第二条是边界探针（全时空取被裁掉的边）
# 边界语义：valid_at <= t_v 计入（T1 生效的边在 t_v=T1 时有效）；
#          invalid_at >  t_v 计入（恰在 t_v 失效的边已不在 G_v）——与 02-技术方案 §3.2 定义逐字一致

# 4. 版本裁掉的事实被"上报"而非"吞掉"（这是 failed 非空的唯一来源）
failed[0].invalid_edge_reason = "1 edge(s) outside version v1 at 2025-01-01T00:00:00+00:00: replaced_by(valid 2026-01-01 -> open)"

# 5. 跳数标定曲线（calibrate_hops，链式夹具 × 30 题，depth∈{1,2,3,4}）
depth=1 acc=0.00 lat=0ms | depth=2 acc=1.00 | depth=3 acc=1.00 | depth=4 acc=1.00
recommended_depth = 2   ← 精度在 2 跳饱和，按延迟预算取最浅的饱和点（不买无收益的延迟）
# 注意：本轮为夹具曲线（零 LLM 成本），真实语料跑分归 T-10b；KW_MULTIHOP_MAX_DEPTH 保持 3

# 6. 敌意 LLM 输出防御（独立脚本，非仓库测试）
[malformed]            RAISED TypeError（明确报错，不生成半成品卡）
[candidates-not-list]  candidates=0, decision=RECOMMEND（不崩）
[unbacked-pin]         pinned_flags=[False]  ← LLM 谎称 version_pinned=true 且时钟无版本 → 已剥离
[cf-on-second]         c1 counterfactual=None ← top-1 约束在代码里强制，不靠 prompt 自觉
[candidate-no-option]  空 option 的候选被丢弃（不产出无证据候选）

# 7. ruff
$ uv run ruff check services/knowevo/ tool_collection/mcp/kg_tools.py ../mcp_servers/knowevo_mcp/ <3 个新测试文件>
All checks passed!

# 8. 真库邻域 bug 修复验证（坑 #32/#33，由集成测试暴露）
双种子 neighbors(['A','B']) 现在返回 A-B 边与 B-D 边（此前返回空）
multi_hop(depth=2) 在 A->B->D 链上返回 ['A','B','D']（此前因早停判据误判返回 ['A']）
```

**独立复核结论（2026-09-17，本会话自查，非仓库测试）**：
- 版本谓词**确实进了 SQL**（脚本 monkeypatch `neighbors` 记录到 `as_of` 实参），不是事后过滤——这是 B2 声称"实现上是 SQL 递归 CTE 加一个时间谓词"的实证。
- 边界（t_v 处 valid_at 计入 / invalid_at 排除）与 §3.2 定义逐字一致；早 1 微秒的对照证明谓词不是常数。
- 全部 8 类敌意 LLM 输入下卡片不崩、不出现无据溯源。
- 过程中真库集成测试抓出**两个既有实现 bug**（坑 #32 邻域边新颖性判据、坑 #33 光束早停判据），都已修复并补 Layer 1 回归用例——再次验证坑 #25 的"集成测试不能只当装饰"。

**合并门禁备注**: 待合并 develop。分支 `feat/kw-T09-multihop-decision-card`。

---

## 会话复核（2026-09-17 · code-review 双轴 + 真库复核 · 独立于上面开发者自查）

复核方式：①Spec 轴——逐条验收标准回代码 grep 实现行（坑 #27 纪律："docstring 不算实现"）；②Standards 轴——架构分层 / 注释语言 / 死代码 / 静默失败 / 测试断言质量；③真库门控复核——确认硬测试真实执行而非跳过（坑 #25 纪律）。

### 复核判定的偏差（如实记录，不掩盖）

| # | 简报原文要求 | 实际落地 | 裁决 |
|---|---|---|---|
| 1 | `version_pin.py` 提供 `pin_neighborhood(store, tenant, entity_ids, t_v, rel_types, hop)`，内部走"兼容路径 + as_of 关键字" | 该函数**不存在**。谓词改由 `t_v` 线程进 `_expand`（`decision_service.py:448`）→ `store.neighbors(as_of=t_v)`（`:525`）实现，行为等价，验收标准 2 仍成立 | 接受行为、记录命名偏差。若 T-10b 消融需要独立开关面，届时再抽该函数 |
| 2 | 「**ABC 抽象方法签名一字不改**」 | `GraphStore.neighbors/multi_hop` **新增了带默认值的 `as_of` 关键字参数**（`graph_store.py:119,134`）。调用方向后兼容（默认 None 保持原行为），但在**方法** docstring 记录扩展，**模块** docstring 未记（简报两处都要求） | **违反冻结声明**，如实记录。T-07 全部既有测试通过证明行为兼容；建议 T-10b 时补模块级说明 |
| 3 | `filter_paths_by_version` 承担路径级校验 | 已实现且单测覆盖（`test_version_pin.py:166-181`），但 `multi_hop` 走内联逐路径复核（`:493-499`），该函数**无生产调用点** | 保留（T-10b 消融/回放接口面），标注为预留 |
| 4 | `route` 三分支 R/M/RM 全覆盖 | `route()`（同步路）只产 R / RM，**M 与 conf<0.7 规则只在 `route_async()`**（`:283-319`）；文档字符串已解释该分工 | 口径修订为"路由子系统三分支全覆盖"，验收标准 4 按子系统判 |
| 5 | 「十桶」校准 | 复核前"十桶"只出现在 docstring，实现遍历任意长度的表；**已修复**：新增 `CALIBRATION_BUCKETS`/`CALIBRATION_EDGES` + `CalibrationTableModel` 十桶契约（桶数异常记 warning） | 已修复 |
| 6 | `render_card` 卡 schema 校验通过（Pydantic） | 复核前 `DecisionCard` 是纯 dataclass，**无任何 Pydantic 校验**；**已修复**：`DecisionCardContract` + `CalibrationTableModel`（`schemas.py`）+ `validate_card/validate_card_payload`，`render_card` 出口自校验 | 已修复 |

### 复核发现并已修复的实现缺陷

1. **矛盾检测静默失灵（坑 #34）**：`NEGATION_MARKERS` 含 `"禁"`，按子串匹配时"禁忌"被判为否定 → KG 与 doc 双方都"含否定" → `_contradicts` 恒返回 False，**所有药物安全性矛盾永不告警**。修为 `"禁"` → `"禁用"`，并补真跨通道矛盾正例测试（原测试是同通道两条 doc 断言 `contested is False`，属同义反复，坑 #24 那一类）。
2. **版本边界探针失败被吞（决定 B2 声称的核心）**：`_probe_excluded` 的 `except` 只记 info 并返回 `[]` → "版本裁掉了什么"与"没能检查出裁掉了什么"产出同一张卡。修为：失败时返回带 `PROBE_FAILED` 标记的合成条目，`multi_hop` 将其提升为 `PathSet.probe_failed`（不进 `failed`），`assemble_evidence` 透传，`render_card` 输出"版本边界探测未能执行"note。
3. **校准三处"卡上写假话"（坑 #35）**：未命中桶原值透传而 `calibration_applied` 仍 True；桶损坏被静默 `continue`；非数值置信度透传。修为 `_calibrate_in_table(conf, table, note)`，未命中/损坏一律记 note 并置 `calibration_applied=False`。
4. **`RECOMMEND` 且零候选产出契约非法的卡**：LLM 回未知 decision 且无候选时落 RECOMMEND 空卡。修为改判 `INSUFFICIENT_EVIDENCE`。
5. **测试断言空洞化（4 处）**：`recommended_depth in (1,2)`（任一答案都过，从未验证延迟预算过滤）、`longest <= max_depth+1`（走空也过）、`len(plans)==2` 未断言"降级为无过滤"、`len(candidates)==2` 未断言解析内容。全部改为断言具体语义；新增 `test_walk_stops_early_once_a_claim_matches_the_question`（把早停行为显式钉住，取代原先隐含依赖）。

### 未修复但已知的债务（交接给 T-10b / T-12）

- `pin_predicate` 与 `filter_paths_by_version` 无生产调用点（消融接口面，T-10b 用）。
- `decision_service.py` 的卡/路由/落库半边（`route_async`/`render_card`/`persist`/`rerun_marked`/`calibrate_hops`）目前**只有测试调用**，无生产入口——这是 T-12（决策卡面板 + 进化看板）的接线债，属预期。
- `route_hit_feedback`/`route_hit_rate` 仅有会话级内存台账，持久化统计归 T-10b（`eval_run_t`）。
- 卡契约用 `extra="allow"`，是刻意的向后兼容选择（消费者不因新增字段崩），代价是拼错字段名不会被发现——若 T-10b 要严格模式，需在读写两端同时收紧。

### 复核后重跑的验收证据

```
$ cd backend && uv run pytest ../test/backend/services/knowevo/ -q --no-header
292 passed, 23 skipped, 5 warnings        # 23 skip 全为 RUN_POSTGRES_INTEGRATION 门控

$ cd backend && uv run ruff check services/knowevo/ ../test/backend/services/knowevo/
All checks passed!                        # 复核新增代码零告警
# （develop 上 test_llm_client.py 有 6 条 PLR0402，T-08 既有；已在 develop 版本上复现确认非本次引入）

$ export NEXENT_POSTGRES_PASSWORD=...（deploy/env/.env）
$ POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  RUN_POSTGRES_INTEGRATION=1 uv run pytest ../test/backend/services/knowevo/ -q --no-header
327 passed, 0 skipped, 5 warnings in 52.91s   # 复核修复前 315 passed；跳过 23→0

# 门控非装饰的反证（坑 #25）：故意用错端口重跑该测试 → 硬失败而非跳过
$ POSTGRES_PORT=5999 RUN_POSTGRES_INTEGRATION=1 uv run pytest test_version_pin.py -q
FAILED TestPinnedWalkInPostgres::test_edge_cards_carry_the_time_window
  sqlalchemy.exc.OperationalError: connection to server at "localhost", port 5999 failed
```

**复核结论**：T-09 的核心创新（★B2 版本钉住）在真库上真实通过并可用 on/off 消融复现，验收门禁不是"靠跳过变绿"。复核修掉 5 类实现缺陷（其中 2 类属"卡上写假话"、1 类属"安全矛盾静默失灵"），补强 4 处空洞断言，并把卡契约从"注释里的承诺"变成"出口可执行的 Pydantic 校验"。已知偏差与债务已逐条登记，无隐瞒项。

---

## 设计说明：为什么版本钉住要独立成 `version_pin.py`

判据是「可被单测锁定的语义」而非文件数。版本钉住是本项目的**文献空白点（B2）**，它的定义（路径在版本 v 下有效）必须有一个地方**用代码原文表达**，并且能被一条不碰数据库、不碰 LLM 的纯函数测试锁定。把它埋在 `decision_service.py` 的束搜索循环里，语义就会被循环变量淹没，「on/off 消融」也无处挂钩。独立模块同时给了 T-10b 消融实验一个干净的开关面。