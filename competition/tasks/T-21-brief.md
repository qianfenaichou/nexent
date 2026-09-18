# T-21：标准对齐器垂直切片（★创新主轴 · 文献空白点①）

**状态**: ★ 待开发（2026-09-18 调度会话）
**Blocked by**: T-18b（事实业务时间——受影响面与最小更新集需要真实的版本时间轴）
**独占文件**（本任务创建/修改）:
- `backend/services/knowevo/alignment_service.py`（**新建**：三段式变更检测 + 受影响面分析 + 最小充分更新集）
- `backend/services/knowevo/pipeline/diff_guidelines.py`（**新建**：CLI，跑两版指南 diff）
- `backend/prompts/knowevo_align_{en,zh}.yaml`（**新建**：段落对齐裁决 + 变更分类 prompt，双语成对）
- `backend/apps/knowledge_graph_app.py`（**自包含**：新增 `/alignment/*` 路由）
- `deploy/sql/migrations/v2.5.5_kw_008_alignment_index.sql`（**新建**：受影响面倒排索引；`doc_version_diff_t` 已有表，零 ALTER）
- `test/backend/services/knowevo/test_alignment_service.py`（**新建**）
- `competition/corpus/guideline_diff_seed.md`（**回填**：逐条核对结论 + 章节锚点，T-11 遗留标记）
- `competition/docs/evolution-log.md`（登记第一轮真实演进）
- `competition/deliverables/alignment-*.json`（**新建**：diff 报告与受影响面报告）

**待接线项**: 无
**禁改清单**: `backend/consts/const.py`、`apps/app_factory.py`、既有迁移、`decision_service.py` 算法、`pipeline/eval_*`（T-18c 独占）、`e1_retrieval.py`（T-18d 独占）、**`competition/corpus/` 下原始 PDF 不得改动/导出**（版权铁律 8）
**允许的新依赖**: **无**（章节树对齐 DP、匈牙利算法、embedding 相似度均纯 Python/标准库；embedding 若需调用走既有 llm_client）

**要构建的行为**（用户视角端到端）:
1. **三段式变更检测**（02-技术方案 §4.1）：
   - STEP 1 章节树对齐（确定性：标题归一化 + 树编辑距离 DP）→ `{matched/added/deleted/moved/renumbered}`
   - STEP 2 段落语义对齐（仅 matched 章节：相似度矩阵 + 匈牙利最优配对；≥0.85 进 STEP 3，0.6-0.85 LLM 裁决）
   - STEP 3 变更分类（UNCHANGED 跳过；否则 LLM 分类 `{ADD/UPDATE/DELETE/MOVE/RENUMBER/SPLIT/MERGE}` + 抽取命题级变更要点）
2. **受影响面分析**（§4.2）：变更段落集 ΔS → 图谱锚定（`kg_evidence_t.evidence_span ∈ ΔS`）→ 受影响实体/关系 `E_aff` → 反向传播到引用 `E_aff` 的决策卡 `D_aff`；
3. **最小充分更新集**（§4.3，★创新内核）：对每项估 `VOI = P(结论会因变更而改变) × impact(被引用次数)`，VOI 降序贪心选入 `U`，直到边际收益 < 成本；输出 `U` 与"未纳入 U 的质量损失估计"；
4. **P/R 标定**：以 `guideline_diff_seed.md` 为金标（逐条核对后）算变更检测 precision/recall（目标 P≥0.90 / R≥0.85）；
5. **落库**：结果写 `doc_version_diff_t`（`section_align`/`changes`/`precision`/`recall`），登记 `evolution_round_t`（`trigger_source=standard_update`）。

**背景（现状核验，2026-09-18）**:
- **`alignment_service.py` 不存在**（`ls backend/services/knowevo/ | grep align` 空）。T-06 的实体对齐在 `kg_service.py` 内，**文档级变更检测完全未实现**。
- `doc_version_diff_t` 表已存在（`knowevo_db.py:224`，字段 `old_doc/new_doc/section_align/changes/precision/recall`），**零代码读写**（grep 仅命中模型）。
- `DocAsset.supersede_of`（knowevo_db.py:217）是版本血缘链字段，两版指南可链接。
- 语料：`competition/corpus/guidelines/` 有 `dm_guideline_2024.pdf`（2024 版）等；**2020 版指南**在 registry 中为 `guide-2020`（`local_file=guidelines/t2dm_guideline_2020.pdf`，2026-09-18 核验时该路径需确认存在）。
- 金标种子：`competition/corpus/guideline_diff_seed.md`（16 条变更，人工预核对，**待逐条 PDF 原文复核**）。
- **文献空白点①**：01-总纲 §0.1 记录"ontology change + impact analysis **零命中**；GitHub 8 轮检索无成熟实现（最接近者 0★ 且只做定性 diff）"——这是创新主轴的立论基础。

**验收命令**:
```bash
# 1. 单测（三段式各段 + VOI 贪心 + 受影响面）
cd backend && uv run pytest ../test/backend/services/knowevo/test_alignment_service.py -q --no-header
# 2. ruff
cd backend && uv run ruff check services/knowevo/alignment_service.py services/knowevo/pipeline/diff_guidelines.py
# 3. ★真跑：两版指南 diff（本地确定性段 + LLM 段）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> KW_LLM_MID_MODEL_ID=7 KW_LLM_LARGE_MODEL_ID=8 \
  uv run python -m services.knowevo.pipeline.diff_guidelines \
    --old guide-2020 --new guide-2024 --gold ../competition/corpus/guideline_diff_seed.md \
    --out ../competition/deliverables/alignment-diff.json
# 4. 受影响面 + 最小更新集
cd backend && POSTGRES_HOST=... uv run python -m services.knowevo.pipeline.diff_guidelines \
  --impact-only --diff ../competition/deliverables/alignment-diff.json \
  --out ../competition/deliverables/alignment-impact.json
# 5. PG 集成全量
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> RUN_POSTGRES_INTEGRATION=1 \
  uv run pytest ../test/backend/services/knowevo/ -q --no-header
```

**验收标准**:
- [ ] 三段式逐段实现，每段可独立单测：STEP1 确定性（无 LLM，可复现）；STEP2 相似度阈值分流（≥0.85 直配 / 0.6-0.85 LLM / <0.6 不配）；STEP3 分类 7 类 + 命题级要点
- [ ] 表格型变更走**确定性 diff**（行列 hash），零 LLM（§4.1 明文要求）
- [ ] 受影响面：`ΔS → E_aff → D_aff` 全链路，**两次索引查询**（不做在线图遍历，§4.2 DECISION）；新增索引迁移 `kw_008`
- [ ] 最小充分更新集：VOI 公式落地（`P × impact`），贪心到边际收益 < 成本；输出 `U` 及"未纳入 U 的损失估计"
- [ ] P/R 标定：以 `guideline_diff_seed.md`（**逐条核对后**）为金标算出 precision/recall，落 `doc_version_diff_t.precision/recall`
- [ ] `guideline_diff_seed.md` 的"待复核标记"逐条处理：PDF 原文核对结论 + 章节锚点补全（**未核对的条目明确标 `unverified`，不得假装核对过**）
- [ ] 真跑产出 `alignment-diff.json` + `alignment-impact.json` 到 deliverables；`evolution-log.md` 登记第一轮演进（含 `knowledge_stamp`、变更条数、受影响实体/卡数、演进成本）
- [ ] `/alignment/*` HTTP 路由走 RBAC 与租户隔离
- [ ] 零新依赖；零新增 env；**未复制/导出受版权语料**（deliverables 只放派生的变更清单，不放 PDF 片段原文）
- [ ] 双语 prompt 成对

**Evidence**: <粘贴 pytest 输出 / diff 真跑统计（变更条数 by type）/ P/R 数字 / 受影响面规模 / 最小更新集大小 / evolution-log 行>

---

## 实现备注（给实现 agent）

1. **这是创新主轴，不是普通任务**：01-总纲 §0.1 明确"标准对齐器应从演示升格为创新主轴"，§4 评分维度③要求"每个空白点给形式化定义 + 消融实验"。本任务的形式化定义已在 02-技术方案 §4.1-4.3 写好，**照做并产出可放进初赛文档的数字**。
2. **P/R 金标的诚实性**：`guideline_diff_seed.md` 自述"基于公开材料整理，未经原文逐页 diff"。**逐条核对是硬要求**；核对不了的条目保留但标注，**不得把未核对条目计入 P/R 分子**（否则是伪造数字）。
3. **2020 版 PDF 存在性**：开工第一件事确认 `competition/corpus/guidelines/t2dm_guideline_2020.pdf` 是否存在（registry 有登记）。不存在则**停下报告**——不得用 2024 版自比或发明 diff。
4. **最小更新集 vs 人审预算**：02-技术方案 §4.3 明确"这同时解决了 §2.1 的人审预算问题（同一个 VOI 框架）——一次设计，两处空白"。VOI 函数应与 T-20 的模板/提案排序共用（若 T-20 已实现排序，复用而非重写）。
5. **版权**：`competition/corpus/` 下原始 PDF/HTML 不得进公开仓库（铁律 8）。deliverables 里只放**派生的变更清单**（短引用 + 出处锚点），**不放长段原文**。
6. **演示剧本 D3（02 §4.5）**：本任务产出的 diff 报告是"压轴 60 秒"的素材。产出后按 D3 五步准备（离线批处理完成②-④，现场只演示①⑤）。
