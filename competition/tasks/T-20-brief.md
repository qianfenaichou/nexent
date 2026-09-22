# T-20：Skill 分层编排 + SKILL.md 模板库（D6）

**状态**: ✅ 完成（2026-09-19 域内轮 3ca491f66 + 集成轮闭环；唯一遗留=模板列表 HTTP 路由未接线（简报未授权路由，见 Evidence 待接线声明））
**Blocked by**: T-18a（导航接线模式复用）
**独占文件**（本任务创建/修改）:
- `competition/skills/`（**新建目录**：三个 SKILL.md 模板 + config，域内自包含）
  - `domain-asset-cognition/SKILL.md`（入口·路由）
  - `retrieval-path/SKILL.md`（检索路）
  - `reasoning-path/SKILL.md`（推理路）
  - `evidence-assembly/SKILL.md`（证据组装）
- `backend/services/knowevo/skill_template_service.py`（**新建**：模板挖掘/参数化/复用统计，落 `skill_template_t`）
- `backend/services/knowevo/pipeline/mine_skill_templates.py`（**新建**：决策轨迹 → 模式归纳 → 模板候选）
- `backend/prompts/knowevo_skill_induce_{en,zh}.yaml`（**新建**：模板归纳 prompt，双语成对）
- `mcp_servers/knowevo_mcp/schemas.py` + `server.py`（**自包含**：新增 `skill_template_apply` 工具）
- `backend/tool_collection/mcp/kg_tools.py`（加性纳入新工具）
- `frontend/features/skillTemplate/` + `frontend/app/[locale]/skillTemplate/page.tsx` + service + types（**新建**）
- `frontend/components/navigation/SideNavigation.tsx` + locale（**接线**：模板库导航项）
- `deploy/sql/migrations/v2.5.5_kw_007_skill_template_rbac.sql`（**新建**）
- `test/backend/services/knowevo/test_skill_template_service.py`（**新建**）
- `competition/docs/pitfalls.md` / `evolution-log.md`

**待接线项**: 无（本任务即接线任务）
**禁改清单**: `backend/consts/const.py`、`apps/app_factory.py`、上游 `backend/management/services/skill/*`（**只调用不修改**）、`backend/agents/create_agent_info.py`（上游，禁改）、`decision_service.py` 算法、`pipeline/eval_*`（T-18c 独占）
**允许的新依赖**: **无**（复用 Nexent 原生 Skill 机制；模板挖掘为纯 Python）

**要构建的行为**（用户视角端到端）:
1. **Skill 分层编排落地**：Nexent Agent 通过三个 SKILL.md（入口→子任务→原子 MCP 工具）完成"检索-推理"双驱动执行流；每个 SKILL.md 声明职责、允许调用的原子工具、输出契约；
2. **模板库**：从真实决策轨迹（`decision_card_t` 的历史卡）归纳出**参数化 SKILL.md 模板**，落 `skill_template_t`（表已存在、零 ALTER），前端 `/skillTemplate` 页可浏览/预览/复制；
3. **复用闭环**：`skill_template_apply` 工具把模板实例化为具体任务（注入 domain/参数），复用次数与成功率回写 `reuse_count`/`reuse_success`；
4. **判据**：至少 1 个模板被真实实例化跑通一次（复用统计非零），且三个 SKILL.md 能被 Nexent 原生 skill 上传/加载机制识别（`SKILL.md not found` 类错误不出现）。

**背景（现状核验，2026-09-18）**:
- **Nexent Skill 机制（已核验）**：skills 是 **SKILL.md 目录 + SQL 表**，非独立 `SkillTool` 原语。
  - 表：`ag_skill_info_t`（`skill_id/skill_name/tenant_id/skill_content`，`db_models.py:1843`）、`ag_skill_tools_rel_t`（:1888）、`ag_skill_instance_t`（:1903，per-agent enabled）。
  - 服务：`backend/management/services/skill/service.py` — `SkillService.create_skill`(:276)、`create_skill_from_file`(:351)、`get_enabled_skills_for_agent`(:773)；SKILL.md 解析/ZIP 在 :398/:415。
  - Agent 侧：`backend/agents/create_agent_info.py:1558` 读 enabled skills；:804 `ReadSkillMdTool`、:816 `ReadSkillConfigTool`、:827 `WriteSkillFileTool`、:790 `RunSkillScriptTool`（SDK 实现于 `sdk/nexent/core/tools/`）。
  - HTTP：`backend/apps/skill_app.py`（`GET ""`、`POST /install`、`POST /upload`（收 SKILL.md 或 ZIP）、`GET /{skill_name}/files` 等），挂在 config_app:154 / runtime_app:61。
  - 前端：`frontend/app/[locale]/skill-space/` 已有 Skill 仓库页（MineSkillsView/RepositoryView）。
- **`skill_template_t` 零代码**：模型在 `knowevo_db.py:281`（字段 `name/task_type/domain/version/body_md/variables/source/reuse_count/reuse_success/avg_edit_distance`），DDL 在 `kw_001:184`，**无 service / app / MCP / 前端读写**（grep 仅命中模型+DDL+测试）。
- **编排原语**：`sdk/nexent/core/agents/nexent_agent.py:219 NexentAgent`、`_wrap_subagent`(:639)、`managed_agents`(:723)、`SubAgentToolWrapper`(:22，发 `subagent_start/end` 信号)；`is_manager`(:1097)。
- **MCP 注册 seam**：新工具 = `server.py` 加 `@mcp.tool` + `schemas.py` 加 Pydantic 模型 + `kg_tools.py` 的 `handlers()/tool_schemas()` 纳入；`local_mcp_service.py` 的单一 `mount` 自动覆盖。
- **prompt 约定**：`backend/prompts/knowevo_*_{zh,en}.yaml` 双语成对；`skill_creation_simple_{zh,en}.yaml` 是上游既有 skill 创建模板（可参照格式）。

**验收命令**:
```bash
# 0. ★ 4h spike 产物（开工第一步，先产出再写代码）：
#    读上述 skill 源码，产出 competition/docs/skill-mechanism.md：
#    - SKILL.md frontmatter 字段、目录约定、config/config.yaml 结构
#    - 从 create_skill_from_file 反推"最小可加载 SKILL.md"样例
#    - Agent 如何拿到 authorized_skill_names、如何触发 ReadSkillMdTool
#    （spike 若发现机制与上述核验不符 → 停下报告，不硬写）
# 1. 模板服务单测
cd backend && uv run pytest ../test/backend/services/knowevo/test_skill_template_service.py -q --no-header
# 2. ruff
cd backend && uv run ruff check services/knowevo/skill_template_service.py services/knowevo/pipeline/mine_skill_templates.py
# 3. 模板挖掘真跑（从 decision_card_t 历史卡归纳）
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> KW_LLM_MID_MODEL_ID=7 KW_LLM_LARGE_MODEL_ID=8 \
  uv run python -m services.knowevo.pipeline.mine_skill_templates --min-support 2
# 4. MCP 工具真跑（模板实例化）
cd backend && uv run python -c "
import asyncio
from mcp_servers.knowevo_mcp.schemas import SkillTemplateApplyInput
from mcp_servers.knowevo_mcp.server import skill_template_apply
print(asyncio.run(skill_template_apply(SkillTemplateApplyInput(template_name='<名>', variables={'domain':'t2dm'}))))
"
# 5. 三个 SKILL.md 可被原生机制加载（上传后 GET files 可见）
curl -s -X POST localhost:3000/api/skill/upload -H 'Authorization: Bearer <t>' -F 'file=@competition/skills/retrieval-path/SKILL.md'
# 6. 前端类型检查
cd frontend && npm run type-check
# 7. PG 集成全量
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent \
  NEXENT_POSTGRES_PASSWORD=<pw> RUN_POSTGRES_INTEGRATION=1 \
  uv run pytest ../test/backend/services/knowevo/ -q --no-header
```

**验收标准**:
- [ ] `competition/docs/skill-mechanism.md` 产出（4h spike 结论，含最小可加载 SKILL.md 样例与源码行号）
- [ ] 三个 SKILL.md 落 `competition/skills/`，结构对齐 02-技术方案 §3.4 的分层（入口→检索路/推理路→证据组装），每个声明：职责、允许原子工具（`knowledge_base_search`/`kg_search`/`kg_multi_hop`/`decision_card_render`）、输出契约、**不含流程硬编码**（流程在 SKILL.md，职责在 duty prompt）
- [ ] 至少 3 个 SKILL.md 通过 Nexent 原生 `POST /api/skill/upload` 加载（真栈验证；若栈不可用，用 `create_skill_from_file` 进程内调用验证并说明）
- [ ] `skill_template_service.py`：归纳（轨迹→参数化模板）、落库（`skill_template_t` 只 INSERT/UPDATE，零 ALTER）、实例化（`apply`）、复用统计（`reuse_count`/`reuse_success` 回写）
- [ ] `mine_skill_templates.py` 从真实 `decision_card_t` 卡归纳出 ≥1 个模板，`variables` 含 `{domain, task_type, relation_template, domain_rules}`，`source` 含 `{pattern, mined_from, induced_at}`
- [ ] `skill_template_apply` MCP 工具双注册（standalone + local），schema 单一来源
- [ ] 前端 `/skillTemplate` 页：列表/预览 body_md/复制；导航双闸门（ROUTE_CONFIG + `kw_007` RBAC）
- [ ] **复用闭环真跑**：至少 1 个模板实例化成功且 `reuse_count >= 1`（真库查询为证）
- [ ] 模板归纳 prompt 双语成对（`knowevo_skill_induce_{en,zh}.yaml`）
- [ ] 零新依赖；零新增 env；未改上游 skill 服务
- [ ] `pitfalls.md` 补记 skill 机制摸索坑；`evolution-log.md` 记录模板沉淀（能力沉淀台账）

**Evidence**（2026-09-19 两轮闭环实测，细节见 `../../../archive/ai-loop-scratch-2026-09/task_b_status-mission-r1-r26/t20-domain.status.json` / `t20-integration.status.json`，2026-09-22 随 `.task_b_status/` 整体归档）:

```text
[spike] competition/docs/skill-mechanism.md：与简报背景核验一致（±1 行号）；要点=frontmatter
        白名单五键（其余键静默丢弃）、allowed-tools 未注册名静默过滤、config/config.yaml 与
        schema.yaml 双通道——三条静默过滤陷阱记 pitfalls #44
[域内]  4 个 SKILL.md（入口/检索路/推理路/证据组装）+ skill_template_service.py +
        mine_skill_templates.py + knowevo_skill_induce_{zh,en}.yaml；35 单测全绿
[挖掘]  真跑 --cross-tenant --tenant 6756b0ab（LLM 配置租户作用域，pitfalls #44）：
        2 模板 LLM 归纳落库 reasoning_decision-general / refusal-general（各 support=50），
        source={pattern, mined_from, induced_at, cross_tenant:true}；cost-ledger 行
        mine-b64f1858（dry-run）/ mine-66f9eddc（真跑 1652+9096 tokens）
[复用]  MCP apply 真跑 reuse_count 1→2（真库为证）；apply 不调 record_reuse_outcome、
        不虚构成功率（诚实契约，payload 无 reuse_success 键）
[集成]  skill_template_apply 双注册（server.py handler + @mcp.tool / kg_tools.py 第 5 工具）；
        template_not_found 结构化错误不泄内部
[kw_007] 真库应用（幂等）后查询：role_permission_t 1520=SU / 1521=ADMIN '/skillTemplate' 两行
[加载]  栈未起（如实记录）→ 简报后备路径：create_skill_from_file 进程内验证 4 个 SKILL.md
        全部通过原生解析加载（ag_skill_info_t 落 4 行，tags 全解析；allowed-tools 映射与
        spike §1 预测一致）
[验收]  单测 57 passed（35 服务 + 22 MCP）/ ruff 全绿 / type-check 通过 /
        PG 集成全量 516 passed（494 基线 + 22 新增，零回归）
```

**待接线声明（诚实）**：模板列表 HTTP 路由未建——T-20 简报未授权任何 HTTP 路由，而
knowledge_graph_app.py 属 T-19 已提交领地，集成轮拒绝越权。交付为"前端页 + 类型 + 服务层
就绪"（页面 catch 后渲染"数据源待接线" Alert，不编造数据）；下一轮在 knowledge_graph_app.py
加只读 GET 路由（约定 GET /api/knowevo/skill-template/list）即激活完整列表 UI。
pitfalls #46 记录：进程内真跑必须带完整 DB env 配方。

---

## 实现备注（给实现 agent）

1. **4h spike 是硬前置**：用户任务书明确"先 4h spike 摸清 Nexent Skill 机制"。**spike 结论与本文背景核验冲突时，以 spike 实测为准并停下报告**（反幻觉条款）。
2. **"Skill 分层编排"的评分价值**（维度①原文"Skill 分层编排逻辑清晰"）：必须能画出 `Agent → 入口 Skill → 子任务 Skill → 原子 MCP 工具` 的调用关系图，并作为 T-23 交付物素材。SKILL.md 是**可复用**的（维度④"可复用 Skill 工作流模板沉淀"），不是一次性 prompt。
3. **不要在 SKILL.md 里写死流程**：02-技术方案 §3.4 明确"Agent duty prompt 只写职责与约束，流程在 Skill"。这是答辩时"模块解耦性"的证据。
4. **`skill_template_t` 已存在，零 ALTER**：服务层只做 CRUD + 归纳/实例化逻辑。表结构见 `knowevo_db.py:281`。
5. **模板挖掘的输入是真实数据**：`decision_card_t` 现有 76 张卡（真库），足够做归纳。**不得编造轨迹**；若模式支持度不足（`--min-support 2` 无候选），如实报告并降低支持度阈值或标注"样本不足"。
