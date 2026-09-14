#!/usr/bin/env bash
# T-02 corpus fetcher: downloads verified drug-label sources into
# competition/corpus/drug_labels/ for registry + ingest.
# URLs were verified live (HTTP 200, content-type checked) by the T-02
# sourcing pass on 2026-09-14; re-verify with curl -I before re-fetching.
set -u
cd "$(dirname "$0")"
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'

fetch() {  # fetch <filename> <url>
  local name="$1" url="$2"
  if [ -s "drug_labels/$name" ]; then echo "skip $name (exists)"; return; fi
  code=$(curl -sL -A "$UA" -o "drug_labels/$name" -w '%{http_code}' "$url")
  size=$(wc -c < "drug_labels/$name" 2>/dev/null || echo 0)
  echo "$code $size $name"
}

fetch metformin_hcl_tab_gehuazhi.html    'https://ypk.39.net/507707/manual/'
fetch metformin_hcl_er_gehuazhi.html     'https://ypk.39.net/2009048/manual'
fetch metformin_hcl_er_domestic.html     'https://db.yaozh.com/instruct/7716935220000068.html'
fetch glimepiride_yamoli.pdf             'https://www.sanofi.cn/assets/dot-cn/pages/docs/products/prescription-products/yamoli-cn-20260107.pdf'
fetch glimepiride_yamoli.html            'https://ypk.39.net/879250/manual/'
fetch gliclazide_mr_diamicon.html        'https://ypk.39.net/2008213/manual'
fetch glipizide_meibida.html             'https://ypk.39.net/507705/manual'
fetch pioglitazone_kecheng.html          'https://ypk.39.net/724977/manual'
fetch acarbose_baitangping.html          'https://ypk.39.net/498311/manual'
fetch voglibose_beixin.html              'https://ypk.39.net/631758/manual/'
fetch miglitol_tab.html                  'https://ypk.39.net/1000007957/manual'
fetch sitagliptin_januvia.html           'https://ypk.39.net/923574/manual/'
fetch saxagliptin_onglyza.html          'https://ypk.39.net/2009006/manual/'
fetch linagliptin_trajenta.html          'https://ypk.39.net/2033115/manual'
fetch vildagliptin_galvus.html           'https://ypk.39.net/2008840/manual'
fetch dapagliflozin_forxiga.html         'https://ypk.39.net/2308986/manual/'
fetch dapagliflozin_fulaida.pdf         'https://cn.hspharm.com/upload/file/2023/12/05/3a0c06786ac84a92b7aaed6892a9568f.pdf'
fetch empagliflozin_jardiance.html       'https://ypk.39.net/1000018964/manual/'
fetch canagliflozin_invokana.html       'https://db.yaozh.com/instruct/44275.html'
fetch liraglutide_victoza.html          'https://ypk.39.net/2309131/manual'
fetch semaglutide_ozempic.html          'https://ypk.39.net/2310026/manual/'
fetch dulaglutide_trulicity.pdf         'https://www.lillymedical.cn/books/%E5%BA%A6%E6%8B%89%E7%B3%96%E8%82%BD%E6%B3%A8%E5%B0%84%E6%B6%B2%E8%AF%B4%E6%98%8E%E4%B9%A6.pdf'
fetch exenatide_dayi_entry.html         'https://www.dayi.org.cn/drug/1152617.html'
fetch insulin_aspart30_novomix.html     'https://ypk.39.net/2002712/manual/'
fetch insulin_glargine_changxiulin.html 'https://ypk.39.net/859082/manual'
fetch insulin_degludec.html              'https://db.yaozh.com/instruct/3604898153449600.html'
fetch insulin_detemir_levemir.html       'https://ypk.39.net/2033732/manual'
fetch repaglinide_novonorm.html          'https://ypk.39.net/504369/manual/'
fetch nateglinide_beijia.html           'https://ypk.39.net/773209/manual/'
