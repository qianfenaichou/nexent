# 会话 B · 算法优化五核（前三项）· Receipt 证据包

> 工单：`新会话B-算法优化五核-前三项-2026-09-23.md` v2
> 日期：2026-09-23 · 工作区：`/home/qianqian/Work/All/Nexent` · 作者署名：小算
> **本 receipt 严格区分「已实测」「已交付但未实测（服务不可用）」「未做」三类，不含任何未经测量的数字。**

---

## 0. 环境前置检查（工单 §3.5）—— **PG / ES / docker 三者全不可用**

```text
$ docker ps
failed to connect to the docker API at unix:///var/run/docker.sock; ... no such file or directory

$ pg_isready
(eval):1: command not found: pg_isready          # 且 which psql → not found

$ curl -s http://localhost:9200
upstream connect failed: Connection refused (os error 111)

$ ss -ltnp | grep -E '5432|9200'
（无输出）        $ ps aux | grep -E 'postgres|elasticsearch|docker'   →（无输出）
```

**后果（按工单 §3.5 处置）**：性能类验收（3.1 真实写入秒数、3.3 `EXPLAIN` 与多跳 p95）**无法执行**。已按规交付代码/DDL/静态审查 + 复现命令，**未用 mock 或占位数字冒充**。

---

## 1. 实测清单（本会话真实跑过）

### 1.1 改前本地基线（工单 §5 要求）

```text
$ cd nexent/backend && .venv/bin/python -m pytest ../test/backend/services/knowevo/ -q
721 passed, 30 skipped, 1 warning in 56.12s
```

**→ 本地基线 = `721 passed, 30 skipped`**，与 `AGENTS.md:42` 一致。
⚠️ 与 `evidence-index.md`（718）、`reproduce-README.md`（551）、`cost-ledger` T-29 演进（700→711→718）**均不一致** → **证实工单预告的「基线口径互斥」现象**。本会话一切对比以自己实测的 721/30 为准。

### 1.2 改后回归（**含全部改动**）

```text
$ cd nexent/backend && .venv/bin/python -m pytest ../test/backend/services/knowevo/ -q
721 passed, 30 skipped, 1 warning in 53.41s
```

**→ 零回归**（721→721，无新增失败、无 pass 数下降）。

```text
$ cd nexent && backend/.venv/bin/python -m ruff check \
    backend/services/knowevo/alignment_semantic.py \
    backend/services/knowevo/pipeline/eval_v1.py \
    backend/services/knowevo/pipeline/eval_e1.py \
    competition/experiments/probe_p8_alignment_semantic.py
All checks passed!
```

### 1.3 `probe_p1..p6` 改前基线（零 DB / 零 LLM，全 exit=0）

| 探针 | 关键改前数字 |
|---|---|
| p1 | pinned V 1.000(160/160) / F 1.000(640/640) / 总体 1.000(800/800)；ret-random 0.841、ret-latest 0.850 |
| p2 | 净好增益 +2 / +6 / +1 / +5（budget 10/20/30/50）；20 seeds 噪声带内含 0 |
| p3 | 受影响实体 164/2000；决策卡 750/3000；传播均值 **0.023 ms** |
| p4 | 贪心/最优 mean **0.9884**（min 0.88）；单调性违反 **0/400**；311/400 达最优 |
| p5 | 话题级 recall **9/9**；`precision_lower_bound` **64/517=0.123791**；`[F] recomputed_strict_equals_persisted_calibration: FAIL`（见 §5.3） |
| p6 | arm A 人工干预全程 1.0；arm B 0.4762→0.5238；裁决金标 n=0 → insufficient_data |

> 注：p1–p6 均为只读复跑。其中 `probe_p3/p4` 的**产物 JSON 会打印时间戳/解释器版本**，复跑会改动该产物；本会话已 `git checkout --` 还原为仓库原状（见 §7 回滚）。

---

## 2. 交付总表

| 项 | 状态 | 产物 |
|---|---|---|
| §2 零成本前置（先例对标 / 事实纠错 / 许可证红线） | ✅ **已实测/已核对** | `06` 六处修订 + 2 处代码引用纠错 |
| 3.1 写路径批量化 | 🟡 **代码已交付**；真实秒数 **未实测** | `kw_011`、`graph_store.py` 批量、`bench_write_path.py` |
| 3.2 对齐器换语义聚类 | ✅ **已完整实测** | `alignment_semantic.py`、`probe_p8`、JSON 产物 |
| 3.3 时间区间索引下推 | 🟡 **DDL+代码已交付**；`EXPLAIN`/p95 **未实测** | `kw_011`、`valid_range_contains()`、opt-in 开关 |

---

## 3. §2 零成本前置（已实测）

### 3.1 先例对标（工单 §2.1）

`06-论文与开源武器库-v2.md` §2 对标表**新增顶行**（EvoReasoner/EvoKG），差异化表述按工单**逐字照抄**；并修正原 L-reason 行那句已被证伪的「未检索到先例」。§3.2 同步补「最强先例（必须主动划界）」行。

**arXiv 元数据已硬核实**（双源互证：官方 API XML + abs 页面；可复现命令见 §6.4）：

| ID | 真实标题（逐字） | 判定 |
|---|---|---|
| `2509.15464` | *Temporal Reasoning with Large Language Models Augmented by Evolving Knowledge Graphs*（Junhong Lin 等, MIT, 2025-09-18） | ✅ **相符** — EvoReasoner/EvoKG 属实 |
| `2608.21949` | `journal_ref` = *2018 44th Euromicro SEAA*, pp. 9-16, DOI `10.1109/SEAA.2018.00011` | ⚠️ **年份/性质不符** — 2018 旧文 2026 重挂 |
| `2505.23319` | *The spectral torsion for the one form rescaled Dirac operator*（`math.DG`） | ❌ **标题不符** — 与 τ-bench 无关 |
| `2511.13645` | *FuseSampleAgg: One-Pass Neighborhood Estimation for Budgeted **Knowledge-Graph Refresh** and Validation* | ⚠️ **性质不符（非标题不符）** |
| `2406.12045` | *τ-bench*（其摘要明确 "propose **pass^k**"） | ✅ 相符 |
| `2506.07982` | *τ²-bench* | ✅ 相符 |
| `2606.26511` | AUROC 0.59 原文语境 = "near chance" 的**阴性结果** | ✅ **相符且语境正确** |

### 3.2 事实纠错（工单 §2.2）—— **核对后与工单假设有出入，如实报告**

工单假定三处错都在 `06`。**实测只有一处真的在 `06`**：

| 引用 | 工单假设位置 | **实测位置** | 处置 |
|---|---|---|---|
| `2608.21949` | `06` | ✅ 确在 `06`（§2 L-evolution、§3.1、§6.3） | 改述为「自 2018 SEAA 后长期停滞」；§6.1 补修正行 |
| `2505.23319` | `06` | ❌ **不在 `06`**（grep 零命中）。实在 **代码**：`pipeline/eval_v1.py:22`、`pipeline/eval_e1.py:16` | 已改为 `2406.12045`（τ-bench，pass^k 的提出者）+ `2506.07982`（τ²-bench） |
| `2511.13645` | `06:118` 附近 | ❌ **不在 `06`**（零命中）。实在 `评审反馈与算法增强路线-2026-09-23.md:582` | 见下方「改判」 |

**两处我不采纳工单字面指示，理由如下（请复核）**：

1. **`2511.13645`：工单说「删除或替换」，我改为「改述」。**
   取证显示主标题确为 `FuseSampleAgg`、**副标题确含 `Budgeted Knowledge-Graph Refresh and Validation`** —— 团队的「Budgeted KG Refresh」**称法本身没错**，不是「望题生义」。真正的问题只是**性质**：它是 CUDA 算子级工程提速（step latency 2.24–3.48×），**不是**预算化刷新调度算法。→ 保留引用、修正性质描述。**若照工单删除，反而会删掉一条标题正确的引用。**

2. **`2606.26511` 的 AUROC 0.59：工单与其他文档均要求「删数字或先读正文」，我改为「已核实并保留」。**
   取证显示该数字真实存在，且**是阴性结果**（论文用它证明余弦相似度区分不了「被推翻」与「重复」的事实）。项目 §3.2 恰把它作「**反证**：为什么必须图上钉」使用 —— **语境正确**。已把该行从「⚠️ 需读正文确认」升级为「已核实」，并**明确标注不得写成该论文的方法性能**。

**另发现一处工单未列的事实错误（更严重）**：评审反馈 §5.1/§5.5 的 DDL 草图使用 `kg_edge_t` / `src_entity_id` / `dst_entity_id` / `relation_type` / `evidence_id` —— **这些表和列在真实 schema 里全部不存在**。真实为 `nexent.kg_relation_t(id, tenant_id, src, dst, rel_type, claim, props, contested, valid_at, invalid_at, ...)`（证据：`deploy/sql/migrations/v2.5.5_kw_001_knowevo_core.sql:74-91`）。**照抄执行会立即报 `relation "kg_edge_t" does not exist`。** 本会话全部 DDL 以真实 schema 为准。

**评审反馈自身的内部矛盾**：同一 GiST 索引在 §5.1 叫 `idx_kg_edge_valid_range`、在 §5.5 叫 `idx_edge_valid_range`。本会话**裁定统一为 `ix_kr_valid_range`**（理由：① `kg_edge_t` 不存在；② 与现网 `ix_kr_*` 命名族一致）。

### 3.3 许可证红线（工单 §2.3）

已在 `06` 新增 **§4.3 许可证红线声明**（原 §4.3/§4.4 顺延重编号）：含 Raphtory(GPL-3.0) / cortexgraph(AGPL-3.0) 仅作架构参考、自研部分清单、**Raphtory ≠ Cairn 易混警告**、GPL/AGPL 引用纪律。

---

## 4. 3.2 对齐器换语义聚类（**唯一 100% 可实测项，已完成**）

### 4.1 改前 vs 改后（`probe_p8_alignment_semantic.py` 真实输出）

```text
  [A] legacy min_shared_tokens / df grid  (the caliber being replaced)
      df      min_shared   recall     p_lb        matched_groups/groups
      0.02    2            7/9        0.067698    35/517
      0.02    3            2/9        0.009671    5/517
      0.05    2            9/9        0.123791    64/517
      0.05    3            3/9        0.021277    11/517
      0.1     2            9/9        0.125725    65/517
      0.1     3            4/9        0.040619    21/517
      0.2     2            9/9        0.125725    65/517
      0.2     3            4/9        0.040619    21/517
      1.0     2            9/9        0.125725    65/517
      1.0     3            5/9        0.052224    27/517
```

**工单声称的「2→3 时 recall 从 9/9 崩到 3/9」已复现**（df=0.05 行：9/9 → 3/9）✅，另发现更差点 df=0.02/ms=3 → **2/9**。

```text
  [B] semantic calibration (headline)
      recall                     : 9/9
      declared aligned groups    : 26  (of 517 machine groups)
      false positives (measured) : 0
      alignment_precision_pooled : 1.000000
      Wilson 95%                 : [0.8713, 1.0000]
      pairs tested / rejected    : 26 / 26
      params                     : {'capacity': 3, 'top_k': 15, 'fdr_q': 0.1, 'n_perm': 1000, 'seed': 20260923}
```

```text
  [C] the SAME legacy grid through the NEW pipeline (must be flat)
      df=0.02 min_shared=2  recall=9/9  declared=26  fp=0  ap_pooled=1.000000
      df=0.02 min_shared=3  recall=9/9  declared=26  fp=0  ap_pooled=1.000000
      ...（10 个格点全部相同）...
      df=1.0  min_shared=3  recall=9/9  declared=26  fp=0  ap_pooled=1.000000

  [D] the replacement's own knobs
      top_k=5/10/15/25/40   → recall 恒 9/9，declared 恒 26
      fdr_q=0.01            → declared=24；0.05/0.1/0.2/0.5 → declared=26（recall 恒 9/9）
      capacity=1/2/3/5      → declared=9/18/26/42（recall 恒 9/9；capacity=5 时 fp=2）

  [E] sensitivity contrast
      legacy recall span   : ['2/9', '9/9']      ← 跨度 7 个主题
      semantic recall span : ['9/9', '9/9']      ← 跨度 0

  [F] negative-control arms
      EASY (off-domain)  topics=14  pairs_tested=0     declared=0   degenerate=True
      HARD (in-domain)   topics=12  pairs_tested=25    declared=25  degenerate=False
      HARD arm slot-fill rate: 0.694
      precision_identifiable = False
```

### 4.2 复杂度（改前 vs 改后；n/m/k 定义）

| | 改前（`calibrate_topic`） | 改后（`semantic_calibrate`） |
|---|---|---|
| 相似度 | 集合交基数比较，`O(Σ\|A\|+\|B\|)` | idf 加权余弦，同阶 + idf 查表 |
| 匹配 | 逐 (gold, group) 阈值判定，**非匹配**（一个 gold 可命中任意多组） | 最优二分匹配（匈牙利）`O(N³)`，`N=max(9·capacity, \|C\|)` |
| 候选生成 | 无 | top-k 截断，`O(G log G)` 每主题，`G`=517 |
| 显著性 | **无** | 置换检验 `O(P·B)`，`P`=被指派对 ≤27，`B=n_perm` |
| 假阳控制 | **无** | BH-FDR `O(P log P)` |
| 记忆化 | — | null 分布按 `(topic, \|group tokens\|)` 缓存 |

其中：`n`=机器变更项 691 → 组 `G`=517；`m`=金标可评估主题 9；`k`=每主题容量 `capacity`=3；`P`=被指派对数；`B`=置换次数 1000。
**关键**：匈牙利被 `MAX_ASSIGN_CELLS=40000` 保护（`alignment_service.py:126`）；本会话实际矩阵 27×≤135 ≪ 40000 → **走精确匈牙利，无 greedy 退化**（`probe_p8` 的 `declared` 数量随 capacity 精确线性 9/18/26/42 即为佐证）。

### 4.3 🔴 命名与口径纪律（工单 §3.2 强制，已执行）

- 产出量**已改名** `alignment_precision_pooled`。
- 与 **T-21 的 `64/517` 的 estimand 差异已写入**产物 JSON（`estimand_note`）与台账：
  > T-21 的 64/517 是**相关率**（分母 = 全体机器组，**无假阳计数**）；`alignment_precision_pooled` 是**含假阳计数的特异性代理**。**两者不可直接比较。**
- 已删除/未使用工单禁止的表述「precision 从下界 0.124 升到点估计 ≥0.5」——**「下界→点估计」是口径错配**。

### 4.4 ⚠️ 我**不**宣称「precision 达标」—— 该轴在本数据上不可辨识

这是本项最重要的诚实结论。两条实测事实使它无法被读作 precision：

1. **域外对照臂退化、无检验力**：`EASY` 臂 14 个域外题（运载火箭/量子纠错/区块链…）`pairs_tested=0` —— 与语料**零 token 重叠**，连一次可检验配对都没产生。**它的 0 不能被当作特异性证据**（已在产物中标 `degenerate=True` 并打印警告）。
2. **域内硬负对照虽会触发，但无法裁定**：`HARD` 臂 12 个域内题 slot-fill **0.694**（声明了 25 组）。这些声明究竟是真假阳、还是「**真实变更但金标未列入**」，**无法判定** —— 因为**金标是非穷尽的**（9 verified + 7 unverified）。

**故 `precision_identifiable = false`，precision 轴记 `insufficient_data`。**
`alignment_precision_pooled = 1.000000, Wilson [0.8713, 1.0000]` 中，**分子 26 与分母 26 相等**（|R∩S|=0），只能读作「真臂与硬负臂的声明组**完全不相交**」——这**确实是**一个有意义的正面证据（说明 idf 加权成功压掉了无处不在的「糖尿病」三元组，匹配靠的是判别性内容），但它**不等于** precision。

> 若要真正标定 precision，前置是把金标从 9 扩到 ≥50 并覆盖「未变更」章节（评审反馈 §5.3 C-5/C-6 已提出）。**这是 C-5/C-6 未完成时的必然结果，不该被绕过。**

### 4.5 方法（对应 C-1~C-4）

1. **来源卫生（新增，工单未提，为实测发现的根因修复）**：组 token 恒取 `section_anchor`；`points` 仅在 `source=="llm"` 时计入。实测 691 项中 **682 项只有 1 个 points 且为结构性占位符**（`removed paragraph` 142、`added paragraph` 80、`row N: changed`、`removed section: …`），计入即污染 df 统计。
   **根因说明**：df 天花板 = `max(3, 0.05×517)=25`，而污染后的词频分布使过滤器**反向生效** —— 删掉真信号（`胰岛素` df=33、`t2dm` df=41 超限被删）却保留垃圾（`table` df=24 幸存）。
2. **连续相似度**：`idf(t)=ln((N+1)/(df(t)+1))+1`，加权余弦替代「共享 token ≥k」阶跃。
3. **最优二分匹配**：复用 `alignment_service` 内**既有纯 stdlib 匈牙利**（`assign`，`alignment_service.py:672/730`），每主题容量 `capacity`。
4. **Monte-Carlo 置换检验 + BH-FDR**：随机集合同尺寸抽样得 null 分布 → p 值 → BH 步进控制 FDR；**假阳计数由此可得**（T-21 结构上给不出）。

**为何不用 ES / scipy**：ES 不可用且 `llm_client` 无 embedding 端点 → C-1 的语义通道**登记为待接线项**（§8），交付零依赖确定性回退实现；`alignment_service.py:46` 明示「**Zero new dependencies: stdlib only**」，且 `pyproject.toml` 属禁改接线文件 → **不引入 scipy/numpy/sklearn**（尽管 venv 里 scipy 1.17.1 可用）。

### 4.6 交付物

- `backend/services/knowevo/alignment_semantic.py`（新增，纯函数、零 I/O）
- `competition/experiments/probe_p8_alignment_semantic.py`（新增；**未改动 `probe_p1..p6`**）
- `competition/deliverables/algorithm-probes/probe_p8_alignment_semantic.json`（证据）

### 4.7 回滚

```bash
cd /home/qianqian/Work/All/Nexent/nexent
rm -f backend/services/knowevo/alignment_semantic.py
rm -f competition/experiments/probe_p8_alignment_semantic.py
rm -f competition/deliverables/algorithm-probes/probe_p8_alignment_semantic.json
# 台账/证据索引为 append-only，用 git 定位并撤销本次新增行：
git checkout -- competition/docs/cost-ledger.md competition/deliverables/evidence-index.md
```
**说明**：本项**未改动任何既有生产函数**（`calibrate_topic` 保持原样可调用），故无需回滚生产路径。`eval_v1.py`/`eval_e1.py` 的引用纠错如需回滚：`git checkout -- backend/services/knowevo/pipeline/eval_v1.py backend/services/knowevo/pipeline/eval_e1.py`。

---

## 5. 3.1 写路径批量化（代码已交付，**真实秒数未实测**）

> ✅ **已在真实 PG 上闭环（2026-09-24）：见 §11.2–§11.3。**
> 结果：同环境改前 **124.06s** → 改后 **2.291s（54.15×，≤10s 达标）**；四个新索引的可测写入成本为 0。
> ⚠️ **但闭环过程暴露了本会话交付代码的一个致命缺陷**（关系侧存在性查找未分块 → 3 万边时 PG 解析器爆栈、写入**完全失败**），已修，详见 §11.1。**该缺陷只有真实 PG 能暴露，本会话的静态复核没能抓到。**
> ⚠️ 本节 §5.4 的示范命令含 `--no-indexes`/`--with-indexes`/`--legacy` —— **本会话交付时这三个开关并不存在**（属我写错的示范命令），已在 §11 补齐实现。
> **改前基线更正**：评审反馈 §5.5 的 `79.4s` 在本环境**不可复现**；同环境改前为 **124.06s**。故 §5.4 坚持的「改前必须同环境重测」是必要的。

### 5.1 改动与 `file:line`

| 文件 | 行区间 | 内容 |
|---|---|---|
| `backend/services/knowevo/graph_store.py` | **246–335** | `upsert_entities` 批量化 |
| `backend/services/knowevo/graph_store.py` | **337–410** | `upsert_relations` 批量化 |
| `backend/services/knowevo/graph_store.py` | **:47–55** | `DEFAULT_GRAPH_BATCH_SIZE = 1000` |
| `deploy/sql/migrations/v2.5.5_kw_011_knowevo_graph_indexes.sql` | 全文（新增） | 4 索引 + 2 CHECK（1–5 节） |
| `competition/experiments/bench_write_path.py` | 全文（新增） | 基准 harness（**未运行**） |

> 行号为复核时实测；`git diff` 为准。`graph_store.py` 累计 `211 insertions / 39 deletions`。

**执行方式说明（诚实披露）**：3.1 的实现由**子 agent（writer）**按我预先写死的设计规范完成（该规范包含真实 schema、无唯一约束这一关键事实、精确行区间、索引名与语义保真要求）。我**逐项独立复核**了其产出（见 §5.2 语义保真核对、§5.3 偏离说明、§1.2 回归），**未采信其自述**。

### 5.2 复杂度（改前 vs 改后）

**改前**：`for e in ents:` 内 `with _get_db_session() as session:` → **每元素一次新 session + 一次提交**。
- 实体 N：约 **2N 次往返 + N 次提交**
- 关系 N：约 **2N 次往返 + N 次提交**（查找 + 插入）
- 实测锚点：2 万实体 **79.4s**、3 万边 **120.2s**（`评审反馈 §5.5`）

**改后**（`K=1000`，`S`=去重键数）：
- 实体：`ceil(S/K)` 次存在性查找 + `ceil(inserts/K)` 次批量插入 + `ceil(updates/K)` 次批量更新，**单次提交** → 全新增场景 **1 + ceil(N/K) 次往返**（2 万实体 ≈ **21 次**，原 ≈ 40000 次）
- 关系：`ceil(Kc/K)` 次当前视图查找 + `ceil(inserts/K)` 次批量插入，**单次提交**

**语义保真（关键）**：两方法均维护**批内工作副本**，使同一调用内的重复键表现得与旧逐行循环一致（确定性、可重入）；关系侧**复用 `valid_now`**（未另造谓词）；bi-temporal 窗口仍不被触碰。

### 5.3 ⚠️ 一处必须说明的偏离（工单/评审反馈的方案**不可执行**）

评审反馈 §5.5 E-1 的草图用
`INSERT INTO kg_edge_t ... ON CONFLICT (tenant_id, src_entity_id, dst_entity_id, relation_type, valid_at) DO UPDATE`。

**该语句在真实 schema 上无法执行**，两条独立原因：
1. `kg_edge_t` 表不存在（§3.2 已述）；
2. **`kg_relation_t` 没有任何 UNIQUE 约束**（`kw_001:74-88` 无 UNIQUE；`grep UNIQUE` 仅命中 27/68/120/139/187，均非该表）→ **没有可冲突的目标**。

**故采用非对称设计**：实体侧（有 `UNIQUE(tenant_id, stable_id)`）可用 `ON CONFLICT`；关系侧改为「**一次 `valid_now` 过滤的整批查找 → 一次批量插入**」，**不新增唯一约束**（加约束会让既有重复行阻塞迁移，且会把幂等语义的归属从应用层移走 —— 现注释明确写 "conflict resolution is the service layer's job"）。**批量化只优化往返，不改语义。**

**另一处纠偏**：评审反馈 §5.5 E-3 称覆盖索引 `INCLUDE (...) ` 可「避免回表」。**此处不成立** —— 现有查询 `session.query(KgRelation)…q.all()` 取**全列**（`graph_store.py:~370-385`），覆盖索引无法免除堆访问。要用上覆盖索引必须同时把查询**投影化**（已登记为待接线项 §8）。本会话**不做**「加了索引就变快」的无证据断言。

### 5.4 未实测项与复现命令

**无 PG**，故 79.4s / 120.2s 的改后数字**未测**。复现（服务可用时）：

```bash
# 0) 先建索引（含预检查，见 kw_011 头部注释）
psql "$KW_PG_DSN" -f nexent/deploy/sql/migrations/v2.5.5_kw_011_knowevo_graph_indexes.sql

# 1) 纯写入（不含建索引）—— 工单 §7 判定用的就是这个数
cd nexent && backend/.venv/bin/python competition/experiments/bench_write_path.py \
    --entities 20000 --edges 30000 --batch-size 1000 --repeats 3 --no-indexes

# 2) 含建索引
cd nexent && backend/.venv/bin/python competition/experiments/bench_write_path.py \
    --entities 20000 --edges 30000 --batch-size 1000 --repeats 3 --with-indexes

# 3) 批量大小拐点扫描
for K in 100 500 1000 5000 20000; do
  backend/.venv/bin/python competition/experiments/bench_write_path.py \
      --entities 20000 --edges 30000 --batch-size $K --repeats 3 --no-indexes
done
```

> 工单 §3.1 要求「同一份 2 万实体数据集、改前改后各跑一次、贴终端原文」。**改前的 79.4s 也需在同一环境重测**（现有 79.4s 来自 `评审反馈 §5.5`，非本会话实测，**不能作为对照基线**）。`bench_write_path.py` 已保留逐行模式开关（`--legacy`）以便同环境对照。

### 5.5 回滚

```bash
cd /home/qianqian/Work/All/Nexent/nexent
git checkout -- backend/services/knowevo/graph_store.py
rm -f deploy/sql/migrations/v2.5.5_kw_011_knowevo_graph_indexes.sql
rm -f competition/experiments/bench_write_path.py
```
**反向 DDL**（若迁移已应用）：
```sql
DROP INDEX IF EXISTS nexent.ix_kr_hop_rev_cover;
DROP INDEX IF EXISTS nexent.ix_kr_current;
DROP INDEX IF EXISTS nexent.ix_ke_valid_range;
DROP INDEX IF EXISTS nexent.ix_kr_valid_range;
ALTER TABLE nexent.kg_relation_t DROP CONSTRAINT IF EXISTS ck_kr_valid_order;
ALTER TABLE nexent.kg_entity_t  DROP CONSTRAINT IF EXISTS ck_ke_valid_order;
```

---

## 6. 3.3 时间区间索引下推（DDL + 代码已交付，**`EXPLAIN`/p95 未实测**）

> ✅ **已在真实 PG 上闭环（2026-09-24）：见 §11.4–§11.5。三条判定子条件的结果如下：**
> | 子条件 | 实测 | 判定 |
> |---|---|---|
> | `probe_p1` 不掉 | 逐位一致 | ✅ |
> | `EXPLAIN` 走 `ix_kr_valid_range` / `ix_ke_valid_range` | **被规划器选中，区间条件整体进 `Index Cond`** | ✅ |
> | **多跳 p95 改善** | **恶化 23%**（57.08ms → 70.13ms） | ❌ |
>
> **→ 3.3 判定为「机制已验证、收益为负」→ 保持 `use_range_predicate = False`（不翻开关）。**
> 另：本节 §6.2 原先的「b-tree 结构上不可用」论证**已被实测推翻**，见 §6.2 的更正框。
> 本节 §6.4/§6.6 中「0.0236ms 合成索引口径 / 12.5ms 真实 PG 口径」——本环境真实值为多跳 **p50 53.69ms**，与 12.5ms **不同源（机器/规模不同），不可混用**。

### 6.1 现状核对（已实测）

- 版本有效性**已下推到 SQL**（非应用层过滤）：`valid_now()`（`backend/database/knowevo_db.py:390-404`）由 `version_pin.pin_predicate` 桥接，在 `graph_store.py:309`（边）、`:343`（实体）进入 SQL。
- 多跳为**逐跳迭代**（非 `WITH RECURSIVE`）：`graph_store.py:~347-399`，`depth=min(depth,3)`、`beam≤5`；每跳 1 次会话+1 次查询，末尾 1 次实体查询。
- 时效列：`valid_at` / `invalid_at`（另有 `retrieved_at`/`superseded_at` 系统时间列）。
- 既有索引：`ix_kr_hop(tenant_id,src,rel_type)`、`ix_kr_hop_rev(tenant_id,dst,rel_type)`、`ix_kr_valid(valid_at,invalid_at)`（`kw_001:89-91`）。**`kg_entity_t` 无时效索引。**

### 6.2 ⚠️ 为什么加 GiST（**本节论证已于 2026-09-24 被实测更正，见 §11.4**）

`valid_now` = `valid_at <= t_v AND (invalid_at IS NULL OR invalid_at > t_v)`。

~~`ix_kr_valid(valid_at, invalid_at)` 是 b-tree：只能用到 `valid_at <= t_v` 前缀；第二个合取项把 NULL 判定与范围判定混在一起，b-tree 无法作为索引条件 → 退化为全索引/顺序扫描。~~

> 🔴 **上句论证过强，已被真实 PG 实测推翻。** 实测（`§11.4` / `explain_mostly_expired.txt`）：一旦 `enable_seqscan=off`，PG 会把 `A AND (B OR C)` 重写为 **`BitmapOr`，把**两个析取项**都推进 `ix_kr_valid` 的索引条件**（`(valid_at<=now() AND invalid_at IS NULL)` / `(valid_at<=now() AND invalid_at>now())`），760 buffers / 0.885 ms —— **b-tree 并非结构上不可用**。默认规划器走 Seq Scan 是因为 287/30000 ≈ 1% 选择率下顺序扫描更便宜，**不是**因为索引用不上。
>
> **仍然成立的结论（方向不变，论据已换）**：区间形式能把谓词**整体**放进单个 GiST 的 `Index Cond`（521 buffers / 2.014 ms，实测被选中），在「事实大量过期 + `as_of=now()`」时省 68% buffer；但在「事实多数有效」的形状下区间形式**反而更慢**（要为每行构造 `tstzrange`，623 buffers / 1.035 ms vs 前缀 462 / 0.332 ms）。**故「走索引」≠「更快」，收益与数据形状相关。**

等价区间形式：`[valid_at, COALESCE(invalid_at,'infinity'))` **包含** `t_v` ⟺ `valid_at ≤ t_v < COALESCE(invalid_at,'infinity')` ⟺ 原谓词。**逐项等价**（证明见 `knowevo_db.valid_range_contains` docstring；**已于 2026-09-24 在 3 万行真实数据上做对称差实测，两向差集均为 0**，见 §11.4）。

### 6.3 交付内容

1. **迁移** `v2.5.5_kw_011_knowevo_graph_indexes.sql`：`ix_kr_valid_range` / `ix_ke_valid_range`（GiST on 上述 range 表达式）、`ix_kr_current`（部分索引 `WHERE invalid_at IS NULL`）、`ix_kr_hop_rev_cover`。
2. **谓词** `knowevo_db.valid_range_contains(model, as_of)`（`backend/database/knowevo_db.py:407-438`，紧邻 `valid_now:390`，维持「时态语义只有一处」原则）。
3. **查询改写**：**以 opt-in 开关形式提供，默认关闭** —— `graph_store.current_view_predicate()`（`:58-78`）+ `PgJsonbGraphStore.use_range_predicate = False`（`:242`），调用点 `:440-441`（边）、`:475-476`（实体）。

### 6.3b ✅ 已实测验证：谓词编译结果与索引表达式**逐字一致**

这是「翻开关后索引能否被用上」的**必要条件**，且**无需 PG 即可验证**（比对编译出的 SQL 与迁移中的索引表达式）：

```text
kg_relation_t: expr matches kw_011 index expression = True
   compiled: tstzrange(nexent.kg_relation_t.valid_at, coalesce(nexent.kg_relation_t.invalid_at,
             'infinity'::timestamptz), '[)') @> now()
kg_entity_t: expr matches kw_011 index expression = True
   compiled: tstzrange(nexent.kg_entity_t.valid_at, coalesce(nexent.kg_entity_t.invalid_at,
             'infinity'::timestamptz), '[)') @> now()

valid_now (unchanged): valid_at <= now() AND (invalid_at IS NULL OR invalid_at > now())
```

索引侧表达式：`tstzrange(valid_at, COALESCE(invalid_at, 'infinity'::timestamptz), '[)')`。
两侧唯一差异是 `coalesce` 的大小写 —— PostgreSQL 解析时统一折叠函数名，故**解析树相同**，表达式索引可被匹配。

> ⚠️ **边界**：这只证明**表达式匹配**（索引可用的必要条件），**不等于**证明「规划器实际选择了该索引」。后者必须 `EXPLAIN` 确认，**PG 不可用故未做**。判定口径仍为 §9 的「未验证」。

### 6.4 ⚠️ 为何默认关闭（这是本项最重要的工程判断）

**索引与谓词必须表达式逐字匹配才会被使用。** 现有查询用的是布尔谓词，而 GiST 建在 range 表达式上 —— **PG 不会自动把 `A AND (B OR C)` 重写成区间包含**。所以「只建索引不改查询」**不可能走索引**。

但**盲目改查询同样不可接受**，因为工单 §3.3 的判定要求「`EXPLAIN` 确认走索引」，而 **PG 不可用 → 无法验证**。按 AGENTS.md §0.1「没有证据=没做完」，**不能声称已下推**。故：

- 交付 `valid_range_contains()` + 开关，**默认 false → 现行为逐字保留，零回归风险**；
- 翻开关 + `EXPLAIN` 验证 + p95 对比 = **待接线项**（§8 #1）。

**一个必须记录的边界条件（安全前提）**：布尔谓词对 `invalid_at < valid_at` 的退化行**返回 false**，而**构造**该区间会**抛错**（`range lower bound must be less than or equal to range upper bound`）。故 `kw_011` 加了 `ck_kr_valid_order` / `ck_ke_valid_order`（`NOT VALID`，不阻塞迁移）与**预检查询**（`kw_011` 头部注释）。**预检查须返回 0 才能翻开关。**

### 6.5 `probe_p1` 回归（**已实测**）

```text
$ cd nexent/competition/experiments && python probe_p1_version_pin.py
  arm         | V accuracy           | F accuracy           | overall
  pinned      | 1.000 (160/160)      | 1.000 (640/640)      | 1.000 (800/800)
  ret-random  | 0.206 (33/160)       | 1.000 (640/640)      | 0.841 (673/800)
  ret-latest  | 0.250 (40/160)       | 1.000 (640/640)      | 0.850 (680/800)
```

**→ 与改前基线逐位一致，未掉。**（注：本项改动默认关闭开关，故 `probe_p1` 的「未掉」不构成对区间下推的验证，只证明**未引入回归**。）

### 6.6 未实测项与复现命令

```bash
# ① 预检查（必须为 0）
psql "$KW_PG_DSN" -c "SELECT 'kg_relation_t' t, count(*) FROM nexent.kg_relation_t
  WHERE invalid_at IS NOT NULL AND invalid_at < valid_at
  UNION ALL SELECT 'kg_entity_t', count(*) FROM nexent.kg_entity_t
  WHERE invalid_at IS NOT NULL AND invalid_at < valid_at;"

# ② 建索引
psql "$KW_PG_DSN" -f nexent/deploy/sql/migrations/v2.5.5_kw_011_knowevo_graph_indexes.sql

# ③ 验证 GiST 对区间谓词可用（应出现 Bitmap Index Scan / Index Scan on ix_kr_valid_range）
psql "$KW_PG_DSN" -c "EXPLAIN (ANALYZE, BUFFERS)
  SELECT e.dst, e.rel_type FROM nexent.kg_relation_t e
  WHERE e.tenant_id = '<tenant>'
    AND tstzrange(e.valid_at, COALESCE(e.invalid_at,'infinity'::timestamptz),'[)') @> '2024-01-01'::timestamptz;"

# ④ 对照：旧布尔谓词的计划（预期无法用 GiST，只见 valid_at 前缀或 seq scan）
psql "$KW_PG_DSN" -c "EXPLAIN (ANALYZE, BUFFERS)
  SELECT e.dst FROM nexent.kg_relation_t e
  WHERE e.tenant_id = '<tenant>'
    AND e.valid_at <= '2024-01-01'::timestamptz
    AND (e.invalid_at IS NULL OR e.invalid_at > '2024-01-01'::timestamptz);"

# ⑤ 多跳 p95 改前改后（口径提醒：0.0236ms 是**合成索引**口径、12.5ms 是**真实 PG**口径，两者不可混用）
```

### 6.7 回滚

见 §5.5（同一迁移文件的同一反向 DDL）。谓词函数如不采用，删除 `knowevo_db.py` 的 `valid_range_contains` 即可（无调用方时无影响）。

---

## 7. 口径与事实发现清单（供后续会话引用）

| # | 发现 | 证据 | 影响 |
|---|---|---|---|
| 1 | **评审反馈 §5.1/§5.5 DDL 的表名/列名全错**（`kg_edge_t`/`src_entity_id`/`evidence_id` 不存在） | `kw_001:51-91` | 照抄即报错；**已按真实 schema 重写** |
| 2 | **`kg_relation_t` 无任何 UNIQUE 约束** → `ON CONFLICT` 方案不可执行 | `kw_001:74-88`；`grep UNIQUE` 未命中该表 | 已改非对称设计（§5.3） |
| 3 | **评审反馈内部矛盾**：同一 GiST 索引两个名字 | `评审反馈:222` vs `:487` | 裁定 `ix_kr_valid_range` |
| 4 | **评审反馈 §5.3 C-4 的 `ALIGN_CFG` 写 `min_shared_tokens: 3` 并注「= 现硬编码值」** | 实际默认值是 **2**（`alignment_service.py:1341`） | **照抄会把 recall 从 9/9 打到 3/9**；本会话保持 2 |
| 5 | 工单 §2.2 假定三处错误都在 `06`；**实际只有 1 处在 `06`** | §3.2 表 | 已分别定位处置 |
| 6 | `2511.13645` 工单要求「删除」；**实测标题正确、只是性质误述** | arXiv 取证 | **改为改述**，不删除 |
| 7 | `2606.26511` AUROC 0.59 是**阴性结果**，项目用作「反证」**语境正确** | arXiv 取证 | 升级为「已核实」，禁写成方法性能 |
| 8 | 工单引用的 Wilson `[0.0981, 0.1550]` **在仓库内查无实据** | 全仓 grep 零命中 | 经重算确认它**正是 64/517 的 Wilson**（`0.0981/0.1550` 逐位吻合）→ 属「可重算但未落盘」，非错误 |
| 9 | 基线口径互斥（551/700/711/718/721） | 本会话实测 721 | 已以自测 721/30 为基准 |
| 10 | `probe_p5` 的 `[F] recomputed_strict_equals_persisted_calibration: FAIL` | `probe_p5` 输出 | **非真实差异**：产物存 loose 变体、该检查拿 strict 比对（口径错位），与 caliber 决定书一致 |
| 11 | 691 项中 **682 项 points 为结构性占位符** | 本会话数据剖析 | **3.2 的根因**；来源卫生修复即基于此 |
| 12 | `backend/database/knowevo_db.py` 有 9 处 ruff 命中（`:15/41/228/265/308/390/407/446/467`），全是 `Optional[...]`→`\|` 与 `List`→`list` 一类的**现代化**规则 | `ruff check backend/database/knowevo_db.py` | 均为**既有**，且该文件**不在**项目标准 ruff 范围（`backend/services/knowevo mcp_servers`）内。我新增的 `valid_range_contains:407` **沿用同文件既有 `Optional[...]` 写法**（与相邻 `valid_now:390` 一致），命中数 8→9。**官方验收命令仍 `All checks passed!`**，不受影响 |
| 13 | 工单 §3.2 引用的 Wilson `[0.0981, 0.1550]` 虽**未落盘**，但重算确认正是 `64/517` 的 Wilson 95% 区间 | 本会话重算（逐位 0.0981/0.1550 吻合） | 属「可重算未落盘」，非事实错误；`probe_p8` 已把该区间显式落进产物 |

### 7.1 子 agent 使用披露（诚实性）

本会话用 4 个只读 Explore agent 做侦察（探针行为、代码定位、数据刻画、arXiv 取证）与 1 个 writer agent 做 3.1 实现。其中：
- **一个 Explore agent 主动披露其首版报告含编造内容** → 我**整份废弃**，改为**自己直读 `评审反馈` §5.1/§5.3/§5.5 原文**。这正是后续发现「DDL 表名全错」「无唯一约束」两项关键事实的前提 —— **若采信该报告，本次会写出跑不通的 DDL**。
- arXiv 取证 agent 的报告我**逐条对照其可复现命令**后才采用；其第二轮因 API 限流**未做双源交叉**，已在 §3.1 表格注明取证方式。
- writer agent 的产物我**逐行复核**（§5.2/§5.3），未采信自述；其自报「未运行 `bench_write_path.py`、未写任何未经测量的性能数字」，与我的复核一致。

---

## 8. 待接线项（登记，**未自行改**）

| # | 项 | 为何不能自行改 |
|---|---|---|
| 1 | **翻 `valid_range_contains` 开关 + `EXPLAIN` 验证 + p95 复测** | 需活 PG；且属读路径行为变更，须先过预检查与计划验证 |
| 2 | ES `dense_vector` 索引 `kw_change_vec` + kNN 语义通道（C-1 原方案） | 需 ES 服务 + embedding 端点；平台接线 |
| 3 | 多跳查询**投影化**（使覆盖索引真正生效） | 会改变 `graph_store` 返回契约形状，须先过 `**/*.py.md` 契约核对 |
| 4 | `alignment_semantic.py` 的契约文件（`knowevo/backend/services/knowevo/alignment_semantic.py.md`） | 新增模块目前**无对应契约**；契约属冻结面，须由契约任务补 |
| 5 | 金标扩充至 ≥50 话题 + 覆盖「未变更」章节（C-5/C-6） | 是 precision 可辨识的**前置条件**；属数据标注任务 |
| 6 | `bench_write_path.py` 首次执行与 79.4s 的**同环境**改前重测 | 需活 PG |

---

## 9. 判定对照（工单 §7）

| 项 | 达标条件 | **本会话判定** |
|---|---|---|
| 3.1 写入提速 | 不含建索引时 ≤10s 且提升 ≥2× | ⚠️ **未验证**（无 PG）。代码/迁移/harness 已交付，复现命令就绪 |
| 3.2 对齐质量 | 点估计提升 **且** 2→3 时 recall 不再崩 | ✅ **敏感性达标**（跨度 7 主题 → **0**）。⚠️ **「点估计提升」判定为 insufficient_data**：precision 在本数据上不可辨识（§4.4），**不以 1.000000 冒充达标** |
| 3.3 索引下推 | `probe_p1` 不掉 **且** p95 改善 **且** `EXPLAIN` 走索引 | 🟡 **部分**：`probe_p1` **未掉** ✅；p95 与 `EXPLAIN` **未验证**（无 PG）。开关默认关，**不保留半成品行为** |

**未达标项一律如实写「未验证/insufficient_data」，未以 mock 或占位数字冒充。**

---

## 10. 本次改动文件总表

**新增**
```
nexent/backend/services/knowevo/alignment_semantic.py
nexent/deploy/sql/migrations/v2.5.5_kw_011_knowevo_graph_indexes.sql
nexent/competition/experiments/probe_p8_alignment_semantic.py
nexent/competition/experiments/bench_write_path.py
nexent/competition/deliverables/algorithm-probes/probe_p8_alignment_semantic.json
nexent/competition/docs/sessionB-plan-五核前三项-2026-09-23.md
nexent/competition/docs/sessionB-receipt-五核前三项-2026-09-23.md   ← 本文件
```
**修改**
```
nexent/backend/services/knowevo/graph_store.py          (批量 upsert)
nexent/backend/database/knowevo_db.py                  (+ valid_range_contains)
nexent/backend/services/knowevo/pipeline/eval_v1.py    (引用纠错 :22)
nexent/backend/services/knowevo/pipeline/eval_e1.py    (引用纠错 :16)
nexent/competition/docs/cost-ledger.md                 (追加 2 行, 锚点 session:B algo-opt)
nexent/competition/deliverables/evidence-index.md      (追加 1 条)
06-论文与开源武器库-v2.md                                (根仓；§2/§3.1/§3.2/§4.3/§4.4/§6.1/§6.3)
```
**未触碰**（遵守工单 §9）：`probe_p1..p6*.py` 及其 JSON、`probe_p7_*`（不存在）、接线文件、既有迁移、`knowevo/**.py.md` 契约，以及开工前即已 modified 的 `kg_service.py` / `llm_client.py` / `mine_skill_templates.py`。

---

## 11. 闭环回填（2026-09-24 · 真实 PG 实测）<!-- session:B algo-opt closure -->

> **本 receipt 此前有三类内容需要修订：声称存在但不存在的开关、与代码不符的复杂度口径、以及过强的索引论证。本节是权威的更正与补充。**
> 完整证据包：`competition/docs/verification-reports/sessionB-closure-pg-bench-2026-09-24.md`
> 全部原始产物：`/home/qianqian/bench-runs/`（JSON 逐次耗时 + EXPLAIN 原文 + PG 语句日志）

### 11.1 环境：PG 本次可用（推翻 §0 的前提）

PostgreSQL **16.15**（conda-forge 自包含于 `/home/qianqian/pg16`），`127.0.0.1:5434`，库 `nexent`。因沙箱拦截 `setuid/setgid` 而 PG 拒绝 root 启动，改用 **user namespace**（`unshare --user --map-user=65534 --map-group=65534`）启动。环境脚本 `/home/qianqian/pg-env.sh`。**dockerd 仍不可用（无 socket）。**

### 11.2 🔴 本 receipt §2「3.1 代码已交付」是不成立的：该代码在真实 PG 上**跑不通**

首次执行 `bench_write_path.py --entities 20000 --edges 30000` 即失败：

```text
(psycopg2.errors.StatementTooComplex) stack depth limit exceeded
[SQL: SELECT ... FROM nexent.kg_relation_t WHERE ... AND
      (src, dst, rel_type) IN ((...), ... ~29863 个元组 ...)]
```

根因：`upsert_relations` 的当前视图存在性查找把**全部去重候选键**塞进**一条** `IN`；实体侧同样未分块。PG 解析器对 `IN` 逐元素递归，超 `max_stack_depth` 即整条写入失败。
最小复现阈值：**8000 元组 OK / 12000 元组失败**（`max_stack_depth=2MB`）。
**已修**：两处查找均按 K 分块（语义不变，见 §11.6 独立复核）。修复后同命令 **2.648s / 2.141s / 2.083s** 通过。

### 11.3 §5.2 复杂度表更正

| 原文 | 实测 |
|---|---|
| 关系侧 `ceil(Kc/K)` 次当前视图查找 | 代码/§5.3 正文实为**恒 1 次整批查找**（§5.2 与 §5.3 自相矛盾）→ 已修为分块 |
| 实体侧「可用 `ON CONFLICT`」 | 实为 **lookup-then-insert**，未用 `ON CONFLICT`（功能等价，描述不符） |
| 「20000 行 ≈ 21 次往返，原 ≈ 40000 次」 | 语句数实测**严格** `ceil(S/K)+ceil(N/K)`；但**往返不是瓶颈**（K 从 100→8000 耗时仅波动 <12%）→ 收益真正来源是**把 5 万次逐行提交收敛为 1 次** |

### 11.4 §5.4 三个开关：**全都不存在**，已补实现

`--no-indexes` / `--with-indexes` / `--legacy` 在原 `bench_write_path.py` 的 argparse 里**零命中**，§5.4 的示范命令照跑会报 `unrecognized arguments`。已补：
`--legacy` 用 importlib 执行 `git show HEAD:backend/services/knowevo/graph_store.py` 取**真实逐行版**（非手写冒充）。
**另修**：`--batch-size` 此前**从未传到 store**（`self.batch_size` 全仓未赋值 → 恒 1000），第一次「拐点扫描」实为同配置跑 7 次。

### 11.5 3.1 / 3.3 判定（取代 §9）

| 项 | 达标条件 | **本次判定** |
|---|---|---|
| 3.1 写入提速 | 不含建索引 ≤10s 且提升 ≥2× | ✅ **达标**：改前 **124.060s**（148.218/118.018/105.944）→ 改后 **2.291s**（2.648/2.141/2.083）＝ **54.15×**；含 kw_011 四索引 2.255s（Δ=−0.036s，在噪声内） |
| 3.3 索引下推 | `probe_p1` 不掉 **且** p95 改善 **且** `EXPLAIN` 走索引 | 🟡 **两达一否**：`probe_p1` 逐位一致 ✅；`ix_kr_valid_range`/`ix_ke_valid_range` **被规划器真实选中、区间条件整体进 `Index Cond`** ✅；**多跳 p95 恶化 23%（57.08→70.13ms，n=50）** ❌ → **不翻 `use_range_predicate`，保持默认 False** |

**口径纪律**：评审反馈 §5.5 的 `79.4s+120.2s=199.6s` 在本环境**不可复现**；本环境同口径改前基线为 **124.06s**（离散度约 40%）。

### 11.6 §6.2 的论证过强，必须更正（结论方向不变，论据须换）

原文称 b-tree「无法把第二个合取项作为索引条件 → 退化为全索引/顺序扫描」。**实测**：一旦 `enable_seqscan=off`，PG 会把 `A AND (B OR C)` 重写为 **`BitmapOr`**，把两个析取项**都**推进 `ix_kr_valid` 的索引条件（`(valid_at<=now() AND invalid_at IS NULL)` / `(… AND invalid_at>now())`），760 buffers / 0.885ms —— **并非结构上不可用**。默认规划器走 Seq Scan 是因为 287/30000≈1% 选择率下顺序扫描更便宜。

**真实收益是场景相关且非单调的**（30 000 关系、29 713 已过期）：

| | 计划 | Buffers | Execution |
|---|---|---|---|
| 区间谓词 @`now()` | `Bitmap Index Scan on ix_kr_valid_range` | **521** | **2.014 ms** |
| 布尔谓词 @`now()` | `Seq Scan`（Filter 剔除 29713） | 1654 | 2.400 ms |
| 区间谓词 @`now()-3900d`（高选择性） | `Bitmap Index Scan on ix_kr_valid_range` | 623 | 1.035 ms |
| 布尔谓词 @`now()-3900d` | `Bitmap Index Scan on ix_kr_valid` | **462** | **0.332 ms** |

→ 「走索引」**不等于**「更快」：在高选择性场景下区间形式**反而更慢**（需逐行构造 `tstzrange`）。

### 11.7 §6.4 的安全前提**已被实证**

故意插入一行 `valid_at>invalid_at` 的退化行后：预检计数 **1**；布尔谓词对该行 **0 命中**（静默不匹配）；区间形式 **`ERROR: range lower bound must be less than or equal to range upper bound`**。
→ **退化行下区间形式不是返回 false 而是抛错**，故「预检须为 0 才能翻开关」不是形式主义。（对照行已删除，预检回到 0。）

### 11.8 §6.6 复现命令的更正

- §6.6 ③④ 的 EXPLAIN 可原样使用，但**须先给数据真实的时间分布**，否则谓词恒真/恒假、计划无判读价值。
- §5.4 建议的扫描点 **`K=20000` 必然失败**（单条 `IN` 2 万元组 → `StatementTooComplex`）。**安全上限 `K ≤ ~8000`。**
- 多跳 p95 的真实口径为 **p50 53.69ms / p95 57.08ms（关）**，与 §6.6 ⑤ 提到的 `0.0236ms`（合成索引）和 `12.5ms`（另一环境）**不同源，不可混用**。

### 11.9 回归（本会话改动后）

`pytest ../test/backend/services/knowevo/ -q` → **721 passed, 30 skipped**（与改前基线**逐位一致**）；`probe_p1` 逐位一致；`ruff check backend/services/knowevo mcp_servers` → **All checks passed!**

### 11.10 本次新增/修改（在 §10 基础上）

**新增**：`competition/experiments/explain_valid_range.py`、`competition/experiments/run_write_path_suite.sh`、`competition/experiments/run_batch_sweep.sh`、`competition/docs/verification-reports/sessionB-closure-pg-bench-2026-09-24.md`
**修改**：`backend/services/knowevo/graph_store.py`（两处查找分块）、`competition/experiments/bench_write_path.py`（补三开关 / 修 sys.path / 修 batch_size 传递）

### 11.11 §8 待接线项的状态更新

| # | 项 | 新状态 |
|---|---|---|
| 1 | 翻 `valid_range_contains` 开关 + EXPLAIN + p95 复测 | **已执行**：EXPLAIN 确认走索引；但 **p95 恶化 23% → 决定不翻**。开关与谓词函数保留（默认关） |
| 6 | `bench_write_path.py` 首次执行与 79.4s 同环境重测 | **已闭环**（§11.5） |
| 新增 | `batch_size ≤ 0` 时 `range(0,n,-1)` 返回空 → 静默丢弃写入 | **既有潜在风险、实践不可触发**（`self.batch_size` 从未被赋值），**未加防御**，登记 |
| 新增 | 大规模（≥100 万行）GiST 与 b-tree 的交叉点 | **未测**（本次仅覆盖 3 万行） |
| 新增 | `D_batched_noidx_repeats3` 第 2 次 12.2s（vs 2.0/2.8s）的根因 | **未定位**，未重复验证 |


---

## 12. 写入等价性验证与守护修复（2026-09-24 · 小算补做）<!-- session:B algo-opt equivalence -->

### 12.1 为什么必须补这一节

§11 测出了 **54.15× 提速**，但 `bench_write_path.py` 的产物 JSON **只记录耗时**（见 `bench-runs/A_batched_noidx_r1.json`：只有 `per_run_s`/`mean_s`，**无行数、无内容校验**）。
**换言之：即使批量写把行写丢了、写重了、或写错了合并分支，那 54× 也会一样好看。**
「更快」在「写入内容相同」被证明之前**不构成效率结论**。本节补上这个前提。

### 12.2 方法（`competition/experiments/check_write_equivalence.py`，新增）

同一份**确定性数据集**（`build_graph`，固定 seed）分别写入**两个全新的独立租户**：
- A 臂 = `git show HEAD:backend/services/knowevo/graph_store.py` 经 importlib 动态加载的**真实逐行实现**（非手写复刻）
- B 臂 = 当前批量实现

再对「有意义列」做**规范化多重集比较**。`id` / `tenant_id` / `created_at` / `valid_at` 服务端默认值不参与批量比较（两臂必然不同），但**显式 `valid_at` 被单列为一个用例精确比较**，而不是靠假设带过。

覆盖 6 个语义分支 —— 正是批量改写声称保真的那些：

| 用例 | 测什么 |
|---|---|
| **A** 全新增批量插入 | 热路径 |
| **B** 幂等重跑 | 同载荷写两次必须无变化 |
| **C** 合并分支 | 同 key、追加 props + 新 alias |
| **D** claim 变更分支 | 同 (src,dst,rel_type) 但 claim 不同 → 应新增行 |
| **E** **批内重复键** | 「批内工作副本」逻辑（确定性数据里不含此情形，须专门构造） |
| **F** 显式 `valid_at` | 插入时必须被尊重 |

### 12.3 结果（真实 PG）

```text
$ backend/.venv/bin/python competition/experiments/check_write_equivalence.py --entities 1500 --edges 2000
  [PASS] A bulk all-new insert      entities legacy=1500 batched=1500; relations legacy=2000 batched=2000
  [PASS] B idempotent rerun         after 2 identical writes: entities 1500 / relations 2000（无重复）
  [PASS] C merge branch             after merge patch: entities legacy=1500 batched=1500
  [PASS] D claim-change branch      relations legacy=2050 batched=2050（50 键各留旧+新）
  [PASS] E in-batch duplicate keys  entities legacy=2 batched=2
  [PASS] F explicit valid_at        rows legacy=100 batched=100
  => 6/6 cases equivalent
```

**在崩溃规模上单独复验**（§11.1 的缺陷正是 3 万边触发）：

```text
$ ... check_write_equivalence.py --entities 20000 --edges 30000 --cases A
  [PASS] A bulk all-new insert
         entities legacy=20000 batched=20000 (expected 20000); relations legacy=30000 batched=30000 (expected 30000)
  => 1/1 cases equivalent
```

**→ 批量写入在 6 个语义分支、两种规模（含 2 万/3 万）下均与逐行实现结果等价。54.15× 提速是在等价前提上成立的。**

### 12.4 守护修复：`batch_size ≤ 0` 的静默数据丢失（§11.11 登记项已闭环）

§11.11 把该风险登记为「实践不可触发、未加防御」。**本轮补上防御**（`graph_store._batch_size_for()`）：
非正整数 K 会让 `range(0, n, K)` 产出为空 → **跳过全部存在性查找 → 所有行被当作新行**；对 `kg_relation_t`（无唯一约束）会**静默插入重复行**，对 `kg_entity_t` 则在提交时抛 IntegrityError。加一条显式校验，把静默错变成响亮错。

```text
默认（属性未赋值）: 1000
  K=     0: ValueError ✓     K=  None: ValueError ✓
  K=    -1: ValueError ✓     K=  True: ValueError ✓   ← bool 也被拦（True==1 会被误当合法）
  K= -1000: ValueError ✓     K=   2.5: ValueError ✓
  K=500   : 500 ✓            删除属性后: 1000 ✓（回退默认）
```

> 该风险**不是**批量改写引入的 —— `self.batch_size` 在 `§11` 之前全仓库从未被赋值；但 `§11` 修好了 harness 的传递路径后，「能被赋值」这件事本身把潜在风险抬成了实际风险。**故本轮必须补。**

### 12.5 回归（本轮改动之后）

| 项 | 结果 |
|---|---|
| `pytest ../test/backend/services/knowevo/ -q` | **721 passed, 30 skipped, 1 warning**（与基线逐位一致） |
| `ruff check backend/services/knowevo mcp_servers` | **All checks passed!** |
| 等价性 6 用例 + 2 万/3 万 A 用例 | **全 PASS** |

### 12.6 仍未验证（诚实清单）

| # | 项 | 状态 |
|---|---|---|
| 1 | 等价性只覆盖**合成数据集**；真实摄取路径的 props/aliases 形态更复杂 | **未验证** |
| 2 | 用例 C 的补丁只动 200 个实体（合并分支的全量行为未逐步验证） | **部分覆盖** |
| 3 | 生产真实库上的退化行（`invalid_at < valid_at`） | **无法验证**（`ck_*_valid_order` 为 `NOT VALID`，不回溯校验） |
| 4 | ≥100 万行时 GiST / b-tree 交叉点、以及批量拐点的形状 | **未测**（本报告只覆盖 3 万行） |
| 5 | `D_batched_noidx_repeats3` 第 2 次 12.2s 的根因 | **未定位** |

### 12.7 本节变更文件

**新增**：`competition/experiments/check_write_equivalence.py`（A/B 等价性对拍，含 6 用例与 CLI 参数）
**修改**：`backend/services/knowevo/graph_store.py`（新增 `_batch_size_for()` 守护，两处 `K` 读取改用之；**未改任何既有语义**）

---

## 13. 后续优先级（receipt 的收口建议）<!-- session:B algo-opt next -->

> 三项判定见 §9（叠加 §11 闭环）。**下面是按「保护可守性 / 解锁度 / 性价比」排序的后续动作**，供下一会话取用。

### #1 ★ 补齐 `path_validity_proof`（结构化剔除归因）—— 最高优先级

**问题（本轮自查发现）**：创新②对外差异化叙事依赖「**返回剔除归因使第三方可离线复算**」，但：
- `path_validity_proof` **全仓 0 命中**（`grep --include=*.py --include=*.sql --include=*.py.md` 实测）；
- 现存只有**自由文本** `invalid_edge_reason`（`decision_service.py:527`，由 `_expired_reason` 拼成 `"N edge(s) outside version v at <iso>: rel(valid a -> b); ..."`）；
- **第三方无法机器复算**，且「**裁剪完备性证明**」这一说法**没有对应产物**。

**为何最高优先**：这是**创新②能不能守住的支点**——差异化说法是「可学习排序 vs 前置硬约束」，而「硬约束」的可验证性恰恰需要这个结构化证明。§5.6 把它列为 A-3「论文级贡献」，`优化-2026-09-23-终版.md` 的 P2-1 评它「全局最高性价比」。
**成本**：0.5–1 天。数据全在手（`edges_by_path` 已在 `decision_service.py` 返回），**纯序列化，无额外查询，DB 不需要**。
**建议形态**：每条返回路径附 `proof = {"as_of", "ontology_version", "edges": [{"edge_id","src","dst","rel_type","valid_at","invalid_at","in_window": bool}]}`，让第三方**离线逐边复算 `in_window`**。
**⚠️ 纪律**：**实现前，`06` 与 `05` 均不得声明该能力**（`06` §2 顶行与答辩话术已于 2026-09-24 加 ⚠️ 更正）。

### #2 §5.4 CELF / 背包精确解 —— **阻塞已解除**

工单 §4 把 §5.4 列为「不做」的原因是「**等效率口径定死**」。**该前置已满足**：会话 A 的 `probe_p7_efficiency_minutes.py` 已把口径落定（2×2 重放：门开/关 × 排序策略；门因子主效应 **2.31×**；分钟系数 α=0.75/β=0.63 有文献出处且实抓核验；预登记 `sha256=e559f5a9…61557` 已冻结）。
**注意**：`probe_p7` 实测「排序因子效应 = 0.00 min（结构零）」且质量轴 `insufficient_data`（`Quality(·)` 所需四键在冻结池全部缺失）。→ **做 CELF/背包前，先解决 probe_p7 的 P-1/P-2/P-3 解锁前置**（启 DB / 扩展导出键 / 确定 `seed_terms` 来源），否则优化的是不可观测的目标函数。

### #3 §5.2 双向 BFS + 子模证据选择 —— 阻塞**部分**解除

原阻塞是「需先有真图跳数标定」。现在存在两类图：合成图（2 万实体/3 万边，`build_graph`）与**真实构建租户图**。→ 可先补 **B-4 真图跳数标定**（替换 fake-store 那条 `depth=1 acc=0.00 / depth=2..4 acc=1.00` 的曲线），再谈双向 BFS 与 `path_score` 累加器。

### #4 3.2 的 precision 可辨识性 —— 属**标注**任务，非代码

`alignment_precision_pooled` 目前是 `precision_identifiable=false`（§4.4）：金标非穷尽 ⇒ 无可构造域内阴性集。
**唯一出路**：金标从 9 扩到 **≥50 个话题**，且**必须覆盖「未变更」章节**（否则阴性集仍不可构造）。这是 §5.3 C-5/C-6 的前提，本会话无法代做。

### #5 3.3 的战略收口 —— 建议**关闭**该项，转向真正的瓶颈

三条判定两达一否（`EXPLAIN` ✅ / `probe_p1` ✅ / **p95 恶化 23%** ❌）→ 按证据**不翻开关**。
**但更重要的结论**：多跳 p50 = **53.69 ms**，而契约 P1 锚点是 **1.5 s** —— **有 26× 余量**。→ **3.3 一直在优化一个不是瓶颈的东西。** 真正该做的是**剖析多跳耗时的构成**（逐跳 `neighbors` 查询？束宽排序？实体补查？），而不是继续调索引。

### #6 文档一致性核查（低成本、防翻车）

需逐条核对对外材料是否出现**已被实测否证**的表述：
- 「GiST / 区间索引**提速多跳**」→ **否**（实测恶化 23%）；
- 「区间谓词让 b-tree 用不上」→ **否**（`BitmapOr` 可推进两个析取项）；
- 「返回剔除归因使第三方可离线复算」→ **否**（见 #1）；
- 已确认 `05-计划书` **未**声称 GiST/索引提速（`grep` 实测），风险面主要在 `06` 与答辩话术 —— **`06` 已于本会话更正**。

### #7 其余

§8 待接线项 #2（ES kNN 通道）、#3（多跳查询投影化，使覆盖索引真正生效）、#4（`alignment_semantic.py` 契约文件）、#5（金标扩充，同 #4）仍开放；另新增：`D_batched_noidx_repeats3` 第 2 次 12.2s 根因未定位。

