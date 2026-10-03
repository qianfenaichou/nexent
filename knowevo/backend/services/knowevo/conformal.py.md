# conformal.py —— L3 单侧 split-conformal 自适应接收线内核
**新建**: 2026-09-28（L3 conformal；2026-09-30 复核轮补契约）
**依据**: 自适应接收线设计评审（详见正文职责节）；方法源见下行
**方法源**: Vovk et al., *Algorithmic Learning in a Random World*（split/conformal prediction）；stdlib-only，**零新增依赖**（`math` / `collections.abc`）

## 职责

把「固定接收线 `KW_AUTO_ACCEPT_LINE`（0.85，拍脑袋值）」替换为**可校准的分位数线** τ：以人工 **REJECTED** 提案的置信度为 bad 类校准集，求单侧 split-conformal 阈值。纯函数、零 DB、零 LLM。DB 读取与 meta 组装归消费方 `ontology_service.resolve_auto_accept_line`（见「消费面」）。

## 接口冻结

```python
DEFAULT_ALPHA = 0.05

def conformal_accept_line(bad_scores: Iterable[float],
                          alpha: float = DEFAULT_ALPHA) -> float | None: ...
```

## 语义（冻结）

**保证陈述**：对与校准集可交换的新 bad 提案，

    P(其 score > τ) <= alpha

即至多 `alpha` 的 rejected 类提案会越线（per-bad-proposal false-accept rate ≤ alpha；auto-accepted 里 bad 的占比还取决于 base rate）。**边际、分布无关**——只需可交换性，不需分数分布。接受条件是 **strict `>`**（ties 只在 `>` 下保持保守）。

**返回值**：

- 校准集非空且 `k = ceil((n+1)*(1-alpha)) <= n` → 返回升序排序后第 `k` 小的分数 `scores[k-1]`；
- `k > n`（有限线撑不住保证；alpha=0.05 时 **n < 19**）或校准集为空 → **返回 `None`**，调用方必须回退固定线——在这里返回数字就是伪造保证；
- `alpha` 域校验：必须 `0.0 < alpha < 1.0`，否则 `ValueError`（先于一切其它逻辑）。

**分数语义**：`bad_scores` 是 rejected 类提案的 confidence（越高 = 门越可能误放）；`float(s)` 宽松转换（int / 可转 float 的 str），不可转则异常自然上抛。排序升序、取第 k 小，确定性。

## 消费面（`ontology_service.resolve_auto_accept_line`）

唯一已知消费者：`ontology_service.resolve_auto_accept_line(tenant_id, alpha=DEFAULT_ALPHA) -> tuple[float, dict]`（`ontology_service.py.md` 2026-09-28 L3 增量回填；实现 `ontology_service.py:630`）：

1. 从 `ontology_change_proposal_t` 读本租户 `status='rejected'` 的非空 `confidence` 作 bad 类；
2. 调 `conformal_accept_line(bad, alpha=alpha)`；
3. `None` → 回退固定线 `AUTO_ACCEPT_LINE`（= `KW_AUTO_ACCEPT_LINE`），meta `method="fixed_fallback"` + `reason`；
4. 有线 → meta `method="conformal"` + `n_calibration`（**usable 非空分数计数，不是 rejected 行数**）+ `alpha` + guarantee 文案。

与 `kg_service.calibrate_thresholds`（实体合并的两类 ROC 扫描）是**不同问题**，刻意分开。

## 诚实边界

- **接线债（在此记录，本契约不改 `auto_accept` 行为）**：保证以 **strict `>`** 陈述；`ontology_service.auto_accept` 现用 `confidence >= threshold`（`ontology_service.py:621`）。校准线一旦直接喂给现有 `auto_accept`，**tie 会使保证偏乐观**。`resolve_auto_accept_line` docstring 自陈「adjust it before ever feeding this line through」——这是**已知未修的接线债**，不是本模块内核缺陷。修法二选一（接线时处理）：`auto_accept` 改 strict `>`，或加 strict 模式。**本契约只记录此债，不改 `auto_accept`**（那是接线债）。
- 校准类太小诚实返回 `None`（不装精确）；alpha=0.05 时 n<19 无有限线。
- 边际保证：不承诺 auto-accepted 集合里 bad 的占比（还取决于 base rate）；不承诺 good 类通过率。
- 可交换性是前提，不是结论——校准集分布漂移时保证失效，本模块无漂移检测。
- 本模块不读库、不改 `AUTO_ACCEPT_LINE` / `KW_AUTO_ACCEPT_LINE`、不动 `auto_accept` 门。

## 验收锚点

- `pytest test/backend/services/knowevo/test_conformal_accept.py -v`：12 用例离线全绿（分位数手算 / n=18 None vs n=19 有线 / 空集 None / alpha 越界 ValueError / 宽松 float 转换 / 蒙特卡洛越线率 ≤ alpha+tol / `resolve_auto_accept_line` 消费面 conformal vs fixed_fallback 双路径 + meta 形状）；
- 全量回归：`pytest ../test/backend/services/knowevo/ -q` 基线不降 + `ruff check backend/services/knowevo` 0 违例；
