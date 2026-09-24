# S1 · 基线读数与**数据前置条件阻塞报告**

**执行日期**：2026-09-23
**执行人**：会话 A（主理人）
**状态**：⚠️ **`Q*` 与 Quality 轴不可得 → 部分材料 `insufficient_data`**；**工作量轴可跑**

> 依 `AGENTS.md` §0 第 1 条：「不得伪造任何数字……没有证据=没做完，写 `待跑` 或 `insufficient_data`，**不许写 0 或占位**冒充。」
> 本文是**如实的前置条件报告**，不是失败记录。

---

## 1. 环境探测结果（逐项实测）

| 探测项 | 命令 | 结果 |
|---|---|---|
| Python venv | `ls backend/.venv/bin/python` | ✅ 存在，软链 `cpython-3.11.15` |
| psql 客户端 | `which psql` | ❌ **不存在** |
| docker 客户端 | `which docker` | ✅ `/usr/bin/docker` |
| **docker 守护进程** | `docker info` | ❌ **`dial unix /var/run/docker.sock: connect: no such file or directory`** |
| `/var/run/docker.sock` | `ls -la` | ❌ **不存在** |
| `DOCKER_HOST` | `echo` | `<unset>` |
| systemd | `systemctl is-active docker` | ❌ **`System has not been booted with systemd as init system (PID 1)`** |
| 监听端口 | `ss -ltn` 过滤 5432/54322/9010/8002 | ❌ **无任何命中** |
| `.env` 文件 | `ls .env* backend/.env*` | ❌ 不存在 |
| 本地 PG 二进制 | `ls /usr/lib/postgresql/*/bin/postgres` | ❌ 不存在 |

**结论**：数据库容器（`supabase-db-mini` / `nexent-postgresql`）**在本会话内不可达**。
用户侧的正常启动方式见 `/home/qianqian/Desktop/tips/open.sh`（`docker start nexent-postgresql … supabase-db-mini …`）。

---

## 2. 【核心发现】`Quality(·)` 在**冻结池上根本无法计算**

这是本轮最重要的技术发现，**比"数据库没开"更严重**——即使数据库开着，**冻结池也不够**。

### 2.1 `Quality(·)` 需要哪些字段（逐字读自 `_k0_metrics_from_snapshot`，`[代码可读]`）

```
classes = snapshot["classes"];  n = len(classes)

cov   ← 需要 seed_terms（外部注入）+ classes[].name
red   ← 需要 classes[].parent 与 classes[].also_parent
dep   ← 需要 classes[].parent（上溯父链）
align ← 需要 classes[].anchor
```

### 2.2 冻结池实际有什么（`[代码可读]` `probe_p6_autonomy_tau.py:111-142`）

```
每项 7 字段：(op, target, confidence, evidence_spans, tenant, gold_error, gold_source)
```

**缺**：`parent`、`also_parent`、`anchor`、`seed_terms`。
⇒ **`Quality(·)` 的四个分量，一个都算不出来。**

### 2.3 这些字段**确实存在**，只是没被导出（`[代码可读]`）

`ontology_service.py` `_apply_ops()`：
```python
if code == "CLS_ADD":
    snapshot["classes"].append(dict(payload, stable_id=payload["name"]))
```
⇒ `CLS_ADD` 的 **payload 整包变成 class 字典** ⇒ **payload 里就带 `parent` / `anchor`**。
但 P6 的 `EXPORT_SQL`（`probe_p6_autonomy_tau.py:96-104`）只 `select` 了
`op / target / confidence / evidence_spans` —— **把 payload 的其余键丢掉了**。

### 2.4 重建可行性逐条排查（全部失败）

| 重建路径 | 结果 |
|---|---|
| 从 DB 读 `ontology_version_t.snapshot` | ❌ 数据库不可达（§1） |
| 从 DB 读 `applied_ops[].payload` | ❌ 同上；且 P6 导出已丢键 |
| 从 `SNAPSHOT_V110_MEMBERS` 重建 | ⚠️ **只给类名**，`parent`/`anchor` 仍缺 |
| 仓库内搜索 `"toc:[0-9]"`（anchor 格式） | ❌ **零命中**（除测试 fixture） |
| 仓库内搜索 `also_parent` | ❌ **零命中**（除实现与测试 fixture） |
| git 历史 / 迁移文件 `kw_001` 中的种子 INSERT | ❌ **无任何 `INSERT INTO` ontology 行** |
| PG dump / 备份文件 | ❌ 不存在 |
| archive 内 `wire-captured.jsonl`（wire 抓包） | ❌ 内容是 LLM 抽取 prompt，`"parent"`/`"anchor"`/`"snapshot"` **命中数均为 0** |
| archive 内 `t28-discrim/probe-results.json`（PG 直查产物） | ⚠️ 有 136 实体的 `class_ref`（类名可用），**但无 `parent`/`anchor`** |
| 跑 `build_ontology_round(dry_run=True)` 重生成 | ❌ 返回值**只有 report**（`round_id`/`proposal counts`/`top5_preview`），**不含 snapshot** |
| 测试 fixture（`test_ontology_service.py:378-386`） | ⚠️ 是**合成 5 类**手算样例，**不是真实 v1.1.0**，不可冒用 |

### 2.5 退化验证（证明"硬算"也救不回来）

若强行用 `SNAPSHOT_V110_MEMBERS` 只造类名：
```
snapshot = {"classes": [{"name": X} for X in SNAPSHOT_V110_MEMBERS], "rel_types": [...]}
⇒ cov   = 0.0     （seed_terms 缺失，代码分支 else: cov = 0.0）
⇒ red   = 0.0     （无 parent ⇒ extra = 0）
⇒ dep   = 1       （无 parent ⇒ 链长恒 1）
⇒ align = 0.0     （无 anchor）
⇒ Quality 恒为 (0.0, 0.0, 1, 0.0) —— 与"审了哪些提案"完全无关
```
⇒ **曲线是平的**。**不是"测不出差异"，是"函数在这种输入下是常数函数"**。
⇒ 若照此出图，就是**用常数冒充测量**——直接违反 `AGENTS.md` §0 第 1 条。

---

## 3. 对实验设计的冲击与处置

### 3.1 受影响的部分

| 件 | 状态 | 原因 |
|---|---|---|
| **甲（真实池 2×2，含 Quality 终点）** | 🚫 **不可执行** | `Quality(·)` 在冻结池上不可计算（§2） |
| 甲 的 **工作量轴**（谁需要人审、多少分钟） | ✅ **可执行** | 只需 `op` / `confidence` / `evidence_spans`（池内都有） |
| **乙（合成异质流机制验证）** | ✅ **可执行** | 合成流里我们自己造 `parent`/`anchor`，Quality 天然可算 |
| **丙（严格版设计）** | ✅ **可执行** | 纯文档 |

### 3.2 处置决定（预登记 v0.2 修正）

> **把「甲」拆成两半**：
> - **甲-工作量（本轮执行）**：2×2 的**人审工作量轴** —— 实测「门因子」与「排序因子」各自决定多少**待审项数与人工分钟数**。**这是 A 级（计数实测）× L 级（系数文献）**，非退化。
> - **甲-质量（本轮 `insufficient_data`）**：达到 `Q*` 所需的累计分钟数 —— **因 `Quality(·)` 不可计算而无法产出**，如实标 `insufficient_data`，并**给出解锁前置条件**（§4）。
>
> **主终点随之调整**：由「达到 `Q*` 的累计分钟数」改为「**门因子决定的人审工作量倍数**」+「**排序因子的人审工作量差异**」。
> **理由**：工作量轴是官方评分句「效率 = 产出/投入」中**投入侧的完整度量**，且在本池上**非退化、可复算、可被第三方核实**。质量侧缺失则在限制中如实声明，并在丙中给出补齐路径。

### 3.3 这样改之后本轮**能**给出的结论（预告，跑前写死）

| 结论 | 性质 |
|---|---|
| 门开/关导致的人审项数与分钟数**倍数** | ✅ A×L，可复算 |
| 排序因子在该池上的人审工作量差异 | ✅ A×L；**预期为 0（结构零，§4.3 of 预登记）** |
| 放宽证据门的**质量代价** | ⛔ `insufficient_data`（P6 裁决金标 n=0，且 Quality 不可算） |
| 达到 `Q*` 的累计分钟数 | ⛔ `insufficient_data`（§2） |
| 合成异质流上排序因子的机制效应 | ⚠️ **合成机制验证**（不得对外当效率证据） |

---

## 4. `insufficient_data` 的**精确解锁前置条件**（写清楚，便于下一阶段执行）

要让「达到 `Q*` 的累计分钟数」这条轴成立，**必须满足以下之一**：

| # | 前置条件 | 具体动作 | 谁能做 |
|---|---|---|---|
| **P-1** | **启动数据库** | 执行 `~/Desktop/tips/open.sh`（或 `docker start nexent-postgresql supabase-db-mini`），使 `ontology_version_t` 可读 | **用户**（本会话无 docker 守护进程，不可达） |
| **P-2** | **补导出 payload 的键** | 在 `probe_p7` 里用**扩展版 SQL** 重取池：`op->'payload'->>'parent'`、`op->'payload'->>'anchor'`、`op->'payload'->>'also_parent'`（**不改 p6**，p6 的元组另存为新结构） | 本任务（**在 P-1 之后**） |
| **P-3** | **提供 `seed_terms`** | `cov` 还需 `seed_terms`。来源应为种子骨架的 S2 输出（每章 top-K 术语）。需确认其持久化位置 | 本任务（P-1 后） |
| **P-4** | **确认 v1.1.0 就是 `Q*` 的基准** | 若用户希望改用别的基准（如某个更完整的版本），需明示 | **用户** |

> **P-2 是关键**：即使数据库开了，**若仍沿用 P6 的 `EXPORT_SQL`，`parent`/`anchor` 依然缺失**。
> 所以「开数据库」和「补导出键」是**两个独立前置条件**，缺一不可。

---

## 5. Evidence 锚点

| 事实 | 位置 |
|---|---|
| `quality_metrics` / `_k0_metrics_from_snapshot` 实现 | `nexent/backend/services/knowevo/ontology_service.py:753-758` / `:990-1035` |
| `_apply_ops` 中 `CLS_ADD` payload 整包入 class | `ontology_service.py` `_apply_ops()`（`CLS_ADD` 分支） |
| `_k0_metrics_from_snapshot` 手算 fixture（语义锁定） | `nexent/test/backend/services/knowevo/test_ontology_service.py:365-400` |
| `build_ontology_round` 返回值**不含 snapshot** | `ontology_service.py:898-945` |
| P6 池字段（7 个，无 parent/anchor） | `nexent/competition/experiments/probe_p6_autonomy_tau.py:111-142` |
| P6 导出 SQL（只取 4 键） | `probe_p6_autonomy_tau.py:96-104` |
| 容器启动清单 | `/home/qianqian/Desktop/tips/open.sh` |
| 池内 `rel_types` 不被 `Quality` 读取（P-2 缺陷） | 同 `_k0_metrics_from_snapshot` 首行 `classes = snapshot["classes"]` |
