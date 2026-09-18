# T-10a：评测基线（评测集 v1 + judge rubric + 纯 RAG 基线）
**Blocked by**: T-02（58 份语料 + doc_asset_t 登记，已合并）、T-07（GraphStore + kg_search 已合并，供 RAG 检索）、T-08（LLM 真实链路/知识库接线）⚠️**部分阻塞**：纯 RAG 基线（E1）需要 T-08 的检索链路；本任务**先完成评测集 v1 种子 + judge rubric 落地 + runner 脚手架**，E1 基线跑分等 T-08 接线后补跑
**拆分裁决**: 本任务是内容密集型（120 题 → 人工校验 α≥0.7）；拆三步——**T-10a-1 评测集 v1 种子 + judge 协议落 code**（本任务核心，已完成种子 20 题）；T-10a-2 E1 纯 RAG 基线跑分（等 T-08）；T-10a-3 120 题完整集（LLM 扩展 + 人工校验，跨会话）

**独占文件**:
- `competition/corpus/testset-v1-seed.json`（**已建**，E0 20 题 → K4 格式：五元组 rubric/权重/evidence_origin/V 双金标）
- `backend/services/knowevo/pipeline/eval_v1.py`（**新建**，评测集校验器 + runner 脚手架：加载种子 → 校验 schema → pass^k 聚合骨架）
- `backend/prompts/knowevo_judge_en.yaml` + `_zh.yaml`（**新建**，judge 五元组双语 prompt，模板清单第 13 对补录）
- `test/backend/services/knowevo/test_eval_v1.py`（**新建**，schema 校验 + pass^k 聚合）
- `backend/prompts/knowevo_expand_en.yaml` + `_zh.yaml`（**新建**，评测集 LLM 扩展 prompt，T-10a-3 用）

**待接线项**（登记给 T-08）: LLM 真实调用链路（judge/expand 的 tier 路由）；检索链路（知识库 → RAG 上下文 → 生成）；上游智能体评估 code 评测器注册（knowevo_passk/knowevo_trace，零侵入，见 02 §6.5）

**禁改清单**: 上游共享文件；T-03 表结构（`eval_run_t` 只写不 ALTER）；E0 既有文件（`e0-baseline.md`/`e0-questions.md` 只读，作为对照锚点）

**允许的新依赖**: **无**（纯标准库 + pyyaml + pydantic 已有）

**要构建的行为**（用户视角端到端）:
评测集 v1 种子能通过 schema 校验（五元组齐全、权重合法、题型枚举、V 题双金标）；runner 能对一次评测输出 pass^k 聚合（pass2/pass3 占比骨架，E2 起用它）；judge prompt 双语成对可注入 LLM（T-08 接线后即可跑 LLM-as-judge 五元组判分）。120 题扩展的 prompt 就位。

**验收命令**:
```bash
cd backend && uv run pytest ../test/backend/services/knowevo/test_eval_v1.py -v
cd backend && uv run python -m services.knowevo.pipeline.eval_v1 --testset ../competition/corpus/testset-v1-seed.json --validate
```

**验收标准**:
- [x] 评测集 v1 种子（`testset-v1-seed.json`）：20 题四级分布（F5/M5/V5/X5），全部含 rubric 五元组 + 权重（key_facts 合计 0.6）+ evidence_origin + V 题双金标
- [x] `eval_v1.py --validate`：schema 校验通过（题型枚举、rubric 字段、V 题双标签；种子集 VALID 20 题 hash=cb5f729221233028）
- [x] `eval_v1.py` pass^k 聚合：对热心 label 数据输出 pass2/pass3 占比（实测 acc=0.8333 pass2=1.0 pass3=0.5）
- [x] judge prompt 双语成对（en/zh），含五元组核对指令 + 判对线 0.8 + 一票否决（knowevo_judge_en/zh.yaml）
- [x] expand prompt 双语成对（种子→变体：改实体/数值/问法，标注 variant_note）（knowevo_expand_en/zh.yaml）
- [x] ruff 全过；注释/docstring 英文
- [x] 新踩坑记 `competition/docs/pitfalls.md`（坑 #28）

**Evidence**:
```
$ cd backend && uv run pytest ../test/backend/services/knowevo/test_eval_v1.py -v
16 passed

$ cd backend && uv run python -m services.knowevo.pipeline.eval_v1 \
    --testset ../competition/corpus/testset-v1-seed.json --validate
VALID: 20 questions, hash=fd56571a304fb09d

$ uv run python -m services.knowevo.pipeline.eval_v1 --runs runs.json
{"acc": 0.8333, "pass2": 1.0, "pass3": 0.5, "n_questions": 2, "n_runs": 6}

$ POSTGRES_* + RUN_POSTGRES_INTEGRATION=1 uv run pytest ../test/backend/services/knowevo/ -q
160 passed   # 全 knowevo 含 PG 集成

$ uv run ruff check services/knowevo/ ../test/backend/services/knowevo/
All checks passed!

交付：testset-v1-seed.json（E0 20 题→K4 格式，五元组+权重+盲区+V 双金标；
     X-003/X-004 改为语料外拒答题，K4 X 型"知识边界自知"语义）
     + knowevo_judge/expand 双语 prompt + eval_v1.py 校验器/pass^k
T-10a-2（E1 纯 RAG 跑分）等 T-08 接线后补跑；T-10a-3（120 题扩展）跨会话。

2026-09-17 审查修复（code-review 双轴）：X 型题语义空（全可答无拒答）→ 2 题改库外拒答
（refusal_expected=true）；testset_hash 占位符 → 写回真实值；en/zh judge prompt
"整题记0"一致化；pass^k 补 tau2-bench 出处（§4.2.3）；补 X 拒答/盲区语义测试。
```

## 反幻觉条款（发任务时必附）
开工先读仓库根 AGENTS.md；评测协议以 `02-技术方案.md` §6（K4+A5）为算法契约、`knowevo/eval/judge-rubric.md` 与 `testset-template.json` 为格式契约；E0 基线只读（对照锚点，历史数据不得改写）；`eval_run_t` 表结构只读不 ALTER；不引入新依赖；不得发明环境变量。