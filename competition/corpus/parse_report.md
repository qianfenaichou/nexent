# 解析体检报告（parse checkup · T-02）

- 体检对象：registry 全部 58 份（含 blind；blind 仅为体检统计，不进构建流水线）
- 索引 chunk 总量：1497
- 落库 parse_quality：58 份
- 低分清单（<0.6）：0 份
- 未入索引（0 chunk）：18 份（blind 切分或摄取排队中）

## 低分清单（<0.6）

| asset_no | 标题 | chunks | 分数 | 备注 |
|---|---|---|---|---|

## 未入索引

| asset_no | 标题 | 切分 |
|---|---|---|
| guide-pc-manual-2022 | 国家基层糖尿病防治管理手册（2022） | blind |
| guide-foot-2019-p1 | 中国糖尿病足防治指南（2019版）第一部分 | blind |
| guide-foot-2019-p2 | 中国糖尿病足防治指南（2019版）第二部分 | blind |
| guide-foot-2024 | 中国糖尿病足诊治指南（2024） | blind |
| guide-foot-path-2023 | 中国糖尿病足诊治临床路径（2023版） | blind |
| cp-t2dm-2009 | 2型糖尿病临床路径（2009年版） | blind |
| edu-foot-2026 | 糖尿病足科普 | blind |
| edu-basic-dzjkb | 糖尿病科普知识 | blind |
| drug-glimep | 格列美脲片（亚莫利）说明书 | blind |
| drug-pioglita | 盐酸吡格列酮片（可成）说明书 | blind |
| drug-acarbose | 阿卡波糖片（拜唐苹）说明书 | blind |
| drug-voglibose | 伏格列波糖片（倍欣）说明书 | blind |
| drug-sita | 磷酸西格列汀片（捷诺维）说明书 | blind |
| drug-lina | 利格列汀片（欧唐宁）说明书 | blind |
| drug-dapa-az | 达格列净片（安达唐）说明书 | blind |
| drug-dapa-fld | 达格列净片（孚来达）说明书 | blind |
| drug-aspart30 | 门冬胰岛素30注射液（诺和锐30）说明书 | blind |
| drug-degludec | 德谷胰岛素注射液说明书 | blind |

## 全量分数

| asset_no | chunks | 分数 |
|---|---|---|
| guide-2020 | 332 | 0.8 |
| guide-2024 | 407 | 0.8 |
| guide-nutri-2022 | 192 | 0.8 |
| guide-pc-2022 | 87 | 0.8 |
| guide-elderly-2024 | 144 | 0.8 |
| guide-elderly-2021 | 104 | 0.8 |
| cp-t2dm-county-2016 | 5 | 0.8 |
| cp-t1dm-county-2016 | 4 | 0.8 |
| cp-t2dm-highrisk-2016 | 3 | 0.8 |
| std-glucose-monitor-2021 | 43 | 0.8 |
| edu-who-factsheet | 4 | 0.8 |
| drug-dula | 16 | 0.8 |
| cp-t1dm-2009 | 3 | 0.6 |
| cp-t2dm-compl-2016 | 5 | 0.6 |
| cp-neuropathy-2016 | 3 | 0.6 |
| cp-foot-2016 | 3 | 0.6 |
| cp-hypoglycemia-2016 | 2 | 0.6 |
| std-glucometer-ws781 | 3 | 0.6 |
| edu-core-2020 | 2 | 0.6 |
| edu-insulin-2022 | 5 | 0.6 |
| edu-bgm-myths-2025 | 1 | 0.6 |
| edu-discern-2023 | 1 | 0.6 |
| edu-lifestyle-2023 | 1 | 0.6 |
| drug-metf-tab | 5 | 0.6 |
| drug-metf-er-gxz | 8 | 0.6 |
| drug-metf-er-gn | 6 | 0.6 |
| drug-gliclazide | 5 | 0.6 |
| drug-glipizide | 4 | 0.6 |
| drug-miglitol | 3 | 0.6 |
| drug-saxa | 4 | 0.6 |
| drug-vilda | 4 | 0.6 |
| drug-empa | 11 | 0.6 |
| drug-cana | 24 | 0.6 |
| drug-lira | 7 | 0.6 |
| drug-semaglu | 11 | 0.6 |
| drug-exena | 12 | 0.6 |
| drug-glargine | 5 | 0.6 |
| drug-detemir | 6 | 0.6 |
| drug-repagli | 7 | 0.6 |
| drug-nategli | 5 | 0.6 |
| guide-pc-manual-2022 | 0 | None |
| guide-foot-2019-p1 | 0 | None |
| guide-foot-2019-p2 | 0 | None |
| guide-foot-2024 | 0 | None |
| guide-foot-path-2023 | 0 | None |
| cp-t2dm-2009 | 0 | None |
| edu-foot-2026 | 0 | None |
| edu-basic-dzjkb | 0 | None |
| drug-glimep | 0 | None |
| drug-pioglita | 0 | None |
| drug-acarbose | 0 | None |
| drug-voglibose | 0 | None |
| drug-sita | 0 | None |
| drug-lina | 0 | None |
| drug-dapa-az | 0 | None |
| drug-dapa-fld | 0 | None |
| drug-aspart30 | 0 | None |
| drug-degludec | 0 | None |