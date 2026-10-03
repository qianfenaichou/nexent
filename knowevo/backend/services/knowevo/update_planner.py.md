# update_planner.py —— L7 演进闭环内核：VOI 最小充分更新集
**新建**: 2026-09-29（L7 内核）· 依据: 最小充分更新集（VOI 框架）设计，详见正文职责节

## 职责

演进闭环的算法内核：对一轮变更的受影响面（本体提案 P_aff ∪ 决策卡 D_aff），选**最小**人工确认/重算集合 U，使「未纳入 U 的项保持旧结论」的期望质量损失 ≤ ε。纯函数、stdlib-only、零 DB、零 LLM。与 L8 人审预算曲线共用同一 VOI 框架（一次设计两处空白 E1+A2）：本模块的 `per_item_voi` 完整暴露每项 VOI（含未选）供 L8 直接复用。数据装配（ΔS/E_aff/D_aff/P_aff → `UpdateCandidate`，含每个 p_change 的估计）归调用方；**生产接线不在本模块**（属后续接线；与 asset_search 同款诚实分层——本内核与 `evolution_service.py` 无任何 import 关系，接线前不影响任何现有行为）。

## 接口冻结

```python
CANDIDATE_KINDS = ("proposal", "decision_card")   # 模块常量；stop_reason 三值见 UpdatePlan 注释

@dataclass(frozen=True)
class UpdateCandidate:
    id: str
    kind: str                 # CANDIDATE_KINDS 之一
    p_change: float           # 0–1：结论翻转概率——调用方估计（变更重叠/对齐冲突信号），本模块不估
    impact: int               # ≥0：结论被引用次数
    cost: float               # >0：确认/重算成本（与 total_rebuild_cost 同单位即可）

    def __post_init__(self) -> None: ...

@dataclass(frozen=True)
class UpdatePlan:
    selected: list[UpdateCandidate]       # U，贪心（VOI 降序）序
    skipped: list[UpdateCandidate]        # 同一确定性序
    per_item_voi: dict[str, float]        # 每项 VOI（含未选），键序=贪心序——L8 复用点
    residual_expected_loss: float         # 未选集 Σ VOI（运行差实现）；ε 停时 ≤ epsilon
    cost_selected: float                  # U 的成本
    cost_total: float                     # 全选上界成本
    cost_saving_rate: float | None        # 仅给 total_rebuild_cost 时 = 1 − cost_selected/total_rebuild_cost
    quality_retention_rate: float         # 1 − residual/Σ全量VOI；ΣVOI=0 时定义为 1.0
    stop_reason: str                      # epsilon_satisfied | marginal_benefit_below_cost | exhausted

def plan_minimal_update(
    candidates: list[UpdateCandidate],
    epsilon: float,
    *,
    total_rebuild_cost: float | None = None,
) -> UpdatePlan: ...
```

## 语义（冻结）

**VOI 与排序**：`VOI_i = p_change_i × impact_i`；按 VOI 降序遍历，破平键 `(kind, id)` 字典序升序。输入顺序无关：同一候选集任意排列 → 完全相等的 plan（测试钉死）。选择条件为 `voi ≥ cost`（**等号选入**，规格「直到边际收益 < 成本」的严格小于）。

**两条停止规则（取先触发者；同一边界皆触发时 ε 规则优先——目标条款是主目标，成本规则是效率启发）**：

1. **ε 目标条款**（规格目标）：在审视下一候选**之前**，若「该候选及其后全部不选」的残差 ΣVOI 已满足 `residual ≤ epsilon` → 停（`stop_reason="epsilon_satisfied"`）——再多选就违反最小性；ε ≥ Σ全量VOI 时 U = ∅（此时 retention = 0.0，诚实反映「什么都没保住」）。
2. **边际收益条款**（规格方法，逐字）：首个 `voi_i < cost_i` 的候选即停（`stop_reason="marginal_benefit_below_cost"`）；**即停不复扫**——其后各项即使自身 `voi ≥ cost` 也不选（复扫是另一个预算最大化算法，规格明确不要；冻结探针 D4 项钉死该语义）。
3. 全部候选被消费 → `stop_reason="exhausted"`（残差 0）。

`epsilon` 可为任意 float：负数/NaN 使规则 1 永不触发（残差恒 ≥ 0），贪心退化为纯成本规则。

**退化语义**：Σ全量VOI = 0（无人会翻转）→ `quality_retention_rate` 定义为 **1.0**（无所可失，不得写 0 占位）；空输入 → 空计划、`residual_expected_loss=0.0`、`stop_reason="exhausted"`、retention=1.0、给了 `total_rebuild_cost` 时 saving=1.0。

**校验（ValueError，校验先行）**：候选域越界（kind 非法 / p_change ∉ [0,1] 含 NaN / impact<0 / cost≤0）在 `UpdateCandidate.__post_init__` 构造即抛（fail-fast 边界）；`plan_minimal_update` 层校验 **id 重复**（`per_item_voi` 按 id 键控，重复会静默塌缩映射）与 **total_rebuild_cost ≤ 0**。

**确定性**：排序键全序 + 浮点运算单一固定顺序 → 同输入（任意排列）同输出逐位一致；`per_item_voi` / `skipped` 均按贪心序。

## 成本对照（规格附带的形式化）

`成本节省率 = 1 − cost_selected / total_rebuild_cost`（增量演进 vs 全量重建，同单位：人时或 token）；`质量保持率 = 1 − residual/Σ全量VOI`。两者成对报告，直接回应「为什么不每次重建」。`total_rebuild_cost` 不给则 saving 为 None——**没有全量重建基准就不宣称节省率**，不发明分母。

## 与 L8 的共用点

`per_item_voi` 对**全部**候选（含 skipped）暴露 VOI 且键序即贪心序 → L8 人审预算曲线按该映射取预算窗即可，无需重新估计或重排。`competition/experiments/probe_p2_voi_budget.py`（VOI 序 vs 置信序评审对照）是同一 VOI 框架在 L8/A2 侧的既有实证。

## 诚实边界

- 本模块**不估计任何概率**：p_change 由调用方从变更重叠/对齐冲突等信号估出，impact 由调用方从引用计数取得；本模块只做规划。
- cost 单位任意但全表须一致（人时或 token 二选一），`total_rebuild_cost` 同单位；跨单位混算是调用方错误，本模块不折算。
- 生产调用点接线属后续工作；本契约不冻结任何调用点。

## 验收锚点

- `pytest test/backend/services/knowevo/test_update_planner.py -v`：28 用例离线全绿（贪心序与等号选入 / ε 提前停与空计划 / 边际停即停不复扫 / 两种 stop_reason 区分 / (kind,id) 破平 / 输入序无关确定性 / 空输入与 ΣVOI=0 退化 / 校验异常 / cost_saving_rate 数学 / per_item_voi 全暴露）；
- 冻结探针：`python3 competition/experiments/probe_voi_minimal_update.py` → `competition/deliverables/algorithm-probes/probe-voi-minimal-update.json`（`content_sha256` 自校验配方见该 JSON；无时间戳字段，双跑逐字节一致）；
- 全量回归收据：`competition/deliverables/pytest-2026-09-29-r3-voi.txt`。
