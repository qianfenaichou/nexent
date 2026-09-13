---
name: retrieval-path
description: 检索路执行：文档混合检索 + 图谱一跳邻域，快速收集单点事实证据并标注 EXTRACTED 原文出处。
version: 1.0.0
---

# 检索路（子任务 Skill）

## 执行步骤
1. `kg_search(query, hop=1, top_k=5)`：锚定实体并取邻域（带证据 id）；
2. `asset_search(query, modality=全部)`：多模态资产补充（表格类事实优先命中）；
3. `doc_search`（Nexent 原生）：文档级混合检索兜底与原文定位；
4. 输出 `EvidenceBundle`：每条 claim 标注 {doc, span, tag: EXTRACTED 优先, source_channel}。

## 纪律
- 只收集不裁决；矛盾事实两侧都带回（交 evidence-assembly 处理 contested）；
- 单路预算：p95 ≤8s；超时返回部分结果+`timeout=true` 标记。
