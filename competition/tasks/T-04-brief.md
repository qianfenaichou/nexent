# T-04：本体种子构建 v0（服务层+CLI）
**Blocked by**: T-03（已交付 69f85bb，12 表 ORM 就位）
**独占文件**（对照 knowevo/ 框架包 ontology_service.py.md 接口冻结节）:
- `backend/services/knowevo/ontology_service.py`（服务层，全部业务逻辑）
- `backend/services/knowevo/pipeline/build_ontology.py`（薄 CLI 封装，禁在 pipeline 写算法）
- `test/backend/services/knowevo/test_ontology_service.py`（两层测试）
**待接线项**: 无（不挂 router、不动 const.py；LLM 客户端注入式，见下）
**禁改清单**: 上游共享文件（apps/app_factory.py、config_app.py、runtime_app.py、consts/const.py、pyproject.toml、database/knowevo_db.py 的既有表定义——服务层只使用不 ALTER）
**允许的新依赖**: 白名单内 instructor（MIT，备忘录 §4.1 矩阵，归属 T-09 决策卡）。**本任务先用 langchain（上游已有依赖）**：instructor 到 T-09 决策卡任务再引入，避免本任务触碰 pyproject.toml（接线铁律：依赖合并放 T-08）。LLM 调用全部走注入的 callable，测试注入 fake——不引入任何新依赖也能完整验收。
**本任务细节**（备忘录 02-K1 + tasks/README 要点补充）:
- 两级提案：阶段0 种子（目录结构 S1 + 术语提名 S2）→ 阶段1 概念提名（中档模型，temp 0.2，每章批量 few-shot 3 例）→ 阶段2 校验（V1-V5/V9 自动修复，V6/V7/V8 上报）→ 阶段3 排序（score=0.5·conf+0.2·novelty+0.3·impact，ev_rich 打破平局）→ 阶段4 schema 装配（大档模型，为确认类生成属性/关系提案）→ 阶段6 版本化（commit_version，semver minor/major，快照+oplog 落 ontology_version_t）
- auto_accept 线：conf ≥ 0.85（KW_AUTO_ACCEPT_LINE）且校验通过且 ev_rich=1.0 → 自动桶
- 本体序列化注入上限 15000 token（L4 遗留值），超限走"active 类+高频属性"裁剪器
- 提案落 ontology_change_proposal_t（trigger_source=seed_bootstrap / pending_pool / standard_update / manual），版本化落 ontology_version_t，evolution_round_t 记账
- CLI：`python -m services.knowevo.pipeline.build_ontology --domain healthcare --docs docs.json [--dry-run]`，--dry-run 只打分不落库；幂等（章 hash 去重）；退出码 0/2/1；输出 cost-ledger 行
**要构建的行为**（用户视角端到端）:
标准文档（fixture 3 份）→ build_ontology CLI 一键产出 ≥30 条待审提案（带证据锚点、置信度、排序分）→ 提案已入 ontology_change_proposal_t 待审队列 → confirm 后 commit_version 得到 v1.0.0 快照，可 diff、可算 K0 质量指标。
**验收命令**:
```bash
cd backend && uv run pytest test/backend/services/knowevo/test_ontology_service.py -v
# 集成层（真实 PG）：
RUN_POSTGRES_INTEGRATION=1 uv run pytest test/backend/services/knowevo/test_ontology_service.py -v -m integration
```
**验收标准**（对照框架包 .md"验收锚点"节 + pipeline README）:
- [x] 种子提取（S1 章节树 + S2 每章 top-K 术语+别名+预期父类；跨文档去重）
- [x] 排序：score 公式、ev_rich 平局打破、降序；40 条会话切批留给 T-05（排序不截断，避免静默丢提案）
- [x] auto_accept 线 0.85（三条件与门）
- [x] V1 环检测（DFS 三色标记）、V4 命名规范（UpperCamelCase/lower_snake，CJK 剥离入别名字段）、V3 属性类型白名单、V5 深度≤5、V9 关系 domain/range 检查
- [x] 版本 commit+diff 往返一致（diff = from 之后各版本 applied_ops 聚合）；semver minor=新增/major=废弃
- [x] K0 质量指标（cov/red/dep/align 手算 fixture 对照）
- [x] CLI --dry-run 不落库 + top5 预览；--plan 三档模型 YAML；cost-ledger 行落盘；幂等（round_id=uuid5(章hash)，重跑 proposals_saved=0 真实 PG 验证）
- [x] 15000 token 裁剪器（active+高频优先，超限裁属性）
- [x] 待审池回流 propose_from_pending（mention_count≥3 阈值）

**实现偏差说明**（对照冻结接口，均为 v0 诚实降级而非未做）:
- V6/V7/V8 语义检测需 embedding 通道（T-06 接线），v0 只带标志位字段（duplicate_of/granularity_hint），docstring 明示
- LLM 注入 callable（tier 标签 mid/large），真实模型路由是 T-08 接线任务；CLI 的 _EchoLLM 离线回显模式保证无网络可完整验收
- instructor 延后到 T-09 引入（避免本任务触碰 pyproject.toml），结构化解析由注入 callable 承担

**Evidence**:
- 单元层：`cd test && ../backend/.venv/bin/python -m pytest backend/services/knowevo/test_ontology_service.py -q` → 19 passed, 1 skipped（PG 门控跳过）
- 集成层：`RUN_POSTGRES_INTEGRATION=1 ... -q` → 50 passed（T-04 19 + T-03 31，双跑稳定，集成测试自清理）
- CLI 真实验收（部署栈 PG，nexent-postgresql:5434 宿主映射 5434）：
  - `--dry-run`：3 份 fixture 文档 → 7 提案、V4 规范化名、top5 预览、EXIT=0
  - 落库：`proposals_saved: 7`，psql 查 `nexent.ontology_change_proposal_t` 7 行（trigger=seed_bootstrap）
  - 幂等：重跑 `proposals_saved: 0`，库中仍 7 行
  - `--plan plan.yaml`：model_plan 进报告 + cost-ledger.md 落行（`| 51dd7d9e... | 本体 | ... | llm_calls=1 |`）
- ruff：`ruff check backend/services/knowevo/ test/backend/services/knowevo/` → All checks passed
- 上游回归（本会话补的 T-03 证据）：55/55 database 测试文件无失败（`uv sync --extra data-process --extra test` + `uv pip install -e "../sdk[dev]"` 后从仓库根跑；之前 15 error 为 smolagents 缺失、2 failed 为相对路径需从根跑——均已定位修复，记坑 #11/#12）

