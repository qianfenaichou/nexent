# 语料溯源核查报告存档（T-02 答辩合规证据链）

> 三批核查代理的原始报告于 2026-09-14 会话产出，2026-09-15 从代理运行目录抢救归档至此。
> 合并门抽查记录见文末。全部 URL 可复验。

## 三批报告清单

| 批次 | 文件 | 代理任务 | 覆盖 |
|------|------|----------|------|
| 1 | `batch1-drug-labels.md` | 核实药品说明书源（降糖药 9 大类） | 28 份说明书 URL，curl 直连 + 章节完整性双验（CDE 官方 PDF/fh21 反爬不可用已排除） |
| 2 | `batch2-guidelines.md` | 核实中国糖尿病指南源 | 11 份指南 PDF（hnysfww 期刊排印版），curl 完整下载 + pdftotext 抽取比对标题/机构/年份 |
| 3 | `batch3-pathways-lab-edu.md` | 核实临床路径/检验/科普源 | 路径 9 份（NHC 官网 412 反爬，采用已核对逐字一致的专业镜像）+ 检验 2 + 科普 8 |

报告中的不可用源清单（1 型糖尿病指南 2021 付费墙、DKA 临床路径无官方体系、HbA1c 检测技术指南无免费公开版、NHC 412）即 registry 58 份 < 60 下界的缺口依据——宁缺毋滥。

## 合并门抽查记录（2026-09-15，合并 develop 前执行）

**方法**：分层抽样 13 个 URL（doc_type × split 全覆盖 + 2020/2024 血缘锚 + 3 个厂家官方 PDF），curl 直连复验 HTTP 状态码 + content-type + 字节数；锚点 PDF 下载后 pdftotext 验证首页标题；HTML 源验证页面标题/正文关键词。

**结果**：13/13 HTTP 200，content-type 与登记一致，PDF 字节数与代理报告记录吻合（guide-2020 4,336,867B / guide-2024 7,077,618B / 亚莫利 439,780B 等）。

| asset_no | 类型 | split | 结果 |
|---------|------|-------|------|
| guide-2020 | guideline | build | 200 PDF，首页"中华糖尿病杂志 2021;13(4)"与 license_note 一致 |
| guide-2024 | guideline | build | 200 PDF 7MB |
| drug-glimep | drug_label | blind | 200 PDF（赛诺菲官网） |
| drug-dapa-fld | drug_label | blind | 200 PDF（豪森官网） |
| drug-dula | drug_label | build | 200 PDF（礼来医学官网） |
| guide-elderly-2021 | guideline | build | 200 PDF |
| guide-foot-path-2023 | guideline | blind | 200 PDF |
| cp-t2dm-county-2016 | policy | build | 200 PDF |
| cp-t2dm-2009 | policy | blind | 200 PDF |
| std-glucometer-ws781 | lab_report | build | 200 HTML，标题含 WS/T781-2021 |
| edu-bgm-myths-2025 | edu_graphic | build | 200 HTML（中国疾控中心） |
| edu-foot-2026 | edu_graphic | blind | 200 HTML，正文含"糖尿病足"主题内容 |
| drug-sita | drug_label | blind | 200 HTML，标题"磷酸西格列汀片(捷诺维)详细说明书" |

**全量本地校验**：58 行 license_note 非空、source_url 格式合法、authority_level 与 doc_type 分级一致（1国标/2指南/3说明书/4科普），零问题。

**结论**：合并门通过。抽查人：T-02 合并前审计（Claude 会话），抽查种子 20260915 可复现。
