"""
Unit and integration tests for services/knowevo/ingest_service.py (T-02).

Layer 1 (always runs): registry parsing and validation, doc_asset value
mapping, supersede-of lineage pairing, parse quality scoring, the
NativeIngestClient against a fake transport (no live stack).

Layer 2 (RUN_POSTGRES_INTEGRATION=1): real-Postgres idempotent registration
into doc_asset_t - re-running register_assets never duplicates rows
(natural key tenant_id+asset_no), lineage updates land, parse write-back
lands. Same gate pattern as test_knowevo_models.py / test_ontology_service.py.
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

from services.knowevo.ingest_service import (
    NativeIngestClient,
    apply_lineage,
    build_supersede_link,
    corpus_file_digest,
    parse_quality_score,
    parse_registry,
    register_assets,
    update_parse_result,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


def _write_registry(tmp_path, lines):
    """lines: list of raw CSV body rows (without header)."""
    header = ("asset_no,title,doc_type,modality,authority_level,"
              "source_url,license_note,local_file,split")
    content = header + "\n" + "\n".join(lines) + "\n"
    p = tmp_path / "registry.csv"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Layer 1: registry parsing
# ---------------------------------------------------------------------------

class TestParseRegistry:
    def test_valid_rows_parse(self, tmp_path):
        p = _write_registry(tmp_path, [
            ("DM-GUIDE-2020,中国2型糖尿病防治指南(2020年版),guideline,text,2,"
             "https://example.org/g2020,中华医学会糖尿病学分会,g2020.pdf,build"),
            ("DM-DRUG-001,二甲双胍片说明书,drug_label,text,3,"
             "https://example.org/metformin,NMPA,metformin.pdf,blind"),
        ])
        rows = parse_registry(p)
        assert len(rows) == 2
        assert rows[0].errors == []
        assert rows[0].doc_type == "guideline"
        assert rows[0].authority_level == 2
        assert rows[1].split == "blind"

    def test_bad_doc_type_and_split_flagged_not_raised(self, tmp_path):
        p = _write_registry(tmp_path, [
            "A-1,某文档,recipe,text,3,,,a.pdf,build",
        ])
        rows = parse_registry(p)
        assert len(rows) == 1
        assert any("doc_type" in e for e in rows[0].errors)

    def test_missing_required_columns_raises(self, tmp_path):
        p = tmp_path / "registry.csv"
        p.write_text("asset_no,title\nA-1,标题\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing required columns"):
            parse_registry(p)

    def test_defaults_modality_and_split(self, tmp_path):
        p = _write_registry(tmp_path, [
            "A-9,检验单样本,lab_report,,3,,,lab.pdf,",
        ])
        rows = parse_registry(p)
        assert rows[0].modality == "text"
        assert rows[0].split == "build"

    def test_to_doc_asset_values_mapping(self, tmp_path):
        p = _write_registry(tmp_path, [
            "A-2,指南,guideline,text,2,https://x.org,学会, g2.pdf ,blind",
        ])
        row = parse_registry(p)[0]
        vals = row.to_doc_asset_values(TENANT_A)
        assert vals["tenant_id"] == TENANT_A
        assert vals["asset_no"] == "A-2"
        assert vals["source_note"] == "学会"
        assert vals["parse_status"] == "pending"
        assert vals["meta_data"]["split"] == "blind"
        assert vals["source_url"] == "https://x.org"
        # local_file with spaces trimmed
        assert row.local_file == "g2.pdf"


# ---------------------------------------------------------------------------
# Layer 1: lineage pairing
# ---------------------------------------------------------------------------

class TestSupersedeLink:
    def test_2020_2024_guide_pair(self, tmp_path):
        p = _write_registry(tmp_path, [
            ("G20,中国2型糖尿病防治指南(2020年版),guideline,text,2,"
             "https://e.org/2020,CDS,g2020.pdf,build"),
            ("G24,中国2型糖尿病防治指南(2024年版),guideline,text,2,"
             "https://e.org/2024,CDS,g2024.pdf,build"),
            "D1,二甲双胍说明书,drug_label,text,3,,,d.pdf,build",
        ])
        rows = parse_registry(p)
        lookup = {"G20": "id-old", "G24": "id-new", "D1": "id-d"}
        updates = build_supersede_link(rows, TENANT_A, lookup)
        assert len(updates) == 1
        assert updates[0] == {"id": "id-new", "supersede_of": "id-old"}

    def test_no_pair_without_older_edition(self, tmp_path):
        p = _write_registry(tmp_path, [
            "G24,中国2型糖尿病防治指南(2024年版),guideline,text,2,,,g.pdf,build",
        ])
        rows = parse_registry(p)
        assert build_supersede_link(rows, TENANT_A, {"G24": "x"}) == []


# ---------------------------------------------------------------------------
# Layer 1: parse quality scoring
# ---------------------------------------------------------------------------

class TestParseQuality:
    def test_empty_is_zero(self):
        assert parse_quality_score("") == 0.0
        assert parse_quality_score("   \n  ") == 0.0

    def test_long_structured_text_scores_high(self):
        text = ("第1章 总则\n" + "糖尿病是由多病因引起的。" * 100 +
                "\n- 要点一\n- 要点二\n")
        assert parse_quality_score(text) >= 0.8

    def test_short_plain_text_scores_low(self):
        assert 0.0 < parse_quality_score("糖尿病") <= 0.3

    def test_score_capped_at_one(self):
        text = ("# 标题\n" * 50 + "\n- 列表\n" + "长内容。" * 500)
        assert parse_quality_score(text) == 1.0


# ---------------------------------------------------------------------------
# Layer 1: native client with fake transport
# ---------------------------------------------------------------------------

class FakeTransport:
    def __init__(self, routes=None):
        # routes: {(method, url_suffix): (status, body)}
        self.calls = []
        self.routes = routes or {}

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for (m, suffix), (status, body) in self.routes.items():
            if m == method and url.endswith(suffix):
                return status, body
        return 404, {"detail": "Not Found"}


class TestNativeClient:
    def test_upload_posts_multipart(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        t = FakeTransport({("POST", "/api/file/upload"): (200, {
            "uploaded_file_paths": ["/data/doc.pdf"],
            "uploaded_filenames": ["doc.pdf"],
        })})
        client = NativeIngestClient("http://x", t)
        result = client.upload_file(f)
        assert result["uploaded_filenames"] == ["doc.pdf"]
        m, url, kwargs = t.calls[0]
        assert m == "POST" and url == "http://x/api/file/upload"
        assert "files" in kwargs and "data" in kwargs

    def test_process_posts_json_body_contract(self):
        t = FakeTransport({("POST", "/api/file/process"): (200, {"task": "ok"})})
        client = NativeIngestClient("http://x", t)
        client.process_files([{"path_or_url": "/data/a.pdf", "filename": "a.pdf"}],
                              index_name="kw-medical-b1")
        _m, _url, kwargs = t.calls[0]
        body = kwargs["json"]
        assert body["index_name"] == "kw-medical-b1"
        assert body["destination"] == "local"
        assert body["chunking_strategy"] == "basic"
        assert body["files"][0]["path_or_url"] == "/data/a.pdf"

    def test_index_create_hits_indices_prefix_route(self):
        t = FakeTransport({("POST", "/api/indices/kw-medical-b1"): (200, {"ok": 1})})
        client = NativeIngestClient("http://x", t)
        client.create_index("kw-medical-b1", embedding_model_id=5)
        _m, url, kwargs = t.calls[0]
        assert url == "http://x/api/indices/kw-medical-b1"
        assert kwargs["json"] == {"embedding_model_id": 5}

    def test_error_status_raises_with_body_snippet(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        t = FakeTransport({("POST", "/api/file/upload"): (400, {"detail": "boom"})})
        client = NativeIngestClient("http://x", t)
        with pytest.raises(RuntimeError, match="HTTP 400"):
            client.upload_file(f)

    def test_hybrid_search_payload_shape(self):
        t = FakeTransport({("POST", "/api/indices/search/hybrid"): (200, {"results": []})})
        client = NativeIngestClient("http://x", t)
        client.hybrid_search("二甲双胍适应证", ["kw-medical-b1"], top_k=5)
        _m, url, kwargs = t.calls[0]
        assert url == "http://x/api/indices/search/hybrid"
        assert kwargs["json"]["query"] == "二甲双胍适应证"
        assert kwargs["json"]["index_names"] == ["kw-medical-b1"]


class TestDigest:
    def test_sha256_stable(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"hello")
        assert corpus_file_digest(f) == corpus_file_digest(f)
        assert len(corpus_file_digest(f)) == 64


# ---------------------------------------------------------------------------
# Layer 2: real-Postgres integration (opt-in)
# ---------------------------------------------------------------------------

PG_ENV_KEYS = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
               "POSTGRES_DB", "NEXENT_POSTGRES_PASSWORD")
pg_integration = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 (plus POSTGRES_* env) to run",
)


@pytest.fixture()
def fresh_tenant():
    return str(uuid_mod.uuid4())


@pg_integration
class TestPgIntegration:
    def _registry(self, tmp_path):
        return _write_registry(tmp_path, [
            ("G20,中国2型糖尿病防治指南(2020年版),guideline,text,2,"
             "https://e.org/2020,CDS,g2020.pdf,build"),
            ("G24,中国2型糖尿病防治指南(2024年版),guideline,text,2,"
             "https://e.org/2024,CDS,g2024.pdf,build"),
            ("M1,二甲双胍片说明书,drug_label,text,3,"
             "https://e.org/m,NMPA,m.pdf,blind"),
        ])

    def test_register_idempotent_and_lineage(self, tmp_path, fresh_tenant):
        p = self._registry(tmp_path)
        rows = [r for r in parse_registry(p) if not r.errors]
        assert len(rows) == 3

        first = register_assets(rows, fresh_tenant)
        assert first["registered"] == 3
        assert first["skipped"] == 0

        # Re-run: natural key (tenant_id, asset_no) must not duplicate.
        second = register_assets(rows, fresh_tenant)
        assert second["registered"] == 0
        assert second["skipped"] == 3
        assert second["ids"] == first["ids"]

        # Lineage: 2024 guide supersedes 2020 edition.
        updates = build_supersede_link(rows, fresh_tenant, first["ids"])
        assert len(updates) == 1
        assert apply_lineage(updates) == 1

        # Parse write-back lands on the right row.
        assert update_parse_result(fresh_tenant, "G24", "processed", 0.87) is True
        assert update_parse_result(fresh_tenant, "MISSING-1", "x", None) is False

        from database.knowevo_db import DocAsset, _get_db_session
        with _get_db_session() as session:
            got = session.query(DocAsset).filter(
                DocAsset.tenant_id == fresh_tenant).all()
            assert len(got) == 3  # exactly the three rows, no duplicates
            by_no = {g.asset_no: g for g in got}
            assert by_no["G24"].supersede_of == by_no["G20"].id
            assert by_no["G24"].parse_quality == 0.87
            assert by_no["G24"].parse_status == "processed"
            assert by_no["M1"].meta_data["split"] == "blind"

    def test_tenant_isolation(self, tmp_path, fresh_tenant):
        p = self._registry(tmp_path)
        rows = [r for r in parse_registry(p) if not r.errors]
        register_assets(rows, fresh_tenant)
        other = register_assets(rows, str(uuid_mod.uuid4()))
        # Same asset_nos registered independently per tenant - no cross-read.
        assert other["registered"] == 3
        assert other["ids"] != register_assets(rows, fresh_tenant)["ids"]
