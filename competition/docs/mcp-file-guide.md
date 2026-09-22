# MCP 文件说明（MCP File Guide）· Nexent-KnowEvo

> **定位**：官方初赛提交要求「json 文件、知识库和 MCP 等文件的说明」**成组独立提交**。本文件即其中「MCP」一册，与 [`kb-file-guide.md`](kb-file-guide.md)（知识库册）配对。
> 素材原散落在 `competition/docs/agent-config.md` §3 与 `mcp_servers/knowevo_mcp/SPEC.md`；此处独立成文并按「文件 → 职责 → 关键内容 → 生成者/消费者」展开，**不改母本**。
> **事实基准日**：2026-09-23。文中每个数字都在 §7 给出 `file:line` 出处，可逐条回溯；查不到出处的一律写「待确认」，不估算、不占位。
> **平台版本口径**：对外一律写 **v2.6.0**（`verification-reports/platform-facts-redline.md` §3 第 1 条）；仓库内 `v2.5.5_kw_*` 迁移文件名与代码注释里的 `v2.5.1` 是**历史/部署事实，保留不改**，仅作仓内引用（见 §8 第 4 条）。

---

## 0. 一句话说清这条链路

**一份 Pydantic schema（`mcp_servers/knowevo_mcp/schemas.py`）→ 一份 handler 实现 + 一份 FastMCP 应用（`server.py`）→ 导出到两个注册面**：① 独立 FastMCP 服务（部署形态，`mcp run` / Docker）；② 平台内置 Local MCP 的内嵌注册（`backend/tool_collection/mcp/kg_tools.py` 被 `local_mcp_service.py` 挂载）。**两个面共用同一个应用实例，因此每个工具名只有一份定义**——这就是「单 schema 源、双注册、禁双份漂移」。

**实际注册的自研工具 = 5 个**：`kg_search` / `kg_stats` / `kg_multi_hop` / `decision_card_render` / `skill_template_apply`（唯一权威：`backend/tool_collection/mcp/kg_tools.py:53-55` 的 `KG_MCP_TOOL_NAMES`）。

---

## 1. 文件总表（文件 → 职责 → 关键内容 → 生成者 / 消费者）

### 1.1 服务与实现（`mcp_servers/knowevo_mcp/`）

| 文件 | 职责 | 关键内容 | 生成者 | 消费者 |
|---|---|---|---|---|
| `mcp_servers/knowevo_mcp/schemas.py`（193 行） | **单一 schema 源**：5 个工具的入参/出参 Pydantic 模型 + 2 个共享卡片形状 + 结构化错误 | `EntityCard` / `EdgeCard` / `ToolError` / `KGSearch{Input,Output}` / `KGStats{Input,Output}` / `KGMultiHop{Input,Output}` / `HopStep` / `KGMultiHopPath` / `DecisionCardInput` / `SkillTemplateApplyInput` | 人工编写（T-07b 起，T-09/T-19/T-20 加性扩展） | `server.py`（装饰器注册）、`backend/tool_collection/mcp/kg_tools.py`（内嵌注册）、离线文档/测试（`tool_schemas()`） |
| `mcp_servers/knowevo_mcp/server.py`（498 行） | handler 实现 + 独立 FastMCP 应用 `mcp` | 5 个 `@mcp.tool(...)` 注册（:452 / :459 / :465 / :472 / :482）；handler 实现（:208 `kg_search` / :244 `kg_stats` / :267 `kg_multi_hop` / :355 `decision_card_render` / :407 `skill_template_apply`）；`_request_tenant()` / `_tenant()` 租户解析；`configure()` 注入 store | 人工编写 | ① `mcp run` 独立服务；② `kg_tools.py:60` 把它 import 成 `knowevo_mcp_app` 供平台内嵌挂载 |
| `mcp_servers/knowevo_mcp/__init__.py` | 包标记 | 空文件（0 行） | — | import 路径 |
| `mcp_servers/knowevo_mcp/tests/` | 服务层测试 | — | 人工编写 | `pytest` |
| `knowevo/mcp_servers/knowevo_mcp/SPEC.md`（45 行） | **接口冻结文档**（契约包内） | 形态与注册决策（:5-7）；**8 工具冻结词表**（:9-10） | 人工编写（规划期） | `schemas.py:4` 引用它作为词表依据 |

### 1.2 平台侧接线（`backend/tool_collection/mcp/`）

| 文件 | 职责 | 关键内容 | 生成者 | 消费者 |
|---|---|---|---|---|
| `backend/tool_collection/mcp/kg_tools.py`（117 行） | **内嵌注册适配器**：把独立服务的同一份应用交给平台 Local MCP | `SERVICE_NAME="knowevo"`（:52）、`KG_MCP_TOOL_NAMES`（:53-55，**工具数唯一权威**）、`tool_schemas()`（:63-71）、`handlers()`（:74-82）、`wire()`（:85-99，含 `KW_MCP_TENANT_ID` 服务级锚定） | 人工编写（T-07b 建，T-08 接线，T-27 加 `KW_MCP_TENANT_ID`） | `local_mcp_service.py` 的 `mount()` |
| `backend/tool_collection/mcp/local_mcp_service.py`（34 行） | **平台 Local MCP 挂载点**（上游共享文件，冻结） | `local_mcp_service.mount(wire_knowevo_mcp(), KNOWEVO_MCP_SERVICE_NAME)`（:19-21）；同文件另挂 `nl2agent_mcp_service` | 上游 + T-08 接线（**仅加挂载调用**） | 平台 Agent 运行时 |
| `backend/prompts/knowevo_*.yaml` | Agent 侧提示词（**不含**工具定义） | — | 人工编写 | Agent 运行时 |

> **归属规则的由来**：`local_mcp_service.py` 是上游共享文件，改动只能由专门接线任务（T-08 系列）集中进行；`mcp_servers/` 与 `kg_tools.py` 是自包含产物，日常演进在这里发生——所以新增工具**改 `schemas.py` + `server.py` 即可**，挂载点是「同一个应用实例」，下次接线运行自动带上，无需二次挂载调用（`kg_tools.py:12-15` 明写此设计）。

### 1.3 Skill 侧引用（`competition/skills/`，4 组）

工具的**调用方**是 Skill 层，不是各自直连：

| Skill | 引用的自研工具（出现次数为文件内**引用次数**，非运行时调用数） |
|---|---|
| `retrieval-path` | `kg_search` ×8、`kg_multi_hop` ×1、`decision_card_render` ×1 |
| `evidence-assembly` | `decision_card_render` ×6、`kg_search` ×3、`kg_multi_hop` ×1 |
| `reasoning-path` | `kg_multi_hop` ×5、`kg_search` ×3、`decision_card_render` ×2 |
| `domain-asset-cognition` | `kg_search` ×6、`kg_multi_hop` ×1、`decision_card_render` ×1 |

（`kg_stats` 与 `skill_template_apply` 在这 4 组 Skill 的 `SKILL.md`/`config/` 里未被引用；两者分别服务于规模自述与模板实例化，由 Agent 直接调用。Skill 分工与三层编排见 [`agent-config.md`](agent-config.md) §5。）

---

## 2. 五个自研工具逐个说明

> 入参约束均来自 `schemas.py` 的 `Field(...)`，由 MCP 层**硬拦截**（超出范围的载荷在进入业务逻辑前即被拒），这是 SPEC discipline 3。

### 2.1 `kg_search` —— 图谱检索（唯一实现于 SPEC 8 词表内）
- **入参**（`schemas.py:44-51`）：`query`（1–200 字）、`hop`（1–2，默认 1）、`top_k`（1–20，默认 5）、`ontology_version`（可选）
- **返回**（`schemas.py:54-59`）：`entities: [EntityCard]`、`edges: [EdgeCard]`、`valid_view: datetime`、`used_tokens`、`elapsed_ms`
- **语义**：词法实体查找 + 邻域展开（当前视图）。`valid_view` 是该次查询所依据的知识视图时间点，答案可回溯。
- **实现**：`server.py:208`；工具描述 `server.py:452-454`

### 2.2 `kg_stats` —— 图谱规模
- **入参**（`schemas.py:66-67`）：`scope`（`graph` | `full`，默认 `graph`）
- **返回**（`schemas.py:70-78`）：`tenant_id`、`scope`、`entities`、`edges_total`、`edges_valid`、`pending`、`used_tokens`、`elapsed_ms`
- **语义**：读图谱规模数字；`pending` 供待审/未落定计数用。
- **实现**：`server.py:244`

### 2.3 `kg_multi_hop` —— 版本钉住的多跳推理（**本项目的机制核心之一**）
- **入参**（`schemas.py:119-129`）：`question`（1–500 字）、`seeds`（列表，最多 10 个）、`depth`（1–3，默认 3）、`beam`（1–3，默认 3）、`ontology_version`（可选）、`top_k`（1–10，默认 5）
- **返回**（`schemas.py:132-139`）：`paths: [KGMultiHopPath]`、`failed: [KGMultiHopPath]`、`version_pinned`、`ontology_version`、`valid_view`、`used_tokens`、`elapsed_ms`
- **路径形状**（`schemas.py:104-116`）：每跳为 `HopStep{src, dst, rel_type, claim, evidence_id}`，整条路径带 `score` 与 `version_valid`
- **关键设计（必须理解，否则会误读输出）**：`version_valid=False` 的路径**不被隐藏**，而是放进 `failed` 返回——`schemas.py:107-112` 明写理由：调用方需要看到「证据被拒绝」以及为什么；消融实验依赖两个变体都可观测。所以**看到 `failed` 非空不等于工具报错**。
- **硬护栏**：`depth<=3`、`beam<=3` 由 MCP 层强制（`schemas.py:120-122`），幻觉中的 Agent 无法请求无界游走。
- **实现**：`server.py:267`；工具描述 `server.py:465-466`

### 2.4 `decision_card_render` —— 决策卡生产入口
- **入参**（`schemas.py:146-165`）：`question`（1–500 字）、`ontology_version`（可选）、`mode`（`full` | `lite`，默认 `full`）。`full` 带风险与反事实，`lite` 为延迟敏感调用方跳过；**一问一卡，永不批量**（SPEC discipline 3）。
- **返回**：**故意不在此重新建模**——卡片载荷由 `services.knowevo.schemas.DecisionCardContract` 拥有（即 `decision_card_t.payload` 的 wire 格式），重新声明会制造第二个 schema 源，正是本模块要防的漂移。handler 返回该载荷的 JSON-safe dict，另加两个键 `persisted`、`card_id`；失败返回结构化 `{error_code, hint}`。
- **语义**：给出候选、证据链、置信度、风险、反事实、知识版本戳与冲突裁定；**无证据支撑时以 `INSUFFICIENT_EVIDENCE` 诚实拒绝**（真实问答截图 `deliverables/T-27-chat-qa-*.png` 三轮均触发此路径）。
- **实现**：`server.py:355`；工具描述 `server.py:472-476`

### 2.5 `skill_template_apply` —— Skill 模板实例化（SPEC 8 词表之外的第 9 个）
- **入参**（`schemas.py:173-194`）：`template_name`（1–64 字，对齐 `skill_template_t.name` 列 `String(64)`，租户内唯一 → 不可能的名字在 DB 往返前就被 MCP 层拒）、`variables`（dict，最多 16 项；对模板已存默认值 `{domain, task_type, relation_template, domain_rules}` 做注入覆盖，未知 `{占位符}` 原样保留）
- **返回**：载荷由 `services.knowevo.skill_template_service.SkillTemplateService.apply_template` 拥有（`name`/`skill_md`/`variables`/`reuse_count`），handler 附加 SPEC 成本字段（`used_tokens`、`elapsed_ms`），或返回结构化错误（`template_not_found` / `skill_template_apply_failed`）
- **语义边界（重要）**：**成功率不在这里写**——apply 不可能知道结果，`reuse_count` 只在此递增，成功率必须等真实运行后才更新（工具描述 `server.py:483-486` 明写）。
- **实现**：`server.py:407`

---

## 3. 双注册面为什么不会漂移（机制，非承诺）

```
              schemas.py  ← 单 schema 源（5 个 Input/Output 模型）
                   │
                   ├──────────────► server.py 的 @mcp.tool 装饰器 ──► [面 ①] 独立 FastMCP 服务
                   │                    （handler 实现也在这里）
                   │
                   └── kg_tools.py:60  `from ...server import mcp as knowevo_mcp_app`
                                   │
                                   └──► local_mcp_service.py:19-21  mount(...) ──► [面 ②] 平台内置 Local MCP
```

不漂移的**结构性原因**（不是纪律要求，是代码形状决定的）：

1. **面 ② 挂载的不是「另一份定义」，而是面 ① 的同一个 `FastMCP` 实例**——`kg_tools.py:60` 直接 import `server.py` 的 `mcp` 对象，`local_mcp_service.py:19-21` 把它 `mount()` 进去。每个工具名在进程内只有一个注册点。
2. **handler 只有一份**：`kg_tools.py:74-82` 的 `handlers()` 回的是 `server.py` 里那 5 个 handler 函数本身，不是副本。
3. **schema 只有一份**：`kg_tools.py:63-71` 的 `tool_schemas()` 调 `model_json_schema()` 从 `schemas.py` 现算，不维护第二份 JSON。
4. **因此新增工具的最小改动面**：改 `schemas.py` + `server.py` 即可，两个注册面同时生效；`local_mcp_service.py`（上游冻结文件）**无需再动**（`kg_tools.py:12-15` 的设计说明）。

**可离线验证**：`kg_tools.tool_schemas()` 与 `kg_tools.handlers()` 不需要起 MCP runtime 就能取到全量形状与处理器映射，测试与文档均以此为准。

---

## 4. 结构化错误契约 `{error_code, hint}`

- **定义**：`schemas.py:86-88` 的 `ToolError{error_code, hint}`。
- **用途**（SPEC discipline 4，`schemas.py:81-84` 引述）：让 **Skill 层能选择「降级」而不是「盲目重试」**——错误码是可判定信号，`hint` 给出下一步建议。
- **观测到的真实用例**：`template_not_found` / `skill_template_apply_failed`（§2.5）；卡链的 `INSUFFICIENT_EVIDENCE` 走的是**正常返回**而非错误（这是语义结果，不是故障）。
- **成本字段**：每个工具返回 `used_tokens` / `elapsed_ms`，使成本台账能从**最外层边界**采集（SPEC discipline 2，`schemas.py:10-11`）。

---

## 5. 租户解析（本部署形态下的真实行为）

**优先级**：显式参数 > 调用方 `Authorization` 头（JWT）> 服务级默认（`server.py` 的 `_tenant()`）。

**但本部署形态下第 2 级不生效**——这是 2026-09-23 实测出的硬事实：

> 平台内置的本地 MCP 服务**不转发 `Authorization` 头**。MCP 服务端日志实测请求头只有 7 个：`accept`、`accept-encoding`、`connection`、`content-length`、`content-type`、`host`、`user-agent`——**没有 `Authorization`**，故每次调用都落到服务级默认。证据：`verification-reports/t27-chatqa-unlock.md` §一·缺陷 2；踩坑台账 #65。

**实际生效的机制**是服务级锚定：`kg_tools.wire()` 读环境变量 `KW_MCP_TENANT_ID`（`kg_tools.py:97-98`）。**对外表述必须区分「请求级多租户已工作」与「服务级锚定在工作」——本项目是后者。**

代码注释中记为 `Nexent v2.5.1`（`kg_tools.py:92-93`，指本 fork 的部署版本）；对外叙述按 §头部红线要求写 v2.6.0。

---

## 6. SPEC 冻结词表（8）vs 实际注册（5）——口径必须说清

| 口径 | 数量 | 内容 | 出处 |
|---|---|---|---|
| **实际注册（对外唯一口径）** | **5** | `kg_search` / `kg_stats` / `kg_multi_hop` / `decision_card_render` / `skill_template_apply` | `kg_tools.py:53-55`（`KG_MCP_TOOL_NAMES`）；`server.py:452/459/465/472/482`（5 个 `@mcp.tool`） |
| SPEC 冻结词表 | 8 | `kg_search` / `kg_multi_hop` / `kg_evolution_trace` / `ontology_diff` / `asset_search` / `decision_card_render` / `evidence_verify` / `kg_stats` | `knowevo/mcp_servers/knowevo_mcp/SPEC.md:9-10` |
| 词表外追加 | 1 | `skill_template_apply`（T-20，代码自称「additive 9th tool beyond the frozen 8」） | `schemas.py:169-171` |

**换算关系**：冻结 8 个中**已实现 4 个**（`kg_search` / `kg_stats` / `kg_multi_hop` / `decision_card_render`），**未实现 4 个**（`kg_evolution_trace` / `ontology_diff` / `asset_search` / `evidence_verify`）；再加词表外的 `skill_template_apply` → **5 个在册**。

> ⚠️ **对外表述纪律**：只说 **5 个**。历史上多处（`05` §2.4 早期版本、`archive/` 备忘录 A2、`SPEC.md` 本身）出现过「8 个」的说法——那指的是**规划词表**，不是已注册工具数。写对外材料时二者不可混用。

---

## 7. 数字 → 出处 对账表

| 数字 / 事实 | 出处 |
|---|---|
| 自研工具 = 5 个 | `backend/tool_collection/mcp/kg_tools.py:53-55` |
| 5 个 `@mcp.tool` 注册点 | `mcp_servers/knowevo_mcp/server.py:452, 459, 465, 472, 482` |
| 5 个 handler | `mcp_servers/knowevo_mcp/server.py:208, 244, 267, 355, 407` |
| `schemas.py` 193 行 / `server.py` 498 行 / `kg_tools.py` 117 行 / `local_mcp_service.py` 34 行 / `SPEC.md` 45 行 | `wc -l`（2026-09-23 实跑） |
| SPEC 冻结词表 = 8 个 | `knowevo/mcp_servers/knowevo_mcp/SPEC.md:9-10` |
| SPEC 词表内未实现 = 4 个 | 同上，减去 `kg_tools.py:53-55` 中的交集 |
| `kg_search` 入参约束 hop 1–2 / top_k 1–20 | `schemas.py:49-50` |
| `kg_multi_hop` 入参约束 depth 1–3 / beam 1–3 | `schemas.py:126-127` |
| `kg_multi_hop` seeds 上限 10 / top_k 1–10 | `schemas.py:125, 129` |
| `decision_card_render` question 1–500 / mode full\|lite | `schemas.py:163, 165` |
| `skill_template_apply` template_name 1–64 / variables ≤16 | `schemas.py:193-194` |
| 结构化错误形状 `ToolError{error_code, hint}` | `schemas.py:86-88` |
| 租户服务级锚定环境变量 `KW_MCP_TENANT_ID` | `backend/tool_collection/mcp/kg_tools.py:97-98` |
| 平台本地 MCP 不发 `Authorization` 头（7 个头，无它） | `verification-reports/t27-chatqa-unlock.md` §一·缺陷 2；`pitfalls.md` #65 |
| 4 组 Skill 对工具的引用次数 | `competition/skills/*/SKILL.md` + `config/*.yaml`（grep 实数，2026-09-23） |

---

## 8. 已知不一致与待确认项（如实列出）

1. **「8 个工具」的口径残留**：`schemas.py:4` 写「The 8-tool vocabulary is frozen in `knowevo/mcp_servers/knowevo_mcp/SPEC.md`」，`DecisionCardInput` docstring（`schemas.py:147`）写「one of the 8 frozen SPEC tools」——这两处说的是**冻结词表**，与「实际注册 5 个」并不矛盾，但字面容易被读成「注册了 8 个」。**已在 §6 划清口径**；是否改写代码注释属工程决策，本册只如实标注，未自行改代码。
2. **SPEC 声明的调用方式与当前实现存在偏差（需人工裁决）**：`SPEC.md:7` 声明「全部工具经 config 服务的内部 HTTP 调 services 层（**不直连 DB**）——独立服务不复制业务逻辑」；而当前 `server.py` 是**进程内**实现（`_store()` 直接构造 `PgJsonbGraphStore()`，`_service()` 直接 `from services.knowevo.decision_service import DecisionService`）。两种部署形态因此共用同一份进程内实现，独立服务形态下**并非**经 HTTP 中转。按 AGENTS.md 硬规矩 3（契约先行，代码与契约冲突时停下报告而非自行改写），此偏差**登记待裁决**，本册不改契约、不改代码。
3. **`kg_stats` 与 `skill_template_apply` 在 4 组 Skill 中未被引用**：它们是给 Agent 直接调用的（规模自述 / 模板实例化），不在三层 Skill 编排链路上。若对外材料声称「Skill 编排覆盖全部 5 工具」，与事实不符。
4. **版本号双写**：代码注释（`kg_tools.py:92-93`）与平台界面（`deliverables/T-27-chat-qa-*.png` 页脚）都是 `v2.5.1`（本 fork 的部署版本），而对外叙述按红线须写 `v2.6.0`（官方 2026-09-16 发版）。**交付文档需加 fork 时点限定一句**，否则评审会看到页脚与正文不一致。
5. **未确认**：`mcp_servers/knowevo_mcp/tests/` 内的用例清单与覆盖范围，本册未逐条核对（未列为必读输入）。如需在对外材料中引用测试数字，请以 `pytest` 实跑输出为准。

---

## 9. 复现命令（验收用）

```bash
# 1) 工具数唯一权威（应为 5）
cd /home/qianqian/Work/All/Nexent/nexent
backend/.venv/bin/python -c "from backend.tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES as n; print(len(n), n)"

# 2) 两个注册面的工具名集合必须相等（单 schema 源不漂移的离线证明）
backend/.venv/bin/python -c "
import sys; sys.path[:0]=['backend','.']
from tool_collection.mcp.kg_tools import tool_schemas, handlers
print(sorted(tool_schemas()) == sorted(handlers()))"

# 3) 独立服务的注册点（应为 5 行 @mcp.tool）
grep -n '@mcp.tool' mcp_servers/knowevo_mcp/server.py

# 4) SPEC 冻结词表（应为 8 个名字，见 §6 换算）
sed -n '9,10p' ../knowevo/mcp_servers/knowevo_mcp/SPEC.md

# 5) 平台侧挂载点（应是同一个应用实例，不是第二份定义）
grep -n "mount" backend/tool_collection/mcp/local_mcp_service.py
```

**以上 5 条已于 2026-09-23 在本工作区全部实跑**，观测输出：①`5 ('kg_search', 'kg_stats', 'kg_multi_hop', 'decision_card_render', 'skill_template_apply')`；②`True` + 同样这 5 个名字；③`5`；④头部为 `## 8 工具 schema（冻结，备忘录 10 §1 表）` + 8 个名字一行；⑤`:21` 处是 knowevo 挂载（`:15` 处是 nl2agent 的挂载）。**命令 1 与 2 会打印 authlib 弃用告警（`JsonWebKey, JsonWebToken`）——那是依赖层已知弃用提示，不影响输出。**

---

## 10. 与其他交付物的关系

| 交付物 | 关系 |
|---|---|
| [`kb-file-guide.md`](kb-file-guide.md) | **配套册**：知识库侧的文件说明；两册合成官方要求的「知识库和 MCP 等文件的说明」 |
| [`agent-config.md`](agent-config.md) | 母本：智能体配置（模型/工具/知识库/Skill 四节）；本册是其 §3「工具」的展开 |
| [`call-graph.md`](call-graph.md) | 调用关系图（Agent→Skill→MCP→服务→存储），本册对应图中 MCP 层 |
| [`skill-mechanism.md`](skill-mechanism.md) | 平台原生 `SKILL.md` 机制（源码行号级结论） |
| [`verification-reports/t27-chatqa-unlock.md`](verification-reports/t27-chatqa-unlock.md) | **运行时证据**：5 个工具在真实问答中被调用的协议级记录（MCP 服务端 71×`CallToolRequest`）与租户解析实测 |
| [`pitfalls.md`](pitfalls.md) #52 / #57 / #65 | 工具链路相关的踩坑：指标观测、无正文 vs 无实体、租户解析 |
| `deliverables/agent-config.json` | 机器可读的配置快照（含工具绑定），随交付物提交 |
