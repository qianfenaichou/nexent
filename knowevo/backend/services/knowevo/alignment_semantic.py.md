# alignment_semantic.py —— K5 语义聚类对齐器（idf 余弦 + 最优匹配 + 置换检验 + BH-FDR）
**归属任务**: 2026-09-23 算法优化轮（session B · work order §3.2 / review-feedback §5.3 C-1..C-4）· 依赖: alignment_service（复用 `topic_tokens` / `normalize_title` / `assign`）
**依据**: [02-技术方案.md](../../../../../02-技术方案.md) · [verification-reports/t21-alignment-caliber.md](../../../../../nexent/competition/docs/verification-reports/t21-alignment-caliber.md)（topic 级 caliber B）

> **契约偏差说明（2026-09-24 补立）**：本模块是 2026-09-23 新模块（566 行语义聚类对齐器），此前双份契约树（根 `knowevo/` 与仓内副本 `nexent/knowevo/`）均无它的 `*.py.md`。本文件与 `nexent/knowevo/...` 副本**同步立约**，内容一致；相对链接按层级调整（根副本 4 级 / 仓内副本 5 级），与 `graph_store.py.md` 先例一致。契约是冻结面：只准新写，不准改其他契约。

## 职责
替换 T-21 旧口径 `alignment_service.calibrate_topic` 的「共享可判别 token 数 ≥ `min_shared_tokens`」硬阈值匹配。旧口径在真实 691 项跑分上坐在悬崖上：recall 在 `min_shared_tokens=2→3` 时从 9/9 塌到 3/9（结构性天花板 `9/691≈1.3%`，单位错配：段落项 ≠ 话题，不可作质量度量）。本模块用**连续相似度 + 最优二分匹配 + 统计显著性 + 多重检验校正**重做对齐，消除阈值敏感性，并给出**可显式计数的假阳**（旧 `precision_lower_bound` 结构上无法给出）。

五步法：
1. **来源卫生（provenance hygiene）**——组 token 永远取自 `section_anchor`，仅当 `points` 条目的 `source == "llm"`（真实散文）时才取 `points`。组 key 不变（与 T-21 的 517 组可比）。
2. **连续相似度**——`idf_weights`（平滑逆文档频率）加权余弦替换「共享 token 数 ≥ k」阶跃函数，无悬崖可掉。
3. **最优分配**——复用 `alignment_service.assign`（纯 stdlib Hungarian）做最大权二分匹配，每话题设 `capacity` 配额，避免单话题无限膨胀。
4. **Monte-Carlo 显著性 + Benjamini-Hochberg FDR**——每个匹配对得置换 p 值；BH 控制期望假发现比例，从而给出**显式假阳计数**。
5. **阴性对照臂**——同管线跑域外/域内诱饵话题，把点估计变成**特异性受控**量。

## 接口冻结

> 零新增依赖纪律：本模块仅用标准库（`math` / `random` / `dataclasses` / `typing`），并 intra-package 复用 `alignment_service.{topic_tokens, normalize_title, assign}`。**不引入 numpy / scipy / networkx / sklearn**——`pyproject.toml` 是受保护接线文件，本模块 Promise「zero new dependencies: stdlib only」。

```python
# ── 数据契约（dataclass）─────────────────────────────
@dataclass
class Group:
    change_type: str
    section_anchor: str
    count: int = 0
    tokens: set[str] = field(default_factory=set)

@dataclass
class DecoyArm:
    n_decoy_topics: int
    declared_groups: int
    declared_group_keys: list[str]
    pairs_tested: int
    degenerate: bool          # True = 臂从未产生可测对（无检验力，其 0 不得读作"无假阳"）

@dataclass
class SemanticCalibration:
    recall: float | None
    matched_topics: int
    gold_total: int
    declared_groups: int
    machine_groups: int
    alignment_precision_pooled: float | None
    false_positives: int
    wilson_low: float | None
    wilson_high: float | None
    false_positives_easy: int
    precision_identifiable: bool     # 真实语料上恒为 False
    precision_caveat: str
    hard_negative_slot_rate: float | None
    capacity: int
    top_k: int
    fdr_q: float
    n_perm: int
    seed: int
    pairs_tested: int
    pairs_rejected: int
    legacy_params_accepted_but_inert: dict[str, Any]
    topic_groups: dict[str, list[str]]

# ── 公开 API ────────────────────────────────────────
def build_groups(machine: Sequence[dict]) -> list[Group]:
    """折叠 change items 成组，**剔除结构性占位**（UNCHANGED 跳过）。
    组 key 与 alignment_service.aggregate_change_groups 完全一致
    (change_type, normalize_title(section_anchor))，组数可比 T-21（517）。
    唯一区别在 token 集：anchor 无条件取，points 仅当 content-bearing 才取。"""

def idf_weights(groups: Sequence[Group]) -> dict[str, float]:
    """idf(t) = ln((N+1)/(df(t)+1)) + 1。平滑，不删除高频 token——
    一个处处出现的 token 权重≈1、白贡献，而非被删掉带走真邻居。"""

def weighted_cosine(a: set[str], b: set[str], idf: dict[str, float],
                    norm_a: float | None = None,
                    norm_b: float | None = None) -> float:
    """idf 加权余弦，[0,1]，连续无阈值。"""

def benjamini_hochberg(pvalues: Sequence[float], q: float) -> list[bool]:
    """BH step-up；q 必须在 (0,1)。返回 per-index reject/keep 标记。"""

def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """Wilson 95% 默认；n==0 返回 (None, None)。"""

def semantic_calibrate(
    machine: Sequence[dict], gold: Sequence[dict], *,
    capacity: int = 3, top_k: int = 15, fdr_q: float = 0.10,
    n_perm: int = 1000, seed: int = 20260923,
    decoy_topics: Sequence[str] = (), hard_negative_topics: Sequence[str] = (),
    max_df_fraction: float | None = None,   # 接受为 legacy 兼容，故意 inert
    min_shared_tokens: int | None = None,   # 接受为 legacy 兼容，故意 inert
) -> tuple[SemanticCalibration, DecoyArm, DecoyArm]:
    """语义校准对齐 + FDR 受控假阳。
    返回 (真实臂结果, 易臂 DecoyArm, 硬臂 DecoyArm)。
    `false_positives` / `alignment_precision_pooled` 仅对**硬**臂计
    （硬臂才有检验力开火）；易臂计数单独报 `false_positives_easy`，
    绝不可当作 precision 基准。"""
```

## 语义约束（硬纪律）

1. **precision 轴不可辨识（estimand 纪律）**：`precision_identifiable` 在真实语料上**恒为 `False`**。`alignment_precision_pooled` 是**特异性代理量**（分母=本次声明组、假阳由阴性臂实测），**不是** T-21 的 `64/517`（相关率，分母=全体机器组、无假阳计数）。两者 estimand 不同，**绝不可并列当作同一件事**。根因：金标**非穷尽**（9 verified + 7 unverified），无可构造的域内阴性集——任何被当作「未变更」的章节都可能是金标未列入的真实变更，对照臂声明无法裁定是假阳还是「真实但漏列」。故该值只可用于同口径相对比较，**不得对外当 precision 宣称**。
2. **组 key 冻结**：`build_groups` 的 key 必须与 T-21 `aggregate_change_groups` 一致，否则 517 组可比性断裂。
3. **零依赖 Promise**：`pyproject.toml` 受保护。任何新增第三方依赖必须走接线任务，**不得在此模块静默引入**。
4. **legacy 参数 inert**：`max_df_fraction` / `min_shared_tokens` 仅为 grid 兼容接受，故意不参与计算（模块意义正在于这两个参数不再 load-bearing）。
5. **易臂退化保护**：`DecoyArm.degenerate` 为 `True` 时其 `pairs_tested==0`、**无检验力**，其 0 **不得**读作「无假阳证据」。

## 实测口径（probe_p8，冻结于 deliverables/algorithm-probes/probe_p8_alignment_semantic.json）

> 适用范围：对既有真实产物（`alignment-diff.json` + `guideline_diff_seed.md` 的 9 个 eligible 话题）的**回溯核算**，零 LLM / 零 DB / 零网络；不是新一次抽取跑分。

- **recall = 9/9（保持）**：eligible 金标话题 9，全命中。
- **声明组 = 26 / 517**。
- **`alignment_precision_pooled` = 1.000000**，Wilson 95% **[0.8713, 1.0000]**；⚠️ **`precision_identifiable = false`**（同上语义约束①）。
- **阈值敏感性消除**：同一条 legacy 10 点网格（5×`max_df_fraction` × 2×`min_shared_tokens`）下，legacy recall 跨度 **2/9–9/9**（差=7），新方法跨度 **9/9–9/9（差=0）**；新方法自身旋钮（`top_k` 5–40 / `fdr_q` 0.01–0.5 / `capacity` 1–3）recall 亦恒 9/9（`capacity=5` 时 declared=42、FP=2、pooled=0.952）。
- **阴性对照**：易臂（域外 14 题）`pairs_tested=0`、`degenerate=True`、**无检验力**（其 0 不可作特异性证据）；硬臂（域内 12 题）`slot-fill 0.694`、与真臂声明组交集 **0**。
- **口径对照**：旧 `64/517` 的 Wilson 为 [0.0981, 0.1550]，**与本次不可直接比较**（相关率 vs 特异性代理，estimand 不同）。

## 验收锚点
- `pytest test/backend/services/knowevo/test_alignment_semantic.py -v`：组 key 与 T-21 一致（`aggregate_change_groups` 同款键函数）、`weighted_cosine` 在正交集上=0、BH 在 q 越界抛 `ValueError`、`wilson_interval(0,0)` 返回 `(None,None)`、`semantic_calibrate` 对 fixture 返回 `precision_identifiable=False`。
- `python competition/experiments/probe_p8_alignment_semantic.py`：recall 9/9、`declared_groups=26`、`precision_identifiable=false`、legacy/semantic 敏感度跨度差=7/0（与 `probe_p8_alignment_semantic.json` 逐字段一致）。
- 回归：后端 knowevo 层 **721 passed / 30 skipped**（session B 基线，见 cost-ledger `sessionB-baseline-20260923`）；官方 ruff 范围 `All checks passed!`。
