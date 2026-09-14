# Token 成本台账（cost-ledger）
> 自动记账入口：pipeline 各 CLI 与 MCP 工具返回的 used_tokens。周检超 K8 预算 20% 预警（R9）。单价假设见备忘录 08（需 T-01 实测牌价回填）。

| run_id | 日期 | 环节(本体/图谱/决策/评测/迁移) | 模型档 | input_tok | output_tok | 折元 | 延迟 | 备注 |
|--------|------|------|------|-----------|------------|------|------|------|
| (自动写入，人工只补备注) | | | | | | | | |

| 51dd7d9e-a6e7-56f6-a150-190ba594b192 | 2026-09-14 17:19 | 本体 | {"mid": "qwen3-32b", "large": "deepseek-v3", "small": "qwen3-8b"} | 0 | 0 | 0 | 1.363 | build_ontology v0 echo-mode llm_calls=1 (dry_run=False) |