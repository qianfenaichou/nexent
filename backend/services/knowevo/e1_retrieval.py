"""E1 pure-RAG retrieval base (T-10a-2): corpus parsing + BM25 index.

This is the "A1 pure RAG" ablation arm from 02-technical-plan 3.5: the
agent gets knowledge_base_search only - no graph, no multi-hop, no version
pinning. E1 therefore needs a document retriever that works over the raw
corpus (58+ PDF/HTML files under competition/corpus/) because the project
had no document index yet (only the T-07 graph store and the T-04 ontology
term matcher, neither of which retrieves document passages).

Design decisions:

* Zero new dependencies. PDF text comes from ``pypdf``/``pdfplumber`` and
  HTML from ``beautifulsoup4`` - all already in the locked dependency tree.
  Chinese tokenization uses character bigrams plus ASCII word tokens (a
  deterministic, auditable scheme) instead of pulling in ``jieba``.
* BM25 over those tokens. Scoring is Okapi BM25 with the usual k1=1.5,
  b=0.75; implemented here rather than importing ``rank_bm25`` for the same
  zero-dependency reason.
* Chunking reuses the same paragraph-split rule as the extraction pipeline
  (blank-line paragraphs, ~600 char chunks) so E1 and later graph arms read
  the same text units, keeping the ablation honest.

Interface is deliberately narrow so tests can drive it without touching
PDFs: ``parse_corpus`` -> ``Retriever.from_documents`` -> ``search``.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Same chunk width as kg_service.chunk_plain_text so E1 and E2+ read the
# same units (ablation isolation depends on this staying equal).
CHUNK_SIZE = 600
_ASCII_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*")
_CJK = re.compile(r"[\u4e00-\u9fff]")

BM25_K1 = 1.5
BM25_B = 0.75


@dataclass
class Chunk:
    """One retrievable passage with its provenance locators."""

    doc_id: str
    title: str
    chunk_idx: int
    text: str
    source: str  # asset_no / corpus-relative path, for the evidence chain
    authority_level: int = 3
    split: str = "build"  # build | blind (corpus 80/20 anti-overfitting split)


def tokenize(text: str) -> list[str]:
    """Deterministic mixed CJK/ASCII tokenizer (no external dictionary).

    CJK runs become overlapping character bigrams (single chars kept when a
    run is one char long); ASCII runs become lowercased word tokens. Bigrams
    are the standard dictionary-free substitute for Chinese word
    segmentation and, unlike a jieba dependency, are reproducible across
    machines and covered by unit tests.
    """
    tokens: list[str] = []
    for ascii_tok in _ASCII_TOKEN.findall(text or ""):
        tokens.append(ascii_tok.lower())
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", text or "")
    for run in cjk_runs:
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Paragraph-aware chunker mirroring kg_service.chunk_plain_text.

    Blank-line paragraphs are packed greedily up to ``size`` characters; an
    over-long paragraph is hard-split so no chunk is unbounded.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        while len(para) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(para[:size])
            para = para[size:]
        if not buf:
            buf = para
        elif len(buf) + 2 + len(para) <= size:
            buf = f"{buf}\n\n{para}"
        else:
            chunks.append(buf)
            buf = para
    if buf:
        chunks.append(buf)
    return chunks


# ---------------------------------------------------------------------------
# Corpus parsing (PDF / HTML -> text). Imports are local so a machine without
# a given parser still loads this module (callers degrade honestly).
# ---------------------------------------------------------------------------

def extract_pdf_text(path: Path) -> str:
    """Extract text from a PDF, preferring pdfplumber then pypdf.

    Empty results are returned as "" (never fabricated); the caller records
    the parse status so a zero-text document is visible, not silent.
    """
    try:
        import pdfplumber

        parts: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        text = "\n\n".join(p for p in parts if p.strip())
        if text.strip():
            return text
    except Exception as exc:  # noqa: BLE001 - parser failure must not abort corpus
        logger.debug("pdfplumber failed for %s: %s", path.name, exc)
    try:
        import pypdf

        reader = pypdf.PdfReader(str(path))
        parts = [(page.extract_text() or "") for page in reader.pages]
        return "\n\n".join(p for p in parts if p.strip())
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf text extraction failed for %s: %s", path.name, exc)
        return ""


def extract_html_text(path: Path) -> str:
    """Extract visible text from an HTML document (bs4, charset-detected)."""
    try:
        from bs4 import BeautifulSoup

        raw = path.read_bytes()
        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
        lines = [ln.strip() for ln in text.splitlines()]
        return "\n".join(ln for ln in lines if ln)
    except Exception as exc:  # noqa: BLE001
        logger.warning("html text extraction failed for %s: %s", path.name, exc)
        return ""


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(path)
    if suffix in (".html", ".htm"):
        return extract_html_text(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


@dataclass
class CorpusDoc:
    """One registered asset parsed from the registry (with its text)."""

    asset_no: str
    title: str
    doc_type: str
    authority_level: int
    split: str
    local_file: str
    text: str = ""
    parse_error: str = ""


def parse_corpus(corpus_root: Path, registry_csv: Path | None = None,
                 include_blind: bool = True) -> list[CorpusDoc]:
    """Parse every registry row into a ``CorpusDoc`` with extracted text.

    ``include_blind=False`` restricts to the build split (the 80% used for
    graph construction), which is what a *pipeline* run should see; the
    evaluation arms keep blind docs so F/M questions sourced from blind
    documents stay answerable (K4 6.1 anti-overfitting isolation).
    """
    import csv

    registry_csv = registry_csv or (corpus_root / "registry.csv")
    docs: list[CorpusDoc] = []
    with open(registry_csv, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            split = (row.get("split") or "build").strip()
            if split == "blind" and not include_blind:
                continue
            local_file = (row.get("local_file") or "").strip()
            path = corpus_root / local_file
            doc = CorpusDoc(
                asset_no=(row.get("asset_no") or "").strip(),
                title=(row.get("title") or "").strip(),
                doc_type=(row.get("doc_type") or "").strip(),
                authority_level=int(row.get("authority_level") or 3),
                split=split,
                local_file=local_file,
            )
            if not path.exists():
                doc.parse_error = "file_missing"
                logger.warning("corpus file missing: %s", path)
            else:
                doc.text = extract_text(path)
                if not doc.text.strip():
                    doc.parse_error = "empty_text"
            docs.append(doc)
    return docs


def build_chunks(docs: Iterable[CorpusDoc]) -> list[Chunk]:
    """Flatten parsed documents into retrievable chunks."""
    out: list[Chunk] = []
    for doc in docs:
        for idx, piece in enumerate(chunk_text(doc.text)):
            out.append(Chunk(
                doc_id=doc.asset_no,
                title=doc.title,
                chunk_idx=idx,
                text=piece,
                source=doc.local_file or doc.asset_no,
                authority_level=doc.authority_level,
                split=doc.split,
            ))
    return out


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------

@dataclass
class Hit:
    """One scored retrieval hit (the document-channel evidence unit).

    ``score`` is the pure BM25 value (what E1 reports and the judge reads,
    stable across ranking changes); ``ranked_score`` is the value actually
    used for ordering after the authority prior is applied (T-18d). Keeping
    the raw BM25 score on the hit preserves auditability: a re-rank never
    rewrites history, it only changes the order.
    """

    chunk: Chunk
    score: float
    ranked_score: float | None = None


# Default authority prior weights (T-18d D4). Semantic: *down-weight* low
# authority rather than boost high authority, so a drug label that happens to
# hit every query term cannot crowd out a guideline with a near-equal BM25
# score. authority_level 1 (national standard) is never penalised; 4 (public
# education) is cut by 25%. Configurable per-retriever; the table is frozen
# by unit tests so a silent change of the default cannot drift the ablation.
DEFAULT_AUTHORITY_WEIGHTS: dict[int, float] = {1: 1.0, 2: 0.95, 3: 0.85, 4: 0.75}
DEFAULT_PER_DOC_QUOTA = 2


@dataclass
class Retriever:
    """In-memory BM25 index over corpus chunks.

    The index is built in ``__post_init__``, so *every* construction path
    (including a direct ``Retriever(chunks=[...])``) yields a searchable
    object. Building only inside the factory methods would leave direct
    construction silently returning empty hits - a search that "works" but
    finds nothing is the hardest kind of retrieval bug to notice.

    Authority-aware ranking (T-18d): ``authority_weights`` applies a
    multiplicative prior ``ranked_score = bm25 * w(authority_level)`` and
    ``per_doc_quota`` caps how many top-k slots one document may occupy
    (deterministic MMR-lite: greedy by ranked score, skip over-quota docs).
    Conventions:
      * ``authority_weights=None`` -> use ``DEFAULT_AUTHORITY_WEIGHTS``
        (authority ranking ON by default - the D4 contract);
      * ``authority_weights={}``    -> disable the prior (pure BM25 order,
        used by the T-22 ablation as the no-authority arm);
      * ``per_doc_quota=None``      -> no per-document cap.
    """

    chunks: list[Chunk] = field(default_factory=list)
    authority_weights: dict[int, float] | None = None
    per_doc_quota: int | None = DEFAULT_PER_DOC_QUOTA
    _authority_weights: dict[int, float] | None = field(default=None, repr=False)
    _df: dict[str, int] = field(default_factory=dict, repr=False)
    _tf: list[dict[str, int]] = field(default_factory=list, repr=False)
    _len: list[int] = field(default_factory=list, repr=False)
    _avg_len: float = 0.0

    def __post_init__(self) -> None:
        if self.authority_weights is None:
            self._authority_weights = dict(DEFAULT_AUTHORITY_WEIGHTS)
        else:
            self._authority_weights = dict(self.authority_weights)
        self._build()

    @classmethod
    def from_documents(cls, docs: Iterable[CorpusDoc],
                       authority_weights: dict[int, float] | None = None,
                       per_doc_quota: int | None = DEFAULT_PER_DOC_QUOTA
                       ) -> Retriever:
        return cls(chunks=build_chunks(docs),
                   authority_weights=authority_weights,
                   per_doc_quota=per_doc_quota)

    @classmethod
    def from_chunks(cls, chunks: list[Chunk],
                    authority_weights: dict[int, float] | None = None,
                    per_doc_quota: int | None = DEFAULT_PER_DOC_QUOTA
                    ) -> Retriever:
        return cls(chunks=list(chunks),
                   authority_weights=authority_weights,
                   per_doc_quota=per_doc_quota)

    def _build(self) -> None:
        df: dict[str, int] = {}
        tf: list[dict[str, int]] = []
        lengths: list[int] = []
        for ch in self.chunks:
            toks = tokenize(f"{ch.title}\n{ch.text}")
            counts: dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            for term in counts:
                df[term] = df.get(term, 0) + 1
            tf.append(counts)
            lengths.append(len(toks))
        self._df = df
        self._tf = tf
        self._len = lengths
        self._avg_len = (sum(lengths) / len(lengths)) if lengths else 0.0

    def search(self, query: str, top_k: int = 5,
               splits: tuple[str, ...] | None = None) -> list[Hit]:
        """Top-k hits for ``query`` ordered by the authority-aware rank.

        ``splits`` optionally restricts to build/blind chunks (used by the
        anti-overfitting check); None searches everything.

        Ranking (T-18d): every chunk is scored by pure BM25 first; the
        authority prior is then applied multiplicatively to produce
        ``ranked_score`` (raw ``score`` is preserved for auditability), and
        the final list is a greedy pass that skips a hit when its document
        already holds ``per_doc_quota`` slots. An over-quota skip does not
        consume a slot, so short documents with no quota competitor keep
        their place - this is a deterministic, zero-dependency substitute
        for MMR diversification.
        """
        if not self.chunks:
            return []
        q_terms = tokenize(query)
        if not q_terms:
            return []
        n = len(self.chunks)
        scored: list[Hit] = []
        for i, ch in enumerate(self.chunks):
            if splits is not None and ch.split not in splits:
                continue
            counts = self._tf[i]
            length = self._len[i] or 1
            score = 0.0
            for term in q_terms:
                f = counts.get(term, 0)
                if not f:
                    continue
                dfi = self._df.get(term, 0)
                idf = math.log(1 + (n - dfi + 0.5) / (dfi + 0.5))
                denom = f + BM25_K1 * (1 - BM25_B + BM25_B * length / (self._avg_len or 1))
                score += idf * (f * (BM25_K1 + 1)) / denom
            if score > 0:
                ranked = score
                if self._authority_weights:
                    weight = self._authority_weights.get(
                        ch.authority_level, 1.0)
                    ranked = score * weight
                scored.append(Hit(chunk=ch, score=round(score, 6),
                                  ranked_score=round(ranked, 6)))
        scored.sort(key=lambda h: (h.ranked_score if h.ranked_score is not None
                                   else h.score), reverse=True)
        if self.per_doc_quota is None:
            return scored[:top_k]
        selected: list[Hit] = []
        per_doc: dict[str, int] = {}
        for hit in scored:
            if len(selected) >= top_k:
                break
            doc_id = hit.chunk.doc_id
            if per_doc.get(doc_id, 0) >= self.per_doc_quota:
                continue
            per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
            selected.append(hit)
        return selected


def retrieve_context(retriever: Retriever, question: str, top_k: int = 5,
                     max_chars: int = 4000) -> tuple[str, list[dict[str, Any]]]:
    """Render retrieved chunks into an LLM context block + evidence records.

    Returns ``(context_text, evidence)`` where evidence items carry the
    doc/title/score locators the judge and the trace metric consume. Both
    ``score`` (pure BM25) and ``ranked_score`` (post-authority-prior) are
    exposed so a downstream report can show why a hit outranked another.
    """
    hits = retriever.search(question, top_k=top_k)
    parts: list[str] = []
    evidence: list[dict[str, Any]] = []
    used = 0
    for rank, hit in enumerate(hits, start=1):
        block = f"[{rank}] 《{hit.chunk.title}》\n{hit.chunk.text}"
        if used + len(block) > max_chars and parts:
            break
        parts.append(block)
        used += len(block)
        evidence.append({
            "rank": rank,
            "doc_id": hit.chunk.doc_id,
            "title": hit.chunk.title,
            "source": hit.chunk.source,
            "chunk_idx": hit.chunk.chunk_idx,
            "score": hit.score,
            "ranked_score": hit.ranked_score if hit.ranked_score is not None
            else hit.score,
            "authority_level": hit.chunk.authority_level,
            "split": hit.chunk.split,
            "span_hash": hashlib.sha256(
                f"{hit.chunk.doc_id}:{hit.chunk.chunk_idx}:{hit.chunk.text}".encode()
            ).hexdigest()[:16],
        })
    return "\n\n".join(parts), evidence
