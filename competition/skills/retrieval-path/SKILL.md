---
name: retrieval-path
description: 检索路执行技能：知识库混合检索加图谱一跳邻域，快速收集单点事实类证据，每条证据标注 EXTRACTED 原文出处。由 domain-asset-cognition 入口技能在单点事实类问题上委托使用。
tags:
  - knowevo
  - retrieval
allowed-tools:
  - knowledge_base_search
  - kg_search
---

# 检索路（子任务技能）

## 职责
为单点事实类问题收集证据。**只收集、不裁决**：发现矛盾事实时两侧都带回，交由证据组装技能处理。

## 允许的原子工具
- `knowledge_base_search(query, top_k)`：文档混合检索，事实主张的第一来源；
- `kg_search(query, hop, top_k)`：图谱一跳邻域，提供实体锚点与关联事实。

## 执行流程
1. 从入口接收问题（含已锚定实体时直接使用；无锚定先 `kg_search(query, hop=1, top_k=5)` 锚定）。
2. `knowledge_base_search(query, top_k=5)`：取文档段落证据；表格类事实（剂量/参考区间）优先核对表格命中。
3. `kg_search(query, hop=1, top_k=5)`：取图谱邻域事实，记录每条关系的 evidence id。
4. 组装 `EvidenceBundle` 输出给入口技能（转交证据组装技能）。

## 输出契约（EvidenceBundle）
```
claims: 每条 {text, doc_ref, span, tag(EXTRACTED|INFERRED), source_channel(doc|kg|kg_doc), evidence_id}
conflicts: 相互矛盾的事实对（两侧都列出）
timeout: bool（超时返回部分结果时置 true）
```

## 纪律
- 延迟预算 p95 ≤ 8s；超时立即返回已收集的部分结果并置 `timeout=true`，不静默截断。
- 每条 claim 必须可溯源（doc_ref/span 或 evidence_id），无出处的猜测不得写入 claims。
- 不调用 `kg_multi_hop`、`decision_card_render`（属于其他子技能）。

## 配置
`config/config.yaml`：`top_k`、`hop`、`timeout_budget_s`（用 `read_skill_config("retrieval-path")` 读取）。
