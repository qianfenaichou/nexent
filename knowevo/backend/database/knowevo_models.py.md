# knowevo_models.py —— 12 表 ORM（database 层）
**归属任务**: T-03（唯一建模权，03 计划 §3.3）· **DDL 事实源**: [备忘录 10-A2 §2](../../../../02-技术方案.md)
**纪律**: 本文件是全部新表的唯一属主；后续任务只使用不 ALTER（要改=新迁移文件+新简报）。字段级 DDL 已在备忘录 10 冻结，此处只写 ORM 层的实现约定。

## 表清单（12）
`ontology_version_t` · `ontology_change_proposal_t` · `kg_entity_t` · `kg_relation_t` · `kg_evidence_t` · `kg_pending_entity_t` · `doc_asset_t` · `doc_version_diff_t` · `decision_card_t` · `evolution_round_t` · `skill_template_t` · `eval_run_t`

## ORM 实现约定
1. 风格对齐上游 `backend/database/`（SQLAlchemy async, `Base` 注册进既有导入链——接线项见下）。
2. JSONB 字段配 Pydantic 模型（payload 的类型化视图：`DecisionCardPayload`、`OpsLogItem`、`ImpactReport` 等，Pydantic 模型放 `services/knowevo/schemas.py`——**T-03 与 T-09 分工的 seam**）。
3. `kg_entity_t.embedding`：T-03 探测 pgvector 可用性（上游 PG 14+ 无预装），不可用则 `JSONB` 数组列 + 注释说明（GraphStore 服务层 cosine）；探测结论进 pitfalls 台账。
4. 索引按备忘录 10 DDL 原样进迁移文件 `deploy/sql/migrations/v2.5.x_kw_001_knowevo_core.sql`（只增不改铁律）。
5. 所有表带 `tenant_id`（多租户隔离对齐上游模式）；服务层查询强制 tenant 过滤（apps 注入）。

## 接线项（T-03）
- `Base` 注册：knowevo_models 导入进上游 db_models 导入链（一行 import）；
- migrate 启动加载新迁移目录文件（验证 `bash deploy/migrate` 或等价命令本地通过）；
- 环境变量：备忘录 10 §3 清单写入 `backend/consts/const.py`（T-03 一次性，禁任务级发明）。

## 验收锚点
- `pytest test/backend/services/knowevo/test_models.py -v`：12 表 CRUD 冒烟 + tenant 隔离（跨租户读写 403/空）+ bi-temporal 谓词的 ORM 表达（现行视图查询构造器）。
- 迁移可执行且幂等（drop 后重建再跑一次无 diff）。
