---
name: evidence-assembly
description: 证据链组装与决策卡生成：融合双路证据、解决矛盾标注、校准置信度、生成含反事实与知识版本戳的决策卡。
version: 1.0.0
---

# 证据组装（子任务 Skill）

## 执行步骤
1. 融合双路 `EvidenceBundle`+`PathSet`：doc 通道 EXTRACTED 优先，图谱通道供连接性；
2. 冲突处理：跨通道/跨文档矛盾 → 调 `evidence_verify` 核对 → 仍矛盾则卡面标"知识不一致"（不静默取舍）；
3. `decision_card_render(question, bundle, mode=full|lite)`：候选×证据链×校准置信×风险×反事实（top-1，素材取失败路径）×知识版本戳；
4. 输出决策卡 + 溯源跳转说明给入口 Skill。

## 纪律
- 拒答判定：关键事实项证据全缺 → 卡结论为 INSUFFICIENT_EVIDENCE（不得生成候选）；
- 免责声明插拔：domain=healthcare 必带；
- lite 模式（事实型）：跳过反事实与风险区，卡体 1/3。
