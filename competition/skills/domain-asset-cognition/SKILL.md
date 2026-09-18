---
name: domain-asset-cognition
description: 领域资产认知入口技能：把领域问题路由到检索路/推理路子技能，再交给证据组装技能生成可溯源决策卡。凡属于"领域事实查询、跨文档决策考量、版本对比"类请求，且 agent 已启用 kg_search 等图谱工具时使用本技能。
tags:
  - knowevo
  - orchestration
  - decision-card
allowed-tools:
  - kg_search
---

# 领域资产认知（入口 · 路由层）

## 职责
你只做**路由与总装**：判断问题类型、加载对应子技能指南、收集各路产物、把最终输出交给证据组装技能。你不直接回答任何实质领域问题，也不直接生成决策卡。

## 允许的原子工具（仅限锚定用途）
- `kg_search`：路由前锚定问题中的领域实体（hop=1, top_k=5），确认实体在图谱中存在；仅此用途，不做证据收集。

## 编排流程
1. **锚定**（可选但推荐）：用 `kg_search` 验证问题核心实体存在；实体不存在直接走拒答纪律。
2. **路由判断**，按以下启发式选择子技能：
   - 单点事实（定义/分类/正常范围/是否收录）→ 加载并委托 `retrieval-path`；
   - 跨文档决策考量（多条件比较/机制链/方案选择）→ 加载并委托 `reasoning-path`；
   - 版本对比（新旧指南/标准、"最新版 vs 旧版"）→ 委托 `reasoning-path` 并注明 `task=version_compare`；
   - 判断不确定 → 两路都加载，依次执行（先检索路，后推理路）。
3. **加载子技能**：`read_skill_md("<子技能名>")` 获取执行指南，严格按指南执行；前一技能的输出作为后一技能的输入。
4. **总装**：把各路产物（EvidenceBundle / PathSet）交给 `evidence-assembly` 技能生成决策卡。
5. **出口**：以证据组装技能输出的决策卡为最终回答，并附证据溯源跳转说明。

## 输出契约
- 成功：决策卡（由 evidence-assembly 产出）+ 一句话路由说明（走了哪条路、为什么）。
- 失败：两路均无证据时，明确输出"现有知识库无依据"，**不得编造**；不得跳过 evidence-assembly 自行组织答案。

## 纪律
- 分层纪律：禁止绕过子技能直接调用 `kg_multi_hop`、`decision_card_render` 等子技能专属工具。
- 同类问题单路失败 2 次后，升级为双路并发。
- `domain=healthcare` 的任务，提醒证据组装技能带认知辅助免责声明（其 config.yaml 的 `disclaimer_domains` 控制）。

## 配置
运行时可调参数见 `config/config.yaml`（用 `read_skill_config("domain-asset-cognition")` 读取）。
