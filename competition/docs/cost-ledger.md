# Token 成本台账（cost-ledger）
> 自动记账入口：pipeline 各 CLI 与 MCP 工具返回的 used_tokens。周检超 K8 预算 20% 预警（R9）。单价假设见备忘录 08（需 T-01 实测牌价回填）。

| run_id | 日期 | 环节(本体/图谱/决策/评测/迁移) | 模型档 | input_tok | output_tok | 折元 | 延迟 | 备注 |
|--------|------|------|------|-----------|------------|------|------|------|
| (自动写入，人工只补备注) | | | | | | | | |

| 51dd7d9e-a6e7-56f6-a150-190ba594b192 | 2026-09-14 17:19 | 本体 | {"mid": "qwen3-32b", "large": "deepseek-v3", "small": "qwen3-8b"} | 0 | 0 | 0 | 1.363 | build_ontology v0 echo-mode llm_calls=1 (dry_run=False) |
| ingest-20260914224108 | 2026-09-14 22:41 | 语料摄取 | {} | 0 | 0 | 0 | 1.327 | ingest_assets registered=0 skipped=39 uploaded=0 indexed=0 |
| ingest-20260915011057 | 2026-09-15 01:10 | 语料摄取 | {} | 0 | 0 | 0 | 1.71 | ingest_assets registered=19 skipped=39 uploaded=0 indexed=0 |
| poc-20260917-graphstore | 2026-09-17 17:00 | 图谱(PoC基准) | {} | 0 | 0 | 0 | 12.5ms/22.7ms | gen_synthetic_graph seed=42 2万实体/3万边落库（upsert 79s/120s）；P1 multi-hop p95=12.5ms、P2 supersede p95=22.7ms；详见 poc-graphstore.md |
| t09-20260917-curve | 2026-09-17 18:00 | 决策(跳数标定) | local fake-store (no LLM) | 0 | 0 | 0 | 0ms | calibrate_hops depth∈{1,2,3,4} × 30 题（链式夹具）：depth=1 acc=0.00 / depth=2..4 acc=1.00，推荐 depth=2（精度饱和即止，不买无收益的延迟）；真实 LLM 跑分归 T-10b |
| t09-20260917-router | 2026-09-17 18:00 | 决策(路由) | 规则 L1 免费；L2 小档 ~200 tok/题 | 0 | 0 | 0 | <1ms (L1) | L1 六条规则覆盖 F 型；版本比较词命中即 RM；L2 few-shot 仅 L1 未定且注入了 llm 时触发（真实调用归 T-10b） |
| e1-80570c7d-fccf-478d-a957-e488c56ee9ba | 2026-09-17 17:30 | 评测(E1 纯RAG) | mid=deepseek-v4-flash / judge=glm-5.3-flash | 4508 | 305 | 0 | p95=8.73s | 【已作废·冒烟】2题×1 runs。⚠️ 模型档字段失实：当时 KW_LLM_* 未导出，实际 fallback 到 tokenrouter glm-5.3-free（旧模型，见坑#38）；eval_run_t 行已删 |
| e1-ab8b6f12-d758-45a6-b1d3-80732058081b | 2026-09-17 18:32 | 评测(E1 纯RAG) | mid=deepseek-v4-flash / judge=glm-5.3-flash | 178949 | 44196 | 0 | p95=48.4s | 【已作废·旧口径】20题×3 runs; acc=0.7636。judge 解析失败的 2 个 run 被旧代码记为答错；改为隔离后重算见下行，eval_run_t 行已删 |
| e1-9388c240-7bc6-4d3f-a25e-d03919bff8a8 | 2026-09-17 18:35 | 评测(E1 纯RAG) | mid=deepseek-v4-flash(4) / judge=glm-5.3-flash(6) | 178949 | 44196 | 0 | p95=48.4s | 【佐证】freeshare 端点。20题×3 runs（有效判定 53/60；7 故障已隔离）。acc=0.7925 pass2=0.70 pass3=0.60 trace=1.0；F .90 / M .80 / V .857 / X .643。与下行独立跑分一致 → 管线可复现。落库 eval_run_t=9388c240 |
| e1-8aba7c1d-8725-4c50-8f33-e05b911b202c | 2026-09-18 01:29 | 评测(E1 纯RAG) | mid:deepseek-v4-flash(7) / large:glm-5.2(8) | 164022 | 103118 | 0 | p95=75.7s | **【权威·E1 基线】sensenova 端点**。20题×3 runs（有效判定 52/60；8 故障已隔离：生成 4 + judge 4）。**acc=0.7692 pass2=0.6842 pass3=0.6316 trace=1.0**；F .80 / M 1.00 / V .80 / X .57。含节流 --pace 8s + 三层超时护栏。逐题报告见 deliverables/e1-baseline-report.json。落库 eval_run_t=8aba7c1d |
| e1-b64faa90-64ac-44bc-95e9-564ad4b99006 | 2026-09-18 21:42 | 评测(E1 纯RAG) | mid:deepseek-v4-flash / large:glm-5.2 | 181819 | 147219 | 0 | p95=82.13s | E1 纯RAG 基线 20题×3 runs; acc=0.6667 pass2=0.65 n_judged=60/60 trace=1.0 |
| algo-probe-20260919 | 2026-09-19 02:35 | 评测(算法机制预验证·实验固化) | 本机 CPU（无 LLM，token=0） | 0 | 0 | 0 | P1 0.05s / P2 0.02s / P3 0.03s / P4 0.11s | T-B1/T-B2 四脚本固化进 competition/experiments/（seed 固定可复跑），JSON 落 deliverables/algorithm-probes/；P1 V题 1.000 vs 0.206/0.250（F 三臂全 1.000）；P2 净差 +2/+6/+1/+5（budget=20 时 11/0 vs 6/1）；P3 164实体/750卡/0.02ms；P4 贪心/最优 mean=0.988（min=0.88），单调性违反 0/400 |

| mine-d3f9b505 | 2026-09-19 05:44 | 模板沉淀 | mid=unresolved / large=unresolved | 0 | 0 | 0 | 0.199s | T-20 mine_skill_templates tenant=00000000-0000-0000-0000-000000000001 min_support=2 limit=200 no_llm=False cross_tenant=True dry_run=True lang=zh llm_calls=0 llm_failures=6 saved=[-] |
| mine-b64f1858 | 2026-09-19 05:48 | 模板沉淀 | mid=deepseek-v4-flash / large=glm-5.2 | 1652 | 11135 | 0 | 85.537s | T-20 mine_skill_templates tenant=6756b0ab-39c0-462a-9745-aa12e1511fcd min_support=2 limit=200 no_llm=False cross_tenant=True dry_run=True lang=zh llm_calls=2 llm_failures=0 saved=[-] |
| mine-66f9eddc | 2026-09-19 05:49 | 模板沉淀 | mid=deepseek-v4-flash / large=glm-5.2 | 1652 | 9096 | 0 | 70.042s | T-20 mine_skill_templates tenant=6756b0ab-39c0-462a-9745-aa12e1511fcd min_support=2 limit=200 no_llm=False cross_tenant=True dry_run=False lang=zh llm_calls=2 llm_failures=0 saved=[reasoning_decision-general(n=50),refusal-general(n=50)] |