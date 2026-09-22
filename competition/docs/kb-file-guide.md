# 知识库文件说明（Knowledge Base File Guide）· Nexent-KnowEvo

> **定位**：官方初赛提交要求「json 文件、知识库和 MCP 等文件的说明」**成组独立提交**。本文件即其中「知识库」一册，与 `mcp-file-guide.md`（MCP 册）配对。
> 素材原散落在 `competition/docs/agent-config.md` §4（知识库信息）与 §6（关键文件说明）；此处独立成文并按「文件 → 职责 → 关键字段 → 生成者/消费者」展开，**不改母本**。
> **事实基准日**：2026-09-23。文中每个数字都在 §7 给出 `file:line` 出处，可逐条回溯；查不到出处的一律写「待确认」，不估算、不占位。
> **平台版本口径**：对外一律写 **v2.6.0**（`verification-reports/platform-facts-redline.md` §3 第 1 条）。仓库内 `deploy/sql/migrations/v2.5.5_kw_001 ~ kw_010` 是**历史迁移文件名，保留不改**（同上 §3.1），仅作仓内引用。
> **构建租户**：`6756b0ab-39c0-462a-9745-aa12e1511fcd`（`competition/deliverables/agent-config.json:7`，知识库链路的 tenant 基准）。

---

## 0. 一句话说清这条链路

**语料原件（`competition/corpus/<dir>/<file>`）→ 登记（`registry.csv`）→ 资产落库（`doc_asset_t`，带资产标识 + 权威级 + 版本血缘）→ 原生上传/解析/分块 → 知识库索引（基于 Elasticsearch 的向量/混合检索）→ 两条消费链：文档检索（`knowledge_base_search` / hybrid API）与图谱抽取（`kg_entity_t`/`kg_relation_t`/`kg_evidence_t`）。**

`registry.csv` 是**唯一人工可编辑的入口**，其后每一环都产出机器可读产物（json / 报告 / 数据库行），互不覆盖。

---

## 1. 文件总表（文件 → 职责 → 关键字段/内容 → 生成者 / 消费者）

### 1.1 语料与登记（`competition/corpus/`）

| 文件 | 职责 | 关键字段/内容 | 生成者 | 消费者 |
|---|---|---|---|---|
| `corpus/registry.csv` | **语料登记（知识库清单，唯一权威）** | 10 列：`asset_no`/`title`/`doc_type`/`modality`/`authority_level`/`source_url`/`license_note`/`local_file`/`split`/`published_at` | `corpus/build_registry.py`（人工逐条核源后生成）+ 人工维护 | `pipeline/ingest_assets.py`、`pipeline/derive_published_at.py`、`pipeline/repair_fact_time.py`、E1 检索评测 |
| `corpus/ingest_manifest.json` | 摄取运行清单（机器可读审计件） | `report.*`（run_id/registered/skipped/uploaded/indexed…）、`registered.ids`（`asset_no → doc_asset_t.id`）、`lineage_applied`、`uploads[]`、`run_at` | `pipeline/ingest_assets.py` | 人工审计、T-02 Evidence、血缘核对 |
| `corpus/blind_split.json` | 80/20 构建/盲区切分清单 | `policy`/`total`/`build_count`/`blind_count`/`build[]`/`blind[]` | `corpus/build_registry.py` | T-04/T-06（构建）、T-10/T-28（评测出题，**blind 不进任何构建流水线**） |
| `corpus/parse_report.md` | 解析体检报告 | 体检对象、chunk 总量、落库份数、低分清单、未入索引清单、**每份 `asset_no → chunks + 分数`** | `corpus/parse_checkup.py` | 人工结论、`doc_asset_t.parse_quality` 回写依据 |
| `corpus/ingest_batch.sh` | 实际批量上传脚本（upload→process，带会话刷新与串行节流） | `EMAIL`/`INDEX`/`BASE`、`chunking_strategy=basic`、`destination=minio` | 人工编写 | T-02 实际摄取 |
| `corpus/fetch_drug_labels.sh` / `corpus/fetch_third_batch.sh` | 语料抓取脚本（带已验证 URL） | URL 清单 | 人工编写 | 语料复现 |
| `corpus/build_registry.py` | 生成 `registry.csv` + 切分规则 | `GUIDES`/`ROWS` 等常量、sha256 切分规则 | 人工编写 | `registry.csv` |
| `corpus/parse_checkup.py` | 解析体检脚本（从索引拉全量 chunk 打分） | `INDEX`/`TENANT`/`NOISE_MARKERS` | 人工编写 | `parse_report.md` |
| `corpus/guideline_diff_seed.md` | 2020/2024 指南 diff 金标种子（T-21 唯一） | 变更条目清单 | 人工整理 | `pipeline/diff_guidelines.py`（T-11） |
| `corpus/e0-questions.md` / `e0_runner.py` / `e0_raw_round3.json` | E0 微基准题库 / 跑分脚本 / 原始数据 | 20 题（F/M/V/X 各 5）+ 原始响应 | 人工出题 + 脚本 | `docs/e0-baseline.md` |
| `corpus/testset-v1-seed.json` / `corpus/testset-discriminating-v1.json` | 评测题集（含 `testset_hash`） | `$schema`/`title`/`note`/`testset_hash`/`questions` | 人工 + 零 LLM 双时钟探针 | `pipeline/eval_v1.py`（`--testset`） |

### 1.2 平台侧 json（索引/上传/检索的请求体 —— 平台只认这几个字段）

| json / 请求体 | 职责 | 关键字段 | 代码出处 |
|---|---|---|---|
| `POST /api/indices/{index_name}` 建索引 | 知识库索引创建（**唯一的「索引 json 配置」**） | `embedding_model_id`（可选；不传则用平台默认向量模型） | `backend/services/knowevo/ingest_service.py:276-283`；平台路由 `backend/apps/vectordatabase_app.py:87` |
| `POST /api/file/upload` 上传 | 文件上传（multipart，非 json） | `file` / `destination` / `folder` | `ingest_service.py:285-292`；平台路由 `backend/apps/file_management_app.py:115` |
| `POST /api/file/process` 处理 | **解析 + 分块 + 入索引**（分块策略在这里） | `files[{path_or_url, filename}]` / `index_name` / `destination` / `chunking_strategy` | `ingest_service.py:294-302`；平台路由 `backend/apps/file_management_app.py:207` |
| `POST /api/indices/{index_name}/documents` | 只索引、不解析（自备 chunk 时用） | 文档数组 | `ingest_service.py:304-308`；平台路由 `vectordatabase_app.py:543` |
| `POST /api/indices/search/hybrid` 检索 | 知识库混合检索冒烟 | `index_names[]` / `query` / `top_k`（1-100）/ `weight_accurate` | `ingest_service.py:310-315`；平台模型 `backend/consts/model.py:945-961`；平台路由 `vectordatabase_app.py:1068` |
| `POST /api/indices/{index_name}/chunks` | 拉全量 chunk（解析体检用） | `max_chunks_count` | `corpus/parse_checkup.py:32-38`；平台路由 `vectordatabase_app.py:892` |
| `competition/deliverables/agent-config.json` 的 `knowledge_base` 块 | 知识库配置的机器可读版（随本册同步提交） | `corpus_count`/`corpus_registry`/`composition`/`graph.*` | `agent-config.json:52-64` |

### 1.3 服务层与库表（代码侧）

| 文件 | 职责 | 关键内容 | 出处 |
|---|---|---|---|
| `backend/services/knowevo/pipeline/ingest_assets.py` | 摄取 CLI（薄壳：参数/进度/exit code/落 manifest） | 三段式：登记 → 血缘 → 原生链；`--dry-run`/`--tenant`/`--index`/`--base-url`/`--ingest` | `pipeline/ingest_assets.py:13-21, 234-248` |
| `backend/services/knowevo/ingest_service.py` | 行为层：登记表校验、血缘配对、原生链客户端、解析评分、文件哈希 | `REGISTRY_COLUMNS`/`DOC_TYPES`/`MODALITIES`/`AUTHORITY_LEVELS`/`SPLITS` | `ingest_service.py:38-51` |
| `backend/services/knowevo/pipeline/derive_published_at.py` | 出版日**推导**（R1-R5 规则，绝不臆造） | R1 显式日期 / R2 期刊期号 / R3 公文号年份 / R4 asset_no 尾年 / R5 无 → NULL | `pipeline/derive_published_at.py:13-30`（规则正文）、`:50-56`（正则） |
| `backend/services/knowevo/pipeline/repair_fact_time.py` | fact-time 数据修复（三阶段，幂等） | docs→`doc_asset_t.metadata.published_at`；versions→`ontology_version_t.metrics.fact_cutoff`；relations→`kg_relation_t.valid_at` | `deploy/sql/migrations/v2.5.5_kw_005_fact_time.sql:17-43` |
| `backend/database/knowevo_db.py`（`DocAsset`） | `doc_asset_t` 表模型（DDL 真源在 kw_001） | 见 §4 字段表 | `backend/database/knowevo_db.py:196-222` |
| `deploy/sql/migrations/v2.5.5_kw_001_knowevo_core.sql` | 域表 DDL 真源 | `doc_asset_t` 建表 + `supersede_of` 自引用外键 | `...kw_001_knowevo_core.sql:124-140` |
| `deploy/sql/migrations/v2.5.5_kw_005_fact_time.sql` | **文档型迁移**（有意不含 DDL/DML，只记录语义变更与复现方式） | 修复语义说明 | `...kw_005_fact_time.sql:1-2`（无 DDL/DML 声明） |

---

## 2. `corpus/registry.csv` —— 语料登记表（知识库的唯一清单）

- **列契约**（`ingest_service.py:38-41`，逐列校验规则见 `:114-173`）：
  `asset_no` / `title` / `doc_type` / `modality` / `authority_level` / `source_url` / `license_note` / `local_file` / `split` / `published_at`。
- **枚举白名单**（写错即该行报错并留空槽，绝不静默丢弃）：
  - `doc_type` ∈ {`guideline`, `drug_label`, `lab_report`, `policy`, `material_list`, `edu_graphic`}（`ingest_service.py:45-48`）
  - `modality` ∈ {`text`, `table`, `image_text`}（`:49`）
  - `authority_level` ∈ {1, 2, 3, 4}（`:50`）
  - `split` ∈ {`build`, `blind`}（`:51`）
  - `published_at` 必须是严格 `YYYY-MM-DD`，否则记为显式错误并置 `None`（`:43, 168-171`）——**非法日期宁可报错，绝不放行**（版本钉住依赖它）。
- **实况（2026-09-23 现场统计 `registry.csv`）**：58 行数据；`doc_type` = drug_label 28 / guideline 11 / policy 9 / edu_graphic 8 / lab_report 2；`authority_level` = 1×18 / 2×11 / 3×28 / 4×1；`split` = build 40 / blind 18；`source_url` 与 `license_note` **0 行为空**；`published_at` 30 行有值 / 28 行为空。
- **口径提醒（人类标签 ↔ 字段 token 的映射）**：`agent-config.md` §4 写的「诊疗路径 9 / 检验 2 / 科普 8」在表里分别落在 `doc_type` 的 `policy`（本批 9 行全部是 `cp-*` 临床路径） / `lab_report` / `edu_graphic`。对外叙述用中文类别名，落到文件一律用 token。

---

## 3. `corpus/ingest_manifest.json` —— 摄取运行清单

- **生成时点**：`ingest_assets.py` 每次非 dry-run 运行结束写一次（`pipeline/ingest_assets.py:217-222`），路径固定为 `competition/corpus/ingest_manifest.json`（`ingest_assets.py:33`）。
- **顶层两段**：`report`（人读摘要）+ 运行明细（`registered`/`lineage_applied`/`uploads[]`/`run_at`）。
- **`registered.ids` 的用途**：`asset_no → doc_asset_t.id` 映射（`ingest_manifest.json:181-184` 可见 `guide-2020 → ec300a7f-…`），是「把 corpus 里的文件名对回数据库 UUID」的唯一现成对照表。
- **实况**：`ingest-20260915011057` 一次运行 —— `rows_total=58` / `rows_valid=58` / `registered=19` / `skipped=39` / `lineage_applied=2` / `uploaded=0` / `indexed=0`（`ingest_manifest.json:3-11`）；同一行也进了成本台账（`docs/cost-ledger.md:10`）。
- **诚实要点（必须一起说，否则会读错）**：这次 CLI 运行的 `uploaded=0 / indexed=0` 说明**上传链不是由 CLI 触发的**（`--ingest` 未走通/未启用，`uploads` 为空数组，`ingest_manifest.json:244`），实际上传由独立脚本 `corpus/ingest_batch.sh` 完成；因此「chunk 是否入索引」要看 §5 的解析体检报告，**不能**用本清单的 `indexed` 字段下结论。

---

## 4. `doc_asset_t` —— 资产标识、权威级与血缘字段

表模型 `backend/database/knowevo_db.py:196-222`；DDL 真源 `deploy/sql/migrations/v2.5.5_kw_001_knowevo_core.sql:124-140`（schema = `nexent`，`:33`）。

| 字段 | 类型 | 含义（字段含义 + 业务用途） | 出处 `knowevo_db.py` | 出处 `kw_001` |
|---|---|---|---|---|
| `id` | UUID PK | 行主键；即链路里的 `doc_id`（证据/图谱指向它） | :204 | :125 |
| `tenant_id` | UUID NOT NULL | 多租户隔离（所有查询必须带） | :205 | :126 |
| `title` | VARCHAR(512) | 文档中文全称 | :206 | :127 |
| `modality` | VARCHAR(12) | `text`/`table`/`image_text` | :207 | :128 |
| **`asset_no`** | VARCHAR(40) NOT NULL | **资产编号 = 资产身份**；与 `tenant_id` 组成自然键 `uq_doc_asset_tenant_asset_no`（幂等重跑的依据） | :208, :200 | :129, :138 |
| **`authority_level`** | INT NOT NULL default 3 | **权威级**：1 国家/行业级、2 指南、3 说明书、4 科普（`doc="1 national std, 2 guideline, 3 label, 4 popular science"`）；冲突消解「权威优先」的判据 | :209-210 | :130 |
| `doc_type` | VARCHAR(24) NOT NULL | 六类之一（同 §2 白名单） | :211-212 | :131 |
| `source_url` | TEXT | 出处 URL（合规/溯源证据；离线文件可为空） | :213 | :132 |
| `source_note` | TEXT | 合规登记行（由 `registry.license_note` 落库），"provenance ledger entry" | :214 | :133 |
| **`supersede_of`** | UUID → `doc_asset_t(id)` | **版本血缘**：新版行指回被它替代的旧版行（自引用外键） | :215 | :134 |
| `parse_status` | VARCHAR(16) | 解析状态（登记时初始 `pending`，见 `ingest_service.py:90`） | :216 | :135 |
| `parse_quality` | FLOAT | 解析体检分（`parse_checkup.py` 打分后回写，`ingest_service.py:370-389`） | :217 | :136 |
| `meta_data` | JSONB（**物理列名 `metadata`**） | `{split, row_number}` + 有值时的 `published_at`；零 DDL 承载新语义 | :218-221 | :137 |
| `created_at` | TIMESTAMPTZ default now() | **墙钟**（=登记时刻），**不是**业务时间 | :222 | :139 |

### 4.1 血缘的四条线（谁指向谁）

1. **资产身份线**：`(tenant_id, asset_no)` 唯一（`:200`）→ 同一份语料重复摄取只 `skipped` 不重复（`ingest_service.py:330-367`）。
2. **版本血缘线**：`supersede_of`（外层行 ↔ 旧版行）。配对规则在 `ingest_service.py:176-219`（按标题「年份家族」配对 2020→2024 等），实况 `lineage_applied=2`（`ingest_manifest.json:9`）：`guide-2024 → guide-2020` 与 `guide-elderly-2024 → guide-elderly-2021`（同 §7 对账表）。
3. **证据血缘线**：`kg_evidence_t.doc_id`（来源文档）→ `doc_asset_t.id`，并带 `span_loc={chunk_idx, page, bbox?}` + `span_text` + `tag`（`EXTRACTED`/`INFERRED`）+ `doc_version_id`（指向文档版本）（`knowevo_db.py:154-174`）。
4. **业务时间线（fact time）**：`registry.published_at` → `doc_asset_t.metadata.published_at`（`ingest_service.py:76-80`）→ 作为 `kg_relation_t.valid_at` 的来源，供版本钉住谓词 `valid_at <= t_v AND (invalid_at IS NULL OR invalid_at > t_v)` 判真（谓词见 `...kw_005_fact_time.sql:13`；问题陈述 `:9-15`；三阶段修复 `:23-43`）。读取方：`KgService.doc_published_at`（`backend/services/knowevo/kg_service.py:571-596`）、`OntologyService.max_doc_published_at`（`backend/services/knowevo/ontology_service.py:213-236`）。

> **命名注意（文档 ≠ 物理名）**：`agent-config.md` §4、`docs/call-graph.md:113` 等处的 `kg_graph` 是**图谱资产的口头简称**，不是表名；物理表是 `kg_entity_t` / `kg_relation_t` / `kg_evidence_t`（`backend/database/knowevo_db.py:94, 129, 154`）。对外引用按 `kg_graph` 叙述可以，写 SQL / 写清单时必须用物理表名。

---

## 5. 上传与分块链路（谁生成 chunk，chunk 在哪）

```
corpus/<dir>/<file>
  └─(1) POST /api/file/upload            multipart：file + destination=minio + index_name
        └─ 返回 uploaded_file_paths[] / uploaded_filenames[]
  └─(2) POST /api/file/process           json：files[{path_or_url,filename}] + index_name
        │                                + destination + chunking_strategy="basic"
        └─ data-process 容器解析（Unstructured）→ bge-m3 向量化（1024 维，模型 id=3）
           → 写入知识库索引（基于 Elasticsearch 的向量/混合检索）
  └─(3) POST /api/indices/{index_name}/chunks   ← 解析体检反查：拉全量 chunk 打分
```

- 三段端点：`ingest_service.py:285-313`（客户端）与 `ingest_service.py:9-13`（模块注释里逐条记的平台源码位置）；实际批跑脚本用 shell 同款三段：`corpus/ingest_batch.sh:22-38`（`chunking_strategy=basic`、`destination=minio`、串行 `sleep PACE`）。
- **分块策略取值**：链路里只用 `basic`（`ingest_batch.sh:37`、`ingest_service.py:296` 默认值）。平台另支持手动 chunk（`ChunkCreateRequest`/`ChunkUpdateRequest`，`backend/consts/model.py:920-944`），本项目未使用 —— **待确认项**（无使用证据，故不声称）。
- **索引标识（两套名字，别混）**：
  - CLI 默认 `--index kw-medical-b1`（`pipeline/ingest_assets.py:240`，用法串同 `:11`；测试同值 `test/backend/services/knowevo/test_ingest_assets.py:237-248`）；
  - 实际批跑与体检脚本用的是平台生成的索引 id `1-fdc99d691db5406e980751dbe131f201`（`corpus/ingest_batch.sh:8`、`corpus/parse_checkup.py:26`）。
  - 两者哪一个是「当前生效索引」**待确认**（无一方可与文档互证；本文件如实并置，不做合并猜测）。
- **chunk 实况（截至解析体检）**：索引 chunk 总量 **1497**；落库 `parse_quality` 58 份；低分（<0.6）0 份；未入索引 18 份（全部为 blind 切分）（`corpus/parse_report.md:4-7`）。体检表的「全量分数」块含 40 行（= build 40 份），chunks 求和 **1497**，与 `:4` 自洽（现场求和验证，见 §7）。

---

## 6. 知识库文件的存放与命名约定

| 约定 | 规则 | 出处 |
|---|---|---|
| 语料根目录 | `competition/corpus/`（CLI 侧常量 `CORPUS_ROOT` 指到同一处，故 `local_file` 始终是**相对路径**） | `pipeline/ingest_assets.py:33` |
| 子目录 | 按 `doc_type` 分五个目录：`guidelines/`(11) · `drug_labels/`(28) · `pathways/`(9) · `lab_standards/`(2) · `public_edu/`(8) | 现场统计 `registry.csv` 的 `local_file` 前缀 |
| 文件命名 | 小写 + 下划线（`t2dm_guideline_2020.pdf` / `empagliflozin_jardiance.html` / `edu_who_factsheet.html`）；版本年写进文件名 | `corpus/guidelines/`、`corpus/drug_labels/`、`corpus/public_edu/` 实况 |
| 资产编号（`asset_no`） | 按来源类型前缀：`guide-*` / `cp-*`（临床路径） / `drug-*` / `std-*`（标准与检验） / `edu-*`（科普）；含年份者以 `-YYYY` 结尾（R4 推导规则依赖该形态） | `registry.csv` 实况；`pipeline/derive_published_at.py:56`（`_ASSET_YEAR = -(20\d{2})$`，规则 R4 见 `:26-29`） |
| `split` 切分 | **确定性**：`int(sha256(asset_no)[:8],16) % 10 < 8` → build，否则 blind；2020/2024 锚文档强制进 build | `corpus/build_registry.py:10-14`；结果 `corpus/blind_split.json:2-5` |
| 数据库 schema | 全部 KnowEvo 域表在 `nexent` schema | `backend/database/knowevo_db.py:33` |
| 表命名 | `*_t` 后缀 + 12 张域表 + 1 张运行台账 | `backend/database/knowevo_db.py:2` |
| 迁移命名 | `v2.5.5_kw_001 ~ kw_010`（历史文件名，保留不改；对外版本叙述用 v2.6.0） | `ls deploy/sql/migrations/*kw_*.sql`；红线 §3.1 |
| 评测/体检产物 | 一律落 `competition/` 内（`corpus/` 数据件、`deliverables/` 证据件、`docs/` 叙事件），不进 `/tmp` | `AGENTS.md:65` 交付纪律 |

---

## 7. 数字 → 出处 对账表

| 数字 | 出处（`file:line` / 命令） |
|---|---|
| 58 份文档 | `corpus/registry.csv`（59 行含表头，现场统计 `len(DictReader)=58`）；`corpus/ingest_manifest.json:5`；`corpus/blind_split.json:3`；`corpus/parse_report.md:3`；`deliverables/agent-config.json:53`；`docs/agent-config.md:83` |
| doc_type 分布 28/11/9/8/2 | 现场统计 `registry.csv`（`drug_label 28 / guideline 11 / policy 9 / edu_graphic 8 / lab_report 2`）；人类标签口径见 `docs/agent-config.md:84`、`agent-config.json:55-57` |
| `authority_level` 分布 18/11/28/1 | 现场统计 `registry.csv`；语义定义 `backend/database/knowevo_db.py:209-210`、`backend/services/knowevo/ingest_service.py:50, 147, 159-160` |
| build 40 / blind 18 | `corpus/blind_split.json:4-5`；`docs/agent-config.md:84`（composition 未含 split，故只引前两处） |
| 未入索引 18 份（全 blind） | `corpus/parse_report.md:7`（其下方 18 行明细同表） |
| 索引 chunk 总量 1497 | `corpus/parse_report.md:4`；体检表 40 行 chunks 求和 = 1497（现场求和验证，2026-09-23） |
| 落库 parse_quality 58 份 / 低分 0 | `corpus/parse_report.md:5-6` |
| 每份 chunk 数（如 guide-2020=332 / guide-2024=407） | `corpus/parse_report.md` 「全量分数」表；`tasks/T-02-brief.md:49`（同值交叉印证） |
| registered 19 / skipped 39 / lineage 2 | `corpus/ingest_manifest.json:7-9`；`docs/cost-ledger.md:10`（同一 run_id `ingest-20260915011057`） |
| uploaded 0 / indexed 0（该 CLI 运行） | `corpus/ingest_manifest.json:10-11`；`docs/cost-ledger.md:10` |
| 血缘 2 对：guide-2024→guide-2020、guide-elderly-2024→guide-elderly-2021 | `tasks/T-02-brief.md:35`（psql 对查验收行）；配对算法 `ingest_service.py:176-219` |
| 12 张域表 + 1 张运行台账 | `backend/database/knowevo_db.py:2` |
| 迁移 10 个（kw_001~kw_010） | `ls deploy/sql/migrations/*kw_*.sql`（10 个文件，2026-09-23） |
| 构建租户 UUID | `deliverables/agent-config.json:7`；`corpus/parse_checkup.py:27`；`docs/cost-ledger.md:22` |
| 索引 id `1-fdc99d691db5406e980751dbe131f201` | `corpus/ingest_batch.sh:8`；`corpus/parse_checkup.py:26` |
| CLI 默认索引名 `kw-medical-b1` | `pipeline/ingest_assets.py:11, 240`；`test/backend/services/knowevo/test_ingest_assets.py:237-268` |
| 检索延迟 ~450ms 级冒烟 | `tasks/T-02-brief.md:50`（curl 实测 query_time 449ms，另两组 438/451ms）——**brief 证据行**，非本文件新测 |
| bge-m3 1024 维 / 模型 id=3 | 模型档 `deliverables/agent-config.json:22`（model_id 3 = bge-m3，embedding）；1024 维见 `tasks/T-02-brief.md:51` 与 `deliverables/evidence-index.md:18`（`EmbeddingModelInfo name=bge-m3 dim=1024`） |

---

## 8. 已知不一致与待确认项（如实列出）

| 项 | 说明 | 处置 |
|---|---|---|
| `ingest_manifest.json` 的 `indexed=0` 与「40 份已入索引」并存 | 前者只反映 CLI 那一次运行的 `--ingest` 段（`uploads: []`，`:244`）；实际上传由 `corpus/ingest_batch.sh` 完成，不入 manifest | 本文件 §3 已并置说明；**两个数字都是真的，但回答的是不同问题** |
| `kw-medical-b1` vs `1-fdc99d691db5406e980751dbe131f201` | 仓库内两套索引标识并存，无一处文档把二者对上 | **待确认**：需在运行栈上 `GET /api/indices` 或管理页核对后再定稿；本文件不合并猜测 |
| `kg_graph` 不是表名 | 文档简称，物理表为 `kg_entity_t`/`kg_relation_t`/`kg_evidence_t` | 本文件 §4 已加命名注意；物理名出处 `knowevo_db.py:94,129,154` |
| `parse_report.md` 的 chunk 数来自**平台索引**，非本地重算 | 复现需 `KW_TOKEN_FILE` + 运行栈（脚本会读 `/tmp/t02_token.txt`） | 复现步骤见 `corpus/parse_checkup.py:24-27`；离线环境下该数**不可复算**，引用时注明来源 |
| `modality` 全为 `text` | 58 行实测均为 `text`；`table`/`image_text` 白名单存在但本批未使用 | 现场统计；如需图文模态语料，属后续批次范围 |
| 平台侧知识库「display_name / 前端知识库页配置项」 | 本文件未收录平台知识库记录表（`KnowledgeRecord`）的字段清单 | **待确认**：与本次「文件说明」范围无关，若官方要求补「知识库配置项」再单列一节 |

---

## 9. 复现命令（验收用）

```bash
# 1) 语料登记自检（零副作用）
cd /home/qianqian/Work/All/Nexent/nexent
head -1 competition/corpus/registry.csv | tr ',' '\n' | nl        # 10 列
wc -l < competition/corpus/registry.csv                            # 59（含表头）
python3 -c "import csv;print(len(list(csv.DictReader(open('competition/corpus/registry.csv',encoding='utf-8-sig')))))"   # 58

# 2) CLI dry-run（不写库、不打网络）
cd backend && .venv/bin/python -m services.knowevo.pipeline.ingest_assets \
    --registry ../competition/corpus/registry.csv --dry-run

# 3) 索引 chunk 反查（需运行栈 + token，见 corpus/parse_checkup.py:24-27）
KW_TOKEN_FILE=/tmp/t02_token.txt python3 competition/corpus/parse_checkup.py

# 4) 资产/血缘核对（需 PG）
psql -c "\d nexent.doc_asset_t"
psql -c "select a.asset_no, b.asset_no from nexent.doc_asset_t a \
         join nexent.doc_asset_t b on a.supersede_of = b.id;"
```

---

## 10. 与其他交付物的关系

- **MCP 册**：`competition/docs/mcp-file-guide.md`（自研 5 工具、双注册、schema、错误结构、tenant 解析）。
- **母本**：`competition/docs/agent-config.md` §4/§6（本文件不改母本；母本继续作为 Agent 配置总览）。
- **机器可读配置**：`competition/deliverables/agent-config.json` 的 `knowledge_base` 块。
- **数字权威**：token/延迟类数字以 `docs/cost-ledger.md` 为准；本文件只搬运与文件结构相关的数字并给出出处。
- **平台表述前置关口**：任何平台能力表述先过 `docs/verification-reports/platform-facts-redline.md` §1-§3（本文件已按其 §3.1 版本口径与 §3.4 检索口径撰写）。
