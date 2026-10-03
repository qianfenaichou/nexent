# budget_curve.py —— L8 人审预算-质量曲线内核
**新建**: 2026-09-29（L8 内核）· 依据: 人审预算-质量曲线协议（协议唯一权威为本文档，详见正文职责节）

## 职责

A2 空白 / E3 的算法内核：给定 L7 规划器的 `per_item_voi`（键序=贪心序）、逐项确认成本表、预算序列，产出**预算-质量曲线点**——每个预算检查点上专家能确认哪些项，以及该确认前缀上的本体质量四指标（Cov/Red/Dep/Align）。纯函数、导入期 stdlib-only、零 DB、零 LLM。

科学主张刻意收窄：**「固定专家确认预算下，本体质量四指标随确认次数的曲线」**。**不**宣称「半自动比人快 X 倍」（那需要专家基线，本项目没有）。目标阈值（02）：Cov≥85% / Red≤5% / Dep_max≤5 Dep_mean∈[2,3] / Align≥60%。

与 L7 的复用接缝：`update_planner.plan_minimal_update.per_item_voi`（含 skipped、键序=贪心序）直接作为本模块 VOI 臂的排序输入，**只读 import，本模块不改 update_planner**。证据门（2×2 门×序）由 `gate_filter` 露出：门在排序**之前**滤掉不合格项，被门滤掉的项不消耗确认预算。

数据装配（p_change/impact/confidence 的估计、真实人审分钟数）归调用方；**生产接线同样由调用方完成**（与 update_planner / asset_search 同一分层方式）。

## 接口冻结

```python
QualityFn = Callable[[tuple[str, ...]], Mapping[str, Any]]   # 确认前缀 -> 质量映射

@dataclass(frozen=True)
class CurvePoint:
    budget: float                      # 预算检查点
    confirmed_ids: tuple[str, ...]     # 评审序下装得进预算的前缀
    n_confirmed: int
    cost_spent: float
    quality: dict[str, Any]            # quality_fn 的结果（拷贝，不别名）

def order_by_score(scores: Mapping[str, float], *, descending: bool = True) -> tuple[str, ...]: ...
def order_randomly(ids: Iterable[str], seed: int) -> tuple[str, ...]: ...
def gate_filter(ids: Iterable[str], keep: Callable[[str], bool]) -> tuple[str, ...]: ...

def k0_quality_fn(
    base_classes: Sequence[Mapping[str, Any]],
    item_classes: Mapping[str, Sequence[Mapping[str, Any]]],
    seed_terms: Sequence[str] | None = None,
    *,
    metrics_fn: Callable[..., Mapping[str, Any]] | None = None,
) -> QualityFn: ...

def budget_curve(
    per_item_voi: Mapping[str, float],
    costs: Mapping[str, float],
    budgets: Sequence[float],
    quality_fn: QualityFn,
    *,
    order: Sequence[str] | None = None,
) -> list[CurvePoint]: ...
```

## 语义（冻结）

**评审序**：缺省 = `tuple(per_item_voi)` 键序（= L7 贪心序）。`order` 给出时必须是 id 集的排列（置信序 / 随机序臂）；`per_item_voi` 在该臂仅作 id 集与 VOI 报告来源。

**前缀准入（prefix）**：沿评审序走，`spent + cost_i <= budget` 才确认；**首个装不下的项即停，不跳过它去拿更便宜的后项**——与 L7 规划器「边际赤字即停、不复扫」同一条规则。横轴「确认次数」= 全部 `cost=1.0` 且 `budgets=1..n` 的特例。

**Quality 注入**：`quality_fn(confirmed_ids)` 在每个检查点被调用，返回映射（拷贝进 CurvePoint）。推荐默认 = 生产 K0 四指标 `ontology_service._k0_metrics_from_snapshot`（05 §3.4 权威）；`k0_quality_fn` 负责拼装快照并懒加载该实现（故本模块导入期仍只依赖 stdlib），`metrics_fn` 可注入以便离线测试。`base_classes` / `item_classes` 不被修改。

**顺序工具**：`order_by_score` 分数降序、破平 id 字典序升序（与 update_planner 全序纪律一致）；`order_randomly` 种子化确定性排列（随机序负对照臂）；`gate_filter` 保序过滤（证据门臂）。

**退化语义**：空候选集 → 每个预算一个空 CurvePoint（`confirmed_ids=()`、`cost_spent=0.0`、`quality=quality_fn(())`）。预算 0 → 空前缀。

**校验（校验先行）**：`costs` 非正/非有限、`budget` 负/非有限、`per_item_voi` 与 `costs` id 集不一致、`order` 非排列或重复 id → `ValueError`；`quality_fn` 不可调用 → `TypeError`。

**确定性**：同一输入（任意映射插入序 + 同一 `order`）→ 逐位相同的曲线；`quality_fn` 自身须确定（探针里用固定 seed 的合成池 + 生产 K0）。

## 关键度量性质（协议必读，不是本模块的 bug）

生产 `cov`/`align` 是**当前类集上的占比**：确认一个未被 seed 覆盖 / 无 anchor 的类会**稀释**比率。因此原始四指标曲线在混入噪声项时**非单调**。协议与探针同时报告：①生产四指标原样（权威）；②绝对计数伴随量（`n_classes`/`n_covered`/`n_anchored`/`n_redundant_parent_edges`）；③固定分母的 `cov_seed_fixed_denominator`（=已覆盖/seed 词表数）——阈值可达性与「预算到 Q*」用后者与全核心态 Q* 对照，且如实标注三者口径不同。

## 诚实边界

- 本模块**不估计任何概率**（p_change/impact/confidence 归调用方），**不读数据库**，**不折算成本单位**（全表同单位即可）。
- 真实人审分钟数在 `evolution_round_t.cost.human_minutes`，`evolution_service` settle 默认写 `0.0`（`:450/:646`）→ 真人分钟缺失时调用方必须报 `insufficient_data`，**不得**用文献系数（α=0.75 min/实体、β=0.63 min/关系，L 级）冒充实测人时。
- 建议偏置（Schroeder 2025：人审 LLM 建议未更快且改变标签分布）由协议的随机置条件 / 独立校标 / 建议可见-不可见两臂控制，不在本内核。

## 与 L7 / 探针的共用点

- 输入直接消费 `plan_minimal_update.per_item_voi`（键序=贪心序），无需重估或重排。
- 冻结探针：`competition/experiments/probe_l8_budget_quality.py` → `competition/deliverables/probe_l8_budget_quality.json`（seed 固定、无时间戳字段、双跑逐字节一致；`content_sha256` 自校验配方见该 JSON）。
- 探针引擎 = 本模块 + `update_planner` + `ontology_service._k0_metrics_from_snapshot`（真实生产三件）。

## 验收锚点

- `cd nexent/backend && .venv/bin/python -m pytest ../test/backend/services/knowevo/test_budget_curve.py -v`：27 用例离线全绿（前缀准入与不复扫 / 显式评审序 / 三种顺序工具 / K0 默认与注入 / 质量拷贝不别名 / 校验异常）；
- `pytest ../test/backend/services/knowevo/ -q`：全量不降（基线 908 passed / 30 skipped + 本模块 27）；
- `backend/.venv/bin/python -m ruff check backend/services/knowevo`：0 违例；
- 冻结探针双跑逐字节一致（见上）。
