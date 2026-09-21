"""Deterministic seed-term extraction shared by both KnowEvo chains (T-26).

The evaluation harness (``pipeline/ablation.py``) and the production
decision-card entry (``apps/knowledge_graph_app.py``) must seed the graph
walk the same way, or the two surfaces answer differently for the same
question - which is exactly the divergence T-26 diagnosed (the panel
structurally refused every sentence-length question while the ablation ran
fine). The function therefore lives here, in one place, and
``pipeline/ablation.py`` re-exports it so the T-22 evaluation semantics and
its existing imports are byte-for-byte unchanged.

Why the splitter looks the way it does: ``PgJsonbGraphStore.entity_lookup``
matches ``KgEntity.name ILIKE '%query%'``, so a term only finds an entity
when it is a substring of an entity name. ASCII words and CJK runs are the
deterministic, model-free approximation of that. It is deliberately crude
(pitfall #47's CJK-segmentation family): the word windows can produce
fragments such as ``双胍是``, so seed *relevance* is bounded by this
heuristic and must be measured, never assumed.
"""
from __future__ import annotations

import re

# Lookup budget: how many distinct terms one question may be split into.
# Kept here (not in either caller) so both chains bound the graph fan-out
# identically.
MAX_SEED_LOOKUPS = 12

_ASCII_TERM = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")

__all__ = ["MAX_SEED_LOOKUPS", "extract_seed_terms"]


def extract_seed_terms(question: str, max_terms: int = MAX_SEED_LOOKUPS
                       ) -> list[str]:
    """Deterministic seed terms for the kg entity-lookup channel.

    ASCII words plus CJK runs; a CJK run longer than 6 chars also yields
    3-char sliding windows (step 2) - the store's ILIKE lookup matches
    names *containing* the query, so a query must be a substring of an
    entity name for the name to be found. Order-preserving, deduped,
    capped - the same few terms for the same question on every run, which
    is what keeps the ablation reproducible.
    """
    terms: list[str] = list(_ASCII_TERM.findall(question or ""))
    for run in _CJK_RUN.findall(question or ""):
        terms.append(run)
        if len(run) > 6:
            terms.extend(run[i:i + 3] for i in range(0, len(run) - 2, 2))
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            out.append(term)
    return out[:max_terms]
