# Token 成本台账（cost-ledger）
> 自动记账入口：pipeline 各 CLI 与 MCP 工具返回的 used_tokens。周检超 K8 预算 20% 预警（R9）。单价假设见备忘录 08（需 T-01 实测牌价回填）。

| run_id | 日期 | 环节(本体/图谱/决策/评测/迁移) | 模型档 | input_tok | output_tok | 折元 | 延迟 | 备注 |
|--------|------|------|------|-----------|------------|------|------|------|
| (自动写入，人工只补备注) | | | | | | | | |

| 51dd7d9e-a6e7-56f6-a150-190ba594b192 | 2026-09-14 17:19 | 本体 | {"mid": "qwen3-32b", "large": "deepseek-v3", "small": "qwen3-8b"} | 0 | 0 | 0 | 1.363 | build_ontology v0 echo-mode llm_calls=1 (dry_run=False) || ingest-20260914224108 | 2026-09-14 22:41 | 语料摄取 | {} | 0 | 0 | 0 | 1.327 | ingest_assets registered=0 skipped=39 uploaded=0 indexed=0 |
| ingest-20260915011057 | 2026-09-15 01:10 | 语料摄取 | {} | 0 | 0 | 0 | 1.71 | ingest_assets registered=19 skipped=39 uploaded=0 indexed=0 |
| poc-20260917-graphstore | 2026-09-17 17:00 | 图谱(PoC基准) | {} | 0 | 0 | 0 | 12.5ms/22.7ms | gen_synthetic_graph seed=42 2万实体/3万边落库（upsert 79s/120s）；P1 multi-hop p95=12.5ms、P2 supersede p95=22.7ms；详见 poc-graphstore.md |
| t09-20260917-curve | 2026-09-17 18:00 | 决策(跳数标定) | local fake-store (no LLM) | 0 | 0 | 0 | 0ms | calibrate_hops depth∈{1,2,3,4} × 30 题（链式夹具）：depth=1 acc=0.00 / depth=2..4 acc=1.00，推荐 depth=2（精度饱和即止，不买无收益的延迟）；真实 LLM 跑分归 T-10b |
| t09-20260917-router | 2026-09-17 18:00 | 决策(路由) | 规则 L1 免费；L2 小档 ~200 tok/题 | 0 | 0 | 0 | <1ms (L1) | L1 六条规则覆盖 F 型；版本比较词命中即 RM；L2 few-shot 仅 L1 未定且注入了 llm 时触发（真实调用归 T-10b） |
