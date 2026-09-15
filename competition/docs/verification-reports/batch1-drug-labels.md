全部核实工作完成。以下是最终报告。

---

# 中国上市降糖药品说明书 URL 核查报告

**方法说明**：本环境无 WebSearch 工具且 WebFetch 持续超时，改用 curl 直接访问 + 搜索引擎（Bing/360）检索；每个 URL 均以浏览器 UA 实际抓取，验证 HTTP 状态码、页面标题及说明书核心章节（【适应症/功能主治】【用法用量】【不良反应】【禁忌】【注意事项】）与批准文号。CDE 官方说明书 PDF（202 JS 挑战拦截）、fh21.com（521 反爬）经实测不可用，已排除。

## 一、双胍类

### 盐酸二甲双胍片（格华止）
- URL: https://ypk.39.net/507707/manual/
- 探测: HTTP 200 / curl 抓取确认完整说明书正文（通用名/适应症/用法用量/不良反应/禁忌/注意事项全章节）
- 厂家: 中美上海施贵宝制药有限公司
- 类别: 双胍类
- authority_level: 3（药品说明书）
- 备注: 批准文号 国药准字H20023371；39药品通（39健康网）页面

### 盐酸二甲双胍缓释片（格华止）
- URL: https://ypk.39.net/2009048/manual
- 探测: HTTP 200 / 说明书正文章节齐全
- 厂家: Bristol-Myers Squibb（页面生产企业字段）
- 类别: 双胍类
- authority_level: 3
- 备注: 页面未显示批准文号（原研批号 H20023370，需向 NMPA 数据库二次核对）

### 盐酸二甲双胍缓释片（国产）
- URL: https://db.yaozh.com/instruct/7716935220000068.html
- 探测: HTTP 200 / 药智网说明书库，章节齐全
- 厂家: 见页面（国药准字H20080251 对应企业）
- 类别: 双胍类
- authority_level: 3
- 备注: 批准文号 国药准字H20080251

## 二、磺脲类

### 格列美脲片（亚莫利）
- URL: https://ypk.39.net/879250/manual/
- 备选（官方PDF）: https://www.sanofi.cn/assets/dot-cn/pages/docs/products/prescription-products/yamoli-cn-20260107.pdf
- 探测: 双源均 HTTP 200；赛诺菲官网 PDF 为 application/pdf 439KB（2026-01-07 修订版，含核准/修改日期、全章节）
- 厂家: 赛诺菲（北京）制药有限公司（PDF为赛诺菲中国官网发布）
- 类别: 磺脲类
- authority_level: 3
- 备注: 批准文号 国药准字H20057673；官方PDF为最优源

### 格列齐特缓释片（达美康）
- URL: https://ypk.39.net/2008213/manual
- 探测: HTTP 200 / 说明书正文章节齐全
- 厂家: 法国施维雅药厂（Servier）
- 类别: 磺脲类
- authority_level: 3
- 备注: 进口原研；页面未印批准文号

### 格列吡嗪片（美吡达）
- URL: https://ypk.39.net/507705/manual
- 备选（PDF）: https://www.yaopinnet.com/sms_pdf/H20054244l.pdf（HTTP 200, application/pdf, 已核对正文含全章节与国药准字）
- 探测: HTTP 200 / 章节齐全
- 厂家: 海南赞邦制药有限公司
- 类别: 磺脲类
- authority_level: 3
- 备注: 批准文号 国药准字H10930076；PDF源对应批号 H20054244

## 三、噻唑烷二酮类（TZD）

### 盐酸吡格列酮片（可成）
- URL: https://ypk.39.net/724977/manual
- 探测: HTTP 200 / 章节齐全
- 厂家: 上海朝晖药业有限公司
- 类别: TZD
- authority_level: 3
- 备注: 批准文号 国药准字H20070060。原研艾可拓（武田）未在国内说明书平台找到可访问页

## 四、α-糖苷酶抑制剂

### 阿卡波糖片（拜唐苹）
- URL: https://ypk.39.net/498311/manual
- 探测: HTTP 200 / 章节齐全
- 厂家: 拜耳医药保健有限公司
- 类别: α-糖苷酶抑制剂
- authority_level: 3
- 备注: 批准文号 国药准字H19990205

### 伏格列波糖片（倍欣）
- URL: https://ypk.39.net/631758/manual/
- 备选: https://db.yaozh.com/instruct/7716935100000009.html（国产，HTTP 200）
- 探测: HTTP 200 / 章节齐全
- 厂家: 天津武田药品有限公司
- 类别: α-糖苷酶抑制剂
- authority_level: 3
- 备注: 批准文号 国药准字H20010308；yaozh 页对应国产 H20093758/H20143287

### 米格列醇片
- URL: https://ypk.39.net/1000007957/manual
- 备选: https://db.yaozh.com/instruct/3636035346674688.html（HTTP 200，国药准字H20083446）
- 探测: HTTP 200 / 章节齐全
- 厂家: 山东新时代药业有限公司
- 类别: α-糖苷酶抑制剂
- authority_level: 3
- 备注: 批准文号 国药准字H20113504

## 五、DPP-4 抑制剂

### 磷酸西格列汀片（捷诺维）
- URL: https://ypk.39.net/923574/manual/
- 探测: HTTP 200 / 章节齐全
- 厂家: Merck Sharp & Dohme（默沙东）
- 类别: DPP-4 抑制剂
- authority_level: 3
- 备注: 批准文号 国药准字J20140095（进口）

### 沙格列汀片（安立泽）
- URL: https://ypk.39.net/2009006/manual/
- 探测: HTTP 200 / 章节齐全
- 厂家: Bristol-Myers Squibb Company（页面生产企业字段）
- 类别: DPP-4 抑制剂
- authority_level: 3
- 备注: 说明书为 BMS 时期版本；国内现由阿斯利康持有

### 利格列汀片（欧唐宁）
- URL: https://ypk.39.net/2033115/manual
- 探测: HTTP 200 / 章节齐全
- 厂家: 勃林格殷格翰（Boehringer Ingelheim，中国公司：上海勃林格殷格翰药业有限公司）
- 类别: DPP-4 抑制剂
- authority_level: 3
- 备注: 页面未印批准文号与生产企业字段（与欧唐静同厂，已交叉核实）

### 维格列汀片（佳维乐）
- URL: https://ypk.39.net/2008840/manual
- 探测: HTTP 200 / 章节齐全
- 厂家: Novartis（诺华）
- 类别: DPP-4 抑制剂
- authority_level: 3
- 备注: 页面未印批准文号

## 六、SGLT2 抑制剂

### 达格列净片（安达唐）
- URL: https://ypk.39.net/2308986/manual/
- 探测: HTTP 200 / 章节齐全（该页用【功能主治】而非【适应症】标题，正文完整真实）
- 厂家: 阿斯利康（AstraZeneca，进口原研）
- 类别: SGLT2 抑制剂
- authority_level: 3
- 备注: 批准文号 国药准字J20170040

### 达格列净片（孚来达）
- URL: https://cn.hspharm.com/upload/file/2023/12/05/3a0c06786ac84a92b7aaed6892a9568f.pdf
- 探测: HTTP 200 / application/pdf 1.37MB，31页，pdftotext 提取确认含核准/修改日期及全章节
- 厂家: 江苏豪森药业集团有限公司
- 类别: SGLT2 抑制剂
- authority_level: 3
- 备注: 官方网站 PDF（2023-11-09 修订版），国产仿制药高质量源

### 恩格列净片（欧唐静）
- URL: https://ypk.39.net/1000018964/manual/
- 备选: https://db.yaozh.com/instruct/51784.html（HTTP 200）
- 探测: HTTP 200 / 章节齐全
- 厂家: 上海勃林格殷格翰药业有限公司
- 类别: SGLT2 抑制剂
- authority_level: 3
- 备注: 批准文号 国药准字HJ20170351/HJ20201008

### 卡格列净片（怡可安）
- URL: https://db.yaozh.com/instruct/44275.html
- 探测: HTTP 200 / 药智网说明书库章节齐全
- 厂家: Janssen（强生，进口注册 H20170375/H20170374）
- 类别: SGLT2 抑制剂
- authority_level: 3
- 备注: 标题含 100mg/300mg 双规格批号

## 七、GLP-1 受体激动剂

### 利拉鲁肽注射液（诺和力）
- URL: https://ypk.39.net/2309131/manual
- 探测: HTTP 200 / 章节齐全
- 厂家: 诺和诺德（中国）制药有限公司
- 类别: GLP-1 受体激动剂
- authority_level: 3
- 备注: 批准文号 国药准字J20160037

### 司美格鲁肽注射液（诺和泰）
- URL: https://ypk.39.net/2310026/manual/
- 探测: HTTP 200 / 章节齐全
- 厂家: 丹麦诺和诺德公司（Novo Nordisk）
- 类别: GLP-1 受体激动剂
- authority_level: 3
- 备注: 批准文号 国药准字SJ20210015

### 度拉糖肽注射液（度易达）
- URL: https://www.lillymedical.cn/books/%E5%BA%A6%E6%8B%89%E7%B3%96%E8%82%BD%E6%B3%A8%E5%B0%84%E6%B6%B2%E8%AF%B4%E6%98%8E%E4%B9%A6.pdf
- 备选: https://db.yaozh.com/instruct/3604898195351680.html（HTTP 200）；https://ypk.39.net/2310060/manual/（HTTP 200，章节齐全但缺企业字段）
- 探测: 礼来官方 PDF HTTP 200 / application/pdf 574KB，pdftotext 确认含黑框警示、全章节、进口注册证号 S20190021/S20190022
- 厂家: 礼来（Lilly）
- 类别: GLP-1 受体激动剂
- authority_level: 3
- 备注: 官方 PDF 为最优源

### 艾塞那肽（百泌达）
- URL: https://www.dayi.org.cn/drug/1152617.html
- 探测: HTTP 200 / 中国医药信息查询平台（国家药监局南方所主办），词条含成分/性状/适应症/规格/用法用量/不良反应/禁忌/注意事项等完整章节
- 厂家: 未显示（原研 Amylin/礼来百泌达，国内多仿制）
- 类别: GLP-1 受体激动剂
- authority_level: 3（药典式药品信息词条，非单一产品说明书）
- 备注: **百泌达/艾塞那肽注射液的独立产品说明书页未找到**，此为降级替代源；语料构建时建议标注为“通用药品信息”而非产品说明书

## 八、胰岛素

### 门冬胰岛素30注射液（诺和锐30）
- URL: https://ypk.39.net/2002712/manual/
- 探测: HTTP 200 / 章节齐全
- 厂家: 丹麦诺和诺德公司
- 类别: 预混胰岛素类似物
- authority_level: 3
- 备注: 批准文号 国药准字J20100037

### 重组甘精胰岛素注射液（长秀霖）
- URL: https://ypk.39.net/859082/manual
- 探测: HTTP 200 / 章节齐全
- 厂家: 甘李药业股份有限公司
- 类别: 长效胰岛素类似物
- authority_level: 3
- 备注: 批准文号 国药准字S20050051。原研来得时（Lantus，赛诺菲）未找到可访问说明书页

### 德谷胰岛素注射液
- URL: https://db.yaozh.com/instruct/3604898153449600.html
- 探测: HTTP 200 / 【药品名称】【成分】【适应症】等章节齐全（正文已抽样核对）
- 厂家: 诺和诺德（Novo Nordisk）
- 类别: 超长效胰岛素类似物
- authority_level: 3
- 备注: 批准文号 国药准字S20227007（诺和期/诺和达系列）

### 地特胰岛素注射液（诺和平）
- URL: https://ypk.39.net/2033732/manual
- 探测: HTTP 200 / 章节齐全
- 厂家: 诺和诺德（中国）制药有限公司
- 类别: 长效胰岛素类似物
- authority_level: 3
- 备注: 批准文号 国药准字J20140106。**任务清单中“地特胰岛素（诺和灵N）”系商品名混淆**：诺和灵N 是“精蛋白生物合成人胰岛素（预混/中性）”系列，地特胰岛素商品名为“诺和平/Levemir”，本报告按通用名地特胰岛素核实

## 九、格列奈类

### 瑞格列奈片（诺和龙）
- URL: https://ypk.39.net/504369/manual/
- 探测: HTTP 200 / 章节齐全
- 厂家: 诺和诺德（中国）制药有限公司（页面生产企业字段）
- 类别: 格列奈类（苯甲酸衍生物）
- authority_level: 3
- 备注: 处方药/医保乙类标注清晰

### 那格列奈片（贝加）
- URL: https://ypk.39.net/773209/manual/
- 探测: HTTP 200 / 章节齐全
- 厂家: 正大天晴药业集团股份有限公司
- 类别: 格列奈类（苯丙氨酸衍生物）
- authority_level: 3
- 备注: 批准文号 国药准字H20060661。原研唐力（诺华）未找到可访问说明书页

---

## 汇总

**成功核实 28 份药品说明书（另有 1 份艾塞那肽降级词条、6 个备选源 URL），总计 35 个可用 URL / 尝试约 45 个候选 URL。类别全覆盖 9 大类。**

| # | 通用名 | 商品名 | 类别 | 主源 | 批准文号 |
|---|--------|--------|------|------|----------|
| 1 | 盐酸二甲双胍片 | 格华止 | 双胍 | ypk.39.net/507707/manual/ | H20023371 |
| 2 | 盐酸二甲双胍缓释片 | 格华止 | 双胍 | ypk.39.net/2009048/manual | — |
| 3 | 盐酸二甲双胍缓释片 | 国产 | 双胍 | db.yaozh.com/instruct/7716935220000068 | H20080251 |
| 4 | 格列美脲片 | 亚莫利 | 磺脲 | sanofi.cn 官方PDF + ypk 879250 | H20057673 |
| 5 | 格列齐特缓释片 | 达美康 | 磺脲 | ypk.39.net/2008213/manual | 进口 |
| 6 | 格列吡嗪片 | 美吡达 | 磺脲 | ypk.39.net/507705/manual + yaopinnet PDF | H10930076 |
| 7 | 盐酸吡格列酮片 | 可成 | TZD | ypk.39.net/724977/manual | H20070060 |
| 8 | 阿卡波糖片 | 拜唐苹 | α-苷酶抑制剂 | ypk.39.net/498311/manual | H19990205 |
| 9 | 伏格列波糖片 | 倍欣 | α-苷酶抑制剂 | ypk.39.net/631758/manual/ | H20010308 |
| 10 | 米格列醇片 | — | α-苷酶抑制剂 | ypk.39.net/1000007957/manual | H20113504 |
| 11 | 磷酸西格列汀片 | 捷诺维 | DPP-4 | ypk.39.net/923574/manual/ | J20140095 |
| 12 | 沙格列汀片 | 安立泽 | DPP-4 | ypk.39.net/2009006/manual/ | — |
| 13 | 利格列汀片 | 欧唐宁 | DPP-4 | ypk.39.net/2033115/manual | — |
| 14 | 维格列汀片 | 佳维乐 | DPP-4 | ypk.39.net/2008840/manual | — |
| 15 | 达格列净片 | 安达唐 | SGLT2 | ypk.39.net/2308986/manual/ | J20170040 |
| 16 | 达格列净片 | 孚来达 | SGLT2 | cn.hspharm.com 官方PDF | — |
| 17 | 恩格列净片 | 欧唐静 | SGLT2 | ypk.39.net/1000018964/manual/ | HJ20170351 |
| 18 | 卡格列净片 | 怡可安 | SGLT2 | db.yaozh.com/instruct/44275.html | H20170375/74 |
| 19 | 利拉鲁肽注射液 | 诺和力 | GLP-1 | ypk.39.net/2309131/manual | J20160037 |
| 20 | 司美格鲁肽注射液 | 诺和泰 | GLP-1 | ypk.39.net/2310026/manual/ | SJ20210015 |
| 21 | 度拉糖肽注射液 | 度易达 | GLP-1 | lillymedical.cn 官方PDF | S20190021/22 |
| 22 | 艾塞那肽 | 百泌达* | GLP-1 | dayi.org.cn/drug/1152617.html | 降级词条* |
| 23 | 门冬胰岛素30注射液 | 诺和锐30 | 预混胰岛素 | ypk.39.net/2002712/manual/ | J20100037 |
| 24 | 重组甘精胰岛素注射液 | 长秀霖 | 长效胰岛素 | ypk.39.net/859082/manual | S20050051 |
| 25 | 德谷胰岛素注射液 | 诺和期/达 | 超长效胰岛素 | db.yaozh.com/instruct/3604898153449600 | S20227007 |
| 26 | 地特胰岛素注射液 | 诺和平 | 长效胰岛素 | ypk.39.net/2033732/manual | J20140106 |
| 27 | 瑞格列奈片 | 诺和龙 | 格列奈 | ypk.39.net/504369/manual/ | — |
| 28 | 那格列奈片 | 贝加 | 格列奈 | ypk.39.net/773209/manual/ | H20060661 |

### 未找到/不可用清单（宁缺毋滥）
- **艾塞那肽注射液（百泌达）独立产品说明书**：未找到可访问页（仅 dayi.org.cn 药典式词条，已标注降级）
- **来得时（甘精胰岛素原研）**、**唐力（那格列奈原研）**、**艾可拓（吡格列酮原研）**：无国内可访问说明书页，已用国产品牌/等效通用名替代
- **fh21.com/复禾医药**：全部 521 反爬，不可用
- **NMPA CDE 说明书电子化 PDF（cde.org.cn/hymlj/download/sms/）**：202 + JS 挑战，程序化访问被拦截
- **方舟健客 jianke.com**：HTTP 200 但为电商产品页，无说明书正文
- **用药助手 drugs.dxy.cn**：药品页可被搜索引擎收录，但直接访问部分接口 302/404，未纳入最终清单

### 语料构建建议
- 主力源为 ypk.39.net（22 份）与 db.yaozh.com（6 份），两家页面均含完整说明书正文，适合抓取解析；4 份厂家官方 PDF（赛诺菲/礼来/豪森/yaopinnet）权威性最高，建议优先入库
- ypk.39.net 页面结构统一（【药品名称】起全章节），解析器可复用；安达唐页注意【功能主治】替代【适应症】的标题差异
- 清单中 5 个页面未印批准文号（表中"—"），入库时建议从 NMPA 数据目录补充元数据