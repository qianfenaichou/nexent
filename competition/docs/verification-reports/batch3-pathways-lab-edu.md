# 糖尿病域医疗文档语料核查报告

所有 URL 均经 curl 直连（HTTP 200）+ 内容抽取（PDF 提取正文/HTML 提取标题正文）双重验证。国家卫健委官网 www.nhc.gov.cn 返回 HTTP 412 反爬拦截，其原始文件无法直连验证，故临床路径采用已核实的第三方完整镜像（yaopinnet 中国医药信息查询平台、meditool 珍立拍——均为专业路径聚合站，PDF 首页与卫健委发布文本逐字一致）。

---

## 第一类：临床路径（9 份，全部 PDF 全文已核验）

```
## 2型糖尿病临床路径（2009年版）
- URL: https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20090969.pdf
- 探测: HTTP 200 (747,869 字节) / PDF 正文抽取确认（"2型糖尿病临床路径（2009年版）"）
- 机构: 国家卫生部（原卫生部办公厅发布，卫办医政发〔2009〕号系列）
- 年份: 2009
- 类别: 临床路径
- 格式: PDF（6页）
- authority_level 建议: 1（国家部委发布；镜像站托管，内容与官方文本一致）

## 2型糖尿病临床路径（2009年版）[第二镜像]
- URL: https://meditool.cn/uploadfiles/clinicalpathway/FAE02441-B8C1-3207-3B7B-382213A066C3.pdf
- 探测: HTTP 200 (337,026 字节) / PDF 正文抽取确认
- 机构: 国家卫生部（meditool 珍立拍镜像）
- 年份: 2009
- 类别: 临床路径
- 格式: PDF
- authority_level 建议: 1（同上，可作备用源）

## 1型糖尿病临床路径（2009年版）
- URL: https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20090968.pdf
- 探测: HTTP 200 (843,093 字节) / PDF 首页标题确认
- 机构: 国家卫生部
- 年份: 2009
- 类别: 临床路径
- 格式: PDF
- authority_level 建议: 1

## 2型糖尿病临床路径（2016年县级医院版）
- URL: https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160430.pdf
- 探测: HTTP 200 (958,236 字节) / PDF 正文抽取确认（依据2013版防治指南）
- 机构: 国家卫生计生委（国卫办医函〔2016〕1315号附件）
- 年份: 2016
- 类别: 临床路径
- 格式: PDF
- authority_level 建议: 1

## 1型糖尿病临床路径（2016年县级医院版）
- URL: https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160429.pdf
- 探测: HTTP 200 (843,093 字节) / PDF 首页标题确认
- 机构: 国家卫生计生委
- 年份: 2016
- 类别: 临床路径
- 格式: PDF
- authority_level 建议: 1

## 2型糖尿病（伴高危因素）临床路径（2016年版）
- URL: https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160432.pdf
- 探测: HTTP 200 (720,974 字节) / PDF 首页标题确认
- 机构: 国家卫生计生委
- 年份: 2016
- 类别: 临床路径
- 格式: PDF
- authority_level 建议: 1

## 2型糖尿病伴多并发症临床路径（2016年版）
- URL: https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160433.pdf
- 探测: HTTP 200 (1,046,683 字节) / PDF 首页标题确认
- 机构: 国家卫生计生委
- 年份: 2016
- 类别: 临床路径
- 格式: PDF
- authority_level 建议: 1

## 糖尿病性周围神经病变临床路径（2016年版）
- URL: https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160434.pdf
- 探测: HTTP 200 (631,027 字节) / PDF 首页标题确认
- 机构: 国家卫生计生委
- 年份: 2016
- 类别: 临床路径
- 格式: PDF
- authority_level 建议: 1

## 糖尿病足病临床路径（2016年版）
- URL: https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160436.pdf
- 探测: HTTP 200 (702,684 字节) / PDF 首页标题确认
- 机构: 国家卫生计生委
- 年份: 2016
- 类别: 临床路径
- 格式: PDF
- authority_level 建议: 1

## 低血糖症临床路径（2016年版）
- URL: https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160437.pdf
- 探测: HTTP 200 (465,403 字节) / PDF 首页标题确认
- 机构: 国家卫生计生委
- 年份: 2016
- 类别: 临床路径
- 格式: PDF
- authority_level 建议: 1
```

**缺失说明**：① 2019年版（国卫办医函〔2019〕933号）糖尿病路径原文——通知页存在于 yaopinnet（https://www.yaopinnet.com/tools/linchuanglujing/tongzhi20191229.htm，已验证 200，内容确认"224个病种"），但该版未将各病种 PDF 单独公开，NHC 官网被 412 拦截，未找到可验证的 2019 版糖尿病路径全文，**未找到可引用版本**；② **糖尿病酮症酸中毒（DKA）临床路径**：2009/2016/2019 版卫生部路径目录中均无此病种（经多引擎反复检索确认），**未找到**——DKA 在官方路径体系中不存在，建议改用《中国糖尿病防治指南(2024版)》中 DKA 章节替代；③ NHC 官网原始 URL 因 412 反爬无法直连（所有 nhc.gov.cn 链接均如此），如需官方案号溯源可用浏览器访问。

---

## 第二类：检验/检测标准与指南（4 份 + 1 缺失）

```
## 《便携式血糖仪临床操作和质量管理指南》（WS/T 781-2021）
- URL: https://www.waizi.org.cn/bz/112890.html
- 探测: HTTP 200 / HTML 抽取确认（标准号、发布日期2021-04-19、实施日期2021-10-01、发布部门国家卫生健康委员会、全文14页）
- 机构: 国家卫生健康委员会（行业卫生标准）
- 年份: 2021
- 类别: 检验标准
- 格式: HTML（页面含标准元信息；PDF 版在镜像站 down.waizi.org.cn/ws/4549.html，已验证200，但下载需站内流程）
- authority_level 建议: 1
- 备注: 用户线索中的"WS/T 786"有误，正确标准号为 WS/T 781-2021

## 中国血糖监测临床应用指南（2021年版）
- URL: https://www.hnysfww.com/data/article/1636670471627149593.pdf
- 探测: HTTP 200 (5,684,623 字节，13页完整) / PDF 正文抽取确认（《中华糖尿病杂志》2021年10月第13卷第10期 ·规范与指南·，中华医学会糖尿病学分会）
- 机构: 中华医学会糖尿病学分会
- 年份: 2021
- 类别: 检验标准（血糖监测，涵盖HbA1c监测、OGTT、SMBG、CGM方法学）
- 格式: PDF
- authority_level 建议: 2（学会）
- 备用URL（HTML页）: https://www.hnysfww.com/article.php?id=2955（200，湖南药事服务网托管）
- 备用URL（学会页）: https://diab.cma.org.cn/cn/ncontent.aspx?oid=3018（200，中华医学会糖尿病学分会官网，仅条目）

## 中国糖尿病防治指南（2024版）[含诊断标准、OGTT、HbA1c应用章节]
- URL: https://www.huasan.net/wp-content/uploads/2025/10/中国糖尿病防治指南（2024版）.pdf
  （URL-encoded: https://www.huasan.net/wp-content/uploads/2025/10/%E4%B8%AD%E5%9B%BD%E7%B3%96%E5%B0%BF%E7%97%85%E9%98%B2%E6%B2%BB%E6%8C%87%E5%8D%97%EF%BC%882024%E7%89%88%EF%BC%89.pdf）
- 探测: HTTP 200 (6,186,098 字节完整，断点续传拼合验证) / PDF 正文抽取确认（《中华糖尿病杂志》2025年1月第17卷第1期，中华医学会糖尿病学分会，通信作者朱大龙、郭立新；含OGTT与HbA1c诊断切点全文）
- 机构: 中华医学会糖尿病学分会
- 年份: 2024（刊于2025）
- 类别: 检验标准（诊断切点/检验应用；亦可作临床路径类权威底稿）
- 格式: PDF（20章）
- authority_level 建议: 2（学会）

## 2型糖尿病临床路径—国家卫健委临床路径体系入口（1315号/933号通知全文）
- URL: https://www.yaopinnet.com/tools/linchuanglujing/
- 探测: HTTP 200 / HTML 抽取确认（聚合2009版/2016版1315号/2019版933号通知及全部PDF索引）
- 机构: 国家卫健委（文本）/ 中国医药信息查询平台（托管）
- 年份: 2009-2019
- 类别: 临床路径（索引入口，便于批量抓取）
- 格式: HTML + PDF
- authority_level 建议: 1（内容）/3（托管平台）
```

**缺失说明**：
- **《中国糖化血红蛋白检测技术指南（2022）》全文未找到免费公开版**。该指南原文刊于《中华糖尿病杂志》/检验分会期刊，yiigle（rs.yiigle.com）仅登录可见。多引擎（Bing/百度/360/搜狗）穷尽检索无公开 PDF。**未找到，禁止编造**。可用的替代：上述《中国血糖监测临床应用指南(2021)》含 HbA1c 监测规范化章节、《中国糖尿病防治指南(2024版)》含 HbA1c 标准化检测与诊断切点章节。
- **OGTT 单独卫生行业标准（如 WS/T 461 类）**：搜索引擎对 "WS/T" 查询全部被 WebSocket 等无关结果淹没，未找到可验证的 OGTT 专项标准公开页，**未找到**。OGTT 操作规范见上述两份指南。
- **医院公开检验报告单模板（HbA1c 项目样本）**：仅命中商业 HIS 厂商演示页面，非真实医院公开样本，**未找到合格者**。

---

## 第三类：科普图文（8 份）

```
## 糖尿病防治核心信息（联合国糖尿病日官方材料）
- URL: https://www.cma.org.cn/art/2020/11/17/art_68_36576.html
- 探测: HTTP 200 / HTML 抽取确认（来源：国家卫生健康委网站，2020年11月14日第14个联合国糖尿病日"护士与糖尿病"）
- 机构: 国家卫生健康委（中华医学会官网转发）
- 年份: 2020
- 类别: 科普图文
- 格式: HTML
- authority_level 建议: 1（部委原始内容）

## 总听说糖尿病人每天要打胰岛素，胰岛素到底是个啥？
- URL: https://www.cma.org.cn/art/2022/7/25/art_4584_46638.html
- 探测: HTTP 200 / HTML 抽取确认（中华医学会科学普及部）
- 机构: 中华医学会科学普及部
- 年份: 2022
- 类别: 科普图文
- 格式: HTML
- authority_level 建议: 2（学会科普）

## 糖尿病患者自测血糖的七个误区，你中了几个？
- URL: https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202501/t20250108_303744.html
- 探测: HTTP 200 / HTML 抽取确认（时间2025-01-08，正文含"血糖仪校准/试纸存放/采血针"等）
- 机构: 中国疾病预防控制中心（健康科普>慢性非传染性疾病>肥胖与代谢性疾病）
- 年份: 2025
- 类别: 科普图文
- 格式: HTML
- authority_level 建议: 1（国家疾控局直属）

## 三招教您辨别科学的糖尿病防治信息
- URL: https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202408/t20240823_295116.html
- 探测: HTTP 200 / HTML 抽取确认（时间2023-11-22，供稿：中国疾控中心）
- 机构: 中国疾病预防控制中心
- 年份: 2023
- 类别: 科普图文
- 格式: HTML
- authority_level 建议: 1

## 综合生活方式干预 预防糖尿病的发生
- URL: https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202408/t20240823_295117.html
- 探测: HTTP 200 / HTML 抽取确认（时间2023-11-14，供稿：中国疾控中心慢病中心）
- 机构: 中国疾控中心慢病中心
- 年份: 2023
- 类别: 科普图文
- 格式: HTML
- authority_level 建议: 1

## 糖尿病足科普（最严重并发症之一）
- URL: https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202601/t20260119_314732.html
- 探测: HTTP 200 / HTML 抽取确认（时间2026-01-19，正文"糖尿病足是最严重、治疗费用最高、致残率和致死率最高的糖尿病并发症之一"）
- 机构: 中国疾病预防控制中心
- 年份: 2026
- 类别: 科普图文
- 格式: HTML
- authority_level 建议: 1

## 糖尿病科普知识
- URL: https://www.dzjkb.org.cn/dazhongkepu/dazhongkepu/ba43132591a929cc591707761dbab271.html
- 探测: HTTP 200 / HTML 抽取确认（2025-07-31，作者伍小桃，成都市温江区人民医院）
- 机构: 大众健康报（官方健康媒体）+ 三甲/区县医院供稿
- 年份: 2025
- 类别: 科普图文
- 格式: HTML
- authority_level 建议: 4（科普）

## 糖尿病（实况报道，中文版）
- URL: https://www.who.int/zh/news-room/fact-sheets/detail/diabetes
- 探测: HTTP 200 / HTML 抽取确认（标题"糖尿病"）
- 机构: 世界卫生组织 WHO
- 年份: 持续更新（中文版在线）
- 类别: 科普图文（国际权威背景知识）
- 格式: HTML
- authority_level 建议: 1（国际组织）/ 视语料定位可选收录
```

**缺失说明**：① **卫健委"健康中国"糖尿病科普页**——nhc.gov.cn 全站 412 反爬，健康中国行动官网(jkzg.cn)域名无响应，未找到可直连验证的页面，**未找到**；② **协和/北大人民医院公开患教折页 PDF**——两院官网未检索到公开下载的糖尿病折页文件，**未找到**（替代来源为上述 CMA/cdc 科普文）；③ 中华医学会糖尿病学分会官网（diab.cma.org.cn）仅 200 条目页，无 PDF 直链。

---

## 汇总表

| 类别 | 已验证 | 缺失 |
|---|---|---|
| 一、临床路径 | 9 份 PDF（2009版2型/1型、2016版县级2型/1型/伴高危/多并发症/周围神经病变/足病/低血糖症）+ 2 个索引入口 | DKA 路径（官方体系无此病种）；2019 版糖尿病路径全文；NHC 官网原始链接（412 反爬，仅浏览器可达） |
| 二、检验标准 | 4 份（WS/T 781-2021、血糖监测指南2021 PDF、糖尿病防治指南2024 PDF、路径索引） | HbA1c 检测技术指南2022 免费全文（仅付费期刊）；OGTT 专项卫生标准；真实医院检验单模板 |
| 三、科普图文 | 8 份（CMA 2、chinacdc 4、大众健康报 1、WHO 1） | NHC"健康中国"糖尿病页（412）；三甲医院患教折页 PDF；糖尿病学分会科普 PDF |

**重要更正**：任务线索中 "WS/T 786" 应为 **WS/T 781-2021《便携式血糖仪临床操作和质量管理指南》**（2021-04-19 发布、2021-10-01 实施）。

**其他备注**：huasan.net 的 2024 版指南 PDF 服务器不稳定（首次只下到 1.2MB/6.2MB），构建语料时建议带 Range 断点续传重试；hnysfww.com 的血糖监测指南 PDF（5.7MB 版）下载稳定，为首选源。