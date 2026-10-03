"""Offline tests for the conflict production seam (conflict_adapter).

Pins the additive-wiring contract:
- row -> Fact mapping (claim-as-value, lineage/version from title, authority
  from doc_asset projection, half-open windows preserved);
- record -> frozen ``ConflictAdjudication`` triple;
- reconcile facade is order-stable and zero-LLM by default;
- kg_service observer seam produces records without owning merge outcomes
  (the seam is observational; it never calls the store).
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo.conflict_adapter import (
    facts_from_relation_rows,
    lineage_key_from_title,
    make_relation_conflict_observer,
    reconcile_relation_rows,
    records_to_adjudications,
    relation_row_to_fact,
    version_tag_from_title,
)
from services.knowevo.conflict_kernel import (
    detect_conflicts,
)
from services.knowevo.schemas import ConflictAdjudication

T0 = datetime(2021, 4, 1, tzinfo=timezone.utc)
T1 = datetime(2024, 11, 1, tzinfo=timezone.utc)


def _row(**kw):
    base = {
        "id": "rel-1",
        "src": "Disease:糖尿病前期",
        "dst": "Disease:空腹血糖受损",
        "rel_type": "includes",
        "claim": "糖尿病前期包括IFG",
        "valid_at": T0,
        "invalid_at": None,
    }
    base.update(kw)
    return base


def _doc(**kw):
    base = {
        "id": "doc-1",
        "title": "中国2型糖尿病防治指南（2020年版）",
        "authority_level": 2,
    }
    base.update(kw)
    return base


class TestTitleParsing:
    def test_lineage_strips_year_version(self):
        assert (
            lineage_key_from_title("中国老年糖尿病诊疗指南（2024版）")
            == "中国老年糖尿病诊疗指南"
        )
        assert (
            lineage_key_from_title("中国老年糖尿病诊疗指南（2021年版）")
            == "中国老年糖尿病诊疗指南"
        )

    def test_lineage_keeps_plain_title(self):
        assert lineage_key_from_title("三招教您辨别科学的糖尿病防治信息") == (
            "三招教您辨别科学的糖尿病防治信息"
        )

    def test_lineage_empty_title(self):
        assert lineage_key_from_title("") == ""
        assert lineage_key_from_title("   ") == ""

    def test_version_tag_from_title(self):
        assert version_tag_from_title("中国老年糖尿病诊疗指南（2024版）") == "2024版"
        assert version_tag_from_title("糖尿病足科普") == ""


class TestRelationRowToFact:
    def test_claim_is_default_value(self):
        f = relation_row_to_fact(_row(), doc=_doc())
        assert f.value == "糖尿病前期包括IFG"
        assert f.claim == "糖尿病前期包括IFG"
        assert f.fact_type == "relation"
        assert f.conflict_key() == (
            "relation",
            "Disease:糖尿病前期",
            "includes",
            "Disease:空腹血糖受损",
        )

    def test_explicit_value_overrides_claim(self):
        f = relation_row_to_fact(_row(), doc=_doc(), value="indicated")
        assert f.value == "indicated"
        assert f.claim == "糖尿病前期包括IFG"

    def test_provenance_from_doc(self):
        f = relation_row_to_fact(_row(), doc=_doc())
        assert f.source_id == "doc-1"
        assert f.source_group == "中国2型糖尿病防治指南"
        assert f.version == "2020年版"
        assert f.authority_level == 2

    def test_authority_defaults_to_3_without_doc(self):
        f = relation_row_to_fact(_row())
        assert f.authority_level == 3
        assert f.source_id == ""
        assert f.source_group == ""

    def test_authority_coerced_on_bad_doc_value(self):
        f = relation_row_to_fact(_row(), doc=_doc(authority_level="x"))
        assert f.authority_level == 3

    def test_windows_preserved(self):
        f = relation_row_to_fact(
            _row(invalid_at=T1), doc=_doc()
        )
        assert f.valid_at == T0
        assert f.invalid_at == T1

    def test_invalid_at_none_stays_open(self):
        f = relation_row_to_fact(_row(), doc=_doc())
        assert f.invalid_at is None

    def test_infinity_string_treated_as_open(self):
        f = relation_row_to_fact(_row(invalid_at="infinity"), doc=_doc())
        assert f.invalid_at is None

    def test_empty_claim_value_will_not_conflict(self):
        # Kernel contract: empty value on both sides is not a disagreement.
        a = relation_row_to_fact(_row(id="a", claim=""), doc=_doc())
        b = relation_row_to_fact(
            _row(id="b", claim=""), doc=_doc()
        )
        assert detect_conflicts([a, b]) == []


class TestFactsFromRows:
    def test_docs_and_values_keyed_by_row_id(self):
        rows = [
            _row(id="a", claim="X"),
            _row(id="b", claim="Y", src="Disease:糖尿病前期",
                 dst="Disease:糖耐量异常", rel_type="includes"),
        ]
        docs = {
            "a": _doc(id="doc-a", title="指南（2020年版）"),
            "b": _doc(id="doc-b", title="说明书", authority_level=3),
        }
        values = {"a": "va", "b": "vb"}
        facts = facts_from_relation_rows(rows, docs=docs, values=values)
        assert [f.fact_id for f in facts] == ["a", "b"]
        assert facts[0].value == "va"
        assert facts[1].value == "vb"
        assert facts[0].source_id == "doc-a"
        assert facts[1].authority_level == 3


class TestRecordsToAdjudications:
    def test_to_wire_projection(self):
        rows = [
            _row(
                id="a",
                claim="A",
                valid_at=T0,
                invalid_at=None,
            ),
            _row(
                id="b",
                claim="B",
                valid_at=T0,
                invalid_at=None,
            ),
        ]
        docs = {
            "a": _doc(id="doc-a", title="指南（2020年版）"),
            "b": _doc(id="doc-b", title="指南（2024版）"),
        }
        result = reconcile_relation_rows(
            rows, docs=docs, version_clock=T1
        )
        assert result.records
        cards = records_to_adjudications(result.records)
        assert len(cards) == len(result.records)
        for card, rec in zip(cards, result.records):
            assert isinstance(card, ConflictAdjudication)
            assert card.conflict_id == rec.conflict_id
            assert card.type == rec.kind
            assert card.resolution == rec.resolution

    def test_plain_mapping_accepted(self):
        cards = records_to_adjudications(
            [
                {
                    "conflict_id": "abc",
                    "kind": "EVOLUTION",
                    "resolution": "version_win",
                }
            ]
        )
        assert len(cards) == 1
        assert cards[0].conflict_id == "abc"
        assert cards[0].type == "EVOLUTION"
        assert cards[0].resolution == "version_win"

    def test_unknown_objects_skipped(self):
        assert records_to_adjudications([object(), None]) == []


class TestReconcileFacade:
    def _overlapping_pair(self):
        rows = [
            _row(id="a", claim="includes IFG", valid_at=T0, invalid_at=None),
            _row(
                id="b",
                claim="includes IFG and IGT",
                valid_at=T0,
                invalid_at=None,
            ),
        ]
        docs = {
            "a": _doc(id="doc-old", title="指南（2020年版）", authority_level=2),
            "b": _doc(id="doc-new", title="指南（2024版）", authority_level=2),
        }
        return rows, docs

    def test_detects_real_style_pair(self):
        rows, docs = self._overlapping_pair()
        result = reconcile_relation_rows(rows, docs=docs, version_clock=T1)
        assert result.n_conflicts == 1
        assert result.kind_counts["EVOLUTION"] == 1

    def test_zero_llm_default(self):
        rows, docs = self._overlapping_pair()

        def _boom(_c):
            raise AssertionError("llm seam must not be called")

        result = reconcile_relation_rows(
            rows, docs=docs, version_clock=T1, llm=_boom
        )
        # EVOLUTION never touches the seam even when supplied.
        assert result.records[0].llm_called is False

    def test_same_source_goes_contested(self):
        rows = [
            _row(id="a", claim="A", valid_at=T0, invalid_at=None),
            _row(id="b", claim="B", valid_at=T0, invalid_at=None),
        ]
        docs = {
            "a": _doc(id="doc-same", title="同一文件（2020年版）"),
            "b": _doc(id="doc-same", title="同一文件（2020年版）"),
        }
        result = reconcile_relation_rows(rows, docs=docs, version_clock=T1)
        assert result.kind_counts["SAME_SOURCE"] == 1
        assert result.records[0].contested is True
        assert result.records[0].resolution == "human_review"

    def test_source_authority_when_no_shared_lineage(self):
        rows = [
            _row(id="a", claim="A", valid_at=T0, invalid_at=None),
            _row(id="b", claim="B", valid_at=T0, invalid_at=None),
        ]
        docs = {
            "a": _doc(id="doc-g", title="指南（2020年版）", authority_level=2),
            "b": _doc(id="doc-l", title="说明书", authority_level=3),
        }
        result = reconcile_relation_rows(rows, docs=docs, version_clock=T1)
        assert result.kind_counts["SOURCE_AUTHORITY"] == 1
        assert result.records[0].winner_source == "doc-g"

    def test_extraction_error_flag(self):
        rows = [
            _row(id="a", claim="A", valid_at=T0, invalid_at=None),
            _row(id="b", claim="B", valid_at=T0, invalid_at=None),
        ]
        docs = {
            "a": _doc(id="doc-a"),
            "b": _doc(id="doc-b", title="别的（2021年版）"),
        }
        result = reconcile_relation_rows(
            rows,
            docs=docs,
            version_clock=T1,
            extraction_error_ids=frozenset({"a"}),
        )
        assert result.kind_counts["EXTRACTION_ERROR"] == 1
        assert result.records[0].winner_id == "b"
        assert result.records[0].resolution == "requeue_extraction"

    def test_order_independent_conflict_id(self):
        rows, docs = self._overlapping_pair()
        r1 = reconcile_relation_rows(rows, docs=docs, version_clock=T1)
        r2 = reconcile_relation_rows(
            list(reversed(rows)), docs=docs, version_clock=T1
        )
        assert [c.conflict_id for c in r1.candidates] == [
            c.conflict_id for c in r2.candidates
        ]
        assert [r.replay_key for r in r1.records] == [
            r.replay_key for r in r2.records
        ]


class TestObserverSeam:
    def test_observer_emits_records_without_store(self):
        captured = []

        def _on_records(records, result):
            captured.append((records, result))

        obs = make_relation_conflict_observer(
            docs={
                "a": _doc(id="doc-old", title="指南（2020年版）"),
                "b": _doc(id="doc-new", title="指南（2024版）"),
            },
            version_clock=T1,
            on_records=_on_records,
        )
        obs(
            {
                "existing": _row(
                    id="a", claim="A", valid_at=T0, invalid_at=None
                ),
                "incoming": _row(
                    id="b", claim="B", valid_at=T0, invalid_at=None
                ),
            }
        )
        assert len(captured) == 1
        records, result = captured[0]
        assert result.n_conflicts == 1
        assert records[0].type == "EVOLUTION"

    def test_observer_ignores_empty_event(self):
        calls = []
        obs = make_relation_conflict_observer(
            docs={}, version_clock=T1, on_records=lambda *a: calls.append(a)
        )
        obs({})
        obs({"existing": {}, "incoming": {}})
        obs({"existing": _row(id="a"), "incoming": {}})
        assert calls == []

    def test_observer_default_on_records_is_drop(self):
        obs = make_relation_conflict_observer(
            docs={
                "a": _doc(id="doc-old", title="指南（2020年版）"),
                "b": _doc(id="doc-new", title="指南（2024版）"),
            },
            version_clock=T1,
        )
        # Must not raise; records are dropped.
        obs(
            {
                "existing": _row(
                    id="a", claim="A", valid_at=T0, invalid_at=None
                ),
                "incoming": _row(
                    id="b", claim="B", valid_at=T0, invalid_at=None
                ),
            }
        )


class TestProductionSeams:
    """Optional injection into kg_service / decision_service: default zero change."""

    @pytest.mark.asyncio
    async def test_kg_service_default_observer_is_none_and_merge_unchanged(self):
        import uuid as uuid_mod

        from services.knowevo.kg_service import KGService
        from services.knowevo.schemas import Entity, ExtractionResult, Relation

        class _Store:
            def __init__(self):
                self.edges = []
                self.authority = {}
                self.published = {}
                self.evidence = {}

            async def current_relation_by_triple(self, tenant, src, dst, rel_type):
                for e in self.edges:
                    if (e["src"] == src and e["dst"] == dst
                            and e["rel_type"] == rel_type
                            and e.get("invalid_at") is None):
                        return e
                return None

            async def insert_relation(self, tenant, values):
                row = dict(values, id=uuid_mod.uuid4(), contested=False,
                           invalid_at=None)
                self.edges.append(row)
                return {"id": row["id"]}

            async def supersede_relation(self, tenant, edge_id):
                for e in self.edges:
                    if e["id"] == edge_id:
                        e["invalid_at"] = "now"
                        return True
                return False

            async def mark_contested(self, tenant, edge_id):
                for e in self.edges:
                    if e["id"] == edge_id:
                        e["contested"] = True
                        return True
                return False

            async def save_evidence(self, tenant, values):
                eid = uuid_mod.uuid4()
                self.evidence[eid] = dict(values, id=eid)
                return {"id": eid}

            async def get_evidence(self, tenant, evidence_id):
                row = self.evidence.get(evidence_id)
                if row is None:
                    return None
                return {"id": evidence_id, "doc_id": row["doc_id"]}

            async def doc_published_at(self, tenant, doc_id):
                return self.published.get(str(doc_id))

            async def doc_authority_level(self, tenant, doc_id):
                return self.authority.get(str(doc_id), 3)

            async def add_alias(self, *a, **k):
                return None

            async def append_ext_id(self, *a, **k):
                return None

            async def get_entity_by_stable_id(self, *a, **k):
                return None

            async def insert_entity(self, *a, **k):
                return None

            async def add_pending(self, *a, **k):
                return None

            async def finalize_evidence(self, *a, **k):
                return True

            async def find_by_alias(self, *a, **k):
                return None, None

            async def find_by_ext_key(self, *a, **k):
                return None

            async def entities_with_embedding(self, *a, **k):
                return []

            async def search_entities(self, *a, **k):
                return []

            async def list_confirmed_examples(self, *a, **k):
                return []

        store = _Store()
        doc_low, doc_high = uuid_mod.uuid4(), uuid_mod.uuid4()
        store.authority[str(doc_low)] = 3
        store.authority[str(doc_high)] = 1
        svc = KGService(store=store, ontology={"classes": [], "rel_types": []},
                        tenant_id="t1")
        ev_low = (await store.save_evidence("t1", {
            "doc_id": doc_low, "span_text": "a", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]
        ev_high = (await store.save_evidence("t1", {
            "doc_id": doc_high, "span_text": "b", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]

        def _ext(claim, ev):
            return ExtractionResult(
                entities=[
                    Entity(name="二甲双胍", class_ref="Drug"),
                    Entity(name="2型糖尿病", class_ref="Disease"),
                ],
                edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                                rel_type="indicated_for", claim=claim,
                                evidence_id=ev)])

        r1 = await svc.merge_delta([_ext("二线用药", ev_low)])
        assert r1.added == 2
        # Default: no observer. Conflict still resolves exactly as before.
        r2 = await svc.merge_delta([_ext("一线用药", ev_high)])
        assert r2.superseded == 1
        assert svc.conflict_observer is None

    @pytest.mark.asyncio
    async def test_kg_service_observer_fires_on_conflict_only(self):
        import uuid as uuid_mod

        from services.knowevo.kg_service import KGService
        from services.knowevo.schemas import Entity, ExtractionResult, Relation

        events = []

        class _Store:
            def __init__(self):
                self.edges = []
                self.authority = {}
                self.published = {}
                self.evidence = {}

            async def current_relation_by_triple(self, tenant, src, dst, rel_type):
                for e in self.edges:
                    if (e["src"] == src and e["dst"] == dst
                            and e["rel_type"] == rel_type
                            and e.get("invalid_at") is None):
                        return e
                return None

            async def insert_relation(self, tenant, values):
                row = dict(values, id=uuid_mod.uuid4(), contested=False,
                           invalid_at=None)
                self.edges.append(row)
                return {"id": row["id"]}

            async def supersede_relation(self, tenant, edge_id):
                for e in self.edges:
                    if e["id"] == edge_id:
                        e["invalid_at"] = "now"
                        return True
                return False

            async def mark_contested(self, tenant, edge_id):
                for e in self.edges:
                    if e["id"] == edge_id:
                        e["contested"] = True
                        return True
                return False

            async def save_evidence(self, tenant, values):
                eid = uuid_mod.uuid4()
                self.evidence[eid] = dict(values, id=eid)
                return {"id": eid}

            async def get_evidence(self, tenant, evidence_id):
                row = self.evidence.get(evidence_id)
                if row is None:
                    return None
                return {"id": evidence_id, "doc_id": row["doc_id"]}

            async def doc_published_at(self, tenant, doc_id):
                return self.published.get(str(doc_id))

            async def doc_authority_level(self, tenant, doc_id):
                return self.authority.get(str(doc_id), 3)

            async def add_alias(self, *a, **k):
                return None

            async def append_ext_id(self, *a, **k):
                return None

            async def get_entity_by_stable_id(self, *a, **k):
                return None

            async def insert_entity(self, *a, **k):
                return None

            async def add_pending(self, *a, **k):
                return None

            async def finalize_evidence(self, *a, **k):
                return True

            async def find_by_alias(self, *a, **k):
                return None, None

            async def find_by_ext_key(self, *a, **k):
                return None

            async def entities_with_embedding(self, *a, **k):
                return []

            async def search_entities(self, *a, **k):
                return []

            async def list_confirmed_examples(self, *a, **k):
                return []

        store = _Store()
        doc_low, doc_high = uuid_mod.uuid4(), uuid_mod.uuid4()
        store.authority[str(doc_low)] = 3
        store.authority[str(doc_high)] = 1

        def _obs(event):
            events.append(event)

        svc = KGService(store=store, ontology={"classes": [], "rel_types": []},
                        tenant_id="t1", conflict_observer=_obs)
        ev_low = (await store.save_evidence("t1", {
            "doc_id": doc_low, "span_text": "a", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]
        ev_high = (await store.save_evidence("t1", {
            "doc_id": doc_high, "span_text": "b", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]

        def _ext(claim, ev):
            return ExtractionResult(
                entities=[
                    Entity(name="二甲双胍", class_ref="Drug"),
                    Entity(name="2型糖尿病", class_ref="Disease"),
                ],
                edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                                rel_type="indicated_for", claim=claim,
                                evidence_id=ev)])

        await svc.merge_delta([_ext("二线用药", ev_low)])
        assert events == [], "first insert has no existing conflict"
        await svc.merge_delta([_ext("一线用药", ev_high)])
        assert len(events) == 1
        assert "existing" in events[0] and "incoming" in events[0]
        # Merge outcome unchanged by the observer.
        assert any(e.get("invalid_at") == "now" for e in store.edges)

    @pytest.mark.asyncio
    async def test_kg_service_observer_exception_swallowed(self):
        import uuid as uuid_mod

        from services.knowevo.kg_service import KGService
        from services.knowevo.schemas import Entity, ExtractionResult, Relation

        class _Store:
            def __init__(self):
                self.edges = []
                self.authority = {}
                self.published = {}
                self.evidence = {}

            async def current_relation_by_triple(self, tenant, src, dst, rel_type):
                for e in self.edges:
                    if (e["src"] == src and e["dst"] == dst
                            and e["rel_type"] == rel_type
                            and e.get("invalid_at") is None):
                        return e
                return None

            async def insert_relation(self, tenant, values):
                row = dict(values, id=uuid_mod.uuid4(), contested=False,
                           invalid_at=None)
                self.edges.append(row)
                return {"id": row["id"]}

            async def supersede_relation(self, tenant, edge_id):
                for e in self.edges:
                    if e["id"] == edge_id:
                        e["invalid_at"] = "now"
                        return True
                return False

            async def mark_contested(self, tenant, edge_id):
                for e in self.edges:
                    if e["id"] == edge_id:
                        e["contested"] = True
                        return True
                return False

            async def save_evidence(self, tenant, values):
                eid = uuid_mod.uuid4()
                self.evidence[eid] = dict(values, id=eid)
                return {"id": eid}

            async def get_evidence(self, tenant, evidence_id):
                row = self.evidence.get(evidence_id)
                if row is None:
                    return None
                return {"id": evidence_id, "doc_id": row["doc_id"]}

            async def doc_published_at(self, tenant, doc_id):
                return self.published.get(str(doc_id))

            async def doc_authority_level(self, tenant, doc_id):
                return self.authority.get(str(doc_id), 3)

            async def add_alias(self, *a, **k):
                return None

            async def append_ext_id(self, *a, **k):
                return None

            async def get_entity_by_stable_id(self, *a, **k):
                return None

            async def insert_entity(self, *a, **k):
                return None

            async def add_pending(self, *a, **k):
                return None

            async def finalize_evidence(self, *a, **k):
                return True

            async def find_by_alias(self, *a, **k):
                return None, None

            async def find_by_ext_key(self, *a, **k):
                return None

            async def entities_with_embedding(self, *a, **k):
                return []

            async def search_entities(self, *a, **k):
                return []

            async def list_confirmed_examples(self, *a, **k):
                return []

        store = _Store()
        doc_low, doc_high = uuid_mod.uuid4(), uuid_mod.uuid4()
        store.authority[str(doc_low)] = 3
        store.authority[str(doc_high)] = 1

        def _obs(_event):
            raise RuntimeError("boom")

        svc = KGService(store=store, ontology={"classes": [], "rel_types": []},
                        tenant_id="t1", conflict_observer=_obs)
        ev_low = (await store.save_evidence("t1", {
            "doc_id": doc_low, "span_text": "a", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]
        ev_high = (await store.save_evidence("t1", {
            "doc_id": doc_high, "span_text": "b", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]

        def _ext(claim, ev):
            return ExtractionResult(
                entities=[
                    Entity(name="二甲双胍", class_ref="Drug"),
                    Entity(name="2型糖尿病", class_ref="Disease"),
                ],
                edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                                rel_type="indicated_for", claim=claim,
                                evidence_id=ev)])

        await svc.merge_delta([_ext("二线用药", ev_low)])
        report = await svc.merge_delta([_ext("一线用药", ev_high)])
        assert report.superseded == 1, "observer failure must not flip merge"

    @pytest.mark.asyncio
    async def test_render_card_conflict_records_merged_and_deduped(self):
        from services.knowevo.decision_service import DecisionService
        from services.knowevo.schemas import (
            DECISION_RECOMMEND,
            EvidenceChain,
            EvidenceItem,
            Provenance,
        )

        card_json = (
            '{"decision": "RECOMMEND", "candidates": [{"option": "A",'
            '"score": 0.9, "confidence_calibrated": 0.9,'
            '"evidence_chain": [], "risks": []}],'
            '"conflict_adjudications": [{"conflict_id": "c1",'
            '"type": "EVOLUTION", "resolution": "version_win"},'
            '{"conflict_id": "c2", "type": "SAME_SOURCE",'
            '"resolution": "human_review"}]}'
        )

        class _LLM:
            def __init__(self):
                self.calls = []

            async def __call__(self, prompt, *, kind, **kw):
                self.calls.append(kind)
                return card_json

        chain = EvidenceChain()
        chain.items.append(EvidenceItem(
            claim="x",
            provenance=Provenance(doc="d", span="s", kg_path=[]),
            tag="EXTRACTED", source_channel="kg"))
        svc = DecisionService(llm=_LLM(), tenant_id="t")
        pre = [
            ConflictAdjudication(
                conflict_id="c1", type="EVOLUTION", resolution="version_win"),
            ConflictAdjudication(
                conflict_id="c1", type="EVOLUTION", resolution="version_win"),
            ConflictAdjudication(
                conflict_id="k9", type="SOURCE_AUTHORITY",
                resolution="authority_win"),
        ]
        card = await svc.render_card("q", chain, conflict_records=pre)
        ids = [a.conflict_id for a in card.conflict_adjudications]
        assert ids == ["c1", "k9", "c2"], (
            "kernel records first, deduped by conflict_id, LLM appends new")
        assert card.decision == DECISION_RECOMMEND

    @pytest.mark.asyncio
    async def test_render_card_default_conflict_records_is_zero_change(self):
        from services.knowevo.decision_service import DecisionService
        from services.knowevo.schemas import EvidenceChain, EvidenceItem, Provenance

        card_json = (
            '{"decision": "RECOMMEND", "candidates": [{"option": "A",'
            '"score": 0.9, "confidence_calibrated": 0.9,'
            '"evidence_chain": [], "risks": []}],'
            '"conflict_adjudications": [{"conflict_id": "c1",'
            '"type": "EVOLUTION", "resolution": "version_win"}]}'
        )

        class _LLM:
            async def __call__(self, prompt, *, kind, **kw):
                return card_json

        chain = EvidenceChain()
        chain.items.append(EvidenceItem(
            claim="x",
            provenance=Provenance(doc="d", span="s", kg_path=[]),
            tag="EXTRACTED", source_channel="kg"))
        svc = DecisionService(llm=_LLM(), tenant_id="t")
        card = await svc.render_card("q", chain)
        assert [a.conflict_id for a in card.conflict_adjudications] == ["c1"]

    @pytest.mark.asyncio
    async def test_render_card_conflict_records_on_refusal_path(self):
        from services.knowevo.decision_service import DecisionService
        from services.knowevo.schemas import (
            DECISION_INSUFFICIENT,
            EvidenceChain,
        )

        class _LLM:
            def __init__(self):
                self.calls = []

            async def __call__(self, prompt, *, kind, **kw):
                self.calls.append(kind)
                return "{}"

        svc = DecisionService(llm=_LLM(), tenant_id="t")
        pre = [ConflictAdjudication(
            conflict_id="k1", type="SAME_SOURCE", resolution="human_review")]
        card = await svc.render_card(
            "q", EvidenceChain(), conflict_records=pre)
        assert card.decision == DECISION_INSUFFICIENT
        assert [a.conflict_id for a in card.conflict_adjudications] == ["k1"]


class TestPolarityValue:
    """A+B heuristic (e10-input-prep §3.2): rel_type vocab + claim keywords."""

    def test_rel_type_positive(self):
        from services.knowevo.conflict_adapter import polarity_value
        assert polarity_value("indicated_for", "需要长期胰岛素替代治疗") == "pos"
        assert polarity_value("treats", "有效改善空腹血糖") == "pos"
        assert polarity_value("prevents", "预防T2DM发生") == "pos"

    def test_rel_type_negative(self):
        from services.knowevo.conflict_adapter import polarity_value
        assert polarity_value("contraindicated_for", "孕期不宜使用") == "neg"
        assert polarity_value("has_adverse_effect", "导致低血糖") == "neg"
        assert polarity_value("not_recommended_for", "不推荐筛查") == "neg"

    def test_claim_keyword_overrides_rel_type(self):
        from services.knowevo.conflict_adapter import polarity_value
        # indicated_for but claim says stop -> neg (B wins)
        assert polarity_value(
            "indicated_for", "使用对比剂时要短期停用二甲双胍") == "neg"
        # unknown rel_type but claim says first-line -> pos
        assert polarity_value("applies_to", "首选胰岛素治疗") == "pos"

    def test_neg_wins_when_both_tokens(self):
        from services.knowevo.conflict_adapter import polarity_value
        assert polarity_value(
            "indicated_for", "若无禁忌证，推荐二甲双胍治疗") == "neg"

    def test_neutral_when_no_signal(self):
        from services.knowevo.conflict_adapter import polarity_value
        assert polarity_value("belongs_to_class", "属于GLP-1RA类药物") == "neutral"
        assert polarity_value("includes", "包括IFG、IGT") == "neutral"

    def test_complementary_claims_share_polarity(self):
        # The e10-input-prep §3.3 point: multi-claim groups are NOT conflicts.
        from services.knowevo.conflict_adapter import polarity_value
        a = polarity_value(
            "indicated_for",
            "新诊断T2DM患者如有明显的高血糖症状，首选胰岛素治疗")
        b = polarity_value(
            "indicated_for",
            "在生活方式和口服降糖药联合治疗的基础上，若血糖仍未达标，可以开始胰岛素治疗")
        c = polarity_value(
            "indicated_for",
            "T2DM患者需要长期胰岛素替代治疗")
        assert a == b == c == "pos"


class TestH2ConflictReconciler:
    """DecisionService.conflict_reconciler: optional (facts, clock) seam."""

    @pytest.mark.asyncio
    async def test_reconciler_called_and_records_merged(self):
        from services.knowevo.decision_service import DecisionService
        from services.knowevo.schemas import EvidenceChain, EvidenceItem, Provenance

        card_json = (
            '{"decision": "RECOMMEND", "candidates": [{"option": "A",'
            '"score": 0.9, "confidence_calibrated": 0.9,'
            '"evidence_chain": [], "risks": []}],'
            '"conflict_adjudications": []}'
        )

        class _LLM:
            async def __call__(self, prompt, *, kind, **kw):
                return card_json

        class _Rec:
            def __init__(self, cid, kind, res):
                self._w = {"conflict_id": cid, "type": kind,
                           "resolution": res}

            def to_wire(self):
                return dict(self._w)

        class _Result:
            records = (_Rec("k1", "SAME_SOURCE", "human_review"),)

        calls = []

        def _reconciler(facts, clock):
            calls.append((facts, clock))
            return _Result()

        chain = EvidenceChain()
        chain.items.append(EvidenceItem(
            claim="x",
            provenance=Provenance(doc="d", span="s", kg_path=[]),
            tag="EXTRACTED", source_channel="kg"))
        svc = DecisionService(llm=_LLM(), tenant_id="t",
                              conflict_reconciler=_reconciler)
        card = await svc.render_card(
            "q", chain, conflict_facts=["f1", "f2"])
        assert len(calls) == 1
        assert calls[0][0] == ["f1", "f2"]
        assert [a.conflict_id for a in card.conflict_adjudications] == ["k1"]

    @pytest.mark.asyncio
    async def test_reconciler_default_none_zero_change(self):
        from services.knowevo.decision_service import DecisionService
        from services.knowevo.schemas import EvidenceChain, EvidenceItem, Provenance

        card_json = (
            '{"decision": "RECOMMEND", "candidates": [{"option": "A",'
            '"score": 0.9, "confidence_calibrated": 0.9,'
            '"evidence_chain": [], "risks": []}],'
            '"conflict_adjudications": [{"conflict_id": "c1",'
            '"type": "EVOLUTION", "resolution": "version_win"}]}'
        )

        class _LLM:
            async def __call__(self, prompt, *, kind, **kw):
                return card_json

        chain = EvidenceChain()
        chain.items.append(EvidenceItem(
            claim="x",
            provenance=Provenance(doc="d", span="s", kg_path=[]),
            tag="EXTRACTED", source_channel="kg"))
        svc = DecisionService(llm=_LLM(), tenant_id="t")
        assert svc.conflict_reconciler is None
        card = await svc.render_card("q", chain, conflict_facts=["f"])
        assert [a.conflict_id for a in card.conflict_adjudications] == ["c1"]

    @pytest.mark.asyncio
    async def test_reconciler_exception_swallowed(self):
        from services.knowevo.decision_service import DecisionService
        from services.knowevo.schemas import EvidenceChain, EvidenceItem, Provenance

        card_json = (
            '{"decision": "RECOMMEND", "candidates": [{"option": "A",'
            '"score": 0.9, "confidence_calibrated": 0.9,'
            '"evidence_chain": [], "risks": []}],'
            '"conflict_adjudications": []}'
        )

        class _LLM:
            async def __call__(self, prompt, *, kind, **kw):
                return card_json

        def _boom(facts, clock):
            raise RuntimeError("reconciler boom")

        chain = EvidenceChain()
        chain.items.append(EvidenceItem(
            claim="x",
            provenance=Provenance(doc="d", span="s", kg_path=[]),
            tag="EXTRACTED", source_channel="kg"))
        svc = DecisionService(llm=_LLM(), tenant_id="t",
                              conflict_reconciler=_boom)
        card = await svc.render_card("q", chain, conflict_facts=["f"])
        assert card.conflict_adjudications == []
        assert card.decision == "RECOMMEND"

    @pytest.mark.asyncio
    async def test_conflict_records_win_over_reconciler(self):
        from services.knowevo.decision_service import DecisionService
        from services.knowevo.schemas import (
            ConflictAdjudication,
            EvidenceChain,
            EvidenceItem,
            Provenance,
        )

        card_json = (
            '{"decision": "RECOMMEND", "candidates": [{"option": "A",'
            '"score": 0.9, "confidence_calibrated": 0.9,'
            '"evidence_chain": [], "risks": []}],'
            '"conflict_adjudications": []}'
        )

        class _LLM:
            async def __call__(self, prompt, *, kind, **kw):
                return card_json

        def _reconciler(facts, clock):
            raise AssertionError("should not run when records given")

        chain = EvidenceChain()
        chain.items.append(EvidenceItem(
            claim="x",
            provenance=Provenance(doc="d", span="s", kg_path=[]),
            tag="EXTRACTED", source_channel="kg"))
        svc = DecisionService(llm=_LLM(), tenant_id="t",
                              conflict_reconciler=_reconciler)
        pre = [ConflictAdjudication(
            conflict_id="pre1", type="EVOLUTION", resolution="version_win")]
        card = await svc.render_card(
            "q", chain, conflict_records=pre, conflict_facts=["f"])
        assert [a.conflict_id for a in card.conflict_adjudications] == ["pre1"]


class TestValueFromClaim:
    """P1: preferred value extractor; claim stays provenance only."""

    def test_alias_matches_polarity(self):
        from services.knowevo.conflict_adapter import (
            polarity_value,
            value_from_claim,
        )
        for rt, claim in (
            ("indicated_for", "首选二甲双胍"),
            ("indicated_for", "使用对比剂时要短期停用"),
            ("belongs_to_class", "属于GLP-1RA"),
            ("contraindicated_for", "均不推荐应用于孕期"),
        ):
            assert value_from_claim(rt, claim) == polarity_value(rt, claim)

    def test_complementary_do_not_disagree(self):
        from services.knowevo.conflict_adapter import value_from_claim
        a = value_from_claim("indicated_for", "首选胰岛素治疗")
        b = value_from_claim("indicated_for", "血糖未达标可以开始胰岛素治疗")
        assert a == b == "pos"

    def test_relation_row_to_fact_accepts_polarity_value(self):
        from services.knowevo.conflict_adapter import (
            relation_row_to_fact,
            value_from_claim,
        )
        row = _row(claim="使用对比剂时要短期停用二甲双胍", rel_type="indicated_for")
        v = value_from_claim("indicated_for", row["claim"])
        f = relation_row_to_fact(row, doc=_doc(), value=v)
        assert f.value == "neg"
        assert f.claim == "使用对比剂时要短期停用二甲双胍"
