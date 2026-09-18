# T-18d：D4 权威度感知检索 + 每文档配额（BM25 不再丢 authority_level）

**状态**: ✅ 完成（2026-09-18）
**Blocked by**: 无
**独占文件**（本任务创建/修改）:
- `backend/services/knowevo/e1_retrieval.py`
- `test/backend/services/knowevo/test_e1_retrieval.py`（更新+新增）
- `competition/docs/pitfalls.md`（补记）

**待接线项**: 无
**禁改清单**: `backend/constants/*`、`pipeline/eval_e1.py`（T-18c 独占）、`ingest_service.py`（T-18b 独占）、`deploy/sql/migrations/*`、registry.csv（T-18b 独占加列）
**允许的新依赖**: **无**（authority 加权与配额均为纯标准库实现）

**要构建的行为**（用户视角端到端）:
1. BM25 检索排序**纳入 `authority_level`**：高权威文档（国家级标准=1、指南=2）在分数相近时优先于低权威（说明书=3、科普图文=4）；
2. **每文档配额**：top-k 结果里同一 `doc_id` 最多占 `per_doc_quota` 个名额（默认 2），避免单份说明书刷屏挤掉指南；
3. 检索接口保持**向后兼容**：`Retriever.search(query, top_k, splits)` 签名与返回 `list[Hit]` 不变；新增加权与配额为可选参数（默认值 = 开启权威度加权 + 配额 2）；
4. **判据**：构造"低权威文档 BM25 分数更高但高权威文档更该被选"的夹具，配额与加权各有一个可观测断言。

**背景（现状核验，2026-09-18）**:
- `e1_retrieval.py:302-333` `Retriever.search`：索引只 tokenize `title + text`（:289）；打分 `score += idf*(f*(k1+1))/denom`（:329）；`scored.sort(key=lambda h: h.score, reverse=True)`（:332）后 `scored[:top_k]`（:333）。**`authority_level` 从不进打分，也无任何按 doc 去重/配额**。
- `Chunk.authority_level`（:58）已存在，`build_chunks`（:227-241）从 `CorpusDoc` 复制（:238），`CorpusDoc.authority_level` 从 registry 读（:212，`authority_level=int(row.get("authority_level") or 3)`）。
- `retrieve_context`（:336-366）在输出 evidence 里带 `authority_level`（:360），但**排序阶段没用它**。
- registry 真值分布：authority 1×18 / 2×11 / 3×28 / 4×1；doc_type：drug_label 28 / guideline 11 / policy 9 / edu_graphic 8 / lab_report 2。
- 已知后果：V-001 实测"BM25 把《2020 版指南》排 #1、《2024 版》排 #3-4"（T-10a-2 简报失分归因）——版本敏感题失分部分源于此。

**验收命令**:
```bash
# 1. 单测（权威度加权 + 配额 + 向后兼容）
cd backend && uv run pytest ../test/backend/services/knowevo/test_e1_retrieval.py -q --no-header
# 2. ruff
cd backend && uv run ruff check services/knowevo/e1_retrieval.py
# 3. ★检索对比真跑（同一 query，加权/配额开 vs 关，看 top-5 doc 分布）
cd backend && uv run python -c "
from services.knowevo.e1_retrieval import parse_corpus, Retriever, retrieve_context
from pathlib import Path
r = Retriever.from_documents(parse_corpus(Path('../competition/corpus')))
ctx, ev = retrieve_context(r, '二甲双胍的禁忌症和注意事项是什么', top_k=5)
print([(e['doc_id'], e['authority_level'], round(e['score'],2)) for e in ev])
"
# 4. PG 集成全量（确认无回归）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> RUN_POSTGRES_INTEGRATION=1 \
  uv run pytest ../test/backend/services/knowevo/ -q --no-header
```

**验收标准**:
- [ ] 排序函数显式使用 `authority_level`；加权方式**写明并单测**（建议：乘性先验 `score *= w(authority)`，`w(1)=1.0, w(2)=0.95, w(3)=0.85, w(4)=0.75`——**不改变原始 score 的可追溯性**，`Hit.score` 保留原始 BM25，新增 `Hit.ranked_score`）
- [ ] `per_doc_quota` 生效：单 doc 最多占 N 个 top-k 名额（默认 2），单测构造 1 份高分文档 + 2 份次高权威文档，断言结果含权威文档
- [ ] 权重表可配置（构造参数），默认值有单测锁定；**不得硬编码在打分循环里无法测试**
- [ ] 向后兼容：不传新参数时行为 = 权威度加权开启 + 配额 2；`Hit` 新增字段不破坏既有 `evidence_chain_text`（eval_e1.py 读 `rank/doc_id/chunk_idx/score/split`）
- [ ] 检索对比真跑输出粘进 Evidence（同一 query 的 top-5 doc/authority/score）
- [ ] 零新依赖；ruff 绿；既有 22 例检索测试全绿（改语义的同步更新）
- [ ] `pitfalls.md` 补记"权威度在检索阶段被丢弃"

**Evidence**:

### 验收 1-2：单测 + ruff（全绿）
```
33 passed in 0.08s   (test_e1_retrieval.py：22 旧 + 11 新)
All checks passed!   (ruff check services/knowevo/e1_retrieval.py)
```
### 验收 3：★检索对比真跑（同 query，加权/配额 ON vs OFF）
```
OFF (pure BM25, weights={}, quota=None)
   1 guide-2024         auth=2  bm25=24.034
   2 guide-pc-2022      auth=1  bm25=22.979
   3 guide-2020         auth=2  bm25=22.589
   4 guide-pc-manual-2022 auth=1 bm25=22.465
   5 guide-2020         auth=2  bm25=21.517
ON  (default prior + quota 2)
   1 guide-pc-2022      auth=1  bm25=22.979  ranked=22.979
   2 guide-2024         auth=2  bm25=24.034  ranked=22.832
   3 guide-pc-manual-2022 auth=1 bm25=22.465  ranked=22.465
   4 guide-2020         auth=2  bm25=22.589  ranked=21.46
   5 guide-2020         auth=2  bm25=21.517  ranked=20.441
```
→ **auth=1 国家指南以更低 BM25(22.979) 超过 auth=2 指南(24.034) 升至 #1**——"分数相近时高权威优先"精确生效;2020 版指南 2 个 chunk 被配额限制在 2 个名额,`guide-pc-manual-2022` 进入 top-5。
### 验收 4：PG 集成全量（无回归）
```
378 passed, 24 skipped   (纯单测, 含 11 个新测试)
402 passed                (PG 集成, 含 11 个新测试)
```
### 新增单测（11 个）覆盖
- `TestAuthorityPrior`（5）：默认权重表冻结、接近分重排、`Hit.score` 纯 BM25 保留、`{}` 关闭先验回到纯 BM25 序、自定义权重可配置、未知权威度按 1.0
- `TestPerDocQuota`（4）：quota=1 单文档限 1 名额、`None` 关闭配额、默认 2、超配额跳过不占名额
- `TestRetrieveContextScores`（1）：evidence 同时带 `score` + `ranked_score`，先验只降不升
- 向后兼容：`Hit.score` 字段未改语义，`evidence_chain_text`（读 rank/doc_id/chunk_idx/score/split）不受影响

### 设计决策（相对简报备注的确认）
1. **乘性先验语义**：`w(1)=1.0..w(4)=0.75` 是**降权低权威**而非提权高权威——权威1不惩罚、权威4砍 25%，与"说明书词频碾压指南"的病灶正对。
2. **`Hit.score` 保留纯 BM25**，新增 `Hit.ranked_score` 用于排序；`retrieve_context` 两个都输出（报告可解释"为什么它排前面"）。
3. **`authority_weights={}` 是 T-22 消融的无权威臂**（零成本开关）；`per_doc_quota=None` 关闭配额。
4. 真库检索对比的 A/B 已粘上方；V 题版本混排**不**归 D4 解决（简报已声明，靠 T-18b/T-21）。
5. pitfalls #41 补记"权威度在检索阶段被丢弃"。

---

## 实现备注（给实现 agent）

1. **加权不要污染 BM25 原始分**：`Hit.score` 是 E1 报告与 judge 上下文里的既有字段，改它会让历史报告不可比。新增 `ranked_score` 专用于排序，`score` 保持纯 BM25。`retrieve_context` 输出里两个都给。
2. **配额与加权的顺序**：先按 `ranked_score` 排序 → 再贪心取到 `top_k`，取时按 `doc_id` 计数超 `per_doc_quota` 则跳过（跳过的不占名额）。这是 MMR 的简化确定性版（零依赖、可复现）。
3. **对 V 题的预期影响**：2024 版指南与 2020 版指南 authority 相同（都是 2），加权**不直接**解决版本混排——版本问题靠 T-18b/T-21。但配额能防止单份 2020 指南占满 top-5，给 2024 版留出位置。**预期方向写清楚，不夸大战果**。
4. 若配额导致某些单文档题（如"某说明书禁忌症"）召回变差 → 配额应为**可选**，E1 默认 2，但 `eval_e1` 可传 `per_doc_quota=None`。这一点在 T-22 消融里要做敏感性。
