#!/usr/bin/env python3
"""Parse health checkup (T-02): score each registered asset's parse quality
from what actually landed in the ES index, write doc_asset_t.parse_quality
and emit competition/corpus/parse_report.md.

Score components (ingest_service.parse_quality_score applied to a
concatenated chunk sample, plus chunk-count coverage):
- text volume & structure from indexed content
- coverage: expected doc has >=1 chunk in the index
- a noise penalty when the sample is mostly navigational boilerplate
  ("相关问答", "更多>", sidebars) rather than body text
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

CORPUS = Path(__file__).resolve().parent
sys.path.insert(0, str(CORPUS.parents[1] / "backend"))
from services.knowevo.ingest_service import parse_quality_score  # noqa: E402

BASE = os.environ.get("KW_BASE_URL", "http://localhost:3000")
TOKEN = Path(os.environ.get("KW_TOKEN_FILE", "/tmp/t02_token.txt")).read_text().strip()
INDEX = os.environ.get("KW_INDEX", "1-fdc99d691db5406e980751dbe131f201")
TENANT = os.environ.get("KW_TENANT", "6756b0ab-39c0-462a-9745-aa12e1511fcd")

NOISE_MARKERS = ["相关问答", "更多>", "扫码关注", "点击咨询", "广告", "相关推荐", "版权声明"]


def fetch_all_chunks() -> dict:
    """Pull every chunk of the index via the native chunks endpoint
    (vectordatabase_app.py L892, POST /{index_name}/chunks)."""
    url = f"{BASE}/api/indices/{INDEX}/chunks"
    body = json.dumps({"max_chunks_count": 20000}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Cookie": f"nexent_access_token={TOKEN}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def main() -> int:
    data = fetch_all_chunks()
    # response: {"status": ..., "chunks": [{content, filename, path_or_url, ...}]}
    chunks = data.get("chunks") or []
    by_file = defaultdict(list)
    for c in chunks:
        fname = c.get("filename") or c.get("metadata", {}).get("original_filename") or ""
        by_file[fname].append(c.get("content", ""))

    import csv
    registry = list(csv.DictReader(open(CORPUS / "registry.csv", encoding="utf-8")))

    results = []
    for row in registry:
        asset = row["asset_no"]
        local = row["local_file"]
        fname = Path(local).name
        # uploaded names may gain a numeric suffix on re-upload; match prefix
        texts = []
        for f, cs in by_file.items():
            if f == fname or f.rsplit(".", 1)[0].startswith(fname.rsplit(".", 1)[0]):
                texts.extend(cs)
        sample = "\n".join(texts[:8])
        if not texts:
            results.append({"asset_no": asset, "title": row["title"],
                            "split": row["split"], "chunks": 0,
                            "score": None, "note": "no chunks in index"})
            continue
        base = parse_quality_score(sample)
        noise = sum(sample.count(m) for m in NOISE_MARKERS)
        noise_ratio = noise / max(1, len(sample)) * 100
        score = round(max(0.0, base - (0.2 if noise_ratio > 1 else 0)), 2)
        results.append({"asset_no": asset, "title": row["title"],
                        "split": row["split"], "chunks": len(texts),
                        "score": score, "note": f"noise_ratio={noise_ratio:.2f}%"})

    # write-back into doc_asset_t via the service (parse_status/parse_quality)
    from services.knowevo.ingest_service import update_parse_result
    written = 0
    for r in results:
        status = "processed" if r["score"] is not None else "no_index_chunk"
        if update_parse_result(TENANT, r["asset_no"], status, r["score"]):
            written += 1

    # report
    low = [r for r in results if r["score"] is not None and r["score"] < 0.6]
    missing = [r for r in results if r["score"] is None]
    lines = [
        "# 解析体检报告（parse checkup · T-02）",
        "",
        f"- 体检对象：registry 全部 {len(results)} 份（含 blind；blind 仅为体检统计，不进构建流水线）",
        f"- 索引 chunk 总量：{sum(len(v) for v in by_file.values())}",
        f"- 落库 parse_quality：{written} 份",
        f"- 低分清单（<0.6）：{len(low)} 份",
        f"- 未入索引（0 chunk）：{len(missing)} 份（blind 切分或摄取排队中）",
        "",
        "## 低分清单（<0.6）",
        "",
        "| asset_no | 标题 | chunks | 分数 | 备注 |",
        "|---|---|---|---|---|",
    ]
    for r in low:
        lines.append(f"| {r['asset_no']} | {r['title'][:30]} | {r['chunks']} | {r['score']} | {r['note']} |")
    lines += ["", "## 未入索引", "", "| asset_no | 标题 | 切分 |", "|---|---|---|"]
    for r in missing:
        lines.append(f"| {r['asset_no']} | {r['title'][:30]} | {r['split']} |")
    lines += ["", "## 全量分数", "", "| asset_no | chunks | 分数 |", "|---|---|---|"]
    for r in sorted(results, key=lambda x: (x["score"] is None, -(x["score"] or 0))):
        lines.append(f"| {r['asset_no']} | {r['chunks']} | {r['score']} |")
    (CORPUS / "parse_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"parse checkup done: total={len(results)} low={len(low)} "
          f"missing={len(missing)} written={written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
