# Skill 工作流模板沉淀方案（Playbook）

> 产出：任务Q5b · C3（对应官方维度④「明确规划可复用 Skill 工作流模板沉淀方案」）。
> 本文是维度④"可复用 Skill 模板沉淀方案"的**正面答复**：先讲方法论，再给出现有 4 组模板的复用边界。
> 全部内容基于 `nexent/competition/skills/` 下 4 组真实 `SKILL.md` + `config/`，以及 `frontend/features/skillTemplate/` 的模板库实现，不编造。

---

## 一、为什么需要沉淀（定位）

官方维度④要求"明确规划可复用 Skill 工作流模板沉淀方案"。本项目的 Skill 体系已经跑通一条真实链路：

```
domain-asset-cognition（入口·路由）
   ├─ retrieval-path（单点事实检索）
   └─ reasoning-path（跨文档多跳推理）
        └─ evidence-assembly（证据融合 → 决策卡）
```

这 4 组 + 平台 `ag_skill_info_t` 的 4 条记录，已是"从项目实践里长出来"的模板，而非凭空设计。沉淀方案的目标：**把"长出来"的东西变成"可被新域、新任务零改复用"的资产**。

---

## 二、沉淀方法论（六步）

### 步骤 1 · 观测（从真实轨迹出发，不拍脑袋）
- 来源：`decision_card_t` 真实出卡轨迹 + 检索/推理路的调用日志。
- 只归纳**真实发生**的任务类型，不预设不存在的模板。本项目真实归纳出 5 类任务（`skillTemplateService` 的 `TASK_TYPE_COLORS`）：`fact_lookup` / `reasoning_decision` / `version_compare` / `refusal` / `general_qa`。

### 步骤 2 · 聚类（按任务类型切分）
- 同一任务类型共享一套编排骨架 → 抽成一组模板。
- 切分判据：是否需要多跳（检索路 vs 推理路）、是否跨版本（version_compare）、是否触发拒答（refusal）。

### 步骤 3 · 参数化（抽变量、留骨架）
- **可变部分外提**：关系模板 → `variables.relation_template`；域词表 / 域规则 → `domain_rules`；运行预算 → `config/config.yaml`（`max_depth` / `beam` / `latency_budget_ms`）。
- **铁律：骨架里不写死域名词、不写死关系**（见 `reasoning-path` SKILL.md：「模板来自 `variables.relation_template`，不写死在技能里」）。这是"换域零改骨架"的前提。

### 步骤 4 · 冻结契约（可复用的硬约束）
- `SKILL.md` frontmatter 只用白名单 5 键（`name` / `description` / `tags` / `allowed-tools`）；**白名单外字段（如 `version`）被静默丢弃**（坑 #44）——所以模板作者必须只写白名单字段。
- `allowed-tools` 可预写尚未注册的工具名（如 `decision_card_render`），但**实例化前必须确认该工具已在平台注册**，否则关联为空（坑 #44）。
- 工具签名单一字段源：FastMCP 工具统一吃 Pydantic 模型，避免 schema 漂移（坑 #27）。

### 步骤 5 · 写明复用边界（每个模板自带"何时用 / 何时不用"）
- 见第三节。每条边界都是**可执行的纪律**（延迟预算、跳数硬顶、拒答优先），不是散文。

### 步骤 6 · 版本化 + 诚实回写复用效果
- 模板带 `version`；复用统计 `reuse_count` 直接显示，`reuse_success`（成功率）**只在真实运行后由 `record_reuse_outcome` 回写**，`apply` 不虚构（前端 `SkillTemplatePanel.tsx` 未回写时显示"未记录" tooltip，而非造假数字）。
- 潜规则：没有真实复用数据前，成功率栏位标 `insufficient_data`，不写"成功率 95%"。

---

## 三、现有 4 组模板的复用边界

| 模板 | 角色 | 允许原子工具 | 复用边界（何时用） | 禁用边界（何时有红线） |
|---|---|---|---|---|
| **domain-asset-cognition** | 入口·路由层 | `kg_search`（仅锚定实体） | 凡"领域事实查询 / 跨文档决策考量 / 版本对比"且 agent 已启用 `kg_search` 等图谱工具。自己**只路由总装，不答实质问题** | 禁止绕过子技能直接调 `kg_multi_hop` / `decision_card_render`；同类问题单路失败 2 次才升双路 |
| **retrieval-path** | 单点事实检索 | `knowledge_base_search` + `kg_search` | `fact_lookup` 类（定义/分类/正常范围/是否收录）。延迟预算 **p95 ≤ 8s**，超时返回部分结果并置 `timeout=true` | 不调 `kg_multi_hop` / `decision_card_render`；每条 claim 必须可溯源（doc_ref/span 或 evidence_id），无出处不写 |
| **reasoning-path** | 跨文档多跳推理 | `kg_search` + `kg_multi_hop` | `reasoning_decision` / `version_compare`。**跳数硬顶 3、束宽默认 3**，p95 ≤ 25s，超时降 `beam=2` 续跑一次，仍超时输出已有路径置 `degraded=true` | 不调 `knowledge_base_search`（检索路专属）；无证据边直接丢弃（幻觉护栏）；不要求工具突破护杆 |
| **evidence-assembly** | 证据融合·出卡（终点） | `decision_card_render` + `kg_search` | 流程终点，融合双路证据 → 可溯源、可拒答、带知识版本戳的卡。冲突 → 卡面标"知识不一致"（contested），**不静默取舍** | 拒答纪律优先于生成：关键事实证据全缺 → 卡结论 `INSUFFICIENT_EVIDENCE`，不生成候选；`lite` 模式跳过反事实与风险区；`domain=healthcare` 必须带认知辅助免责声明 |

### 3.1 跨模板的共用纪律（复用时的全局规则）
1. **拒答优先**：宁可空卡，不可编造（evidence-assembly + decisionCard 双处一致）。
2. **分层调用**：入口→子技能→组装，**工具不可跨层挪用**（每组的"不调用 X"是硬边界）。
3. **延迟预算各自封顶**：检索路 8s / 推理路 25s，超时降级不静默截断。
4. **域无关骨架**：关系模板与域词表全部参数化，换域只改 `config` + 注入变量。

### 3.2 `skill_template_t` 参数化模板库（已落库、前端可浏览）
- 来源：从真实决策卡轨迹由 `mine_skill_templates` 归纳，存 `nexent.skill_template_t`。
- 形态：渲染前 `body_md` 原文 + 变量占位（`{relation_template}` / `{domain_rules}`）。
- 复用方式：前端 `SkillTemplatePanel` 预览/复制 → 经 `skill_template_apply` MCP 工具实例化（变量注入由工具完成）。
- **当前限制（诚实标注）**：模板清单的只读 HTTP 路由尚未接线（见 `SkillTemplatePanel.tsx` 的"数据源待接线"警告），模板库可 psql 直查 `skill_template_t` 核实；实例化已可用（MCP 工具）。

---

## 四、推广到新行业域（通用推广价值）

官方维度④还要求"具备通用推广价值"。基于上述参数化设计，换域动作收敛为：
1. 改 `corpus/registry.csv`（换语料） + 换迁移脚本（换域词表 / 关系模板）；
2. 复用 4 组骨架不动，仅改 `variables.relation_template` 与 `config`；
3. 重新跑 `mine_skill_templates` 归纳该域模板。
→ **"换域零改骨架"是通用推广的实证基础**（多行业实测由任务Q4-C1 / 任务Q5a 补，本文只作结构性论述，未把没跑的迁移写成已验证）。

---

## 五、与官方评分句的对应

维度④原句：「……明确规划可复用 Skill 工作流模板沉淀方案，具备通用推广价值」。
- 本文 §二 = 方法论（从实践到模板的六步）；§三 = 4 组模板的复用边界（可复用方案）；§四 = 通用推广价值（换域零改骨架）。
- 未编造任何成功率 / 数据集数字；缺真实复用统计处标 `insufficient_data`。
