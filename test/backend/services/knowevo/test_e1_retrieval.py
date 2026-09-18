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