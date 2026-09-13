# pipeline/ —— 离线批处理入口（python -m 可跑，幂等断点续跑）
**归属任务**: T-04（build_ontology）/ T-06（ingest_graph）/ T-11（detect_doc_change）/ T-07（gen_synthetic_graph）
**纪律**: 每个 pipeline 是服务层函数的**薄 CLI 封装**——业务逻辑全部在 services，CLI 只做参数解析、进度输出、cost-ledger 记账、退出码。禁在 pipeline 写算法。

## build_ontology.py（T-04）
```bash
python -m services.knowevo.pipeline.build_ontology \
  --domain healthcare --docs docs.json --model-plan plan.yaml [--dry-run]
```
- 调 `ontology_service.extract_seed → propose_concepts → autofix → rank`，产出待审提案（`--dry-run` 只打分不落库）。
- 幂等：章 hash 去重；输出 RoundReport + tokens 记账。
- 验收：对 fixture 3 份标准文档产出 ≥30 提案且 V1-V5 全过。

## ingest_graph.py（T-06）
```bash
python -m services.knowevo.pipeline.ingest_graph --batch batch.json \
  [--checkpoint every=100] [--tables-only] [--llm]
```
- 调 `kg_service` 逐段抽取合并；`--tables-only` 跳过 LLM 通道（确定性先行，冒烟用）。
- 断点：`kg_extract_run_t` span hash 幂等，重跑只补漏。
- 验收：20 份文档全跑 + IngestReport 记账 + 抽检 50 条信噪比 ≥70%。

## detect_doc_change.py（T-11）
```bash
python -m services.knowevo.pipeline.detect_doc_change --old <doc_id> --new <doc_id> \
  [--calibrate sample=60]
```
- 调 `alignment_service.detect_doc_change`；`--calibrate` 走人工审计采样输出（P/R 门 0.90/0.85）。
- 验收：2020 vs 2024 指南 diff 报告 + P/R 达标记录。

## gen_synthetic_graph.py（T-07 PoC 支撑）
```bash
python -m services.knowevo.pipeline.gen_synthetic_graph --entities 20000 --edges 30000 --out sql
```
- 合成图数据（参数化规模），服务 A1 PoC 三必测点与 R5 压力测试。
- 验收：P1/P2/P3 三点延迟数据落 `competition/docs/poc-results.md`。

## 统一约定
- 全部入口支持 `--plan`（三档模型分配 YAML，K8 §2 表的运行时形态）；
- 全部输出写 cost-ledger 行（run_id, 环节, 档位, tokens, 折元, wall_seconds）；
- 退出码：0 成功 / 2 部分失败（断点可续）/ 1 致命错。日志英文（上游 lint）。
