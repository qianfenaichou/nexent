---
name: reasoning-path
description: 推理路执行：图谱束搜索多跳（≤3跳/束宽3），沿本体关系模板收集跨文档证据路径，保留失败路径作反事实素材。
version: 1.0.0
---

# 推理路（子任务 Skill）

## 执行步骤
1. 从入口接收问题与种子实体（无种子先 kg_search 锚定）；
2. `kg_multi_hop(question, seeds, depth≤3, beam=3)`：束搜索路径集（含路径分）；
3. `kg_evolution_trace(entity_id)`（仅版本对比型问题）：知识演化回溯；
4. 输出 `PathSet`：成功路径（证据链骨架）+ 失败路径（标记 unused_reason，供反事实）。

## 纪律
- 跳数硬顶 3（工具层已护杆）；延迟预算 p95 ≤25s，超时降 beam=2 续跑；
- 路径上每条边必须带 claim 文本与 evidence id（无证据的边丢弃——幻觉护栏）；
- 医疗域关系模板参考：禁忌→药品→适应症→检验指标。
