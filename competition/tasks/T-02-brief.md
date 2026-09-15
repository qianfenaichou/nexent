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
- [x] registry.csv 58 份（<60 下界，缺口 2 份如实报告见下）；每行 source_url 非空且真实访问验证（三批核查代理逐 URL 直连 + 人工抽查 4 处关键源全 200；不可用源清单已存档）
- [x] 2020/2024 指南两版就位且 supersede_of 血缘已建（`select a.asset_no,b.asset_no from doc_asset_t a join doc_asset_t b on a.supersede_of=b.id` → guide-2024→guide-2020 + guide-elderly-2024→guide-elderly-2021 两对）
- [x] doc_asset_t 58 行=registry 58 行；幂等验证：重跑 CLI registered=0 skipped=58 errors=0
- [x] 原生知识库可检索：`POST /api/indices/search/hybrid` 三查询（二甲双胍适应证/HbA1c 控制目标/糖尿病足 Wagner 分级）全部命中带 filename+path_or_url 出处的真实指南内容，~450ms
- [x] parse_quality 58 份全量落库（update_parse_result）；<0.6 清单 0 条（parse_report.md 已生成，含全量分数表）
- [x] 80/20 切分清单落 blind_split.json（deterministic sha256 切分：build 40 / blind 18）
- [x] 2020/2024 diff 条目 16 条落 guideline_diff_seed.md（含 T-11 复核清单节，如实标注"公开材料整理、未经原文逐页 diff"）
- [x] E0 微基准 20 题双档数据落 competition/docs/e0-baseline.md（主档 45%/24.8s/10310tok；小档 65%/36.8s/13102tok；三轮原始数据 e0_raw_round3.json 归档；回收 L1/L2/L7）
- [x] 新踩坑记 pitfalls.md #15-#21（minio/local、embedding base_url 端点、Ray OOM、compose 网络 label、宿主 DNS、免费档 429/空响应、标准号/标题一手源核验）
**Evidence**（2026-09-14/15 全部实测输出）:
- **pytest 单测**：`uv run pytest ../test/backend/services/knowevo/test_ingest_assets.py -v` → **17 passed, 2 skipped**（PG 集成门控跳过）
- **pytest PG 集成**：`POSTGRES_HOST=localhost POSTGRES_PORT=5434 ... RUN_POSTGRES_INTEGRATION=1` → **19 passed**（幂等登记/血缘/parse 回写/租户隔离全绿）
- **CLI dry-run**：`python -m services.knowevo.pipeline.ingest_assets --registry ../competition/corpus/registry.csv --dry-run` → `parsed=58 valid=58 invalid=0`，exit 0
- **CLI 幂等重跑**：`registered=19 skipped=39 errors=0`（第三批并入后重跑 registered=0 skipped=58）
- **psql 计数**：`select count(*),doc_type from nexent.doc_asset_t group by doc_type` → drug_label 28 / guideline 11 / policy 9 / edu_graphic 8 / lab_report 2 = **58 行**（=registry 58 行含表头 59 行文件）
- **摄取全链**：build 40/40 文档经 `POST /api/file/upload`(destination=minio) → `POST /api/file/process` → Unstructured 解析 → bge-m3 embedding → ES 索引 **1497 chunks**（2020 版 332 / 2024 版 407 / 营养指南 192 / 老年 2024 版 144 等，分文件清单见 parse_report.md）
- **hybrid 检索冒烟**：`curl -X POST localhost:3000/api/indices/search/hybrid -d '{"index_names":["kw-medical-b1"],"query":"二甲双胍适应证","top_k":3}'` → HTTP 200，命中 t2dm_guideline_2020.pdf "二甲双胍为 T2DM 药物治疗和药物联合中的基本用药"等带出处 chunk，query_time 449ms（另两组 438/451ms）
- **T-01 遗留回收**：①向量模型注册——tokenrouter 两 Key 均无 embedding 权限（额度 ¥0），用户提供 SiliconFlow Key，注册 Pro/BAAI/bge-m3（1024 维）connectivity=true available，索引 kw-medical-b1 绑定成功（坑 #16：base_url 须填完整 /v1/embeddings 端点）；②E0 微基准双档出数落 e0-baseline.md
- **语料原件**：competition/corpus/{guidelines 11 PDF, drug_labels 30, pathways 9 PDF, lab_standards 2, public_edu 8} 全部落盘（fetch 脚本 fetch_drug_labels.sh / fetch_third_batch.sh 可复现，均带已验证 URL）
- **三批核查代理报告归档**（2026-09-15 补）：`competition/docs/verification-reports/{batch1-drug-labels, batch2-guidelines, batch3-pathways-lab-edu}.md` + README（含合并门 13 样本抽查记录：13/13 直连 200、content-type/字节数吻合、锚点 PDF 首页标题与 license_note 一致、58 行本地校验零问题）
- **已知缺口（如实报告）**：① registry 58 份 < 60 下界 2 份——1 型糖尿病指南 2021 官方 PDF 付费墙内、DKA 临床路径官方体系不存在、HbA1c 检测技术指南 2022 无免费公开版、NHC 官网 412 反爬（详见三批核查报告不可用源清单），按"宁缺毋滥"未收录拿不到真实 URL 的文档；② E0 双档实为同模型不同 Key（glm-5.3-free），分档对比不构成模型能力差异证据（正式双档待 T-08）；③ blind 18 份未入任何构建流水线（设计如此，T-10 出题用）；④ hybrid 命中含少量 HTML 页面侧栏噪声 chunk（如"相关问答"），已在 parse_checkup 噪声统计中捕捉，清洗归 T-06

## 反幻觉条款（发任务时必附）
开工先读仓库根 AGENTS.md；registry.csv 的 source_url 必须是你真实访问过的 URL，拿不到的文档宁缺毋滥（60 份是下限不是任务失败线，如实报告缺口）；原生 API 路径以 apps/*.py 源码 grep 为准（file_management_app.py/vectordatabase_app.py），与简报不符时停下报告；不得发明环境变量。
