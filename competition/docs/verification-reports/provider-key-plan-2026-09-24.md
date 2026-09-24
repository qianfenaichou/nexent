# Provider Key 配置与环境就绪计划 · 2026-09-24

> 会话：任务Q3（环境就绪规划）。工作区 `/home/qianqian/Work/All/Nexent`。
> 背景：2026-09-23 子智能体 provider（sensenova2）报 `Invalid API Key`（见 `archive/交接记录-2026-09/HANDOFF-待推送与provider-2026-09-23.md`），卡住 visual-judge 视觉验收与 E2 全量 LLM 侧。
> 铁律：① 不读/不复制/不输出任何真实密钥值，本文件只写"从哪拿 → 填到哪 → 重启什么 → 用哪条命令验证"；② 不自行 commit；③ 不伪造数字；④ 涉及官网/赛事规则一律逐字抄录 + 记 URL 与访问日期。

---

## §零 只有用户能做的步骤（你是整条链的源头）

> `rubric-scorecard.md` 要求：只有用户能做的几步要置顶单列。下面是**必须用户本人**完成的动作（AI 会话碰不到密钥，也不该碰）。

| # | 动作 | 去哪拿 / 填到哪 | 预计耗时 |
|---|---|---|---|
| U1 | 从商汤 sensenova（或所选 LLM 供应商）控制台取得新 API Key | 供应商官网控制台 | 5–15 分钟 |
| U2 | 在 **Nexent 平台**（租户管理员登录）→ `/models` 注册 LLM 模型，填 provider=对应厂商、api_key、base_url、模型名 | 平台 UI → 写库 `model_record_t` | 10–20 分钟 |
| U3 | 在平台 UI 设置 `KW_LLM_SMALL/MID/LARGE_MODEL_ID` 指向 U2 注册的模型（或把值写入部署 `.env` 后重启） | 平台配置 / `backend/consts/const.py` 读取的环境变量 | 5–10 分钟 + 重启 |
| U4 | 在**智能体编排工具（ZCode）设置**里为子智能体指定 sensenova2/deepseek-flash 并填有效 key（**这是 09-23 真正卡住的那条轨**） | ZCode / 编排工具设置页（不在本代码仓） | 5–10 分钟 |
| U5 | 跑下方最小验证命令确认生效 | 见各步"验证命令" | 1–2 分钟/次 |

> 注：U2/U3 是"平台侧模型注册轨"（影响 in-platform 智能体 LLM 调用）；U4 是"子智能体 provider 轨"（影响 visual-judge / E2 LLM 侧）。两条轨独立，缺任何一条都会在不同环节报密钥错。

---

## 一、Provider 配置链路调研（Q3-1，只读）

### 1.1 两条轨的区分（关键）

| 轨 | 配置落点 | 影响范围 | 本仓是否可见 |
|---|---|---|---|
| **轨A：平台侧模型注册** | Nexent 平台 UI → `model_record_t`（DB 表） | 平台内智能体 / 摄取 / 本项目的 LLM 调用 | 代码可读 `model_record_t`，但 key 值存在 DB，不在仓内 |
| **轨B：子智能体 provider** | 智能体编排工具（ZCode）设置 | visual-judge 视觉验收、E2 全量 LLM 侧、build_ontology_round | **完全在仓外**，本会话无法触及 |

`HANDOFF-待推送与provider-2026-09-23.md` 记录的 `Invalid API Key`（717ms 失败）来自**轨B**——"你把子智能体供应商换成 sensenova2/deepseek-flash 后，Agent 派生即时报 Invalid API Key……需在 ZCode 设置里给 sensenova2 配有效 key"。

### 1.2 关键代码锚点（只读确认，不修改）

- `backend/database/db_models.py:435` — `ModelRecord.connect_status` 列（NOT_DETECTED / DETECTING / AVAILABLE / UNAVAILABLE）。
- `backend/database/model_management_db.py` — `create_model_record` / `get_model_records` / `get_model_by_model_id`（`model_record_t` CRUD）。
- `backend/agents/create_agent_info.py:62,892,898,928` — 从 `get_model_records({"model_type":"llm"}, tenant_id)` 取模型，且 `api_key=main_model_config.get("api_key","")` 直接读 DB 行里的 key（**证明轨A 的 key 存于 DB 而非 env**）。
- `backend/consts/const.py:807-809` — `KW_LLM_SMALL/MID/LARGE_MODEL_ID = os.getenv(...)`（**env 只选"用哪个已注册模型"，不存 key**）。
- `backend/services/knowevo/llm_client.py:97` — `_tier_model_id(tier)` 活读上述 env 常量（`globals()[...]`），故改 env 须重启进程才生效。
- `backend/services/config_sync_service.py:122-129` — 仅 `embedding` 类型的 `api_key` 会被同步进 env（`EMBEDDING_API_KEY`）；LLM 的 key 走 DB。
- `backend/services/model_health_service.py:292-339` — 连接检测：`connect_status` 置 DETECTING → 探测后 AVAILABLE / UNAVAILABLE（**验证用**）。
- `backend/apps/datamate_app.py:71` — `POST /test_connection` 端点（DataMate 连接测试，参考验证模式）。

### 1.3 一个 key 从用户拿到到生效经过哪几层

```
[U1] 用户从供应商控制台取得 api_key（真实密钥，本会话不碰）
        │
        ├─ 轨A（平台侧，影响 in-platform LLM）
        │    └─ [U2] 平台 /models 注册 LLM（provider + api_key + base_url + 模型名）
        │            → 写入 model_record_t（connect_status=detecting）
        │    └─ [平台UI] 触发连接检测（model_health_service）→ connect_status=available
        │    └─ [U3] 设 KW_LLM_*_MODEL_ID 指向该 model_id（平台配置或 .env）
        │            → 重启 backend 相关服务使 env 生效
        │
        └─ 轨B（子智能体 provider，影响 visual-judge / E2）
             └─ [U4] 编排工具（ZCode）设置里选 sensenova2/deepseek-flash + 填有效 key
                     （仓外，纯外部动作）
```

---

## 二、配置计划（Q3-2）

> 每步 = 动作 + 验证命令 + 429/配额行为。最小成本验证。

### Step A1 — 拿 key（用户，U1）
- 动作：登录所选 LLM 供应商控制台，创建/复制 API Key。
- 验证：无（用户侧）；**密钥值不得进入任何文件或聊天**。

### Step A2 — 平台注册 LLM 模型（用户，U2）
- 动作：租户管理员登录 Nexent 平台 → `/models` → 新增 LLM → 填 provider、api_key（U1 取得）、base_url、模型名 → 保存。
- 验证（平台 UI）：保存后平台触发连接检测，观察 `connect_status` 变为 `available`（代码侧见 `model_health_service.py`）。
- 命令行辅助验证（若平台暴露）：`curl -X POST <平台>/api/.../test_connection`（参考 `datamate_app.py:71` 形态）。

### Step A3 — 指向已注册模型（用户，U3）
- 动作：在平台把 SMART/MID/LARGE 三档模型 ID 设为 A2 注册的模型；或写入部署 `.env` 的 `KW_LLM_SMALL/MID/LARGE_MODEL_ID` 后重启。
- 验证：重启后最小 LLM 调用——
  ```bash
  cd nexent/backend && .venv/bin/python -c "from services.knowevo.llm_client import LlmRouter; \
    print(LlmRouter().complete('say hi', tier='small'))"
  ```
  （实际符号以 `llm_client.py` 当前导出为准；本条为最小成本验证范式，非必抄命令。）

### Step B1 — 子智能体 provider（用户，U4）【这是真卡点】
- 动作：在编排工具（ZCode）设置 → 子智能体供应商选 `sensenova2`（或 `deepseek-flash`）→ 填入有效 key → 保存。
- 验证：派一个最小子智能体任务（如对任意文档派 visual-judge），**不再报 `Invalid API Key`（717ms 失败）**即生效。

### 429 / 配额行为记录（历史观测，来自台账）
- 摄取批跑遇 429：`HANDOFF` 记载 batch-C 摄取"429 退避中属正常"；对策 = 降速 / 排队重试，如实记录，不伪造落库数。
- 免费档并发限流（pitfall #20）：E0 小档首轮 17/20 题 429，空响应率 ~40%。对策 = 指数退避（15s×attempt，上限 90s）+ 题间 sleep 3s + 空响应标记 EMPTY。
- 推论：轨B 恢复后，**visual-judge 与 Q2B 摄取会抢同一份 sensenova2 配额，429 互相放大**（见 `NEXT-SESSION-PROMPTS` 并行总则）。编排上 A4 视觉验收第一波禁用，推到服务栈收口后。

### 双轨生效对照表
| 任务 | 依赖轨 | 不配会怎样 |
|---|---|---|
| in-platform 智能体问答 / 摄取 LLM 侧 | 轨A | 平台模型 `connect_status=unavailable`，LLM 调用失败 |
| visual-judge 视觉验收（Q2A-A4） | 轨B | `Invalid API Key`（真发生的故障） |
| E2 全量消融 LLM 侧 / build_ontology_round 截图（Q2B） | 轨B | 子智能体派生即失败 |

---

## 三、两月冲刺 · 环境就绪总清单（Q3-3）

> 面向报名截止 2026-11-30 之后的初赛窗口（材料提交约 2026-12，待用户确认）。每项列"谁去做 / 卡谁 / 验证命令"。

| 依赖项 | 就绪度 | 谁去做 | 卡谁 | 验证命令 |
|---|---|---|---|---|
| **provider key（轨A 平台侧）** | 待用户 | 用户（U2/U3） | 用户拿 key + 平台注册 | 平台 `model_record_t.connect_status=available`；A3 最小 LLM 调用返回非空 |
| **subagent provider（轨B）** | ❌ 当前失效 | 用户（U4，ZCode 设置） | 用户填有效 key | 派子智能体不再 `Invalid API Key` |
| **PG** | 脚本就绪 | 任务Q2B 独家起 | 用户未起栈则 Q1 Phase1+ 复用不了 | `source /home/qianqian/pg-env.sh && pg_start`；`psql -h 127.0.0.1 -p 5434 -d nexent -c "\dt nexent.*"` |
| **ES** | compose 就绪 | 任务Q2B | dockerd 无 socket 时降级（如实记录，不装新组件） | `curl -s localhost:9200/_cluster/health` 返回 `green`/`yellow` |
| **minio** | ✅ 已实证可用 | 已就绪（T-27） | 无 | `curl -s http://127.0.0.1:9010/minio/health/live` → `healthy` |
| **服务栈总入口** | 由 Q2B 持有 | 任务Q2B | 禁止第二会话再起一套 | 见 `00-索引与状态.md` §八 Q2B 登记 |

> 纪律（来自 `NEXT-SESSION-PROMPTS` 并行总则）：PG/ES/minio **只有 Q2B 能起**；Q1 Phase 1+ 复用其栈，禁止第二套。本会话（Q3）**不起任何服务**，只出计划。

---

## 四、收尾与登记
- 本计划文档：`nexent/competition/docs/verification-reports/provider-key-plan-2026-09-24.md`
- 外部核验文档：`nexent/competition/docs/verification-reports/official-site-2026-09-24-external-crosscheck.md`
- §八「任务Q3」小节已登记本计划路径与结论（不改其他任务小节、不改 §一–§七）。
- 踩坑台账：在 `pitfalls.md` 号段 `#105–109` 追加 2 条校准（报名截止误读、全球总决赛口径冲突）。
- **不自行 commit**，由单点统一收口提交。
