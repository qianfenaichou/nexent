"""L5 community summary kernel (2026-09-29).

Cluster a KnowEvo graph view into communities, build a deterministic
skeleton summary per community (no LLM), and expose a ``global`` retrieval
seam that ranks communities for aggregate questions and expands them into
an ordered entity-id list (the L6 graph-route shape).

Scope of this module (honest layering, same rule as rrf_fusion /
update_planner / budget_curve):
- stdlib-only at import time: no networkx, no igraph, no DB, no LLM, no ES;
- default clustering is deterministic greedy modularity (CNM-style) with
  lexicographic tie-breaks - zero RNG, so two runs partition identically;
- the LLM community-summary pass is a frozen *prompt protocol* stub only
  (``build_llm_prompt`` + ``LLM_SUMMARY_PROTOCOL``); no model is called;
- production wiring (kg_summary_t persistence, schemas.Route branch) is
  NOT done here and is NOT imported by any running path yet, so default
  retrieval/routing behaviour is unchanged.

Persistence seam (T-08 follow-up, 2026-09-30; docstring note only - no
code change in this kernel): community summaries are persisted by
``services.knowevo.summary_store.SummaryStore`` (table
``nexent.kg_summary_t``, migration ``v2.5.5_kw_012_kg_summary.sql``).
The production global-route wiring point is: load the version-scoped
record set with ``SummaryStore.load_summaries``, pass ``record.skeleton``
to ``score_communities`` and rebuild ``Community`` objects from
``record.member_ids`` for ``entities_from_hits``. This kernel stays
persistence-blind: it neither imports the store nor gains parameters.

Why greedy modularity (not Leiden) as the default clusterer: GraphRAG
(Microsoft) uses Leiden via igraph/leidenalg, neither of which is a
declared dependency, and networkx's ``leiden_communities`` is a
backend-dispatch stub that raises ``NotImplementedError`` without a
separate backend package (verified on networkx 3.6.1 in the project venv).
networkx itself is present only as a torch transitive dependency and is
not declared in backend/pyproject.toml (a frozen wiring file). The default
here is Clauset-Newman-Moore-style greedy modularity maximisation in pure
stdlib with exact tie-breaks; a pluggable ``cluster_fn`` seam lets a later
task swap in Leiden when the dependency is allowed.

Determinism contract (tested):
- every public function is a pure function of its arguments;
- community ids are the lexicographic minimum of their member ids;
- communities are returned sorted by community_id;
- skeleton fingerprints are sha256 over canonical sorted JSON;
- ranking ties break by ``(-score, -size, community_id)``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Multi-hop caps match graph_store.multi_hop / KW_MULTIHOP_* (memo 10).
DEFAULT_DEPTH = 3
DEFAULT_BEAM = 3

# Recommended additive Route vocabulary value (design doc L5; schemas.py
# itself is a wiring-adjacent module and is NOT edited by this kernel).
GLOBAL_ROUTE = "G"

DEFAULT_TOP_ENTITIES = 5
DEFAULT_MAX_CLAIMS = 3
DEFAULT_MAX_ITER = 16
# Merges must exceed this dQ; absorbs float noise so ties stay ties.
MODULARITY_EPS = 1e-12

# Frozen LLM summary protocol. The kernel never calls a model; callers that
# do must satisfy these acceptance gates before persisting summary_text.
LLM_SUMMARY_PROTOCOL: dict[str, Any] = {
    "version": "l5-community-summary-v1",
    "model_temperature": 0,
    "max_output_tokens": 400,
    "must": [
        "ground every claim in the skeleton (entity names / claims given)",
        "cite member entity names verbatim when used",
        "prefer aggregation over enumeration (themes, not a full roster)",
    ],
    "must_not": [
        "invent entities, numbers, or relations absent from the skeleton",
        "answer as if the summary were a local fact lookup",
        "mix in facts from other communities",
    ],
    "acceptance_gates": [
        "every named entity in the output appears in member names",
        "no digits outside those appearing in skeleton claims",
        "length <= max_output_tokens",
    ],
    "on_gate_fail": "discard LLM output, keep skeleton summary (summary_kind='skeleton')",
}

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_SPLIT_RE = re.compile(r"[^A-Za-z0-9_\u4e00-\u9fff]+")


def _require_str(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be str, got {type(value).__name__}")
    return value


def _require_nonempty_str(value: Any, what: str) -> str:
    s = _require_str(value, what)
    if not s:
        raise ValueError(f"{what} must be a non-empty string")
    return s


def _require_positive_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} must be int, got {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{what} must be >= 1, got {value}")
    return value


@dataclass(frozen=True)
class Community:
    """One flat community (level 0). Hierarchical levels stay future work."""

    community_id: str
    member_ids: tuple[str, ...]
    level: int = 0

    def __post_init__(self) -> None:
        _require_nonempty_str(self.community_id, "community_id")
        if not self.member_ids:
            raise ValueError("member_ids must be non-empty")
        for m in self.member_ids:
            _require_nonempty_str(m, "member id")
        if tuple(sorted(self.member_ids)) != self.member_ids:
            raise ValueError("member_ids must be sorted ascending")
        if self.community_id != self.member_ids[0]:
            raise ValueError("community_id must be min(member_ids)")


@dataclass(frozen=True)
class SkeletonSummary:
    """Deterministic no-LLM digest of one community."""

    community_id: str
    size: int
    top_entities: tuple[tuple[str, str, int], ...]  # (id, name, degree)
    rel_type_counts: tuple[tuple[str, int], ...]  # sorted (-count, rel_type)
    bridge_claims: tuple[str, ...]
    fingerprint: str

    @property
    def searchable_text(self) -> str:
        parts = [e[1] for e in self.top_entities]
        parts.extend(rt for rt, _ in self.rel_type_counts)
        parts.extend(self.bridge_claims)
        return " ".join(parts)


@dataclass(frozen=True)
class GlobalHit:
    """One ranked community hit for a global (aggregate) query."""

    community_id: str
    score: int
    matched_terms: tuple[str, ...]
    rank: int  # 1-based


@dataclass(frozen=True)
class GlobalRouteResult:
    """Full global-route verdict: hits + expanded entity ids (L6 shape)."""

    route: str
    hits: tuple[GlobalHit, ...]
    entity_ids: tuple[str, ...]
    depth: int
    beam: int


def tokenize(text: str) -> tuple[str, ...]:
    """Deterministic tokens: lowercase ASCII words + CJK chars and bigrams.

    Order is first-seen; duplicates are kept out (set semantics via dict).
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    out: dict[str, None] = {}
    for word in _WORD_RE.findall(text):
        out.setdefault(word.lower(), None)
    for run in _CJK_RE.findall(text):
        for i, ch in enumerate(run):
            out.setdefault(ch, None)
            if i + 1 < len(run):
                out.setdefault(run[i : i + 2], None)
    return tuple(out.keys())


def _edge_ends(edge: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(edge, Mapping):
        raise TypeError(f"edge must be a Mapping, got {type(edge).__name__}")
    src = _require_nonempty_str(edge.get("src"), "edge.src")
    dst = _require_nonempty_str(edge.get("dst"), "edge.dst")
    return src, dst


def _adjacency(
    node_ids: Iterable[str], edges: Sequence[Mapping[str, Any]]
) -> dict[str, set[str]]:
    nodes = sorted({_require_nonempty_str(n, "node id") for n in node_ids})
    adj: dict[str, set[str]] = {n: set() for n in nodes}
    for edge in edges:
        src, dst = _edge_ends(edge)
        if src not in adj or dst not in adj:
            continue
        if src == dst:
            continue
        adj[src].add(dst)
        adj[dst].add(src)
    return adj


def _communities_from_labels(labels: Mapping[str, str]) -> list[Community]:
    groups: dict[str, list[str]] = defaultdict(list)
    for node, label in labels.items():
        groups[label].append(node)
    comms = [
        Community(
            community_id=min(members),
            member_ids=tuple(sorted(members)),
            level=0,
        )
        for members in groups.values()
    ]
    return sorted(comms, key=lambda c: c.community_id)


def cluster_connected_components(
    node_ids: Iterable[str],
    edges: Sequence[Mapping[str, Any]],
) -> list[Community]:
    """Undirected connected components - coarse but fully deterministic."""
    adj = _adjacency(node_ids, edges)
    labels: dict[str, str] = {}
    for start in sorted(adj):
        if start in labels:
            continue
        stack = [start]
        labels[start] = start
        while stack:
            node = stack.pop()
            for nxt in sorted(adj[node]):
                if nxt not in labels:
                    labels[nxt] = start
                    stack.append(nxt)
    return _communities_from_labels(labels)


def cluster_lpa(
    node_ids: Iterable[str],
    edges: Sequence[Mapping[str, Any]],
    *,
    max_iter: int = DEFAULT_MAX_ITER,
) -> list[Community]:
    """Deterministic label propagation (kept as an alternative clusterer).

    Each node adopts the majority label of its neighbours; ties break by
    the label string ascending (never by insertion order or RNG). Nodes are
    visited in sorted id order every pass. Isolated nodes keep their own
    id as label, so they form singleton communities.

    Known limit (honest): LPA collapses into one community on graphs that
    only differ by a few bridge edges (two cliques + one bridge collapse to
    one community). Prefer :func:`cluster_greedy_modularity` whenever any
    bridge edge exists; LPA is fine for already-separated components.
    """
    max_iter = _require_positive_int(max_iter, "max_iter")
    adj = _adjacency(node_ids, edges)
    labels: dict[str, str] = {n: n for n in sorted(adj)}
    for _ in range(max_iter):
        changed = False
        for node in sorted(adj):
            votes: Counter[str] = Counter()
            for neigh in adj[node]:
                votes[labels[neigh]] += 1
            if not votes:
                continue
            best = min(votes.items(), key=lambda kv: (-kv[1], kv[0]))
            if best[0] != labels[node]:
                labels[node] = best[0]
                changed = True
        if not changed:
            break
    return _communities_from_labels(labels)


def _degree_map(
    node_ids: Iterable[str], edges: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    adj = _adjacency(node_ids, edges)
    return {n: len(neighs) for n, neighs in adj.items()}


def _edges_between(
    edges: Sequence[Mapping[str, Any]],
    left: set[str],
    right: set[str],
) -> int:
    """Count edges with one end in ``left`` and the other in ``right``.

    The pair is treated as unordered; an edge inside the intersection is
    counted once. Self-loops are ignored (they never cross two sets).
    """
    n = 0
    for edge in edges:
        src, dst = _edge_ends(edge)
        if src == dst:
            continue
        if (src in left and dst in right) or (src in right and dst in left):
            n += 1
    return n


def modularity(
    communities: Sequence[Community],
    *,
    node_ids: Iterable[str],
    edges: Sequence[Mapping[str, Any]],
) -> float:
    """Newman-Girvan modularity of a partition (unweighted, resolution 1).

        Q = sum_c [ E_c / m - (K_c / (2m))^2 ]

    with ``E_c`` = intra-community edge count (each edge once) and
    ``K_c`` = sum of member degrees. Empty graph -> 0.0.
    """
    degrees = _degree_map(node_ids, edges)
    n_edges = sum(1 for e in edges if _edge_ends(e)[0] != _edge_ends(e)[1])
    if n_edges == 0:
        return 0.0
    m = float(n_edges)
    total = 0.0
    member_sets = [set(c.member_ids) for c in communities]
    for members in member_sets:
        # _edges_between counts each undirected edge once (including
        # intra-set edges, see its docstring); do NOT halve again.
        e_c = _edges_between(edges, members, members)
        k_c = sum(degrees.get(n, 0) for n in members)
        total += e_c / m - (k_c / (2.0 * m)) ** 2
    return total


def cluster_greedy_modularity(
    node_ids: Iterable[str],
    edges: Sequence[Mapping[str, Any]],
) -> list[Community]:
    """CNM-style greedy modularity maximisation, fully deterministic.

    Start with every node in its own community and repeatedly merge the
    adjacent community pair whose merge raises modularity the most:

        dQ = e_ab / m - (K_a * K_b) / (2 * m^2)

    Stop when the best dQ <= MODULARITY_EPS. Pair ties break on the
    community-id pair ``(min_id, max_id)`` ascending, so the partition does
    not depend on edge insertion order or dict iteration order.
    """
    adj = _adjacency(node_ids, edges)
    if not adj:
        return []
    degrees = {n: len(neighs) for n, neighs in adj.items()}
    undirected: list[tuple[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        src, dst = _edge_ends(edge)
        if src == dst or src not in adj or dst not in adj:
            continue
        key = (src, dst) if src <= dst else (dst, src)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        undirected.append(key)
    m = float(len(undirected))
    if m == 0.0:
        # All singletons - no edge to merge across.
        return [
            Community(community_id=n, member_ids=(n,), level=0) for n in sorted(adj)
        ]

    comm_of: dict[str, str] = {n: n for n in sorted(adj)}
    members: dict[str, set[str]] = {n: {n} for n in sorted(adj)}
    deg_sum: dict[str, int] = dict(degrees)

    def edge_count(a: str, b: str) -> int:
        if a == b:
            # Intra edges: undirected already stores each edge once.
            left = members[a]
            return sum(1 for u, v in undirected if u in left and v in left)
        left, right = members[a], members[b]
        return sum(1 for u, v in undirected if (u in left and v in right) or (u in right and v in left))

    def dq(a: str, b: str) -> float:
        e_ab = edge_count(a, b)
        return e_ab / m - (deg_sum[a] * deg_sum[b]) / (2.0 * m * m)

    while True:
        best_pair: tuple[str, str] | None = None
        best_dq = float("-inf")
        pairs: set[tuple[str, str]] = set()
        for u, v in undirected:
            a, b = comm_of[u], comm_of[v]
            if a == b:
                continue
            pairs.add((a, b) if a <= b else (b, a))
        for a, b in sorted(pairs):
            value = dq(a, b)
            if value > best_dq + MODULARITY_EPS:
                best_dq = value
                best_pair = (a, b)
            elif abs(value - best_dq) <= MODULARITY_EPS and best_pair is not None:
                # Tie: keep the lexicographically smaller pair (already sorted).
                best_pair = min(best_pair, (a, b))
        if best_pair is None or best_dq <= MODULARITY_EPS:
            break
        a, b = best_pair
        # Merge b into a (a is the smaller id by construction).
        members[a] |= members[b]
        deg_sum[a] += deg_sum[b]
        for node in members[b]:
            comm_of[node] = a
        del members[b]
        del deg_sum[b]

    return _communities_from_labels(comm_of)


def _edge_key(edge: Mapping[str, Any]) -> tuple[str, str, str, str]:
    src, dst = _edge_ends(edge)
    rel = _require_str(edge.get("rel_type", "") or "", "edge.rel_type")
    claim = _require_str(edge.get("claim", "") or "", "edge.claim")
    return (src, dst, rel, claim)


def skeleton_summary(
    community: Community,
    *,
    names: Mapping[str, str],
    edges: Sequence[Mapping[str, Any]],
    top_k: int = DEFAULT_TOP_ENTITIES,
    max_claims: int = DEFAULT_MAX_CLAIMS,
) -> SkeletonSummary:
    """Build the deterministic skeleton digest of one community.

    Only intra-community edges contribute to degrees, rel_type_counts and
    bridge_claims. ``names`` maps stable_id -> display name (missing name
    falls back to the id, never raises).
    """
    if not isinstance(community, Community):
        raise TypeError(f"community must be Community, got {type(community).__name__}")
    if not isinstance(names, Mapping):
        raise TypeError(f"names must be a Mapping, got {type(names).__name__}")
    top_k = _require_positive_int(top_k, "top_k")
    max_claims = _require_positive_int(max_claims, "max_claims")

    members = set(community.member_ids)
    degree: Counter[str] = Counter()
    rel_types: Counter[str] = Counter()
    claims: list[tuple[str, str, str, str]] = []
    for edge in edges:
        src, dst = _edge_ends(edge)
        if src not in members or dst not in members:
            continue
        degree[src] += 1
        degree[dst] += 1
        rel = _require_str(edge.get("rel_type", "") or "", "edge.rel_type")
        rel_types[rel] += 1
        claims.append(_edge_key(edge))

    top_entities = []
    for node in sorted(members):
        name = names.get(node, node)
        if not isinstance(name, str):
            name = node
        top_entities.append((node, name, degree.get(node, 0)))
    top_entities.sort(key=lambda t: (-t[2], t[0]))
    top_entities_t = tuple(top_entities[:top_k])

    rel_counts_t = tuple(
        sorted(rel_types.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    # Deterministic claim pick: lexicographic on the canonical edge key.
    bridge_claims = tuple(c[3] for c in sorted(set(claims))[:max_claims])

    payload = {
        "community_id": community.community_id,
        "member_ids": list(community.member_ids),
        "top_entities": [list(t) for t in top_entities_t],
        "rel_type_counts": [list(r) for r in rel_counts_t],
        "bridge_claims": list(bridge_claims),
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    return SkeletonSummary(
        community_id=community.community_id,
        size=len(community.member_ids),
        top_entities=top_entities_t,
        rel_type_counts=rel_counts_t,
        bridge_claims=bridge_claims,
        fingerprint=fingerprint,
    )


def render_skeleton_text(skel: SkeletonSummary) -> str:
    """Deterministic plain-text rendering of a skeleton (no LLM)."""
    if not isinstance(skel, SkeletonSummary):
        raise TypeError(f"skel must be SkeletonSummary, got {type(skel).__name__}")
    parts = [
        f"community={skel.community_id}",
        f"size={skel.size}",
        "top_entities=" + ", ".join(f"{name}({deg})" for _, name, deg in skel.top_entities),
        "rel_types=" + ", ".join(f"{rt}:{n}" for rt, n in skel.rel_type_counts),
    ]
    if skel.bridge_claims:
        parts.append("claims=" + " | ".join(skel.bridge_claims))
    return " ; ".join(parts)


def build_llm_prompt(
    skel: SkeletonSummary,
    *,
    question: str | None = None,
    language: str = "zh",
) -> str:
    """Frozen prompt template for the (out-of-band) LLM summary pass.

    Returns a string; this function never calls a model. ``language`` is
    validated against the two supported values only.
    """
    if not isinstance(skel, SkeletonSummary):
        raise TypeError(f"skel must be SkeletonSummary, got {type(skel).__name__}")
    if language not in ("zh", "en"):
        raise ValueError(f"language must be 'zh' or 'en', got {language!r}")
    if question is not None:
        _require_str(question, "question")
    body = render_skeleton_text(skel)
    q_line = f"question={question}\n" if question else ""
    lang_line = "respond in Chinese" if language == "zh" else "respond in English"
    return (
        "You are summarizing ONE knowledge-graph community for a global "
        "search layer.\n"
        f"{q_line}"
        f"skeleton:\n{body}\n"
        f"{lang_line}.\n"
        "Rules: "
        + "; ".join(LLM_SUMMARY_PROTOCOL["must"])
        + ". Forbidden: "
        + "; ".join(LLM_SUMMARY_PROTOCOL["must_not"])
        + ".\n"
    )


def score_communities(
    query: str,
    summaries: Sequence[SkeletonSummary],
    *,
    top_n: int = 3,
) -> list[GlobalHit]:
    """Rank communities for a global query by distinct term overlap.

    score = number of distinct query tokens present in the community's
    searchable text (top entity names + rel types + bridge claims).
    Ties: higher score first, larger size first, then community_id asc.
    """
    _require_str(query, "query")
    top_n = _require_positive_int(top_n, "top_n")
    if not isinstance(summaries, Sequence):
        raise TypeError("summaries must be a Sequence")
    terms = set(tokenize(query))
    scored: list[tuple[int, int, str, tuple[str, ...]]] = []
    for skel in summaries:
        if not isinstance(skel, SkeletonSummary):
            raise TypeError(
                f"summaries items must be SkeletonSummary, got {type(skel).__name__}"
            )
        text_tokens = set(tokenize(skel.searchable_text))
        matched = tuple(sorted(terms & text_tokens))
        scored.append((len(matched), skel.size, skel.community_id, matched))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    hits: list[GlobalHit] = []
    for i, (score, _size, cid, matched) in enumerate(scored[:top_n], start=1):
        hits.append(
            GlobalHit(community_id=cid, score=score, matched_terms=matched, rank=i)
        )
    return hits


def entities_from_hits(
    hits: Sequence[GlobalHit],
    communities: Mapping[str, Community] | Sequence[Community],
    *,
    edges: Sequence[Mapping[str, Any]] = (),
    depth: int = DEFAULT_DEPTH,
    beam: int = DEFAULT_BEAM,
) -> tuple[str, ...]:
    """Expand global hits into an ordered unique entity-id list.

    Order: hit rank, then within a community the sorted member ids; then a
    bounded one-hop-at-a-time expansion along intra/partial edges. ``depth``
    and ``beam`` mirror graph_store.multi_hop caps (depth <= 3, beam <= 5
    in the store; defaults here are 3/3). No cycles, no RNG.
    """
    depth = _require_positive_int(depth, "depth")
    beam = _require_positive_int(beam, "beam")
    if depth > DEFAULT_DEPTH:
        raise ValueError(f"depth must be <= {DEFAULT_DEPTH}, got {depth}")
    if isinstance(communities, Mapping):
        comm_map = dict(communities)
    else:
        comm_map = {}
        for c in communities:
            if not isinstance(c, Community):
                raise TypeError(
                    f"communities items must be Community, got {type(c).__name__}"
                )
            comm_map[c.community_id] = c

    ordered: dict[str, None] = {}
    frontier: list[str] = []
    for hit in hits:
        if not isinstance(hit, GlobalHit):
            raise TypeError(f"hits items must be GlobalHit, got {type(hit).__name__}")
        comm = comm_map.get(hit.community_id)
        if comm is None:
            continue
        for member in comm.member_ids:
            if member not in ordered:
                ordered[member] = None
                frontier.append(member)

    if not edges:
        return tuple(ordered.keys())

    adj: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        src, dst = _edge_ends(edge)
        adj[src].append(dst)
        adj[dst].append(src)
    for node, neighs in adj.items():
        adj[node] = sorted(set(neighs))

    for _ in range(depth):
        if not frontier:
            break
        next_frontier: list[str] = []
        for node in frontier:
            expansions = 0
            for neigh in adj.get(node, ()):
                if expansions >= beam:
                    break
                if neigh in ordered:
                    continue
                ordered[neigh] = None
                next_frontier.append(neigh)
                expansions += 1
        frontier = next_frontier
    return tuple(ordered.keys())


def global_route(
    query: str,
    *,
    communities: Sequence[Community],
    names: Mapping[str, str],
    edges: Sequence[Mapping[str, Any]],
    top_n: int = 3,
    depth: int = DEFAULT_DEPTH,
    beam: int = DEFAULT_BEAM,
    cluster_fn=None,
) -> GlobalRouteResult:
    """End-to-end global path: cluster (if needed) -> skeleton -> rank -> expand.

    If ``communities`` is empty and ``cluster_fn`` is provided, cluster_fn
    is called as ``cluster_fn(node_ids, edges)``. ``names`` supplies display
    names and, via its keys when communities are empty, the node id set.
    """
    _require_str(query, "query")
    top_n = _require_positive_int(top_n, "top_n")
    depth = _require_positive_int(depth, "depth")
    beam = _require_positive_int(beam, "beam")
    if not isinstance(names, Mapping):
        raise TypeError("names must be a Mapping")
    if not isinstance(communities, Sequence):
        raise TypeError("communities must be a Sequence")

    comm_list = list(communities)
    if not comm_list:
        if cluster_fn is None:
            raise ValueError("communities empty and cluster_fn not provided")
        node_ids = sorted(names.keys())
        comm_list = list(cluster_fn(node_ids, edges))

    summaries = [
        skeleton_summary(c, names=names, edges=edges) for c in comm_list
    ]
    hits = score_communities(query, summaries, top_n=top_n)
    entity_ids = entities_from_hits(
        hits, comm_list, edges=edges, depth=depth, beam=beam
    )
    return GlobalRouteResult(
        route=GLOBAL_ROUTE,
        hits=tuple(hits),
        entity_ids=entity_ids,
        depth=depth,
        beam=beam,
    )
