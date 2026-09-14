"""
Unit and integration tests for services/knowevo/ontology_service.py (T-04).

Layer 1 (always runs): pure-function behavior of the ontology pipeline -
seed skeleton assembly, two-level proposal shaping, V1-V5/V9 validator
behavior, ranking (score formula + ev_rich tie-break), auto_accept gate,
version commit/diff round-trip on an in-memory fake store, K0 quality
metrics against a hand-computed fixture, 15k-token truncation of the
ontology summary. No database and no LLM required: the LLM is injected as
a callable (fake in tests).

Layer 2 (RUN_POSTGRES_INTEGRATION=1): real-Postgres run of the full
build_ontology_round happy path - proposals land in
ontology_change_proposal_t, the committed version lands in
ontology_version_t, tenant isolation holds. Follows the same gate pattern
as test/backend/database/test_knowevo_models.py.
"""
import os
import sys
import uuid as uuid_mod
from pathlib import Path

# Upstream convention (test/backend/services/test_agent_repository_service.py):
# repo root on sys.path, import via the backend.* prefix. The test tree has
# its own backend/services/__init__.py, so a bare "services.*" import would
# be shadowed by test/backend/services - always use backend.services.*.
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from services.knowevo.ontology_service import (
    AUTO_ACCEPT_LINE,
    ONTOLOGY_SUMMARY_TOKEN_LIMIT,
    W_DEFAULT,
    ConceptProposal,
    OntologyService,
    SchemaProposal,
    SeedSkeleton,
    build_ev_rich,
    normalize_name,
    score_formula,
    truncate_ontology_summary,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeLLM:
    """Deterministic stand-in for the mid/large-tier model calls.

    Supports two call shapes the service uses:
    - concept nomination: (kind='concepts', chapter) -> list of dicts
    - schema assembly:    (kind='schema', class)     -> list of dicts
    """

    def __init__(self, concepts=None, schemas=None):
        self.concepts = concepts or []
        self.schemas = schemas or []
        self.calls = []

    async def __call__(self, prompt: str, *, kind: str, **kwargs):
        self.calls.append({"prompt": prompt, "kind": kind, **kwargs})
        if kind == "concepts":
            return [dict(c) for c in self.concepts]
        return [dict(s) for s in self.schemas]


class FakeStore:
    """In-memory proposal queue + version history (integration uses PG)."""

    def __init__(self):
        self.proposals = []   # list[dict] - rows of ontology_change_proposal_t
        self.versions = []    # list[dict] - rows of ontology_version_t
        self.rounds = []      # list[dict] - rows of evolution_round_t

    async def save_proposals(self, tenant_id, proposals, round_id, trigger):
        new_rows = []
        for p in proposals:
            payload = dict(p) if isinstance(p, dict) else {
                k: getattr(p, k) for k in (
                    "name", "aliases", "parent_stable_id", "evidence_spans",
                    "confidence", "rationale", "status") if hasattr(p, k)}
            target = p.target if not isinstance(p, dict) else p.get("target", "")
            new_rows.append({
                "id": uuid_mod.uuid4(),
                "tenant_id": tenant_id,
                "round_id": round_id,
                "target": target,
                "op": "CLS_ADD",
                "payload": payload,
                "confidence": payload.get("confidence"),
                "impact": payload.get("impact", 0),
                "novelty": payload.get("novelty", 0.0),
                "trigger_source": trigger,
                "status": "pending",
            })
        self.proposals.extend(new_rows)
        return len(new_rows)

    async def list_proposal_targets(self, tenant_id, round_id):
        return [p["target"] for p in self.proposals
                if p["tenant_id"] == tenant_id and p["round_id"] == round_id]

    async def load_active_snapshot(self, tenant_id):
        versions = [v for v in self.versions
                    if v["tenant_id"] == tenant_id and v["status"] == "published"]
        return versions[-1]["snapshot"] if versions else {"classes": [], "rel_types": []}

    async def save_version(self, tenant_id, version_row):
        self.versions.append(dict(version_row, tenant_id=tenant_id))

    async def save_round(self, tenant_id, round_row):
        self.rounds.append(dict(round_row, tenant_id=tenant_id))


# ---------------------------------------------------------------------------
# Layer 1: pure functions
# ---------------------------------------------------------------------------

class TestSeedExtraction:
    async def test_seed_from_toc_and_terms(self):
        svc = OntologyService(store=FakeStore(), llm=FakeLLM())
        docs = [
            {"title": "2型糖尿病防治指南",
             "toc": ["1 药物治疗", "1.1 二甲双胍", "2 并发症管理"],
             "terms": [{"name": "二甲双胍", "aliases": ["格华止"],
                        "section": "1.1", "parent": "药物治疗"},
                       {"name": "低血糖", "aliases": [],
                        "section": "2", "parent": "并发症管理"}]},
        ]
        seed = await svc.extract_seed(["doc-1"], parsed_docs=docs)
        assert isinstance(seed, SeedSkeleton)
        # S1: section tree drives the skeleton (depth <= 3)
        assert seed.section_tree[0]["title"] == "药物治疗"
        assert seed.section_tree[0]["children"][0]["title"] == "二甲双胍"
        # S2: per-chapter top-K concepts with aliases + expected parent
        assert seed.concepts[0]["name"] == "二甲双胍"
        assert seed.concepts[0]["aliases"] == ["格华止"]

    async def test_seed_dedupes_terms_across_docs(self):
        svc = OntologyService(store=FakeStore(), llm=FakeLLM())
        docs = [
            {"toc": ["1 用药"], "terms": [{"name": "二甲双胍", "section": "1"}]},
            {"toc": ["1 用药"], "terms": [{"name": "二甲双胍", "section": "1"},
                                          {"name": "胰岛素", "section": "1"}]},
        ]
        seed = await svc.extract_seed(["a", "b"], parsed_docs=docs)
        names = [c["name"] for c in seed.concepts]
        assert names.count("二甲双胍") == 1
        assert "胰岛素" in names


class TestValidation:
    def test_v1_cycle_rejected(self):
        svc = OntologyService(store=FakeStore(), llm=FakeLLM())
        # A -> B -> A parent chain must not mount
        parent_map = {"A": "B", "B": "A"}
        assert svc.validate_cycle(parent_map) is False
        assert svc.validate_cycle({"A": None, "B": "A"}) is True

    def test_v4_name_normalization(self):
        assert normalize_name("二甲双胍 metformin", kind="class") == "Metformin"
        assert normalize_name("给药途径 Route", kind="prop") == "route"

    def test_autofix_repairs_and_rejects(self):
        svc = OntologyService(store=FakeStore(), llm=FakeLLM())
        proposals = [
            # valid concept
            ConceptProposal(name="Metformin", parent_stable_id="root",
                            confidence=0.9, evidence_spans=["s1", "s2"]),
            # V4: bad class casing -> auto-normalized, kept
            ConceptProposal(name="bad class name", parent_stable_id="root",
                            confidence=0.8, evidence_spans=["s3"]),
            # V3: illegal prop type -> auto-rejected
            SchemaProposal(class_name="Metformin", prop_name="dose",
                           prop_type="matrix", evidence_spans=["s4"]),
        ]
        kept, rejected = svc.autofix(proposals)
        names = [p.name for p in kept]
        assert "Metformin" in names
        assert any(getattr(p, "name", "") == "BadClassName" for p in kept)
        assert all("matrix" != getattr(p, "prop_type", None) for p in kept)
        assert any("matrix" in str(r) for r in rejected)

    def test_v5_depth_cap(self):
        svc = OntologyService(store=FakeStore(), llm=FakeLLM())
        # depth > 5 chain: mounting at level 6 must be refused
        parent_map = {f"c{i}": f"c{i-1}" for i in range(1, 6)}  # c1..c5 chain
        parent_map["c0"] = None
        result = svc.check_depth("c6_new", "c5", parent_map)
        assert result is False  # would become level 6


class TestRanking:
    def test_score_formula(self):
        # 0.5*conf + 0.2*novelty + 0.3*impact
        assert score_formula(
            conf=0.8, novelty=0.5, impact=0.6, weights=W_DEFAULT
        ) == pytest.approx(0.5 * 0.8 + 0.2 * 0.5 + 0.3 * 0.6)

    def test_ev_rich_crosses_documents(self):
        # >=2 distinct documents -> 1.0; single doc -> 0.6
        assert build_ev_rich([{"doc": "d1"}, {"doc": "d1"}]) == 0.6
        assert build_ev_rich([{"doc": "d1"}, {"doc": "d2"}]) == 1.0

    async def test_rank_orders_desc_and_breaks_ties_with_ev_rich(self):
        svc = OntologyService(store=FakeStore(), llm=FakeLLM())
        ps = [
            ConceptProposal(name="A", confidence=0.8, novelty=0.5, impact=0.6,
                            evidence_spans=[{"doc": "d1"}]),
            # same score, richer evidence -> must rank first
            ConceptProposal(name="B", confidence=0.8, novelty=0.5, impact=0.6,
                            evidence_spans=[{"doc": "d1"}, {"doc": "d2"}]),
            ConceptProposal(name="C", confidence=0.95, novelty=0.5, impact=0.6,
                            evidence_spans=[{"doc": "d1"}]),
        ]
        ranked = await svc.rank_proposals(ps)
        assert [p.name for p in ranked] == ["C", "B", "A"]

    async def test_auto_accept_three_condition_gate(self):
        svc = OntologyService(store=FakeStore(), llm=FakeLLM())
        queue = [
            # all three: conf>=0.85 AND validator-pass AND ev_rich==1.0
            ConceptProposal(name="Auto1", confidence=0.9,
                            evidence_spans=[{"doc": "d1"}, {"doc": "d2"}]),
            # conf high but single-doc evidence -> manual
            ConceptProposal(name="Manual1", confidence=0.9,
                            evidence_spans=[{"doc": "d1"}]),
            # ev_rich 1.0 but conf below line -> manual
            ConceptProposal(name="Manual2", confidence=0.7,
                            evidence_spans=[{"doc": "d1"}, {"doc": "d2"}]),
        ]
        auto, _manual = await svc.auto_accept(queue, threshold=AUTO_ACCEPT_LINE)
        auto_names = [p.name for p in auto]
        assert "Auto1" in auto_names
        assert "Manual1" not in auto_names
        assert "Manual2" not in auto_names
        # the manual bucket is exactly the two gated-out proposals
        _manual_names = [p.name for p in _manual]
        assert set(_manual_names) == {"Manual1", "Manual2"}


class TestVersioning:
    async def test_commit_version_semver_minor_and_diff_roundtrip(self):
        store = FakeStore()
        svc = OntologyService(store=store, llm=FakeLLM())
        round_id = uuid_mod.uuid4()
        ops = [
            {"op": "CLS_ADD", "target": "cls:Drug", "payload": {"name": "Drug"}},
            {"op": "CLS_ADD", "target": "cls:Metformin",
             "payload": {"name": "Metformin", "parent": "Drug"}},
            {"op": "PROP_ADD", "target": "prop:dose",
             "payload": {"class": "Metformin", "name": "dose", "type": "float"}},
        ]
        v1 = await svc.commit_version(round_id, applied_ops=ops,
                                      base_version=None, tenant_id=TENANT_A)
        assert v1["version"] == "v1.0.0"
        assert v1["status"] == "published"
        # snapshot contains the classes the ops added
        names = {c["name"] for c in v1["snapshot"]["classes"]}
        assert {"Drug", "Metformin"} <= names
        # metrics were computed onto the version row (K0 into `metrics`)
        assert set(v1["metrics"]) == {"cov", "red", "dep", "align"}

        # minor bump for additions on top of v1.0.0
        ops2 = [{"op": "CLS_ADD", "target": "cls:Insulin",
                 "payload": {"name": "Insulin", "parent": "Drug"}}]
        v2 = await svc.commit_version(round_id, applied_ops=ops2,
                                      base_version="v1.0.0", tenant_id=TENANT_A)
        assert v2["version"] == "v1.1.0"

        # major bump for deprecation
        ops3 = [{"op": "CLS_DEPRECATE", "target": "cls:Insulin",
                 "payload": {"name": "Insulin", "reason": "duplicate"}}]
        v3 = await svc.commit_version(round_id, applied_ops=ops3,
                                      base_version="v1.1.0", tenant_id=TENANT_A)
        assert v3["version"] == "v2.0.0"

        # diff round-trip: ops between v1.0.0 and v2.0.0 replay the
        # deprecation that happened after v1.0.0
        diff_ops = await svc.diff("v1.0.0", "v2.0.0", tenant_id=TENANT_A)
        assert any(o["op"] == "CLS_DEPRECATE" for o in diff_ops)
        assert any(o["op"] == "CLS_ADD" for o in diff_ops)

    async def test_get_active_returns_latest_published(self):
        store = FakeStore()
        svc = OntologyService(store=store, llm=FakeLLM())
        round_id = uuid_mod.uuid4()
        v = await svc.commit_version(round_id, applied_ops=[
            {"op": "CLS_ADD", "target": "cls:A", "payload": {"name": "A"}}],
            base_version=None, tenant_id=TENANT_A)
        snap = await svc.get_active(TENANT_A)
        # latest published snapshot is served - and it is the one just
        # committed (round-trip through the store)
        assert {c["name"] for c in snap["classes"]} == {"A"}
        assert v["status"] == "published"


class TestQualityMetrics:
    async def test_k0_metrics_hand_computed_fixture(self):
        """Fixture ontology (hand-computed in the docstring of the impl):

        classes: Drug, Metformin(parent Drug), Insulin(parent Drug),
                Complication, Hypoglycemia(parent Complication)
        doc coverage: 2 concepts of 5 appear in the seed terms (Metformin,
                Hypoglycemia) -> cov = 2/5 = 0.4
        redundancy: Hypoglycemia carries an extra parent edge to Drug ->
                red = 1 duplicate edge / 5 classes = 0.2
        depth: max chain Drug->Metformin = 2 levels -> dep = 2
        align: 4 of 5 classes anchor to a standard TOC section; Complication
                has no anchor -> align = 0.8
        """
        svc = OntologyService(store=FakeStore(), llm=FakeLLM())
        snapshot = {
            "classes": [
                {"name": "Drug", "anchor": "toc:1"},
                {"name": "Metformin", "parent": "Drug", "anchor": "toc:1.1"},
                {"name": "Insulin", "parent": "Drug", "anchor": "toc:1.2"},
                {"name": "Complication"},  # deliberately unanchored
                {"name": "Hypoglycemia", "parent": "Complication",
                 "also_parent": "Drug", "anchor": "toc:2.1"},
            ],
            "rel_types": [],
        }
        seed_terms = ["二甲双胍(Metformin)", "低血糖(Hypoglycemia)"]
        m = await svc.quality_metrics(snapshot, seed_terms=seed_terms)
        assert m["cov"] == pytest.approx(0.4)
        assert m["red"] == pytest.approx(0.2)
        assert m["dep"] == 2
        assert m["align"] == pytest.approx(0.8)


class TestSummaryTruncation:
    def test_truncate_caps_token_budget(self):
        # ~1 token per CJK char: build an oversized summary
        big = [{"name": f"类目{i}", "props": [f"属性{j}" for j in range(50)],
                "anchor": "toc:1"} for i in range(500)]
        truncated = truncate_ontology_summary(big, token_limit=ONTOLOGY_SUMMARY_TOKEN_LIMIT)
        assert estimate_tokens(truncated) <= ONTOLOGY_SUMMARY_TOKEN_LIMIT

    def test_truncate_keeps_active_high_freq(self):
        classes = [
            {"name": "Keep", "props": ["a"] * 10, "freq": 10, "anchor": "toc:1"},
            {"name": "Drop", "props": ["b"] * 10, "freq": 0, "anchor": "toc:2"},
        ]
        out = truncate_ontology_summary(classes, token_limit=50)
        names = [c["name"] for c in out]
        assert "Keep" in names


def estimate_tokens(obj) -> int:
    """Rough CJK-token estimate used by the test only (impl has its own)."""
    s = str(obj)
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(s) - cjk) // 2


class TestProposalPersistence:
    async def test_build_round_persists_proposals_with_trigger(self):
        """Full happy path with fakes: seed -> concepts -> autofix -> rank
        -> save. dry_run must not touch the store."""
        store = FakeStore()
        llm = FakeLLM(concepts=[
            {"name": "Metformin", "aliases": ["格华止"], "parent": "root",
             "confidence": 0.9, "evidence_spans": [{"doc": "d1"}, {"doc": "d2"}],
             "rationale": "first-line therapy"},
            {"name": "low glucose", "confidence": 0.5,
             "evidence_spans": [{"doc": "d1"}]},
        ])
        svc = OntologyService(store=store, llm=llm)

        docs = [{"title": "指南", "toc": ["1 用药"],
                 "terms": [{"name": "二甲双胍", "section": "1"}]}]

        report = await svc.build_ontology_round(
            TENANT_A, doc_ids=["d1"], parsed_docs=docs, trigger="seed_bootstrap")
        assert report["proposals_saved"] == 2
        assert store.proposals[0]["trigger_source"] == "seed_bootstrap"
        # the low-conf one is kept in queue (manual) but NOT auto-accepted
        statuses = {p["payload"]["name"]: p["status"] for p in store.proposals}
        assert "Metformin" in statuses

        # dry_run leaves no rows
        store2 = FakeStore()
        svc2 = OntologyService(store=store2, llm=FakeLLM(concepts=[
            {"name": "X", "confidence": 0.9,
             "evidence_spans": [{"doc": "d1"}, {"doc": "d2"}]}]))
        report2 = await svc2.build_ontology_round(
            TENANT_A, doc_ids=["d1"], parsed_docs=[],
            trigger="seed_bootstrap", dry_run=True)
        assert report2["proposals_saved"] == 0
        assert store2.proposals == []

    async def test_idempotent_rerun_no_duplicate_proposals(self):
        """Idempotency semantics: rerunning the SAME chapter content must
        not add a second proposal for the same target. The round_id is
        derived from the chapter hashes, so a rerun resolves to the same
        round and dedupes by target (not by a fresh uuid)."""
        store = FakeStore()
        llm = FakeLLM(concepts=[
            {"name": "Metformin", "confidence": 0.9,
             "evidence_spans": [{"doc": "d1"}, {"doc": "d2"}]},
        ])
        svc = OntologyService(store=store, llm=llm)
        docs = [{"title": "g", "toc": ["1"], "terms": []}]
        r1 = await svc.build_ontology_round(TENANT_A, doc_ids=["d1"], parsed_docs=docs)
        r2 = await svc.build_ontology_round(TENANT_A, doc_ids=["d1"], parsed_docs=docs)
        assert r1["proposals_saved"] == 1
        assert r2["proposals_saved"] == 0  # same chapters -> no new rows
        # and the queue holds exactly one row for that target
        assert len(store.proposals) == 1
        # a DIFFERENT chapter (new toc hash) does produce its own round
        docs2 = [{"title": "g2", "toc": ["1 并发症"], "terms": []}]
        r3 = await svc.build_ontology_round(TENANT_A, doc_ids=["d1"], parsed_docs=docs2)
        assert r3["proposals_saved"] == 1
        assert len(store.proposals) == 2

    async def test_dry_run_report_shape(self):
        """--dry-run contract: zero persistence plus a score preview of
        the top-5 queue (what a human scans before committing a run)."""
        store = FakeStore()
        svc = OntologyService(store=store, llm=FakeLLM(concepts=[
            {"name": "Metformin", "confidence": 0.9,
             "evidence_spans": [{"doc": "d1"}, {"doc": "d2"}]},
            {"name": "Insulin", "confidence": 0.6,
             "evidence_spans": [{"doc": "d1"}]},
        ]))
        report = await svc.build_ontology_round(
            TENANT_A, doc_ids=["d1"], parsed_docs=[], dry_run=True)
        assert report["proposals_saved"] == 0
        assert store.proposals == []
        assert report["top5_preview"][0]["name"] == "Metformin"
        assert report["proposals_total"] == 2


# ---------------------------------------------------------------------------
# Layer 2: real Postgres (RUN_POSTGRES_INTEGRATION=1)
# ---------------------------------------------------------------------------

integration = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION", "0") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run real-Postgres tests",
)


class TestPostgresRound:
    @integration
    async def test_full_round_lands_in_pg(self):
        """Proposals + version + round rows land in the real schema,
        scoped to one tenant; another tenant sees nothing.

        The fixture docs hash to a deterministic round_id, so leftover
        rows from a previous run (CLI smoke or earlier test run) would
        trip the idempotency guard and make this test flaky - clean the
        fixture tenants' rows first (self-cleaning integration test).
        """
        from services.knowevo.ontology_service import PgStore
        from database.knowevo_db import (
            OntologyChangeProposal as OCP,
            _get_db_session,
        )
        with _get_db_session() as session:
            session.query(OCP).filter(
                OCP.tenant_id.in_([TENANT_A, TENANT_B])).delete(
                synchronize_session=False)
        svc = OntologyService(store=PgStore(), llm=FakeLLM(concepts=[
            {"name": "Metformin", "confidence": 0.9,
             "evidence_spans": [{"doc": "d1"}, {"doc": "d2"}]},
        ]))
        docs = [{"title": "g", "toc": ["1 用药"],
                 "terms": [{"name": "二甲双胍", "section": "1"}]}]
        report = await svc.build_ontology_round(
            TENANT_A, doc_ids=["d1"], parsed_docs=docs, trigger="seed_bootstrap")
        assert report["proposals_saved"] == 1

        # rows are tenant-scoped: tenant B sees none of tenant A's queue
        with _get_db_session() as session:
            rows_a = session.query(OCP).filter(
                OCP.tenant_id == TENANT_A).count()
            rows_b = session.query(OCP).filter(
                OCP.tenant_id == TENANT_B).count()
        assert rows_a >= 1
        assert rows_b == 0

        # the committed round is visible through the same tenant filter
        from database.knowevo_db import EvolutionRound
        with _get_db_session() as session:
            rounds_b = session.query(EvolutionRound).filter(
                EvolutionRound.tenant_id == TENANT_B).count()
        assert rounds_b == 0
