# T-27 诊断报告：chat QA 链路解锁（sandbox / minio blocker）

**任务简报**：[`../../tasks/T-27-brief.md`](../../tasks/T-27-brief.md)
**结论日期**：2026-09-23
**状态**：✅ **已解锁**——chat 页 3 轮真实问答全部完成（`errors: []`，无一轮「Agent execution failed」），且平台智能体在问答过程中**真实调用了自研 KG MCP 工具**（MCP 服务端日志 **71 条 `CallToolRequest`**）。产出 3 张交付截图。

> 本报告只写有产物可回溯的事实。每个数字标注出处；无法测量一律写「未测量」。运行工件在仓外 `~/t27-chatqa/`（**未入 git**，遵循「仓内不放运行残渣」纪律；`.task_b_status/` 已废弃，未新建）。

---

## 一、根因：两个独立缺陷，各自都真实阻塞过链路

### 缺陷 1（主阻塞）：sandbox 级别 = `docker`，走 docker 内部 DNS 名

**现象**（修复前实测，`~/t27-chatqa/round-api-1.out` 尾段）：

```
Run Agent Error: Error in interaction: Failed to grant sandbox output access:
chgrp: cannot access '/home/qianqian/nexent-data/workdir/406c1278-.../9bc8fca14d6649b6a883e18955156e6b':
No such file or directory
```

**根因**：后端跑在**宿主机**，而 sandbox 级别配置是 `NEXENT_SANDBOX_DEFAULT_LEVEL=docker`，sandbox 的产物回传链路会去连 docker 网络内部的 DNS 名 `nexent-minio:9000`；宿主机解析不了该名字 → `EndpointConnectionError` → 沙箱产物授权失败 → 面上表现成 `MemoryPreparationException` → 前端只看到「Agent execution failed」。

**修复**（按简报的「侵入度升序」，选了最低的 ① 纯环境级，**零仓内文件改动**）：

| 配置键 | 修复前 | 修复后 | 依据 |
|---|---|---|---|
| `NEXENT_SANDBOX_DEFAULT_LEVEL` | `docker` | `local` | `~/t27-chatqa/host.env`（导出集快照，2026-09-23 00:19） |
| `MINIO_ENDPOINT` | docker 内部名 | `http://127.0.0.1:9010` | 同上；容器 `nexent-minio` 的映射为 `0.0.0.0:9010->9000/tcp`（`docker ps` 实测） |

后端以该环境重启（`~/t27-chatqa/config-5010.log`：`23:42:34 Started server process [117047]` → `Application startup complete`，监听 `0.0.0.0:5010`，无报错）。

### 缺陷 2（叠加）：MCP tenant 在共享 MCP 服务里解析不到

**现象**：`~/t27-chatqa/mcp-5018.log` 中反复出现（共 61 行含 `tenant` 关键字）：

```
WARNING mcp_servers.knowevo_mcp.server] request-scoped tenant unavailable:
no Authorization header (headers present: ['accept', 'accept-encoding', 'connection',
'content-length', 'content-type', 'host', 'user-agent'])
```

**根因**：`knowevo_mcp` 被平台**进程级挂载一次**、服务所有租户，因此调用方的 JWT 是唯一的 per-call 租户来源。但实测表明：**Nexent v2.5.1 内置的本地 MCP 服务根本不发 `Authorization` 头**（上列 7 个头里没有它）——所以 request-scoped 路径在本部署形态下**不会触发**，tenant 解析落在服务级设置上。

**修复**（限定 knowevo 自包含文件，无上游共享文件改动）：

- `mcp_servers/knowevo_mcp/server.py`：新增 `_request_tenant()`（从 `Authorization` 头 → `utils.auth_utils.get_current_user_id` 取 tenant，异常一律降级为 `''` 而非让工具失败；日志只记**头名**不记 token 值）与 `_tenant()`（优先级：显式参数 > 请求头 > 服务级默认），`_service` / `_resolve` / `_card_service` 统一改走它。
- `backend/tool_collection/mcp/kg_tools.py`：`wire()` 支持 `KW_MCP_TENANT_ID` 环境变量作为**服务级锚定**——这正是本部署形态下实际生效的机制。
- `frontend/public/locales/{zh,en}/custom.json`：**未触碰**（简报禁改清单项；该文件在主会话另有未提交改动）。

> **诚实标注（重要）**：request-scoped 租户解析在本部署形态下**没有被触发**（平台不发头），它是为独立 FastMCP 部署形态预留的前向兼容路径。**实际让链路通的是 `KW_MCP_TENANT_ID` 服务级锚定 + 缺陷 1 的环境修复。** 不得把二者混为一谈宣称「请求级多租户已工作」。

---

## 二、复验（真实链路，非构造）

**3 轮真实问答**，全部走完整 `/api/agent/run`（`~/t27-chatqa/chat-qa-summary.json`：`"errors": []`，每轮 `agent_posts` 中 `/api/agent/run` 与 `/api/agent/automations/proposals` 均 `status: 200`）：

| 轮 | 问题 | 截图 |
|---|---|---|
| 1 | 糖尿病前期包括哪些情况？请依据知识图谱回答。 | `deliverables/T-27-chat-qa-1.png` |
| 2 | 随机血糖可以用来诊断糖尿病吗？请给出图谱证据。 | `deliverables/T-27-chat-qa-2.png` |
| 3 | 空腹血糖多少可以诊断糖尿病？请依据知识图谱给出证据。 | `deliverables/T-27-chat-qa-3.png` |

**智能体确实调用了 KG 工具（这份证据此前一直缺）**：

- **协议级**：MCP 服务端日志 `mcp-5018.log` 含 **71 条 `Processing request of type CallToolRequest`**（跨 10 个 session），全部 `202 Accepted`。
- **推理轨迹级**：summary JSON 的 `answer_tail` 可见智能体的思考与代码——`local_knowevo_kg_search(inputs={"query": "糖尿病前期", ...})`、`local_knowevo_kg_multi_hop(inputs={"question": ..., "seeds": ["Disease:糖尿病前期", ...], "depth": 2, "beam": 3, ...})`、`local_knowevo_decision_card_render(inputs={"question": ..., "mode": "full"})`。
- **答案本体级**（截图可直接读出）：轮 1 给出 `糖尿病前期 → includes → 糖耐量异常(IGT) / 空腹血糖受损(IFG)` 关系表并标注 `version_valid: true`、共 3 条 `includes` 证据路径；轮 2 标注 `valid_view = 2026-09-22T16:58:23.964908Z` 与决策卡 `decision = INSUFFICIENT_EVIDENCE`（带 `uncertainty_notes` UUID）；轮 3 给出 `空腹血糖 ≥ 7.0 mmol/L`、头实体 `Examination:空腹血糖 (Examination 类)`，并主动披露**工具侧不一致**（`decision_card_render` 与 `kg_multi_hop` 的返回冲突，以图谱原始证据为准）。

**截图真实性**：3 张均为 1440×980 PNG，md5 互不相同（`8e1bc576…` / `8c015b14…` / `1a5b18cb…`），内容为 Nexent 真实界面（登录态、左侧会话列表、任务详情折叠块、底部「Knowevo 助手 + deepseek-flash」选择器、页脚 `Nexent © 2026 · v2.5.1`），非拼图。

### 可回溯性核验（真库直查，P0-2 验收项）

`nexent.decision_card_t` 直查（窗口 `created_at >= 2026-09-22 16:45:00+00`，UTC；本地时区为 UTC+8，故对应本地 2026-09-23 00:45 之后）：

| decision_card_t.id | decision | created_at (UTC) | 问题 |
|---|---|---|---|
| `e2e0a66f-f531-405c-aa8c-56e31a71ad3f` | `INSUFFICIENT_EVIDENCE` | 2026-09-22 16:55:41.653844 | 糖尿病前期包括哪些情况？ ← **截图 1** |
| `348c8a4d-9d50-43ac-a205-d837546b9ec0` | `INSUFFICIENT_EVIDENCE` | 2026-09-22 16:58:09.309581 | 随机血糖可以用来诊断糖尿病吗？ ← **截图 2** |
| `962c9cf2-3949-435e-8dc3-5b6259cf837c` | `INSUFFICIENT_EVIDENCE` | 2026-09-22 17:01:38.244163 | 空腹血糖多少可以诊断糖尿病？ ← **截图 3** |

（同窗口另有 3 行同一会话内的其他提问：`6cdd3ef4` / `da2578c5`「糖尿病前期患者应该如何干预？」、`496784c3`「空腹血糖诊断糖尿病的血糖阈值是多少」。表内共 205 行。）

**截图 ↔ 真库互锁证据**：截图 2 的任务详情里显示决策卡 `uncertainty_notes` 内的 UUID 为 `…43ac-a205-d837546b9ec0`，与库行 `348c8a4d-9d50-43ac-a205-d837546b9ec0` **后缀逐字符吻合**——这不是「时间相近」的推断，而是同一标识符的两处出现。三张截图对应的问题与库行问题逐条对应。

> 复核命令（凭据从仓外 `~/t27-chatqa/host.env` 取，不写入仓内）：
> `docker exec -e PGPASSWORD=<...> nexent-postgresql psql -U root -d nexent -c "SELECT id, payload->>'decision', created_at FROM nexent.decision_card_t WHERE created_at >= '2026-09-22 16:45:00+00' ORDER BY created_at;"`

**测试与静态检查**（本次实测）：

- `backend/.venv/bin/python -m pytest ../test/backend/services/knowevo/ -q` → **718 passed, 30 skipped**（较 T-28 后的 711 增加 7 个，来自新增的 `TestRequestScopedTenant` 用例）。
- ruff 对 T-27 改动的三个文件（`kg_tools.py` / `server.py` / `test_knowevo_mcp.py`）→ **All checks passed**，零新增。
- ⚠️ 但按 AGENTS.md §3 的原样命令 `ruff check backend/services/knowevo mcp_servers` 在 HEAD 上仍有 **5 个 `I001`（import 未排序）**，全部落在**本任务未触碰**的文件里，属历史遗留，非本次引入。

---

## 三、LLM 用量（诚实口径）

**未测量**。3 轮问答的 token 用量未落盘：`turn{1,2,3}.json` 与 `chat-qa-summary.json` 都只记录问题/截图/HTTP 状态/答案尾部，不含用量字段；本轮**未从库侧补测**，故不作任何估算。

---

## 四、给交付材料的两条口径提醒

1. **平台版本号**：截图页脚是 `v2.5.1`（这是本 fork 的**部署版本**，与 `VERSION` 文件一致、sandbox 镜像也标 `v2.5.1`），而 `platform-facts-redline.md` §3 要求**对外叙述一律写 v2.6.0**（官方 2026-09-16 发版）。截图进交付件后，评委可能直接看到页脚版本与正文表述不一致 → 建议在交付文档中加一句**fork 时点限定**（例：「本项目基于 Nexent fork 开发（fork 时点对应上游 v2.5.1），上游最新发版为 v2.6.0」）。**本报告不擅自改对外母本，提出待主会话裁决。**
2. **本任务的三张截图同时补齐了「示例问答截图 ≥3」这一 P0 交付缺口**，登记见 `deliverables/evidence-index.md`。

---

## 五、未完成 / 遗留

| 项 | 状态 | 说明 |
|---|---|---|
| LLM 用量回溯 | 未测量 | 见 §三；如需入台账需从库侧补测，不得估算 |
| 三份知识库/MCP 文件说明独立成文（P0-3） | 另立任务 | 与本任务无关的独立缺口 |
| `frontend/public/locales/{zh,en}/custom.json` 的 `FILE_UPLOAD_SIZE_LIMIT` 100→10 | **未提交、来源未记录** | 本次未触碰、未提交。注意：`competition/corpus/guidelines/dm_guideline_2024.pdf` 为 **16.3 MB**，而该键经 `frontend/const/chatConfig.ts:36` 控制聊天输入框的客户端上传上限 → 若保留 10，**聊天框将无法上传 2024 指南**（知识库摄取走独立接口，不受影响）。需人工确认该改动是有意为之还是调试残留 |

---

## 六、Evidence 锚点

| 产物 | 路径 |
|---|---|
| 3 张交付截图 | `competition/deliverables/T-27-chat-qa-{1,2,3}.png` |
| 问答汇总 + 推理轨迹 | `~/t27-chatqa/chat-qa-summary.json`、`turn{1,2,3}.json` |
| 修复前失败证据 | `~/t27-chatqa/round-api-1.out` |
| MCP 协议级调用证据（71×CallToolRequest） | `~/t27-chatqa/mcp-5018.log` |
| 运行环境快照 | `~/t27-chatqa/host.env` |
| 后端重启日志 | `~/t27-chatqa/config-5010.log` |
| 代码改动 | `mcp_servers/knowevo_mcp/server.py`、`backend/tool_collection/mcp/kg_tools.py`、`test/backend/services/knowevo/test_knowevo_mcp.py` |

**报告填写**：2026-09-23（主会话，T-27 收口轮）。所有数字均由本会话现场复跑/直读产出，无转述。
