# 算法机制预验证实验（competition/experiments）

## 目录用途

本目录存放 Nexent-KnowEvo 三个创新点的**机制预验证（probe）脚本**与 **(1−1/e) 声明的子模性实证检查**：在合成数据上验证核心算法机制是否按设计工作（版本钉住遍历、人审预算 VOI 排序、受影响面倒排传播、贪心近似的子模性前提），为提交材料中的"算法预验证"小节提供可复现数字。四个脚本彼此独立、互不 import，只用 Python 3 标准库（零新依赖），各自可单独复跑。

> **适用范围：合成条件下的机制验证，非真实数据结论。**

## 怎么跑

在仓库根目录（`nexent/`）下执行：

```bash
python3 competition/experiments/probe_p1_version_pin.py    # P1 版本钉住 vs 无版本检索
python3 competition/experiments/probe_p2_voi_budget.py     # P2 固定人审预算下 VOI 排序 vs 置信度排序
python3 competition/experiments/probe_p3_impact_prop.py    # P3 受影响面两级倒排传播
python3 competition/experiments/probe_p4_submodularity.py  # P4 子模性实证：贪心/穷举最优比率 + 边际收益曲线
```

通用参数（四个脚本一致）：

- `--seed N`：随机种子，默认值即产出当前 JSON 的种子（P1=20260918，P2=7，P3=11，P4=20260919）；固定默认种子可精确复现 JSON；
- `--output PATH`：结果 JSON 落盘路径，默认写入下节所述位置。

每次运行会在 stdout 打印人读表格，并写出含 `config`（种子与全部规模参数）与 `results`（每个指标带分母 `n_ok`/`n_total` 或 `n_total`+`fraction`）的 JSON。

## 结果 JSON 落盘位置

- `competition/deliverables/algorithm-probes/probe_p1_version_pin.json`
- `competition/deliverables/algorithm-probes/probe_p2_voi_budget.json`
- `competition/deliverables/algorithm-probes/probe_p3_impact_prop.json`
- `competition/deliverables/algorithm-probes/probe_p4_submodularity.json`

## 三实验与三个创新点的对应关系

| 脚本 | 验证机制 | 创新点 | 文档锚点 |
|---|---|---|---|
| `probe_p1_version_pin.py` | 版本钉住遍历（按 `t_v` 过滤现行视图后作答）vs 无版本随机检索 / 最新版偏置检索，在版本敏感题（V 题）与稳定题（F 题）上的正确率对比 | 创新②：版本钉住的多跳推理 | 计划书 §5.1；技术方案 §3.2 |
| `probe_p2_voi_budget.py` | 固定专家确认预算下，提案按 VOI（`p_confirm × impact`）降序 vs 按置信度（`p_confirm`）降序进入确认环的净好提案数对比 | 创新③：人审预算优化（VOI） | 计划书 §3.1；技术方案 §2.1 |
| `probe_p3_impact_prop.py` | 标准文档变更后，受影响面（Impact Scope）= 变更文档 → 图谱实体 → 决策卡 的两级倒排索引查询（非在线图遍历）的规模与耗时 | 创新①：标准演进对齐器·受影响面 | 计划书 §4.2；技术方案 §4.2 |
| `probe_p4_submodularity.py` | 合成覆盖型 impact 上贪心 vs **穷举最优**的比率分布（回应 (1−1/e)≈0.63 声明）+ 真实形状双通道 impact（被引用实体数 + 证据段覆盖）的边际收益递减曲线与单调性违反计数 | 创新③/①共用的 VOI 框架性质声明（计划书 §3.1"算法性质"段） | 计划书 §3.1；技术方案 §2.1 |

## 当前快照数字（默认种子；权威数字以下述 JSON 为准）

- **P1**（200 题 × 4 版本，每臂 800 个题-版本对）：
  pinned：V 题 1.000（160/160），F 题 1.000（640/640），overall 1.000（800/800）；
  ret-random：V 题 0.206（33/160），F 题 1.000（640/640），overall 0.841（673/800）；
  ret-latest：V 题 0.250（40/160），F 题 1.000（640/640），overall 0.850（680/800）。
  结论：版本钉住使 V 题正确率从约 20–25% 升至 100%，且三臂 F 题均为 100%（不误翻稳定结论，回归护栏）。
- **P2**（提案池 120，其中 true_quality 71/120；预算 10/20/30/50）：
  净好提案差（VOI − 置信度）依次为 **+2 / +6 / +1 / +5**；budget=20 时 VOI 11 好/0 坏 vs 置信度 6 好/1 坏（均分母 n_reviewed=20）。
- **P3**（80 文档 / 2000 实体 / 3000 决策卡，8 份文档变更）：
  受影响实体 164/2000（8.20%），受影响决策卡 750/3000（25.00%），传播耗时 mean 0.022 ms（min 0.019，max 0.050，n=50 次计时）。
- **P4**（子模性检查：n=14 提案、预算 k=3、400 随机实例穷举；真实形状双通道 impact 400 实例）：
  贪心/最优比率 min 0.880 / mean 0.988 / median 1.000，400/400 实例 ≥ (1−1/e)≈0.632，311/400 实例贪心恰为穷举最优；真实形状 impact 的贪心边际增益曲线逐实例单调性违反 0/400。
  结论：合成覆盖型设定下贪心达到最优的 98.8%，支持工程使用；真实函数的子模性声明保持"近似/经验成立"的诚实措辞。

## 数字纪律

1. **一切对外引用以 `competition/deliverables/algorithm-probes/` 下四个 JSON 为准**；本 README 的快照仅便于速览，可能滞后于复跑结果。
2. 所有正确率/占比指标在 JSON 中带分母（`n_ok`/`n_total` 或 `n_total`+`fraction`）；引用时必须连同分母，禁止只报百分比。
3. 复跑时固定默认 `--seed` 可精确重现 JSON 数字；更换种子允许个位数差异。
4. **P2 诚实性说明**：该合成池中 `true_quality` 与两个排序分数相互独立，故无权重净好差的期望为 0，单次试验的净差属抽样噪声。JSON 中 `net_good_gain_seed_sensitivity` 附录给出 20 个额外种子下的净差噪声带（min/mean/max 与正例占比）；引用 headline 数字时应一并说明该噪声带，不得将单次净差表述为确定性收益。
5. P3 的传播耗时为机器相关量，JSON 中记录计时方法与重复次数（n_timed_runs），跨机器比较应引用量级而非精确值。

## 适用范围声明

本目录四个实验均为合成数据上的机制验证，用于证明算法链路按设计工作（钉住谓词生效、排序决定进入预算窗口的提案构成、倒排索引两步查询即可得受影响面、覆盖型前提下贪心接近穷举最优），**不构成真实医疗数据上的效果结论**。

> 适用范围：合成条件下的机制验证，非真实数据结论。
