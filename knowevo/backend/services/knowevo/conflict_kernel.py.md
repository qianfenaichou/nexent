# conflict_kernel.py —— A4 知识冲突消解内核
**新建**: 2026-09-30（A4 内核）· 依据: A4 冲突消解设计（检测→分类→消解三段，设计唯一权威为本文档接口冻结节）

## 职责

知识冲突消解的算法内核：把「contested 边不自动合并 / never silently overwrite」的**原则**落成**可回放算法**——检测（同键 + 值矛盾 + valid_at 重叠）→ 分类（四类归因词表冻结）→ 消解（权威优先级 + bi-temporal 版本时钟 + 可选 LLM seam）。输出**每条冲突的裁决记录**（可溯源、可回放）。纯函数、stdlib-only、零 DB、零 LLM。

与既有锚点的分工（**本模块不改它们**）：`schemas.ConflictAdjudication` / `conflict_adjudications` / `contested` / `conflict_signal` 是**接线面**（schemas.py:426-476, :672-732）；`graph_store` 的 `valid_at`/`invalid_at`/`supersede()` 是**持久化面**（其注释明确 conflict resolution is the service layer's job）；`version_pin.VersionClock` 是**时钟源**；`decision_service` 已有 contested 标记与 `conflict_adjudications` 回填（:1071）。本内核只做裁决计算，`to_wire()` 投影出 schemas 冻结三元组供后续接线直接回填。

## 接口冻结

```python
CONFLICT_KINDS = ("EVOLUTION", "SOURCE_AUTHORITY", "EXTRACTION_ERROR", "SAME_SOURCE")
FACT_TYPES = ("relation", "attribute")
PREFER_AUTHORITY = "authority"; PREFER_VERSION = "version"
# resolution 字面量: authority_win | version_win | tie_break_id
#                 | requeue_extraction | human_review | llm_seam

> **裁决注记（2026-09-30）**：`ReconcileResult.kind_counts` 字段类型保持契约冻结的 `dict[str, int]`；构造完成后视为**只读**，调用方禁止原地改写（frozen dataclass 不挡嵌套可变）。赛后若破签名一并改 Mapping。


class ConflictKind(str, Enum):  # 闭词表，仅上列四值
    EVOLUTION / SOURCE_AUTHORITY / EXTRACTION_ERROR / SAME_SOURCE

@dataclass(frozen=True)
class Fact:
    fact_id: str
    fact_type: str            # relation | attribute
    subject: str
    predicate: str
    object: str = ""          # relation 必填（dst）；attribute 恒 ""
    value: str = ""           # 断言内容（属性值 / 关系极性·限定词）；值矛盾即此字段不等
    source_id: str = ""
    source_group: str = ""    # 谱系/系列（同一指南跨年份）
    version: str = ""         # 版本标签（edition）
    authority_level: int = 3  # 平台标度 1 国标 .. 4 科普；**小者权威高**
    valid_at: datetime | None = None      # 闭开区间 [valid_at, invalid_at)
    invalid_at: datetime | None = None
    claim: str = ""

    def conflict_key(self) -> tuple[str, str, str, str]: ...
        # relation -> (relation, subject, predicate, object)
        # attribute -> (attribute, subject, predicate, "")

@dataclass(frozen=True)
class ConflictCandidate:
    left: Fact                # 恒 left.fact_id <= right.fact_id
    right: Fact
    key: tuple[str, str, str, str]
    @property
    def conflict_id(self) -> str: ...   # sha256(key|left|right)[:16]

@dataclass(frozen=True)
class ClassifyContext:
    extraction_error_ids: frozenset[str] = frozenset()

@dataclass(frozen=True)
class ConflictAdjudicationRecord:
    conflict_id: str
    kind: str
    resolution: str
    winner_id: str
    loser_id: str
    winner_source: str
    loser_source: str
    basis: str
    policy: str
    version_clock_iso: str
    winner_authority: int
    loser_authority: int
    winner_valid_at_iso: str
    loser_valid_at_iso: str
    llm_called: bool
    contested: bool
    superseded_ids: tuple[str, ...]     # 败者打戳保留（非删除）
    replay_key: str                     # sha256(裁决要点)[:16]
    def to_wire(self) -> dict[str, str]: ...  # {conflict_id, type, resolution}

@dataclass(frozen=True)
class ReconcileResult:
    candidates: tuple[ConflictCandidate, ...]
    records: tuple[ConflictAdjudicationRecord, ...]
    kind_counts: dict[str, int]
    @property n_conflicts: int
    @property n_contested: int
    @property superseded_fact_ids: tuple[str, ...]

def windows_overlap(a: Fact, b: Fact) -> bool: ...
def valid_at_clock(fact: Fact, clock: datetime) -> bool: ...
def detect_conflicts(facts) -> list[ConflictCandidate]: ...
def classify_conflict(candidate, context: ClassifyContext | None = None) -> ConflictKind: ...
def resolve_conflict(
    candidate, *,
    authority_rank: Mapping[str, int] | None,
    version_clock: Any,                 # datetime 或带 .as_of 的 VersionClock
    llm: Callable[[ConflictCandidate], Any] | None = None,
    prefer: str | None = None,          # None=按 kind 默认 | "authority" | "version"
    context: ClassifyContext | None = None,
) -> ConflictAdjudicationRecord: ...
def reconcile(
    facts, *,
    authority_rank: Mapping[str, int] | None = None,
    version_clock: Any,
    llm=None, prefer=None, context=None,
) -> ReconcileResult: ...
```

## 语义（冻结）

**检测**：候选 = 同 `conflict_key` **且** `value` 不等 **且** 窗口重叠。窗口为半开 `[valid_at, invalid_at)`（对齐 `graph_store` / 迁移 kw_011 的 `'[)'`）：端点相接**不**重叠；`valid_at == invalid_at` 为空窗永不冲突；`invalid_at < valid_at` 构造即 `ValueError`；`None` 端 = ±∞。成对产出（3 值分裂 A/B/A → 恰两对）；`left.fact_id ≤ right.fact_id`，列表按 `(key, left, right)` 排序——**输入序无关**。重复 `fact_id` 抛 `ValueError`。

**分类（闭词表，先匹配先赢）**：
1. 任一侧 ∈ `extraction_error_ids` → `EXTRACTION_ERROR`（抽取错误）；
2. 同 `source_id` 且同 `version`（含双空）→ `SAME_SOURCE`（同源矛盾）；
3. 同 `source_group` 或同 `source_id`，且 version 不等 → `EVOLUTION`（真·知识演化）；
4. 其余 → `SOURCE_AUTHORITY`（来源差异）。

**消解**：两轴固定算法——
- 权威轴：`authority_rank[source_id]`（缺省回落 `Fact.authority_level`），**小者胜**（平台 1 国标 .. 4 科普，`ranked_by authority_level asc`）；
- 版本轴（bi-temporal）：先比 `valid_at ≤ t_v < invalid_at`（版本时钟上的现势），再比 `valid_at` 越晚越新。

kind 默认主轴（spec 消解策略）：`EVOLUTION`→版本（时效优先）/ `SOURCE_AUTHORITY`→权威（authority_level 定现行）/ `EXTRACTION_ERROR`→标记侧必败（回抽取队列）/ `SAME_SOURCE`→`contested`+`human_review`（人审，仍记 provisional winner）。`prefer` 可显式覆盖主轴。破平序：主轴 → 次轴 → `fact_id` 字典序（`tie_break_id`）。败者进 `superseded_ids`（打戳保留，非删除）。权威压版本 / 版本压权威均可配置（测试钉死）。

**LLM seam**：`llm=None`（默认）**绝不调用**；即使传入 callable，也只在 `SAME_SOURCE` 触发一次，`resolution="llm_seam"` 且 **`contested` 保持 True**（seam 不静默消 contested）。其余三类纯规则。

> **2026-09-30 双轴审查 P2 增量注记**：①`llm` 返回值**不参与裁决**（签名语义为咨询记录，非投票）；②`Fact.claim` 为**调用方透传溯源文本**，本内核 detect/classify/resolve/to_wire 全路径不消费。

**确定性**：同事实集任意排列 → 相同 `candidates`/`records`（含 `conflict_id`/`replay_key`）；`version_clock` 接受 `datetime` 或 `VersionClock`（取 `.as_of`）；naive datetime 按 UTC 解释（对齐 `version_pin._ensure_aware`）。

**校验（fail-fast）**：`fact_id` 空 / `fact_type` 越界 / subject·predicate 空 / relation 缺 object / `authority_level` 非正 int / `invalid_at < valid_at` → 构造 `ValueError`；`prefer` 越界 → `ValueError`；`version_clock` 既非 datetime 又无 `.as_of` → `TypeError`。

**成本对照**：零 LLM、零 DB、纯 stdlib；每冲突一次裁决计算 O(1)。

## 与 L5 / E10 的对照

L5 回答「这个社区聚合说了什么」（检索/聚合面）；A4 回答「这两条主张谁现行、为何」（演进完整性面）。二者共用「变更是常态」前提（提分总纲 §L5 原注）。planned E10（冲突消解消融）尚未跑真实语料；本内核探针是其**机制级前身**，不替代 E10。

## 诚实边界

- **不估计权威等级**：rank 由调用方从 `doc_asset_t` 等处装配；本模块只按给定映射/字段比较。
- **不接生产写路径**：不写库、不调 `supersede`、不动 `schemas`/`decision_service`；接线属后续工作。
- **不提取 value**：关系极性/属性值由调用方填入 `value`；双空 value 的同键事实不构成值矛盾。
- **成对而非成组**：3 路矛盾产出 3 条成对记录，不做组合归并。
- **探针数字是合成种植面**（48/48 分类正确）**不可外推**真实语料；真实 E10 = `insufficient_data`。

## 验收锚点

- `pytest test/backend/services/knowevo/test_conflict_kernel.py -q`：55 用例离线全绿（四类分类 / 窗口开闭·零重叠·单点·空窗 / 权威压版本与版本压权威可配置 / 同分破平 / 输入序无关 / 空输入 / llm=None 不调用且 seam 仅 SAME_SOURCE 触发 / 校验异常 / to_wire 三元组）；
- 冻结探针：`python3 competition/experiments/probe_a4_conflict.py` → `competition/deliverables/a4-conflict-probe.json`（136 facts / 48 种植对 / 四类各 12；双跑文件 sha256 一致；`content_sha256` 自校验配方见该 JSON；哈希体内无时间戳、无绝对路径）；
- 全量回归收据：见设计文档 §6（本任务不改既有台账文件，收据由主会话统一登记）。
