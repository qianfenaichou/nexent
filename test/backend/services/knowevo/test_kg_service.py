"""
Unit and integration tests for services/knowevo/kg_service.py (T-06).

Layer 1 (always runs): pure-function behavior of the K2 pipeline -
ontology subgraph retrieval (P0-4), plain-text chunking, table channel
determinism, anchored parsing (unmappable -> pending), three-level
alignment including the P0-1 external-key/alias-table branch that makes
"格华止 = 二甲双胍" work, threshold calibration ROC semantics, the K2 3.2
merge conflict table (NEW / ALIAS / CONTRA / CONTENDED), split round-trip,
pending-pool accounting, and tenant isolation. No database and no LLM:
the LLM is injected as a callable (fake in tests), persistence uses
FakeStore.

Layer 2 (RUN_POSTGRES_INTEGRATION=1): real-Postgres run of extract ->
merge_delta -> entity/relation/evidence rows, pending pool rows, and the
kg_extract_run_t idempotency ledger. Same gate pattern as
test_ontology_service.py (pitfalls #14 template).
"""
import os
import sys
import uuid as uuid_mod
from pathlib import Path

# Upstream convention: repo backend/ on sys.path, import via the
# services.* prefix (see test_ontology_service.py header for the shadowing
# note - test tree has its own backend/services package).
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo.kg_service import (
    ADJUDICATE_EXECUTE_LINE,
    DEFAULT_TAU1,
    DEFAULT_TAU2,
    EXT_SCHEMES,
    MAX_SUBGRAPH_CLASSES,
    KGService,
    chunk_plain_text,
    ontology_subgraph_text,
    retrieve_ontology_subgraph,
)
from services.knowevo.schemas import (
    Entity,
    EvidenceSpan,
    ExtractionResult,
    LabeledPair,
    ParsedTable,
    Relation,
    cosine,
    normalize_name_key,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"

ONTOLOGY = {
    "classes": [
        {"name": "Drug", "stable_id": "Drug", "parent": None,
         "props": ["generic_name", "atc_code"], "aliases": ["药物", "药品"]},
        {"name": "Biguanide", "stable_id": "Biguanide", "parent": "Drug",
         "props": ["mechanism"], "aliases": ["双胍类"]},
        {"name": "AlphaGlucosidaseInhibitor",
         "stable_id": "AlphaGlucosidaseInhibitor", "parent": "Drug",
         "props": ["mechanism"], "aliases": ["α-糖苷酶抑制剂"]},
        {"name": "Disease", "stable_id": "Disease", "parent": None,
         "props": ["icd_code"], "aliases": ["疾病"]},
        {"name": "Type2Diabetes", "stable_id": "Type2Diabetes",
         "parent": "Disease", "props": ["diagnosis_criteria"],
         "aliases": ["2型糖尿病"]},
        {"name": "Guideline", "stable_id": "Guideline", "parent": None,
         "props": ["publish_year"], "aliases": ["指南"]},
    ],
    "rel_types": [{"rel_type": "indicated_for", "domain": "Drug",
                   "range": "Disease"},
                  {"rel_type": "contraindicated_with", "domain": "Drug",
                   "range": "Drug"}],
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeLLM:
    """Deterministic stand-in for the mid-tier extraction call.

    Supports the two call shapes kg_service uses:
    - extraction: (kind='extract') -> {"entities": [...], "edges": [...]}
    - adjudication: (kind='align') -> {"action", "target", "confidence"}
    """

    def __init__(self, extraction=None, adjudication=None):
        self.extraction = extraction or {"entities": [], "edges": []}
        self.adjudication = adjudication or {"action": "new", "confidence": 0.0}
        self.calls = []

    async def __call__(self, prompt: str, *, kind: str, **kwargs):
        self.calls.append({"prompt": prompt, "kind": kind, **kwargs})
        if kind == "align":
            return dict(self.adjudication)
        return dict(self.extraction)


class FakeStore:
    """In-memory graph store mirroring PgStore's contract.

    ``rows`` are per-tenant dicts keyed by stable_id; edges and evidence
    live in flat lists with tenant tags so isolation is testable.
    """

    def __init__(self, ontology=None):
        self.ontology = ontology or ONTOLOGY
        self.entities: dict[str, dict] = {}   # key: (tenant, stable_id)
        self.edges: list[dict] = []
        self.evidence: dict = {}
        self.pending: dict[str, dict] = {}    # key: (tenant, name)
        self.extract_runs: set[tuple[str, str]] = set()
        self.authority: dict = {}             # doc_id -> authority_level

    # -- ontology
    async def load_ontology_snapshot(self, tenant_id):
        return self.ontology

    # -- entities
    async def get_entity_by_stable_id(self, tenant_id, stable_id):
        return self.entities.get((tenant_id, stable_id))

    async def insert_entity(self, tenant_id, values):
        row = dict(values)
        self.entities[(tenant_id, row["stable_id"])] = row
        return {"id": uuid_mod.uuid4()}

    async def list_entities(self, tenant_id, stable_ids):
        return {sid: self.entities[(tenant_id, sid)]
                for sid in stable_ids
                if (tenant_id, sid) in self.entities}

    async def find_by_ext_key(self, tenant_id, value, scheme):
        for (t, _), row in self.entities.items():
            if t != tenant_id or row.get("status") != "active":
                continue
            for e in (row.get("props") or {}).get("ext_ids", []):
                if e.get("value") == value and e.get("scheme") == scheme:
                    return row
        return None

    async def find_by_alias(self, tenant_id, alias):
        needle = normalize_name_key(alias)
        for (t, _), row in self.entities.items():
            if t != tenant_id or row.get("status") != "active":
                continue
            if normalize_name_key(row.get("name", "")) == needle:
                return row, "form"
            for a in row.get("aliases") or []:
                if normalize_name_key(a.get("alias", "")) == needle:
                    return row, a.get("type", "form")
        return None, None

    async def entities_with_embedding(self, tenant_id):
        return [row for (t, _), row in self.entities.items()
                if t == tenant_id and row.get("status") == "active"
                and row.get("embedding")]

    async def add_alias(self, tenant_id, stable_id, alias, alias_type):
        row = self.entities.get((tenant_id, stable_id))
        if row is None:
            return False
        row.setdefault("aliases", []).append({"alias": alias,
                                              "type": alias_type})
        return True

    async def append_ext_id(self, tenant_id, stable_id, value, scheme):
        row = self.entities.get((tenant_id, stable_id))
        if row is None:
            return False
        props = dict(row.get("props") or {})
        ext_ids = list(props.get("ext_ids") or [])
        if not any(e.get("value") == value and e.get("scheme") == scheme
                   for e in ext_ids):
            ext_ids.append({"value": value, "scheme": scheme})
        props["ext_ids"] = ext_ids
        row["props"] = props
        return True

    async def deprecate_entity(self, tenant_id, stable_id, split_into):
        row = self.entities.get((tenant_id, stable_id))
        if row is None:
            return False
        row["status"] = "split"
        row["split_into"] = list(split_into)
        return True

    # -- relations
    async def current_relation_by_triple(self, tenant_id, src, dst, rel_type):
        for e in self.edges:
            if (e["tenant"] == tenant_id and e["src"] == src
                    and e["dst"] == dst and e["rel_type"] == rel_type
                    and e.get("invalid_at") is None):
                return e
        return None

    async def insert_relation(self, tenant_id, values):
        row = dict(values, tenant=tenant_id, id=uuid_mod.uuid4(),
                   contested=False, invalid_at=None)
        self.edges.append(row)
        return {"id": row["id"]}

    async def supersede_relation(self, tenant_id, edge_id):
        for e in self.edges:
            if e["id"] == edge_id:
                e["invalid_at"] = "now"
                return True
        return False

    async def mark_contested(self, tenant_id, edge_id):
        for e in self.edges:
            if e["id"] == edge_id:
                e["contested"] = True
                return True
        return False

    async def list_relations_by_entity(self, tenant_id, stable_id):
        return [e for e in self.edges if e["tenant"] == tenant_id
                and stable_id in (e["src"], e["dst"])]

    async def reassign_edge_endpoint(self, tenant_id, old_sid, new_sid, which):
        count = 0
        for e in self.edges:
            if e["tenant"] == tenant_id and e[which] == old_sid:
                e[which] = new_sid
                count += 1
        return count

    # -- evidence
    async def save_evidence(self, tenant_id, values):
        eid = uuid_mod.uuid4()
        self.evidence[eid] = dict(values, tenant=tenant_id, id=eid)
        return {"id": eid}

    async def get_evidence(self, tenant_id, evidence_id):
        row = self.evidence.get(evidence_id)
        if row is None or row["tenant"] != tenant_id:
            return None
        return {"id": evidence_id, "doc_id": row["doc_id"], "tag": row["tag"],
                "modality": row["modality"]}

    async def finalize_evidence(self, tenant_id, evidence_id, entity_refs,
                                edge_ids):
        row = self.evidence.get(evidence_id)
        if row is None:
            return False
        row["entity_refs"] = list(entity_refs)
        row["edge_ids"] = list(edge_ids)
        return True

    async def doc_authority_level(self, tenant_id, doc_id):
        return self.authority.get(str(doc_id), 3)

    async def list_confirmed_examples(self, tenant_id, limit=2):
        return [{"span_text": "示例证据段"} for _ in range(limit)]

    # -- pending
    async def add_pending(self, tenant_id, name, evidence_id,
                          suggested_class=None):
        key = (tenant_id, name)
        row = self.pending.get(key)
        if row is None:
            self.pending[key] = {"id": uuid_mod.uuid4(), "name": name,
                                 "mention_count": 1,
                                 "evidence_ids": [evidence_id] if evidence_id
                                 else [],
                                 "suggested_class": suggested_class,
                                 "status": "pending"}
        else:
            row["mention_count"] += 1
            if evidence_id and evidence_id not in row["evidence_ids"]:
                row["evidence_ids"].append(evidence_id)
        return True

    async def list_pending(self, tenant_id, status="pending"):
        return [dict(r) for (t, _), r in self.pending.items()
                if t == tenant_id and (status is None or r["status"] == status)]

    async def update_pending_status(self, tenant_id, ids, status):
        n = 0
        for (t, _), r in self.pending.items():
            if t == tenant_id and r["id"] in ids:
                r["status"] = status
                n += 1
        return n

    # -- query surface
    async def search_entities(self, tenant_id, query, limit):
        return [row for (t, _), row in self.entities.items()
                if t == tenant_id and query in row.get("name", "")][:limit]

    async def neighbors(self, tenant_id, stable_ids, hop=1):
        touched = set(stable_ids)
        out = []
        for e in self.edges:
            if e["tenant"] != tenant_id or e.get("invalid_at") is not None:
                continue
            if e["src"] in touched or e["dst"] in touched:
                out.append(e)
                touched.add(e["src"])
                touched.add(e["dst"])
        return out

    # -- run ledger
    async def is_extract_done(self, tenant_id, span_hash):
        return (tenant_id, span_hash) in self.extract_runs

    async def record_extract_run(self, tenant_id, run_id, span_hash,
                                 channel, tokens_spent):
        self.extract_runs.add((tenant_id, span_hash))
        return True


def _svc(store=None, llm=None, tenant=TENANT_A, **kwargs) -> KGService:
    return KGService(store=store, llm=llm, ontology=ONTOLOGY,
                     tenant_id=tenant, **kwargs)


def _entity(name, class_ref, **kwargs) -> Entity:
    return Entity(name=name, class_ref=class_ref, **kwargs)


# ---------------------------------------------------------------------------
# Layer 1: subgraph retrieval (P0-4)
# ---------------------------------------------------------------------------

class TestOntologySubgraph:
    def test_retrieves_matching_class_with_parent_chain(self):
        sub = retrieve_ontology_subgraph(
            "二甲双胍属于双胍类口服降糖药", ONTOLOGY["classes"])
        names = [c["name"] for c in sub]
        assert "Biguanide" in names
        # parent chain included: Drug comes before Biguanide (top-down)
        assert "Drug" in names
        assert names.index("Drug") < names.index("Biguanide")

    def test_alias_hit_scores(self):
        sub = retrieve_ontology_subgraph("2型糖尿病的一线治疗", ONTOLOGY["classes"])
        names = [c["name"] for c in sub]
        assert "Type2Diabetes" in names
        assert "Disease" in names

    def test_no_hit_fills_with_frequency_but_respects_top_k(self):
        sub = retrieve_ontology_subgraph("完全无关的一段文本", ONTOLOGY["classes"],
                                         top_k=3)
        assert len(sub) == 3

    def test_top_k_cap_respected(self):
        sub = retrieve_ontology_subgraph(
            "药物 疾病 指南 双胍类 2型糖尿病 机制", ONTOLOGY["classes"], top_k=4)
        assert len(sub) == 4

    def test_default_cap_constant(self):
        assert MAX_SUBGRAPH_CLASSES == 15

    def test_never_mutates_input(self):
        import copy
        snapshot = copy.deepcopy(ONTOLOGY["classes"])
        retrieve_ontology_subgraph("二甲双胍", ONTOLOGY["classes"])
        assert ONTOLOGY["classes"] == snapshot

    def test_subgraph_text_is_compact_and_labelled(self):
        sub = retrieve_ontology_subgraph("二甲双胍", ONTOLOGY["classes"])
        text = ontology_subgraph_text(sub)
        assert "cls:" in text
        assert "parent=" in text
        assert len(text) < 2000  # compact, not the 15k bomb


# ---------------------------------------------------------------------------
# Layer 1: chunking helper
# ---------------------------------------------------------------------------

class TestChunkPlainText:
    def test_splits_on_blank_lines_and_respects_size(self):
        text = ("第一段内容。" * 20) + "\n\n" + ("第二段内容。" * 20) + \
               "\n\n" + "第三段。"
        spans = chunk_plain_text(text, size=80)
        assert len(spans) >= 3
        assert all(s.text.strip() for s in spans)

    def test_empty_text_yields_no_spans(self):
        assert chunk_plain_text("") == []
        assert chunk_plain_text("\n\n\n") == []

    def test_span_hash_stable_and_content_sensitive(self):
        spans = chunk_plain_text("甲\n\n乙", size=10)
        h1 = [s.span_hash() for s in spans]
        h2 = [s.span_hash() for s in chunk_plain_text("甲\n\n乙", size=10)]
        assert h1 == h2
        assert len(set(h1)) == len(h1)
        other = chunk_plain_text("甲\n\n丙", size=10)
        assert [s.span_hash() for s in other] != h1


# ---------------------------------------------------------------------------
# Layer 1: table channel (deterministic)
# ---------------------------------------------------------------------------

class TestTableChannel:
    def test_headers_become_props_rows_become_entities(self):
        svc = _svc()
        table = ParsedTable(
            doc_id=uuid_mod.uuid4(), chunk_idx=0,
            headers=["名称", "剂量", "适应症"],
            rows=[["二甲双胍", "500mg tid", "2型糖尿病"],
                  ["阿卡波糖", "50mg tid", "2型糖尿病"]],
            class_hint="Drug")
        result = svc.extract_table(table)
        assert result.channel == "table"
        assert [e.name for e in result.entities] == ["二甲双胍", "阿卡波糖"]
        assert result.entities[0].props["剂量"] == "500mg tid"
        assert result.entities[0].class_ref == "Drug"
        assert result.pending == []
        # zero LLM: service has no llm injected at all
        assert svc.llm is None

    def test_unknown_class_hint_routes_all_rows_to_pending(self):
        svc = _svc()
        table = ParsedTable(doc_id=uuid_mod.uuid4(), chunk_idx=0,
                            headers=["名称", "值"], rows=[["新概念", "x"]],
                            class_hint="NotAClass")
        result = svc.extract_table(table)
        assert result.entities == []
        assert len(result.pending) == 1
        assert result.pending[0].name == "新概念"

    def test_duplicate_rows_deduped_and_empty_cells_skipped(self):
        svc = _svc()
        table = ParsedTable(doc_id=uuid_mod.uuid4(), chunk_idx=0,
                            headers=["名称", "剂量"],
                            rows=[["二甲双胍", "500mg"], ["二甲双胍", "850mg"],
                                  ["", "x"], ["阿卡波糖", ""]],
                            class_hint="Drug")
        result = svc.extract_table(table)
        assert [e.name for e in result.entities] == ["二甲双胍", "阿卡波糖"]
        assert result.entities[1].props == {}

    def test_same_schema_as_llm_channel(self):
        svc = _svc()
        table_result = svc.extract_table(ParsedTable(
            doc_id=uuid_mod.uuid4(), chunk_idx=0, headers=["名称"],
            rows=[["二甲双胍"]], class_hint="Drug"))
        llm_result = svc._parse_extraction({
            "entities": [{"name": "二甲双胍", "class_ref": "Drug"}],
            "edges": []})
        for result in (table_result, llm_result):
            assert isinstance(result, ExtractionResult)
            assert isinstance(result.entities[0], Entity)
            assert result.entities[0].name == "二甲双胍"
            assert result.entities[0].class_ref == "Drug"
            assert result.entities[0].tag == "EXTRACTED"

    def test_table_span_hash_distinct_from_text(self):
        table = ParsedTable(doc_id="d1", chunk_idx=0, headers=["名称"],
                            rows=[["二甲双胍"]])
        text = EvidenceSpan(doc_id="d1", chunk_idx=0, text="二甲双胍")
        assert table.span_hash() != text.span_hash()


# ---------------------------------------------------------------------------
# Layer 1: anchored parsing
# ---------------------------------------------------------------------------

class TestAnchoredParsing:
    def test_unmappable_class_routes_to_pending(self):
        svc = _svc()
        result = svc._parse_extraction({
            "entities": [{"name": "某种未知概念", "class_ref": "NotAClass"},
                         {"name": "二甲双胍", "class_ref": "Drug"}],
            "edges": []})
        assert [e.name for e in result.entities] == ["二甲双胍"]
        assert [e.name for e in result.pending] == ["某种未知概念"]
        assert result.pending[0].class_ref == "NotAClass"

    def test_cls_prefix_accepted(self):
        svc = _svc()
        result = svc._parse_extraction({
            "entities": [{"name": "二甲双胍", "class_ref": "cls:Drug"}],
            "edges": []})
        assert result.entities[0].class_ref == "Drug"

    def test_type_field_alias_and_tag_default(self):
        svc = _svc()
        result = svc._parse_extraction({
            "entities": [{"name": "2型糖尿病", "type": "Disease",
                          "tag": "inferred"}], "edges": []})
        assert result.entities[0].class_ref == "Disease"
        assert result.entities[0].tag == "INFERRED"

    def test_edges_parsed_and_deduped(self):
        svc = _svc()
        result = svc._parse_extraction({
            "entities": [{"name": "胰岛素", "class_ref": "Drug"}],
            "edges": [
                {"src": "胰岛素", "dst": "2型糖尿病",
                 "rel_type": "indicated_for", "claim": "一线治疗"},
                {"src": "胰岛素", "dst": "2型糖尿病",
                 "rel_type": "indicated_for", "claim": "重复"},
                {"src": "", "dst": "x", "rel_type": "y"},
            ]})
        assert len(result.edges) == 1
        assert result.edges[0].claim == "一线治疗"

    def test_ext_id_carried_through(self):
        svc = _svc()
        result = svc._parse_extraction({
            "entities": [{"name": "二甲双胍", "class_ref": "Drug",
                          "ext_id": "A10BA02", "ext_scheme": "atc"}],
            "edges": []})
        assert result.entities[0].ext_id == "A10BA02"
        assert result.entities[0].ext_scheme == "atc"

    def test_tokens_spent_read(self):
        svc = _svc()
        result = svc._parse_extraction({"entities": [], "edges": [],
                                        "tokens_spent": 123})
        assert result.tokens_spent == 123


# ---------------------------------------------------------------------------
# Layer 1: three-level alignment (P0-1)
# ---------------------------------------------------------------------------

class TestAlignment:
    @pytest.mark.asyncio
    async def test_l0_external_key_merges_brand_to_generic(self):
        """P0-1 regression: 格华止 (brand) meets 二甲双胍 (generic) through
        the external key, never through similarity."""
        store = FakeStore()
        await store.insert_entity(TENANT_A, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug", "props": {
                "ext_ids": [{"value": "A10BA02", "scheme": "atc"}]},
            "status": "active"})
        svc = _svc(store=store)
        decision = await svc.align(_entity("格华止", "Drug", ext_id="A10BA02",
                                          ext_scheme="atc"))
        assert decision.action == "merge"
        assert decision.level == "L0"
        assert decision.target_stable_id == "Drug:二甲双胍"
        assert decision.alias_type == "ext_key"

    @pytest.mark.asyncio
    async def test_l0_alias_table_merges_and_records_alias_type(self):
        store = FakeStore()
        await store.insert_entity(TENANT_A, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug",
            "aliases": [{"alias": "格华止", "type": "brand"}],
            "props": {}, "status": "active"})
        svc = _svc(store=store)
        decision = await svc.align(_entity("格华止", "Drug"))
        assert decision.action == "merge"
        assert decision.level == "L0"
        assert decision.alias_type == "brand"

    @pytest.mark.asyncio
    async def test_l0_alias_normalization_matches_dosage_form(self):
        store = FakeStore()
        await store.insert_entity(TENANT_A, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug", "props": {}, "status": "active"})
        svc = _svc(store=store)
        decision = await svc.align(_entity("盐酸二甲双胍片", "Drug"))
        assert decision.action == "merge"
        assert decision.level == "L0"

    @pytest.mark.asyncio
    async def test_l1_above_tau1_merges(self):
        store = FakeStore()
        await store.insert_entity(TENANT_A, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug", "props": {}, "status": "active",
            "embedding": [1.0, 0.0, 0.0]})
        svc = _svc(store=store)
        decision = await svc.align(_entity(
            "二甲双胍缓释片", "Drug",
            props={"embedding": [0.99, 0.05, 0.0]}))
        assert decision.action == "merge"
        assert decision.level == "L1"
        assert decision.confidence > DEFAULT_TAU1

    @pytest.mark.asyncio
    async def test_l1_below_tau2_creates_new(self):
        store = FakeStore()
        await store.insert_entity(TENANT_A, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug", "props": {}, "status": "active",
            "embedding": [1.0, 0.0, 0.0]})
        svc = _svc(store=store)
        # orthogonal-ish vector: cosine ~0.17 <= tau2
        decision = await svc.align(_entity(
            "阿卡波糖", "Drug", props={"embedding": [0.1, 0.99, 0.0]}))
        assert decision.action == "new"
        assert decision.level == "L1"

    @pytest.mark.asyncio
    async def test_l2_band_llm_high_confidence_merges(self):
        store = FakeStore()
        await store.insert_entity(TENANT_A, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug", "props": {}, "status": "active",
            "embedding": [1.0, 0.0, 0.0]})
        llm = FakeLLM(adjudication={"action": "merge", "target": "二甲双胍",
                                    "confidence": 0.95, "reason": "same drug"})
        svc = _svc(store=store, llm=llm)
        # cosine ~0.71 sits inside (tau2, tau1]
        decision = await svc.align(_entity(
            "二甲双胍片", "Drug", props={"embedding": [0.7, 0.7, 0.0]}))
        assert decision.action == "merge"
        assert decision.level == "L2"
        assert decision.target_stable_id == "Drug:二甲双胍"

    @pytest.mark.asyncio
    async def test_l2_low_confidence_falls_to_human(self):
        store = FakeStore()
        await store.insert_entity(TENANT_A, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug", "props": {}, "status": "active",
            "embedding": [1.0, 0.0, 0.0]})
        llm = FakeLLM(adjudication={"action": "merge", "target": "二甲双胍",
                                    "confidence": 0.6, "reason": "unsure"})
        svc = _svc(store=store, llm=llm)
        decision = await svc.align(_entity(
            "二甲双胍片", "Drug", props={"embedding": [0.7, 0.7, 0.0]}))
        assert decision.action == "pending_review"
        assert decision.level == "L3"
        assert decision.confidence < ADJUDICATE_EXECUTE_LINE

    @pytest.mark.asyncio
    async def test_l3_when_no_signal_at_all(self):
        store = FakeStore()
        svc = _svc(store=store)  # no llm, no embedding
        decision = await svc.align(_entity("全新药物", "Drug"))
        assert decision.action == "new"

    @pytest.mark.asyncio
    async def test_tenant_isolation_no_cross_tenant_merge(self):
        store = FakeStore()
        await store.insert_entity(TENANT_B, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug", "props": {}, "status": "active"})
        svc = _svc(store=store, tenant=TENANT_A)
        decision = await svc.align(_entity("二甲双胍", "Drug"))
        assert decision.action == "new"  # B's row is invisible to A

    def test_ext_schemes_whitelist(self):
        assert EXT_SCHEMES == {"atc", "nmpa", "insurance", "alias_table"}


# ---------------------------------------------------------------------------
# Layer 1: threshold calibration
# ---------------------------------------------------------------------------

class TestCalibration:
    def test_roc_separates_same_and_different(self):
        pairs = ([LabeledPair(sim=0.95, is_same=True) for _ in range(50)]
                 + [LabeledPair(sim=0.85, is_same=True) for _ in range(48)]
                 + [LabeledPair(sim=0.30, is_same=False) for _ in range(50)]
                 + [LabeledPair(sim=0.10, is_same=False) for _ in range(52)])
        th = _svc().calibrate_thresholds(pairs)
        assert 0.30 < th.tau2 <= 0.85 <= th.tau1 < 0.95
        assert th.auc > 0.95
        assert th.n_pairs == 200

    def test_tau1_respects_false_merge_cap(self):
        # one hard negative at 0.9: tau1 must not sit at or below it
        pairs = ([LabeledPair(sim=0.95, is_same=True) for _ in range(99)]
                 + [LabeledPair(sim=0.90, is_same=False)])
        th = _svc().calibrate_thresholds(pairs)
        assert th.tau1 > 0.90

    def test_tau2_respects_recall_floor(self):
        # one same pair at 0.5: tau2 must not sit above it
        pairs = ([LabeledPair(sim=0.95, is_same=True) for _ in range(99)]
                 + [LabeledPair(sim=0.50, is_same=True)]
                 + [LabeledPair(sim=0.10, is_same=False) for _ in range(100)])
        th = _svc().calibrate_thresholds(pairs)
        assert th.tau2 <= 0.50
        assert th.tau1 >= th.tau2

    def test_empty_pairs_falls_back_to_defaults(self):
        th = _svc().calibrate_thresholds([])
        assert th.tau1 == DEFAULT_TAU1
        assert th.tau2 == DEFAULT_TAU2

    def test_monotone_roc_points(self):
        pairs = [LabeledPair(sim=s / 100.0, is_same=(s > 60))
                 for s in range(101)]
        th = _svc().calibrate_thresholds(pairs)
        assert 0.0 <= th.auc <= 1.0
        assert th.recall >= 0.0 and th.false_merge_rate >= 0.0


# ---------------------------------------------------------------------------
# Layer 1: merge conflict table (K2 3.2)
# ---------------------------------------------------------------------------

class TestMergeDelta:
    @pytest.mark.asyncio
    async def test_new_entity_and_edge_inserted(self):
        store = FakeStore()
        svc = _svc(store=store)
        ext = ExtractionResult(
            entities=[_entity("二甲双胍", "Drug"),
                      _entity("2型糖尿病", "Disease")],
            edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                            rel_type="indicated_for", claim="一线用药")])
        report = await svc.merge_delta([ext])
        assert report.added == 2
        assert report.errors == []
        assert len(store.edges) == 1
        assert store.edges[0]["claim"] == "一线用药"

    @pytest.mark.asyncio
    async def test_same_claim_deduped(self):
        store = FakeStore()
        svc = _svc(store=store)
        ext = ExtractionResult(
            entities=[_entity("二甲双胍", "Drug"),
                      _entity("2型糖尿病", "Disease")],
            edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                            rel_type="indicated_for", claim="一线用药")])
        await svc.merge_delta([ext])
        await svc.merge_delta([ext])
        assert len(store.edges) == 1

    @pytest.mark.asyncio
    async def test_contra_higher_authority_supersedes_old(self):
        """Newer/higher-authority guideline wins; old fact keeps a stamp."""
        store = FakeStore()
        doc_low, doc_high = uuid_mod.uuid4(), uuid_mod.uuid4()
        store.authority[str(doc_low)] = 3
        store.authority[str(doc_high)] = 1
        svc = _svc(store=store)
        ev_low = (await store.save_evidence(TENANT_A, {
            "doc_id": doc_low, "span_text": "a", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]
        ev_high = (await store.save_evidence(TENANT_A, {
            "doc_id": doc_high, "span_text": "b", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]
        old = ExtractionResult(
            entities=[_entity("二甲双胍", "Drug"),
                      _entity("2型糖尿病", "Disease")],
            edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                            rel_type="indicated_for", claim="二线用药",
                            evidence_id=ev_low)])
        new = ExtractionResult(
            entities=[_entity("二甲双胍", "Drug"),
                      _entity("2型糖尿病", "Disease")],
            edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                            rel_type="indicated_for", claim="一线用药",
                            evidence_id=ev_high)])
        await svc.merge_delta([old])
        report = await svc.merge_delta([new])
        assert report.superseded == 1
        current = [e for e in store.edges if e.get("invalid_at") is None]
        assert len(current) == 1
        assert current[0]["claim"] == "一线用药"
        assert store.edges[0]["invalid_at"] == "now"  # old keeps a stamp

    @pytest.mark.asyncio
    async def test_contra_lower_authority_new_claim_loses(self):
        store = FakeStore()
        doc_low, doc_high = uuid_mod.uuid4(), uuid_mod.uuid4()
        store.authority[str(doc_low)] = 4
        store.authority[str(doc_high)] = 1
        svc = _svc(store=store)
        ev_high = (await store.save_evidence(TENANT_A, {
            "doc_id": doc_high, "span_text": "a", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]
        ev_low = (await store.save_evidence(TENANT_A, {
            "doc_id": doc_low, "span_text": "b", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]
        first = ExtractionResult(
            entities=[_entity("二甲双胍", "Drug"),
                      _entity("2型糖尿病", "Disease")],
            edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                            rel_type="indicated_for", claim="一线用药",
                            evidence_id=ev_high)])
        second = ExtractionResult(
            entities=[_entity("二甲双胍", "Drug"),
                      _entity("2型糖尿病", "Disease")],
            edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                            rel_type="indicated_for", claim="偏方有效",
                            evidence_id=ev_low)])
        await svc.merge_delta([first])
        report = await svc.merge_delta([second])
        assert report.superseded == 1
        current = [e for e in store.edges if e.get("invalid_at") is None]
        assert current[0]["claim"] == "一线用药"

    @pytest.mark.asyncio
    async def test_contended_same_authority_marks_not_overwrites(self):
        """never silently overwrite: equal authority -> both kept, new
        row contested for human review."""
        store = FakeStore()
        doc_a, doc_b = uuid_mod.uuid4(), uuid_mod.uuid4()
        store.authority[str(doc_a)] = 2
        store.authority[str(doc_b)] = 2
        svc = _svc(store=store)
        ev_a = (await store.save_evidence(TENANT_A, {
            "doc_id": doc_a, "span_text": "a", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]
        ev_b = (await store.save_evidence(TENANT_A, {
            "doc_id": doc_b, "span_text": "b", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]
        first = ExtractionResult(
            entities=[_entity("二甲双胍", "Drug"),
                      _entity("2型糖尿病", "Disease")],
            edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                            rel_type="indicated_for", claim="可用",
                            evidence_id=ev_a)])
        second = ExtractionResult(
            entities=[_entity("二甲双胍", "Drug"),
                      _entity("2型糖尿病", "Disease")],
            edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                            rel_type="indicated_for", claim="慎用",
                            evidence_id=ev_b)])
        await svc.merge_delta([first])
        report = await svc.merge_delta([second])
        assert report.contended == 1
        assert len([e for e in store.edges
                    if e.get("invalid_at") is None]) == 2
        assert any(e["contested"] for e in store.edges)

    @pytest.mark.asyncio
    async def test_alias_merge_attaches_alias_and_merges_ext_key(self):
        store = FakeStore()
        svc = _svc(store=store)
        await svc.merge_delta([ExtractionResult(
            entities=[_entity("二甲双胍", "Drug", ext_id="A10BA02",
                              ext_scheme="atc")])])
        report = await svc.merge_delta([ExtractionResult(
            entities=[_entity("格华止", "Drug", ext_id="A10BA02",
                              ext_scheme="atc")])])
        assert report.merged == 1
        assert report.added == 0
        row = store.entities[(TENANT_A, "Drug:二甲双胍")]
        assert any(a["alias"] == "格华止" for a in row["aliases"])
        # ext key kept on the surviving entity (future L0 hits)
        assert row["props"]["ext_ids"][0]["value"] == "A10BA02"

    @pytest.mark.asyncio
    async def test_unmappable_entity_goes_to_pending(self):
        store = FakeStore()
        svc = _svc(store=store)
        ext = ExtractionResult(pending=[_entity("未知概念", "NotAClass")])
        report = await svc.merge_delta([ext])
        assert report.pending == 1
        assert (TENANT_A, "未知概念") in store.pending

    @pytest.mark.asyncio
    async def test_edge_with_unresolved_endpoint_errors_not_crash(self):
        store = FakeStore()
        svc = _svc(store=store)
        ext = ExtractionResult(
            entities=[_entity("二甲双胍", "Drug")],
            edges=[Relation(src="二甲双胍", dst="不存在", rel_type="x",
                            claim="c")])
        report = await svc.merge_delta([ext])
        assert report.errors
        assert store.edges == []

    @pytest.mark.asyncio
    async def test_tokens_accumulate(self):
        store = FakeStore()
        svc = _svc(store=store)
        r1 = ExtractionResult(entities=[], edges=[], tokens_spent=100)
        r2 = ExtractionResult(entities=[], edges=[], tokens_spent=250)
        report = await svc.merge_delta([r1, r2])
        assert report.tokens_spent == 350
        assert report.wall_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_evidence_finalized_with_refs_and_edges(self):
        store = FakeStore()
        svc = _svc(store=store)
        ev_id = (await store.save_evidence(TENANT_A, {
            "doc_id": uuid_mod.uuid4(), "span_text": "x", "entity_refs": [],
            "edge_ids": [], "tag": "EXTRACTED", "modality": "text"}))["id"]
        ext = ExtractionResult(
            entities=[_entity("二甲双胍", "Drug", evidence_id=ev_id),
                      _entity("2型糖尿病", "Disease", evidence_id=ev_id)],
            edges=[Relation(src="二甲双胍", dst="2型糖尿病",
                            rel_type="indicated_for", claim="c",
                            evidence_id=ev_id)])
        await svc.merge_delta([ext])
        row = store.evidence[ev_id]
        assert set(row["entity_refs"]) == {"Drug:二甲双胍", "Disease:2型糖尿病"}
        assert len(row["edge_ids"]) == 1


# ---------------------------------------------------------------------------
# Layer 1: split repair
# ---------------------------------------------------------------------------

class TestSplitEntity:
    @pytest.mark.asyncio
    async def test_split_round_trip(self):
        store = FakeStore()
        svc = _svc(store=store)
        await store.insert_entity(TENANT_A, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug",
            "props": {"atc_code": "A10BA02", "brand": "格华止"},
            "status": "active"})
        await store.insert_entity(TENANT_A, {
            "stable_id": "Disease:2型糖尿病", "name": "2型糖尿病",
            "class_ref": "Disease", "props": {}, "status": "active"})
        await store.insert_relation(TENANT_A, {
            "src": "Drug:二甲双胍", "dst": "Disease:2型糖尿病",
            "rel_type": "indicated_for", "claim": "c", "props": {}})
        sid_a, sid_b = await svc.split_entity(
            "Drug:二甲双胍",
            '{"name_a": "二甲双胍", "name_b": "格华止", '
            '"props_b": ["brand"]}')
        assert (TENANT_A, sid_a) in store.entities
        assert (TENANT_A, sid_b) in store.entities
        assert store.entities[(TENANT_A, sid_b)]["props"] == {"brand": "格华止"}
        origin = store.entities[(TENANT_A, "Drug:二甲双胍")]
        assert origin["status"] == "split"
        assert origin["split_into"] == [sid_a, sid_b]
        # affected edge re-anchored onto side A
        assert store.edges[0]["src"] == sid_a

    @pytest.mark.asyncio
    async def test_split_unknown_entity_raises(self):
        store = FakeStore()
        svc = _svc(store=store)
        with pytest.raises(ValueError):
            await svc.split_entity("nope", "{}")

    @pytest.mark.asyncio
    async def test_split_requires_store(self):
        svc = _svc()
        with pytest.raises(RuntimeError):
            await svc.split_entity("x", "{}")


# ---------------------------------------------------------------------------
# Layer 1: pending pool
# ---------------------------------------------------------------------------

class TestPendingPool:
    @pytest.mark.asyncio
    async def test_update_pending_pool_aggregates(self):
        store = FakeStore()
        svc = _svc(store=store)
        for _ in range(4):
            await store.add_pending(TENANT_A, "高频概念", None, "NewClass")
        await store.add_pending(TENANT_A, "低频概念", None, "NewClass")
        summary = await svc.update_pending_pool()
        assert summary.total == 2
        assert summary.by_status == {"pending": 2}
        assert summary.by_class == {"NewClass": 2}
        assert [r["name"] for r in summary.high_frequency] == ["高频概念"]

    @pytest.mark.asyncio
    async def test_pending_to_proposals_threshold_and_status(self):
        store = FakeStore()

        class FakeOntologyService:
            async def propose_from_pending(self, pending):
                return [{"name": p["name"]} for p in pending]

        svc = _svc(store=store, )
        svc.ontology_service = FakeOntologyService()
        for _ in range(3):
            await store.add_pending(TENANT_A, "高频概念", None)
        await store.add_pending(TENANT_A, "低频概念", None)
        proposals = await svc.pending_to_proposals(min_mentions=3)
        assert [p["name"] for p in proposals] == ["高频概念"]
        statuses = {r["name"]: r["status"]
                    for r in await store.list_pending(TENANT_A, status=None)}
        assert statuses["高频概念"] == "proposed"
        assert statuses["低频概念"] == "pending"

    @pytest.mark.asyncio
    async def test_pending_to_proposals_without_ontology_service_noop(self):
        store = FakeStore()
        svc = _svc(store=store)
        for _ in range(5):
            await store.add_pending(TENANT_A, "高频概念", None)
        assert await svc.pending_to_proposals() == []


# ---------------------------------------------------------------------------
# Layer 1: extract() end-to-end with fakes
# ---------------------------------------------------------------------------

class TestExtract:
    @pytest.mark.asyncio
    async def test_extract_anchors_entities_and_writes_evidence(self):
        store = FakeStore()
        doc_id = uuid_mod.uuid4()
        llm = FakeLLM(extraction={
            "entities": [
                {"name": "二甲双胍", "class_ref": "Drug",
                 "ext_id": "A10BA02", "ext_scheme": "atc"},
                {"name": "未知概念", "class_ref": "Nope"}],
            "edges": [{"src": "二甲双胍", "dst": "2型糖尿病",
                       "rel_type": "indicated_for", "claim": "一线"}],
            "tokens_spent": 88})
        svc = _svc(store=store, llm=llm)
        span = EvidenceSpan(doc_id=doc_id, chunk_idx=3,
                            text="二甲双胍是2型糖尿病的一线用药")
        result = await svc.extract(span)
        assert [e.name for e in result.entities] == ["二甲双胍"]
        assert [e.name for e in result.pending] == ["未知概念"]
        assert result.entities[0].evidence_id is not None
        assert result.tokens_spent == 88
        assert len(store.evidence) == 1
        ev = next(iter(store.evidence.values()))
        assert ev["doc_id"] == doc_id
        assert ev["span_text"] == span.text
        # prompt carried the subgraph, the span and the few-shot block
        prompt = llm.calls[0]["prompt"]
        assert "二甲双胍" in prompt
        assert "cls:" in prompt
        assert "few-shot" in prompt or "Few-shot" in prompt

    @pytest.mark.asyncio
    async def test_extract_without_llm_raises(self):
        svc = _svc(store=FakeStore())
        with pytest.raises(RuntimeError):
            await svc.extract(EvidenceSpan(doc_id="d", chunk_idx=0, text="x"))

    @pytest.mark.asyncio
    async def test_fewshot_has_three_static_plus_dynamic(self):
        svc = _svc(store=FakeStore())
        examples = await svc.fewshot_for(
            EvidenceSpan(doc_id="d", chunk_idx=0, text="x"))
        assert sum(1 for e in examples if e.source == "static") == 3
        assert sum(1 for e in examples if e.source == "dynamic") == 2


# ---------------------------------------------------------------------------
# Layer 1: query surface + stubs
# ---------------------------------------------------------------------------

class TestQuerySurface:
    @pytest.mark.asyncio
    async def test_search_returns_hits_and_neighbors(self):
        store = FakeStore()
        svc = _svc(store=store)
        await store.insert_entity(TENANT_A, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug", "props": {}, "status": "active"})
        await store.insert_entity(TENANT_A, {
            "stable_id": "Disease:2型糖尿病", "name": "2型糖尿病",
            "class_ref": "Disease", "props": {}, "status": "active"})
        await store.insert_relation(TENANT_A, {
            "src": "Drug:二甲双胍", "dst": "Disease:2型糖尿病",
            "rel_type": "indicated_for", "claim": "c", "props": {}})
        out = await svc.search("二甲双胍", hop=1)
        assert [e["name"] for e in out["entities"]] == ["二甲双胍"]
        assert len(out["edges"]) == 1
        assert out["ontology_version"] is None

    @pytest.mark.asyncio
    async def test_version_pin_param_passes_through(self):
        svc = _svc(store=FakeStore())
        out = await svc.search("x", ontology_version="v1.2.0")
        assert out["ontology_version"] == "v1.2.0"

    @pytest.mark.asyncio
    async def test_future_owners_are_explicit_stubs(self):
        svc = _svc(store=FakeStore())
        with pytest.raises(NotImplementedError):
            await svc.ingest_new_version(None, None, [])
        with pytest.raises(NotImplementedError):
            await svc.evolution_trace(None, None)


# ---------------------------------------------------------------------------
# Layer 1: helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_cosine_basic(self):
        assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
        assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
        assert cosine([], [1]) == 0.0
        assert cosine([1, 2], [1, 2, 3]) == 0.0

    def test_normalize_name_key_dosage_form(self):
        assert normalize_name_key("盐酸二甲双胍片") == "盐酸二甲双胍"
        assert normalize_name_key("二甲双胍 缓释片") == "二甲双胍"
        assert normalize_name_key("格华止（缓释片）") == "格华止"
        assert normalize_name_key("二甲双胍") == "二甲双胍"


# ---------------------------------------------------------------------------
# Layer 2: real Postgres (RUN_POSTGRES_INTEGRATION=1)
# ---------------------------------------------------------------------------

pytestmark_pg = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION", "0") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run real-Postgres tests")


@pytestmark_pg
class TestPgIntegration:
    @pytest.mark.asyncio
    async def test_extract_merge_round_trip_and_idempotency(self):
        from services.knowevo.kg_service import KGService, PgStore
        from services.knowevo.schemas import EvidenceSpan

        tenant = str(uuid_mod.uuid4())
        doc_id = uuid_mod.uuid4()
        store = PgStore()
        llm = FakeLLM(extraction={
            "entities": [{"name": "二甲双胍", "class_ref": "Drug",
                          "ext_id": "A10BA02", "ext_scheme": "atc"}],
            "edges": []})
        svc = KGService(store=store, llm=llm, ontology=ONTOLOGY,
                        tenant_id=tenant)
        span = EvidenceSpan(doc_id=doc_id, chunk_idx=0, text="二甲双胍")
        result = await svc.extract(span)
        report = await svc.merge_delta([result])
        assert report.added == 1
        row = await store.get_entity_by_stable_id(tenant, "Drug:二甲双胍")
        assert row is not None and row["name"] == "二甲双胍"
        # span-hash idempotency ledger
        assert await store.is_extract_done(tenant, span.span_hash()) is False
        await store.record_extract_run(tenant, uuid_mod.uuid4(),
                                       span.span_hash(), "llm", 0)
        assert await store.is_extract_done(tenant, span.span_hash()) is True

    @pytest.mark.asyncio
    async def test_l0_ext_key_blocking_on_real_db(self):
        from services.knowevo.kg_service import KGService, PgStore

        tenant = str(uuid_mod.uuid4())
        store = PgStore()
        await store.insert_entity(tenant, {
            "stable_id": "Drug:二甲双胍", "name": "二甲双胍",
            "class_ref": "Drug",
            "props": {"ext_ids": [{"value": "A10BA02", "scheme": "atc"}]},
            "aliases": [], "embedding": None})
        svc = KGService(store=store, ontology=ONTOLOGY, tenant_id=tenant)
        decision = await svc.align(_entity("格华止", "Drug",
                                          ext_id="A10BA02", ext_scheme="atc"))
        assert decision.action == "merge"
        assert decision.level == "L0"

    @pytest.mark.asyncio
    async def test_pending_pool_upsert_on_real_db(self):
        from services.knowevo.kg_service import KGService, PgStore

        tenant = str(uuid_mod.uuid4())
        store = PgStore()
        svc = KGService(store=store, ontology=ONTOLOGY, tenant_id=tenant)
        await store.add_pending(tenant, "未知概念", None, "NewClass")
        await store.add_pending(tenant, "未知概念", None, "NewClass")
        summary = await svc.update_pending_pool()
        assert summary.total == 1
        assert summary.high_frequency == []  # count 2 < 3
        await store.add_pending(tenant, "未知概念", None, "NewClass")
        summary = await svc.update_pending_pool()
        assert summary.high_frequency[0]["name"] == "未知概念"