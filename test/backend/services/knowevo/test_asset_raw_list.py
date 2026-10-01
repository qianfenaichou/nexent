"""asset_search fusion phase 2: AssetRawListClient + env factory (offline).

Layer 1 (always runs): the adapter must reshape raw ES hits into the
frozen asset id space (``AssetHit.id`` / ``str``), force tenant isolation
on every route, refuse to pretend a dense route exists while the asset
index carries no embedding field, and return ``None`` from the factory
when ES env is absent (= every caller keeps today's behaviour bit for bit).

Id space freeze (l6-m2-id-space-decision §2): asset context uses
``AssetHit.id``; ``document.id`` from the knowledge-base document space
must never be mixed in. Cross-space fuse is a TypeError/ValueError at the
adapter / fusion layer, never a silent concat.
"""
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo.asset_raw_list import (
    ASSET_INDEX_DEFAULT,
    ASSET_INDEX_HAS_NO_EMBEDDING_FIELD,
    AssetRawListClient,
    build_asset_raw_client,
)

TENANT = "11111111-1111-1111-1111-111111111111"


class FakeEsClient:
    """Records raw ``search(index=, body=)`` calls; returns canned ES hits."""

    def __init__(self, hits):
        self.hits = list(hits)
        self.calls = []

    def search(self, index=None, body=None, **kwargs):
        self.calls.append({"index": list(index) if index else index,
                           "body": body})
        return {"hits": {"hits": list(self.hits)}}


class FakeCore:
    """Duck-typed ES core: only ``client.search`` is ever consumed.

    ``accurate_search`` / ``semantic_search`` must never be called: the
    platform weighted query targets KB ``title``/``content`` fields the
    asset index does not carry (pitfall #170 family), and there is no
    dense route this round.
    """

    def __init__(self, hits=None):
        self.client = FakeEsClient(hits or [])

    @property
    def calls(self):
        return self.client.calls

    def accurate_search(self, *a, **k):
        raise AssertionError("accurate_search must not be called (KB fields)")

    def semantic_search(self, *a, **k):
        raise AssertionError("semantic_search must not be called (no dense)")


def _es_hit(asset_id, title, score=2.0, **source_extra):
    source = {
        "asset_no": f"no-{asset_id}",
        "title": title,
        "modality": "text",
        "doc_type": "guideline",
        "authority_level": 2,
        "parse_status": "processed",
    }
    source.update(source_extra)
    return {
        "_score": score,
        "_id": asset_id,
        "_source": source,
        "_index": ASSET_INDEX_DEFAULT,
    }


class TestAssetBm25Hits:
    def test_reshapes_to_asset_hit_id_space(self):
        core = FakeCore([_es_hit("a-1", "二甲双胍说明书", score=3.0),
                         _es_hit("a-2", "格华止", score=1.0)])
        client = AssetRawListClient(core)
        hits = client.asset_bm25_hits(TENANT, "二甲双胍", 5)
        assert [h["id"] for h in hits] == ["a-1", "a-2"]
        assert hits[0]["title"] == "二甲双胍说明书"
        assert hits[0]["es_score"] == 3.0
        assert hits[0]["asset_no"] == "no-a-1"
        assert hits[0]["authority_level"] == 2

    def test_id_from_source_id_fallback_and_drop(self):
        """id = ES _id, else document.id; missing/blank/non-str -> drop."""
        core = FakeCore([
            {"_score": 2.0, "_id": "", "_source": {"title": "blank id"},
             "_index": "x"},
            {"_score": 1.5, "_source": {"id": "from-doc", "title": "doc id",
                                       "asset_no": "n"}, "_index": "x"},
            {"_score": 1.0, "_id": 42, "_source": {"title": "int id"},
             "_index": "x"},
            {"_score": 0.5, "_id": "ok-1", "_source": {"title": "ok",
                                                      "asset_no": "n"},
             "_index": "x"},
        ])
        client = AssetRawListClient(core)
        hits = client.asset_bm25_hits(TENANT, "q", 10)
        assert [h["id"] for h in hits] == ["from-doc", "ok-1"]

    def test_tenant_filter_forced_and_index_and_size(self):
        core = FakeCore([_es_hit("a-1", "t")])
        client = AssetRawListClient(core, asset_index="knowevo_assets_m2")
        client.asset_bm25_hits(TENANT, "查询", 7)
        assert len(core.calls) == 1
        call = core.calls[0]
        assert call["index"] == ["knowevo_assets_m2"]
        body = call["body"]
        assert body["size"] == 7
        must = body["query"]["bool"]["must"]
        assert must == [{"multi_match": {
            "query": "查询",
            "fields": ["title"],
            "operator": "and",
        }}]
        assert body["query"]["bool"]["filter"] == [
            {"term": {"tenant_id": TENANT}}]

    def test_blank_query_hits_nothing(self):
        core = FakeCore([_es_hit("a-1", "t")])
        client = AssetRawListClient(core)
        assert client.asset_bm25_hits(TENANT, "   ", 5) == []
        assert core.calls == []

    def test_none_document_hits_dropped(self):
        core = FakeCore([
            {"_score": 1.0, "_id": "x", "_index": "i"},  # no _source
            {"_score": 1.0, "_id": "y", "_source": "not-a-mapping",
             "_index": "i"},
            _es_hit("ok", "t"),
        ])
        client = AssetRawListClient(core)
        hits = client.asset_bm25_hits(TENANT, "q", 5)
        assert [h["id"] for h in hits] == ["ok"]


class TestAssetDenseHits:
    def test_honestly_empty_with_frozen_reason(self):
        core = FakeCore([_es_hit("a-1", "t")])
        client = AssetRawListClient(core)
        assert client.asset_dense_hits(TENANT, "q", 5) == []
        assert core.calls == []
        assert ASSET_INDEX_HAS_NO_EMBEDDING_FIELD == (
            "asset_index_has_no_embedding_field")

    def test_multimodal_guard_still_returns_empty(self):
        class Multimodal:
            model_type = "multimodal"

        client = AssetRawListClient(FakeCore(), embedding_model=Multimodal())
        assert client.asset_dense_hits(TENANT, "q", 5) == []


class TestBuildAssetRawClient:
    def test_env_absent_returns_none(self, monkeypatch):
        monkeypatch.delenv("ELASTICSEARCH_HOST", raising=False)
        assert build_asset_raw_client() is None

    def test_env_present_builds_default_index(self, monkeypatch):
        monkeypatch.setenv("ELASTICSEARCH_HOST", "http://127.0.0.1:9210")
        monkeypatch.setenv("ELASTICSEARCH_API_KEY", "k")
        monkeypatch.delenv("KW_ASSET_ES_INDEX", raising=False)
        core = FakeCore()
        client = build_asset_raw_client(core=core)
        assert client is not None
        assert client.core is core
        assert client.asset_index == ASSET_INDEX_DEFAULT
        assert ASSET_INDEX_DEFAULT == "knowevo_assets_m2"

    def test_env_index_override(self, monkeypatch):
        monkeypatch.setenv("ELASTICSEARCH_HOST", "http://127.0.0.1:9210")
        monkeypatch.setenv("KW_ASSET_ES_INDEX", "knowevo_assets")
        client = build_asset_raw_client(core=FakeCore())
        assert client.asset_index == "knowevo_assets"
