# T-02：医疗数据资产批次1（糖尿病域 60-80 份）
**Blocked by**: T-01（已交付：13 容器全 Up、admin@knowevo.com ADMIN 就绪、双档 LLM 已注册）
**独占文件**:
- `competition/corpus/`（新建目录：语料原件 + `registry.csv` 出处登记 + `ingest_manifest.json`）
- `backend/services/knowevo/pipeline/ingest_assets.py`（薄 CLI：登记 doc_asset_t + 触发原生摄取）
- `test/backend/services/knowevo/test_ingest_assets.py`
**待接线项**: 向量模型注册（运行时操作，非代码——见"前置缺口"）；若发现登记需求超出原生 API，登记到 asset_app.py（归 T-14），本任务不建新 app
**禁改清单**: 上游共享文件（apps/*.py、consts/const.py、pyproject.toml、knowevo_db.py 既有表定义——只 INSERT 不 ALTER）
**允许的新依赖**: 无（纯文件+CLI+已有 requests/httpx；**不碰 pyproject.toml**）
**本任务细节**（备忘录 08-K8 §4 + tasks/README T-02 行）:
- 语料口径 60-80 份（K8 裁决：上限不在 token 在人力，卡点是解析质量）：指南 2 版（中国 2 型糖尿病防治指南 2020 vs 2024——T-11 变更检测的锚文档）+临床路径+药品说明书+检验单样本+图文
- **80/20 构建盲区切分**：80% 文档走"构建"（T-04/T-06 用），20% 留盲区（只进 T-10 评测出题，不进任何构建流水线——防评测泄漏）
- 2020/2024 指南 diff 条目预核对（回收 L9 遗留值：人工列 10-20 条变更清单，T-11 的 P/R 金标种子）
- 出处登记进 `registry.csv`：title / doc_type / modality(text|table|image_text) / authority_level(1国标/2指南/3说明书/4科普) / source_url / license_note —— 每份语料必须可溯源（答辩合规线）
- 登记落 `doc_asset_t`（T-03 已建表：asset_no 编号 + supersede_of 版本血缘——2020/2024 指南挂血缘链）+ parse_quality 分入该表字段
- 摄取走 **Nexent 原生链**：`POST /api/file/upload`（file_management_app.py L115）→ `POST /api/file/process`（L207，data-process 容器 Unstructured 解析）→ 知识库索引 `POST /api/vectordatabase/{index_name}`（vectordatabase_app.py L87）+ 文档入索引（L543）。前端等价操作：知识库页上传
- 解析体检（parse checkup）：每份抽样比对原件 vs 解析产物（表格结构/章节树完整性），分数落 doc_asset_t.parse_quality；<0.6 的文档单独列清单
- **E0 微基准顺带回收**（T-01 遗留）：语料就位后用双档模型对 20 题出数（L1/L2/L7 遗留值）
**前置缺口（开工先处理，10 分钟）**:
- 向量模型未注册（T-01 遗留：仅 LLM 已配）——知识库索引需要 embedding。admin 登录 → 模型配置 → 注册向量模型（tokenrouter 有 embedding 端点则注册；无则报告并降级：先只做 parse 链路，索引留待接线任务）
**要构建的行为**（用户视角端到端）:
60-80 份糖尿病域文档就位于 `competition/corpus/`（每份可溯源）→ `python -m services.knowevo.pipeline.ingest_assets --registry competition/corpus/registry.csv` 一键完成：doc_asset_t 全量登记（带血缘/权威/模态）→ 摄取进 Nexent 知识库 → 原生检索 `POST /api/vectordatabase/search/hybrid` 对"二甲双胍适应证"返回带出处的命中 → 解析体检分落库。
**验收命令**:
```bash
# 前置：栈在跑（docker ps），admin 会话可用
cd backend && uv run pytest ../test/backend/services/knowevo/test_ingest_assets.py -v
RUN_POSTGRES_INTEGRATION=1 uv run pytest ../test/backend/services/knowevo/test_ingest_assets.py -v
# 端到端冒烟：
python -m services.knowevo.pipeline.ingest_assets --registry ../competition/corpus/registry.csv --dry-run
docker exec nexent-postgresql psql -U root -d nexent -c "select count(*),doc_type from nexent.doc_asset_t group by doc_type"
curl -s -X POST localhost:3000/api/vectordatabase/search/hybrid -H "..." -d '{"question":"二甲双胍适应证",...}'
```
**验收标准**:
- [ ] registry.csv ≥60 份且每行 source_url 非空（真实公开来源，禁止编造）
- [ ] 2020/2024 指南两版就位且 supersede_of 血缘已建（doc_asset_t 可查）
- [ ] doc_asset_t 行数=registry 行数（幂等：重跑不重复登记）
- [ ] 原生知识库可检索：search/hybrid 返回带文档出处的 chunk
- [ ] parse_quality 全量落库；<0.6 清单存 `competition/corpus/parse_report.md`
- [ ] 80/20 切分清单落 `competition/corpus/blind_split.json`（build/blind 标记）
- [ ] 2020/2024 diff 条目 ≥10 条落 `competition/corpus/guideline_diff_seed.md`
- [ ] E0 微基准 20 题双档数据落 `competition/docs/e0-baseline.md`（回收 L1/L2/L7）
- [ ] 新踩坑记 pitfalls.md
**Evidence**: <测试输出/检索命中截图/psql 计数——没有证据=没做完>

## 反幻觉条款（发任务时必附）
开工先读仓库根 AGENTS.md；registry.csv 的 source_url 必须是你真实访问过的 URL，拿不到的文档宁缺毋滥（60 份是下限不是任务失败线，如实报告缺口）；原生 API 路径以 apps/*.py 源码 grep 为准（file_management_app.py/vectordatabase_app.py），与简报不符时停下报告；不得发明环境变量。
