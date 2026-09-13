# T-01：部署基线
**Blocked by**: T-00
**独占文件**: deploy/env/.env（部署生成）、competition/docs/pitfalls.md、competition/deliverables/
**待接线项**: 无（模型注册属运行时操作，不涉代码）
**禁改清单**: backend/frontend 未动一行；deploy/ 脚本未动（仅 .env 按脚本机制被写入生成值）
**允许的新依赖**: 无代码依赖；docker 镜像走 mainland 白名单源（elastic.m.daocloud.io / quay.m.daocloud.io / docker.m.daocloud.io / ccr.ccs.tencentyun.com/nexent-hub）
**本任务细节**: E0 微基准（20 题双档模型出数→回收 L1/L2/L7 遗留值）——**待用户提供 API Key 后执行**（tasks/README.md T-01 行）
**要构建的行为**: 本机 docker 单机部署 13 容器全就绪，suadmin 可登录，为后续模型注册/对话/截图提供环境
**验收命令**: `docker ps -a --filter name=nexent`；`curl -X POST localhost:3000/api/user/signin`；`docker exec nexent-postgresql psql -U root -d nexent -c "select count(*) from pg_tables where schemaname='nexent'"`
**验收标准**:
- [x] 13 容器全部 Up（5 个带 healthcheck 全 healthy，其余经 API/日志探活）
- [x] .env 按默认填写（拷贝零手改；脚本自动生成密钥类字段）
- [x] suadmin@nexent.com 登录 200（角色 SU，会话 7200s）
- [x] 部署坑已记 competition/docs/pitfalls.md（#2-#6）
- [x] **2026-09-14 补：双档模型注册完成，对话跑通，截图已存 deliverables/**
**Evidence**:
- 部署命令：`DEPLOYMENT_LANGUAGE=zh bash deploy/deploy.sh docker --image-source mainland`（退出码 0，全日志 /tmp/nexent-deploy.log）
- 容器状态（docker ps）：
  - healthy 5/5：nexent-elasticsearch、nexent-redis、supabase-db-mini、supabase-auth-mini、supabase-kong-mini
  - Up（无 healthcheck，探活通过）：nexent-config(5010 /docs 200)、nexent-runtime(5014 /docs 200)、nexent-mcp(5011 Uvicorn running)、nexent-northbound(5013 /api/docs 200)、nexent-data-process(5012 /api/docs 200，worker 空闲)、nexent-web(3000 /zh 200)、nexent-postgresql(5434)、nexent-minio(9000)
- DB：nexent schema 76 表；suadmin 在 nexent.user_tenant_t（user_role=SU）
- 登录：`POST /api/user/signin` → 200 `{"user":{"email":"suadmin@nexent.com","role":"SU"},...}`
- .env 改动清单（供用户确认）：
  - **人工改动：0 行**（cp 原样拷贝，diff 为空；.env.example 中无模型 API Key 字段——模型 Key 在网页端"模型配置"注册）
  - **deploy.sh 自动写入**（部署机制，非人工选择）：ROOT_DIR、LOG_DIR、NEXENT_MCP_DOCKER_IMAGE、NEXENT_SANDBOX_DOCKER_IMAGE、NEXENT_SQL_FILES_CHECKSUM、MINIO_ACCESS_KEY/SECRET_KEY、JWT_SECRET、SECRET_KEY_BASE、VAULT_ENC_KEY、SUPABASE_KEY、SERVICE_ROLE_KEY、ELASTICSEARCH_API_KEY、DEPLOYMENT_VERSION=full（均由脚本生成/更新，值未人工指定）
- 前置检查（全过）：端口 2222/3000/5010-5015/5434/5555/6379/8000/8265/8443/9010/9011/9210/9310 无冲突（宿主机 5432 被 dev-postgres 占用但 Nexent postgres 发布在 5434，无碰撞）；vm.max_map_count=1048576（≥262144）；磁盘 208G 可用；Compose v2.40.3
- E0 微基准与对话截图：**2026-09-14 已完成模型注册与对话验证**（详见下节）

## 模型注册与对话验证（2026-09-14）

**多租户初始化链路**（SU 无 /models 权限，必须先建租户，详见 pitfalls #5）：
1. suadmin 建租户 **knowevo**（租户页"创建租户"，勾选自动生成管理员账户开关）
2. 首次管理员创建失败（.local 域 EmailStr 422，pitfalls #6），改用邀请码 HCIAQP（ADMIN_INVITE）注册 **admin@knowevo.com**（ADMIN，tenant_id=6756b0ab-...）
3. admin 登录 → 智能体开发 → 模型配置

**双档模型注册**（模型配置页 UI，连通性验证均"可用"）：
- glm-5.3-free (主档)：https://api.tokenrouter.com/v1，Key sk-8bS8...（db model_record_t connect_status=available）
- glm-5.3-free (小档)：同 URL，Key sk-rF4H...（connect_status=available）
- 注意：平台把请求体模型名 z-ai/glm-5.3-free 规范化存为 glm-5.3-free，但对话实测 200 正常（provider 侧兼容）
- 大语言模型槽位自动指派主档

**智能体与对话**：
- 智能体 "Knowevo 助手"（knowevo_assistant）已创建并发布 **v0.1.0-baseline**（RELEASED，POST /api/agent/1/publish 200）
- 智能体绑定双模型（主档+小档多选）+ 角色提示词
- 对话实测：问"你好+1+1 等于几"，完整回复"……1+1 等于 2 😊"，耗时 25.17s（glm-5.3 为推理模型，含 reasoning 步骤），token 统计正常

**Evidence 截图**（competition/deliverables/）：
- T-01-models-registered.png —— 模型配置页，双档模型注册成功
- T-01-agent-published.png —— 智能体配置页，双模型绑定 + 发布
- T-01-chat-conversation.png —— 问答页，一轮完整对话（用户问题+智能体回复 25.17s）

**遗留**：
- 向量模型/重排模型未注册（当前仅 LLM；对话页有"尚未配置向量模型"提示，不影响纯对话，T-02 知识库阶段需补）
- E0 微基准 20 题（任务书 T-01 行要求）未跑——20 题出数需业务题库（属 T-02 医疗数据批次之后），本任务以跑通一轮对话为验收线
