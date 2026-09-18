---
name: evidence-assembly
description: 证据组装技能：融合检索路与推理路的证据，处理矛盾标注、校准置信度，调用 decision_card_render 生成含反事实与知识版本戳的可溯源决策卡。由 domain-asset-cognition 入口技能在证据收集完成后委托使用。
tags:
  - knowevo
  - evidence
  - decision-card
allowed-tools:
  - decision_card_render
  - kg_search
---

# 证据组装（子任务技能）

## 职责
把双路证据融合为一张**可溯源、可拒答、带知识版本戳**的决策卡。你是流程的终点：卡生成后直接返回入口技能，不再发起新的证据收集。

## 允许的原子工具
- `decision_card_render(question, bundle, mode)`：决策卡渲染（T-19 注册的 MCP 工具）；
- `kg_search(query, hop=1, top_k=3)`：仅用于核对争议事实的邻域一致性。

## 执行流程
1. 接收入口转交的 `EvidenceBundle`（检索路）与 `PathSet`（推理路）。
2. 融合：doc 通道 EXTRACTED 标记优先，图谱通道提供连接性；同主张双通道一致 → 置信上调。
3. 冲突处理：跨通道/跨文档矛盾 → 用 `kg_search` 复核邻域；仍矛盾 → 卡面标注"知识不一致"（contested），**不得静默取舍**。
4. `decision_card_render(question, bundle, mode=full|lite)`：生成候选 × 证据链 × 校准置信 × 风险 × 反事实（素材取 PathSet.failed_paths）× 知识版本戳的完整卡。
5. 输出决策卡 + 溯源跳转说明给入口技能。

## 输出契约（决策卡）
- 结论与候选；每条主张带证据链（doc_ref/span 或 kg_path evidence id）；
- 校准置信度（无校准表时透传并标注 `calibration_applied=false`）；
- 反事实（full 模式）、知识版本戳、风险与免责声明；
- 拒答：关键事实项证据全缺 → 卡结论 `INSUFFICIENT_EVIDENCE`，不生成候选。

## 纪律
- 拒答纪律优先于生成纪律：宁可空卡，不可编造。
- `lite` 模式（单点事实）：跳过反事实与风险区，卡体缩至 1/3。
- `domain=healthcare` 时必须带认知辅助免责声明（`config.yaml` 的 `disclaimer_domains` 控制）。
- 不调用 `kg_multi_hop`、`knowledge_base_search`（证据收集属于上游子技能）。

## 配置
`config/config.yaml`：`disclaimer_domains`、`default_mode`（用 `read_skill_config("evidence-assembly")` 读取）。
