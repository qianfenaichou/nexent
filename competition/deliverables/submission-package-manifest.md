# 初赛提交物实体打包清单（Submission Package Manifest）· Nexent-KnowEvo

> **用途**：官方初赛提交物第 2 项（智能体设计说明）末句要求「**同时附上需要同步提交的 json 文件、知识库和 MCP 等文件的说明**」——**文件实体要随说明一并提交，不只是写说明文档**。
> 官方原文口径见 `verification-reports/official-site-2026-09-23.md` §三 L42–L44（逐字）：
> - L43：「提交**开发设计文档 Word**，需包含项目概述、整体方案设计、知识图谱构建方案、智能体构建方案、原始数据说明、数据处理说明等」
> - L44：「**Nexent 平台上智能体整体设计思路及详细说明**，包含 Agent 配置的完整细节描述（模型信息、工具信息、知识库信息、调用关系图、调试迭代经验、示例问答截图等等），同时**附上需要同步提交的 json 文件、知识库和 MCP 等文件的说明**」
>
> ⚠️ **本清单只产出「清单 + 打包目录」**。在线填报与最终上传**只有用户能做**（见末尾「必须由用户执行的动作」）。本会话**不提交、不上传**任何内容。
> ⚠️ **版权铁律**：58 份原始语料 PDF/HTML **不入 git、不对外再分发**；知识库实体提交的是**清单 + 元数据（registry.csv / 解析体检 / 摄取清单）**，非原始文档。

---

## 一、五项提交物 → 对应文件 → 打包路径 → 提交方式

| # | 官方要求项 | 我们的对应文件（实体） | 打包路径（相对 `nexent/competition/`，仓库内） | 提交方式 |
|---|---|---|---|---|
| ① | 开发设计文档 Word | 套版稿源 Markdown：`docs/dev-design-doc.md`；已转 Word 母版：`deliverables/【赛题3-模板1】开发设计文档-套版稿-2026-09-23.docx`（29 页，六节齐）；终版导出名见下「命名规则」 | `docs/dev-design-doc.md` · `deliverables/【赛题3-模板1】开发设计文档-套版稿-2026-09-23.docx` | 按官方模板命名规则**重命名导出**后上传（用户执行） |
| ② | 智能体整体设计思路及详细说明 | 本批装配稿：`docs/智能体设计说明-初赛提交版.md`（六节 + 跨域可迁移性节） | `docs/智能体设计说明-初赛提交版.md` | 可另存为 Word/PDF 随说明一并提交（用户执行） |
| ③ | json 文件（Agent 配置） | 机器可读配置快照：`deliverables/agent-config.json`（真实导出，API key 已脱敏） | `deliverables/agent-config.json` | 随说明文档打包提交 |
| ④ | 知识库说明 + 知识库文件实体 | 说明册：`docs/kb-file-guide.md`；实体：知识库清单 `corpus/registry.csv`（58 行）+ 解析体检 `corpus/parse_report.md` + 摄取清单 `corpus/ingest_manifest.json` + 切分 `corpus/blind_split.json`；`deliverables/agent-config.json` 的 `knowledge_base` 块 | `docs/kb-file-guide.md` · `corpus/registry.csv` · `corpus/parse_report.md` · `corpus/ingest_manifest.json` · `corpus/blind_split.json` | 随说明文档打包提交（**原始 PDF 不提交**，仅元数据） |
| ⑤ | MCP 说明 + MCP 文件实体 | 说明册：`docs/mcp-file-guide.md`；实体代码：`backend/tool_collection/mcp/kg_tools.py`（双注册）+ `mcp_servers/knowevo_mcp/server.py` + `mcp_servers/knowevo_mcp/schemas.py` + `backend/tool_collection/mcp/local_mcp_service.py` + `knowevo/mcp_servers/knowevo_mcp/SPEC.md` | `docs/mcp-file-guide.md` · `backend/tool_collection/mcp/kg_tools.py` · `mcp_servers/knowevo_mcp/server.py` · `mcp_servers/knowevo_mcp/schemas.py` 等 | 随说明文档打包提交（代码仓内既有路径） |

> 配套支撑（非独立提交项，但说明文档引用、建议一并归档）：`docs/agent-config.md`、`docs/call-graph.md`、`docs/reproduce-README.md`、`docs/citation-map.md`、`docs/pitfalls.md`、`deliverables/*.png`（16 张截图，含示例问答 ≥3）、`deliverables/submission-package-manifest.md`（本清单）。

---

## 二、官方模板命名规则（开发设计文档 Word）

- **官方模板名（L5 §五）**：`【赛题3-模板1】创新赛-开发设计文档-学校-队名-队长姓名-手机号`
- **下载链接（需登录态）**：`https://e.huawei.com/cn/talent/backplatform/#/download?fileId=16462&edocId=M3T1A82N1301249787291177249`（2026-09-23 实测重定向 UniPortal 登录 → 待用户从已登录 Edge 下载入库）
- **导出动作**：将 `deliverables/【赛题3-模板1】开发设计文档-套版稿-2026-09-23.docx` 按官方模板版式套用，**填写封面 8 项**（见 `deliverables/待用户填写清单.md`），**重命名为上述官方模板名**后提交。
- ⚠️ 当前套版稿封面仍留 8 处【】占位（学校/队名/指导老师/队长姓名/队员姓名×2/手机号/邮箱）——属预期待填项，非内容缺漏。

---

## 三、诚实声明（打包时须随附，避免「材料无法复现 = 取消晋级/获奖资格」）

- **可复现性**：`reproduce-README.md` 提供从 clone 到复现评测数字的完整步骤；PG 集成测试 **721 passed / 30 skipped**（2026-09-24 凌晨实测，见 `archive/overnight-2026-09-24/receipts/T1-pytest-raw.txt`）。
- **尚未全量 / 如实标注项**：① 图谱摄取 15/120 段（r20 收盘；09-23 曾到 35/120 后续跑已停），跨文档规模证据待任务Q2B 起栈续跑；② E2 四级消融 A3(multihop) 待跑、E8 Δ=0.0 为 honest null（n=6 小样本）；③ 120 题全量测试集未建成（当前 20 题 seed + 8 题判别集）；④ 多行业迁移**零实测**（语料 58 份全在糖尿病域），维度①②「多行业」证据待任务Q4。上述均按 `insufficient_data`/`待跑` 如实标注，未编造。
- **平台版本口径**：对外统一写 **v2.6.0**；无 pgvector；「可进化」是本项目 L4 实现，非平台能力。

---

## 四、必须由用户执行的动作（置顶单列）

1. **登录官网下载官方模板1**（`fileId=16462`），把套版稿按官方模板版式套用。
2. **填写封面 8 项**（学校/队名/指导老师/队长姓名/队员姓名×2/手机号/邮箱）+ 准备**团队合照约 2 MB**（见 `待用户填写清单.md`）。
3. **按官方命名规则重命名**开发设计文档 Word（含学校/队名/队长姓名/手机号）。
4. **在线填报 / 作品上传**：在赛事系统把 ①–⑤ 各项文件实体 + 说明文档一并提交（AI 不代上传）。
5. **打包自检**：核对本清单「一」表的 5 组实体路径均存在、未含任何 API key/token/密码/内网 IP/私人手机号邮箱；原始 PDF 不进包。

> 本清单所有路径均为仓库内**既有/已生成**文件，未要求新增任何代码或接线文件。
