"""Standard-alignment service (T-21, K5 - the innovation main axis).

Implements the document change detection pipeline defined in 02-tech-plan
sections 4.1-4.3:

STEP 1  deterministic section-tree alignment (no LLM, reproducible)
STEP 2  paragraph semantic alignment (similarity matrix + optimal assignment;
        >= 0.85 direct, 0.6-0.85 LLM arbitration, < 0.6 unmatched)
STEP 3  change classification into {UNCHANGED, ADD, UPDATE, DELETE, MOVE,
        RENUMBER, SPLIT, MERGE} with proposition-level points

plus the two downstream stages:

- affected surface (4.2): changed spans -> kg_evidence_t rows -> entities
  (E_aff) -> decision cards referencing them (D_aff), served by indexes only;
- minimal sufficient update set (4.3): VOI = P(conclusion changes) x impact,
  selected greedily until the marginal gain drops below cost, reporting the
  quality-loss estimate of the excluded tail.

Honest scope notes (verified against the repository, 2026-09-20; keep these
in sync with reality rather than with the brief's shorthand):

1. The brief's phrase "evidence_span in deltaS" has no matching column.
   ``kg_evidence_t`` stores ``span_loc`` JSONB (``{chunk_idx, page, bbox?}``)
   plus ``span_text`` and ``doc_id``; the entity link is ``entity_refs``
   (an ARRAY of entity ``stable_id`` strings) - there is no ``entity_id``.
   This module therefore keys a changed span as ``(asset_no, chunk_idx)``
   and resolves it through ``doc_id`` + ``span_loc->>'chunk_idx'``.
2. ``llm_client`` exposes no embedding endpoint. STEP 2 therefore uses an
   injectable ``embed`` callable and falls back to a deterministic lexical
   similarity (``lexical_similarity``) when none is supplied. No embedding
   provider is invented.
3. There is no reusable ranking to borrow: the existing ranker
   (``ontology_service.score_formula``) is a weighted linear proposal score,
   not a VOI. VOI is implemented here for the first time.
4. Detection of SPLIT/MERGE is semantic and is only reported when the LLM
   says so; the deterministic path never claims those two labels. MOVE and
   RENUMBER are deterministic (section tree position and numbering).
5. ``kg_extract_run_t``-style bookkeeping does not apply here: this module
   writes ``doc_version_diff_t`` and ``evolution_round_t`` only. The latter
   has no ``knowledge_stamp`` column, so the stamp is carried inside
   ``ops_summary`` and that deviation is stated in the persisted payload.

Zero new dependencies: stdlib only (dataclasses, hashlib, json, math, re,
unicodedata, typing).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import Integer, cast, or_, select, tuple_

logger = logging.getLogger(__name__)

__all__ = [
    "CHANGE_TYPES",
    "HIGH_SIM",
    "LOW_SIM",
    "ROUTE_DIRECT",
    "ROUTE_LLM",
    "ROUTE_UNMATCHED",
    "UNCHANGED_SIM",
    "AffectedEntity",
    "AffectedSurface",
    "AlignmentResult",
    "AlignmentService",
    "Assignment",
    "Calibration",
    "ChangeItem",
    "DetectResult",
    "MinimalUpdateSet",
    "ParagraphPair",
    "SectionAlign",
    "SectionNode",
    "TableBlock",
    "TableChange",
    "TopicCalibration",
    "TopicGroup",
    "UpdateCandidate",
    "aggregate_change_groups",
    "align_sections",
    "assign_paragraphs",
    "calibrate_pr",
    "calibrate_topic",
    "diff_tables",
    "lexical_similarity",
    "minimal_update_set",
    "normalize_title",
    "parse_sections",
    "similarity_matrix",
    "topic_tokens",
    "voi",
]

CHANGE_TYPES = (
    "UNCHANGED",
    "ADD",
    "UPDATE",
    "DELETE",
    "MOVE",
    "RENUMBER",
    "SPLIT",
    "MERGE",
)
REAL_CHANGE_TYPES = tuple(t for t in CHANGE_TYPES if t != "UNCHANGED")

ROUTE_DIRECT = "direct"
ROUTE_LLM = "llm"
ROUTE_UNMATCHED = "unmatched"

HIGH_SIM = 0.85
LOW_SIM = 0.6
UNCHANGED_SIM = 0.98

# Assignment guard: the pure-python assignment is O(n^3); above this many
# cells we degrade to greedy pairing (documented, still deterministic).
MAX_ASSIGN_CELLS = 40000

# Markdown-ish heading or numbering prefixes, e.g. "第3章", "3.2", "3.2.1".
# The numeric token must be followed by an explicit separator (or end the
# line): without that guard ordinary body lines such as "2型糖尿病防治指南",
# "2024年指南更新" or "500mg起始剂量" would be misread as headings, which on a
# real guideline produced thousands of phantom sections (measured 2026-09-20).
_NUM_RE = re.compile(
    r"^(?P<num>(第\s*[0-9一二三四五六七八九十百]+\s*[章节篇])"
    r"|(?P<dec>\d{1,2}(?:\.\d{1,2}){0,3}))"
    r"(?=$|[\s、.．,，:：/])"
    r"\s*[、.．,，:：]?\s*(?P<title>.*)$"
)
_CN_NUM_RE = re.compile(r"^[（(]\s*[0-9一二三四五六七八九十]+\s*[)）]\s*")
_TRAILING_YEAR_RE = re.compile(r"[（(]\s*(19|20)\d{2}\s*年?[版]?\s*[)）]\s*$")
_TABLE_CAPTION_RE = re.compile(r"^\s*表\s*\d+")
_PUNCT_RE = re.compile(r"[\s\u3000,，、;；:：。.!！?？\"'“”‘’()（）\[\]【】<>《》|/\\\-—_]+")

# A small, explicit structural synonym folding table. Only clearly
# equivalent structural words are folded; anything else stays distinct so
# that "matched" never silently merges different medical concepts.
_TITLE_FOLD = {
    "总论": "概述",
    "引言": "概述",
    "前言": "概述",
    "导言": "概述",
    "诊断标准": "诊断",
    "治疗原则": "治疗",
    "防治管理": "管理",
    "治疗管理": "管理",
}


# ---------------------------------------------------------------------------
# dataclasses (frozen interface contract)
# ---------------------------------------------------------------------------


@dataclass
class TableBlock:
    """One table: caption plus rows (header row included when present)."""

    caption: str
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class SectionNode:
    """One section of a parsed document."""

    section_id: str
    number: str
    title: str
    norm_title: str
    level: int
    path: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    tables: list[TableBlock] = field(default_factory=list)


@dataclass
class SectionAlign:
    """STEP 1 output: the five section-level alignment buckets."""

    matched: list[tuple[str, str]] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    moved: list[tuple[str, str, int, int]] = field(default_factory=list)
    renumbered: list[tuple[str, str, str, str]] = field(default_factory=list)


@dataclass
class Assignment:
    """One raw assignment from the optimal matching (before thresholding)."""

    old_index: int
    new_index: int
    similarity: float


@dataclass
class ParagraphPair:
    """STEP 2 output row: one aligned (or unmatched) paragraph pair."""

    old_section_id: str | None
    new_section_id: str | None
    old_index: int | None
    new_index: int | None
    similarity: float
    route: str
    old_text: str = ""
    new_text: str = ""


@dataclass
class TableChange:
    """Deterministic table diff row (row/column hash comparison, no LLM)."""

    section_id: str
    kind: str
    rows_changed: list[int] = field(default_factory=list)
    detail: list[str] = field(default_factory=list)


@dataclass
class ChangeItem:
    """STEP 3 output: one classified change with proposition-level points.

    ``old_span`` / ``new_span`` are ``(asset_no, ordinal)`` pairs. The ordinal
    is the document paragraph ordinal produced by :func:`parse_sections`; it
    is *not* the ingest chunk index. Convert it with
    :meth:`AlignmentService.resolve_changed_spans` (which matches paragraph
    text against ``kg_evidence_t.span_text``) before calling
    :meth:`AlignmentService.affected_surface`.
    """

    change_type: str
    section_anchor: str
    old_section_id: str | None = None
    new_section_id: str | None = None
    similarity: float = 0.0
    points: list[str] = field(default_factory=list)
    source: str = "deterministic"
    old_span: tuple[str, int] | None = None
    new_span: tuple[str, int] | None = None


@dataclass
class DetectResult:
    """Whole change-detection report for one document pair."""

    old_asset_no: str
    new_asset_no: str
    sections: SectionAlign
    changes: list[ChangeItem] = field(default_factory=list)
    table_changes: list[TableChange] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class AffectedEntity:
    """One entity touched by the changed spans, with its citing cards."""

    stable_id: str
    class_ref: str = ""
    name: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    decision_card_ids: list[str] = field(default_factory=list)


@dataclass
class AffectedSurface:
    """Section 4.2 output: deltaS -> E_aff -> D_aff."""

    changed_spans: list[tuple[str, int]] = field(default_factory=list)
    entities: list[AffectedEntity] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    decision_cards: list[dict[str, Any]] = field(default_factory=list)
    index_queries: int = 0
    resolved_spans: list[tuple[str, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class UpdateCandidate:
    """One item that may need human confirmation / recomputation."""

    kind: str
    ref_id: str
    label: str
    p_change: float
    impact: int
    voi: float


@dataclass
class MinimalUpdateSet:
    """Section 4.3 output: the selected set plus the excluded-tail loss."""

    selected: list[UpdateCandidate] = field(default_factory=list)
    excluded: list[UpdateCandidate] = field(default_factory=list)
    loss_estimate: float = 0.0
    epsilon: float = 0.0


@dataclass
class Calibration:
    """Precision/recall against the gold seed.

    ``gold_total`` counts the *evaluable* gold rows (verified/corrected);
    ``unverified_excluded`` counts the rows left out of both numerator and
    denominator, so the two together are the raw gold size.
    """

    precision: float | None
    recall: float | None
    matched: int
    gold_total: int
    machine_total: int
    unverified_excluded: int


@dataclass
class AlignmentResult:
    """Top-level result of one alignment run."""

    detect: DetectResult
    affected: AffectedSurface | None = None
    update_set: MinimalUpdateSet | None = None
    calibration: Calibration | None = None
    topic_calibration: TopicCalibration | None = None
    persisted: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# STEP 1 - deterministic section tree alignment
# ---------------------------------------------------------------------------


def normalize_title(raw: str) -> str:
    """Normalise a heading for matching (deterministic, no LLM).

    Strips numbering ("3.2", "第三章", "(一)"), folds full-width to
    half-width, drops punctuation/whitespace, and folds a small set of
    structural synonyms. Two headings that mean the same thing in the
    standard's own numbering scheme must map to the same key.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw).strip()
    text = _TRAILING_YEAR_RE.sub("", text)
    text = _CN_NUM_RE.sub("", text)
    match = _NUM_RE.match(text)
    if match:
        text = match.group("title") or ""
    text = _PUNCT_RE.sub("", text)
    text = text.lower()
    for src, dst in _TITLE_FOLD.items():
        if text == src:
            return dst
    return text


def _heading_number(line: str) -> tuple[str, str, int] | None:
    """Return (number, title, level) when the line looks like a heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return None
    if stripped.endswith(("。", "；", ";", "，", ",")):
        return None
    match = _NUM_RE.match(stripped)
    if not match:
        return None
    num = re.sub(r"\s+", "", match.group("num"))
    title = (match.group("title") or "").strip()
    if title[:1].isdigit():
        return None
    if match.group("dec"):
        level = num.count(".") + 1
    else:
        level = 1
        if not title:
            return None
    if not title and level > 1:
        return None
    return num, title, min(level, 4)


def _extract_tables(lines: Sequence[str], start: int) -> tuple[list[TableBlock], int]:
    """Collect table blocks starting at ``start`` (caption line "表 N")."""
    caption = lines[start].strip()
    rows: list[list[str]] = []
    index = start + 1
    while index < len(lines):
        raw = lines[index].rstrip()
        if not raw.strip():
            break
        cells = _split_table_row(raw)
        if len(cells) < 2:
            break
        rows.append(cells)
        index += 1
    if not rows:
        return [], start + 1
    return [TableBlock(caption=caption, rows=rows)], index


def _split_table_row(raw: str) -> list[str]:
    """Split a table row on tabs, pipes or runs of 2+ spaces."""
    line = raw.strip()
    if "|" in line:
        return [c.strip() for c in line.strip("|").split("|")]
    if "\t" in line:
        return [c.strip() for c in line.split("\t") if c.strip() != ""]
    parts = re.split(r"\s{2,}", line)
    return [p.strip() for p in parts if p.strip() != ""]


def parse_sections(text: str) -> list[SectionNode]:
    """Parse plain document text into a flat list of sections.

    Heading detection is heuristic (numbering prefix + length/terminator
    guards); anything that cannot be recognised stays inside the current
    section's paragraphs rather than being invented as a section. Tables are
    recognised from a "表 N" caption followed by delimiter-separated rows.
    """
    if not text:
        return []
    lines = text.splitlines()
    nodes: list[SectionNode] = []
    stack: list[tuple[int, str, str]] = []

    current_number = ""
    current_title = "(preamble)"
    current_level = 0
    buffer: list[str] = []
    tables: list[TableBlock] = []
    seen_ids: dict[str, int] = {}

    def flush() -> None:
        nonlocal buffer, tables
        paragraphs = _split_paragraphs(buffer)
        if current_level == 0 and not paragraphs and not tables:
            return
        path = (
            [norm for _, _, norm in stack]
            if stack
            else [normalize_title(current_title)]
        )
        segments = [
            f"{num}|{norm}" if num else norm for _, num, norm in stack
        ]
        base = ">".join([seg for seg in segments if seg]) or normalize_title(
            current_title
        )
        base = base or "preamble"
        count = seen_ids.get(base, 0) + 1
        seen_ids[base] = count
        section_id = base if count == 1 else f"{base}#{count}"
        nodes.append(
            SectionNode(
                section_id=section_id,
                number=current_number,
                title=current_title,
                norm_title=normalize_title(current_title),
                level=current_level or 1,
                path=list(path),
                paragraphs=paragraphs,
                tables=tables,
            )
        )
        buffer = []
        tables = []

    index = 0
    while index < len(lines):
        line = lines[index]
        heading = _heading_number(line)
        if heading is not None:
            flush()
            number, title, level = heading
            while stack and stack[-1][0] >= level:
                stack.pop()
            norm = normalize_title(title) or normalize_title(number)
            stack.append((level, number, norm))
            current_number, current_title, current_level = number, title, level
            index += 1
            continue
        if _TABLE_CAPTION_RE.match(line):
            found, nxt = _extract_tables(lines, index)
            if found:
                tables.extend(found)
                index = nxt
                continue
        buffer.append(line)
        index += 1
    flush()
    return nodes


def _split_paragraphs(lines: Sequence[str]) -> list[str]:
    """Group non-empty line runs into paragraph blocks."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line.strip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return [" ".join(b) for b in blocks if "".join(b).strip()]


def _section_similarity(left: SectionNode, right: SectionNode) -> float:
    if left.norm_title and left.norm_title == right.norm_title:
        return 1.0
    title_sim = lexical_similarity(left.norm_title, right.norm_title)
    path_sim = lexical_similarity(
        "|".join(left.path), "|".join(right.path)
    )
    return max(title_sim, 0.5 * title_sim + 0.5 * path_sim)


def align_sections(
    old: Sequence[SectionNode], new: Sequence[SectionNode]
) -> SectionAlign:
    """Align two section trees deterministically.

    Matching is exact-normalised-title first, then optimal assignment over
    the remaining similarity matrix. Position changes become ``moved`` rows
    and numbering-only changes become ``renumbered`` rows; unmatched
    sections are reported as added/deleted. Same input, same output.
    """
    result = SectionAlign()
    old_pos = {node.section_id: i for i, node in enumerate(old)}
    new_pos = {node.section_id: i for i, node in enumerate(new)}
    used_old: set[int] = set()
    used_new: set[int] = set()

    for i, left in enumerate(old):
        for j, right in enumerate(new):
            if j in used_new or not left.norm_title:
                continue
            if left.norm_title == right.norm_title:
                result.matched.append((left.section_id, right.section_id))
                used_old.add(i)
                used_new.add(j)
                break

    rest_old = [i for i in range(len(old)) if i not in used_old]
    rest_new = [j for j in range(len(new)) if j not in used_new]
    if rest_old and rest_new:
        matrix = [
            [_section_similarity(old[i], new[j]) for j in rest_new]
            for i in rest_old
        ]
        for assignment in assign(matrix, high=0.75, low=0.75):
            if assignment.similarity < 0.75:
                continue
            left = old[rest_old[assignment.old_index]]
            right = new[rest_new[assignment.new_index]]
            result.matched.append((left.section_id, right.section_id))
            used_old.add(rest_old[assignment.old_index])
            used_new.add(rest_new[assignment.new_index])

    for i, left in enumerate(old):
        if i not in used_old:
            result.deleted.append(left.section_id)
    for j, right in enumerate(new):
        if j not in used_new:
            result.added.append(right.section_id)

    old_by_id = {n.section_id: n for n in old}
    new_by_id = {n.section_id: n for n in new}
    for old_id, new_id in result.matched:
        left, right = old_by_id[old_id], new_by_id[new_id]
        i, j = old_pos[old_id], new_pos[new_id]
        if left.number != right.number and left.norm_title == right.norm_title:
            result.renumbered.append(
                (old_id, new_id, left.number, right.number)
            )
        if i != j:
            result.moved.append((old_id, new_id, i, j))
    return result


# ---------------------------------------------------------------------------
# similarity + optimal assignment (STEP 2 machinery)
# ---------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    """Deterministic token set: CJK bigrams plus latin/digit words."""
    normalised = unicodedata.normalize("NFKC", text or "").lower()
    words = set(re.findall(r"[a-z0-9]+", normalised))
    cjk = re.findall(r"[\u4e00-\u9fff]", normalised)
    words.update("".join(pair) for pair in itertools.pairwise(cjk))
    return words


def _trigrams(text: str) -> set[str]:
    """Deterministic character 3-grams over the alphanumeric skeleton."""
    normalised = unicodedata.normalize("NFKC", text or "").lower()
    skeleton = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalised)
    if len(skeleton) < 3:
        return {skeleton} if skeleton else set()
    return {skeleton[i : i + 3] for i in range(len(skeleton) - 2)}


def _set_cosine(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    shared = len(a & b)
    if not shared:
        return 0.0
    return shared / math.sqrt(len(a) * len(b))


def lexical_similarity(left: str, right: str) -> float:
    """Cosine similarity over deterministic lexical features (0.0 - 1.0).

    Two feature sets are compared and the better one wins: CJK bigrams plus
    latin words (order-insensitive, good for reordered wording), and
    character 3-grams (order-sensitive, good for near-identical sentences).
    No embedding provider is required, so STEP 2 stays reproducible offline;
    an injected ``embed`` callable replaces this when one is available.
    """
    return max(
        _set_cosine(_tokens(left), _tokens(right)),
        _set_cosine(_trigrams(left), _trigrams(right)),
    )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    na = math.sqrt(sum(a * a for a in left))
    nb = math.sqrt(sum(b * b for b in right))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def similarity_matrix(
    old_texts: Sequence[str],
    new_texts: Sequence[str],
    embed: Callable[[Sequence[str]], Sequence[Sequence[float]]] | None = None,
) -> list[list[float]]:
    """Pairwise paragraph similarity, optional embedding provider.

    ``embed`` is an injected callable (no embedding endpoint is wired in
    ``llm_client``); when it is None the deterministic lexical similarity is
    used so STEP 2 stays reproducible offline.
    """
    if embed is not None:
        old_vecs = list(embed(list(old_texts)))
        new_vecs = list(embed(list(new_texts)))
        return [[_cosine(a, b) for b in new_vecs] for a in old_vecs]
    return [
        [lexical_similarity(a, b) for b in new_texts] for a in old_texts
    ]


def assign(
    sims: Sequence[Sequence[float]],
    *,
    high: float = HIGH_SIM,
    low: float = LOW_SIM,
) -> list[Assignment]:
    """Optimal one-to-one assignment over a similarity matrix.

    Maximises total similarity (Hungarian algorithm, pure stdlib). Pairs
    below ``low`` are dropped; routing (``direct`` / ``llm``) is applied by
    :func:`assign_paragraphs`. Above :data:`MAX_ASSIGN_CELLS` a greedy
    fallback keeps runtime bounded (still deterministic).
    """
    rows = len(sims)
    cols = len(sims[0]) if rows else 0
    if rows == 0 or cols == 0:
        return []
    if rows * cols > MAX_ASSIGN_CELLS:
        return _greedy_assign(sims, low)
    size = max(rows, cols)
    cost = [[0.0] * size for _ in range(size)]
    for i in range(rows):
        for j in range(cols):
            cost[i][j] = -float(sims[i][j])
    out: list[Assignment] = []
    for i, j in _hungarian(cost):
        if i >= rows or j >= cols:
            continue
        sim = float(sims[i][j])
        if sim < low:
            continue
        out.append(Assignment(old_index=i, new_index=j, similarity=sim))
    return out


def _greedy_assign(
    sims: Sequence[Sequence[float]], low: float
) -> list[Assignment]:
    pairs = sorted(
        (
            (float(sims[i][j]), i, j)
            for i in range(len(sims))
            for j in range(len(sims[i]))
        ),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    used_old: set[int] = set()
    used_new: set[int] = set()
    out: list[Assignment] = []
    for sim, i, j in pairs:
        if sim < low or i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        out.append(Assignment(old_index=i, new_index=j, similarity=sim))
    return out


def _hungarian(cost: Sequence[Sequence[float]]) -> list[tuple[int, int]]:
    """Classic O(n^3) min-cost assignment for a square matrix."""
    n = len(cost)
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = -1
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    return [(p[j] - 1, j - 1) for j in range(1, n + 1) if p[j] != 0]


def assign_paragraphs(
    sims: Sequence[Sequence[float]],
    *,
    high: float = HIGH_SIM,
    low: float = LOW_SIM,
    old_texts: Sequence[str] | None = None,
    new_texts: Sequence[str] | None = None,
    old_section_id: str | None = None,
    new_section_id: str | None = None,
) -> list[ParagraphPair]:
    """Route paragraph pairs into direct / llm / unmatched buckets.

    Every old and every new paragraph appears exactly once in the output:
    assigned pairs carry the similarity and route, the rest are reported as
    unmatched rows (which STEP 3 turns into DELETE / ADD).
    """
    old_texts = list(old_texts or [])
    new_texts = list(new_texts or [])
    # The matrix shape is the source of truth for indices; the text lists are
    # optional payload, so an unmatched row is still reported when no texts
    # were supplied (tests and callers with pre-computed similarities).
    row_count = len(sims)
    col_count = len(sims[0]) if row_count else 0
    pairs: list[ParagraphPair] = []
    used_old: set[int] = set()
    used_new: set[int] = set()
    for item in assign(sims, high=high, low=low):
        route = ROUTE_DIRECT if item.similarity >= high else ROUTE_LLM
        pairs.append(
            ParagraphPair(
                old_section_id=old_section_id,
                new_section_id=new_section_id,
                old_index=item.old_index,
                new_index=item.new_index,
                similarity=item.similarity,
                route=route,
                old_text=(
                    old_texts[item.old_index]
                    if item.old_index < len(old_texts)
                    else ""
                ),
                new_text=(
                    new_texts[item.new_index]
                    if item.new_index < len(new_texts)
                    else ""
                ),
            )
        )
        used_old.add(item.old_index)
        used_new.add(item.new_index)
    for i in range(max(row_count, len(old_texts))):
        if i not in used_old:
            pairs.append(
                ParagraphPair(
                    old_section_id=old_section_id,
                    new_section_id=None,
                    old_index=i,
                    new_index=None,
                    similarity=0.0,
                    route=ROUTE_UNMATCHED,
                    old_text=old_texts[i] if i < len(old_texts) else "",
                )
            )
    for j in range(max(col_count, len(new_texts))):
        if j not in used_new:
            pairs.append(
                ParagraphPair(
                    old_section_id=None,
                    new_section_id=new_section_id,
                    old_index=None,
                    new_index=j,
                    similarity=0.0,
                    route=ROUTE_UNMATCHED,
                    new_text=new_texts[j] if j < len(new_texts) else "",
                )
            )
    return pairs


# ---------------------------------------------------------------------------
# deterministic table diff
# ---------------------------------------------------------------------------


def _normalise_cell(cell: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", cell or "")).lower()


def _row_hash(row: Sequence[str]) -> str:
    payload = "\u0001".join(_normalise_cell(c) for c in row)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# Deterministic table-matching thresholds (zero LLM). Measured against the
# 2020/2024 guideline pair (2026-09-20): the same table reworded across
# versions keeps a header-token Jaccard >= 0.6 and a body-token Jaccard
# >= 0.6, while genuinely different tables stay far below both (e.g. the
# DR-grading and DME-grading tables at 0.50/0.17).
TABLE_COL_JACCARD_FLOOR = 0.6   # header token-set overlap for "same columns"
TABLE_NCOLS_TOLERANCE = 2       # |old_cols - new_cols| (column merge/split)
TABLE_SECTION_SIM_FLOOR = 0.75  # section context similarity that confirms
TABLE_CONTENT_FLOOR = 0.6       # body token-set overlap that confirms content

# Deterministic tokenisation: any CJK run or latin/digit word >= 2 chars.
_CELL_TOKEN_RE = re.compile(r"[^\w\u4e00-\u9fff]+")


def _cell_tokens(cell: str) -> set[str]:
    """Normalized tokens of one cell (the column-signature feature space)."""
    norm = _normalise_cell(cell)
    tokens: set[str] = set()
    for token in _CELL_TOKEN_RE.split(norm):
        if token and len(token) > 1:
            tokens.add(token)
    return tokens


def _column_signature(table: TableBlock) -> tuple[frozenset[str], int]:
    """Deterministic column signature: header-row token set + column count."""
    if not table.rows:
        return frozenset(), 0
    tokens: set[str] = set()
    for cell in table.rows[0]:
        tokens.update(_cell_tokens(cell))
    return frozenset(tokens), len(table.rows[0])


def _body_tokens(table: TableBlock) -> frozenset[str]:
    """Deterministic content signature over the non-header cells."""
    tokens: set[str] = set()
    for row in table.rows[1:]:
        for cell in row:
            tokens.update(_cell_tokens(cell))
    return frozenset(tokens)


def _set_jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _pad_contexts(
    contexts: Sequence[str] | None, size: int
) -> list[str]:
    """Normalise a parallel section-context list to one entry per table."""
    if not contexts:
        return [""] * size
    return [str(c) for c in contexts[:size]] + [""] * max(0, size - len(contexts))


def diff_tables(
    old_tables: Sequence[TableBlock],
    new_tables: Sequence[TableBlock],
    *,
    old_sections: Sequence[str] | None = None,
    new_sections: Sequence[str] | None = None,
) -> list[TableChange]:
    """Deterministic table comparison by row hash (zero LLM, reproducible).

    Tables are matched on ``(section context, column signature)`` rather
    than on caption text, so a table whose caption was reworded between
    versions is still recognised as the same table. The column signature is
    the header row's normalized token set plus the column count; the row
    hash diff (``_row_hash``) below is unchanged.

    Matching is a deterministic four-tier cascade (the same input always
    pairs the same tables):

    0. caption  - identical normalized caption (a table keeps its own name;
       this is the strongest signal and restores caption behaviour for the
       many tables whose titles did not change between versions);
    1. exact  - equal ``(section context, header tokens, column count)``;
    2. tolerant - same section context, header token Jaccard >=
       ``TABLE_COL_JACCARD_FLOOR`` and ``|d column count| <=
       TABLE_NCOLS_TOLERANCE`` (covers column renames and merge/split);
    3. content-confirmed - unmatched leftovers whose columns still overlap
       at the floor are paired when the section titles are similar (>=
       ``TABLE_SECTION_SIM_FLOOR``) *or* the non-header cell tokens overlap
       at ``TABLE_CONTENT_FLOOR``. This tier exists because on the real
       2020/2024 text the parsed section titles are polluted by the PDF
       layout, so section-context equality alone cannot confirm cross-version
       pairs; body-content overlap is the deterministic proof that they are
       the same table (never a caption-less guess).

    ``old_sections`` / ``new_sections`` are parallel lists of section
    contexts (normalized title of the section holding each table). They are
    optional for backward compatibility; when omitted every table gets an
    empty context and matching falls back to column signature alone.

    Output keeps the frozen ``TableChange`` contract
    (``section_id/kind/rows_changed/detail``).
    """
    changes: list[TableChange] = []
    n_old, n_new = len(old_tables), len(new_tables)
    old_ctx = _pad_contexts(old_sections, n_old)
    new_ctx = _pad_contexts(new_sections, n_new)
    old_sig = [_column_signature(t) for t in old_tables]
    new_sig = [_column_signature(t) for t in new_tables]

    matches: dict[int, int] = {}
    used_old: set[int] = set()
    used_new: set[int] = set()

    # Tier 0: identical normalized caption (the table kept its own name).
    new_by_caption: dict[str, list[int]] = {}
    for j, table in enumerate(new_tables):
        key = normalize_title(table.caption) or table.caption
        new_by_caption.setdefault(key, []).append(j)
    for i, table in enumerate(old_tables):
        key = normalize_title(table.caption) or table.caption
        for j in new_by_caption.get(key, ()):
            if j not in used_new:
                matches[i] = j
                used_old.add(i)
                used_new.add(j)
                break

    # Tier 1: exact (section context, header tokens, column count).
    new_by_key: dict[tuple[Any, Any, int], list[int]] = {}
    for j, (ctx, (tokens, ncols)) in enumerate(zip(new_ctx, new_sig)):
        new_by_key.setdefault((ctx, tokens, ncols), []).append(j)
    for i, (ctx, (tokens, ncols)) in enumerate(zip(old_ctx, old_sig)):
        for j in new_by_key.get((ctx, tokens, ncols), ()):
            if j not in used_new:
                matches[i] = j
                used_old.add(i)
                used_new.add(j)
                break

    # Tier 2: same section context, column-tolerant.
    if n_old - len(used_old) and n_new - len(used_new):
        candidates: list[tuple[float, int, int]] = []
        for i in range(n_old):
            if i in used_old:
                continue
            tokens_i, ncols_i = old_sig[i]
            for j in range(n_new):
                if j in used_new or new_ctx[j] != old_ctx[i]:
                    continue
                tokens_j, ncols_j = new_sig[j]
                if abs(ncols_i - ncols_j) > TABLE_NCOLS_TOLERANCE:
                    continue
                sim = _set_jaccard(tokens_i, tokens_j)
                if sim >= TABLE_COL_JACCARD_FLOOR:
                    candidates.append((sim, i, j))
        for _, i, j in sorted(candidates, key=lambda x: (-x[0], x[1], x[2])):
            if i in used_old or j in used_new:
                continue
            matches[i] = j
            used_old.add(i)
            used_new.add(j)

    # Tier 3: cross-section, content-confirmed.
    if n_old - len(used_old) and n_new - len(used_new):
        old_body = [_body_tokens(t) for t in old_tables]
        new_body = [_body_tokens(t) for t in new_tables]
        candidates = []
        for i in range(n_old):
            if i in used_old:
                continue
            tokens_i, ncols_i = old_sig[i]
            for j in range(n_new):
                if j in used_new:
                    continue
                tokens_j, ncols_j = new_sig[j]
                if abs(ncols_i - ncols_j) > TABLE_NCOLS_TOLERANCE:
                    continue
                if _set_jaccard(tokens_i, tokens_j) < TABLE_COL_JACCARD_FLOOR:
                    continue
                section_sim = lexical_similarity(old_ctx[i], new_ctx[j])
                content_sim = _set_jaccard(old_body[i], new_body[j])
                if (
                    section_sim >= TABLE_SECTION_SIM_FLOOR
                    or content_sim >= TABLE_CONTENT_FLOOR
                ):
                    candidates.append(
                        (max(section_sim, content_sim), i, j)
                    )
        for _, i, j in sorted(
            candidates, key=lambda x: (-x[0], x[1], x[2])
        ):
            if i in used_old or j in used_new:
                continue
            matches[i] = j
            used_old.add(i)
            used_new.add(j)

    # Row-level diff for every matched pair, then unmatched tables as ADD /
    # DELETE. Iteration order is stable: new tables first (row diffs and
    # ADDs in new-document order), then old-only tables as DELETEs.
    for j, new_table in enumerate(new_tables):
        i = next((o for o, m in matches.items() if m == j), None)
        if i is None:
            changes.append(
                TableChange(
                    section_id=normalize_title(new_table.caption)
                    or new_table.caption,
                    kind="ADD",
                    rows_changed=list(range(len(new_table.rows))),
                    detail=[f"table added: {new_table.caption}"],
                )
            )
            continue
        old_table = old_tables[i]
        old_hashes = [_row_hash(r) for r in old_table.rows]
        new_hashes = [_row_hash(r) for r in new_table.rows]
        if old_hashes == new_hashes:
            continue
        old_set, new_set = set(old_hashes), set(new_hashes)
        added = [i for i, digest in enumerate(new_hashes) if digest not in old_set]
        removed = [i for i, digest in enumerate(old_hashes) if digest not in new_set]
        both = sorted(set(added) & set(removed))
        added_only = [i for i in added if i not in removed]
        removed_only = [i for i in removed if i not in added]
        if both:
            kind = "UPDATE"
            rows_changed = sorted(set(both) | set(added_only) | set(removed_only))
            detail = [
                *(f"row {i}: changed" for i in both),
                *(f"row {i}: added" for i in added_only),
                *(f"row {i}: removed" for i in removed_only),
            ]
        elif added_only and not removed_only:
            kind = "ADD"
            rows_changed = added_only
            detail = [f"row {i}: added" for i in added_only]
        elif removed_only and not added_only:
            kind = "DELETE"
            rows_changed = removed_only
            detail = [f"row {i}: removed" for i in removed_only]
        else:
            kind = "UPDATE"
            rows_changed = sorted(set(added_only) | set(removed_only))
            detail = [
                *(f"row {i}: added" for i in added_only),
                *(f"row {i}: removed" for i in removed_only),
            ]
        changes.append(
            TableChange(
                section_id=normalize_title(new_table.caption)
                or new_table.caption,
                kind=kind,
                rows_changed=rows_changed,
                detail=detail,
            )
        )
    for i, old_table in enumerate(old_tables):
        if i in used_old:
            continue
        changes.append(
            TableChange(
                section_id=normalize_title(old_table.caption)
                or old_table.caption,
                kind="DELETE",
                rows_changed=list(range(len(old_table.rows))),
                detail=[f"table removed: {old_table.caption}"],
            )
        )
    return changes


# ---------------------------------------------------------------------------
# VOI + minimal sufficient update set (section 4.3)
# ---------------------------------------------------------------------------


def voi(p_change: float, impact: int) -> float:
    """value-of-information = P(conclusion changes) x impact (citation count)."""
    p = max(0.0, min(1.0, float(p_change)))
    return p * max(0, int(impact))


def minimal_update_set(
    candidates: Sequence[UpdateCandidate],
    *,
    epsilon: float,
    cost: float = 1.0,
) -> MinimalUpdateSet:
    """Greedy VOI selection of the minimal sufficient update set.

    Candidates are visited in descending VOI order; an item is selected while
    its VOI covers ``cost`` and the remaining unselected mass still exceeds
    ``epsilon``. Everything not selected is reported, and the loss estimate
    is the summed VOI of that excluded tail (never silently dropped).
    """
    ordered = sorted(candidates, key=lambda c: (-c.voi, c.ref_id))
    remaining = sum(c.voi for c in ordered)
    selected: list[UpdateCandidate] = []
    excluded: list[UpdateCandidate] = []
    for item in ordered:
        if remaining <= epsilon:
            excluded.append(item)
            continue
        if item.voi >= cost:
            selected.append(item)
            remaining -= item.voi
        else:
            excluded.append(item)
    return MinimalUpdateSet(
        selected=selected,
        excluded=excluded,
        loss_estimate=sum(c.voi for c in excluded),
        epsilon=epsilon,
    )


# ---------------------------------------------------------------------------
# P/R calibration against the gold seed
# ---------------------------------------------------------------------------


def _gold_key(change_type: str, anchor: str) -> tuple[str, str]:
    return (change_type.upper(), normalize_title(anchor))


def calibrate_pr(
    machine: Sequence[ChangeItem], gold: Sequence[dict[str, Any]]
) -> Calibration:
    """Precision/recall against a gold seed, excluding unverified rows.

    Gold rows whose ``status`` is not ``verified``/``corrected`` are counted
    in ``unverified_excluded`` and take no part in either the numerator or
    the denominator - an unverified row must never inflate the numbers.
    Machine rows labelled UNCHANGED are not changes and are excluded too.
    """
    machine_real = [c for c in machine if c.change_type != "UNCHANGED"]
    eligible = [
        g
        for g in gold
        if str(g.get("status", "")).lower() in ("verified", "corrected")
    ]
    unverified = len(gold) - len(eligible)
    remaining = {
        _gold_key(str(g.get("change_type", "")), str(g.get("section_anchor", "")))
        for g in eligible
    }
    matched = 0
    for item in machine_real:
        key = _gold_key(item.change_type, item.section_anchor)
        if key in remaining:
            remaining.discard(key)
            matched += 1
    # With no evaluable gold row there is nothing to measure against: report
    # None rather than 0.0, which would read as "precision is zero".
    precision = (matched / len(machine_real)) if (machine_real and eligible) else None
    recall = (matched / len(eligible)) if eligible else None
    return Calibration(
        precision=precision,
        recall=recall,
        matched=matched,
        gold_total=len(eligible),
        machine_total=len(machine_real),
        unverified_excluded=unverified,
    )


# ---------------------------------------------------------------------------
# Topic-level calibration (T-21 calibration refactor)
# ---------------------------------------------------------------------------

_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")


def topic_tokens(text: str | None) -> set[str]:
    """Topic-signal tokens for gold<->machine matching (deterministic).

    ASCII words of length >= 2 are kept as written. CJK has no delimiter, so
    a run is shingled into character trigrams: tokenising a CJK run as one
    token made a whole clause a single un-matchable unit (the r14 calibration
    bug), while a segmenter would add a dependency. Trigrams are the smallest
    shingle that still discriminates topics ("控制目" vs "代谢手") without
    one. Nothing here reads the gold seed, so the same text always yields the
    same tokens.
    """
    if not text:
        return set()
    tokens: set[str] = {w.lower() for w in _ASCII_WORD_RE.findall(text)}
    for run in _CJK_RUN_RE.findall(text):
        for i in range(len(run) - 2):
            tokens.add(run[i : i + 3])
    return tokens


@dataclass
class TopicGroup:
    """Machine change items collapsed by (change_type, normalised section)."""

    change_type: str
    section_anchor: str
    count: int = 0
    tokens: set[str] = field(default_factory=set)


@dataclass
class TopicCalibration:
    """Topic-level P/R for a topical, non-exhaustive gold seed.

    The gold seed lists *topics* that changed between two document versions,
    while the detector emits *paragraph-level* items. Joining them item by
    item is a granularity mismatch that measures neither side: on the real
    691-item run it produced precision=0.00145 / recall=0.111, numbers driven
    by the 691x9 denominator mismatch rather than by detection quality.

    This calibration collapses the machine side into (change_type, section)
    groups and matches gold topics to groups on discriminative-token overlap,
    so both sides are compared at topic level:

    * ``recall`` - share of evaluable gold topics with >= 1 matching group.
    * ``precision_lower_bound`` - share of machine groups matching some gold
      topic. The gold seed is a curated sample, not an exhaustive annotation
      of the diff, so a group with no gold counterpart is not necessarily
      wrong; this number is a **lower bound** and must be quoted as one.

    Honesty rules are those of :func:`calibrate_pr`: rows without a
    ``verified``/``corrected`` verdict take part in neither numerator nor
    denominator, and UNCHANGED items are not changes.
    """

    recall: float | None
    precision_lower_bound: float | None
    matched_topics: int
    gold_total: int
    matched_groups: int
    machine_groups: int
    machine_total: int
    unverified_excluded: int
    max_df_fraction: float
    min_shared_tokens: int
    topic_match_counts: dict[str, int] = field(default_factory=dict)


def aggregate_change_groups(machine: Sequence[ChangeItem]) -> list[TopicGroup]:
    """Collapse paragraph-level changes into (change_type, section) groups.

    One section commonly carries many paragraph items (an ADD/DELETE pair per
    edited paragraph); the gold seed describes one change per topic, so the
    groups are the comparable unit.
    """
    buckets: dict[tuple[str, str], TopicGroup] = {}
    for item in machine:
        if item.change_type == "UNCHANGED":
            continue
        key = (item.change_type, normalize_title(item.section_anchor))
        group = buckets.get(key)
        if group is None:
            group = TopicGroup(
                change_type=item.change_type,
                section_anchor=item.section_anchor,
            )
            buckets[key] = group
        group.count += 1
        group.tokens |= topic_tokens(item.section_anchor)
        for point in item.points:
            group.tokens |= topic_tokens(point)
    return list(buckets.values())


def calibrate_topic(
    machine: Sequence[ChangeItem],
    gold: Sequence[dict[str, Any]],
    *,
    max_df_fraction: float = 0.05,
    min_shared_tokens: int = 2,
) -> TopicCalibration:
    """Topic-level P/R with discriminative-token matching.

    A gold topic matches a machine group when they share at least
    ``min_shared_tokens`` *discriminative* tokens - tokens appearing in more
    than ``max_df_fraction`` of the groups are section boilerplate ("糖尿病",
    "治疗") and cannot carry a match on their own.

    Sensitivity on the real 691-item run (for the record, not a guarantee):
    recall is 9/9 for df fractions 0.05-1.0 with ``min_shared_tokens=2``,
    but drops to 7/9 already at df=0.02 and to 3/9 at ``min_shared_tokens=3``
    - the defaults sit on a plateau, not a cliff, but the plateau has edges.
    Negative controls must be chosen from topics that do not occur anywhere
    in the diff: a topic whose text genuinely appears in the corpus matches
    whatever the gold seed says (the gold seed is non-exhaustive, so this is
    expected, not a defect). Both parameters are reported on the result so
    the number can be reproduced or challenged.
    """
    if not (0.0 <= max_df_fraction <= 1.0):
        raise ValueError(f"max_df_fraction must be in [0, 1], got {max_df_fraction!r}")
    if min_shared_tokens < 1:
        raise ValueError(
            f"min_shared_tokens must be >= 1, got {min_shared_tokens!r}"
        )
    groups = aggregate_change_groups(machine)
    eligible = [
        g
        for g in gold
        if str(g.get("status", "")).lower() in ("verified", "corrected")
    ]
    unverified = len(gold) - len(eligible)
    matched_groups: set[int] = set()
    match_counts: dict[str, int] = {}
    matched_topics = 0

    if groups and eligible:
        df: dict[str, int] = {}
        for group in groups:
            for token in group.tokens:
                df[token] = df.get(token, 0) + 1
        df_limit = max(3, int(len(groups) * max_df_fraction))
        for row in eligible:
            gold_tokens = topic_tokens(
                str(row.get("section_anchor", ""))
            ) | topic_tokens(str(row.get("field", "") or ""))
            hits = 0
            for index, group in enumerate(groups):
                shared = {
                    token
                    for token in (gold_tokens & group.tokens)
                    if df.get(token, 0) <= df_limit
                }
                if len(shared) >= min_shared_tokens:
                    hits += 1
                    matched_groups.add(index)
            match_counts[str(row.get("id") or row.get("section_anchor") or "")] = hits
            if hits:
                matched_topics += 1

    # With no evaluable gold row there is nothing to match against: both
    # rates are not measurable and must be None, not 0.0 - mirroring
    # calibrate_pr, and never implying a measured (zero) precision.
    return TopicCalibration(
        recall=(matched_topics / len(eligible)) if eligible else None,
        precision_lower_bound=(
            (len(matched_groups) / len(groups)) if (groups and eligible) else None
        ),
        matched_topics=matched_topics,
        gold_total=len(eligible),
        matched_groups=len(matched_groups),
        machine_groups=len(groups),
        machine_total=sum(g.count for g in groups),
        unverified_excluded=unverified,
        max_df_fraction=max_df_fraction,
        min_shared_tokens=min_shared_tokens,
        topic_match_counts=match_counts,
    )


# ---------------------------------------------------------------------------
# LLM verdicts for STEP 3
# ---------------------------------------------------------------------------


def _parse_verdict(text: Any) -> tuple[str, list[str], float] | None:
    """Parse the align verdict; accepts a dict or a raw JSON string.

    The platform LLM contract for ``kind="align"`` returns an already-parsed
    dict (see ``_EchoLLM`` / the ingest driver's str->JSON adapter), while a
    raw ``LlmRouter`` call returns text. Both shapes are handled; anything
    unusable returns None so the caller degrades instead of fabricating.
    """
    if isinstance(text, dict):
        data = text
    else:
        if not isinstance(text, str) or not text.strip():
            return None
        body = text.strip()
        if body.startswith("```"):
            body = body.strip("`")
            if body.lower().startswith("json"):
                body = body[4:]
        start, end = body.find("{"), body.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(body[start : end + 1])
        except (ValueError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    label = str(data.get("label", "")).upper()
    if label not in CHANGE_TYPES:
        return None
    points = [str(p) for p in (data.get("points") or []) if str(p).strip()]
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return label, points, confidence


def _deterministic_label(pair: ParagraphPair) -> str:
    """Fallback label used when no LLM verdict is available.

    Only the labels the deterministic pipeline can actually justify are
    produced here; SPLIT/MERGE are never claimed without an LLM verdict.
    """
    if pair.route == ROUTE_UNMATCHED:
        return "ADD" if pair.old_index is None else "DELETE"
    if pair.similarity >= UNCHANGED_SIM:
        return "UNCHANGED"
    return "UPDATE"


# ---------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------


class AlignmentService:
    """Document alignment + affected surface + minimal update set.

    The DB touch points are deliberately narrow: ``affected_surface`` issues
    the two index-served queries of section 4.2, and ``persist`` writes one
    ``doc_version_diff_t`` row plus one ``evolution_round_t`` row. Sessions
    come from ``database.knowevo_db._get_db_session`` unless a
    ``session_factory`` is injected (tests use that seam).
    """

    def __init__(
        self,
        tenant_id: str,
        llm: Any | None = None,
        embed: Callable[[Sequence[str]], Sequence[Sequence[float]]] | None = None,
        session_factory: Callable[[], Any] | None = None,
        *,
        lang: str = "zh",
        high_sim: float = HIGH_SIM,
        low_sim: float = LOW_SIM,
        max_llm_calls: int = 200,
    ) -> None:
        self.tenant_id = tenant_id
        self.llm = llm
        self.embed = embed
        self.lang = lang
        self.high_sim = high_sim
        self.low_sim = low_sim
        self.max_llm_calls = max_llm_calls
        self._session_factory = session_factory
        self.llm_calls = 0
        self.parse_failures = 0
        self.tokens_in = 0
        self.tokens_out = 0
        # Paragraph texts of everything detect() classified as changed, per
        # asset_no. Kept here rather than on ChangeItem so the frozen
        # ChangeItem contract stays at its nine documented fields.
        self.last_changed_texts: dict[str, list[str]] = {}

    def _record_changed_text(self, asset_no: str, text: str) -> None:
        if not text:
            return
        bucket = self.last_changed_texts.setdefault(asset_no, [])
        if text not in bucket:
            bucket.append(text)

    # -- session helper ---------------------------------------------------
    def _session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from database.knowevo_db import _get_db_session

        return _get_db_session()

    # -- STEP 1..3 --------------------------------------------------------
    async def detect(
        self,
        old_text: str,
        new_text: str,
        *,
        old_asset_no: str,
        new_asset_no: str,
    ) -> DetectResult:
        """Run the three-step detection over one document pair."""
        self.last_changed_texts = {}
        old_sections = parse_sections(old_text)
        new_sections = parse_sections(new_text)
        sections = align_sections(old_sections, new_sections)
        old_by_id = {s.section_id: s for s in old_sections}
        new_by_id = {s.section_id: s for s in new_sections}

        changes: list[ChangeItem] = []
        for old_id, new_id in sections.matched:
            left, right = old_by_id.get(old_id), new_by_id.get(new_id)
            if left is None or right is None:
                continue
            anchor = self._anchor(right)
            pairs = assign_paragraphs(
                similarity_matrix(left.paragraphs, right.paragraphs, self.embed),
                high=self.high_sim,
                low=self.low_sim,
                old_texts=left.paragraphs,
                new_texts=right.paragraphs,
                old_section_id=old_id,
                new_section_id=new_id,
            )
            for pair in pairs:
                label, points, source = await self._classify(pair, anchor)
                if label == "UNCHANGED":
                    continue
                if pair.old_index is not None:
                    self._record_changed_text(old_asset_no, pair.old_text)
                if pair.new_index is not None:
                    self._record_changed_text(new_asset_no, pair.new_text)
                changes.append(
                    ChangeItem(
                        change_type=label,
                        section_anchor=anchor,
                        old_section_id=old_id if pair.old_index is not None else None,
                        new_section_id=new_id if pair.new_index is not None else None,
                        similarity=pair.similarity,
                        points=points,
                        source=source,
                        old_span=self._span(old_asset_no, pair.old_index),
                        new_span=self._span(new_asset_no, pair.new_index),
                    )
                )
        for section_id in sections.added:
            node = new_by_id.get(section_id)
            if node is None:
                continue
            changes.append(
                ChangeItem(
                    change_type="ADD",
                    section_anchor=self._anchor(node),
                    new_section_id=section_id,
                    points=[f"new section: {node.title}"],
                    source="deterministic",
                    new_span=self._span(new_asset_no, None),
                )
            )
        for section_id in sections.deleted:
            node = old_by_id.get(section_id)
            if node is None:
                continue
            changes.append(
                ChangeItem(
                    change_type="DELETE",
                    section_anchor=self._anchor(node),
                    old_section_id=section_id,
                    points=[f"removed section: {node.title}"],
                    source="deterministic",
                    old_span=self._span(old_asset_no, None),
                )
            )
        for old_id, new_id, old_pos, new_pos in sections.moved:
            node = new_by_id.get(new_id) or old_by_id.get(old_id)
            changes.append(
                ChangeItem(
                    change_type="MOVE",
                    section_anchor=self._anchor(node) if node else new_id,
                    old_section_id=old_id,
                    new_section_id=new_id,
                    points=[f"section moved: position {old_pos} -> {new_pos}"],
                    source="deterministic",
                )
            )
        for old_id, new_id, old_num, new_num in sections.renumbered:
            node = new_by_id.get(new_id)
            changes.append(
                ChangeItem(
                    change_type="RENUMBER",
                    section_anchor=self._anchor(node) if node else new_id,
                    old_section_id=old_id,
                    new_section_id=new_id,
                    points=[f"section renumbered: {old_num} -> {new_num}"],
                    source="deterministic",
                )
            )

        table_changes = diff_tables(
            [t for s in old_sections for t in s.tables],
            [t for s in new_sections for t in s.tables],
            old_sections=[s.norm_title for s in old_sections for _ in s.tables],
            new_sections=[s.norm_title for s in new_sections for _ in s.tables],
        )
        for change in table_changes:
            changes.append(
                ChangeItem(
                    change_type="ADD" if change.kind == "ADD" else (
                        "DELETE" if change.kind == "DELETE" else "UPDATE"
                    ),
                    section_anchor=change.section_id,
                    points=list(change.detail[:5]),
                    source="deterministic",
                )
            )

        stats = {
            "old_sections": len(old_sections),
            "new_sections": len(new_sections),
            "matched_sections": len(sections.matched),
            "table_changes": len(table_changes),
            "llm_calls": self.llm_calls,
            "llm_parse_failures": self.parse_failures,
            "llm_budget": self.max_llm_calls,
            "llm_budget_exhausted": self.llm_calls >= self.max_llm_calls,
            "change_counts": _count_by_type(changes),
        }
        return DetectResult(
            old_asset_no=old_asset_no,
            new_asset_no=new_asset_no,
            sections=sections,
            changes=changes,
            table_changes=table_changes,
            stats=stats,
        )

    def _anchor(self, node: SectionNode | None) -> str:
        if node is None:
            return ""
        parts = [p for p in node.path if p]
        if node.title:
            parts = parts[:-1] + [f"{node.number} {node.title}".strip()]
        return " > ".join(parts)

    def _span(
        self,
        asset_no: str,
        index: int | None,
    ) -> tuple[str, int] | None:
        """Paragraph ordinal span: ``(asset_no, index)`` (see ChangeItem)."""
        if index is None:
            return None
        return (asset_no, index)

    async def _classify(
        self, pair: ParagraphPair, anchor: str
    ) -> tuple[str, list[str], str]:
        """STEP 3: LLM verdict when routable, deterministic label otherwise."""
        label = _deterministic_label(pair)
        if pair.route == ROUTE_UNMATCHED:
            return label, [f"{'added' if label == 'ADD' else 'removed'} paragraph"], (
                "deterministic"
            )
        if label == "UNCHANGED" and pair.route == ROUTE_DIRECT:
            return label, [], "deterministic"
        if self.llm is None or self.llm_calls >= self.max_llm_calls:
            return label, [f"similarity {pair.similarity:.3f}"], "deterministic"
        verdict = await self._ask_llm(pair, anchor)
        if verdict is None:
            return label, [f"similarity {pair.similarity:.3f}"], "deterministic"
        verdict_label, points, _confidence = verdict
        return verdict_label, points, "llm"

    async def _ask_llm(
        self, pair: ParagraphPair, anchor: str
    ) -> tuple[str, list[str], float] | None:
        from services.knowevo.kg_service import _render_prompt

        system, user = _render_prompt(
            "knowevo_align",
            self.lang,
            section_path=anchor,
            old_text=pair.old_text[:2000],
            new_text=pair.new_text[:2000],
        )
        prompt = f"{system}\n\n{user}"
        try:
            from services.knowevo.llm_client import TIER_MID as tier
        except Exception:  # noqa: BLE001 - constant fallback, same value
            tier = "mid"
        try:
            result = await self.llm(
                prompt, kind="align", tier=tier, temperature=0.0
            )
        except TypeError:
            result = await self.llm(prompt, kind="align")
        except Exception as exc:  # noqa: BLE001 - degrade, never fabricate
            logger.warning("align llm call failed: %s", exc)
            return None
        self.llm_calls += 1
        text, usage = result if isinstance(result, tuple) else (result, {})
        if isinstance(usage, dict):
            self.tokens_in += int(usage.get("input_tokens") or 0)
            self.tokens_out += int(usage.get("output_tokens") or 0)
        verdict = _parse_verdict(text)
        if verdict is None:
            self.parse_failures += 1
        return verdict

    # -- span resolution --------------------------------------------------
    def resolve_changed_spans(
        self,
        asset_no: str,
        texts: Sequence[str] | None = None,
        *,
        threshold: float = 0.5,
    ) -> list[tuple[str, int]]:
        """Translate changed paragraph texts into ingest spans.

        Paragraph ordinals from :func:`parse_sections` are not ingest chunk
        indexes, so this best-effort step matches each changed paragraph
        against the ``kg_evidence_t.span_text`` rows already in the graph and
        returns the ``(asset_no, chunk_idx)`` pairs that actually carry
        evidence. Paragraphs with no matching evidence row are simply absent
        from the result - never guessed - which is why a sparse graph yields
        a small affected surface (an honest limitation, not a bug).

        ``texts=None`` uses the paragraphs the last :meth:`detect` marked as
        changed for that asset.
        """
        from database.knowevo_db import DocAsset, KgEvidence

        if texts is None:
            texts = self.last_changed_texts.get(asset_no, [])
        if not texts:
            return []
        with self._session() as session:
            asset_row = session.execute(
                select(DocAsset.id).where(
                    DocAsset.tenant_id == self.tenant_id,
                    DocAsset.asset_no == asset_no,
                )
            ).first()
            if asset_row is None:
                return []
            rows = session.execute(
                select(KgEvidence.span_text, KgEvidence.span_loc).where(
                    KgEvidence.tenant_id == self.tenant_id,
                    KgEvidence.doc_id == asset_row.id,
                )
            ).all()
        resolved: list[tuple[str, int]] = []
        for text in texts:
            if not text:
                continue
            best: tuple[float, int] | None = None
            for row in rows:
                chunk_idx = (row.span_loc or {}).get("chunk_idx")
                if chunk_idx is None:
                    continue
                score = lexical_similarity(text, row.span_text or "")
                if score >= threshold and (best is None or score > best[0]):
                    best = (score, int(chunk_idx))
            if best is not None:
                span = (asset_no, best[1])
                if span not in resolved:
                    resolved.append(span)
        return resolved

    # -- section 4.2 ------------------------------------------------------
    def affected_surface(
        self, changed_spans: Sequence[tuple[str, int]]
    ) -> AffectedSurface:
        """Map changed spans to entities and to the cards citing them.

        Two index-served queries, no online graph traversal:
        Q1 ``kg_evidence_t`` rows whose ``(doc_id, span_loc->>'chunk_idx')``
        is in the changed span set -> ``entity_refs`` (E_aff);
        Q2 ``decision_card_t`` rows whose ``payload->'candidates'`` contains
        any E_aff ``stable_id`` in a ``provenance.kg_path`` -> D_aff.
        """
        from database.knowevo_db import DecisionCard, DocAsset, KgEntity, KgRelation

        surface = AffectedSurface(changed_spans=list(changed_spans))
        if not changed_spans:
            return surface
        with self._session() as session:
            from database.knowevo_db import KgEvidence

            assets = [span[0] for span in changed_spans]
            asset_rows = session.execute(
                select(DocAsset.id, DocAsset.asset_no).where(
                    DocAsset.tenant_id == self.tenant_id,
                    DocAsset.asset_no.in_(assets),
                )
            ).all()
            asset_ids = {row.asset_no: row.id for row in asset_rows}
            pairs = [
                (asset_ids[asset], int(chunk))
                for asset, chunk in changed_spans
                if asset in asset_ids
            ]
            if not pairs:
                surface.notes.append("no doc_asset row matched the given asset_no values")
                return surface
            evidence_rows = session.execute(
                select(
                    KgEvidence.id,
                    KgEvidence.doc_id,
                    KgEvidence.entity_refs,
                    KgEvidence.span_loc,
                ).where(
                    KgEvidence.tenant_id == self.tenant_id,
                    tuple_(
                        KgEvidence.doc_id,
                        cast(KgEvidence.span_loc["chunk_idx"].astext, Integer),
                    ).in_([(doc_id, chunk) for doc_id, chunk in pairs]),
                )
            ).all()
            surface.index_queries += 1

            stable_ids: list[str] = []
            evidence_by_entity: dict[str, list[str]] = {}
            for row in evidence_rows:
                for stable_id in row.entity_refs or []:
                    evidence_by_entity.setdefault(stable_id, []).append(str(row.id))
                    if stable_id not in stable_ids:
                        stable_ids.append(stable_id)
            if not stable_ids:
                return surface

            entity_rows = session.execute(
                select(KgEntity).where(
                    KgEntity.tenant_id == self.tenant_id,
                    KgEntity.stable_id.in_(stable_ids),
                )
            ).scalars().all()
            relation_rows = session.execute(
                select(KgRelation.id, KgRelation.src, KgRelation.dst).where(
                    KgRelation.tenant_id == self.tenant_id,
                    or_(
                        KgRelation.src.in_(stable_ids),
                        KgRelation.dst.in_(stable_ids),
                    ),
                )
            ).all()
            surface.relations = [str(row.id) for row in relation_rows]

            probes = [
                json.dumps(
                    [
                        {
                            "evidence_chain": [
                                {"provenance": {"kg_path": [stable_id]}}
                            ]
                        }
                    ],
                    ensure_ascii=False,
                )
                for stable_id in stable_ids
            ]
            card_query = select(
                DecisionCard.id, DecisionCard.payload, DecisionCard.question_id
            ).where(DecisionCard.tenant_id == self.tenant_id)
            card_query = card_query.where(
                or_(
                    *[
                        DecisionCard.payload["candidates"].contains(
                            json.loads(probe)
                        )
                        for probe in probes
                    ]
                )
            )
            card_rows = session.execute(card_query).all()
            surface.index_queries += 1

        cards_by_entity: dict[str, list[str]] = {sid: [] for sid in stable_ids}
        for row in card_rows:
            payload = row.payload or {}
            cited = _cited_stable_ids(payload)
            for stable_id in stable_ids:
                if stable_id in cited:
                    cards_by_entity[stable_id].append(str(row.id))
        surface.entities = [
            AffectedEntity(
                stable_id=row.stable_id,
                class_ref=row.class_ref or "",
                name=row.name or "",
                evidence_ids=evidence_by_entity.get(row.stable_id, []),
                decision_card_ids=cards_by_entity.get(row.stable_id, []),
            )
            for row in entity_rows
        ]
        surface.decision_cards = [
            {
                "card_id": str(row.id),
                "question_id": row.question_id,
                "conclusion": _card_conclusion(row.payload),
                "needs_rerun": None,
                "matched_entities": sorted(
                    sid for sid in stable_ids if sid in _cited_stable_ids(row.payload or {})
                ),
            }
            for row in card_rows
        ]
        surface.notes.append(
            "P_aff (ontology proposals) is supplied by the caller: "
            "ontology_change_proposal_t has no entity/version link"
        )
        return surface

    # -- section 4.3 ------------------------------------------------------
    def minimal_update(
        self,
        surface: AffectedSurface,
        proposals: Sequence[UpdateCandidate] | None = None,
        *,
        epsilon: float = 0.1,
        cost: float = 1.0,
    ) -> MinimalUpdateSet:
        """Build VOI candidates from the affected surface and select U."""
        candidates: list[UpdateCandidate] = list(proposals or [])
        for card in surface.decision_cards:
            matched = list(card.get("matched_entities") or [])
            total_entities = max(1, len(surface.entities))
            p_change = len(matched) / total_entities
            impact = len(matched)
            candidates.append(
                UpdateCandidate(
                    kind="decision_card",
                    ref_id=str(card.get("card_id")),
                    label=str(card.get("conclusion") or "")[:120],
                    p_change=p_change,
                    impact=impact,
                    voi=voi(p_change, impact),
                )
            )
        return minimal_update_set(candidates, epsilon=epsilon, cost=cost)

    # -- persistence ------------------------------------------------------
    def persist(
        self,
        *,
        detect: DetectResult,
        affected: AffectedSurface | None = None,
        update_set: MinimalUpdateSet | None = None,
        calibration: Calibration | None = None,
        topic_calibration: TopicCalibration | None = None,
        knowledge_stamp: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write doc_version_diff_t + evolution_round_t rows."""
        from database.knowevo_db import (
            DocAsset,
            DocVersionDiff,
            EvolutionRound,
            create_row,
        )

        with self._session() as session:
            asset_rows = session.execute(
                select(DocAsset.id, DocAsset.asset_no).where(
                    DocAsset.tenant_id == self.tenant_id,
                    DocAsset.asset_no.in_(
                        [detect.old_asset_no, detect.new_asset_no]
                    ),
                )
            ).all()
            asset_ids = {row.asset_no: row.id for row in asset_rows}
            missing = [
                key
                for key in (detect.old_asset_no, detect.new_asset_no)
                if key not in asset_ids
            ]
            if missing:
                raise ValueError(f"doc_asset row not found for asset_no: {missing}")
            old_id = asset_ids[detect.old_asset_no]
            new_id = asset_ids[detect.new_asset_no]
        diff_row = create_row(
            DocVersionDiff,
            tenant_id=self.tenant_id,
            old_doc=old_id,
            new_doc=new_id,
            section_align=asdict(detect.sections),
            changes=[_jsonable(asdict(c)) for c in detect.changes],
            # ``precision`` holds a true precision only; the topic-level
            # lower bound is not one (the gold seed is a curated sample, so
            # an unmatched group is not proven wrong), hence NULL here with
            # the number carried in ops_summary.calibration under an explicit
            # ``precision_is_lower_bound`` label. recall is kept: topic recall
            # IS a recall (share of gold topics covered).
            precision=(
                None
                if topic_calibration
                else (calibration.precision if calibration else None)
            ),
            recall=(
                topic_calibration.recall
                if topic_calibration
                else (calibration.recall if calibration else None)
            ),
        )
        ops_summary = {
            "changes": _count_by_type(detect.changes),
            "matched_sections": len(detect.sections.matched),
            "table_changes": len(detect.table_changes),
            "affected_entities": len(affected.entities) if affected else 0,
            "affected_cards": len(affected.decision_cards) if affected else 0,
            "update_set_size": len(update_set.selected) if update_set else 0,
            # evolution_round_t has no knowledge_stamp column (schema fact,
            # verified 2026-09-20); the stamp is carried here instead.
            "knowledge_stamp": knowledge_stamp or {},
            "loss_estimate": update_set.loss_estimate if update_set else None,
        }
        if topic_calibration or calibration:
            # ``precision`` above is the topic-level *lower bound* whenever
            # ``topic_calibration`` exists: the gold seed is a curated sample,
            # so unmatched groups are not proven wrong. The label travels with
            # the numbers to keep that from being lost downstream.
            ops_summary["calibration"] = {
                "mode": (
                    "topic_aggregate" if topic_calibration else "item_anchor"
                ),
                "precision_is_lower_bound": bool(topic_calibration),
                "recall_topic": (
                    topic_calibration.recall if topic_calibration else None
                ),
                "precision_lower_bound": (
                    topic_calibration.precision_lower_bound
                    if topic_calibration
                    else None
                ),
                "matched_topics": (
                    topic_calibration.matched_topics if topic_calibration else None
                ),
                "evaluable_gold": (
                    topic_calibration.gold_total
                    if topic_calibration
                    else (calibration.gold_total if calibration else None)
                ),
                "machine_groups": (
                    topic_calibration.machine_groups if topic_calibration else None
                ),
                "machine_items": (
                    topic_calibration.machine_total
                    if topic_calibration
                    else (calibration.machine_total if calibration else None)
                ),
                "unverified_excluded": (
                    topic_calibration.unverified_excluded
                    if topic_calibration
                    else (calibration.unverified_excluded if calibration else None)
                ),
                # Item-level comparison is kept for audit only; it mixes the
                # gold's topic-level labels with paragraph-level items, so its
                # precision is not a true precision (it cannot be). Produced
                # by calibrate_pr (run()) or calibrate_loose (CLI).
                "item_level_baseline": (
                    {
                        "precision": calibration.precision,
                        "recall": calibration.recall,
                        "matched": calibration.matched,
                        "machine_total": calibration.machine_total,
                    }
                    if calibration
                    else None
                ),
            }
        round_row = create_row(
            EvolutionRound,
            tenant_id=self.tenant_id,
            trigger_source="standard_update",
            trigger_ref=diff_row["id"],
            ops_summary=ops_summary,
            cost={
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "llm_calls": self.llm_calls,
                "cny": None,
                "human_minutes": None,
            },
        )
        return {
            "diff_id": str(diff_row["id"]),
            "round_id": str(round_row["id"]),
            "old_doc": str(old_id),
            "new_doc": str(new_id),
        }

    def list_diffs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """List persisted alignment diffs for this tenant, newest first."""
        from collections import Counter

        from database.knowevo_db import DocAsset, DocVersionDiff

        with self._session() as session:
            rows = session.execute(
                select(
                    DocVersionDiff.id,
                    DocVersionDiff.old_doc,
                    DocVersionDiff.new_doc,
                    DocVersionDiff.created_at,
                    DocVersionDiff.changes,
                    DocAsset.asset_no.label("old_asset_no"),
                )
                .join(DocAsset, DocAsset.id == DocVersionDiff.old_doc)
                .where(DocVersionDiff.tenant_id == self.tenant_id)
                .order_by(DocVersionDiff.created_at.desc())
                .limit(limit)
            ).all()
            new_rows = session.execute(
                select(DocAsset.id, DocAsset.asset_no).where(
                    DocAsset.tenant_id == self.tenant_id,
                    DocAsset.id.in_([row.new_doc for row in rows]),
                )
            ).all()
        new_by_id = {row.id: row.asset_no for row in new_rows}
        diffs: list[dict[str, Any]] = []
        for row in rows:
            counts = dict(
                Counter(
                    item.get("change_type")
                    for item in (row.changes or [])
                    if isinstance(item, dict) and item.get("change_type")
                )
            )
            diffs.append(
                {
                    "diff_id": str(row.id),
                    "old_asset_no": row.old_asset_no,
                    "new_asset_no": new_by_id.get(row.new_doc),
                    "created_at": (
                        row.created_at.isoformat() if row.created_at else None
                    ),
                    "change_counts": counts,
                }
            )
        return diffs

    async def run(
        self,
        *,
        old_asset_no: str,
        new_asset_no: str,
        old_text: str,
        new_text: str,
        old_chunks: Sequence[str] | None = None,
        new_chunks: Sequence[str] | None = None,
        gold: Sequence[dict[str, Any]] | None = None,
        proposals: Sequence[UpdateCandidate] | None = None,
        epsilon: float = 0.1,
        cost: float = 1.0,
        persist: bool = True,
        knowledge_stamp: dict[str, Any] | None = None,
    ) -> AlignmentResult:
        """Detect, propagate, select, calibrate and (optionally) persist."""
        detect = await self.detect(
            old_text,
            new_text,
            old_asset_no=old_asset_no,
            new_asset_no=new_asset_no,
            old_chunks=old_chunks,
            new_chunks=new_chunks,
        )
        spans: list[tuple[str, int]] = []
        for item in detect.changes:
            for span in (item.old_span, item.new_span):
                if span is not None and span not in spans:
                    spans.append(span)
        affected = self.affected_surface(spans)
        update_set = self.minimal_update(
            affected, proposals, epsilon=epsilon, cost=cost
        )
        calibration = calibrate_pr(detect.changes, list(gold or [])) if gold else None
        topic_calibration = (
            calibrate_topic(detect.changes, list(gold or [])) if gold else None
        )
        result = AlignmentResult(
            detect=detect,
            affected=affected,
            update_set=update_set,
            calibration=calibration,
            topic_calibration=topic_calibration,
        )
        if persist:
            result.persisted = self.persist(
                detect=detect,
                affected=affected,
                update_set=update_set,
                calibration=calibration,
                topic_calibration=topic_calibration,
                knowledge_stamp=knowledge_stamp,
            )
        return result


# ---------------------------------------------------------------------------
# JSON helpers (shared with the CLI)
# ---------------------------------------------------------------------------


def _count_by_type(changes: Iterable[ChangeItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in changes:
        counts[item.change_type] = counts.get(item.change_type, 0) + 1
    return counts


def _cited_stable_ids(payload: dict[str, Any]) -> set[str]:
    """Collect entity stable_ids referenced anywhere in a card payload."""
    found: set[str] = set()
    for candidate in payload.get("candidates") or []:
        for evidence in candidate.get("evidence_chain") or []:
            provenance = evidence.get("provenance") or {}
            for node in provenance.get("kg_path") or []:
                if isinstance(node, str):
                    found.add(node)
    return found


def _card_conclusion(payload: dict[str, Any]) -> str:
    decision = payload.get("decision") or {}
    if isinstance(decision, dict):
        return str(decision.get("option") or decision.get("summary") or "")
    return str(decision)


def to_report_json(result: AlignmentResult) -> dict[str, Any]:
    """Serialise an AlignmentResult into a JSON-safe dict (no private data)."""
    detect = result.detect
    return {
        "old_asset_no": detect.old_asset_no,
        "new_asset_no": detect.new_asset_no,
        "stats": detect.stats,
        "sections": asdict(detect.sections),
        "changes": [_jsonable(asdict(c)) for c in detect.changes],
        "table_changes": [asdict(c) for c in detect.table_changes],
        "affected": (
            {
                "changed_spans": [list(s) for s in result.affected.changed_spans],
                "entity_count": len(result.affected.entities),
                "relation_count": len(result.affected.relations),
                "decision_card_count": len(result.affected.decision_cards),
                "index_queries": result.affected.index_queries,
                "entities": [asdict(e) for e in result.affected.entities],
                "decision_cards": result.affected.decision_cards,
                "notes": result.affected.notes,
            }
            if result.affected
            else None
        ),
        "update_set": (
            {
                "epsilon": result.update_set.epsilon,
                "loss_estimate": result.update_set.loss_estimate,
                "selected": [asdict(c) for c in result.update_set.selected],
                "excluded": [asdict(c) for c in result.update_set.excluded],
            }
            if result.update_set
            else None
        ),
        "calibration": (
            asdict(result.calibration) if result.calibration else None
        ),
        "topic_calibration": (
            asdict(result.topic_calibration) if result.topic_calibration else None
        ),
        "persisted": result.persisted,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value
