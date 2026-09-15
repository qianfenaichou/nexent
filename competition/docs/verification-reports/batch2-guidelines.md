# 中国2型糖尿病防治指南相关文档 URL 核查报告

核查方法说明：所有 URL 均用 curl 实际访问（HTTP 头 + 完整下载 + pdftotext 抽取正文比对标题/机构/年份）。百度搜索中途触发风控、Bing 返回缓存通用结果、搜狗/360 PC 版被限流，最终以 360 移动版（m.so.com）和定向站点探测完成核查。全部 11 个 PDF 均已完整下载到本地（/tmp/tnb_*.pdf）并验证页数与正文内容，非仅 HTTP 探测。

主要权威转载源：**湖南药事服务网（hnysfww.com）**——湖南省药学会主办的药事专业平台，其"指南•规范•共识"栏目收录的 PDF 均为中华医学会系列杂志排印版原文（PDF 内含期刊页眉、卷期、DOI 信息），内容与官方期刊发表版一致。

---

## 1. 中国2型糖尿病防治指南（2020年版）
- 最佳URL: https://www.hnysfww.com/data/article/1619222032934605040.pdf
- 文库页: https://www.hnysfww.com/article.php?id=2633
- 探测结果: HTTP 200, content-type: application/pdf, 4,336,867 字节
- 机构/作者: 中华医学会糖尿病学分会（CDS）；通信作者朱大龙（南京鼓楼医院）
- 年份: 2021年4月发表于《中华糖尿病杂志》第13卷第4期（指南版本为2020年版）
- 格式: PDF，95页（已下载验证，正文首页确认为"中国2型糖尿病防治指南（2020年版）"）
- 备注: authority_level=2（学会指南）。本指南由《中华糖尿病杂志》和《中华内分泌代谢杂志》2021年4月同步发表；中华医学会期刊库（yiigle）需登录，此为公开可下载的杂志排印版原文。

## 2. 中国糖尿病防治指南（2024版）
- 最佳URL: https://www.hnysfww.com/data/article/1735800276657362700.pdf
- 文库页: https://www.hnysfww.com/article.php?id=4137
- 探测结果: HTTP 200, content-type: application/pdf, 17,069,600 字节（首下载曾截断，续传后完整校验）
- 机构/作者: 中华医学会糖尿病学分会（CDS）；通信作者朱大龙、郭立新
- 年份: 2025年1月发表于《中华糖尿病杂志》第17卷第1期（指南版本为2024版）
- 格式: PDF，124页
- 备注: authority_level=2。**重要更正：2024版官方正式标题为《中国糖尿病防治指南（2024版）》，不再含"2型"二字**，内容涵盖1/2型及特殊类型糖尿病、妊娠糖尿病等共20章。请勿按"中国2型糖尿病防治指南2024"命名。

## 3. 中国1型糖尿病诊治指南（2021版）
- 最佳URL（摘要页）: https://drugs.dxy.cn/pc/clinicalGuidelines/pzEQXdT7vhq6M5KQHxFqZdw
- 探测结果: HTTP 200, content-type: text/html（丁香园临床指南库，可见完整"概述"，全文需会员）
- 机构/作者: 中华医学会糖尿病学分会、中国医师协会内分泌代谢科医师分会、中华医学会内分泌学分会、中华医学会儿科学分会
- 年份: 2021版；正式期刊发表：中华糖尿病杂志 2022年第14卷第11期（发布日期2022-11-20）
- 格式: 官方全文仅 HTML（登录墙后）；无公开 PDF
- 备查权威链接（均已验证可访问）:
  - 官方期刊原文页（401登录墙）: https://rs.yiigle.com/CN115791202211/1433533.htm
  - 发布新闻（中南大学糖尿病免疫学教育部重点实验室，HTTP 200）: https://dmkeylab.csu.edu.cn/info/1016/1079.htm
  - 发布新闻（人民网湖南，HTTP 200）: http://hn.people.com.cn/n2/2022/0711/c371273-40033368.html
  - 全文预览（道客巴巴，非官方，HTTP 200，67页）: https://www.doc88.com/p-58261372960430.html
- 备注: authority_level=2。**未找到任何公开可下载的官方 PDF**——此指南在中华医学会期刊库为付费全文。上述 doc88/book118 为文库预览站，权威性低（仅作参考）。医脉通指南库（guide.medlive.cn）搜索与详情页均强制登录（302 跳转登录页），无法匿名访问。

## 4/5. 中国糖尿病医学营养治疗指南（2022版）
- 最佳URL: https://www.hnysfww.com/data/article/1664407059367528280.pdf
- 文库页: https://www.hnysfww.com/article.php?id=3363
- 探测结果: HTTP 200, content-type: application/pdf, 7,101,004 字节
- 机构/作者: 中国医疗保健国际交流促进会营养与代谢管理分会、中国营养学会临床营养分会、中华医学会糖尿病学分会、中华医学会肠外肠内营养学分会、中国医师协会营养医师专业委员会（通信作者陈伟，北京协和医院）
- 年份: 2022年9月发表于《中华糖尿病杂志》第14卷第9期
- 格式: PDF，53页
- 备注: authority_level=2（五学会联合指南）。这是该指南的最新正式版本；未发现 2022 之后的更新版（2024版《中国糖尿病防治指南》内含医学营养治疗章节，见条目2）。

## 6. 国家基层糖尿病防治管理指南（2022）
- 最佳URL: https://www.hnysfww.com/data/article/1647557424677414104.pdf
- 文库页: https://www.hnysfww.com/article.php?id=3132
- 探测结果: HTTP 200, content-type: application/pdf, 2,385,720 字节
- 机构/作者: 中华医学会糖尿病学分会、国家基层糖尿病防治管理办公室（在国家卫健委基层卫生健康司、中华医学会指导下制定；通信作者贾伟平，上海六院）
- 年份: 2022年3月发表于《中华内科杂志》第61卷第3期
- 格式: PDF，14页
- 配套手册（同已验证）: 《国家基层糖尿病防治管理手册（2022）》PDF，32页，https://www.hnysfww.com/data/article/1658193574047096075.pdf（文库页 article.php?id=3293，《中华内科杂志》2022年7月第61卷第7期）
- 备注: authority_level 介于1–2 之间（卫健委基层司指导、办公室组织制定的国家级基层管理指南）。国家卫健委官网（nhc.gov.cn）启用了 JS 动态反爬（HTTP 412 + 加密 cookie 脚本），curl/WebFetch 均无法访问其原始发布页。

## 7. 中国老年糖尿病诊疗指南（2024版）
- 最佳URL: https://www.hnysfww.com/data/article/1708887770197275603.pdf
- 文库页: https://www.hnysfww.com/article.php?id=3903
- 探测结果: HTTP 200, content-type: application/pdf, 5,706,869 字节
- 机构/作者: 国家老年医学中心、中华医学会老年医学分会、中国老年保健协会糖尿病专业委员会（通信作者郭立新、肖新华）
- 年份: 2024年2月发表于《中华糖尿病杂志》第16卷第2期
- 格式: PDF，43页
- 备注: authority_level=2。

## 7b. 中国老年糖尿病诊疗指南（2021年版）
- 最佳URL: https://www.hnysfww.com/data/article/1612462771125575707.pdf
- 文库页: https://www.hnysfww.com/article.php?id=2523
- 探测结果: HTTP 200, content-type: application/pdf, 1,808,909 字节
- 机构/作者: 同上三机构（通信作者郭立新、肖新华）
- 年份: 2021年1月发表于《中华糖尿病杂志》第13卷第1期
- 格式: PDF，33页
- 备注: authority_level=2。注意另存在《中国老年2型糖尿病防治临床指南（2022年版）》（文库条目 id=3094，未逐字验证PDF），与本病指南不是同一文件。

## 8a. 中国糖尿病足防治指南（2019版）
- 最佳URL（第Ⅰ部分）: https://www.hnysfww.com/data/article/1560731720094487230.pdf（17页）
- 最佳URL（第Ⅱ部分）: https://www.hnysfww.com/data/article/1560731777831482671.pdf（29页）
- 配套解读: https://www.hnysfww.com/data/article/1578807729280543391.pdf（4页，薛耀明、邹梦晨，解读）
- 文库页: https://www.hnysfww.com/article.php?id=1265
- 探测结果: 均 HTTP 200, content-type: application/pdf（1.08MB / 1.20MB / 0.89MB）
- 机构/作者: 中华医学会糖尿病学分会、中华医学会感染病学分会、中华医学会组织修复与再生分会
- 年份: 2019年2月（第11卷第2期）与2019年3月（第11卷第3期）分两部分发表于《中华糖尿病杂志》
- 格式: PDF（Ⅰ册17页 + Ⅱ册29页 + 解读4页，均已下载验证）
- 备注: authority_level=2。

## 8b. 中国糖尿病足诊治指南（2024，最新）
- 最佳URL: https://www.hnysfww.com/data/article/1732082536099677072.pdf
- 文库页: https://www.hnysfww.com/article.php?id=4106
- 探测结果: HTTP 200, content-type: application/pdf, 1,409,797 字节
- 机构/作者: 中国医疗保健国际交流促进会外周血管医学分会、首都医科大学下肢动脉硬化闭塞症临床诊疗与研究中心、北京华炎血管疾病诊疗产业技术创新战略联盟
- 年份: 2024年11月发表于《中国临床医生杂志》第52卷第11期（doi:10.3969/j.issn.2095-8552.2024.11.007）
- 格式: PDF，10页
- 备注: authority_level=2。另见配套《中国糖尿病足诊治临床路径(2023版)》PDF 10页：https://www.hnysfww.com/data/article/1678146402772954828.pdf（《中华内分泌代谢杂志》2023年2月第39卷第2期，CDS糖尿病足与周围血管病学组）。

---

# 汇总表

| # | 文档 | 结果 | 最佳URL | 格式 | 状态 |
|---|------|------|---------|------|------|
| 1 | 中国2型糖尿病防治指南（2020年版） | 找到 | hnysfww.com/data/article/1619222032934605040.pdf | PDF 95页 | HTTP 200 已下载验证 |
| 2 | 中国糖尿病防治指南（2024版）* | 找到 | hnysfww.com/data/article/1735800276657362700.pdf | PDF 124页 | HTTP 200 已下载验证 |
| 3 | 中国1型糖尿病诊治指南（2021版） | 仅HTML | drugs.dxy.cn/pc/clinicalGuidelines/pzEQXdT7vhq6M5KQHxFqZdw | HTML摘要 | 官方PDF付费墙内 |
| 4/5 | 中国糖尿病医学营养治疗指南（2022版） | 找到 | hnysfww.com/data/article/1664407059367528280.pdf | PDF 53页 | HTTP 200 已下载验证 |
| 6 | 国家基层糖尿病防治管理指南（2022） | 找到 | hnysfww.com/data/article/1647557424677414104.pdf | PDF 14页 | HTTP 200 已下载验证 |
| 6b | 国家基层糖尿病防治管理手册（2022） | 找到 | hnysfww.com/data/article/1658193574047096075.pdf | PDF 32页 | HTTP 200 已下载验证 |
| 7 | 中国老年糖尿病诊疗指南（2024版） | 找到 | hnysfww.com/data/article/1708887770197275603.pdf | PDF 43页 | HTTP 200 已下载验证 |
| 7b | 中国老年糖尿病诊疗指南（2021年版） | 找到 | hnysfww.com/data/article/1612462771125575707.pdf | PDF 33页 | HTTP 200 已下载验证 |
| 8a | 中国糖尿病足防治指南（2019版） | 找到 | 两部分PDF（见上文8a） | PDF 17+29页 | HTTP 200 已下载验证 |
| 8b | 中国糖尿病足诊治指南（2024） | 找到 | hnysfww.com/data/article/1732082536099677072.pdf | PDF 10页 | HTTP 200 已下载验证 |

\* 2024版官方正式标题不含"2型"。

**统计**：8类目标文档中 7 类找到真实可下载 PDF（共11个PDF文件，全部 HTTP 200 + application/pdf + 完整下载验证）；1 类（1型糖尿病2021指南）无公开 PDF，仅能给出 HTML 摘要页与权威发布新闻链。

**重要核查发现**：
1. 用户提示中的候选来源均不可用：`guidelines.diabetes.ac.cn` 不存在（DNS解析失败）；医脉通指南库（guide.medlive.cn）搜索和详情页全部强制登录（302到登录页）；中华医学会期刊库（rs.yiigle.com）全文需401授权；万方/维普为JS单页应用+登录墙；nhc.gov.cn 有动态反爬（HTTP 412）。
2. 实际最佳公开源为湖南药事服务网（湖南省药学会主办），其指南PDF为期刊排印版原文（含页眉卷期与DOI），可信度高；但该站服务器对大文件有中途断流现象，下载需用断点续传（-C -）。
3. 所有列出的 URL 均为本次会话实际访问验证过的真实地址，无编造。