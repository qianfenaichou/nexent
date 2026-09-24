# 真实脱敏行业数据集效果评测方案（前期可行性）

> 产出：任务Q5b · C4（对应官方**中国总决赛附加分②**「真实脱敏行业数据集开展效果评测」，官网原文见 `verification-reports/official-site-2026-09-23.md` §三 L54）。
> 本文件是**前期可行性方案**：只回答「能拿到哪些公开脱敏行业数据集 / 接进来要多少成本 / 用现有口径怎么评」三问，**不含任何实测结果**（本轮零服务依赖、未跑任何数据集）。
> **核实口径**：数据集的名称 / 来源 URL / 许可 / 访问方式凡经联网核实的，逐条标注「已核实（URL + 访问日期 2026-09-24）」；抓取失败或无法确认的，标注「未核实」或直接删除，**不编造数据集名称、URL、许可或数值**。
> **红线**：附加分②属**中国总决赛**段（晋级后才需要），初赛不直接计分（`official-site-2026-09-23.md` §一/§三）；本文件只作**同向铺路**，不在初赛材料中宣称「已完成真实脱敏数据集评测」。

---

## 〇、结论先行（TL;DR）

1. **可得性**：公开可下载/申请的脱敏行业数据集是**充足**的——本文核实了 **10 条**，覆盖医疗（6）/制造（2）/金融（1）/政务（1）四个方向，其中**6 条为 ODbL/CC 系开放许可可直接获取**、**3 条为「申请制/凭证制」（gated，需审核或签署 DUA）**、**1 条为平台级（无单一 CC 许可，受平台用户协议约束）**。
2. **最大障碍不是「拿不到数据」，而是「数据形态不匹配」**：公开数据集绝大多数是**结构化表格（CSV）**或**标注 JSON**，而本项目现有摄取管线（`registry.csv` → `doc_asset_t` → `parse_corpus`）吃的是**文档（PDF/HTML）文本**。表格型数据集接入成本高（需文本化或新增适配器），文档型/标注型接入成本低到中。
3. **评测方案可沿用现有口径零改**：E1 基线（`eval_e1`，A1_pure_rag = BM25 纯检索）、E2 消融（`pipeline/ablation.py`，A1→A4）、T-21 对齐（话题级 recall）三套评测器**均已存在且有 `--testset` 加性参数**，新增行业语料只需补一份题集 JSON，**不改评测器代码**。
4. **诚实定位**：本文 ③ 的评测方案是**设计**，不是**结果**；所有「效果」格位在真跑前一律 `insufficient_data`。申请制数据集（MIMIC-IV / MMC 队列等）能否拿到，取决于审核/合规，**不预设可得**。

---

## 一、可得数据集清单（每条：名称 + 来源 + 许可 + 访问方式 + 核实状态）

### 1.1 医疗方向

| # | 名称 | 来源（URL） | 许可 / 使用条款 | 访问方式 | 核实状态 |
|---|---|---|---|---|---|
| M1 | **Diabetes 130-US Hospitals for Years 1999-2008**（美国 130 家医院 10 年糖尿病住院记录，10.1 万行 / 47 特征，**去标识化摘要**） | UCI ML Repo `https://archive.ics.uci.edu/dataset/296`（DOI `10.24432/C5230J`） | **CC BY 4.0**（署名即可商用/改编） | 直接下载（`ucimlrepo` 包或网页） | **已核实**（UCI 页面明示 CC BY 4.0 + DOI；来源即 Cerner Health Facts 的 de-identified abstract） |
| M2 | **DiaKG**（中文糖尿病科研文献实体关系数据集，22,050 实体 / 6,890 关系，源自 41 篇糖尿病指南与共识；CCKS 2021） | 阿里云天池 `https://tianchi.aliyun.com/dataset/dataDetail?dataId=88836`；OpenKG `http://openkg.cn/dataset/diakg` | **CC BY-NC 4.0**（署名 + **非商业**）；天池页另注 OpenKG 版标 CC BY-SA 4.0（**两处许可标注不一致，以天池页 CC BY-NC 4.0 为准**） | **申请制**：点「申请」填表并同意使用条款，**7 天内审核**，通过后下载 | **已核实**（天池页 + OpenKG 页 + 论文 `arXiv:2105.15033` 三源一致；**许可两源不一致已如实标注**） |
| M3 | **CBLUE**（中文生物医学语言理解评测基准，8 子任务：实体识别/关系抽取/术语归一化/文本分类/句对/QA；含 **CN-Diabetes-QC 糖尿病质控**子任务） | 天池 `https://tianchi.aliyun.com/dataset/81513`（ChineseBLUE，**CC BY-NC 4.0**）、`https://tianchi.aliyun.com/dataset/211461`（CBLUE 4.0，**CC BY-NC-SA 4.0**）；代码 GitHub `https://github.com/CBLUEbenchmark/CBLUE`（**Apache-2.0**，仅代码） | 数据 **CC BY-NC 4.0 / CC BY-NC-SA 4.0**（非商业）；代码 Apache-2.0 | **申请制**（天池评测平台，需提交申请） | **已核实**（天池两版数据集页 + GitHub 仓库 + ACL 2022 论文 `aclanthology.org/2022.acl-long.544` 一致） |
| M4 | **MIMIC-IV**（MIT/BIDMC 重症监护电子病历，去标识化） | PhysioNet `https://physionet.org/content/mimiciv/` | **PhysioNet Credentialed Health Data License 1.5.0** + **Data Use Agreement（DUA）**；需 CITI「Data or Specimens Only Research」培训 | **凭证制（gated）**：PhysioNet 注册 → 完成人研培训 → 签 DUA → 审核（1–2 周） | **已核实**（PhysioNet 与 mimic.mit.edu 官方访问指南；License/DUA 版本号与训练要求均为页面原文） |
| M5 | **共享杯版_糖尿病并发症预警数据集 V1.0**（解放军总医院提交） | 国家人口健康科学数据中心 `https://www.ncmi.cn/phda/dataDetails.do?id=CSTR:A0006.11.A0005.202006.001018`（DOI `10.12213/11.A0005.202006.001018`） | **CC0 1.0**（公有领域奉献，可商用）；**公益免费、商业收费** | 平台申请（依平台流程） | **已核实**（NCmi 页面明示 CC0 1.0 + DOI + 收费方式；**注意 CC0 与"商业收费"并存，需按页面流程执行**） |
| M6 | **2017-2020 国家代谢性疾病标准化管理中心（MMC）队列糖尿病患者临床及流行病学信息数据集**（25 万例 2 型糖尿病，覆盖 30 省市，**直接糖尿病域**） | 国家人口健康科学数据中心（NCmi） | **线下共享 / 有条件共享**（`escience.org.cn` 元数据页明示） | **申请制 + 线下共享**：按 PHDA 服务流程申请 | **已核实**（中国科技资源共享网 `escience.org.cn` 元数据页；共享方式原文"线下共享/有条件共享"） |

> 说明：M6 与本项目现有语料（2 型糖尿病指南）**同域**，是"真·行业数据集效果评测"最贴切的一条，但它**是有条件共享的线下数据**，能否取得取决于机构审核，**不得预设可得**。

### 1.2 制造方向

| # | 名称 | 来源（URL） | 许可 / 使用条款 | 访问方式 | 核实状态 |
|---|---|---|---|---|---|
| F1 | **AI4I 2020 Predictive Maintenance Dataset**（合成工业预测性维护数据，10,000 条 / 14 特征 / 5 类故障标签） | UCI ML Repo `https://archive.ics.uci.edu/dataset/601`（DOI `10.24432/C5HS5C`） | **CC BY 4.0** | 直接下载 | **已核实**（UCI 页面明示 CC BY 4.0 + DOI + 论文 *Explainable AI for Predictive Maintenance*, S. Matzka 2020） |
| F2 | **NASA PCoE Turbofan Engine Degradation Simulation（C-MAPSS）**（涡扇发动机退化仿真，4 组工况/故障模式） | Zenodo 镜像 `https://zenodo.org/records/15346912`（DOI `10.5281/zenodo.15346912`）；原 NASA PCoE 仓库 | **Zenodo 标注 CC BY 4.0**；NASA 侧口径为「U.S. Government Work，公共领域，开放供非商业研究」（**两处口径并存，已如实标注**） | 直接下载 | **已核实**（Zenodo 记录页明示 CC BY 4.0 + DOI；NASA 原始仓库 `ti.arc.nasa.gov/.../pcoe/prognostic-data-repository`） |

> 说明：F1 是**合成**数据（论文自述"reflective of real predictive maintenance"，非真实产线），F2 是**仿真**数据。两者均非"真实行业脱敏数据"，接入价值在于**跨域可迁移性验证**（换域零改骨架，见 §三），**不得对外称其为"真实制造行业脱敏数据集"**。

### 1.3 金融方向

| # | 名称 | 来源（URL） | 许可 / 使用条款 | 访问方式 | 核实状态 |
|---|---|---|---|---|---|
| N1 | **Statlog (German Credit Data)**（德国信用数据，1000 条 / 20 特征；属性已符号化去标识） | UCI ML Repo `https://archive.ics.uci.edu/dataset/144`（DOI `10.24432/C5NC77`） | **CC BY 4.0** | 直接下载 | **已核实**（UCI 页面明示 CC BY 4.0 + DOI；页面原文"All attribute names and values have been changed to meaningless symbols to protect confidentiality") |

### 1.4 政务方向

| # | 名称 | 来源（URL） | 许可 / 使用条款 | 访问方式 | 核实状态 |
|---|---|---|---|---|---|
| G1 | **上海市公共数据开放平台**（市级政务开放数据统一平台） | `https://data.sh.gov.cn/` | **平台用户协议**（非单一 CC 许可）：分「无条件开放类」（免注册直接下载）与「有条件开放类」（申请审核）；利用成果须注明数据来源与下载日期 | 无条件类直接下载；有条件类提交申请 | **已核实（平台级）**：平台页 + 《上海市公共数据开放实施细则》（`sh.gov.cn` 2023 版）确认三分类与获取流程；**未锁定单一具体数据集**（属平台级，非单条数据） |

> 未核实并**已删除/不列入**：国家政府数据统一开放平台（`www.data.gov.cn`）——**本次抓取失败（fetch failed）**，其可访问性、具体数据集与许可**未核实**，故不作为可得条目；如需使用，须由后续会话重新联网核实后再入册。

---

## 二、接入成本评估（对照现有 `registry.csv` / `doc_asset_t` / 摄取管线）

### 2.1 现有接入管线（读代码所得，非推测）

- **注册入口**：`competition/corpus/registry.csv`（10 列：`asset_no,title,doc_type,modality,authority_level,source_url,license_note,local_file,split,published_at`；定义见 `backend/services/knowevo/ingest_service.py:38-51`）。
- **白名单约束**（同文件）：`DOC_TYPES={guideline, drug_label, lab_report, policy, material_list, edu_graphic}`、`MODALITIES={text, table, image_text}`、`AUTHORITY_LEVELS={1,2,3,4}`、`SPLITS={build, blind}`。**任一越界即该行报错、整行被拒**（`parse_registry` 逐行校验，不静默丢弃）。
- **落库映射**：`RegistryRow.to_doc_asset_values()`（`ingest_service.py:70-92`）→ `doc_asset_t`（`parse_status="pending"`，`source_url`/`source_note` 空值记 `None` 不臆造）。
- **文本摄取**：`pipeline/ingest_assets.py`（CLI）→ `parse_corpus`（`e1_retrieval.py`，吃 **PDF/HTML**）→ 分块 `chunk_plain_text`（**600 字/块**，与 E1/E2 同口径，`e1_retrieval.py:36`）。
- **评测器**：`pipeline/eval_e1.py`（E1 基线）、`pipeline/ablation.py`（E2 A1→A4 消融）、E8 版本钉住；题集由 `corpus/testset-v1-seed.json` 驱动，`eval_v1` 有 **`--testset` 加性参数**（默认路径逐字符不变）+ `validate_testset_file`（坑 #63 落地）。

### 2.2 三条数据形态的接入成本

| 形态 | 代表条目 | 是否需要清洗 | 与 `doc_asset_t`/管线的对接改动 | 成本判断 |
|---|---|---|---|---|
| **(a) 文档型**（指南/共识 PDF/HTML） | 无新件（现有 58 份即此型）；M2 的**源**（41 篇指南）属此型 | 需 OCR 校正（DiaKG 论文自述 β 细胞误识为 B 细胞） | **零改**：registry.csv 加行 + PDF 落 `corpus/` + 跑 `ingest_assets` | **低** |
| **(b) 标注型**（实体/关系/分类 JSON） | M2 DiaKG、M3 CBLUE | 需 schema 映射（其 18 类实体 / 15 类关系 ↔ 本项目 `kg_entity_t`/`kg_relation_t`） | **中**：标注 JSON **不是语料**，应作为**评测金标**接入（对齐/抽取的 gold），不直接入 `doc_asset_t`；需写一个 gold 适配脚本 | **中** |
| **(c) 结构化表格型**（CSV） | M1、F1、F2、N1 | 需表头/编码归一 + 缺失值处理（M1 页面已列缺值列） | **高**：`doc_type` 白名单**无「dataset/table」类**，`modality=table` 虽存在但 `doc_type` 无对应值 → 直接入册会被 `parse_registry` 判错。两条路：① 文本化（把行转自然语言句）后按现有文档型接入；② **扩 `DOC_TYPES` 白名单 + 新增 CSV 适配器**（改 `ingest_service.py` 契约，属**契约变更**，须单独评审放行） | **高** |

### 2.3 标注与清洗工作量（定性）

- **数据集自带标注**：M1/M5 是**结构化标签**（读模型标签），M2/M3 是**实体/关系/分类金标**，F1/F2 是**故障/剩余寿命标签**，N1 是**信用好坏标签**——**均无需本项目再人工标注标签本身**。
- **本项目需自建的是"评测题集"**：把行业语料转成 F/M/V/X 四型问题 + 金标（口径同 `testset-v1-seed.json`，其中 X 型含"库外拒答"设计）。题集构建口径：`testset-v1-seed.json` 头部注明「120 题完整集由 LLM 扩展 → 人工校验（**α≥0.7**）」——即**标注工作量集中在题集，而非数据集**。
- **清洗**：文档型需 OCR/版式校正；表格型需缺值/编码归一。**未跑清洗，不给清洗耗时估算**（`insufficient_data`）。

### 2.4 对接改动量小结（可执行的下一步）

1. **零改可直入的**：M2 的 41 篇**源指南 PDF**（文档型）——加 `registry.csv` 行即可进现有管线。
2. **需写适配脚本的**：M1/M5（转文本）、M2/M3（转 gold）。脚本产出**新增文件**，不改 `ingest_service.py` 契约。
3. **需契约变更评审的**：M1/F1/F2/N1 若走原样 CSV 入册 → 须扩 `DOC_TYPES` 白名单，**这一步停下报告**（属接线/契约改动，按红线不自行做）。
4. **申请制前置的**：M4/M6（及 M3/M5 的申请流程）——**审批不通过则整条路径不可用**，接入成本视审批结果而定（`insufficient_data`）。

---

## 三、评测方案（沿用项目既有口径）

### 3.1 评测设计（三问三答）

| 问题 | 沿用口径 | 对照设置 | 预期结论形态 |
|---|---|---|---|
| **Q1 · 域外检索基线**：本系统在**新行业域**语料上，纯检索准确率如何？ | **E1 基线**（A1_pure_rag，BM25，`eval_e1.run_question`，口径零改动） | 同一生成模型/prompt（mid 档）；新域题集 vs 现有糖尿病题集（`testset-v1-seed.json` 20 题） | 报 `pass^2`/`acc` + **Wilson 95% CI** + `n_judged`；零判定格 `insufficient_data` |
| **Q2 · 图增益与版本钉住**：接入图谱后，多跳/版本敏感题相比纯检索有无增益？ | **E2 消融**（A1/A2/A3/A4，`pipeline/ablation.py`）+ **E8** 版本钉住 on/off | 四级配置同题对照；pin-on vs pin-off 配对（**同 invocation**，坑 #62 纪律） | 报 Δ(pass^2) + 配对 `same_invocation=true`；无判别题时 Δ=0 **不得**解读为机制无效（坑 #63） |
| **Q3 · 规范对齐（换域后）**：本体/实体对齐在新域能否保持？ | **T-21 对齐**（**话题级为对外唯一口径**，`recall` + `precision_lower_bound`） | 话题级金标（**同单位比较**，坑 #66）；负向对照（域外话题命中应≈0） | 报 `recall`（分子分母同单位）+ `precision` 下界；**报 P/R 前先算结构性天花板**（坑 #66） |

### 3.2 判分与诚实纪律（全部沿用既有契约）

1. **判分链**：复用 T-18c 冻结的五元 judge 链（`eval_e1._judge_once`，judge 与生成模型**不同族**）。
2. **诚实分母**：`count_fail` 契约——非平台故障与端点失败**一律 pass=0 留在分母**，不静默排除（`experiment-reports.md` §2）。
3. **零判定格**：显式 `insufficient_data`，**不缩格、不消失**（同报告 §3.2 交叉表）。
4. **口径单位一致**：P/R 的分子分母**必须同单位**，否则先算结构性天花板（坑 #66）。
5. **不加新评测器**：新域题集走 `--testset` 加性参数，**评测器代码零改**（保证与现有数字可比）。

### 3.3 数据不足时的降级形态（预登记）

| 情形 | 处理 |
|---|---|
| 申请制数据集审批未通过（M3/M4/M5/M6） | 该项标 `insufficient_data`，仅用开放许可条目（M1/M2 源文档/M5 若可下/F1/F2/N1） |
| 结构化表格无法文本化或不允许扩契约 | 该项标 `insufficient_data` + 原因；**不得伪造"已评测"** |
| 新域语料题集未达 α≥0.7 校验 | 评测不启动，题集标 `insufficient_data` |

---

## 四、与官方评分句的对应 & 落地路径

- 官方**附加分②**原句：「真实脱敏行业数据集开展效果评测」（中国总决赛段，`official-site-2026-09-23.md` §三 L54）。
- 本文件 = **前期可行性**的三件套答复：§一=可得清单（10 条，逐条核实）、§二=接入成本（三形态分档 + 契约变更红线）、§三=评测方案（E1/E2/T-21 三口径零改）。
- **落地路径（供后续会话执行，非本轮任务）**：
  1. 先做**零成本路径**：M2 的 41 篇源指南 PDF（文档型）→ registry.csv 加行 → `ingest_assets` → 建新域题集 → 跑 E1/E2。**全程不改契约、不起新服务之外的动作**。
  2. 再评估**申请制路径**：M3/M5/M6（及 M4，注意其 DUA **禁止第三方 LLM 处理**，见下方红线）→ 提交申请 → 按审批结果决定是否入册。
  3. 表格型（M1/F1/F2/N1）**最后做**，且必须先完成契约变更评审（扩 `DOC_TYPES`）。

### 红线（本方案的合规底线）

1. **MIMIC-IV（M4）的 DUA 明确禁止**将凭据数据经第三方 API/在线 LLM 处理（PhysioNet 2025 公告原文「prohibits sharing access to the data with third parties, including sending it through APIs or using it on online platforms」）——**本项目管线走在线 LLM，故 M4 默认不可用于本评测**，如要用须改用**本地部署 LLM** 且经合规评审。
2. **非商业许可**（M2 CC BY-NC 4.0、M3 CC BY-NC 4.0/SA）**不得用于商业用途**；对外材料须注明许可与来源。
3. **表格型直入须先扩契约**（§2.2c），**不自行改** `DOC_TYPES`；需要时停下报告。
4. 凡未核实的条目（如 `www.data.gov.cn`）**不入册**；所有核实条目随附 URL + 访问日期 2026-09-24。

---

## 五、`insufficient_data` 清单（本文件所有"不知道"的诚实登记）

| # | 项 | 状态 |
|---|---|---|
| 1 | 任何数据集的**实测效果数字**（本轮未跑） | `insufficient_data` |
| 2 | 各数据集的**清洗耗时 / 标注人时**估算 | `insufficient_data`（未跑清洗） |
| 3 | 申请制数据集（M3/M4/M5/M6）**审批是否通过** | `insufficient_data`（取决于机构审核） |
| 4 | 国家政府数据统一开放平台（`www.data.gov.cn`）**可访问性与许可** | `insufficient_data`（抓取失败，未核实 → 不列条目） |
| 5 | M2 **许可两源不一致**（天池 CC BY-NC 4.0 vs OpenKG CC BY-SA 4.0）的**权威裁定** | `insufficient_data`（暂以天池页为准） |
| 6 | M6（MMC 队列）**语料化可行性**（临床队列表 → 文档/题集的映射路径） | `insufficient_data`（未评估） |
| 7 | 表格型数据集（M1/F1/F2/N1）**文本化后信息损失率** | `insufficient_data`（未实测） |

---

## 六、核实方法与可复现性

- **核实方式**：联网检索（WebSearch）+ 官方页面抓取（WebFetch），逐条比对**数据集页原文**的 License / DOI / 访问说明；访问日期统一 **2026-09-24**。
- **未核实即不写**：`www.data.gov.cn` 抓取失败后**未凭记忆补写**其许可或数据集名。
- **代码依据**：`registry.csv` 列定义与白名单取自 `backend/services/knowevo/ingest_service.py:38-51,70-92`；摄取/分块取自 `e1_retrieval.py:36`（CHUNK_SIZE=600）；评测器与 `--testset` 加性参数取自 `pipeline/eval_e1.py` / `pipeline/ablation.py` / `eval_v1`（坑 #63）。
- **本文件不改任何代码 / 契约 / 接线文件**；下一步动作（加 registry 行、写适配脚本、跑评测）**均留给后续授权会话**。
