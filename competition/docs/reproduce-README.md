# 复现说明（Reproduce README）· Nexent-KnowEvo

> T-23 交付物之一。本文档说明从 clone 本 fork 到「平台上真实跑通智能体 + 复现评测数字」的完整步骤。
> 事实基准：Nexent v2.6.0 fork（本机实测，2026-09-13 ~ 2026-09-20）；坑位均来自 `competition/docs/pitfalls.md` 台账（44 条实记，此处只引与本流程直接相关的）。

---

## 0. 前置要求

| 项 | 要求 | 备注 |
|---|---|---|
| 硬件 | ≥16G 内存（本机 15G 实测可用，前端 build 与全栈互斥，见坑 #22） | 内存不足时 `docker compose` 可能 OOM |
| 软件 | Docker + docker compose；`uv`（Python 依赖）；Node ≥ 20（前端） | 后端 Python 3.11 |
| 系统 | Linux；ES 要求 `vm.max_map_count ≥ 262144` | 本机实测 1048576 免调（坑 #1）；如 ES 起不来先 `sysctl -w vm.max_map_count=262144` |
| 网络 | 能访问模型网关（sensenova / tokenrouter 等，凭据在 `deploy/env/.env`） | 国内环境部署镜像用 `--image-source mainland`（坑 #2） |

---

## 1. Clone 与准备

```bash
git clone <本 fork 地址> nexent && cd nexent
# 默认走 main（稳定演示）或 develop（集成）；任务开发在 feat/kw-TNN-<slug> 分支
git checkout main
```

---

## 2. 部署平台（Docker）

```bash
# 自动化/非交互环境：显式传镜像源（国内必须 mainland，坑 #2：交互选单在管道环境不可达、默认走 general 慢）
bash deploy.sh --defaults --image-source mainland docker

# 交互式部署（可选）：bash deploy.sh docker
```

要点（来自上游 README + 实测）：
- 组件：`infrastructure` 必需；`application`、`data-process`、`supabase` 默认勾选。
- 端口策略：开发用 `--port-policy development`（本机实测 PostgreSQL 映射 `5434`，非默认 5432；具体以 `deploy/env/.env` 为准）。
- 配置落 `deploy/env/.env`；成功部署会存 `deploy.options` 供下次复用。
- 本机栈状态：`nexent-postgresql` 容器常驻（端口 5434）；web/config/runtime/mcp 等容器按需启动。

### 关键环境变量（`deploy/env/.env`，脱敏）

| 变量 | 用途 |
|---|---|
| `NEXENT_POSTGRES_PASSWORD` | PostgreSQL 密码（评测/摄取脚本经它连库） |
| 模型网关 key | sensenova / tokenrouter / siliconflow 等（模型注册用，见 §4） |

---

## 3. 建租户 + 管理员（多租户初始化，坑 #5）

Nexent 是多租户架构：suadmin 是平台超管（管租户/配额），**模型配置是租户级页面**（ADMIN/SPEED/DEV/USER 角色才有 `/models`）。

```bash
# 1) suadmin 登录（http://localhost:3000，默认账号见部署输出）
# 2) 建租户 knowevo → 生成 ADMIN_INVITE 邀请码
# 3) 注册 admin@knowevo.com（ADMIN 角色）
# 4) 用管理员登录 → 配模型（§4）
```

初始化顺序铁律：**建租户 → 建管理员 → 再配业务资源**（坑 #5：SU 直接配 /models 会 404/无权限）。

---

## 4. 注册模型（评测与智能体依赖）

在租户管理员登录态下打开「模型配置」，注册以下档位（`model_record_t` 真实记录，2026-09-20 导出）：

| 用途 | 模型 | 工厂 | 备注 |
|---|---|---|---|
| 生成·小/中档 | deepseek-v4-flash | OpenAI-API-Compatible | `KW_LLM_SMALL_MODEL_ID=7`、`KW_LLM_MID_MODEL_ID=7` |
| judge·异家族 | glm-5.2 | OpenAI-API-Compatible | `KW_LLM_LARGE_MODEL_ID=8`（sensenova 网关档位） |
| embedding | bge-m3 | silicon | 知识库向量化 |

> 全部 9 档真实配置见 `competition/docs/agent-config.md` §2（key 一律不入库不入文）。
> 注册后平台界面可见双档/多档模型（对应截图 `T-01-models-registered.png`）。

---

## 5. 智能体与知识库（KnowEvo 侧配置）

- **Agent**：平台「智能体」页创建 `knowevo_assistant`（duty prompt：职责+诚实约束，见 `agent-config.md` §1）。
- **Skill 分层**：4 个真实 Skill（`domain-asset-cognition` 入口路由 + `retrieval-path`/`reasoning-path`/`evidence-assembly`），落 `ag_skill_info_t`，内容见 `agent-config.md` §5。
- **MCP 工具**：自研 knowevo 工具族（`kg_search/kg_stats/kg_multi_hop/decision_card_render/skill_template_apply`）Local MCP + FastMCP 双注册（`backend/tool_collection/mcp/kg_tools.py`）。
- **知识库**：58 份语料登记（`competition/corpus/registry.csv`），经 Nexent 摄取管线（Unstructured）入库。

---

## 6. 复现评测数字（T-22 消融）

依赖：PostgreSQL 容器运行（5434）+ 构建租户图谱已摄取（见 §7）+ 模型网关配额充足（sensenova tpm 小配额，见坑/候选协议）。

```bash
cd backend
export POSTGRES_HOST=localhost POSTGRES_PORT=5434 POSTGRES_USER=root POSTGRES_DB=nexent
export NEXENT_POSTGRES_PASSWORD=<pw>
export KW_LLM_SMALL_MODEL_ID=7 KW_LLM_MID_MODEL_ID=7 KW_LLM_LARGE_MODEL_ID=8

# 四级 headline（每级一条 eval_run_t；断点续跑：重跑同命令自动跳过已完成 pair）
uv run python -m services.knowevo.pipeline.ablation --levels A1,A2,A3,A4 --runs 3 --top-k 5 --pace 8

# E8：版本钉住 on/off × V,F（判分按臂选金标）
uv run python -m services.knowevo.pipeline.ablation --levels A4 --pin on,off --types V,F --runs 3

# 单测（不依赖真 LLM）
uv run pytest ../test/backend/services/knowevo/test_ablation.py -q --no-header
# PG 集成全量（真实库，零回归基线 551 passed）
uv run pytest ../test/backend/services/knowevo/ -q --no-header
```

结果落 `competition/deliverables/e2-ablation-report.json`（partial 状态如实标注）；叙事见 `competition/docs/e2-ablation.md`。

### 已知坑（复现时先读）

- **LLM 契约**：平台 llm 调用要求返回解析后的 dict；`str` 会在 `_parse_extraction` 被拆字符（坑：摄取驱动加了 str→JSON 适配层，仓库零改动）。
- **429 风暴**：sensenova 网关 tpm 小配额，连续打 1-2 span 即撞 429，退避 125-850s 必撞适配器 120s 超时——摄取/评测必须走**突发协议**（探针 + 8 分钟窗 + 风暴熔断 + span_hash 断点续跑）。
- **评测判定**：Code 判定签名逐字 `evaluate(query, expected, actual, runtime_events)`（平台官方文档核验）；未答计错（E1 分母污染教训）。

---

## 7. 构建租户图谱摄取（可选，解锁 E8/A3 真数字）

```bash
# 0) 探网关（双档健康才开突发）
cd nexent/backend && .venv/bin/python -u .task_b_status/ingest-r7/probe_gateway.py   # 注：驱动脚本为工作区 .task_b_status/ 下的循环运行产物，比赛仓库不含

# 1) 突发窗（8 分钟 + 20s 强杀宽限，SIGINT 优雅退出保证 usage 落盘）
cd nexent/backend && setsid nohup env POSTGRES_HOST=… NEXENT_POSTGRES_PASSWORD=… \
  timeout --signal=INT --kill-after=20 480 .venv/bin/python -u <驱动> \
  --batch <batch.json> --tenant <构建租户 uuid> --checkpoint 10 >> seg-r8.log 2>&1 &

# 2) 验收（判别性 SQL）
docker exec -i nexent-postgresql psql -U root -d nexent -t -A < validate_ingest.sql
```

> 说明：摄取驱动脚本与批次文件是研发循环的运行产物（`.task_b_status/`，不进入比赛仓库）；若评审方需要复现摄取，可在仓库内重建等价驱动（T-08 接线后 `ingest_graph` 原生支持真实 LLM 注入）。

---

## 8. 验收自查清单

- [ ] `bash deploy.sh --defaults --image-source mainland docker` 一键起栈
- [ ] 租户/管理员/模型注册走通（§3-4）
- [ ] 智能体 + 4 Skill + 5 自研 MCP 工具双注册可见（§5）
- [ ] `uv run pytest ../test/backend/services/knowevo/` 全量通过（PG 集成 551）
- [ ] 消融命令可复跑、断点续跑生效（§6）
- [ ] 示例问答可回溯 `decision_card_t`/`eval_run_t` 行（真实问答硬要求）

---

*本文档所有数字/命令均来自真实运行记录；`.task_b_status/` 内看门狗与运行脚本属研发循环产物，不在比赛仓库中（用户红线：不污染比赛项目）。*
