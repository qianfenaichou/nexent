# T-18b：D1 事实业务时间 —— valid_at 显式写入 + 版本事实截止 t_v

**状态**: ★ 待开发（2026-09-18 调度会话）
**Blocked by**: T-18a（同批迁移文件序号；`list_versions` 补 `created_at` 是 t_v 回退路径前置）
**独占文件**（本任务创建/修改）:
- `competition/corpus/registry.csv`（**加列** `published_at`：业务出版日，来源可溯）
- `backend/services/knowevo/ingest_service.py`（`REGISTRY_COLUMNS` + `RegistryRow.published_at` + 落 `doc_asset_t.meta_data`）
- `backend/services/knowevo/kg_service.py`（`_merge_edge` 显式传 `valid_at`；`doc_published_at` 读业务时间；`_edge_provenance` 同步）
- `backend/services/knowevo/graph_store.py`（`insert_relation`/`upsert_relations` 支持显式 `valid_at`；`PgStore.doc_published_at` 读 meta_data）
- `backend/services/knowevo/version_pin.py`（`resolve_version_clock` 优先 `fact_cutoff`，回退 `created_at`）
- `backend/services/knowevo/ontology_service.py`（`commit_version`/`save_version` 写入 `fact_cutoff`）
- `backend/services/knowevo/decision_service.py`（`_resolve_clock` 读 `fact_cutoff`）
- `deploy/sql/migrations/v2.5.5_kw_005_fact_time.sql`（**新建**：一次性数据修复说明 + 可选索引）
- `backend/services/knowevo/pipeline/repair_fact_time.py`（**新建**：一次性回填脚本，幂等）
- `test/backend/services/knowevo/test_version_pin.py` / `test_kg_service.py` / `test_decision_service.py`（追加/更新）
- `competition/docs/pitfalls.md` / `evolution-log.md`（补记）

**待接线项**: 无
**禁改清单**: `backend/consts/const.py`、`apps/app_factory.py`、`deploy/sql/migrations/v2.5.5_kw_001~004*.sql`（已存在不可改）、`eval_run_t` schema、`pipeline/eval_e1.py`（T-18c 独占）、`e1_retrieval.py`（T-18d 独占）
**允许的新依赖**: **无**

**要构建的行为**（用户视角端到端）:
1. 每份语料带**业务出版日**（registry.csv `published_at`，从 `license_note` 的期刊引用可溯），摄取后落 `doc_asset_t.meta_data.published_at`；
2. 抽取入图的每条关系，`valid_at` = 其来源文档的业务出版日（**不再是入库墙钟时间**）；
3. 本体版本提交时记录 `fact_cutoff`（该版本覆盖的事实业务时间上界），`resolve_version_clock` 优先用它当 t_v；
4. **结果**：同一问题在 t_v=旧版 vs t_v=新版 下，版本钉住谓词**真的**裁掉/纳入不同事实——A4 消融 Δ 可测（D1 修复的可观测判据）；
5. 一次性回填：现存 294 条关系的 `valid_at` 从墙钟改为其来源文档业务日；v1.0.0 补 `fact_cutoff`。

**背景（现状核验，2026-09-18，真库）**:
- `kg_entity_t.valid_at`（knowevo_db.py:122）、`kg_relation_t.valid_at`（:147）均 `server_default=text("now()")`；生产代码**无一处显式写入** `valid_at`（仅测试夹具写）。
- `version_pin.resolve_version_clock`（version_pin.py:89-116）解析顺序：显式 `as_of` → `versions[i].created_at`（`source="version_created_at"`）→ `now()`。`OntologyVersion.created_at` 本身也是 `now()` 默认 → **t_v 与事实 valid_at 同为墙钟，`valid_at <= t_v` 恒真**。
- 真库实测：`ontology_version_t` 仅 1 行 v1.0.0，`created_at=2026-09-18 01:58:56.32`；`kg_relation_t` 294 行中 149 行 `valid_at` 落在 2026-09-17/18（入库墙钟），145 行是历史合成日期（2024-01-01×73 / 2026-01-01×72，来源为 PoC 合成图，非真实业务时间）。
- `decision_service._resolve_clock`（:634-659）从 `OntologyVersion` 读 `{version, created_at}` 喂给 `resolve_version_clock`。
- `kg_service.doc_published_at`（:557）用 `doc_asset_t.created_at`（**也是墙钟**）——冲突消解的"时效性优先"同样失真。
- `graph_store.upsert_relations`（:238）/`kg_service.insert_relation`（:460）均不透传 `valid_at`。

**验收命令**:
```bash
# 1. 单测（version_pin 判别性 + kg_service 显式 valid_at + 回归）
cd backend && uv run pytest ../test/backend/services/knowevo/test_version_pin.py ../test/backend/services/knowevo/test_kg_service.py ../test/backend/services/knowevo/test_decision_service.py ../test/backend/services/knowevo/test_ingest_service.py -q --no-header
# 2. ruff
cd backend && uv run ruff check services/knowevo/
# 3. PG 集成全量（无回归）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<见 deploy/env/.env> RUN_POSTGRES_INTEGRATION=1 \
  uv run pytest ../test/backend/services/knowevo/ -q --no-header
# 4. 回填脚本（幂等，先 --dry-run）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> uv run python -m services.knowevo.pipeline.repair_fact_time --dry-run
# 5. ★判别性真库验证（D1 修复的可观测判据）
#    同一问题在 t_v=2022-01-01 vs t_v=2025-06-01 下，pinned 谓词纳入的事实数必须不同
docker exec -e PGPASSWORD=<pw> nexent-postgresql psql -U root -d nexent -c "
select 't_v=2022' as clock, count(*) from nexent.kg_relation_t
  where valid_at <= timestamptz '2022-01-01' and (invalid_at is null or invalid_at > timestamptz '2022-01-01')
union all
select 't_v=2025', count(*) from nexent.kg_relation_t
  where valid_at <= timestamptz '2025-06-01' and (invalid_at is null or invalid_at > timestamptz '2025-06-01')
union all select 'all', count(*) from nexent.kg_relation_t;"
```

**验收标准**:
- [ ] `registry.csv` 新增 `published_at` 列，**每行有可溯来源**（来自 `license_note` 期刊年卷期或官方发布日期；不得凭空发明——反幻觉条款）；`parse_registry` 校验 ISO 日期格式，非法值记 error 不静默
- [ ] 摄取后 `doc_asset_t.meta_data.published_at` 存在（零 ALTER：走既有 JSONB）
- [ ] `_merge_edge` 入图时 `valid_at` = 来源文档业务日；无来源文档（未知 evidence）回退 `now()` 且**在 report 里计数**（诚实口径）
- [ ] `resolve_version_clock` 解析顺序变为：显式 `as_of` → `fact_cutoff`（source=`"fact_cutoff"`）→ `created_at`（`"version_created_at"`）→ `now()`；`VersionClock.source` 如实标注
- [ ] `commit_version` 写入 `fact_cutoff`（该轮入库文档 `published_at` 的最大值；无文档则 `now()`）
- [ ] **判别性单测**：同一组边，`t_v` 取两个不同业务时间，`edge_in_version` 结果不同（不是恒真）——这是 D1 的回归护栏
- [ ] `repair_fact_time.py` 幂等：回填后重跑不变；`--dry-run` 只报告不写
- [ ] 回填说明写入简报 Evidence + `pitfalls.md`（记录"墙钟时间污染双时轴"这一坑）
- [ ] 既有测试同步更新（改数据语义 → 受影响测试必须改，不得删测试）；PG 集成全量绿
- [ ] 零新依赖、零新增 env、既有迁移文件未改

**Evidence**: <完成后粘贴 pytest 输出 / dry-run 报告 / ★判别性 SQL 结果（三个 count 必须不同）>

---

## 实现备注（给实现 agent）

1. **判别性是本任务的唯一成功判据**：若回填后 `t_v=2022` 与 `t_v=2025` 纳入的事实数仍相同，D1 **没修好**，停下报告。先跑第 5 条验收命令看基线（当前应两值相同或近乎相同），修完再看差值。
2. **为什么不用 ALTER**：12 张域表 schema 冻结（03 §3.3）。`published_at` 落 `doc_asset_t.meta_data`（JSONB 已存在）、`fact_cutoff` 落 `ontology_version_t.metrics`（JSONB 已存在）——零 DDL，`kw_005` 迁移只放索引或注释说明（若无需索引可只留数据修复说明，仍建文件以留痕）。
3. **业务时间来源必须可溯**：`guide-2020` 的 `license_note` = "中华糖尿病杂志2021;13(4)" → `published_at` 取 `2021-04-01`；`guide-2024` = "中华糖尿病杂志2025;17(1)" → `2025-01-01`。**这是从已有出处推导，不是发明**。无出处者（如合成 PoC 图）标注 `null` 并回退。
4. **合成图数据（145 行 2024/2026-01-01）**：`gen_synthetic_graph.py` 造的 PoC 数据无真实文档来源 → 回填脚本应将其标为 `source="synthetic"` 或按 `meta_data` 无法映射时**保留原值并计数**，不得伪装成业务时间。
5. `_edge_provenance` 的 `(authority, published_at)` 元组用于冲突消解，改读业务时间后，冲突消解测试（T-06 遗留）可能受影响 → 一并核对。
