# summary_store.py —— L5 社区摘要持久化 store：kg_summary_t 幂等写 + 版本读
**归属任务**: T-08 后续项（2026-09-30 新建）· 依据: `nexent/competition/docs/tech-optimization-2026-09-28/l5-community-summary-design-2026-09-29.md` §6（表草案）+ 踩坑 #129/#108 前置纪律族
**迁移**: `deploy/sql/migrations/v2.5.5_kw_012_kg_summary.sql`（DDL 唯一事实源；本模块不建表、不跑迁移）

## 职责

把 L5 内核（`community_summary.py`）产出的每社区摘要持久化到 `nexent.kg_summary_t`，并按 (tenant, ontology version) 读回。**只做持久化**：不聚类、不调模型、不做检索接线；`schemas.Route` 分支与 `global_route` 的生产调用点仍归 T-08——当前没有任何运行路径 import 本模块，默认检索/路由行为零变化。

session 纪律对齐 `graph_store` / `doc_asset_service`：每次调用一个 session（context manager 退出即一次 commit），**异常不吞**。`session_factory` 可注入（离线测试用），默认解析共享 `_get_db_session`。

## 接口冻结

> **裁决注记（2026-09-30）**：重建时 `skeleton_json["size"]` 与 `len(member_stable_ids)` **交叉校验**，不一致 = 改坏的行 → `ValueError`（仍不重算 fingerprint）。结构 size 事实源 = `len(member_ids)`。


```python
SUMMARY_KIND_SKELETON = "skeleton"   # 永远可用的回退行（协议 on_gate_fail）
SUMMARY_KIND_LLM = "llm"
SUMMARY_KINDS = ("skeleton", "llm")  # 迁移 CHECK 同款两值词表
VERSION_REF_MAX_LEN = 20             # 对齐 ontology_version_t.version 列宽
COMMUNITY_ID_MAX_LEN = 80            # 对齐 kg_entity_t.stable_id 域

@dataclass(frozen=True)
class SummaryRecord:
    skeleton: SkeletonSummary         # 内核骨架（fingerprint 在内）
    member_ids: tuple[str, ...]       # 非空、升序；且 skeleton.community_id == member_ids[0]
    level: int = 0                    # 内核只产 0；>0 预留
    summary_kind: str = SUMMARY_KIND_SKELETON
    summary_text: str | None = None   # 仅 kind='llm' 可非空
    model: str | None = None          # 仅 kind='llm' 可非空（模型身份溯源）
    llm_protocol_version: str | None = None  # 仅 kind='llm' 可非空
    llm_gate_passed: bool | None = None      # kind='llm' 必须为 True
    graph_snapshot_at: datetime | None = None

class SummaryStore:
    def __init__(self, session_factory: Any = None): ...
    async def save_summaries(self, tenant_id: str, version_ref: str,
                             summaries: Sequence[SummaryRecord]) -> int: ...
    async def load_summaries(self, tenant_id: str, version_ref: str) -> list[SummaryRecord]: ...
```

## 语义（冻结）

### 键与幂等

- `version_ref` = 本体版本标签（`ontology_version_t.version`）。摘要是**一个版本下图状态的快照**，不同版本的摘要集共存互不覆盖——这正是本表**不加** bi-temporal 列的原因：版本标签承载时间语义，再加 valid_at/invalid_at 会造出与 `ontology_version_t` 对不齐的第二口钟。UNIQUE = `(tenant_id, ontology_version, community_id, level)`。
- `save_summaries` 幂等：同 (tenant, version, community_id, level) **重写覆盖**（全部可变列 + `updated_at = now()`，DB 侧时钟，不信任调用方时钟），绝不追加重复行；批内重复键 = 后写胜（按实参顺序，确定性）；返回写入行数（插入 + 更新）。其它版本 / 其它租户的行不动。
- 空 `summaries` → 返回 0 且不碰 session。
- `load_summaries` 按 (level, community_id) 升序返回——level 0 集合即内核的 community_id 升序；行在 session 内重建为 SummaryRecord（session 退出后 ORM 属性过期，禁止出 session 再摸属性）。

### 指纹边界

**store 不重算 fingerprint**：「图未变 → 跳过重算」是调用方拿存储指纹与现算骨架指纹比对的决策（设计 §6）；store 里复制内核 payload 配方 = 第二份身份函数，禁止。重建走 SummaryRecord 校验：改坏的行抛 `ValueError`，不静默接受。

### 校验（TypeError / ValueError，校验先行）

- `tenant_id` / `version_ref` 非空 str；`version_ref` 长度 ≤ 20、`community_id` ≤ 80（对齐迁移列宽，提前给指向性报错而非 PG DataError）。
- `summaries` 必须是 Sequence[SummaryRecord]（str/bytes 显式拒绝）；每项必须是 SummaryRecord。
- SummaryRecord：`member_ids` 非空升序且 `skeleton.community_id == member_ids[0]`（保证内核 Community 可精确重建）；`level` ≥ 0；`fingerprint` 是 64 位小写 sha256 hex；`graph_snapshot_at` 为 datetime 或 None。
- 门纪律（与迁移 CHECK `ck_kgs_llm_gate` 是同一条规则的两端）：`kind='skeleton'` 行的 `summary_text` / `model` / `llm_protocol_version` / `llm_gate_passed` 必须全 None（一列一语义）；`kind='llm'` 行必须有非空 `summary_text`、非空 `llm_protocol_version`、`llm_gate_passed is True`——**门失败的 LLM 输出不落 llm 行**（落 skeleton 行，`LLM_SUMMARY_PROTOCOL.on_gate_fail`）。`model` 若给出必须非空。

### 前置纪律（坑 #129 族）

无 published 版本 / 无摄取图的租户会**静默产出零社区 → 零行**，与管线故障不可区分。生成/读取摘要前先答「该租户有没有 published 本体版本」（同构于坑 #108「连库前先答连的哪套」）。

## 表与迁移

`nexent.kg_summary_t` 由 `v2.5.5_kw_012_kg_summary.sql` 定义（设计 §6 草案的落地版，差异均有 WHY 注释）：新增 `ontology_version` 列并入 UNIQUE；`member_ids` 更名 `member_stable_ids`；新增 `model` 列；UNIQUE 扩为 (tenant_id, ontology_version, community_id, level)；两个 CHECK（kind 两值词表内联 + `ck_kgs_llm_gate` 门纪律）；索引 `ix_kgs_fp(tenant_id, fingerprint)`；草案的 `ix_kgs_tenant` 被 UNIQUE 前缀取代（读路径恒为版本内）。迁移由 runner（`deploy/common/run-sql-migrations.sh`）自动登记 `nexent.schema_migrations`（文件名 + checksum），无需手工注册。ORM 模型 `KgSummary` 定义在本模块（单文件自持；**不进** `KNOWEVO_MODELS`——那是 12 张冻结域表 + T-06 run ledger 的清单，本模块也不在任何 create-all 路径上）。

## 诚实边界

- 不聚类、不调 LLM、不重算指纹、不做检索接线；当前零运行路径 import 本模块。
- 摘要质量 / 真实聚合题增益 = `insufficient_data`（须走设计 §5 的 E2 式消融并遵守 Zeng null 路径）。
- 真库行为（JSONB 往返 / CHAR(64) / CHECK 约束）由 PG-gated 测试覆盖（`RUN_POSTGRES_INTEGRATION=1`；套件默认 skip，只读探查不写库）。

## 验收锚点

- `pytest test/backend/services/knowevo/test_summary_store.py -q`：离线层全绿（迁移静态断言 / SummaryRecord 校验 / fake session 幂等覆盖往返 / 版本共存与租户隔离 / 排序确定性 / 异常不吞）；
- 全量回归：`pytest ../test/backend/services/knowevo/ -q` 基线不降；
- `ruff check backend/services/knowevo` 0 违例；
- 契约双副本逐字节一致（根 `knowevo/` + 仓内 `nexent/knowevo/`）。
