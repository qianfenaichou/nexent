"""
KnowEvo evaluation scaffolding v1 (T-10a): testset validator + pass^k
aggregation + judge/expand prompt rendering.

K4 protocol (02-technical-plan 6): 120-question four-type testset, five-
tuple LLM-as-judge rubric, pass^2 as the headline metric (each question
run 3x, share judged PASS >= 2), trace completeness, p95 latency/tokens.

This task delivers the v1 seed testset (E0 20 questions upgraded to the K4
shape, corpus/testset-v1-seed.json) plus the tooling that T-10a-2/3 and E1
build on:

  validate_testset  json-schema shape checks (type enum, rubric weights,
                    V-type dual gold labels, evidence_origin)
  passk_aggregate   per-question 0/1 runs -> pass^2 / pass^3 shares
  render_judge      five-tuple judge prompt (bilingual YAML pair)
  render_expand     seed->variants expansion prompt (bilingual YAML pair)

No LLM call and no database in v0: the judge/expand prompts render here
and the real tier routing is a T-08 wiring concern. Evidence shape matches
eval_run_t.metrics keys (acc/pass2/pass3/...). The pass^k scorer logic is
rewritten from tau2-bench (arXiv:2505.23319) pass^k aggregation - "Design
inspired by tau2-bench" per 03-development-plan 4.2.3.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts"

# K4 6.1: four types and their target counts in the full 120 set.
TYPE_TARGETS = {"F": 35, "M": 45, "V": 20, "X": 20}
VALID_TYPES = set(TYPE_TARGETS)

# Five-tuple rubric weights (judge-rubric.md): key_facts sums to 0.6.
RUBRIC_KEYS = ("key_facts", "refusal", "evidence", "reasoning", "safety")
DEFAULT_WEIGHTS = {"key_facts": 0.6, "refusal": 0.1, "evidence": 0.1,
                   "reasoning": 0.1, "safety": 0.1}
PASS_LINE = 0.8  # judge-rubric.md: total >= 0.8 counts PASS


def _render_prompt(name: str, lang: str, **variables: Any
                   ) -> tuple[str, str]:
    """Load ``{name}_{lang}.yaml`` and substitute {{ var }} placeholders
    (same convention as the kg_service prompt renderer, kept local so this
    pipeline module owns its templates without importing private symbols)."""
    import yaml
    path = PROMPT_DIR / f"{name}_{lang}.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    system = str(data.get("system_prompt", ""))
    user = str(data.get("user_prompt", ""))

    def fill(template: str) -> str:
        for key, value in variables.items():
            for pattern in (f"{{{{ {key} }}}}", f"{{{{{key}}}}}",
                            f"{{{{ {key}}}}}", f"{{{{{key} }}}}"):
                template = template.replace(pattern, str(value))
        return template

    return fill(system), fill(user)


def validate_testset(data: dict[str, Any]) -> list[str]:
    """Shape-check one testset document; return a list of violations.

    Checks the frozen template (knowevo/eval/testset-template.json) plus
    the K4 semantic rules: type enum, id prefix matches type, V questions
    carry dual gold labels, rubric has key_facts + weights summing to 1,
    evidence_origin present. An empty list means the testset is valid.
    """
    errors: list[str] = []
    questions = data.get("questions") or []
    if not questions:
        return ["testset has no questions array"]
    for i, q in enumerate(questions):
        loc = f"questions[{i}] ({q.get('id', '?')})"
        qtype = q.get("type")
        if qtype not in VALID_TYPES:
            errors.append(f"{loc}: invalid type {qtype!r}")
            continue
        qid = str(q.get("id", ""))
        if not qid.startswith(qtype + "-"):
            errors.append(f"{loc}: id {qid!r} does not start with {qtype}-")
        # V questions need dual gold labels
        ans = q.get("answer")
        if qtype == "V":
            if not isinstance(ans, dict) or "answer_old" not in ans \
                    or "answer_new" not in ans:
                errors.append(f"{loc}: V question must carry "
                              f"answer_old/answer_new dual labels")
        elif ans is None or (isinstance(ans, str) and not ans.strip()):
            errors.append(f"{loc}: missing answer")
        # rubric: key_facts + weights
        rubric = q.get("rubric") or {}
        kf = rubric.get("key_facts")
        if not isinstance(kf, list) or not kf:
            errors.append(f"{loc}: rubric.key_facts must be a non-empty list")
        weights = rubric.get("weights") or {}
        if not isinstance(weights, dict) or abs(
                sum(float(v) for v in weights.values()) - 1.0) > 1e-6:
            errors.append(f"{loc}: rubric.weights must sum to 1.0")
        if abs(float(weights.get("key_facts", 0)) - 0.6) > 1e-6:
            errors.append(f"{loc}: rubric.weights.key_facts must be 0.6")
        # evidence_origin (K4 1.2-4 anti-overfitting isolation)
        eo = q.get("evidence_origin")
        if not isinstance(eo, dict) or "blind_set_ratio" not in eo:
            errors.append(f"{loc}: missing evidence_origin.blind_set_ratio")
    return errors


def passk_aggregate(runs: list[dict[str, Any]]) -> dict[str, float]:
    """pass^k over per-question runs.

    ``runs`` is a list of {question_id, pass: 0|1} (3 runs per question for
    the K4 6.2 protocol). Returns {acc, pass2, pass3, n_questions,
    n_runs} - acc is the share of runs judged PASS, pass2/pass3 are the
    share of questions with >=2 / 3 PASS runs.
    """
    from collections import defaultdict
    per_q: dict[str, list[int]] = defaultdict(list)
    n_pass = 0
    for r in runs:
        qid = r["question_id"]
        ok = 1 if r.get("pass") else 0
        per_q[qid].append(ok)
        n_pass += ok
    total_runs = max(1, len(runs))
    n_q = len(per_q)
    pass2 = sum(1 for v in per_q.values() if sum(v) >= 2) / max(1, n_q)
    pass3 = sum(1 for v in per_q.values() if sum(v) >= 3) / max(1, n_q)
    return {
        "acc": round(n_pass / total_runs, 4),
        "pass2": round(pass2, 4),
        "pass3": round(pass3, 4),
        "n_questions": n_q,
        "n_runs": len(runs),
    }


def render_judge(lang: str = "en", **variables: Any) -> tuple[str, str]:
    """Five-tuple judge prompt (system, user)."""
    return _render_prompt("knowevo_judge", lang, **variables)


def render_expand(lang: str = "en", **variables: Any) -> tuple[str, str]:
    """Seed->variants expansion prompt (system, user)."""
    return _render_prompt("knowevo_expand", lang, **variables)


def _testset_hash(data: dict[str, Any]) -> str:
    """Deterministic hash over question content (K4: hash-frozen testset).

    Leading underscore: pytest's default collection (``python_functions =
    test*``) would otherwise pick up a module-level ``testset_hash`` as a
    test and fail on the ``data`` argument."""
    payload = json.dumps(data.get("questions") or [],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="KnowEvo eval v1 scaffolding (validate / aggregate)")
    parser.add_argument("--testset", help="path to testset JSON")
    parser.add_argument("--validate", action="store_true",
                        help="validate the testset shape")
    parser.add_argument("--runs", help="path to runs JSON "
                        "[{question_id, pass}] for pass^k")
    args = parser.parse_args(argv)

    if args.validate:
        data = json.loads(Path(args.testset).read_text(encoding="utf-8"))
        errors = validate_testset(data)
        if errors:
            print("INVALID:")
            for e in errors:
                print(f"  - {e}")
            return 1
        print(f"VALID: {len(data.get('questions') or [])} questions, "
              f"hash={_testset_hash(data)}")
    elif args.runs:
        runs = json.loads(Path(args.runs).read_text(encoding="utf-8"))
        print(json.dumps(passk_aggregate(runs), indent=2))
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())