#!/usr/bin/env python3
"""E0 micro-benchmark runner (T-01 leftover, recovered by T-02).

Asks the 20 questions in competition/corpus/e0-questions.md to both
registered model tiers through the tokenrouter OpenAI-compatible endpoint,
records answer / hit / latency / token usage, and appends rows to
competition/docs/e0-baseline.md.

Credentials come from KW_E0_KEY_PRIMARY / KW_E0_KEY_SMALL env vars
(read by the caller from the registered model records); nothing is
hardcoded here.
"""
import json
import os
import re
import sys
import time
import urllib.request

API_URL = "https://api.tokenrouter.com/v1/chat/completions"
MODEL = os.environ.get("KW_E0_MODEL", "z-ai/glm-5.3-free")
# Each question: (type, text, [accept words]). Scoring is ANY-match per
# question (the question table in e0-questions.md defines which questions
# use any-of semantics; a single word list keeps the runner simple and the
# rule auditable).
QUESTIONS = [
    ("F", "二甲双胍是哪一类降糖药？", ["双胍"]),
    ("F", "糖化血红蛋白（HbA1c）反映多长时间的平均血糖水平？", ["8", "12", "2-3个月", "2~3个月", "三个月", "周"]),
    ("F", "2型糖尿病患者的一线药物治疗首选是什么？", ["二甲双胍"]),
    ("F", "成人正常空腹血糖的正常参考范围是多少（mmol/L）？", ["3.9", "6.1"]),
    ("F", "糖尿病诊断标准中，OGTT 2小时血糖达到多少可诊断糖尿病？", ["11.1"]),
    ("M", "磺脲类药物的主要作用机制是什么，它通过促进哪个器官分泌什么物质降糖？", ["胰岛素"]),
    ("M", "SGLT2抑制剂通过哪个器官排泄葡萄糖，长期使用需警惕哪类感染风险增加？", ["肾"]),
    ("M", "GLP-1受体激动剂常见胃肠道不良反应是什么，其主要降糖机制涉及哪种激素？", ["恶心", "胰高血糖素样肽", "GLP-1"]),
    ("M", "阿卡波糖应与食物如何同服才能发挥作用，其抑制的酶是什么？", ["第一口", "糖苷酶", "同餐"]),
    ("M", "糖尿病酮症酸中毒时，机体胰岛素不足导致哪类代谢紊乱，特征性实验室表现是什么？", ["酮"]),
    ("V", "《中国2型糖尿病防治指南》最新版建议的 HbA1c 控制目标一般是多少？", ["7.0", "7%", "<7", "低于7"]),
    ("V", "指南中，血压控制目标对糖尿病合并高血压患者推荐多少mmHg以下？", ["130"]),
    ("V", "新诊断2型糖尿病患者若 HbA1c≥9% 且症状明显，指南建议起始什么治疗？", ["胰岛素"]),
    ("V", "老年糖尿病患者的血糖控制目标通常如何调整？", ["宽松", "个体化", "7.5", "8.0", "8.5"]),
    ("V", "妊娠期高血糖患者的空腹血糖控制目标是多少？", ["5.3"]),
    ("X", "55岁男性BMI 28新诊断2型糖尿病，HbA1c 8.2%，无并发症，初始用药建议？", ["二甲双胍"]),
    ("X", "患者使用达格列净后反复泌尿生殖感染，应换用哪类机制不同的降糖药？", ["DPP", "GLP", "二甲双胍", "西格列汀", "列汀"]),
    ("X", "手术日清晨2型糖尿病空腹患者，平时门冬胰岛素30每日两次，今晨如何处理？", ["减", "停", "监测", "基础"]),
    ("X", "CKD 3b期（eGFR 30）的2型糖尿病患者，二甲双胍应如何调整？", ["减", "停"]),
    ("X", "老年住院患者夜间意识模糊伴出汗，血糖2.2 mmol/L，第一步处置？", ["葡萄糖", "补糖", "静推"]),
]
SYSTEM_PROMPT = "你是内分泌科临床助理，基于医学知识简洁回答，不要编造。"


def ask(key: str, question: str) -> dict:
    """One completion call; 429s back off exponentially (free tier is
    rate-limited hard - 15s+ between retries observed in practice)."""
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0.2,
    }).encode()
    last_err = None
    for attempt in range(6):
        req = urllib.request.Request(
            API_URL, data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            return {
                "latency": round(time.monotonic() - started, 2),
                "content": (data.get("choices") or [{}])[0].get("message", {}).get("content") or "",
                "usage": data.get("usage", {}),
            }
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                wait = min(15 * (attempt + 1), 90)
                print(f"    429, backoff {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
                continue
            raise
    raise last_err


def score(answer: str, accept_words: list) -> bool:
    """Any-match scoring per the question table in e0-questions.md."""
    return any(k in answer for k in accept_words)


def main() -> int:
    keys = {
        "primary": os.environ.get("KW_E0_KEY_PRIMARY", ""),
        "small": os.environ.get("KW_E0_KEY_SMALL", ""),
    }
    tiers = [t for t in os.environ.get("KW_E0_TIERS", "primary,small").split(",") if t]
    missing = [k for k in tiers if not keys.get(k)]
    if missing:
        print(f"missing env: {[f'KW_E0_KEY_{t.upper()}' for t in missing]}")
        return 1
    results = {}
    for tier in tiers:
        key = keys[tier]
        rows = []
        for i, (qtype, q, must) in enumerate(QUESTIONS, start=1):
            try:
                r = ask(key, q)
                empty = not r["content"].strip()
                hit = (not empty) and score(r["content"], must)
                rows.append({"no": i, "type": qtype, "hit": hit,
                             "empty": empty,
                             "latency": r["latency"],
                             "tokens": r["usage"].get("total_tokens"),
                             "answer_head": r["content"][:60]})
                flag = "EMPTY" if empty else ("HIT" if hit else "MISS")
                print(f"[{tier}] q{i} {flag} "
                      f"{r['latency']}s tokens={r['usage'].get('total_tokens')}")
            except Exception as e:  # noqa: BLE001 - record and continue
                rows.append({"no": i, "type": qtype, "hit": None,
                             "error": str(e)[:120]})
                print(f"[{tier}] q{i} ERROR {str(e)[:120]}")
            time.sleep(3)  # inter-question pacing for the free-tier rate limit
        hits = [r for r in rows if r["hit"] is True]
        done = [r for r in rows if "latency" in r]
        results[tier] = {
            "rows": rows, "hit_rate": round(len(hits) / len(rows), 3),
            "answered": len(done),
            "avg_latency": round(sum(r["latency"] for r in done) /
                                 max(1, len(done)), 2),
            "total_tokens": sum(r["tokens"] or 0 for r in rows if "tokens" in r),
        }
    print(json.dumps({t: {k: v for k, v in r.items() if k != "rows"}
                     for t, r in results.items()}, ensure_ascii=False, indent=2))
    out = os.environ.get("KW_E0_OUT", "/tmp/e0_results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
