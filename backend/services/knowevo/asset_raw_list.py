"""Raw-list ES adapter for the asset_search fusion phase 2 (T-08, 2026-09-30).

The fusion kernel (``rrf_fusion.fuse``) consumes ranked hit lists whose
items expose a string id in ONE shared id space. For the asset context
that frozen space is ``AssetHit.id`` (str-normalised doc_asset_t row id)
- see ``l6-m2-id-space-decision-2026-09-29.md`` §2. This adapter is the
single place that reshapes raw ES hits into that space. Mixing
``document.id`` (knowledge-base document space) into an asset fuse is a
TypeError/ValueError at the adapter/fusion layer, never a silent concat.

Query construction lives here and deliberately does NOT go through the
platform ``accurate_search``: its weighted query targets the KB schema
fields ``title``/``content``, which the asset index does not carry
(``title`` only; no body field). Pointing ``accurate_search`` at this
index returns zero hits silently - the pitfall #170 family, same reason
``EsRawListClient`` owns its own DSL. The adapter issues a self-contained
``multi_match`` over ``title`` with ``operator=and`` and a forced tenant
filter via the raw ``client.search`` transport.

Two surfaces, one adapter:

- ``asset_bm25_hits``  - BM25 raw list for the asset fusion route
  (ids are ``AssetHit.id``s, ES order preserved, tenant filter forced);
- ``asset_dense_hits`` - the dense route slot. Honestly empty this round:
  the asset index carries no ``embedding`` field and ``es_index_writer``
  has a frozen dense-writeback reason, so no knn query is issued rather
  than pretending to search. Multimodal guard retained (same as the
  entity adapter): only a plain ``model_type == "embedding"`` model may
  ever serve this slot.

Environment (read here on purpose - ``backend/consts/const.py`` is a
shared wiring file and this module must not touch it):

- ``ELASTICSEARCH_HOST`` / ``ELASTICSEARCH_API_KEY``: ES credentials;
- ``KW_ASSET_ES_INDEX``: asset index name, default
  ``ASSET_INDEX_DEFAULT`` (the minimal verification index filled by the
  asset_search M2 probe). Production index naming is the A0 decision
  (``t08-es-index-name-decision-2026-09-30.md``); this round does not
  create production indices and does not change ``es_raw_list`` defaults.

``build_asset_raw_client`` returns ``None`` when the host env is absent -
every caller then keeps today's behaviour bit for bit.
"""
from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from typing import Any

ASSET_INDEX_DEFAULT = "knowevo_assets_m2"

# Audit string for the honest dense-slot emptiness (asset_fusion records it
# in the fusion outcome; never a fabricated score).
ASSET_INDEX_HAS_NO_EMBEDDING_FIELD = "asset_index_has_no_embedding_field"

ENV_ES_HOST = "ELASTICSEARCH_HOST"
ENV_ES_API_KEY = "ELASTICSEARCH_API_KEY"
ENV_ASSET_INDEX = "KW_ASSET_ES_INDEX"

_TITLE_FIELDS = ("title",)


class AssetRawListClient:
    """Thin adapter over the platform ES core for the knowevo asset index.

    ``core`` is duck-typed: only the raw ``client.search`` is ever called,
    so offline tests can inject a fake. Query construction intentionally
    does NOT go through ``accurate_search`` (see the module docstring).
    ``embedding_model`` stays None in production wiring this round.
    """

    def __init__(self, core: Any, asset_index: str = ASSET_INDEX_DEFAULT,
                 embedding_model: Any = None):
        self.core = core
        self.asset_index = asset_index
        self.embedding_model = embedding_model

    @staticmethod
    def _tenant_filter(tenant_id: str) -> list[dict[str, Any]]:
        """Tenant isolation is forced on every route, never caller-supplied."""
        return [{"term": {"tenant_id": tenant_id}}]

    @staticmethod
    def _raw_hits(response: Any) -> Iterator[tuple[Mapping, Mapping, Any]]:
        """Yield (raw_hit, source, es_id) for hits carrying a mapping source."""
        for hit in (response or {}).get("hits", {}).get("hits") or []:
            if not isinstance(hit, Mapping):
                continue
            source = hit.get("_source")
            if not isinstance(source, Mapping):
                continue
            yield hit, source, hit.get("_id")

    @staticmethod
    def _extract_id(source: Mapping, es_id: Any) -> str | None:
        """AssetHit.id space: ES ``_id`` first, else ``document.id``.

        Non-string / blank ids are dropped (the fusion may only see ids
        the AssetHit refiller can resolve). ``asset_no`` is NEVER used as
        the fused id - the id-space freeze forbids mixing the two fields
        in one list.
        """
        for raw in (es_id, source.get("id")):
            if isinstance(raw, str) and raw:
                return raw
        return None

    def _search_raw(self, tenant_id: str, query: str,
                    top_k: int) -> list[tuple[Mapping, Mapping, Any]]:
        """One asset-index search, returned as (hit, source, es_id) triples.

        The DSL is self-contained (multi_match over ``title``,
        ``operator=and``, tenant filter forced) and runs through the raw
        ES ``client.search`` transport.
        """
        body = {
            "size": top_k,
            "query": {
                "bool": {
                    "must": [{"multi_match": {
                        "query": query,
                        "fields": list(_TITLE_FIELDS),
                        "operator": "and",
                    }}],
                    "filter": self._tenant_filter(tenant_id),
                },
            },
        }
        response = self.core.client.search(index=[self.asset_index],
                                           body=body)
        return list(self._raw_hits(response))

    def asset_bm25_hits(self, tenant_id: str, query: str,
                        top_k: int) -> list[dict[str, Any]]:
        """BM25 raw list in the AssetHit.id space (ES order preserved).

        Hits without a usable string id are dropped. ``es_score`` and
        ``authority_level`` ride along for audit only - RRF consumes
        ranks, never scores. Authority reordering (if ever applied) must
        happen inside this route before fuse; the fusion layer never
        multiplies authority back onto a fused score.
        """
        if not isinstance(query, str) or not query.strip():
            return []
        hits: list[dict[str, Any]] = []
        for hit, source, es_id in self._search_raw(tenant_id, query, top_k):
            hid = self._extract_id(source, es_id)
            if hid is None:
                continue
            auth = source.get("authority_level")
            hits.append({
                "id": hid,
                "title": source.get("title"),
                "asset_no": source.get("asset_no"),
                "authority_level": auth if isinstance(auth, int) else None,
                "es_score": hit.get("_score"),
                "index": hit.get("_index"),
            })
        return hits

    def asset_dense_hits(self, tenant_id: str, query: str,
                         top_k: int) -> list[dict[str, Any]]:
        """Dense route slot - honestly empty this round (see module docstring).

        Returns ``[]`` with no ES call: the reason is a frozen constant
        (``ASSET_INDEX_HAS_NO_EMBEDDING_FIELD``) recorded by the fusion
        outcome, not a silent skip.
        """
        model_type = getattr(self.embedding_model, "model_type", None)
        if model_type != "embedding":
            # Multimodal guard: semantic_search's multimodal branch returns
            # two concatenated knn lists, which is not a single ranked list
            # RRF can consume.
            return []
        # Honest boundary: even with a usable embedding model the asset
        # index has no ``embedding`` field yet. Empty beats fabricated.
        return []


def build_asset_raw_client(core: Any = None) -> AssetRawListClient | None:
    """Env-gated factory; ``None`` means "ES not configured, keep today".

    ``core`` injection is for tests (and callers that already hold an ES
    core); without it the SDK core is constructed lazily from the env so
    importing this module never pulls the ES client stack.
    """
    host = os.environ.get(ENV_ES_HOST)
    if not host:
        return None
    if core is None:
        from nexent.vector_database.elasticsearch_core import ElasticSearchCore

        core = ElasticSearchCore(host=host,
                                 api_key=os.environ.get(ENV_ES_API_KEY))
    index = os.environ.get(ENV_ASSET_INDEX) or ASSET_INDEX_DEFAULT
    return AssetRawListClient(core=core, asset_index=index)
