# T-23：初赛交付物取证与装配

**状态**: ★ 待开发（2026-09-18 调度会话）
**Blocked by**: T-18a-d、T-19、T-20（多数完成后；可按已有素材先起步）
**独占文件**（本任务创建/修改）:
- `competition/deliverables/`（**截图集 + 素材**）
- `competition/docs/dev-design-doc.md`（**新建**：初赛开发设计文档 Word 骨架的 Markdown 母本）
- `competition/docs/agent-config.md`（**新建**：智能体配置完整细节说明）
- `competition/docs/call-graph.md`（**新建**：调用关系图，Mermaid）
- `competition/docs/reproduce-README.md`（**新建**：复现说明）
- `competition/deliverables/agent-config.json`（**新建**：Agent 配置 json）
- `competition/deliverables/evidence-index.md`（**新建**：素材 → 评分维度映射表）

**待接线项**: 无
**禁改清单**: 所有代码（本任务**只产出文档与截图**，不改代码）；`competition/corpus/` 原始语料（版权铁律 8）
**允许的新依赖**: **无**（截图用既有栈；Word 转换由用户/调度会话决定，本任务产出 Markdown 母本 + 截图）

**要构建的行为**（用户视角端到端）:
按 01-总纲 §6 初赛交付物清单，产出**可直接进初赛文档**的素材：

1. **开发设计文档 Word 母本**（官方模板1 六节）：
   项目概述 / 整体方案设计 / **知识图谱构建方案** / 智能体构建方案 / 原始数据说明 / 数据处理说明 + 评测与结果
2. **智能体设计思路详细说明**：模型信息、工具信息（自研 MCP 清单）、知识库信息、**调用关系图**、调试迭代经验、**示例问答截图**、json/知识库/MCP 文件说明
3. **截图集**（`deliverables/`）：
   - 本体工作台（导航可见 + 本体树 + diff 视图）
   - 决策卡（证据链展开 + 版本戳 + 免责声明）
   - 检索-推理双驱动问答（示例问答截图，≥3 个真实问答）
   - 新旧指南版本对比（同一问题两版答案不同）
   - Skill 模板库页面
   - 评测报告（消融柱状图/曲线）
4. **调用关系图**（Mermaid，可渲染进 Word/PPT）：`Agent → Skill 分层 → MCP 工具 → 服务 → 存储`
5. **素材 → 评分维度映射表**：每张截图/每个数字进哪一维、证据强度

**背景（现状核验，2026-09-18）**:
- 已有截图：`deliverables/T-01-agent-published.png`、`T-01-chat-conversation.png`、`T-01-models-registered.png`（部署基线 3 张，**不够**：初赛要求"示例问答截图"是问答效果，不是部署界面）。
- 已有报告：`e1-baseline-report.json`（E1 基线）、`e1-baseline-run.log`。
- **官方原文**（01-总纲附录A）：初赛提交 ①开发设计文档 Word（六节）②Nexent 平台智能体设计思路及详细说明（含 Agent 配置完整细节、模型/工具/知识库信息、调用关系图、调试迭代经验、**示例问答截图**、json/知识库/MCP 文件说明）。
- **初赛交文档不交代码**（01-总纲 §1.3）——文档质量 = 分数。
- 栈当前状态（2026-09-18）：`nexent-postgresql` 已启动（5434）；web/config/runtime/mcp 等容器 **Exited**——截图前需重新起栈（`deploy/deploy.sh`，见 pitfalls #2 的 mainland 源）。
- 三台账：`pitfalls.md`（42 条，以台账实数为准）、`evolution-log.md`、`cost-ledger.md`——是"调试迭代经验"节的现成素材。
- vision agent 职责：审 `deliverables/` 每张截图，判"作为初赛示例问答截图够不够格"，不合格要求重拍。

**验收命令**:
```bash
# 1. 素材完整性检查（脚本化，可复跑）
ls -la competition/deliverables/*.png competition/deliverables/*.json competition/docs/*.md
# 2. 调用关系图可渲染（mermaid 语法校验）
npx -y @mermaid-js/mermaid-cli -i competition/docs/call-graph.md -o /tmp/call-graph.png 2>&1 | tail -3
# 3. Agent 配置 json 合法
python3 -c "import json;json.load(open('competition/deliverables/agent-config.json'));print('json ok')"
# 4. ★vision agent 审图（每张截图判"够不够格"）
#    按 ~/.zcode/skills/vision/SKILL.md，逐张审 competition/deliverables/*.png
# 5. 示例问答真实性核验：每个问答截图对应的 eval_run_t / decision_card_t 行可查
docker exec -e PGPASSWORD=<pw> nexent-postgresql psql -U root -d nexent -tAc \
  "select id, knowledge_stamp from nexent.decision_card_t order by created_at desc limit 5"
```

**验收标准**:
- [ ] `dev-design-doc.md` 覆盖官方模板1 六节，每节**有数字/图/表**（不写空话）；**知识图谱构建方案**与**智能体构建方案**是重点节（对应维度②③）
- [ ] `agent-config.md` 含：模型信息（档位/id/用途）、工具信息（自研 MCP 清单，逐个说明输入输出）、知识库信息（语料 N 份/类型/切分）、**调用关系图**、调试迭代经验（引用 pitfalls）、json/知识库/MCP 文件说明
- [ ] `call-graph.md` Mermaid 渲染通过；图含 `Agent → 入口 Skill → 检索路/推理路 Skill → MCP 工具 → 服务 → PG/ES`
- [ ] 截图集齐全且**均为真实运行截图**（非 mock）：本体工作台 / 决策卡 / 示例问答 ≥3 / 版本对比 / Skill 模板库 / 评测报告
- [ ] vision agent 逐张审过，不合格的重拍；审查结论记录在 `evidence-index.md`
- [ ] `agent-config.json` 合法且与平台实际配置一致（导出自真实配置，不手写编造）
- [ ] `evidence-index.md`：每张截图/每个数字 → 评分维度 + 证据强度 + 来源文件
- [ ] `reproduce-README.md`：从 clone 到跑通的步骤（含已知坑）
- [ ] **未把受版权 PDF/HTML 或长段原文放进 deliverables**（铁律 8）；截图中的原文引用为合理引用（短句 + 出处）
- [ ] 示例问答的每个答案可回溯到 `decision_card_t`/`eval_run_t` 行（真库查询为证）

**Evidence**: <粘贴文件清单 / mermaid 渲染结果 / vision agent 结论 / psql 卡片查询 / evidence-index 表>

---

## 实现备注（给实现 agent）

1. **本任务不改代码**：只产出文档、截图、图表、配置导出。若截图暴露代码 bug，**登记给对应任务**，不在此处修。
2. **示例问答截图是初赛硬要求**（官方原文点名）：必须是**真实问答**（问题 + 回答 + 证据链 + 版本戳），不是部署界面。每个问答要能对应到真库记录。
3. **诚实性**：文档里所有数字必须来自真实跑分（E1/E2/E3），**不得美化或估算**；预期值与实测值不符时如实写。用户任务书明确"不用'感觉变好了'当证据；不上'稻草人人工基线'"。
4. **Word 转换**：本任务产出 Markdown 母本 + 截图；转 Word 由调度会话按官方模板处理（可用 document-skills:docx）。**母本结构对齐官方模板1**，便于转换。
5. **栈重启**：截图前按 `deploy/deploy.sh --image-source mainland` 起栈（pitfalls #2）；本机 15G 内存，前端 build 与全栈互斥（pitfalls #22），分时操作。
