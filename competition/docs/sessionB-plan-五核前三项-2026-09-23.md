# 会话 B · 算法优化五核（前三项）执行计划

> **工单**：`新会话B-算法优化五核-前三项-2026-09-23.md` v2
> **产出**：§8「先输出，再动手」的正式输出件
> **日期**：2026-09-23 · **工作区**：`/home/qianqian/Work/All/Nexent`
> **作者署名**：小算（高级算法工程师）

---

## 0. 环境前置检查结果（工单 §3.5）——**结论：服务不可用，性能验收受阻**

```text
$ docker ps
failed to connect to the docker API at unix:///var/run/docker.sock ... no such file or directory

$ pg_isready
(eval):1: command not found: pg_isready      # 且 `which psql` → not found

$ curl -s -o /dev/null -w "%{http_code}" http://localhost:9200
upstream connect failed: Connection refused (os error 111)   # ES 端口无监听

$ ss -ltnp | grep -E '5432|9200'
（无输出——5432/9200 均无监听）
$ ps aux | grep -E 'postgres|elasticsearch|docker'
（无输出）
```

**判定：PG / ES / docker 三者全部不可用。**

按工单 §3.5 规定处置：

| 允许做 | 禁止做 |
|---|---|
| ✅ 纯算法层改动 | 🚫 自己起服务（可能污染环境） |
| ✅ 零依赖探针（`probe_p1..p6` 零 DB / 零 LLM） | 🚫 用 mock / 伪造数字冒充实测 |
| ✅ 迁移 DDL 的**静态审查** | 🚫 声称性能已达标 |

**因此本轮交付分两类，且必须在 receipt 中严格区分**：
- **「已实测」**：pytest 回归、`probe_p1..p6` 输出、纯算法层（3.2）的全部数字。
- **「未执行（服务不可用）」**：3.1 的真实写入秒数、3.3 的 `EXPLAIN` 与多跳 p95。**给出复现命令，不填数字。**

---

## 1. 改前本地基线（工单 §5 要求「先跑一次」）

> 工单 §5 指出文档里有 5 个互斥基线（551/700/711/718/721）。**以下是本会话自己实测的**，后续一切对比以它为准。

```text
$ cd nexent/backend && .venv/bin/python -m pytest ../test/backend/services/knowevo/ -q
721 passed, 30 skipped, 1 warning in 56.12s
```

**→ 本会话本地基线 = `721 passed, 30 skipped`。** 与 `AGENTS.md:42` 的记载一致，与 `evidence-index.md:30`（718）、`reproduce-README.md`（551）不一致——**这本身就是工单预告的「口径互斥」现象，记入 receipt。**

`probe_p1..p6` 改前基线（零 DB / 零 LLM，seed 固定，全部 exit=0）：见 §3.2 与 receipt。

---

## 2. 执行顺序与验收方法

三项的性价比排序沿用工单 §3。**由于服务不可用，实际可执行度不同**，故按「可完整交付 → 部分交付」重排执行序：

| 序 | 项 | 可执行度 | 验收方法 | 预计产物 |
|---|---|---|---|---|
| **P0** | §2 零成本前置（先例对标 + 事实纠错 + 许可证红线） | **100%** | 三方交叉核对（`06` 原文 / arXiv 元数据 / 代码） | `06` §2/§3.1/§3.2/§4/§6.1 修订；2 处代码引用修正 |
| **P1** | 3.2 对齐器换语义聚类 | **100%**（零 DB） | `probe_p5` 改前改后对比 + 阈值扫描曲线 + BH-FDR 假阳计数 | 新探针 `probe_p8`、`alignment_precision_pooled`、敏感性曲线 |
| **P2** | 3.1 写路径批量化 | **代码 100% / 实测 0%** | 真实秒数 ❌（服务不可用）→ 交付实现 + 基准脚本 + 静态审查 | 迁移 `kw_011`、批量 upsert、基准 harness + 复现命令 |
| **P3** | 3.3 时间区间索引下推 | **DDL 100% / 实测 0%** | `EXPLAIN` 与 p95 ❌（服务不可用）→ 交付 DDL + SQL 改写 + `probe_p1` 回归 | 同一迁移 `kw_011`、多跳 SQL 改写、复现命令 |

**3.1 与 3.3 共用同一张迁移**（索引集是同一套：邻接 + 反向邻接 + GiST 时间区间），这是两者天然耦合之处，也符合工单 §8「具体到 `v2.5.5_kw_011_xxx.sql` 的完整名」的单数表述。

---

## 3. 独占文件清单

### 3.1 新建迁移（唯一新 DDL 入口）

```
nexent/deploy/sql/migrations/v2.5.5_kw_011_knowevo_graph_indexes.sql
```

> 编号接续现网最大号 `kw_010`（已核实：`ls *_kw_*.sql` 最大为 `v2.5.5_kw_010_agent_kg_tool_binding.sql`）。**只新增，不改任何既有迁移。**

**新增索引名（沿用 `kw_001` 已有 `ix_ke_*` / `ix_kr_*` 命名族）：**

| 索引名 | 表 | 类型与定义 | 服务项 |
|---|---|---|---|
| `ix_kr_valid_range` | `kg_relation_t` | `GIST (tstzrange(valid_at, COALESCE(invalid_at,'infinity'::timestamptz),'[)'))` | 3.3 |
| `ix_ke_valid_range` | `kg_entity_t` | 同上（实体侧同样参与版本过滤） | 3.3 |
| `ix_kr_current` | `kg_relation_t` | `btree (tenant_id, src, rel_type) WHERE invalid_at IS NULL`（部分索引，加速当前视图） | 3.3 |
| `ix_kr_hop_rev_cover` | `kg_relation_t` | `btree (tenant_id, dst, rel_type) INCLUDE (src)` | 3.1/3.3 |

> **命名冲突已裁定**：评审反馈 §5.1 写 `idx_kg_edge_valid_range`、§5.5 写 `idx_edge_valid_range`（**同一索引两个名字**）。本会话统一取 **`ix_kr_valid_range`**，理由：① `kg_edge_t` 表不存在（见 §4 事实纠错）；② 与现网 `ix_kr_*` 族一致。

### 3.2 会修改的 `knowevo/*.py`（含预期行区间）

| 文件 | 行区间 | 改什么 |
|---|---|---|
| `backend/services/knowevo/graph_store.py` | **205–272** | `upsert_entities` / `upsert_relations` 逐行 session → 批量（详见 §5.2） |
| `backend/services/knowevo/graph_store.py` | **302–315** | 多跳取边谓词改用区间索引可用形式 |
| `backend/services/knowevo/alignment_service.py` | **新增模块级区块**（不删既有） | 语义聚类 + Hungarian + bootstrap + BH-FDR；`calibrate_topic` **保持可调用**（回归护栏），新路径另起 |
| `backend/services/knowevo/pipeline/eval_v1.py` | **22** | 删除错误 arXiv ID（见 §4） |
| `backend/services/knowevo/pipeline/eval_e1.py` | **16** | 同上 |

**不碰**：任何接线文件（`app_factory.py`/`config_app.py`/`runtime_app.py`/`const.py`/`pyproject.toml`/前端）、`knowevo/**.py.md` 契约、既有迁移。
**另注意**：`kg_service.py`、`llm_client.py`、`pipeline/mine_skill_templates.py` 在本会话开工前**已处于 modified 状态**（非本会话所为），本会话**不触碰**。

### 3.3 新增探针（不改 `probe_p1..p6`）

```
nexent/competition/experiments/probe_p8_alignment_semantic.py   # 新算法；只读既有产物
nexent/competition/experiments/_sweep_p5_threshold.py           # 阈值扫描 wrapper（工单 §9 明确要求：参数化不得改原文件）
```
产物 → `competition/deliverables/algorithm-probes/probe_p8_alignment_semantic.json`

### 3.4 台账登记（工单 §9 独占声明）

| 文件 | 权限 | 动作 |
|---|---|---|
| `competition/docs/cost-ledger.md` | ✅ 只追加写入耗时/对齐质量行 | 追加 2 行，带锚点 `<!-- session:B algo-opt -->` |
| `competition/deliverables/evidence-index.md` | ✅ 只追加 | 追加本任务条目，**不重排既有表格** |
| `competition/docs/verification-reports/` | ✅ 新增文件 | 可选：3.2 口径决定书 |
| `06-论文与开源武器库-v2.md` | ✅ 只改 §2/§3/§4/§6 | §2.1–§2.3 三处 |

**待接线项（登记，不自行改）**：见 §6。

---

## 4. 事实纠错（工单 §2.2）——**核对后与工单假设有出入，如实报告**

工单 §2.2 假定三处错误都在 `06`。**实测：只有一处真的在 `06`。**

| 引用 | 工单假设位置 | **实测位置** | 处置 |
|---|---|---|---|
| `2608.21949` | `06` | ✅ 确在 `06`：§2 对标表 L-evolution 行、§3.1 首行、§6.3 L582 | 改述为「该方向自 2018 SEAA 后长期停滞」；§6.1 补修正行 |
| `2505.23319` | `06` | ❌ **不在 `06`**（`grep` 零命中）。实际在**代码**：`pipeline/eval_v1.py:22`、`pipeline/eval_e1.py:16` 的 docstring 称其为 τ²-bench | 改正代码 docstring（工单 §0 的 P0-2） |
| `2511.13645` | `06:118` 附近 | ❌ **不在 `06`**（零命中）。实际在 `评审反馈与算法增强路线-2026-09-23.md:582` | 改 `评审反馈:582`；并在 `06` §6.1 补一行修正记录 |

**另一处更严重的事实错误（工单未列、本会话发现）**：评审反馈 §5.1/§5.5 的 DDL 草图使用
`kg_edge_t` / `src_entity_id` / `dst_entity_id` / `relation_type` / `evidence_id`。
**这些表和列在真实 schema 里全部不存在。** 真实为：

```
nexent.kg_relation_t(id, tenant_id, src, dst, rel_type, claim, props,
                     contested, valid_at, invalid_at, retrieved_at,
                     superseded_at, created_at)
nexent.kg_entity_t(id, tenant_id, stable_id, name, aliases, class_ref, props,
                   embedding, status, split_into, valid_at, invalid_at, ...)
```
（证据：`deploy/sql/migrations/v2.5.5_kw_001_knowevo_core.sql:51-91` 原文）

**若照抄评审反馈的 DDL 直接执行，会立即报 `relation "kg_edge_t" does not exist`。** 本会话所有 DDL 一律以真实 schema 为准。

---

## 5. 三项技术方案要点

### 5.1 3.3 时间区间索引（下推的正确形式）

**现状（已核实）**：`valid_now()`（`backend/database/knowevo_db.py:390-404`）产生
`valid_at <= t_v AND (invalid_at IS NULL OR invalid_at > t_v)`，本身就是**半开区间包含**语义；
该谓词已下推到 SQL（`graph_store.py:309` / `343`），**但缺索引支撑**。

**问题精确定位**：现有 `ix_kr_valid(valid_at, invalid_at)` 是 btree，只能利用 `valid_at <= t_v` 前缀；
`invalid_at IS NULL OR invalid_at > t_v` 在 btree 里**无法作为索引条件**（一个 OR + 一个范围）。
GiST on `tstzrange` 才能把整个 `@>` 变成一个索引条件。

**改写**：
```sql
-- 改前：valid_at <= :t_v AND (invalid_at IS NULL OR invalid_at > :t_v)   ← 半可索引
-- 改后：tstzrange(valid_at, COALESCE(invalid_at,'infinity'),'[)') @> :t_v ← 全可索引
```
**语义等价性**：`[valid_at, invalid_at)` 包含 `t_v` ⟺ `valid_at <= t_v < invalid_at` ⟺ 原谓词。**逐项等价，无口径漂移。**

**代价披露（诚实）**：`ADD COLUMN ... GENERATED ALWAYS AS ... STORED` 在 PG 中会**重写整表并持 ACCESS EXCLUSIVE 锁**。2 万实体/3 万边的演示库可接受，但必须在 receipt 注明。**低锁替代方案**：改用**表达式索引**（不加列），查询写同样表达式——不重写表，代价是表达式必须与索引逐字一致。本会话同时给出两版，默认走上者（工单 §3.3 明确要求「生成列」）。

### 5.2 3.1 写路径批量化——**方案因一个事实而必须偏离评审反馈**

**现状（已核实）**：`graph_store.py:205-242` 与 `253-272` 均为
```python
for e in ents:                      # ← 循环在外
    with _get_db_session() as session:   # ← 每个元素新开 session + 提交
```
`_get_db_session()` 的 contextmanager 在退出时 commit/close ⇒ **N 个实体 = 约 2N 次往返 + N 次提交**。这是 79.4s 的根因。

**关键事实（决定方案）**：
- `kg_entity_t` **有** `UNIQUE (tenant_id, stable_id)`（`kw_001:68`）⇒ 可用 `ON CONFLICT` 单语句批量 upsert。
- `kg_relation_t` **没有任何 UNIQUE 约束**（`kw_001:74-88` 无 UNIQUE；`grep UNIQUE` 仅命中 27/68/120/139/187，均非该表）⇒ **评审反馈 §5.5 E-1 的 `ON CONFLICT (tenant_id, src_entity_id, ...)` 不可执行**（无对应唯一索引，PG 会直接报错）。

**故采用非对称设计（语义保真优先）**：

| 表 | 方案 | 往返数 |
|---|---|---|
| 实体 | `INSERT ... SELECT FROM unnest(...) ON CONFLICT (tenant_id, stable_id) DO UPDATE` | **O(1)** |
| 关系 | ① 一次 `SELECT` 取全部候选键的现存行 → ② 一次 `INSERT ... FROM unnest(...)` 只插「新增 + claim 变更」行 | **O(1)（2 次）** |

关系侧**不引入新唯一约束**——因为①加约束会让既有重复行阻塞迁移；②会改变幂等语义的归属（现语义由应用层承担，注释明确写"conflict resolution is the service layer's job"）。**批量化只优化往返，不改变语义**，这是本项的安全边界。

**另一处纠偏**：评审反馈 §5.5 E-3 的「覆盖索引 `INCLUDE (dst_entity_id, relation_type)` 避免回表」**此处不适用**——
现有查询 `session.query(KgRelation)...q.all()` 取**全列**（`graph_store.py:303-319`），覆盖索引无法免除堆访问。
要用上覆盖索引，必须同时把查询改成**投影必需列**。本会话在 receipt 中标注该前提，不做「加了索引就变快」的无证据断言。

### 5.3 3.2 对齐器语义聚类（唯一可 100% 实测的一项）

**现状（已核实）**：`alignment_service.py:1336-1342` `calibrate_topic(max_df_fraction=0.05, min_shared_tokens=2)`；
判定点 `:1394` `if len(shared) >= min_shared_tokens`；`precision_lower_bound` 在 `:1406`。
docstring `:1350-1353` 自述敏感性：「min_shared_tokens=2 时 9/9，=3 时掉到 3/9」。

> ⚠️ **与评审反馈的冲突（已核实）**：评审反馈 §5.3 C-4 的 `ALIGN_CFG` 写 `"min_shared_tokens": 3` 并注释「默认值 = 现硬编码值」。
> **代码里默认是 2，不是 3。** 若照抄该配置，等于把 recall 从 9/9 主动打到 3/9。**本会话保持默认 2，并登记此冲突。**

**目标**（严格按工单 §3.2 的**已修正口径**，不写「下界→点估计」那类错配表述）：
- 产出改名后的 `alignment_precision_pooled`（**含假阳计数**）+ Wilson 区间
- 阈值敏感性显著下降（2→3 时 recall 不再崩）
- 给一维扫参曲线

**方法（四件，对应 C-1~C-4）**：
1. **连续相似度**替代硬阈值：idf 加权 token 相似度（软重叠），去掉「≥k 个 token」的阶跃。
2. **二分图最优匹配**：复用服务内**已有的纯 stdlib 匈牙利实现**（`alignment_service.py:628/730` 附近），不引入新依赖。
3. **bootstrap 稳定性选择**：多次重采样，只保留高频率出现的对齐。
4. **BH-FDR 控制假阳**：置换检验出 p 值 → Benjamini-Hochberg 控制 FDR；**假阳计数由此可得**（这正是 T-21 缺的量）。

**唯一受限处**：C-1 的「ES `dense_vector` + kNN 语义通道」**无法接入**（ES 不可用；且已核实 `llm_client` 无 embedding 端点）。
→ **登记为「待接线项」**，本会话交付**通道接口 + 零依赖回退实现**（确定性、可复算），**不声称已接 ES**。

**命名与口径纪律（工单 §3.2 强制）**：产出数字一律用 `alignment_precision_pooled`，并注明与 T-21 的 `64/517` 的 **estimand 差异**：
> T-21 的 `64/517` 是**相关率**（分母 = 全体机器组，**无假阳计数**）；本次 `alignment_precision_pooled` 是**含假阳计数的对齐质量点估计**。**两者不可直接比较。**

---

## 6. 待接线项（登记，不自行改）

| # | 项 | 为什么不能自己改 |
|---|---|---|
| 1 | ES `dense_vector` 索引 `kw_change_vec` + kNN 通道 | 需 ES 服务 + embedding 端点；属平台接线 |
| 2 | 若采用「生成列」路线，`kg_entity_t`/`kg_relation_t` 的 ORM 模型需加 `valid_range` 只读属性 | 触及 `database/knowevo_db.py` 的模型定义，属共享面 |
| 3 | 多跳 SQL 的投影化（为让覆盖索引真正生效） | 会改变 `graph_store` 返回契约形状，需先过 `**/*.py.md` 契约核对 |

---

## 7. 判定标准（动代码前写死，工单 §7）

| 项 | ✅ 达标 | ❌ 推翻 |
|---|---|---|
| 3.1 写入提速 | 不含建索引时纯写入 ≤10s | >10s 或提升 <2× |
| 3.2 对齐质量 | 点估计提升，且 2→3 时 recall 不再崩 | 持平/下降，或敏感性未改善 |
| 3.3 索引下推 | `probe_p1` 不掉 **且** p95 有改善 **且** `EXPLAIN` 走索引 | `probe_p1` 掉，或未走索引 |

**服务不可用导致无法判定的项，一律写「未达标/未验证」并附复现命令，不保留半成品。**
