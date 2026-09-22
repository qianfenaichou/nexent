# T-27：chat QA 链路解锁（sandbox/minio docker DNS blocker）

**状态**: ★ 待开发（2026-09-22 r26 轮；来源 = evidence-index L70 kw-qa r25 槽位如实上报「chat QA 3 轮均 Agent execution failed，根因 = `nexent-minio:9000` docker DNS 名宿主机不可解析，待 team-lead 解锁」）
**Blocked by**: 无
**独占文件**（本任务创建/修改）:
- `competition/docs/verification-reports/t27-chatqa-unlock.md`（新建：诊断报告，file:line/配置键 + 每步命令与输出）
- `.task_b_status/t27-chatqa/**`（运行工件/日志，不进 git）
- `competition/deliverables/T-27-chat-qa.png`（解锁成功后 1 张真实对话截图）
- 如需仓内配置修复：仅限 knowevo 自包含文件或本地 `.env` 类未跟踪配置——**先诊断，定位后再动**

**待接线项**: 无
**禁改清单**: 上游 `deploy/docker-compose*.yml`、`backend/apps/*`、`backend/consts/const.py`、其他任务的独占文件、`frontend/public/locales/**`（主会话有未提交改动）。仓外操作（/etc/hosts、docker network connect 等）**不自行执行，STOP 上报主会话**（报出确切命令与理由）。
**允许的新依赖**: **无**

## 现象（先各自复现再动手）
- chat 页 3 轮提问均「Agent execution failed」（kw-qa r25 实测，evidence-index L70）。
- 后端日志中应有 sandbox/minio 相关连接错误（`nexent-minio:9000` 解析失败）。当前后端**未运行**——先按 T-26 配方拉起（参考 `.task_b_status/kw-cardfix/backend-5010-r24.log` 环境快照；如该目录为空则从 deploy 脚本/docker ps 现场重建环境快照并记入报告）。
- 本机约束：后端进程跑在**宿主机**，minio 等中间件跑在 **docker**——宿主机解析不了 docker 内部 DNS 名。这是结构性矛盾，修复要么让名字可解析、要么让 endpoint 配置指向宿主机可达地址（端口映射 127.0.0.1:9000）。

## 任务（诊断优先）
1. **诊断**：`docker ps` 查 minio 容器/端口映射；grep chat 链路中 minio endpoint 的来源（env 变量/DB 配置/代码默认值），给出精确出处（file:line 或配置键名）；用 `getent hosts nexent-minio`、`curl` 复现解析失败。
2. **修复**（按侵入度升序，选定后在报告里写明为何选它）：
   - ① 纯环境级：如 endpoint 走 env/DB 配置 → 改为 `127.0.0.1:<映射端口>`（零仓内文件改动优先）；
   - ② 需要仓内改动 → 只动 knowevo 自包含文件或未跟踪本地配置；
   - ③ 必须改上游共享文件/宿主机系统 → **STOP 上报主会话裁决**，附最小 diff 或命令。
3. **复验**（真实链路）：chat 页 3 轮真实问答，≥1 轮有 kg 工具调用痕迹（日志或回答内容佐证）；1 张截图（登录后对话界面）。

## 验收标准
- [ ] 根因定位写进报告（配置键/file:line + 复现命令）
- [ ] 修复后 chat QA ≥3 轮真实完成（或如实记录新 blocker + STOP，不伪造）
- [ ] 截图 1 张入 deliverables 并在报告登记
- [ ] LLM 预算 ≤10 次调用，逐次用途记入报告（台账行报主会话登记，不直接改 cost-ledger）
- [ ] 独占文件边界外零改动；禁改清单零触碰；不 push 不合并

**Evidence**: （完成后填写）
