"""Tests for DocAssetService.search_assets (asset-search charter M1, 2026-09-29).

The service is ES-first (injected ``es_client.asset_search`` seam, sync or
async, mirroring GraphStore.entity_lookup / L1 step 3) with a deterministic
PG ilike fallback. The PG fallback is stubbed here so the whole
orchestration - seam adaptation, uniform post-filter, normalization, PG
fill with id dedup, ranking, truncation - runs offline with no database;
the SQL itself is exercised by the real-DB suites (M2 evidence).

Every asserted value is derived from the frozen contract
(knowevo/backend/services/knowevo/doc_asset_service.py.md) and verified by
actually running: over-fetch fetch_n = min(limit*3, 30), ES score
normalized to 0-1 against the filtered set with the raw score kept in
``why.es_score_raw``, PG path scoring 0.0 with the documented
``ranked_by`` rule, parse gate = parse_status == "processed" (no quality
threshold is invented - the charter defines none), supersede folding by
default.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo.doc_asset_service import (
    DocAssetService,
    _filters_applied,
    _pg_why,
    _row_to_hit,
)
from services.knowevo.schemas import AssetHit

TENANT = "6756b0ab-39c0-462a-9745-aa12e1511fcd"


# ---------------------------------------------------------------------------
# Fakes: the ES seam (sync / async / None / raising) and the PG stub harness
# ---------------------------------------------------------------------------

class _FakeEs:
    """Synchronous injected client; records (tenant_id, query, fetch_n)."""

    def __init__(self, hits=None):
        self._hits = list(hits) if hits is not None else []
        self.calls = []

    def asset_search(self, tenant_id, query, fetch_n):
        self.calls.append((tenant_id, query, fetch_n))
        return list(self._hits)


class _FakeEsAsync(_FakeEs):
    """Async variant - the service must adapt to either form."""

    async def asset_search(self, tenant_id, query, fetch_n):
        self.calls.append((tenant_id, query, fetch_n))
        return list(self._hits)


class _FakeEsNone(_FakeEs):
    """Seam returns None - must be treated like empty (PG fallback)."""

    def asset_search(self, tenant_id, query, fetch_n):
        self.calls.append((tenant_id, query, fetch_n))


class _BoomEs:
    """Injected client whose every call fails - must degrade to PG."""

    def __init__(self):
        self.calls = 0

    def asset_search(self, tenant_id, query, fetch_n):
        self.calls += 1
        raise RuntimeError("ES unreachable")


class _Harness(DocAssetService):
    """Offline harness: ``_search_assets_pg`` is stubbed (counted, returns
    preseeded AssetHits); everything above it runs for real."""

    def __init__(self, es_client=None, pg_hits=None):
        super().__init__(es_client=es_client)
        self._pg_hits = list(pg_hits or [])
        self.pg_calls = []

    async def _search_assets_pg(self, tenant_id, q, modality, doc_type,
                                authority_min, include_superseded, limit):
        self.pg_calls.append({"tenant_id": tenant_id, "q": q,
                              "modality": modality, "doc_type": doc_type,
                              "authority_min": authority_min,
                              "include_superseded": include_superseded,
                              "limit": limit})
        return list(self._pg_hits)[:limit]


def _es_hit(id_, title, *, score=0.0, modality="text", doc_type="guideline",
            authority_level=2, asset_no=None, parse_status="processed",
            parse_quality=0.8, superseded=False, drop_parse_status=False):
    d = {"id": id_, "asset_no": asset_no or id_.upper(), "title": title,
         "modality": modality, "doc_type": doc_type,
         "authority_level": authority_level, "score": score,
         "parse_status": parse_status, "parse_quality": parse_quality,
         "superseded": superseded}
    if drop_parse_status:
        d.pop("parse_status")
    return d


def _pg_hit(id_, title, *, authority_level=2, modality="text",
            doc_type="guideline", superseded=False):
    filters = {"modality": None, "doc_type": None, "authority_min": None,
               "include_superseded": superseded}
    return AssetHit(id=id_, asset_no=id_.upper(), title=title,
                    modality=modality, doc_type=doc_type,
                    authority_level=authority_level, score=0.0,
                    why=_pg_why(filters), parse_status="processed",
                    parse_quality=0.8, superseded=superseded)


# ---------------------------------------------------------------------------
# search_assets orchestration (offline, PG stubbed)
# ---------------------------------------------------------------------------

class TestSearchAssetsESFirst:
    def test_no_es_injection_is_pg_only(self):
        """es_client=None => PG fallback only, ES never consulted."""
        pg = [_pg_hit("DA-1", "二甲双胍片说明书")]
        svc = _Harness(es_client=None, pg_hits=pg)
        res = asyncio.run(svc.search_assets(TENANT, "二甲双胍", limit=5))
        assert [h.id for h in res] == ["DA-1"]
        assert svc.es_client is None
        assert len(svc.pg_calls) == 1

    def test_es_hits_first_then_pg_fill_dedup(self):
        """ES matches lead (normalized); PG fills remaining slots, dup ids
        dropped, order = score desc then authority asc."""
        es = _FakeEs([_es_hit("A", "二甲双胍片说明书", score=2.0),
                      _es_hit("B", "二甲双胍缓释片说明书", score=1.0)])
        pg = [_pg_hit("B", "二甲双胍缓释片说明书"),
              _pg_hit("C", "二甲双胍胶囊说明书"),
              _pg_hit("D", "二甲双胍颗粒说明书")]
        svc = _Harness(es_client=es, pg_hits=pg)
        res = asyncio.run(svc.search_assets(TENANT, "二甲双胍", limit=4))
        assert [h.id for h in res] == ["A", "B", "C", "D"]
        assert [h.score for h in res] == [1.0, 0.5, 0.0, 0.0]
        assert es.calls == [(TENANT, "二甲双胍", 12)]
        assert len(svc.pg_calls) == 1

    def test_es_exception_falls_back_to_pg(self):
        pg = [_pg_hit("DA-1", "二甲双胍片说明书")]
        es = _BoomEs()
        svc = _Harness(es_client=es, pg_hits=pg)
        res = asyncio.run(svc.search_assets(TENANT, "二甲双胍", limit=5))
        assert [h.id for h in res] == ["DA-1"]
        assert es.calls == 1  # ES was attempted before failing
        assert len(svc.pg_calls) == 1

    def test_es_empty_falls_back_to_pg(self):
        pg = [_pg_hit("DA-1", "二甲双胍片说明书")]
        es = _FakeEs([])
        svc = _Harness(es_client=es, pg_hits=pg)
        res = asyncio.run(svc.search_assets(TENANT, "二甲双胍", limit=5))
        assert [h.id for h in res] == ["DA-1"]
        assert es.calls == [(TENANT, "二甲双胍", 15)]
        assert len(svc.pg_calls) == 1

    def test_es_none_falls_back_to_pg(self):
        pg = [_pg_hit("DA-1", "二甲双胍片说明书")]
        es = _FakeEsNone()
        svc = _Harness(es_client=es, pg_hits=pg)
        res = asyncio.run(svc.search_assets(TENANT, "二甲双胍", limit=5))
        assert [h.id for h in res] == ["DA-1"]
        assert len(es.calls) == 1
        assert len(svc.pg_calls) == 1

    def test_es_all_filtered_out_falls_back_to_pg(self):
        """ES returned rows but the uniform post-filter keeps none (wrong
        modality) => full PG fallback, not an empty ES answer."""
        es = _FakeEs([_es_hit("A", "二甲双胍片说明书", modality="image")])
        pg = [_pg_hit("DA-1", "二甲双胍片说明书")]
        svc = _Harness(es_client=es, pg_hits=pg)
        res = asyncio.run(svc.search_assets(TENANT, "二甲双胍", modality="text",
                                            limit=5))
        assert [h.id for h in res] == ["DA-1"]
        assert len(svc.pg_calls) == 1

    def test_postfilter_modality_doc_type_authority_and_pg_fill(self):
        """Exact modality/doc_type equality + authority floor drop ES rows;
        the surviving set is short of limit so PG fills the page."""
        es = _FakeEs([
            _es_hit("A", "二甲双胍片说明书", score=4.0,
                    modality="text", doc_type="guideline", authority_level=2),
            _es_hit("B", "B超影像报告", score=3.0,
                    modality="image", doc_type="guideline", authority_level=2),
            _es_hit("C", "降糖政策文件", score=2.0,
                    modality="text", doc_type="policy", authority_level=2),
            _es_hit("D", "降糖科普图", score=1.0,
                    modality="text", doc_type="guideline", authority_level=1),
        ])
        pg = [_pg_hit("E", "二甲双胍缓释片说明书", authority_level=2),
              _pg_hit("F", "二甲双胍胶囊说明书", authority_level=2)]
        svc = _Harness(es_client=es, pg_hits=pg)
        res = asyncio.run(svc.search_assets(
            TENANT, "降糖", modality="text", doc_type="guideline",
            authority_min=2, limit=3))
        # B (modality), C (doc_type), D (authority 1 < floor 2) filtered;
        # A survives with score 4.0/4.0 = 1.0; PG fill tops the page up.
        assert [h.id for h in res] == ["A", "E", "F"]
        assert res[0].score == 1.0
        assert res[1].score == 0.0 and res[2].score == 0.0
        assert len(svc.pg_calls) == 1
        assert svc.pg_calls[0]["modality"] == "text"
        assert svc.pg_calls[0]["doc_type"] == "guideline"
        assert svc.pg_calls[0]["authority_min"] == 2

    def test_include_superseded_default_folds_and_explicit_keeps(self):
        es_sup = _FakeEs([_es_hit("A", "旧版说明书", score=2.0, superseded=True),
                          _es_hit("B", "现行说明书", score=1.0)])
        svc = _Harness(es_client=es_sup, pg_hits=[])
        res = asyncio.run(svc.search_assets(TENANT, "说明书", limit=5))
        assert [h.id for h in res] == ["B"], (
            "superseded rows are folded by default (charter §3 lineage rule)")
        assert len(svc.pg_calls) == 1  # short page was topped up from PG
        assert svc.pg_calls[0]["include_superseded"] is False

        es_keep = _FakeEs([_es_hit("A", "旧版说明书", score=2.0, superseded=True),
                           _es_hit("B", "现行说明书", score=1.0)])
        svc = _Harness(es_client=es_keep, pg_hits=[])
        res = asyncio.run(svc.search_assets(TENANT, "说明书",
                                            include_superseded=True, limit=5))
        assert [h.id for h in res] == ["A", "B"]
        assert res[0].score == 1.0 and res[1].score == 0.5
        assert svc.pg_calls[0]["include_superseded"] is True

    def test_pg_only_path_receives_supersede_flag(self):
        """Without ES the flag must reach the fallback (it decides the SQL
        NOT EXISTS clause) - and the service must not second-guess it."""
        pg = [_pg_hit("A", "旧版说明书", superseded=True),
              _pg_hit("B", "现行说明书")]
        svc = _Harness(pg_hits=pg)
        res = asyncio.run(svc.search_assets(TENANT, "说明书",
                                            include_superseded=True, limit=5))
        assert svc.pg_calls[0]["include_superseded"] is True
        assert {h.id for h in res} == {"A", "B"}

    def test_es_normalized_top_is_one_and_raw_kept_in_why(self):
        es = _FakeEs([_es_hit("A", "二甲双胍片说明书", score=3.0),
                      _es_hit("B", "格华止说明书", score=1.5)])
        svc = _Harness(es_client=es, pg_hits=[])
        res = asyncio.run(svc.search_assets(TENANT, "说明书", limit=5))
        assert [h.score for h in res] == [1.0, 0.5]
        assert res[0].why["es_score_raw"] == 3.0
        assert res[1].why["es_score_raw"] == 1.5
        assert res[0].why["matched"] == "title+metadata"
        assert res[0].why["parse_gate"] == "processed"
        assert res[0].why["filters_applied"] == _filters_applied(
            None, None, None, False)

    def test_es_non_positive_scores_all_zero_tie_by_authority(self):
        """max raw <= 0 => everything 0.0 (no division), ties broken by
        authority_level ascending."""
        es = _FakeEs([_es_hit("A", "说明书甲", score=0.0, authority_level=3),
                      _es_hit("B", "说明书乙", score=0.0, authority_level=1)])
        svc = _Harness(es_client=es, pg_hits=[])
        res = asyncio.run(svc.search_assets(TENANT, "说明书", limit=5))
        assert [h.id for h in res] == ["B", "A"]
        assert all(h.score == 0.0 for h in res)

    def test_pg_path_shape_score_zero_and_ranked_by(self):
        """PG fallback: no relevance signal => score 0.0, and ``why`` names
        the deterministic ranking rule instead of a fabricated score."""
        filters = _filters_applied(None, None, None, False)
        why = _pg_why(filters)
        assert why == {"matched": "title ilike",
                       "ranked_by": "authority_level asc, created_at desc",
                       "es_score_raw": None,
                       "filters_applied": filters}
        row = SimpleNamespace(id="row-1", asset_no="GL-2025",
                              title="二甲双胍片说明书", modality="text",
                              doc_type="guideline", authority_level=2,
                              parse_status="processed", parse_quality=0.8)
        hit = _row_to_hit(row, filters, superseded=False)
        assert hit.score == 0.0
        assert hit.why == why
        assert hit.id == "row-1" and hit.authority_level == 2

    def test_empty_query_returns_empty_list(self):
        es = _FakeEs([])
        svc = _Harness(es_client=es, pg_hits=[_pg_hit("DA-1", "任意")])
        res = asyncio.run(svc.search_assets(TENANT, "   ", limit=5))
        assert res == []
        assert es.calls == [] and svc.pg_calls == []

    def test_sync_and_async_es_clients_both_adapted(self):
        for cls in (_FakeEs, _FakeEsAsync):
            es = cls([_es_hit("A", "二甲双胍片说明书", score=1.0)])
            svc = _Harness(es_client=es, pg_hits=[])
            res = asyncio.run(svc.search_assets(TENANT, "二甲双胍", limit=5))
            assert [h.id for h in res] == ["A"]
            assert len(es.calls) == 1

    def test_over_fetch_fetch_n_is_min_limit_times_3_30(self):
        for limit, expected in ((2, 6), (5, 15), (10, 30), (20, 30)):
            es = _FakeEs([])
            svc = _Harness(es_client=es, pg_hits=[_pg_hit("DA-1", "兜底")])
            asyncio.run(svc.search_assets(TENANT, "说明书", limit=limit))
            assert es.calls[0][2] == expected, (
                f"limit={limit} must over-fetch min(limit*3, 30)={expected}")

    def test_parse_gate_drops_non_processed_es_rows(self):
        """The only parse gate is parse_status == 'processed'; a missing
        status is not a pass either (no quality threshold is invented)."""
        es = _FakeEs([_es_hit("A", "未解析完的报告", parse_status="no_index_chunk"),
                      _es_hit("B", "已解析的报告"),
                      _es_hit("C", "状态缺失的报告", drop_parse_status=True)])
        svc = _Harness(es_client=es, pg_hits=[])
        res = asyncio.run(svc.search_assets(TENANT, "报告", limit=5))
        assert [h.id for h in res] == ["B"]

    def test_limit_truncates_without_pg_fill_when_es_is_full(self):
        es = _FakeEs([_es_hit(i, f"说明书{i}", score=float(5 - int(i)))
                      for i in ("1", "2", "3", "4", "5")])
        svc = _Harness(es_client=es, pg_hits=[_pg_hit("X", "不该被取到")])
        res = asyncio.run(svc.search_assets(TENANT, "说明书", limit=3))
        assert [h.id for h in res] == ["1", "2", "3"]
        assert svc.pg_calls == [], (
            "ES already covers the page: no fallback consultation")

    def test_es_negative_scores_all_zero_not_kept_raw(self):
        """max_raw <= 0 must wipe to 0.0; negative raw scores must
        never survive normalization (contract: max<=0 -> all 0.0)."""
        es = _FakeEs([_es_hit("A", "说明书甲", score=-2.0, authority_level=3),
                      _es_hit("B", "说明书乙", score=-1.0, authority_level=1)])
        svc = _Harness(es_client=es, pg_hits=[])
        res = asyncio.run(svc.search_assets(TENANT, "说明书", limit=5))
        assert all(h.score == 0.0 for h in res)
        assert [h.id for h in res] == ["B", "A"]
        assert res[0].why["es_score_raw"] == -1.0
        assert res[1].why["es_score_raw"] == -2.0

    def test_escape_like_literals_wildcards(self):
        """A user query is text, not a SQL LIKE pattern."""
        from backend.services.knowevo.doc_asset_service import _escape_like
        assert _escape_like("100%") == "100\\%"
        assert _escape_like("a_b") == "a\\_b"
        assert _escape_like("plain") == "plain"
        assert _escape_like("x\\y") == "x\\\\y"
