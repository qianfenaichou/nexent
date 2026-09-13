# knowledge_graph_app.py & asset_app.py —— L1-L4 HTTP 边界（apps 层）
**归属任务**: T-03（骨架+接线）/ T-05（工作台 API）/ T-09（决策 API）/ T-14（资产 API）
**Nexent 分层铁律**: apps 只做 解析/鉴权/路由注册——零业务逻辑；业务全在 services。

## knowledge_graph_app.py（挂 config_app: 5010，管理面）
```
router = APIRouter(prefix="/api/knowevo", tags=["knowevo"])

管理面（工作台/看板用）:
POST   /ontology/seed                  → ontology.extract_seed            (T-04)
GET    /ontology/proposals             → 待审队列（分页+排序特征）          (T-04/T-05)
POST   /ontology/proposals/{id}/review → confirm/reject/reparent           (T-05)
POST   /ontology/versions              → commit_version                    (T-05)
GET    /ontology/versions/{v}/metrics  → K0 四指标                         (T-05)
GET    /ontology/diff                  → diff(from,to)                     (T-05/T-12)
GET    /graph/pending                  → 待审池                            (T-06)
POST   /graph/pending/{id}/resolve     → 人审对齐裁决                      (T-06)
GET    /evolution/timeline             → evolution.timeline                (T-12)
GET    /evolution/rounds/{id}          → round_detail                      (T-12)
POST   /evolution/rounds/{id}/rollback → rollback                         (T-11)
GET    /assets                         → 资产清单（分页/模态/权威过滤）     (T-14)
POST   /assets                         → 资产登记+版本血缘                  (T-02/T-14)
GET    /eval/runs                      → eval_run_t 查询                  (T-10b/T-12)

运行面（决策会话/演示用）:
POST   /decisions                      → decision.render_card            (T-09)
GET    /decisions/{id}                 → 决策卡（含证据链展开）            (T-09)
POST   /decisions/{id}/rerun           → 按当前知识戳重出卡                (T-11)
```
**鉴权**: 复用 Nexent 多租户 RBAC 中间件（`tenant_id` 从会话注入，服务层签名里的 tenant_id 全部由 apps 层注入——services 不读请求上下文）。

## asset_app.py（可并入 knowledge_graph_app；独立存在的理由）
L1 资产域（摄取回调/解析体检/血缘图）预留给 data-process 服务（5012）联动——Unstructured 摄取完成的 webhook 回调入口、`parse_quality` 打分、`supersede_of` 血缘维护。若 T-02 实施时发现 Nexent 原生知识库 API 已覆盖登记需求，则本 app 收缩为"资产血缘+体检"两个端点的薄层（03 计划 §1.1 已注明"可并入上者"）。

## 接线项（归 T-03/T-08，禁功能任务碰）
- `config_app.py` / `runtime_app.py`: `include_router(knowevo_router)` 各一行；
- 决策运行面若走 runtime（5014）：按"沙箱调用本地服务"模式注册（与 Nexent 原生 agent 会话工具一致的入口），具体挂载点 T-09 时按仓库实际裁定并回报。

## 验收锚点
- 路由冒烟（pytest + TestClient）：每个端点 happy path + 鉴权 401/403；
- OpenAPI schema 生成无冲突（与上游 72 表既有端点前缀无碰撞——`/api/knowevo` 命名空间隔离）。
