"""Unit tests for services/knowevo/e1_retrieval.py (T-10a-2 E1 baseline).

Layer 1: pure functions (tokenizer, chunking, BM25 scoring, context
rendering). No network, no LLM, no real corpus: fixture documents are tiny
strings so the index math is fully deterministic and auditable.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo.e1_retrieval import (
    DEFAULT_AUTHORITY_WEIGHTS,
    DEFAULT_PER_DOC_QUOTA,
    Chunk,
    CorpusDoc,
    Retriever,
    build_chunks,
    chunk_text,
    retrieve_context,
    tokenize,
)


class TestTokenizer:
    def test_ascii_words_lowercased(self):
        assert tokenize("HbA1c OGTT mmol/L") == ["hba1c", "ogtt", "mmol", "l"]

    def test_cjk_bigrams(self):
        toks = tokenize("二甲双胍")
        assert "二甲" in toks and "双胍" in toks and len(toks) == 3

    def test_single_cjk_char_is_kept(self):
        assert tokenize("低") == ["低"]

    def test_mixed_text_both_systems(self):
        toks = tokenize("二甲双胍 500mg")
        assert "双胍" in toks and "500mg" in toks

    def test_empty_text(self):
        assert tokenize("") == []


class TestChunking:
    def test_short_text_single_chunk(self):
        assert chunk_text("一段很短的文字") == ["一段很短的文字"]

    def test_blank_line_paragraph_boundary(self):
        chunks = chunk_text("第一段。\n\n第二段。", size=10)
        # "第一段。\n\n第二段。" is exactly 10 chars -> packed into one chunk
        assert len(chunks) == 1

    def test_blank_line_paragraph_splits_when_over_size(self):
        chunks = chunk_text("第一段甲。\n\n第二段。", size=6)
        assert len(chunks) == 2

    def test_overlong_paragraph_hard_split(self):
        chunks = chunk_text("甲" * 30, size=10)
        assert all(len(c) <= 10 for c in chunks)
        assert "".join(chunks) == "甲" * 30

    def test_packing_respects_size(self):
        chunks = chunk_text("\n\n".join([f"段{i}" * 8 for i in range(4)]), size=20)
        assert all(len(c) <= 20 for c in chunks)


FIXTURE_DOCS = [
    CorpusDoc(asset_no="dm-guide", title="糖尿病防治指南",
              doc_type="guideline", authority_level=2, split="build",
              local_file="guidelines/dm_guide.pdf",
              text="二甲双胍是2型糖尿病患者的一线首选降糖药物。双胍类\n"
                   "二甲双胍通过减少肝糖输出发挥作用。"),
    CorpusDoc(asset_no="insulin-guide", title="胰岛素用药手册",
              doc_type="guideline", authority_level=3, split="build",
              local_file="guidelines/insulin.pdf",
              text="胰岛素用于1型糖尿病患者的替代治疗。"),

    CorpusDoc(asset_no="blind-doc", title="盲区文档",
              doc_type="edu_graphic", authority_level=4, split="blind",
              local_file="public_edu/blind.html",
              text="低血糖处置的科普知识。"),
]


@pytest.fixture
def retriever() -> Retriever:
    return Retriever.from_documents(FIXTURE_DOCS)


class TestBuildChunks:
    def test_flattens_docs_into_chunks(self):
        chunks = build_chunks(FIXTURE_DOCS)
        assert len(chunks) == 3
        assert all(isinstance(c, Chunk) for c in chunks)

    def test_empty_doc_yields_no_chunks(self):
        doc = CorpusDoc(asset_no="x", title="t", doc_type="g",
                        authority_level=1, split="build", local_file="",
                        text="")
        assert build_chunks([doc]) == []


class TestBM25:
    def test_relevant_doc_ranks_first(self, retriever):
        hits = retriever.search("二甲双胍 一线 首选", top_k=2)
        assert hits and hits[0].chunk.doc_id == "dm-guide"

    def test_insulin_doc_ranked_above_dm_guide_for_insulin_query(self, retriever):
        hits = retriever.search("胰岛素 1型糖尿病 替代治疗", top_k=3)
        assert hits[0].chunk.doc_id == "insulin-guide"

    def test_top_k_respected(self, retriever):
        assert len(retriever.search("二甲双胍", top_k=1)) <= 1

    def test_no_match_returns_empty(self, retriever):
        assert retriever.search("完全不存在的概念XYZ", top_k=2) == []

    def test_splits_filter(self, retriever):
        blind_only = retriever.search("低血糖", top_k=3, splits=("blind",))
        assert blind_only and all(h.chunk.split == "blind" for h in blind_only)
        build_only = retriever.search("低血糖", top_k=3, splits=("build",))
        assert all(h.chunk.split == "build" for h in build_only)
        assert not any(h.chunk.doc_id == "blind-doc" for h in build_only)

    def test_empty_index(self):
        assert Retriever.from_chunks([]).search("任何问题") == []

    def test_direct_construction_is_searchable(self):
        # The index must be built on every construction path: direct
        # construction used to return empty hits silently (a search that
        # "works" but finds nothing is the worst retrieval bug to notice).
        chunks = build_chunks([FIXTURE_DOCS[0]])
        direct = Retriever(chunks=chunks)
        hits = direct.search("二甲双胍 一线", top_k=1)
        assert hits and hits[0].chunk.doc_id == "dm-guide"


class TestRetrieveContext:
    def test_returns_context_and_evidence(self, retriever):
        ctx, ev = retrieve_context(retriever, "二甲双胍一线药物", top_k=2)
        assert "糖尿病防治指南" in ctx
        # Only dm-guide shares tokens with this query; one hit is correct
        # (a "2 evidence records asserted" test would be asserting noise).
        assert len(ev) >= 1
        assert ev[0]["rank"] == 1
        assert ev[0]["doc_id"] == "dm-guide"
        assert ev[0]["title"] == "糖尿病防治指南"
        assert ev[0]["span_hash"]
        assert ev[0]["authority_level"] == 2
        assert ev[0]["split"] == "build"

    def test_max_chars_caps_context(self, retriever):
        ctx, ev = retrieve_context(retriever, "二甲双胍", top_k=5, max_chars=50)
        assert len(ctx) <= 50 + 100  # one block may overhang slightly
        assert len(ev) <= 2

    def test_no_hits_empty_context(self, retriever):
        ctx, ev = retrieve_context(retriever, "不存在的概念XYZ", top_k=3)
        assert ctx == "" and ev == []


# ---------------------------------------------------------------------------
# T-18d D4: authority-aware ranking + per-doc quota
# ---------------------------------------------------------------------------


def _auth_doc(asset_no: str, title: str, authority_level: int, text: str,
              split: str = "build") -> CorpusDoc:
    return CorpusDoc(asset_no=asset_no, title=title, doc_type="guideline",
                     authority_level=authority_level, split=split,
                     local_file=f"g/{asset_no}.pdf", text=text)


AUTH_DOCS = [
    # Low-authority drug label that matches the query term-for-term (BM25
    # favourite) vs a high-authority guideline that matches with slightly
    # lower frequency - the exact "说明书压过指南" scenario D4 targets.
    _auth_doc("label-1", "二甲双胍说明书", 3,
              "二甲双胍 二甲双胍 二甲双胍 二甲双胍 二甲双胍 二甲双胍 "
              "二甲双胍 二甲双胍 二甲双胍 二甲双胍"),
    _auth_doc("guide-1", "糖尿病防治指南", 2,
              "二甲双胍 二甲双胍 二甲双胍 二甲双胍 二甲双胍 "
              "二甲双胍 二甲双胍 二甲双胍"),
    _auth_doc("policy-1", "糖尿病诊疗规范", 1,
              "二甲双胍 二甲双胍 二甲双胍 二甲双胍 二甲双胍 "
              "二甲双胍"),
    _auth_doc("edu-1", "糖尿病科普图文", 4,
              "二甲双胍 二甲双胍 二甲双胍"),
]


@pytest.fixture
def auth_retriever() -> Retriever:
    return Retriever.from_documents(AUTH_DOCS)


class TestAuthorityPrior:
    def test_default_weights_frozen(self):
        assert DEFAULT_AUTHORITY_WEIGHTS == {1: 1.0, 2: 0.95, 3: 0.85, 4: 0.75}
        assert DEFAULT_PER_DOC_QUOTA == 2

    def test_authority_prior_reorders_close_scores(self, auth_retriever):
        # label-1 has the raw BM25 favourite; with the prior, guide-1's
        # near-score must outrank it (0.95 vs 0.85 multiplier flips the pair
        # only when raw scores are close - the D4 contract).
        hits = auth_retriever.search("二甲双胍", top_k=4)
        ordered = [h.chunk.doc_id for h in hits]
        assert ordered[0] in ("guide-1", "policy-1"), ordered
        assert ordered.index("guide-1") < ordered.index("label-1")
        assert ordered.index("policy-1") < ordered.index("edu-1")

    def test_raw_bm25_score_preserved(self, auth_retriever):
        hits = auth_retriever.search("二甲双胍", top_k=4)
        for hit in hits:
            weight = DEFAULT_AUTHORITY_WEIGHTS.get(hit.chunk.authority_level, 1.0)
            # ranked_score = round(round(bm25,6) * weight, 6); tolerance is
            # the double rounding of score and ranked_score.
            assert abs(hit.score * weight - hit.ranked_score) < 1e-5

    def test_no_prior_keeps_pure_bm25_order(self):
        # {} disables the prior: the raw BM25 favourite (label-1) ranks first
        # again, proving the prior (not the corpus) caused the reorder.
        plain = Retriever.from_documents(AUTH_DOCS, authority_weights={})
        hits = plain.search("二甲双胍", top_k=4)
        assert hits[0].chunk.doc_id == "label-1"

    def test_custom_weights_configurable(self):
        # A steeper prior can force a strict authority order even when the
        # low-authority doc dominates BM25.
        steep = Retriever.from_documents(
            AUTH_DOCS,
            authority_weights={1: 1.0, 2: 0.9, 3: 0.5, 4: 0.4})
        hits = steep.search("二甲双胍", top_k=4)
        ranked = [h.chunk.doc_id for h in hits]
        assert ranked == ["policy-1", "guide-1", "label-1", "edu-1"]

    def test_authority_level_missing_uses_weight_1(self):
        chunk = Chunk(doc_id="noauth", title="t", chunk_idx=0,
                      text="二甲双胍 二甲双胍 二甲双胍",
                      source="noauth", authority_level=9, split="build")
        r = Retriever.from_chunks([chunk])
        hits = r.search("二甲双胍", top_k=1)
        assert hits and abs(hits[0].score - hits[0].ranked_score) < 1e-6


class TestPerDocQuota:
    def test_quota_caps_one_doc(self, retriever):
        # dm-guide is the only document matching "二甲双胍一线首选"; with
        # quota=1 it must occupy exactly one top-k slot even when multiple of
        # its chunks score.
        q1 = Retriever.from_documents(FIXTURE_DOCS, per_doc_quota=1)
        hits = q1.search("二甲双胍 一线 首选", top_k=5)
        assert len({h.chunk.doc_id for h in hits}) == len(hits)
        assert all(h.chunk.doc_id != "insulin-guide" for h in hits) or len(hits) == 1

    def test_quota_off_returns_topk(self, retriever):
        noq = Retriever.from_documents(FIXTURE_DOCS, per_doc_quota=None)
        hits = noq.search("二甲双胍 一线 首选", top_k=5)
        assert len(hits) <= 5
        assert len({h.chunk.doc_id for h in hits}) >= 1

    def test_quota_default_2(self, auth_retriever):
        hits = auth_retriever.search("二甲双胍", top_k=10)
        counts: dict[str, int] = {}
        for h in hits:
            counts[h.chunk.doc_id] = counts.get(h.chunk.doc_id, 0) + 1
        assert max(counts.values()) <= 2

    def test_over_quota_skips_do_not_consume_slots(self, auth_retriever):
        # guide-1 (2 chunks) should occupy at most 2 of the 4 slots, letting
        # the remaining docs fill the rest rather than dropping them.
        hits = auth_retriever.search("二甲双胍", top_k=4)
        assert len(hits) == 4
        ids = [h.chunk.doc_id for h in hits]
        assert ids.count("guide-1") <= 2


class TestRetrieveContextScores:
    def test_evidence_carries_both_scores(self, auth_retriever):
        ctx, ev = retrieve_context(auth_retriever, "二甲双胍", top_k=3)
        assert len(ev) >= 1
        for e in ev:
            assert "score" in e and "ranked_score" in e
            assert e["ranked_score"] <= e["score"] + 1e-9  # prior never boosts
            assert "二甲双胍" in ctx