---
name: domain-asset-cognition
description: 领域资产认知智能体入口编排：路由问题到检索/推理双路，组装可溯源决策卡。当用户提出领域事实查询或跨文档决策考量类问题时使用本技能。
version: 1.0.0
---

# 领域资产认知（入口 Skill · A4 编排顶层）

## 职责与路由原则（正文即 duty 的流程层，duty prompt 只写职责）

1. **判断问题类型**（调用 `kg_search` 先锚定实体，或直接依据以下启发）：
   - 单点事实（剂量/定义/是否收录）→ 委托 `retrieval-path`
   - 跨文档决策考量（多条件/比较/方案选择）→ 委托 `reasoning-path`
   - 版本对比（新旧指南/标准）→ 委托 `reasoning-path` 并传 `kg_evolution_trace`
   - 不确定 → **两路并发**，由 `evidence-assembly` 融合
2. **不自己回答实质问题**——你只做路由与总装，证据收集与卡生成委托子 Skill；
3. 任何输出必须以**决策卡**为出口（事实型问题用 lite 模式卡）；
4. 拒答纪律：两路证据均不足时，明确说"现有知识库无依据"，不得编造（X 题的红线）。

## 编排流

```
用户问题
 → [路由判断]（上述启发 + 失败升级规则：同类问题失败≥2次→并发双路）
 → retrieval-path / reasoning-path（可并发）
 → evidence-assembly（融合+决策卡）
 → 输出卡 + 证据链跳转说明
```

## 边界
- 不跳过子 Skill 直接调底层 MCP 工具（分层纪律）；
- 医疗域（domain=healthcare）：卡尾自动加认知辅助免责声明（由 decision_card_render 参数控制）。
