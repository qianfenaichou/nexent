# T-06：抽取流水线 v0（本体锚定抽取 + 三级对齐 + bi-temporal 合并 + 待审池）
**Blocked by**: T-02（58 份语料 + doc_asset_t 登记，已合并 develop）、T-03（12 表 ORM + 迁移 + const.py 接线，已合并）
**拆分裁决**（03 计划 §3.4：新文件 ≤8、新码 ≤1200 行）：本任务为**单会话不可拆**的垂直切片（tracer bullet：spans→抽取→对齐→图谱表→待审池），不做 a/b 拆分；若单会话超预算，按"先 L0 主键对齐 + 合并主干，再补 calibrate/split 分支"顺序降级交付，并在回执注明未完成项。

**独占文件**:
- `backend/services/knowevo/schemas.py`（T-06a 已建草稿：EvidenceSpan/Entity/Relation/ExtractionResult/AlignDecision/IngestReport + cosine/normalize_name_key；本任务**补齐** ParsedTable/LabeledPair/Thresholds/Example/PendingSummary）
- `backend/services/knowevo/kg_service.py`（**新建**，接口冻结于框架包同名 .md）
- `backend/services/knowevo/pipeline/ingest_graph.py`（**新建**，薄 CLI）
- `backend/prompts/knowevo_extract_en.yaml` + `backend/prompts/knowevo_extract_zh.yaml`（**新建**，双语成对）
- `deploy/sql/migrations/v2.5.5_kw_002_kg_extract_run.sql`（**新增迁移**，只增不改）
- `test/backend/services/knowevo/test_kg_service.py`（**新建**）
- **文件归属转移声明**（沿用 T-05 对 ontology_service.py 的先例）：`backend/database/knowevo_db.py` 由 T-03 持有 → 本任务**追加** `KgExtractRun` 模型类与注册表条目（**只追加，不改既有 12 表定义**）；迁移 001 文件绝对禁改。

**待接线项**（登记给 T-08，本任务不碰）:
- `KW_ALIGN_TAU1/TAU2` 读取：const.py 已有此二常量（T-03 已接线），本任务**直接 import 使用**，不新增环境变量；`calibrate_thresholds` 的产物回写 env 动作归 T-08。
- embedding 通道（BGE-M3 或上游 embedding 模型）与 LLM struct 输出（instructor）的真实链路接线归 T-08；本任务 LLM 以注入 callable 形式存在，测试用 fake。

**禁改清单**: 上游共享文件（apps/app_factory.py、config_app.py、runtime_app.py、consts/const.py、pyproject.toml）；T-04/T-05 已冻结接口（ontology_service 既有方法签名）；T-03 已冻结表结构（不改列、不 ALTER）；`deploy/sql/migrations/v2.5.5_kw_001_knowevo_core.sql`。

**允许的新依赖**: **无**（纯标准库 + pyyaml[已在依赖树]；不引入 networkx/instructor/fastmcp——图算法与结构化输出各归 T-07/T-09）。

**要构建的行为**（用户视角端到端）:
给一批已登记的语料（doc_asset_t 行 + 文档文本块）跑离线抽取：每个文本块经"本体子图检索 → LLM 锚定抽取"（或表格走确定性解析，零 LLM）产出实体/关系候选，实体必须挂到本体 active 类（挂不上→待审池），关系带 claim 与证据；随后实体过**三级对齐**（L0 外部主键/别名表 → L1 向量 → L2 LLM 裁决 → L3 人审池），按 bi-temporal 规则合并进图谱表（NEW 插入 / ALIAS 挂别名 / 矛盾按时效+权威度 supersede / 同源矛盾 contested 不静默覆盖）；全程写 `kg_evidence_t` 溯源行，重复跑同一批文档不重复抽取（span hash 幂等）。动物：`python -m services.knowevo.pipeline.ingest_graph --batch batch.json --tables-only` 能在无 LLM 情况下跑通并输出 IngestReport。

**本任务兑现的两个 P0**（02 技术方案 §7）:
- **P0-1 实体对齐漏洞修正**（正确性）：`align` 第 0 级外部标识主键 blocking——ATC 编码 / 国家医保码 / NMPA 批准文号本位码 / 药品别名表。**"二甲双胍=格华止"必须靠外部标识命中合并，不靠 embedding 相似度**（相似度路线在 0.85 阈值下根本合不上，降阈值又会误合阿卡波糖）。三档阈值 τ1/τ2 从 const 读默认 0.80/0.60，并给 `calibrate_thresholds` 提供 ROC 标定入口（L3 开放问题：200 对人工标注 → 假合并率 ≤2% 定 τ1、召回 ≥95% 定 τ2）。
- **P0-4 本体子图检索替代 15k 全量注入**（规模炸弹）：新增纯函数 `retrieve_ontology_subgraph(chunk_text, classes, top_k=15)`——按类名/别名/属性词面命中打分，取 top-k 类 + 其父类链，产出的紧凑摘要注入抽取 prompt（通常 <3k token）。不做 embedding 检索（embedding 通道归 T-08），但**接口形态即最终形态**，T-08 只把打分函数换成向量召回。

**接口（框架包 kg_service.py.md 冻结，本任务实现以下子集）**:
- `extract(span, ontology_summary) -> ExtractionResult`：LLM 通道，EXTRACTED/INFERRED 标注，class_ref 必须命中 active 类，否则进 pending
- `extract_table(table) -> ExtractionResult`：确定性通道（列头→属性、行→实体，零 LLM、可复现）
- `fewshot_for(span) -> list[Example]`：3 静态 + 2 动态（从已确认抽取证据池检索）
- `align(entity) -> AlignDecision`：L0 主键/别名表 → L1 余弦 → L2 LLM 裁决 → L3 人审；merge 记 alias_type
- `calibrate_thresholds(labeled_pairs) -> Thresholds`：ROC 标定 τ1/τ2
- `merge_delta(extractions) -> IngestReport`：NEW 插入 / ALIAS 合并 / CONTRA supersede / CONTENDED 标 contested；**never silently overwrite**
- `split_entity(entity_id, criteria) -> (id_a, id_b)`：错误合并修复，原实体 DEPRECATE + split_into + 受影响边重锚
- `update_pending_pool() -> PendingSummary`、`pending_to_proposals(min_mentions=3)`：高频未映射实体回流本体提案（复用 T-04 `propose_from_pending`）
- `search(query, hop=1, top_k=5, ontology_version=None)`：薄查询面（v0 词面召回 + 1 跳邻居；T-07/T-09 扩展为 MCP 工具）
- **不在本任务**：`ingest_new_version`（T-11）、`evolution_trace`（T-09）、GraphStore 抽象（T-07）——留显式 NotImplemented 桩，不发明实现。

**验收命令**:
```bash
cd backend && uv run pytest ../test/backend/services/knowevo/test_kg_service.py -v
# 确定性通道冒烟（无 LLM、无 DB 也可跑的表解析路径）：
cd backend && uv run python -m services.knowevo.pipeline.ingest_graph --batch /tmp/kw_batch.json --tables-only --dry-run
# 迁移自检（有 PG 时）：
psql -f deploy/sql/migrations/v2.5.5_kw_002_kg_extract_run.sql
```

**验收标准**:
- [x] `extract` 锚定失败实体进 pending 池（suggested_class 保留原始类名），锚定成功实体 class_ref 必为 active 类 stable_id
- [x] `extract_table` 与 LLM 通道输出**同一 schema**（同一 dataclass 校验通过），且零 LLM 调用
- [x] `align` 四分支各自有测试：L0 外部主键命中直接合并（alias_type 记录）；L1 sim>τ1 合并、(τ2,τ1] 进 L2、≤τ2 新建；L2 置信 ≥0.9 执行、<0.9 落 L3；L3 落待审池
- [x] **"格华止"（brand）经别名表命中"二甲双胍"（generic）合并** —— P0-1 回归断言，不得依赖相似度
- [x] `calibrate_thresholds` 对合成标注对给出 τ1（假合并率 ≤2%）与 τ2（召回 ≥95%），ROC 单调性断言
- [x] `merge_delta` 四类冲突规则各一测：新增/属性追加/矛盾（时效晚者现行 + 旧边 invalid_at 打戳）/同源矛盾 contested=true + 人审
- [x] `split_entity` 往返：两个新实体 + 原实体 status=split + split_into 双边 + 受影响边已重锚
- [x] 待审池：`update_pending_pool` 汇总计数；`pending_to_proposals(min_mentions=3)` 只对高频实体产出提案并把状态置 proposed
- [x] 幂等：同一 span hash 二次跑不重复写证据/实体（`kg_extract_run_t` 去重）
- [x] ruff 全过；注释/docstring 英文；prompt 模板双语成对；租户隔离（FakeStore 测试覆盖跨租户不可见）
- [x] 新踩坑记 `competition/docs/pitfalls.md`；迁移新增文件编号 002 且 001 未被改动

**Evidence**: 
```
$ cd backend && uv run pytest ../test/backend/services/knowevo/test_kg_service.py -v
60 passed, 3 skipped (3 = PG 集成，RUN_POSTGRES_INTEGRATION 门控，本会话无 PG 环境跳过)

$ cd backend && uv run pytest ../test/backend/services/knowevo/ -q        # 全量回归
110 passed, 7 skipped (零既有测试破坏)

$ cd backend && uv run ruff check services/knowevo/ ../test/backend/services/knowevo/
All checks passed!

$ cd backend && uv run python -m services.knowevo.pipeline.ingest_graph \
    --batch /tmp/kw_batch.json --tables-only --dry-run
{"run_id": "...", "tenant_id": "...", "mode": "dry-run", "docs": 1, "chunks_planned": 2}

2026-09-16 修复节点：初始 12 failed/48 passed → 5 处实现 bug 修正（extract 漏 await、
_merge_edge 权威方向反、pending 未处理、ext_id/status 丢失、L0 阻断过强）+ τ1 语义与
Mann-Whitney AUC 修正 → 60 passed/3 skipped。坑见 pitfalls #22/#23。
```

## 反幻觉条款（发任务时必附）
开工先读仓库根 AGENTS.md；框架包 `knowevo/backend/services/knowevo/kg_service.py.md` 是接口契约，`02-技术方案.md` §2.3/§2.4 是算法契约；T-03 表结构只可读不可改（新增表必须走新迁移文件）；T-04/T-05 的 ontology_service 方法签名禁改（只允许调用）；不得发明环境变量（τ 值走已有 KW_ALIGN_TAU1/TAU2）；不得引入简报外依赖；`ingest_new_version`/`evolution_trace`/GraphStore 属其他任务，只留桩不实现。