# T-28：判别题集 v1（E8 版本钉住判别性验证前置，零 LLM）

**状态**: ★ 待开发（2026-09-22 r26 轮；来源 = pitfalls #62 沉淀机制④：e8-paired2 两臂满分 Δ=0.0 是「测试集无判别题」所致——要暴露版本钉住差异，必须有「旧时钟 0 证据 vs 新时钟 N 证据」的判别题）
**Blocked by**: 无
**独占文件**（本任务创建/修改）:
- `competition/corpus/testset-discriminating-v1.json`（新建：判别题集）
- `backend/services/knowevo/pipeline/eval_v1.py`（**加性**：校验器/CLI 支持指定题集路径，不改既有 20 题语义）
- `test/backend/services/knowevo/test_eval_discriminating.py`（新建）或既有测试文件加性扩展
- `.task_b_status/t28-discrim/**`（零 LLM walk 探针输出，不进 git）

**待接线项**: 无
**禁改清单**: `competition/corpus/testset-v1-seed.json` 既有 20 题、`pipeline/ablation.py` 评测语义（E8 口径）、`pipeline/eval_e1.py`、`backend/prompts/**`、`frontend/public/locales/**`、其他任务独占文件
**允许的新依赖**: **无**；**LLM 调用 = 0**（判别实体用零 LLM store walk 探针预筛）

## 背景（现状核验 2026-09-22 r26）
- 既有题集：`competition/corpus/testset-v1-seed.json` 20 题（F5/M5/V5/X5），schema 见 `knowevo/eval/testset-template.json`；校验器 `eval_v1.py::validate_testset`（K4 语义规则：type enum、id 前缀、V 题双金标）。
- kw-qa r25 零 LLM walk 探针已定位一个真实分化实体：**糖化血红蛋白**（as_of=2021-06-01 → 0 边；as_of=2025-06-01 → 3 边），且已有真实双卡佐证（decision_card `9ed40a72` INSUFFICIENT vs `109425c1` RECOMMEND，evidence-index L70）。图谱还携带 valid_at=2021-04 与 2025-01 两批事实边（pitfalls #61 修复方案）。
- 判别题形态：**同一问题双金标**——`as_of=t_old` 期望诚实拒绝（INSUFFICIENT_EVIDENCE/0 候选），`as_of=t_new` 期望证据支撑答案。这正是 V 题 K4 双金标协议的延伸。

## 任务
1. **探针预筛（零 LLM）**：写 walk 探针脚本（放 `.task_b_status/t28-discrim/`），对候选实体逐个跑双时钟 store 直查，登记「实体 × (t_old 边数, t_new 边数)」；入选标准 = 旧时钟 0 有效证据边 且 新时钟 ≥1 有效证据边。目标 ≥4 个实体（不足就如实写 N，不硬凑）。
2. **建题**：`testset-discriminating-v1.json`，8 题（每个合格实体出 1-2 题），schema 与 testset-v1-seed.json 完全一致；V 型 id（如 `V-D001`…遵循模板 id 规则，若模板要求严格前缀校验则以校验器实际规则为准）；每题 `expected` 写双金标（as_of 两个工作点各自的期望），并在题内新增可选字段 `discriminating: true` + `probe_evidence`（两时钟边数与实体名）——校验器对未知新字段必须宽容（additive）。
3. **校验器加性扩展**：`validate_testset` 支持从 CLI/函数参数接收题集路径（默认仍指 testset-v1-seed.json，既有调用零变化）；对新文件跑全绿。
4. **测试**：新测试覆盖「判别题集通过校验」「默认路径行为不变」「probe_evidence 字段宽容」。

## 验收标准
- [ ] `python -m ... eval_v1 validate --path competition/corpus/testset-discriminating-v1.json` 全绿（按实际 CLI 形态，命令贴 Evidence）
- [ ] 既有 20 题校验行为零变化（贴 before/after 输出）
- [ ] pytest 新测试绿 + `pytest ../test/backend/services/knowevo/ -q` 零回归（从 backend 目录跑，贴数字）
- [ ] ruff 零新增；注释英文；LLM=0；每题带真实探针证据（两时钟边数）
- [ ] 独占文件边界外零改动；不 push 不合并；台账行报主会话登记

**Evidence**: （2026-09-22 t28-discrim 实测回填）
- 探针预筛（零 LLM）：`.task_b_status/t28-discrim/probe_dual_clock.py` 直连 PG（构建租户 6756b0ab，与 ablation D1 同一连接方式），扫描 136 active 实体 × 双时钟（t_old=2021-06-01 / t_new=2025-06-01），输出 `probe-results.json`：`entities=136 discriminative=31`（目标 ≥4）。典型命中：`Indicator:糖化血红蛋白 old=0 new=3`（与 kw-qa r25 及决策卡 9ed40a72/109425c1 佐证一致）。题集使用 7 实体（8 题），新时钟 claim 全部 valid_at=2025-01-01（guide-2024 批次）。
- 验收命令（backend 目录真跑）：
  - `python -m services.knowevo.pipeline.eval_v1 --validate`（新默认路径）→ `VALID: 20 questions, hash=fd56571a304fb09d`
  - `python -m services.knowevo.pipeline.eval_v1 --testset ../competition/corpus/testset-discriminating-v1.json --validate` → `VALID: 8 questions, hash=d59034f245061ffc`
  - before/after（HEAD 版临时同目录模块真跑）：seed 显式路径 before `VALID: 20 questions, hash=fd56571a304fb09d` / after 无 `--testset` 输出逐字符一致（20 题语义零变化）；before 无 `--testset` 参数时 `TypeError: ... NoneType` 崩溃 → after 正常走默认 seed。
- pytest（从 backend 目录）：`python -m pytest ../test/backend/services/knowevo/ -q` → **711 passed, 30 skipped, 1 warning in 52.76s**（含新增 test_eval_discriminating.py 10 测试：判别题集校验/双金标形态/哈希冻结/≥4 实体判别规则/默认路径行为不变/CLI 两形态/additive 字段宽容）。
- ruff：`services/knowevo/pipeline/eval_v1.py` + `test_eval_discriminating.py` 全部 `All checks passed!`（行宽 119）；注释全英文；零新依赖；LLM 调用 = 0（探针为纯 SQL store walk）。
- 独占文件边界外零改动；不 push 不合并。台账行（probe 探针真跑、判别题集登记）见 `.task_b_status/t28-discrim.status.json` ledger_lines，待主会话登记共享台账。
