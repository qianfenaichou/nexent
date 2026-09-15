#!/usr/bin/env bash
# Third corpus batch: clinical pathways / lab standards / public-edu pages.
# All URLs verified live by the sourcing pass (2026-09-14); two PDF hosts
# (yaopinnet, hnysfww) occasionally truncate - resume (-C -) if a download
# lands short.
set -u
cd "$(dirname "$0")"
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
mkdir -p pathways lab_standards public_edu

dl() {  # dl <dir> <name> <url>
  local dir="$1" name="$2" url="$3"
  if [ -s "$dir/$name" ]; then echo "skip $name"; return; fi
  code=$(curl -sL -A "$UA" -C - -o "$dir/$name" -w '%{http_code}' "$url")
  sz=$(wc -c < "$dir/$name" 2>/dev/null || echo 0)
  echo "$code $sz $name"
}

# --- clinical pathways (national 2009/2016 editions, yaopinnet mirror)
dl pathways cp_t2dm_2009.pdf      'https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20090969.pdf'
dl pathways cp_t1dm_2009.pdf      'https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20090968.pdf'
dl pathways cp_t2dm_county_2016.pdf 'https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160430.pdf'
dl pathways cp_t1dm_county_2016.pdf 'https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160429.pdf'
dl pathways cp_t2dm_highrisk_2016.pdf 'https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160432.pdf'
dl pathways cp_t2dm_compl_2016.pdf   'https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160433.pdf'
dl pathways cp_neuropathy_2016.pdf   'https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160434.pdf'
dl pathways cp_foot_2016.pdf         'https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160436.pdf'
dl pathways cp_hypoglycemia_2016.pdf 'https://www.yaopinnet.com/tools/linchuanglujing/xy/xy20160437.pdf'

# --- lab / monitoring standards
dl lab_standards ws_t781_2021_glucometer.html 'https://www.waizi.org.cn/bz/112890.html'
dl lab_standards glucose_monitor_guide_2021.pdf 'https://www.hnysfww.com/data/article/1636670471627149593.pdf'

# --- public education pages (CMA / China CDC / WHO / 大众健康报)
dl public_edu edu_core_info_cma.html     'https://www.cma.org.cn/art/2020/11/17/art_68_36576.html'
dl public_edu edu_insulin_cma.html       'https://www.cma.org.cn/art/2022/7/25/art_4584_46638.html'
dl public_edu edu_bgm_myths_cdc.html     'https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202501/t20250108_303744.html'
dl public_edu edu_discern_info_cdc.html  'https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202408/t20240823_295116.html'
dl public_edu edu_lifestyle_cdc.html     'https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202408/t20240823_295117.html'
dl public_edu edu_foot_cdc.html          'https://www.chinacdc.cn/jkkp/mxfcrb/fpdx/202601/t20260119_314732.html'
dl public_edu edu_basic_dzjkb.html       'https://www.dzjkb.org.cn/dazhongkepu/dazhongkepu/ba43132591a929cc591707761dbab271.html'
dl public_edu edu_who_factsheet.html     'https://www.who.int/zh/news-room/fact-sheets/detail/diabetes'
