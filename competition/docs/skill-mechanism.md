# skill-mechanism.md —— Nexent 原生 Skill 机制 spike 结论（T-20 硬前置）

- 作者：T-20 域内子智能体（第 2 次尝试）
- 日期：2026-09-19
- 方法：只读源码走查（未改任何上游文件），全部结论带源码行号；与 T-20-brief 背景核验逐条对照。
- 结论先行：**spike 实测与简报背景核验一致，无冲突**（差异仅为 ±1 行号级别的偏移，见 §7）。

## 1. SKILL.md frontmatter 字段（解析器契约）

解析器：`sdk/nexent/skills/skill_loader.py` `SkillLoader`。

- frontmatter 分割正则：`^---\s*\n(.*?)\n---\s*\n(.*)$`（`skill_loader.py:26`，`_split_frontmatter` :206-211）。**必须以 `---` 开头且成对出现**，否则 `SKILL.md must have YAML frontmatter`（:43-44）。
- 必填字段：`name`（:59-60）、`description`（:61-62）。缺失即 `ValueError`，上传路径将其包装为 `Invalid SKILL.md format`（`backend/management/services/skill/service.py:415`）。
- 允许的字段白名单 `_ALLOWED_SKILL_META_KEYS`（:14-20）：`name / description / allowed-tools / tags / script_outputs`。**其他键（如 `version`、`author`）被静默过滤，不报错也不保存**（:64-65）——注意仓库 `knowevo/skills/` 里 T-00 脚手架 stub 写的 `version: 1.0.0` 实际会被丢弃，写不写无效果。
- `allowed-tools` 解析为字符串列表（:70；YAML flow list 或 regex fallback :197-201）；映射为 tool_ids 存 `ag_skill_tools_rel_t`（`service.py:440` → `skill_db.get_tool_ids_by_names`，`backend/database/skill_db.py:674-692`）。**未注册的工具名被静默过滤（只查 `ToolInfo.name IN (...)`，查不到就返回空），上传不报错**——因此 `decision_card_render`（T-19 待注册）预先写进 allowed-tools 是安全的。
- `tags` 只保留非空字符串（:77-82）；body（`---` 之后的部分）strip 后作为 `content`（:73）。
- YAML 容错：`_fix_yaml_frontmatter`（:84-141）会给含特殊字符的未加引号标量自动加引号；失败时退到 regex 抽取（:143-203）。
- 回写（save_skill → `to_skill_md`，:214-239）：只序列化 name/description/allowed-tools/tags/script_outputs 五键。

## 2. 目录与 config 约定

- 本地目录布局：`<SKILLS_PATH>/<tenant_id>/<skill_name>/SKILL.md`。`SkillManager.resolve_tenant_dir`（`sdk/nexent/skills/skill_manager.py:67-94`，tenant_id 穿越防护）、`resolve_skill_dir`（:96-101）、`save_skill` 写 SKILL.md 与附加文件（:190-246）。
- `SKILLS_PATH` 环境变量经 `CONTAINER_SKILLS_PATH = os.getenv("SKILLS_PATH")`（`backend/consts/const.py:97`）注入各 skill 工具的 `local_skills_dir` 参数（`backend/agents/create_agent_info.py:795,809,821,837`）。
- **config/config.yaml** → `config_values`（运行时值 dict）：ZIP 上传经 `_read_params_from_zip_config_yaml`（`service.py:449`；查找逻辑 `support.py:318-328`，任意深度找 `config/config.yaml`）；本地目录 overlay 经 `_enrich_configs_from_yaml`（`service.py:137-176`）。
- **config/schema.yaml** → `config_schemas`（参数元数据列表）：`_parse_skill_schema_from_yaml_bytes`（`backend/management/services/skill/support.py:843-855`）。期望结构：

```yaml
param_name:
  type: string | number | boolean | array | object
  required: true | false
  description_en: "English description"
  description_zh: "中文描述"
  depends_on: other_param_name
```

- config 读取工具：`ReadSkillConfigTool` 读 `<skill_dir>/config/config.yaml`（`sdk/nexent/core/tools/read_skill_config_tool.py:66`），并合并 per-agent `config_overrides`（:45；overrides 来自 `ag_skill_instance_t.config_values`，装配于 `create_agent_info.py:768-772,822`）。

## 3. 最小可加载 SKILL.md 样例（从 create_skill_from_file 反推）

入库链路：`POST /skills/upload`（`backend/apps/skill_app.py:202-252`）→ `SkillService.create_skill_from_file`（`service.py:351-361`）→ `_save_skill_upload`（:363-475）：`normalize_skill_upload` 判 md/zip → `SkillLoader.parse`（:410）→ 查重（:430，重复 409）→ `skill_db.create_skill`（:464）→ `skill_manager.save_skill`（:466）落本地目录。

最小样例（已按 §1 契约裁剪到不可再少）：

```markdown
---
name: retrieval-path
description: 检索路执行：混合检索+图谱一跳，收集单点事实证据。单点事实类问题使用。
---

# 检索路

1. 调用 `kg_search` …
2. 输出 EvidenceBundle …
```

红线清单（触发即上传失败）：
- 无成对 `---` frontmatter（`skill_loader.py:43-44`）；
- 缺 `name` 或 `description`（:59-62）；
- md 上传但 skill_name 重名 → 409（`service.py:430`、`skill_app.py:246-247`）。

`ReadSkillMdTool` 运行时读取（`sdk/nexent/core/tools/read_skill_md_tool.py`）：默认读 `SKILL.md` 并**剥离 frontmatter**（:81-91, :116-117, :157-162）；`SKILL.md not found in skill: <name>` 错误串在 :161（简报判据 4 所指）。空 `skill_name` 时按根目录直读（:140-141, :187-218）。

## 4. Agent 如何拿到技能清单（authorized_skill_names / prompt 注入）

1. **DB 来源**：`ag_skill_instance_t`（per-agent enabled，`backend/database/db_models.py:1903`）→ `skill_db.search_skills_for_agent` → `SkillService.get_enabled_skills_for_agent`（`service.py:773-819`），合并仓库级 `config_values`/`config_schemas`（:801-812）。
2. **system prompt 注入**：`create_agent_info.py:1363` `_get_skills_for_template`（:554-583，取 name+description）→ `render_kwargs["skills"]`（:1389）与 `build_context_inputs(skills=...)`（:1457）→ `backend/utils/context_utils.py:611-620`（`system:skills_usage` 项）→ 渲染 `<available_skills><skill><name/><description/>…` 块（`sdk/nexent/core/agents/context/formatting.py:124-134`）。
3. **使用规则注入**（这是"分层编排"的平台支撑）：`_format_skills_usage_requirements`（formatting.py:475-526）明确要求——① description 匹配即必须先 `read_skill_md()` 再照指南执行；② 忠实执行不跳步；⑤ **技能组合：按逻辑依赖顺序依次加载，前一技能的输出可作为后一技能的输入**。即"入口 Skill → 子任务 Skill"的编排是平台 prompt 规则背书的标准用法，无需自定义运行时。
4. **authorized_skill_names**：仅 `RunSkillScriptTool` 有硬白名单——`create_agent_info.py:797` 传入 enabled skill 名的排序列表，SDK 侧越权调用拒绝（`sdk/nexent/core/tools/run_skill_script_tool.py:244`）。`ReadSkillMdTool`/`ReadSkillConfigTool` 本身不做名单校验（构造参数有 agent_id/tenant_id 供租户目录隔离，:52-72），隔离靠 `ag_skill_instance_t` 决定哪些 skill 对该 agent 可见 + 文件系统租户目录。
5. **skill 隐式带工具**：enabled skills 的 `tool_ids` 会把未显式启用的工具并入 agent 工具集（`create_agent_info.py:_resolve_runtime_tool_records` :1541-1608）——SKILL.md 的 allowed-tools 因此有真实语义：上传后该 skill 启用即自动挂载其声明的工具。

## 5. 分层编排落法（T-20 四个 SKILL.md 的设计依据）

- 技术方案 §3.4（根 [`02-技术方案.md`](../../../02-技术方案.md)，**勿引用 `docs/plan/` 快照**）的编排树：`domain-asset-cognition`（入口·路由）→ `retrieval-path` / `reasoning-path` → `evidence-assembly`（含 decision_card_render）。
- 机制映射：入口 SKILL.md 的 body 写"路由判断 + 指示 `read_skill_md("retrieval-path"/...)`"；平台规则（§4.3）保证子技能按序加载、输出接力；每个子技能 body 写"允许原子工具 + 输出契约"，frontmatter `allowed-tools` 声明 `knowledge_base_search/kg_search/kg_multi_hop/decision_card_render`（后者 T-19 注册前被静默忽略，见 §1）。
- "流程在 SKILL.md、职责在 duty prompt"（简报实现备注 3）：SKILL.md body 是流程层；agent duty prompt 不复制流程。

## 6. `skill_template_t`（T-20 模板库落点，零 ALTER 确认）

- 模型：`backend/database/knowevo_db.py:281-302` `SkillTemplate`，表 `skill_template_t`（schema 见 `SCHEMA`），字段：`id(UUID pk)/tenant_id/not null: name(String64,Unique uq_skill_template_name), task_type(String64), domain(String32), version(String20), body_md(Text 参数化 SKILL.md 全文), variables(JSONB {domain,task_type,relation_template,domain_rules}), source(JSONB {pattern,mined_from,induced_at}), reuse_count(Integer default 0), reuse_success(Float), avg_edit_distance(Float), created_at`。
- 通用行助手：`_get_db_session`（:367-373，包装 `database.client.get_db_session`）、`create_row`（:393-399）、`get_by_id`（:402-411）、`list_tenant_rows`（:414-423）。服务层可直接复用（`decision_service.py:1124-1139` 的 persist 即此模式）。
- 全仓 grep 确认：除模型/DDL/测试外无读写方，`skill_template_apply` MCP 注册留给集成轮。

## 7. 与 T-20-brief 背景核验的逐条对照

| 简报断言 | spike 实测 | 判定 |
|---|---|---|
| `ag_skill_info_t` db_models.py:1843 | `:1843` | 一致 |
| `ag_skill_tools_rel_t` :1888 | `:1888` | 一致 |
| `ag_skill_instance_t` :1903 | `:1903` | 一致 |
| `create_skill` service.py:276 | `:276` | 一致 |
| `create_skill_from_file` :351 | `:351` | 一致 |
| `get_enabled_skills_for_agent` :773 | `:773` | 一致 |
| SKILL.md 解析/ZIP :398/:415 | `:398` ZIP 缺 SKILL.md 报错；`:415` "Invalid SKILL.md in ZIP" 标签 | 一致（同一解析块 :407-421） |
| create_agent_info.py:1558 读 enabled skills | `:1558` `skill_db.search_skills_for_agent` | 一致 |
| :804 ReadSkillMdTool / :816 ReadSkillConfigTool / :827 WriteSkillFileTool / :790 RunSkillScriptTool | :804 / :814-816 / :828-829 / :779(类) :790(authorized params) | 一致（±1-2 行） |
| skill_template_t knowevo_db.py:281 | `:281` | 一致 |
| 简报称"三个 SKILL.md" | 简报文件清单实际列 4 个 skill 目录；验收写"至少 3 个加载" | 表述差异，按 4 个文件、≥3 个可加载执行 |

**无实质冲突，继续实现。**

## 8. 对实现的其他约束性发现（坑位预警）

1. frontmatter 写 `version:` 无效（被过滤，§1）——模板版本号应落在 `skill_template_t.version` 列与 body 正文，不放 frontmatter。
2. `description` 落库列 `skill_description` 为 String(1000)（db_models.py:1875），frontmatter description 控制在 1000 字符内。
3. skill 名在租户内 active 唯一（部分唯一索引，db_models.py:1847-1856）；`competition/skills/` 四个名字与 `knowevo/skills/` stub 同名——真栈上传时若 stub 曾入库会 409，需先确认/删除（集成轮验证时的注意事项）。
4. 上传 md 时 `decode_skill_text` 做 UTF-8/GB18030 解码（`sdk/nexent/skills/text_codec.py`），中文 SKILL.md 直接可传。
5. `ReadSkillMdTool` 剥 frontmatter 后给模型的是纯 body——**SKILL.md 的信息必须写在 body，不能只写在 frontmatter**。
