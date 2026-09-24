# 文献四要素核验报告

> 核验日期：2026-09-23
> 核验人：general-purpose-8
> 方法：对每篇用 WebFetch 实抓权威页面（arXiv abs/HTML、CEUR-WS、PubMed/PMC、Sage/语义网期刊），逐字比对四要素并核对关键数字；未被 NCBI 直接抓取的文献用 PubMed/PMC 镜像与其全文摘要页补全。
> 重要说明：**本次 4 篇与项目前科的 `arXiv:2505.23319`(实为数学物理论文) 不同 —— 4 个编号均正确对应所声称的论文，无张冠李戴。** 但发现若干著录细节（标题前缀、期刊全称、年份）需修正。

---

## 1. 逐篇核验结果

### 文献 1: Gao et al. 2019, "Efficient Knowledge Graph Accuracy Evaluation"
- 实抓 URL: https://arxiv.org/abs/1907.09657 （HTML 全文 https://arxiv.org/html/1907.09657v1）
- 四要素比对:
  - 标题 ✅ 与声称一致（逐字："Efficient Knowledge Graph Accuracy Evaluation"）
  - 作者 ✅ 第一作者 Junyang Gao（另含 Xian Li, Yifan Ethan Xu, Bunyamin Sisman, Xin Luna Dong, Jun Yang）
  - 年份 ✅ 2019（v1 submitted 23 Jul 2019，VLDB 2019）
  - 编号 ✅ arXiv:1907.09657（即所声称编号，非误引）
- 关键数字核验:
  - 成本函数 `Cost(G′)=|E′|·c1+|G′|·c2` ✅ 实抓第 3.1/3.2 节 Definition 3 原文。
  - `c1 = 45 (second)` ✅ 第 7.1.3 节原文："compute the best parameter settings as c1=45(second) and c2=25(second)"。
  - `c2 = 25 (second)` ✅ 同上。
  - c1=实体识别（原文术语 **Entity Identification**）、c2=关系校验（原文 **Relationship Validation**）✅ 与声称语义一致（用词为 identification/validation，非 recognition）。
  - MOVIE KG（IMDb + Wikidata，>200 万 triples）✅ 第 7.1.1、7.1.3 节多次出现，数字即在该 KG 上拟合。
- 判定: ✅通过（四要素全对 + 数字全部可核）

### 文献 2: Paulheim 2018, "Estimating the Cost of Knowledge Graph Creation"
- 实抓 URL: http://ceur-ws.org/Vol-2180/ （目录确认）；全文 PDF http://www.heikopaulheim.com/docs/iswc_bluesky_cost2018.pdf
- 四要素比对:
  - 标题 ⚠️→✅ 实际完整标题为 **"How much is a Triple? Estimating the Cost of Knowledge Graph Creation"**。声称只写了副标题部分"Estimating the Cost of Knowledge Graph Creation"，缺前缀"How much is a Triple?"，但确为同一篇（CEUR-WS Vol-2180 目录逐字列出）。建议预登记补全主标题。
  - 作者 ✅ Heiko Paulheim
  - 年份 ✅ 2018（ISWC 2018 Blue Sky Ideas Track）
  - 编号 ✅ CEUR-WS Vol-2180
- 关键数字核验:
  - 人工策展 $2–$6/triple ✅ 摘要与正文："the cost of manually curating a triple is between $2 and $6"。
  - Cyc 9.5 min/assertion ✅ 正文："1,000 person years boils down to 9.5 minutes per assertion"（基于 $120M/21M assertions 推算）。
  - Freebase 18.7 min/sentence ✅ 正文："18.7 minutes per sentence"（基于 Wikipedia 41M 工时 / 3.6M 页 / 36.4 句推算，再折算 $2.25/句）。
- 判定: ✅通过（四要素对；标题仅缺前缀已注明；数字全部可核。注：Cyc/Freebase 数字为基于工时/工资的推导值，非直接标注计时，但确为该文给出。）

### 文献 3: uComp — Protégé 众包本体工程
- 实抓 URL: https://journals.sagepub.com/doi/full/10.3233/SW-150181 （开放全文）；另 https://www.semantic-web-journal.net/system/files/swj894.pdf
- 四要素比对:
  - 标题 ✅ "Crowd-based ontology engineering with the uComp Protégé plugin"（与声称一致）
  - 作者 ✅ Gerhard Wohlgenannt, Marta Sabou, Florian Hanika
  - 年份 ⚠️ 声称未给年份；实际为 **2016**（Semantic Web 期刊 vol. 7(4): 379–398，2016；线上 2015）
  - 编号 ✅ DOI 10.3233/SW-150181（即所声称 SW-150181）
  - 期刊注记：声称写"Sage Journals"，实际期刊为 **Semantic Web**（由 IOS Press 出版，DOI 前缀 10.3233 为 IOS Press）。建议预登记改写期刊全名，避免误归为 Sage。
- 关键数字核验:
  - 领域相关性校验 3.65 concepts/min ✅ 第 6 节："for our estimation we consider an average speed of 3.65 concept verifications per minute"（由 climate 3.68、finance 3.61 取均值）。
  - subsumption 1.6 relations/min ✅ 第 6 节："an average subsumption verification speed of 1.6 relations/minute"。
  - climate 27.4 min/101 concepts ✅ Table 4 + 正文："27.4 minutes ... to judge the domain relevance for 101 concepts"。
  - financial 21.3 min/77 concepts ✅ Table 4 + 正文："21.3 minutes were needed on average to verify 77 concepts"。
- 判定: ✅通过（四要素对；年份/期刊著录细节需补正，已注明；数字全部可核）

### 文献 4: 心衰 KG（LLM + 提示工程）
- 实抓 URL: https://pmc.ncbi.nlm.nih.gov/articles/PMC11250484/ （NCBI 主站被临时拦截，改用 PMC 镜像全文页 + PubMed 摘要页 https://pubmed.ncbi.nlm.nih.gov/39015745/ 双重确认）；期刊信息 https://www.peeref.com/works/82961057
- 四要素比对:
  - 标题 ✅ "Knowledge graph construction for heart failure using large language models with prompt engineering"（与声称一致）
  - 作者 ✅ 第一作者 Tianhan Xu（另 Yixun Gu, Mantian Xue, Renjie Gu, Bin Li, Xiang Gu）
  - 年份 ✅ 2024（DOI 10.3389/fncom.2024.1389475；PMID 39015745；PMCID PMC11250484）
  - 编号 ✅ PMC11250484（即所声称）；期刊全称 **Frontiers in Computational Neuroscience**（声称只写"Frontiers"，建议补全）
- 关键数字核验（第 4.6 节 Efficiency evaluation，287 个 500–700 词文本块）:
  - 人工标注 63.3 min/文本块 ✅ 原文："Manual Annotation has the highest time cost with an average time of 63.3 min per text chunk"。
  - LLM+精修 32.1 min/块 ✅ 原文："The time cost for refinements after manual annotation and LLM annotation is 30.7 and 32.1 min per text chunk, respectively"（LLM 标注+精修合计 32.1；另 LLM 单块标注约 1 min 忽略）。
  - 专家精修 30.7 min/块 ✅ 同上（人工标注后专家精修 30.7 min）。
  - 文本块 500–700 词 ✅ 原文明确。
- 判定: ✅通过（四要素全对 + 数字全部可核；仅期刊名建议补全为 Frontiers in Computational Neuroscience）

---

## 2. 零命中检索记录

检索日期：2026-09-23 ｜ 检索工具：通用 web（WebSearch 接口）
说明：本应支持"提案级（per-proposal）审核耗时无文献先例"的论断。诚实记录如下——**并非三个检索式都干净零命中**。

| 检索式 | 库 | 日期 | 命中数 | 相关命中 |
|---|---|---|---|---|
| `ontology proposal human review minutes` | 通用 web | 2026-09-23 | 5 | **有**（OBO Foundry SOP：新本体注册的人工评审"relatively quick (~ 2 hours)"；为全本体注册评审，非原子级变更提案）|
| `KG curation time per proposal` | 通用 web | 2026-09-23 | 5 | 无直接相关命中（最贴近：SciKGDash 15 人实验"4/5 curation tasks in under 5 min"，属科研 KG 策展任务计时，非 per-proposal；另 KG building 指南给 MVP 2–3 个月，属项目级）|
| `ontology change proposal review time` | 通用 web | 2026-09-23 | 5 | 无直接命中（OBO NOR Manager 称流程"weeks or months"为总周期；OBO SOP 仍给 ~2 hours 为全本体注册评审；Palantir 描述 proposal 流程但无计时）|

**对论断的影响（诚实结论）：**
- "原子级（单条实体/关系变更）提案的审核耗时"在检索中**确无直接文献给出分钟级数字**——Q2、Q3 支持这一子论断。
- 但 Q1 出现**相关先例**：OBO Foundry 对新本体注册的手动评审量级为 **~2 小时/本体**（非 per-proposal 分钟级）。这与"完全无先例"的强论断相抵触，应降级为"**无原子级提案的分钟级耗时文献，但存在更粗粒度（全本体注册）的 ~2 小时级先例**"。建议在预登记中据此弱化表述，不得写"完全无文献先例"。

---

## 3. 核验后仍成立的系数区间

> α = 分钟/新实体；β = 分钟/新关系。仅列经实抓核验仍成立的来源。

### α（分钟 / 新实体）
| 取值 | 来源 | 性质 |
|---|---|---|
| 0.75 min（=45 s）| 文献 1（MOVIE KG，c1=实体识别）| **实测**（在该 KG 上拟合）|
| 0.27 min（=1/3.65）| 文献 3（uComp，领域相关性校验）| **实测**（已抽取概念验证速率）|

→ 仍成立区间：**α ≈ 0.27–0.75 min/实体**（实测）。注意文献 3 为"校验已抽取概念"，文献 1 为"从零标注"，前者更省时。

### β（分钟 / 新关系）
| 取值 | 来源 | 性质 |
|---|---|---|
| 0.42 min（=25 s）| 文献 1（MOVIE KG，c2=关系校验）| **实测** |
| 0.63 min（=1/1.6）| 文献 3（uComp，subsumption 校验）| **实测** |
| 9.5–18.7 min/assertion | 文献 2（Cyc / Freebase）| **推断**（由工时/工资反推，为完整人工策展量级，远高于校验量级）|

→ 仍成立区间：
- 校验级（实测）：**β ≈ 0.42–0.63 min/关系**
- 完整策展级（推断）：**β ≈ 9.5–18.7 min/断言**（文献 2，作为上界参考，非直接标注计时）
- 文献 4 仅给"文本块级"耗时（人工 63.3 / LLM+精修 32.1 / 专家精修 30.7 min per 500–700 词块），无法直接折算 per-关系，作量级参考。

**建议预登记采用**：α=0.3–0.75 min/实体（实测，文献 1、3）；β=0.4–0.6 min/关系（实测校验级，文献 1、3），并以文献 2 的 9.5–18.7 min 作为"完整人工策展"情景的上界推断。

---

## 4. 必须从预登记中删除的条目（❌ 列表 + 原因）

**无 ❌ 条目。** 四篇文献四要素与关键数字均通过实抓核验，编号无张冠李戴（与项目前科 `arXiv:2505.23319` 误引不同）。**但需修正以下著录细节（非删除，是补正准确性）**：

1. 文献 2：标题补全为 **"How much is a Triple? Estimating the Cost of Knowledge Graph Creation"**（现仅写副标题）。
2. 文献 3：期刊改 **Semantic Web (IOS Press)**，非"Sage Journals"；补年份 **2016**。
3. 文献 4：期刊补全为 **Frontiers in Computational Neuroscience**（现仅写"Frontiers"）；可补 DOI 10.3389/fncom.2024.1389475。
4. 文献 1：术语建议用原文 **Entity Identification / Relationship Validation**（声称写"实体识别/关系校验"语义一致，但"识别"对应 identification 更准）。
5. "提案级审核耗时无文献先例"的强论断需弱化（见第 2 节 Q1 相关先例 OBO ~2 小时/本体注册）。

---

## 5. 未完成项（网络失败等）

- 文献 4 的 NCBI 主站（www.ncbi.nlm.nih.gov）被临时拦截（abuse block），已用 PMC 镜像全文页 + PubMed 摘要页 + Peeref 三重来源补全，四要素与数字均确认，不受影响。
- 未单独跑 Google Scholar / arXiv 专属库检索（仅用通用 web）。如需更强"可复核"证据，可补跑 `ontology change proposal review time site:scholar.google.com` 等；当前通用 web 检索已足以支撑第 2 节结论。
- 文献 2 的 Cyc/Freebase 数字为推导值，未能取得其原始出处（Lenat 2017 演讲、Geiger & Halfaker 2013）逐字复核，但确为该文引用并原文给出，标注为"推断"已在第 3 节区分。
