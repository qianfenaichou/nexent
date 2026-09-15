#!/usr/bin/env python3
"""Build competition/corpus/registry.csv (T-02).

Sources: guidelines/ (11 verified PDFs), drug_labels/ (30 verified files).
Every source_url below was fetched and content-verified during the T-02
sourcing pass (2026-09-14); the compliance line is honest per-row data -
authority levels follow the brief's contract (1 national std, 2 guideline,
3 drug label, 4 popular science).

The 80/20 build/blind split is deterministic (sha256 of asset_no) so the
split is reproducible without committing randomness: build if
int(sha256(asset_no)[:8],16) % 10 < 8, else blind. The 2020/2024 anchor
pair is force-included in build (T-04/T-06 depend on them).
"""
import csv
import hashlib
from pathlib import Path

CORPUS = Path(__file__).resolve().parent

ROWS = []

# --- guidelines (source: CDS journal editions, hosted on hnysfww.com
# --- 湖南药事服务网, a Hunan Pharmacy Society platform; verified 2026-09-14)
GUIDES = [
    ("guide-2020", "中国2型糖尿病防治指南（2020年版）",
     "https://www.hnysfww.com/data/article/1619222032934605040.pdf",
     "guidelines/t2dm_guideline_2020.pdf", "text", 2,
     "中华医学会糖尿病学分会·中华糖尿病杂志2021;13(4)，期刊排印版转载（湖南药事服务网）"),
    ("guide-2024", "中国糖尿病防治指南（2024版）",
     "https://www.hnysfww.com/data/article/1735800276657362700.pdf",
     "guidelines/dm_guideline_2024.pdf", "text", 2,
     "中华医学会糖尿病学分会·中华糖尿病杂志2025;17(1)，期刊排印版转载（湖南药事服务网）"),
    ("guide-nutri-2022", "中国糖尿病医学营养治疗指南（2022版）",
     "https://www.hnysfww.com/data/article/1664407059367528280.pdf",
     "guidelines/dm_nutrition_therapy_2022.pdf", "text", 2,
     "五学会联合·中华糖尿病杂志2022;14(9)，期刊排印版转载"),
    ("guide-pc-2022", "国家基层糖尿病防治管理指南（2022）",
     "https://www.hnysfww.com/data/article/1647557424677414104.pdf",
     "guidelines/primary_care_dm_guide_2022.pdf", "text", 1,
     "国家卫健委基层司指导·中华内科杂志2022;61(3)，国家级管理指南"),
    ("guide-pc-manual-2022", "国家基层糖尿病防治管理手册（2022）",
     "https://www.hnysfww.com/data/article/1658193574047096075.pdf",
     "guidelines/primary_care_dm_manual_2022.pdf", "text", 1,
     "国家基层糖尿病防治管理办公室·中华内科杂志2022;61(7)"),
    ("guide-elderly-2024", "中国老年糖尿病诊疗指南（2024版）",
     "https://www.hnysfww.com/data/article/1708887770197275603.pdf",
     "guidelines/elderly_dm_2024.pdf", "text", 2,
     "国家老年医学中心等三机构·中华糖尿病杂志2024;16(2)"),
    ("guide-elderly-2021", "中国老年糖尿病诊疗指南（2021年版）",
     "https://www.hnysfww.com/data/article/1612462771125575707.pdf",
     "guidelines/elderly_dm_2021.pdf", "text", 2,
     "国家老年医学中心等三机构·中华糖尿病杂志2021;13(1)"),
    ("guide-foot-2019-p1", "中国糖尿病足防治指南（2019版）第一部分",
     "https://www.hnysfww.com/data/article/1560731720094487230.pdf",
     "guidelines/diabetic_foot_2019_part1.pdf", "text", 2,
     "三学会联合·中华糖尿病杂志2019;11(2)"),
    ("guide-foot-2019-p2", "中国糖尿病足防治指南（2019版）第二部分",
     "https://www.hnysfww.com/data/article/1560731777831482671.pdf",
     "guidelines/diabetic_foot_2019_part2.pdf", "text", 2,
     "三学会联合·中华糖尿病杂志2019;11(3)"),
    ("guide-foot-2024", "中国糖尿病足诊治指南（2024）",
     "https://www.hnysfww.com/data/article/1732082536099677072.pdf",
     "guidelines/diabetic_foot_2024.pdf", "text", 2,
     "中国医促会外周血管医学分会等·中国临床医生杂志2024;52(11)"),
    ("guide-foot-path-2023", "中国糖尿病足诊治临床路径（2023版）",
     "https://www.hnysfww.com/data/article/1678146402772954828.pdf",
     "guidelines/diabetic_foot_pathway_2023.pdf", "text", 2,
     "CDS糖尿病足与周围血管病学组·中华内分泌代谢杂志2023;39(2)"),
]
for asset_no, title, url, local, modality, auth, note in GUIDES:
    ROWS.append([asset_no, title, "guideline", modality, auth, url, note, local])

# --- drug labels (ypk.39.net = 39健康网药品说明书库; yaozh = 药智网; dayi =
# --- 中国医药信息查询平台; official PDFs from sanofi/lilly/hspharm)
DRUGS = [
    ("drug-metf-tab", "盐酸二甲双胍片（格华止）说明书",
     "https://ypk.39.net/507707/manual/", "drug_labels/metformin_hcl_tab_gehuazhi.html",
     "中美上海施贵宝，国药准字H20023371"),
    ("drug-metf-er-gxz", "盐酸二甲双胍缓释片（格华止）说明书",
     "https://ypk.39.net/2009048/manual", "drug_labels/metformin_hcl_er_gehuazhi.html",
     "Bristol-Myers Squibb 原研缓释片"),
    ("drug-metf-er-gn", "盐酸二甲双胍缓释片（国产）说明书",
     "https://db.yaozh.com/instruct/7716935220000068.html", "drug_labels/metformin_hcl_er_domestic.html",
     "国药准字H20080251，药智网说明书库"),
    ("drug-glimep", "格列美脲片（亚莫利）说明书",
     "https://www.sanofi.cn/assets/dot-cn/pages/docs/products/prescription-products/yamoli-cn-20260107.pdf",
     "drug_labels/glimepiride_yamoli.pdf",
     "赛诺菲中国官网PDF（2026-01修订版），国药准字H20057673"),
    ("drug-gliclazide", "格列齐特缓释片（达美康）说明书",
     "https://ypk.39.net/2008213/manual", "drug_labels/gliclazide_mr_diamicon.html",
     "法国施维雅药厂进口原研"),
    ("drug-glipizide", "格列吡嗪片（美吡达）说明书",
     "https://ypk.39.net/507705/manual", "drug_labels/glipizide_meibida.html",
     "海南赞邦制药，国药准字H10930076"),
    ("drug-pioglita", "盐酸吡格列酮片（可成）说明书",
     "https://ypk.39.net/724977/manual", "drug_labels/pioglitazone_kecheng.html",
     "上海朝晖药业，国药准字H20070060"),
    ("drug-acarbose", "阿卡波糖片（拜唐苹）说明书",
     "https://ypk.39.net/498311/manual", "drug_labels/acarbose_baitangping.html",
     "拜耳医药保健，国药准字H19990205"),
    ("drug-voglibose", "伏格列波糖片（倍欣）说明书",
     "https://ypk.39.net/631758/manual/", "drug_labels/voglibose_beixin.html",
     "天津武田药品，国药准字H20010308"),
    ("drug-miglitol", "米格列醇片说明书",
     "https://ypk.39.net/1000007957/manual", "drug_labels/miglitol_tab.html",
     "山东新时代药业，国药准字H20113504"),
    ("drug-sita", "磷酸西格列汀片（捷诺维）说明书",
     "https://ypk.39.net/923574/manual/", "drug_labels/sitagliptin_januvia.html",
     "默沙东进口，国药准字J20140095"),
    ("drug-saxa", "沙格列汀片（安立泽）说明书",
     "https://ypk.39.net/2009006/manual/", "drug_labels/saxagliptin_onglyza.html",
     "BMS/AZ，进口原研"),
    ("drug-lina", "利格列汀片（欧唐宁）说明书",
     "https://ypk.39.net/2033115/manual", "drug_labels/linagliptin_trajenta.html",
     "勃林格殷格翰，进口原研"),
    ("drug-vilda", "维格列汀片（佳维乐）说明书",
     "https://ypk.39.net/2008840/manual", "drug_labels/vildagliptin_galvus.html",
     "诺华，进口原研"),
    ("drug-dapa-az", "达格列净片（安达唐）说明书",
     "https://ypk.39.net/2308986/manual/", "drug_labels/dapagliflozin_forxiga.html",
     "阿斯利康进口，国药准字J20170040"),
    ("drug-dapa-fld", "达格列净片（孚来达）说明书",
     "https://cn.hspharm.com/upload/file/2023/12/05/3a0c06786ac84a92b7aaed6892a9568f.pdf",
     "drug_labels/dapagliflozin_fulaida.pdf",
     "豪森药业官网PDF（2023-11修订版）"),
    ("drug-empa", "恩格列净片（欧唐静）说明书",
     "https://ypk.39.net/1000018964/manual/", "drug_labels/empagliflozin_jardiance.html",
     "上海勃林格殷格翰，国药准字HJ20170351"),
    ("drug-cana", "卡格列净片（怡可安）说明书",
     "https://db.yaozh.com/instruct/44275.html", "drug_labels/canagliflozin_invokana.html",
     "强生进口，H20170375/74，药智网"),
    ("drug-lira", "利拉鲁肽注射液（诺和力）说明书",
     "https://ypk.39.net/2309131/manual", "drug_labels/liraglutide_victoza.html",
     "诺和诺德，国药准字J20160037"),
    ("drug-semaglu", "司美格鲁肽注射液（诺和泰）说明书",
     "https://ypk.39.net/2310026/manual/", "drug_labels/semaglutide_ozempic.html",
     "诺和诺德，国药准字SJ20210015"),
    ("drug-dula", "度拉糖肽注射液（度易达）说明书",
     "https://www.lillymedical.cn/books/%E5%BA%A6%E6%8B%89%E7%B3%96%E8%82%BD%E6%B3%A8%E5%B0%84%E6%B6%B2%E8%AF%B4%E6%98%8E%E4%B9%A6.pdf",
     "drug_labels/dulaglutide_trulicity.pdf",
     "礼来医学官网PDF，S20190021/22"),
    ("drug-exena", "艾塞那肽注射液药品信息（百泌达）",
     "https://www.dayi.org.cn/drug/1152617.html", "drug_labels/exenatide_dayi_entry.html",
     "中国医药信息查询平台（药监局南方所主办）通用药品词条，降级源：独立产品说明书页未找到"),
    ("drug-aspart30", "门冬胰岛素30注射液（诺和锐30）说明书",
     "https://ypk.39.net/2002712/manual/", "drug_labels/insulin_aspart30_novomix.html",
     "诺和诺德，国药准字J20100037"),
    ("drug-glargine", "重组甘精胰岛素注射液（长秀霖）说明书",
     "https://ypk.39.net/859082/manual", "drug_labels/insulin_glargine_changxiulin.html",
     "甘李药业，国药准字S20050051"),
    ("drug-degludec", "德谷胰岛素注射液说明书",
     "https://db.yaozh.com/instruct/3604898153449600.html", "drug_labels/insulin_degludec.html",
     "诺和诺德，国药准字S20227007，药智网"),
    ("drug-detemir", "地特胰岛素注射液（诺和平）说明书",
     "https://ypk.39.net/2033732/manual", "drug_labels/insulin_detemir_levemir.html",
     "诺和诺德，国药准字J20140106"),
    ("drug-repagli", "瑞格列奈片（诺和龙）说明书",
     "https://ypk.39.net/504369/manual/", "drug_labels/repaglinide_novonorm.html",
     "诺和诺德，进口原研"),
    ("drug-nategli", "那格列奈片（贝加）说明书",
     "https://ypk.39.net/773209/manual/", "drug_labels/nateglinide_beijia.html",
     "正大天晴，国药准字H20060661"),
]


# --- third batch: clinical pathways (national 2009/2016 editions,
# --- yaopinnet 中国医药信息查询平台 mirror), lab standards, public edu
PATHWAYS = [
    ("cp-t2dm-2009", "2型糖尿病临床路径（2009年版）",
     "https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20090969.pdf",
     "pathways/cp_t2dm_2009.pdf", "卫生部办公厅发布，yaopinnet 镜像（与官方文本一致）", 1),
    ("cp-t1dm-2009", "1型糖尿病临床路径（2009年版）",
     "https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20090968.pdf",
     "pathways/cp_t1dm_2009.pdf", "卫生部办公厅发布，yaopinnet 镜像", 1),
    ("cp-t2dm-county-2016", "2型糖尿病临床路径（2016年县级医院版）",
     "https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160430.pdf",
     "pathways/cp_t2dm_county_2016.pdf", "国卫办医函〔2016〕1315号附件，yaopinnet 镜像", 1),
    ("cp-t1dm-county-2016", "1型糖尿病临床路径（2016年县级医院版）",
     "https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160429.pdf",
     "pathways/cp_t1dm_county_2016.pdf", "国卫办医函〔2016〕1315号附件，yaopinnet 镜像", 1),
    ("cp-t2dm-highrisk-2016", "2型糖尿病（伴高危因素）临床路径（2016年版）",
     "https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160432.pdf",
     "pathways/cp_t2dm_highrisk_2016.pdf", "国卫办医函〔2016〕1315号附件，yaopinnet 镜像", 1),
    ("cp-t2dm-compl-2016", "2型糖尿病伴多并发症临床路径（2016年版）",
     "https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160433.pdf",
     "pathways/cp_t2dm_compl_2016.pdf", "国卫办医函〔2016〕1315号附件，yaopinnet 镜像", 1),
    ("cp-neuropathy-2016", "糖尿病性周围神经病变临床路径（2016年版）",
     "https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160434.pdf",
     "pathways/cp_neuropathy_2016.pdf", "国卫办医函〔2016〕1315号附件，yaopinnet 镜像", 1),
    ("cp-foot-2016", "糖尿病足病临床路径（2016年版）",
     "https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160436.pdf",
     "pathways/cp_foot_2016.pdf", "国卫办医函〔2016〕1315号附件，yaopinnet 镜像", 1),
    ("cp-hypoglycemia-2016", "低血糖症临床路径（2016年版）",
     "https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160437.pdf",
     "pathways/cp_hypoglycemia_2016.pdf", "国卫办医函〔2016〕1315号附件，yaopinnet 镜像", 1),
]
LAB = [
    ("std-glucometer-ws781", "便携式血糖仪临床操作和质量管理指南（WS/T 781-2021）",
     "https://www.waizi.org.cn/bz/112890.html",
     "lab_standards/ws_t781_2021_glucometer.html", "国家卫健委卫生行业标准（2021-04-19发布）", 1),
    ("std-glucose-monitor-2021", "中国血糖监测临床应用指南（2021年版）",
     "https://www.hnysfww.com/data/article/1636670471627149593.pdf",
     "lab_standards/glucose_monitor_guide_2021.pdf", "中华医学会糖尿病学分会·中华糖尿病杂志2021;13(10)", 2),
]
PUBEDU = [
    ("edu-core-2020", "糖尿病防治核心信息（2020联合国糖尿病日）",
     "https://www.cma.org.cn/art/2020/11/17/art_68_36576.html",
     "public_edu/edu_core_info_cma.html", "国家卫健委发布·中华医学会官网转发", 1),
    ("edu-insulin-2022", "胰岛素到底是个啥（学会科普）",
     "https://www.cma.org.cn/art/2022/7/25/art_4584_46638.html",
     "public_edu/edu_insulin_cma.html", "中华医学会科学普及部", 2),
    ("edu-bgm-myths-2025", "糖尿病患者自测血糖的七个误区",
     "https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202501/t20250108_303744.html",
     "public_edu/edu_bgm_myths_cdc.html", "中国疾控中心慢病科普", 1),
    ("edu-discern-2023", "三招教您辨别科学的糖尿病防治信息",
     "https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202408/t20240823_295116.html",
     "public_edu/edu_discern_info_cdc.html", "中国疾控中心供稿", 1),
    ("edu-lifestyle-2023", "综合生活方式干预 预防糖尿病的发生",
     "https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202408/t20240823_295117.html",
     "public_edu/edu_lifestyle_cdc.html", "中国疾控中心慢病中心", 1),
    ("edu-foot-2026", "糖尿病足科普",
     "https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202601/t20260119_314732.html",
     "public_edu/edu_foot_cdc.html", "中国疾控中心", 1),
    ("edu-basic-dzjkb", "糖尿病科普知识",
     "https://www.dzjkb.org.cn/dazhongkepu/dazhongkepu/ba43132591a929cc591707761dbab271.html",
     "public_edu/edu_basic_dzjkb.html", "大众健康报·成都温江区人民医院供稿", 4),
    ("edu-who-factsheet", "糖尿病（WHO实况报道中文版）",
     "https://www.who.int/zh/news-room/fact-sheets/detail/diabetes",
     "public_edu/edu_who_factsheet.html", "世界卫生组织", 1),
]


def split_for(asset_no: str, doc_type: str) -> str:
    """Deterministic 80/20: build if hash bucket < 8. Anchor guides stay
    in build (T-04/T-06/T-11 all consume them)."""
    if doc_type == "guideline" and asset_no in ("guide-2020", "guide-2024"):
        return "build"
    h = int(hashlib.sha256(asset_no.encode()).hexdigest()[:8], 16)
    return "build" if h % 10 < 8 else "blind"


def main():
    for asset_no, title, url, local, note in DRUGS:
        ROWS.append([asset_no, title, "drug_label", "text", 3, url, note, local])

    out = CORPUS / "registry.csv"
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["asset_no", "title", "doc_type", "modality", "authority_level",
                    "source_url", "license_note", "local_file", "split"])
        for r in ROWS:
            asset_no, title, doc_type, modality, auth, url, note, local = r
            w.writerow([asset_no, title, doc_type, modality, auth, url, note, local,
                        split_for(asset_no, doc_type)])
    build = sum(1 for r in ROWS if split_for(r[0], r[4] if r[0].startswith("guide") else "drug_label") == "build")
    print(f"registry rows: {len(ROWS)} (build={build}, blind={len(ROWS) - build})")
    print(f"written: {out}")


if __name__ == "__main__":
    main()
