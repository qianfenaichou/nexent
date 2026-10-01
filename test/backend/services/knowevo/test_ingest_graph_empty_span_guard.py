"""
Regression test for - the "silent loss on resume" gap in the
span-ledger idempotency of ``services/knowevo/pipeline/ingest_graph.py``.

Background: the paced ingest driver returns ``{}`` once it has exhausted its
per-span retries (non-storm path). ``svc.extract`` turns ``{}`` into an
*empty* ``ExtractionResult`` without raising, so the pipeline used to write
the span's ``kg_extract_run_t`` row anyway - with ``status="done"`` - and
``is_extract_done()`` then skipped that span on every later resume. The span
was silently lost: no error, no entity, no retry.

The guard under test refuses to record an empty LLM extraction, so the span
stays retryable, and surfaces the event in the run report's ``errors`` list
(run rc becomes 2, the pipeline's existing convention for ``errors``).

Layer 1 only: no database and no LLM. ``PgStore`` / ``KGService`` are
monkeypatched, and ``COST_LEDGER_PATH`` is redirected at the session ledger so
the test never appends to the real ``competition/docs/cost-ledger.md``.
"""
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo import kg_service
from services.knowevo.pipeline import ingest_graph as ig
from services.knowevo.schemas import (
    Entity,
    EvidenceSpan,
    ExtractionResult,
)

TENANT = "tenant-empty-guard"
DOC_ID = "doc-1"
SPAN_TEXT = "hello world"


class _FakeStore:
    """Minimal PgStore surface used by _run (snapshot + run ledger)."""

    def __init__(self):
        self.done: set[tuple] = set()
        self.rows: list[dict] = []

    async def load_ontology_snapshot(self, tenant_id):
        return {"classes": {}}

    async def is_extract_done(self, tenant_id, span_hash):
        return (tenant_id, span_hash) in self.done

    async def record_extract_run(self, tenant_id, run_id, span_hash, channel,
                                 tokens_spent, **diagnostics):
        self.done.add((tenant_id, span_hash))
        self.rows.append({"tenant_id": tenant_id, "span_hash": span_hash,
                          "channel": channel, "tokens_spent": tokens_spent,
                          **diagnostics})
        return True


class _FakeSvc:
    """Canned extraction result; merge_delta returns a zeroed summary."""

    result: ExtractionResult | None = None

    def __init__(self, **kwargs):
        pass

    async def extract(self, span, ontology_summary=None):
        return _FakeSvc.result

    async def merge_delta(self, results):
        return {"added": 0, "merged": 0, "superseded": 0,
                "contended": 0, "pending": 0}


def _write_batch(tmp_path, n_chunks=1):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({
        "tenant_id": TENANT,
        "docs": [{"doc_id": DOC_ID, "chunks": [
            {"chunk_idx": i, "modality": "text", "text": SPAN_TEXT}
            for i in range(n_chunks)]}],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _wire(monkeypatch, store, tmp_path):
    # PgStore / KGService are imported *inside* _run, so they must be patched
    # on the kg_service module rather than on ingest_graph.
    monkeypatch.setattr(kg_service, "PgStore", lambda: store)
    monkeypatch.setattr(kg_service, "KGService", _FakeSvc)
    monkeypatch.setattr(ig, "_EchoLLM", lambda: object())
    monkeypatch.setattr(ig, "COST_LEDGER_PATH", tmp_path / "ledger.md")


def _span_hash(chunk_idx=0):
    return EvidenceSpan(doc_id=DOC_ID, chunk_idx=chunk_idx,
                        text=SPAN_TEXT).span_hash()


def test_empty_llm_extraction_stays_retryable(monkeypatch, tmp_path, capsys):
    """An empty LLM result must NOT be recorded as done."""
    store = _FakeStore()
    _wire(monkeypatch, store, tmp_path)
    _FakeSvc.result = ExtractionResult(channel="llm")

    rc = ig.main(["--batch", str(_write_batch(tmp_path))])
    report = json.loads(capsys.readouterr().out)

    assert rc == 2, "run-level rc must flag the error (existing convention)"
    assert store.rows == [], "empty span must stay unrecorded -> retryable"
    assert (TENANT, _span_hash()) not in store.done
    assert report["extracted"] == 0
    assert any("empty extraction" in e for e in report["errors"])


def test_nonempty_llm_extraction_is_still_recorded(monkeypatch, tmp_path,
                                                   capsys):
    """Control case: the guard must not change normal spans."""
    store = _FakeStore()
    _wire(monkeypatch, store, tmp_path)
    _FakeSvc.result = ExtractionResult(
        channel="llm",
        entities=[Entity(name="二甲双胍", class_ref="Drug")])

    rc = ig.main(["--batch", str(_write_batch(tmp_path))])
    report = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert len(store.rows) == 1
    assert store.rows[0]["span_hash"] == _span_hash()
    assert (TENANT, _span_hash()) in store.done
    assert report["errors"] == []
    assert report["extracted"] == 1


def test_pending_only_extraction_counts_as_nonempty(monkeypatch, tmp_path,
                                                    capsys):
    """Unanchored names land in `pending` - still a real extraction."""
    store = _FakeStore()
    _wire(monkeypatch, store, tmp_path)
    _FakeSvc.result = ExtractionResult(
        channel="llm",
        pending=[Entity(name="未知概念", class_ref="Unknown")])

    rc = ig.main(["--batch", str(_write_batch(tmp_path))])
    report = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert len(store.rows) == 1
    assert report["extracted"] == 1


def test_empty_span_is_retried_on_resume(monkeypatch, tmp_path, capsys):
    """The whole point of the guard: a resume re-attempts the empty span."""
    store = _FakeStore()
    _wire(monkeypatch, store, tmp_path)
    batch = _write_batch(tmp_path)

    _FakeSvc.result = ExtractionResult(channel="llm")
    ig.main(["--batch", str(batch)])
    capsys.readouterr()
    assert store.rows == []

    # Resume with a working extraction: the same span is attempted again
    # (it was never marked done) and now lands in the ledger.
    _FakeSvc.result = ExtractionResult(
        channel="llm",
        entities=[Entity(name="存款保险条例", class_ref="PolicyDocument")])
    rc = ig.main(["--batch", str(batch)])
    report = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert len(store.rows) == 1
    assert report["extracted"] == 1
