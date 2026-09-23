# 会话 B 闭环：真实 PostgreSQL 上的写入路径与索引下推实测

> 日期：2026-09-24 01:30–02:00 · 工作区：`/home/qianqian/Work/All/Nexent` · 执行者：主会话（含 3 个子 agent 的并行侦察与独立复核）
> 上游：`competition/docs/sessionB-receipt-五核前三项-2026-09-23.md` §5.4 / §6.6 的「未实测项」
> **本报告的每个数字都有产物可回溯**（`/home/qianqian/bench-runs/*.json` 与 `*.txt`、PG 语句日志）。凡未测的，明确写「未验证」。

---

## 0. 环境（本次首次真正具备）

| 项 | 值 |
|---|---|
| PostgreSQL | **16.15**（conda-forge 构建，自包含于 `/home/qianqian/pg16`） |
| 监听 | `127.0.0.1:5434`，库 `nexent`，trust 认证 |
| 数据目录 | `/home/qianqian/pgdata` |
| 关键 GUC | `max_stack_depth=2MB`、`shared_buffers=128MB`、`work_mem=4MB`（均为默认） |
| 启动方式 | **user namespace**（`unshare --user --map-user=65534 --map-group=65534`）。原因：PG 拒绝以 root 启动，而本机沙箱拦截 `setuid/setgid`，无法常规降权；userns 内 PG 看到自己是非 root，且仍可访问 home。 |
| schema | `v2.5.5_kw_001_knowevo_core.sql`（12 表）+ `v2.5.5_kw_011_knowevo_graph_indexes.sql`（4 索引 + 2 CHECK） |
| 环境脚本 | `/home/qianqian/pg-env.sh`（导出 `$KW_PG_DSN` 与 `POSTGRES_*`，含 `pg_start`/`pg` 辅助函数） |

> ⚠️ 与 receipt §0 的前提相反：**PG 本次可用**。dockerd 仍不可用（无 socket），ES 未尝试。

---

## 1. 🔴 最重要发现：会话 B 交付的批量写入路径在真实 PG 上**跑不通**

任务要求的第一条就是「`bench_write_path.py --entities 20000 --edges 30000`」。**首次执行直接失败**：

```text
Database operation failed: (psycopg2.errors.StatementTooComplex) stack depth limit exceeded
HINT:  Increase the configuration parameter "max_stack_depth" (currently 2048kB) ...
[SQL: SELECT ... FROM nexent.kg_relation_t
  WHERE tenant_id = %(...)s AND valid_at <= now() AND (...) AND
        (src, dst, rel_type) IN ((...), (...), ... ~29863 个元组 ...)]
```

**根因**：`upsert_relations` 的「当前视图存在性查找」把**全部去重候选键**塞进**一条** `IN`；实体侧 `upsert_entities` 同样未分块（2 万键侥幸通过，3 万键必崩）。PostgreSQL 解析器对 `IN` 列表**逐元素递归**，超过 `max_stack_depth` 即整条写入失败。

**最小复现与阈值**（`/home/qianqian/probe_in_clause_limit.py`，与建表无关的纯语句）：

| 单条 `(a,b,c) IN (...)` 的元组数 | 结果 |
|---|---|
| 1000 / 2000 / 4000 / **8000** | OK |
| **12000** / 16000 / 20000 / 24000 / 28000 / 30000 | `StatementTooComplex: stack depth limit exceeded` |

→ **崩溃阈值在 8000 与 12000 之间**（`max_stack_depth=2MB`）。默认 `K=1000` 有 8 倍安全余量。

**修复**（本会话新增，`backend/services/knowevo/graph_store.py`）：两处存在性查找均**按 K 分块**。查找全部发生在「决定 insert/update」的循环**之前**，故分块不改变语义（顺序、同名覆盖、批内工作副本、`valid_now` 的 `now()` 在同一事务内恒定，逐条核对见 §5 独立复核）。

**修复后**：同一条命令 **2.648s / 2.141s / 2.083s 完成**（3 次独立进程）。

---

## 2. 写路径实测（工单 §7 · 3.1 判定项）

**口径**：A（改后，工作区批量版）与 B（改前，`git HEAD` 逐行版）**交替**执行（A,B,A,B,A,B），消除表增长混淆；**每次计时前 TRUNCATE**，故每次都是「空表纯 INSERT」；同一进程、同一数据集、同一环境。改前版本用 importlib 从 `git show HEAD:backend/services/knowevo/graph_store.py` 动态加载，**非手写冒充**。

### 2.1 含建索引时的对比（20 000 实体 + 30 000 边）

| 条件 | 逐次耗时 (s) | 均值 (s) |
|---|---|---|
| **改前**（HEAD 逐行） | 148.218 / 118.018 / 105.944 | **124.060** |
| **改后**（批量 K=1000） | 2.648 / 2.141 / 2.083 | **2.291** |

**→ 提升 `54.15×`；改后均值 2.291s，远低于工单「≤10s」门槛。**

> **口径更正**：评审反馈 §5.5 的 `79.4s + 120.2s = 199.6s` **在本环境不可复现**。本环境的同环境改前基线是 **124.06s**（首跑 148.2s，末跑 105.9s，逐次下降，离散度约 40%）。**这正是 receipt §5.4 坚持「改前必须在同环境重测」的理由 —— 该要求本次被满足。**

### 2.2 含 kw_011 四个索引时的附加写入成本

| 条件 | 逐次 (s) | 均值 (s) |
|---|---|---|
| 不含四个新索引 | 2.648 / 2.141 / 2.083 | 2.291 |
| **含四个新索引** | 2.217 / 2.258 / 2.291 | **2.255** |

**Δ = −0.036s / 次（−1.6%），落在逐次离散度之内 → 本规模下四个索引对写入的附加成本不可测。**
（与 `kw_011` 头部「索引会让写变慢」的诚实提示不矛盾：该提示是对机制的一般描述，本规模下未被本次测量分辨出来。）

### 2.3 同进程 repeats=3（首次插入 + 后续合并/更新分支）

`D_batched_noidx_repeats3` 逐次 = **[2.026, 12.204, 2.835] s**（均值 5.688s）。
第 2 次显著偏慢（12.2s）。**未定位根因**（可能与检查点/刷盘时机有关），**故不把它当作稳定数字使用**，仅登记。**未重复验证**。

---

## 3. 批量拐点扫描：`--batch-size` 曾是**空操作**

### 3.1 第一次扫描（平坦到可疑）

K = 100 / 500 / 1000 / 2000 / 5000 / 8000 / 20000 → `2.443 / 2.457 / 2.489 / 2.351 / 2.412 / 2.415 / 2.416` s，**K 变化 200 倍，耗时变化 <6%**，且 K=20000 未报参数上限错误。

**根因（两处，均已修/查清）**：

1. **`--batch-size` 从未传到 store**。`PgJsonbGraphStore` 读 `getattr(self, "batch_size", DEFAULT_GRAPH_BATCH_SIZE)`，而全仓库**从未给 `self.batch_size` 赋值** → 恒为 1000。**第一次「拐点扫描」实际是同一配置跑了 7 次。** 已修：`bench_write_path.py` 的 `_timed_writes()` 现在显式 `store.batch_size = batch_size`。
2. **驱动层把单条多值 INSERT 封顶在 1000 行**（PG 语句日志实测，见 §3.3）。

### 3.2 修正后的扫描（K 真正生效）

| K | 100 | 500 | 1000 | 2000 | 4000 | 8000 |
|---|---|---|---|---|---|---|
| 耗时 (s) | 3.253 | 2.647 | 2.729 | 2.698 | 2.975 | 2.759 |

**仍然平坦（80 倍 K 变化 → <12% 波动）**。结论：**往返次数不是本规模的瓶颈**，瓶颈在逐行工作量（UUID 生成 / JSONB 编码 / 索引维护）与提交。默认 `K=1000` 落在甜点区。

### 3.3 真实下发的语句（服务端语句日志地面真相）

`log_min_duration_statement=0` + `logging_collector`，直接数日志行：

| 场景 | 实际 `INSERT` 语句数 | 按 K 应为 |
|---|---|---|
| 3000 实体，K=100 | **30** | 30 ✓ |
| 3000 实体，K=1500 | **4** | 2 ✗（驱动拆成 1000 行/条） |
| 20000 实体 + 30000 边，K=1000 | 各 `ceil(N/K)` | ✓ |

- **批量是真的**：语句是**多值 INSERT**（200 行/K=100 → 2 条，而非 200 条单行）。
- 日志显示的 INSERT **值是内联字面量**而非绑定参数 → **不存在 65535 绑定参数上限问题**。
- 机制结论：`K ≤ 1000` 时语句数 = `ceil(N/K)`；`K > 1000` 时语句数 = `ceil(N/1000)`（驱动封顶）。
- **安全上限**：`K ≤ ~8000`。实测 `K=20000` 时关系侧查找单条 `IN` 带 20000 个元组 → **立刻复现 `stack depth limit exceeded`**。故 receipt §5.4 建议的 `K=20000` 扫描点**不可用**。

---

## 4. 索引下推实测（工单 §7 · 3.3 判定项）

### 4.1 前置：预检（receipt §6.6 ①）

数据集：30 000 关系 + 20 000 实体。

| 检查 | 结果 |
|---|---|
| 空库预检 | `kg_relation_t=0`, `kg_entity_t=0` ✓ |
| 灌入 2 万/3 万后预检 | `0` / `0` ✓ |

**阳性对照（证明该检查真有牙齿）**——故意插入一行 `valid_at='2024-06-01'`、`invalid_at='2024-01-01'` 的退化行：

```text
预检计数：1                              ← 检查确实能抓到
布尔谓词对该行：0 行命中                 ← 静默不匹配
区间形式对该行：ERROR: range lower bound must be less than or equal to range upper bound
```

**→ receipt §6.4 那个此前只能「论证」的安全前提，本次被**实证**：退化行下布尔谓词静默返回 false，区间形式**直接抛错**。故预检必须为 0，这不是形式主义。**（对照行已删除，删除后预检回到 0。）**

### 4.2 谓词等价性（结果集层面，实测）

同一 `as_of` 下对 30 000 行做对称差：

| as_of | 区间形式 | 布尔形式 | 区间−布尔 | 布尔−区间 |
|---|---|---|---|---|
| `now()`（29713 行已过期） | 287 | 287 | **0** | **0** |
| `now() - 3900d` | 957 | 957 | **0** | **0** |

**→ 两种谓词逐行等价，已在真实数据上实测（receipt §6.3b 此前只证明了表达式文本匹配）。**

### 4.3 EXPLAIN 对照（receipt §6.6 ③④）

数据形状：30 000 关系，**29 713 已过期 / 287 当前**（长期知识图谱的常态）。完整原文见 `/home/qianqian/bench-runs/explain_mostly_expired.txt`。

| 变体 | 计划 | Index Cond | Buffers (shared hit) | Execution |
|---|---|---|---|---|
| **区间谓词 @ `now()`** | **`Bitmap Index Scan on ix_kr_valid_range`** ✅ | **完整区间包含条件** | **521** | **2.014 ms** |
| 布尔谓词 @ `now()` | **`Seq Scan`** | —（Filter 剔除 29713 行） | **1654** | 2.400 ms |
| 区间谓词 @ `now()`（`enable_seqscan=off`） | `Bitmap Index Scan on ix_kr_valid_range` | 完整区间条件 | 521 | 1.981 ms |
| 布尔谓词 @ `now()`（`enable_seqscan=off`） | `BitmapOr` → 两个 `Bitmap Index Scan on ix_kr_valid` | `(valid_at<=now() AND invalid_at IS NULL)` 与 `(valid_at<=now() AND invalid_at>now())` | 760 | 0.885 ms |
| 实体表 区间谓词 @ `now()` | **`Bitmap Index Scan on ix_ke_valid_range`** ✅ | 完整区间条件 | 1105 | 4.005 ms |
| 实体表 布尔谓词 @ `now()` | `Seq Scan` | — | 909 | 3.039 ms |

**判定（正面，工单要的那一条）**：`ix_kr_valid_range` 与 `ix_ke_valid_range` **确实被规划器选中，且区间条件整体进入 `Index Cond`**。`probe_p1` 的「表达式逐字匹配」至此升级为「规划器实际采用」。

**判定（反向，必须如实记录）**：
1. **receipt §6.2 的论证过强。** 原文称 b-tree「无法把第二个合取项作为索引条件 → 退化为全索引/顺序扫描」。实测：一旦禁用 seq scan，PG 会把 `A AND (B OR C)` 重写为 **`BitmapOr`，把两个析取项都推进 `ix_kr_valid` 的索引条件**（`(valid_at<=now() AND invalid_at IS NULL)` / `(valid_at<=now() AND invalid_at>now())`），760 buffers / 0.885ms —— **并非「用不上」**。默认规划器之所以走 Seq Scan，是因为 287/30000 ≈ 1% 的选择率下顺序扫描更便宜，**不是**因为 b-tree 结构上不可用。
2. **收益是场景相关的、非单调。** 在「事实多数有效 + 高选择性 as_of」的数据形状下（`now()-3900d`，957/30000），区间形式走 GiST 是 623 buffers / 1.035ms，而布尔形式走 `ix_kr_valid` 前缀只要 462 buffers / 0.332ms —— **区间形式反而更慢**，因为它要为每行构造 `tstzrange`。
3. 因此**不能把「走索引」等同于「更快」**。

### 4.4 多跳 p95（receipt §6.6 ⑤；契约 P1 锚点 = p95 < 1.5s @ 2 万实体/3 万边）

| 模式 | n | p50 | **p95** | mean |
|---|---|---|---|---|
| `use_range_predicate = False`（现状） | 50 | 53.69 ms | **57.08 ms** | 53.75 ms |
| `use_range_predicate = True`（翻开关） | 50 | 63.93 ms | **70.13 ms** | 64.02 ms |

**Δp95 = +13.05 ms，`×0.814`（即翻开关后多跳 p95 恶化 23%）。** 两臂分布基本不重叠（off ∈ 47.8–71.2ms，on ∈ 56.7–71.8ms），**退化是真实的，不是噪声**。

两臂都远低于契约 P1 的 1.5s 锚点，但**方向与「下推应提速」的预期相反**。

### 4.5 结论：**不翻 `use_range_predicate`**

工单 §7 对 3.3 的达标条件是**三项同时成立**：`probe_p1` 不掉 **且** p95 改善 **且** `EXPLAIN` 走索引。

| 子条件 | 实测 | 判定 |
|---|---|---|
| `probe_p1` 不掉 | 逐位一致 | ✅ |
| `EXPLAIN` 走 `ix_kr_valid_range` / `ix_ke_valid_range` | 被选中，条件整体进 `Index Cond` | ✅ |
| **p95 改善** | **恶化 23%** | ❌ |

**→ 3.3 判定为「机制已验证、收益为负」，保持 `use_range_predicate = False`（原默认）。**
依 AGENTS.md §0「没有证据=没做完」与 receipt §6.4「不留半成品行为」的纪律，**在收益为负时不翻开关**是对的；翻开关会使「p95 改善」这一条明确不成立。

> 若只关心「事实大量过期 + `as_of=now()`」的单条查询（§4.3 第一行：省 68% buffer），翻开关是有利的；但代价是多跳 p95 恶化 23%。**该取舍留给决策者，本报告不代做**。
> 另：`valid_range_contains()` 与开关**保留在代码里**（默认关），其价值已被 §4.3 证明。

---

## 5. 回归（本会话改动之后）

| 项 | 结果 | 与改前基线 |
|---|---|---|
| `pytest ../test/backend/services/knowevo/ -q` | **721 passed, 30 skipped, 1 warning in 49.32s** | **逐位一致**（基线 721/30）✅ |
| `probe_p1_version_pin.py` | pinned `1.000 (800/800)`、ret-random `0.841`、ret-latest `0.850` | **逐位一致** ✅ |
| `ruff check backend/services/knowevo mcp_servers`（官方范围） | **All checks passed!** | ✅ |

---

## 6. 独立复核（子 agent，静态审查，禁止连库）

由独立 agent 逐行复核「分块修复是否改变语义」，结论摘要：

- 分块后的查找全部在写入/决策循环之前（`graph_store.py:284-299` 实体、`381-394` 关系，均早于 `300-344` / `396-418`）→ **无顺序差异**；
- 去重后的键每键唯一赋值，同名覆盖语义不变；变量 `i` 不冲突；
- `valid_now` 的 `now()` 在同一事务内恒定，逐分块重复求值**无语义差异**；
- **发现并确认**：`self.batch_size` 全仓库从未赋值 → K 恒为 1000（本会话已修 harness 传递路径）；
- **发现**：原先若 `batch_size` 被设为 0 或负数，`range(0, n, -1)` 会返回空 → **静默丢弃全部写入**。此为既有代码遗留的潜在风险（实践中不可触发，因该属性从未被赋值），**本会话未加防御，登记为待接线项**。

> receipt §7.1 记录过「子 agent 报告曾编造内容」的教训。本次复核**要求逐条给出 `file:line`**，且**禁止连库**以免污染计时；其结论已与主会话的直读结论交叉核对。

---

## 7. 口径更正清单（对 receipt 的修订）

| # | receipt 原文（位置） | 实测 | 处置 |
|---|---|---|---|
| 1 | §5.4 示范命令用 `--no-indexes` / `--with-indexes` | 该两开关**不存在**（原 argparse 只有 `--entities/--edges/--batch-size/--repeats/--output`），照跑报 `unrecognized arguments` | 已补实现 |
| 2 | §5.4 正文称「已保留逐行模式开关 `--legacy`」 | **不存在** | 已补实现（importlib 加载 HEAD 真源码） |
| 3 | §5.2 复杂度表：关系侧 `ceil(Kc/K)` 次当前视图查找 | 代码实为**恒 1 次整批查找**（§5.3 正文「一次整批查找」自相矛盾）；且该 1 次在 3 万键时**直接崩** | 已修为 `ceil(Kc/K)` 并注明原因 |
| 4 | §5.3 称实体侧「可用 `ON CONFLICT`」 | 代码实为 **lookup-then-insert**，未用 `ON CONFLICT` | 登记（功能等价，但与描述不符） |
| 5 | §5.2「20000 行 ≈ 21 次往返」 | 实测语句数**严格** `ceil(S/K) + ceil(N/K)`（2000 行/K=1000 → 4 条），且**往返不是瓶颈**（§3.2） | 已更正 |
| 6 | §6.2「b-tree 无法把第二个合取项作为索引条件 → 退化为全索引/顺序扫描」 | 禁用 seq scan 后 PG 用 **`BitmapOr`** 把两个析取项**都**推进 `ix_kr_valid` 的索引条件 | 已更正（结论方向不变，但论据须换） |
| 7 | §6.6 ⑤ 提及「0.0236ms 合成索引口径 / 12.5ms 真实 PG 口径」 | 本环境真实 PG 多跳 p50 = 53.69ms（关）/ 63.93ms（开），与 12.5ms **不同源**（数据规模/机器不同） | 已登记新数字，**不混用** |
| 8 | §5.4 建议扫描 `K=20000` | 实测 `K=20000` **必然失败**（单条 `IN` 2 万元组 → `stack depth limit exceeded`） | 已更正为 `K ≤ 8000` |
| 9 | §5.5 引用的 `79.4s + 120.2s` 作为改前 | 本环境同口径改前 = **124.06s**（148.2/118.0/105.9） | 已以本环境实测值取代 |
| 10 | §6.5 称「`probe_p1` 未掉不构成对区间下推的验证」 | 本次已用 EXPLAIN + p95 给出直接判定（§4） | 已升级 |

---

## 8. 本次变更文件

**新增（本会话）**

```
competition/experiments/explain_valid_range.py        ← 证据采集器（preflight / EXPLAIN / 多跳 p95）
competition/experiments/run_write_path_suite.sh       ← A/B 交替写入基准套件
competition/experiments/run_batch_sweep.sh            ← 批量拐点扫描（K 已真正生效）
competition/docs/verification-reports/sessionB-closure-pg-bench-2026-09-24.md   ← 本文件
```

**修改（本会话）**

```
backend/services/knowevo/graph_store.py               ← 两处存在性查找按 K 分块（修 StatementTooComplex）
competition/experiments/bench_write_path.py           ← 补 --legacy / --no-indexes / --with-indexes；修 sys.path；
                                                        修 --batch-size 未传到 store
competition/docs/sessionB-receipt-五核前三项-2026-09-23.md   ← 追加 §11 闭环回填
competition/docs/cost-ledger.md                       ← 追加台账行
competition/deliverables/evidence-index.md            ← 追加生成记录
```

**环境侧（仓外，供复现）**

```
/home/qianqian/pg-env.sh                 ← PG 环境与本机 DSN
/home/qianqian/pg16/                     ← PostgreSQL 16.15（conda-forge，自包含）
/home/qianqian/pgdata/                   ← 数据目录
/home/qianqian/probe_in_clause_limit.py  ← IN 元组数崩溃阈值的最小复现
/home/qianqian/count_roundtrips.py       ← 语句计数探针
/home/qianqian/summarize_bench.py        ← 基准汇总
/home/qianqian/bench-runs/               ← 全部原始产物（JSON + EXPLAIN 原文 + 日志）
```

---

## 9. 未验证 / 开放项（诚实清单）

| # | 项 | 状态 |
|---|---|---|
| 1 | 多跳 p95 的**跨机器**绝对可比性 | **未验证**。本数字只在本机 PG 16.15 + 本数据集下成立 |
| 2 | `D_batched_noidx_repeats3` 第 2 次 12.2s 的根因 | **未定位**（疑与检查点/刷盘时机有关），未重复验证 |
| 3 | `batch_size ≤ 0` 的静默丢失防御 | **未加**（既有遗留、实践不可触发），登记待接线 |
| 4 | 生产真实库上的退化行（`invalid_at < valid_at`） | **无法验证**。`ck_*_valid_order` 是 `NOT VALID`，不会回溯校验既有行；本次预检为 0 **仅对合成数据集成立** |
| 5 | 大规模（≥100 万行）下 GiST 与 b-tree 的交叉点 | **未测**。本报告只覆盖 3 万行 |
| 6 | ES 语义通道（receipt §8 #2） | **未做**（本次范围外） |
| 7 | 多跳查询投影化使覆盖索引生效（receipt §8 #3） | **未做**（本次范围外） |

---

## 10. 一句话结论

**任务要求的三条全部跑通了，而「跑通」本身暴露了上一会话交付代码里的一个致命缺陷**：批量写入的关系侧查找未分块，在 3 万边规模下直接令 PG 解析器爆栈、写入完全失败 —— 这个缺陷只有真实 PG 才能暴露。修复后，**20 000 实体 + 30 000 边的写入从 124.06s 降到 2.291s（54.15×，≤10s 达标）**，且四个新索引的可测写入成本为零。索引下推一侧，`ix_kr_valid_range` 被规划器**真实选中并完整承接谓词**，谓词等价性也在数据上实测通过；但**翻开关会让多跳 p95 恶化 23%**，故按证据**不翻**，并如实更正了 receipt §6.2 与 §5.2 中两处过强的论证。
