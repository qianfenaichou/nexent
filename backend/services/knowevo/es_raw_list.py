"""Raw-list ES adapter for the kg_search three-way RRF fusion (L6-M2).

The fusion kernel (``rrf_fusion.fuse``) consumes ranked hit lists whose
items expose a string id in ONE shared id space. This adapter is the
single place that reshapes raw ES hits into the fused entity-id space.

Query construction lives here too, and that is deliberate: the platform's
``ElasticSearchCore.accurate_search`` builds its weighted query over the
knowledge-base schema fields ``title``/``content`` (``build_weighted_query``
default field weights), which the knowevo entity index does not carry
(strict mapping: ``name``/``aliases.alias``/``class_ref``). Pointing
``accurate_search`` at this index therefore returns ZERO hits silently -
caught by the 2026-09-30 real-ES acceptance (bm25_count=0 on exact-name
queries). The adapter owns entity-index query construction instead: a
``multi_match`` over ``name`` + ``aliases.alias`` with ``operator=and``
(the semantics proven by the L1#3 real-ES integration probe).

Three surfaces, one adapter:

- ``entity_bm25_hits``  - BM25 raw list for the kg_search fusion route
  (ids are ``stable_id``s, ES order preserved, tenant filter forced);
- ``entity_search``     - the ``PgJsonbGraphStore`` es_client seam
  (``entity_search(tenant_id, query, top_k)``): returns graph-store
  compatible dicts (``stable_id`` + ``name`` at minimum) so a production
  injection into ``_store()`` makes ``entity_lookup`` truly ES-first;
- ``dense_entity_hits`` - the dense route slot. Honestly empty this round:
  the entity index carries no ``embedding`` field (``KgEntity.embedding``
  is a PG JSONB column and no write path backfills vectors into ES), so
  no knn query is issued rather than pretending to search. The multimodal
  guard is still enforced: only a plain ``model_type == "embedding"``
  model may ever serve this route (``semantic_search``'s multimodal
  branch returns two concatenated knn lists - not one ranked list RRF
  can consume).

Environment (read here on purpose - ``backend/consts/const.py`` is a
shared wiring file and this module must not touch it; the names are the
platform's existing ES env names):

- ``ELASTICSEARCH_HOST`` / ``ELASTICSEARCH_API_KEY``: ES credentials;
- ``KW_ENTITY_ES_INDEX``: entity index name, default
  ``ENTITY_INDEX_DEFAULT`` (the minimal verification index filled by the
  L6-M2 probes; the production write path stays upstream-owned).

``build_es_raw_client`` returns ``None`` when the host env is absent -
every caller then keeps today's behaviour bit for bit. Exceptions from
real ES calls propagate to the caller, whose contract is to fall back
silently (ES-first + deterministic-PG style, same as ``entity_lookup``).
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator, Mapping
from typing import Any

ENTITY_INDEX_DEFAULT = "knowevo_entities_m2"

# Audit string for the honest dense-slot emptiness (kg_fusion records it
# in the fusion outcome; never a fabricated score).
DENSE_ENTITY_DISABLED_REASON = "entity_index_has_no_embedding_field"

ENV_ES_HOST = "ELASTICSEARCH_HOST"
ENV_ES_API_KEY = "ELASTICSEARCH_API_KEY"
ENV_ENTITY_INDEX = "KW_ENTITY_ES_INDEX"


class EsRawListClient:
    """Thin adapter over the platform ES core for the knowevo entity index.

    ``core`` is duck-typed: only the raw ``client.search`` (and, for a
    future dense route, ``semantic_search``) is ever called, so offline
    tests can inject a fake. Query construction intentionally does NOT go
    through ``accurate_search`` - its weighted query targets the KB schema
    fields ``title``/``content``, which this index does not carry (see the
    module docstring). ``embedding_model`` stays None in production wiring
    this round (see ``dense_entity_hits``).
    """

    _NAME_FIELDS = ("name", "aliases.alias")

    def __init__(self, core: Any, entity_index: str = ENTITY_INDEX_DEFAULT,
                 embedding_model: Any = None):
        self.core = core
        self.entity_index = entity_index
        self.embedding_model = embedding_model

    @staticmethod
    def _tenant_filter(tenant_id: str) -> list[dict[str, Any]]:
        """Tenant isolation is forced on every route, never caller-supplied."""
        return [{"term": {"tenant_id": tenant_id}}]

    @staticmethod
    def _documents(raw_hits: Any) -> Iterator[tuple[Mapping, Mapping]]:
        """Yield (raw_hit, document) pairs for hits carrying a mapping document."""
        for hit in raw_hits or []:
            if not isinstance(hit, Mapping):
                continue
            doc = hit.get("document")
            if isinstance(doc, Mapping):
                yield hit, doc

    def _search_raw(self, tenant_id: str, query: str,
                    top_k: int) -> list[Mapping]:
        """One entity-index search, returned in the exec_query hit shape.

        The DSL is self-contained (multi_match over the knowevo entity
        fields, ``operator=and`` per the L1#3 proven semantics, tenant
        filter forced) and runs through the raw ES ``client.search`` -
        the same transport the platform's ``exec_query`` uses - so the
        returned ``{score, document, index}`` shape matches what the
        platform would hand back for a KB index.
        """
        body = {
            "size": top_k,
            "query": {
                "bool": {
                    "must": [{"multi_match": {
                        "query": query,
                        "fields": list(self._NAME_FIELDS),
                        "operator": "and",
                    }}],
                    "filter": self._tenant_filter(tenant_id),
                },
            },
        }
        response = self.core.client.search(index=[self.entity_index],
                                           body=body)
        hits = (response or {}).get("hits", {}).get("hits") or []
        return [
            {"score": hit.get("_score"),
             "document": hit.get("_source"),
             "index": hit.get("_index")}
            for hit in hits if isinstance(hit, Mapping)
        ]

    def entity_bm25_hits(self, tenant_id: str, query: str,
                         top_k: int) -> list[dict[str, Any]]:
        """BM25 raw list in the stable_id id space (ES order preserved).

        Hits whose ``document`` carries no ``stable_id`` are dropped: the
        fusion may only see ids the graph neighbourhood can resolve. The
        ES score rides along as ``es_score`` for audit only - RRF consumes
        ranks, never scores.
        """
        if not isinstance(query, str) or not query.strip():
            return []
        hits: list[dict[str, Any]] = []
        for hit, doc in self._documents(self._search_raw(tenant_id, query, top_k)):
            sid = doc.get("stable_id")
            if not isinstance(sid, str) or not sid:
                continue
            hits.append({
                "id": sid,
                "name": doc.get("name"),
                "es_score": hit.get("score"),
                "index": hit.get("index"),
            })
        return hits

    async def entity_search(self, tenant_id: str, query: str,
                            top_k: int) -> list[dict[str, Any]]:
        """Graph-store es_client seam: dicts ``_as_entity_card`` accepts.

        Async (Standards review 2026-09-30, contract-first): the ES call
        runs in a worker thread so the store's ES-first branch can never
        block the handler event loop - same rationale as the fusion path's
        threaded ``entity_bm25_hits``. The store's ``_maybe_await`` awaits
        the coroutine natively. Shape mirrors ``GraphStore._entity_to_card``:
        ``stable_id``/``name`` required (a hit missing either is dropped -
        the store never invents an entity), aliases normalised to
        ``list[str]``. ES contributes only hits and relative order; no
        score is folded into the card.
        """
        if not isinstance(query, str) or not query.strip():
            return []
        raw = await asyncio.to_thread(self._search_raw, tenant_id, query,
                                      top_k)
        cards: list[dict[str, Any]] = []
        for _hit, doc in self._documents(raw):
            sid = doc.get("stable_id")
            name = doc.get("name")
            if not isinstance(sid, str) or not sid or not name:
                continue
            aliases = doc.get("aliases") or []
            cards.append({
                "stable_id": sid,
                "name": name,
                "class_ref": doc.get("class_ref", "Unknown"),
                "props": dict(doc.get("props") or {}),
                "aliases": [a.get("alias") for a in aliases
                            if isinstance(a, dict) and a.get("alias")],
            })
        return cards

    def dense_entity_hits(self, tenant_id: str, query: str,
                          top_k: int) -> list[dict[str, Any]]:
        """Dense route slot - honestly empty this round (see module docstring).

        Returns ``[]`` with no ES call: the reason is a frozen constant
        (``DENSE_ENTITY_DISABLED_REASON``) recorded by the fusion outcome,
        not a silent skip.
        """
        model_type = getattr(self.embedding_model, "model_type", None)
        if model_type != "embedding":
            # Multimodal guard: semantic_search's multimodal branch returns
            # two concatenated knn lists, which is not a single ranked list
            # RRF can consume - only a plain text-embedding model may ever
            # serve this slot.
            return []
        # Honest boundary: even with a usable embedding model the entity
        # index has no ``embedding`` field yet, so a knn query would fail
        # on the missing field. Empty beats fabricated.
        return []


def build_es_raw_client(core: Any = None) -> EsRawListClient | None:
    """Env-gated factory; ``None`` means "ES not configured, keep today".

    ``core`` injection is for tests (and callers that already hold an ES
    core); without it the SDK core is constructed lazily from the env so
    importing this module never pulls the ES client stack. A configured
    client does not connect at construction - connection failures surface
    at call time and belong to the caller's fallback.
    """
    host = os.environ.get(ENV_ES_HOST)
    if not host:
        return None
    if core is None:
        from nexent.vector_database.elasticsearch_core import ElasticSearchCore

        core = ElasticSearchCore(host=host,
                                 api_key=os.environ.get(ENV_ES_API_KEY))
    index = os.environ.get(ENV_ENTITY_INDEX) or ENTITY_INDEX_DEFAULT
    return EsRawListClient(core=core, entity_index=index)
