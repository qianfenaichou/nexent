---
name: reasoning-path
description: 推理路执行技能：图谱束搜索多跳（默认≤3跳、束宽3），沿本体关系模板收集跨文档证据路径，保留失败路径作为反事实素材。由 domain-asset-cognition 入口技能在跨文档决策考量与版本对比类问题上委托使用。
tags:
  - knowevo
  - reasoning
  - multi-hop
allowed-tools:
  - kg_search
  - kg_multi_hop
---

# 推理路（子任务技能）

## 职责
为需要关联多跳事实的问题收集**证据路径**：成功路径构成证据链骨架，失败路径保留为反事实素材。只找路径、不下结论。

## 允许的原子工具
- `kg_search(query, hop, top_k)`：种子实体锚定（入口未提供种子时）；
- `kg_multi_hop(question, seeds, depth, beam)`：版本钉住的束搜索多跳遍历。

## 执行流程
1. 从入口接收问题与种子实体；无种子先 `kg_search(query, hop=1, top_k=5)` 锚定。
2. `kg_multi_hop(question, seeds, depth=3, beam=3)`：执行束搜索，取带路径分的路径集。
3. 版本对比类问题（入口标注 `task=version_compare`）：对关键实体分别取新旧知识版本的有效视图，对比路径差异。
4. 过滤：路径上每条边必须带 claim 文本与 evidence id，**无证据的边直接丢弃**（幻觉护栏）。
5. 输出 `PathSet` 给入口技能（转交证据组装技能）。

## 输出契约（PathSet）
```
successful_paths: 每条 {nodes, edges:[{src, rel, dst, claim, evidence_id}], path_score}
failed_paths: 每条 {nodes, unused_reason}（供 decision_card_render 反事实区使用）
degraded: bool（超时降束后的标记）
```

## 纪律
- 跳数硬顶 3、束宽默认 3（工具层有护杆，不得要求工具突破）。
- 延迟预算 p95 ≤ 25s；超时降 beam=2 续跑一次，仍超时则输出已有路径并置 `degraded=true`。
- 医疗域常用关系模板（模板库可沉淀）：禁忌 → 药品 → 适应症 → 检验指标；模板来自 `variables.relation_template`（模板实例化时注入），不写死在技能里。
- 不调用 `knowledge_base_search`（检索路专属）、`decision_card_render`（证据组装专属）。

## 配置
`config/config.yaml`：`max_depth`、`beam`、`latency_budget_ms`（用 `read_skill_config("reasoning-path")` 读取）。
